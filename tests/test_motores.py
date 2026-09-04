"""Pruebas de los dos ejes de enfoque y del autofoco, sin hardware.

    cd tests
    SP=$PWD PROY=$PWD/../codigo/MicroscopeOS python3 test_motores.py

Usa los emuladores de emuladores.py: un TMC2209 que habla el datagrama
real del datasheet sobre un bus compartido (eco incluido) y un RPi.GPIO
que cuenta flancos de STEP. La camara se simula con una textura
desenfocada por un Gaussiano cuyo sigma crece con la distancia al foco
"verdadero": asi se puede comprobar que el autofoco converge y a cuanto.

Lo que NO cubre: nada mecanico. Que el husillo tenga el juego que
asumimos, que la plataforma no choque con la muestra, que 450mA alcancen
con la carga real y que el rango barrido cubra el foco de verdad son
cosas que solo se ven en el microscopio.
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, os.environ.get("SP", os.path.dirname(os.path.abspath(__file__))))
import emuladores
emuladores.instalar()
gpio = emuladores.instalar_gpio()
sys.path.insert(0, os.environ.get(
    "PROY", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "codigo", "MicroscopeOS")))

import cv2
import numpy as np

from core import motor_focus as mf
from core.autofocus import (Autofocus, medir_nitidez, tenengrad,
                            imagen_dpc, um_por_micropaso)
from core.timelapse import TimelapseManager

ok = fail = 0
def check(nombre, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print(f"  PASS  {nombre}  {extra}".rstrip())
    else:    fail += 1; print(f"  FAIL  {nombre}  {extra}")


# ---------------- camara simulada ----------------
# Dos texturas independientes: una que ABSORBE (se ve en campo claro y
# se desplaza con el desenfoque bajo media apertura) y otra de FASE (no
# absorbe: solo se manifiesta como gradiente en el DPC, o como contraste
# proporcional al desenfoque en campo claro).
_rng = np.random.default_rng(0)
# Textura de AMPLITUD (absorbe). Pre-suavizada: una muestra real tiene
# estructura de varios pixeles, no ruido pixel a pixel -- y el ruido
# blanco se muere con cualquier desenfoque, lo que haria imposible tanto
# la correlacion como cualquier metrica de nitidez.
TEXTURA = cv2.GaussianBlur(
    _rng.integers(0, 255, (480, 640)).astype(np.float32), (0, 0), 2.5)
TEXTURA = (TEXTURA - TEXTURA.mean()) / TEXTURA.std()

# Objeto de FASE independiente (no absorbe).
FASE = cv2.GaussianBlur(
    _rng.integers(0, 255, (480, 640)).astype(np.float32), (0, 0), 3.5)
FASE = (FASE - FASE.mean()) / FASE.std()
FASE_GX = cv2.Sobel(FASE, cv2.CV_32F, 1, 0, ksize=3)
FASE_GY = cv2.Sobel(FASE, cv2.CV_32F, 0, 1, ksize=3)
FASE_LAP = cv2.Laplacian(FASE, cv2.CV_32F)


def _desplazar(img, dx, dy):
    """Desplazamiento subpixel exacto, por rampa de fase en Fourier.

    Con warpAffine el resultado depende de la parte fraccionaria del
    corrimiento (interpola, y esa interpolacion suaviza distinto segun
    caiga en 0.0 o en 0.5 px). Eso mete un rizado en la curva de nitidez
    que no existe en la optica real y que confundiria justamente a la
    etapa fina, que trabaja en ese rango.
    """
    if dx == 0 and dy == 0:
        return img
    alto, ancho = img.shape
    fy = np.fft.fftfreq(alto).reshape(-1, 1)
    fx = np.fft.fftfreq(ancho).reshape(1, -1)
    rampa = np.exp(-2j * np.pi * (fx * dx + fy * dy))
    return np.real(np.fft.ifft2(np.fft.fft2(img) * rampa)).astype(np.float32)


class CamaraDesenfocable:
    """Camara simulada con la fisica que el autofoco explota.

    Bajo media apertura TODO el plano desenfocado se proyecta de lado
    (amplitud y fase por igual), y para lados opuestos segun de que mitad
    venga la luz. El corrimiento SATURA lejos del foco (tanh): la
    relacion es lineal solo cerca, igual que en la optica real.

    El objeto de fase no absorbe:
      - en campo claro su contraste es proporcional al DESENFOQUE
        (transporte de intensidad) y SE ANULA en el foco -- la patologia
        que hace inservible la metrica cruda;
      - bajo media apertura aparece su gradiente con signo opuesto entre
        las dos mitades, asi que el DPC lo recupera, y como los dos
        aportes llegan corridos +s y -s, el DPC se suaviza al
        desenfocar: maximo EN el foco.
    """

    PX_POR_MICROPASO = 0.02      # 1200 micropasos de desenfoque = 24 px
    SATURACION_PX = 30.0         # mas alla, el corrimiento deja de crecer
    ESCALA_BLUR = 300.0          # micropasos por unidad de sigma

    def __init__(self, motores, foco_real, luces=None, absorcion=6.0,
                 fase=60.0, ruido=0.0):
        self.motores, self.foco_real = motores, foco_real
        self.luces = luces
        self.absorcion, self.fase = absorcion, fase
        # Ruido de lectura sintetico (desvio estandar en la escala 0-255).
        # 0 por defecto: todas las pruebas existentes siguen siendo
        # deterministas. Con ruido>0, cada llamada a get_focus_frame
        # devuelve algo LEVEMENTE distinto en la misma posicion -- lo que
        # hace falta para poder medir si repeticiones>1 (mediana de
        # varias lecturas) reduce de verdad la dispersion del resultado.
        self.ruido = ruido
        self._rng_ruido = np.random.default_rng()  # sin semilla: varia entre llamadas
        self.capturas = []

    def _corrimiento(self, d):
        sat = self.SATURACION_PX
        return sat * np.tanh(d * self.PX_POR_MICROPASO / sat)

    def get_focus_frame(self, camera_num):
        d = self.motores[camera_num].position - self.foco_real[camera_num]
        sigma = max(0.3, abs(d) / self.ESCALA_BLUR)
        luz = self.luces.get(camera_num) if self.luces else None
        patron = getattr(luz, "patron", None)
        signo = {"left": 1, "right": -1, "top": 1, "bottom": -1}.get(patron, 0)
        eje_x = patron in ("left", "right")

        img = np.full(TEXTURA.shape, 128.0, dtype=np.float32)
        c = signo * self._corrimiento(d)
        dx, dy = (c, 0) if eje_x else (0, c)

        if self.absorcion:
            a = _desplazar(TEXTURA, dx, dy) if signo else TEXTURA
            img += self.absorcion * cv2.GaussianBlur(a, (0, 0), sigma)

        if self.fase:
            if signo:
                g = FASE_GX if eje_x else FASE_GY
                img += (self.fase * signo *
                        cv2.GaussianBlur(_desplazar(g, dx, dy), (0, 0), sigma))
            else:
                # Campo claro: transporte de intensidad. Contraste
                # proporcional al desenfoque, nulo en el foco.
                img += (self.fase * (-d * 0.02) *
                        cv2.GaussianBlur(FASE_LAP, (0, 0), sigma))

        if self.ruido:
            img = img + self._rng_ruido.normal(0, self.ruido, img.shape)
        return np.clip(img, 0, 255).astype(np.uint8)

    def capture_image(self, camera_num, folder, filename):
        os.makedirs(folder, exist_ok=True)
        open(filename, "wb").write(b"TIF")
        self.capturas.append((camera_num, os.path.basename(filename)))
        return filename


class LuzFake:
    def __init__(self): self.encendida = False; self.patron = None; self.patrones = []
    def _set(self, p): self.encendida = True; self.patron = p; self.patrones.append(p)
    def on(self): self._set("on")
    def left(self): self._set("left")
    def right(self): self._set("right")
    def top(self): self._set("top")
    def bottom(self): self._set("bottom")
    def off(self): self.encendida = False; self.patron = None; self.patrones.append("off")


print("\n=== BUS UART COMPARTIDO (2 drivers, 1 solo cable PDN) ===")
motores, bus = mf.crear_motores(camaras=(0, 1), max_current_ma=550, microsteps=16)
escrituras = bus.ser.tmc.escrituras
check("se crean los dos ejes", set(motores) == {0, 1}, str(sorted(motores)))
check("cada eje en su direccion UART",
      (motores[0].uart_address, motores[1].uart_address) == (0, 1))
check("los dos comparten el mismo puerto serie",
      motores[0]._uart.bus is motores[1]._uart.bus)
check("pines STEP/DIR/EN distintos por eje",
      (motores[0].step_pin, motores[1].step_pin) == (21, 26))
check("GCONF con pdn_disable+mstep_reg_select en ambos",
      all((a, mf.REG_GCONF, 0xC0) in escrituras for a in (0, 1)))
check("DRV_STATUS se decodifica bien", motores[0].leer_estado()["cs_actual"] == 12)

print("\n=== CORRIENTE (tope duro por software) ===")
irun, ihold = motores[0].set_current(irun_ma=450)
check("IRUN real cerca de lo pedido", abs(irun - 450) < 30, f"{irun}mA")
check("IHOLD ~ 1/3 de IRUN", abs(ihold - irun / 3) < 30, f"{ihold}mA")
check("max_current_ma topea aunque se pida de mas",
      motores[0].set_current(irun_ma=5000)[0] <= 560)
motores[0].set_current(irun_ma=450)

print("\n=== MOVIMIENTO Y POSICION ===")
gpio.reset_pulsos()
motores[0].mover(100, direction=1, delay=0)
check("100 micropasos = 100 pulsos de STEP", gpio.pulsos.get(21) == 100)
check("posicion acumulada +100", motores[0].position == 100)
motores[0].mover(40, direction=-1, delay=0)
check("posicion 60 tras volver 40", motores[0].position == 60)
check("deja el driver deshabilitado", not motores[0].is_enabled())
check("mover un eje no toca el otro",
      motores[1].position == 0 and gpio.pulsos.get(26) is None)

print("\n=== BACKLASH (repetibilidad del autofoco) ===")
gpio.reset_pulsos()
motores[0].mover_a(0, delay=0, backlash=64)
check("llega exacto a la posicion pedida", motores[0].position == 0)
check("al cambiar de sentido sobrepasa y vuelve",
      gpio.pulsos.get(21) == 60 + 64 + 64, str(gpio.pulsos.get(21)))
gpio.reset_pulsos()
motores[0].mover_a(200, delay=0, backlash=64)
check("no sobrepasa si ya viene del lado bueno", gpio.pulsos.get(21) == 200)

print("\n=== RESOLUCION EN CALIENTE ===")
pos = motores[0].position
motores[0].set_microsteps(32)
check("MRES reescrito por UART, sin recrear el objeto",
      any(a == 0 and r == mf.REG_CHOPCONF and (v >> 24) & 0xF == 3
          for a, r, v in escrituras))
check("la posicion se reescala al cambiar de resolucion",
      motores[0].position == pos * 2, str(motores[0].position))
motores[0].set_microsteps(16)
try:
    motores[0].set_microsteps(7); rechazado = False
except ValueError:
    rechazado = True
check("resolucion invalida rechazada", rechazado)

print("\n=== JOG CONTINUO Y WATCHDOG ===")
gpio.reset_pulsos()
pos_pre = motores[0].position
motores[0].start_jog(direction=1, delay=0.0005, watchdog=0.4)
time.sleep(0.15)
check("se mueve mientras el joystick esta apretado", gpio.pulsos.get(21, 0) > 0)
check("jog_activo mientras corre", motores[0].jog_activo())
motores[0].stop_jog()
parado = gpio.pulsos.get(21, 0)
time.sleep(0.15)
check("stop_jog frena de verdad", gpio.pulsos.get(21, 0) == parado)
check("deshabilita el driver al soltar", not motores[0].is_enabled())
check("la posicion refleja exactamente los pulsos del jog",
      motores[0].position == pos_pre + parado)

gpio.reset_pulsos()
motores[0].start_jog(direction=-1, delay=0.0005, watchdog=0.25)
time.sleep(0.7)   # nadie refresca: es el caso "se cayo el WiFi apretado"
check("el watchdog corta solo si dejan de llegar pedidos",
      not motores[0].jog_activo())
n = gpio.pulsos.get(21, 0); time.sleep(0.1)
check("no sigue moviendose despues del corte", gpio.pulsos.get(21, 0) == n)

print("\n=== ESTADO PARA LA INTERFAZ ===")
est = motores[1].estado_completo()
check("trae posicion, resolucion y flags del driver",
      {"posicion", "microsteps", "sobretemp_corte", "uart"} <= set(est))
check("marca el UART como ok", est["uart"] == "ok")

print("\n=== AUTOFOCO POR BARRIDO (respaldo) ===")
# Muestra por defecto de la simulacion: celulas sin tenir (fase
# dominante, poca absorcion), que es para lo que es este microscopio.
foco_real = {0: motores[0].position + 700, 1: motores[1].position - 450}
luces = {0: LuzFake(), 1: LuzFake()}
camara = CamaraDesenfocable(motores, foco_real, luces)
af = Autofocus(camara, motores, luces)
check("la metrica sube al enfocar",
      medir_nitidez(cv2.GaussianBlur(TEXTURA, (0, 0), 0.35)) >
      medir_nitidez(cv2.GaussianBlur(TEXTURA, (0, 0), 4.0)))
check("un paso a 1/16 son 0.31 um (husillo T6x1, 200 pasos/vuelta)",
      abs(um_por_micropaso(16) - 0.3125) < 1e-6,
      f"{um_por_micropaso(16):.4f} um/micropaso")

for cam in (0, 1):
    r = af.enfocar(cam, rango=3200, puntos=13, refinamientos=2,
                   delay=0, settle=0, backlash=64)
    err = abs(r["posicion"] - foco_real[cam])
    check(f"cam{cam}: encuentra el foco", err <= 40,
          f"error {err} micropasos de un paso grueso de 266")
    check(f"cam{cam}: la plataforma queda en el foco hallado",
          motores[cam].position == r["posicion"])
    check(f"cam{cam}: no lo reporta como fuera de rango", not r["fuera_de_rango"])
    check(f"cam{cam}: apaga la luz al terminar", not luces[cam].encendida)
    check(f"cam{cam}: deja el driver deshabilitado", not motores[cam].is_enabled())
    check(f"cam{cam}: devuelve la curva de nitidez", len(r["curva"]) == 13)

foco_real[0] = motores[0].position + 9000
check("avisa cuando el maximo queda pegado al borde del rango",
      af.enfocar(0, rango=1600, puntos=9, refinamientos=1,
                 delay=0, settle=0)["fuera_de_rango"])

print("\n=== LOS DOS EJES A LA VEZ SOBRE EL MISMO BUS ===")
errores = []
def martillar(m, n):
    try:
        for _ in range(n):
            m.leer_estado(); m.mover(2, direction=1, delay=0)
    except Exception as e:
        errores.append(e)
hilos = [threading.Thread(target=martillar, args=(motores[i], 30)) for i in (0, 1)]
[h.start() for h in hilos]; [h.join() for h in hilos]
check("sin errores de UART con los dos ejes en paralelo", not errores,
      str(errores[:1]))

print("\n=== DEGRADACION: EJES NO CABLEADOS ===")
# Un driver que no esta (o que tiene VM sin conectar) no contesta las
# lecturas. Antes esto pasaba desapercibido: configurar es solo escribir,
# y una escritura por UART no tiene acuse.
emuladores.SerialFake._tmc.clear()
emuladores.SerialFake._tmc_direcciones = (0,)
solo0, _ = mf.crear_motores(camaras=(0, 1), max_current_ma=550)
check("arranca igual con un solo eje cableado", set(solo0) == {0})
af1 = Autofocus(CamaraDesenfocable(solo0, {0: 0}), solo0, {})
check("el autofoco sabe de que camaras puede ocuparse",
      af1.disponible(0) and not af1.disponible(1))

emuladores.SerialFake._tmc.clear()
emuladores.SerialFake._tmc_direcciones = ()
ninguno, _ = mf.crear_motores(camaras=(0, 1))
check("sin ningun driver no tira excepcion, solo no hay motores",
      ninguno == {})

print("\n=== OBJETO DE FASE: por que la metrica cruda no sirve ===")
# Celulas vivas sin tenir: no absorben, solo retrasan el frente de onda.
# En campo claro es el propio desenfoque el que convierte esa fase en
# intensidad, asi que en el foco exacto CASI DESAPARECEN.
luces_f = {0: LuzFake(), 1: LuzFake()}
cam_fase = CamaraDesenfocable(motores, {0: 0, 1: 0}, luces_f,
                              absorcion=6.0, fase=60.0)
af_fase = Autofocus(cam_fase, motores, luces_f)
foco_fase = motores[0].position
cam_fase.foco_real = {0: foco_fase, 1: motores[1].position}
DESENFOQUE = 60      # micropasos = 18.8 um, dentro del rango util

def _ir(pos):
    motores[0].mover_a(pos, delay=0, backlash=64)

def _cruda(pos):
    _ir(pos); luces_f[0].on()
    return medir_nitidez(cam_fase.get_focus_frame(0))

def _dpc(pos):
    _ir(pos)
    luces_f[0].left();  izq = cam_fase.get_focus_frame(0).astype(np.float32)
    luces_f[0].right(); der = cam_fase.get_focus_frame(0).astype(np.float32)
    return tenengrad(imagen_dpc(izq, der))

c_foco, c_mas, c_menos = (_cruda(foco_fase), _cruda(foco_fase + DESENFOQUE),
                          _cruda(foco_fase - DESENFOQUE))
check("la metrica cruda tiene un VALLE en el foco, no un pico",
      c_foco < c_mas and c_foco < c_menos,
      f"{c_menos:.1f} / foco {c_foco:.1f} / {c_mas:.1f}")

d_foco, d_mas, d_menos = (_dpc(foco_fase), _dpc(foco_fase + DESENFOQUE),
                          _dpc(foco_fase - DESENFOQUE))
check("la metrica sobre el DPC tiene un PICO en el foco",
      d_foco > d_mas and d_foco > d_menos,
      f"{d_menos:.4f} / foco {d_foco:.4f} / {d_mas:.4f}")

# Y la consecuencia practica: el mismo barrido, con una metrica y con la
# otra, sobre la misma muestra.
_ir(foco_fase + 700)
r_mala = af_fase.enfocar(0, rango=3200, puntos=13, refinamientos=2,
                         metrica="bruta", delay=0, settle=0)
_ir(foco_fase + 700)
luces_f[0].patrones.clear()
r_buena = af_fase.enfocar(0, rango=3200, puntos=13, refinamientos=2,
                          metrica="dpc", delay=0, settle=0)
check("en el barrido de respaldo la luz tambien se prende antes de "
      "posicionarse en el extremo del rango",
      luces_f[0].patrones and luces_f[0].patrones[0] == "on",
      f"primer comando de luz: {luces_f[0].patrones[:1]}")
err_mala = abs(r_mala["posicion"] - foco_fase)
err_buena = abs(r_buena["posicion"] - foco_fase)
check("barrer la metrica cruda se va a un plano equivocado",
      err_mala > 200,
      f"error {err_mala} micropasos ({err_mala * um_por_micropaso(16):.0f} um)")
check("barrer la metrica del DPC enfoca bien",
      err_buena < 80,
      f"error {err_buena} micropasos ({err_buena * um_por_micropaso(16):.1f} um)")
check("el resultado dice que metrica se uso", r_buena["metrica"] == "dpc")

# El pedido real: enfocar a mano, quedar CERCA, y que el autofoco
# refine desde ahi sin irse lejos primero. rango_um (servido en la API,
# convertido aca a mano con la misma formula) hace que el rango del
# barrido sea chico y quede anclado a la posicion actual.
from core.autofocus import micropasos_por_um
rango_60um = micropasos_por_um(60, 16)
check("60 micras de rango son unos pocos cientos de micropasos, no miles",
      0 < rango_60um < 250, f"{rango_60um} micropasos")

pos_manual = foco_fase - 40   # "enfoque a mano" con un poco de error
_ir(pos_manual)
r_local = af_fase.enfocar(0, rango=rango_60um, puntos=13, refinamientos=1,
                          metrica="dpc", delay=0, settle=0)
excursion = max(abs(p - pos_manual) for p, _ in r_local["curva"])
check("con rango chico el barrido no se aleja mucho de donde ya enfocaste",
      excursion < rango_60um + 20,
      f"excursion maxima {excursion} micropasos (rango pedido {rango_60um})")
check("y aun asi converge cerca del foco real",
      abs(r_local["posicion"] - foco_fase) < 30,
      f"error {abs(r_local['posicion'] - foco_fase)} micropasos")

# El caso espejo, para no dejar la impresion de que una metrica es
# "mejor": con una muestra que ABSORBE (tenida, pigmentada) en el foco
# las dos medias aperturas dan la misma imagen, el DPC se anula y su
# Tenengrad tiene el valle. Ahi la correcta es la cruda.
cam_abs = CamaraDesenfocable(motores, {0: foco_fase, 1: 0}, luces_f,
                             absorcion=40.0, fase=0.0)
af_abs = Autofocus(cam_abs, motores, luces_f)
_ir(foco_fase); luces_f[0].on()
abs_cruda_foco = medir_nitidez(cam_abs.get_focus_frame(0))
_ir(foco_fase + 300); luces_f[0].on()
abs_cruda_lejos = medir_nitidez(cam_abs.get_focus_frame(0))
check("con muestra absorbente la metrica cruda SI tiene el pico en el foco",
      abs_cruda_foco > abs_cruda_lejos,
      f"foco {abs_cruda_foco:.0f} > desenfocado {abs_cruda_lejos:.0f}")
_ir(foco_fase + 700)
r_abs_ok = af_abs.enfocar(0, rango=3200, puntos=13, refinamientos=2,
                          metrica="bruta", delay=0, settle=0)
_ir(foco_fase + 700)
r_abs_mal = af_abs.enfocar(0, rango=3200, puntos=13, refinamientos=2,
                           metrica="dpc", delay=0, settle=0)
err_ok = abs(r_abs_ok["posicion"] - foco_fase)
err_mal = abs(r_abs_mal["posicion"] - foco_fase)
check("con muestra absorbente la metrica correcta es la cruda, no la del DPC",
      err_ok < err_mal,
      f"bruta {err_ok} vs dpc {err_mal} micropasos")
check("y la deja dentro de un paso del barrido grueso", err_ok < 266,
      f"error {err_ok} micropasos ({err_ok * um_por_micropaso(16):.1f} um) "
      f"-- la curva de una muestra que absorbe es plana cerca del foco")

print("\n=== AUTOFOCO DPC: repeticiones reducen el ruido de medicion ===")
# Sin esto, enfocar_dpc() tomaba UNA sola medicion L/R por iteracion --
# justo donde el ruido pesa mas (cerca del foco, delta chico). Se prueba
# con una camara CON ruido de lectura sintetico (0 en el resto de las
# pruebas, que son deterministas a proposito).
cam_ruido = CamaraDesenfocable(motores, {0: foco_fase, 1: 0}, luces_f,
                               absorcion=6.0, fase=60.0, ruido=6.0)
af_ruido = Autofocus(cam_ruido, motores, luces_f)
_ir(foco_fase + 300)   # desenfocado un poco: delta_px moderado, no saturado

muestras_1 = [af_ruido.medir_par(0, settle=0)["delta_px"] for _ in range(25)]
muestras_5 = [af_ruido.medir_par(0, settle=0, repeticiones=5)["delta_px"]
              for _ in range(25)]
disp_1, disp_5 = float(np.std(muestras_1)), float(np.std(muestras_5))
check("combinar 5 lecturas por mediana reduce la dispersion del corrimiento",
      disp_5 < disp_1 * 0.8,
      f"desvio con 1 lectura={disp_1:.3f}px, con 5={disp_5:.3f}px")

check("medir_par informa cuantas repeticiones combino de verdad",
      af_ruido.medir_par(0, settle=0, repeticiones=5)["repeticiones"] == 5)

# Y el flujo real (enfocar_dpc) ya no llama a una medicion suelta: usa
# repeticiones=3 por defecto en cada iteracion de la etapa 1.
import inspect
firma = inspect.signature(Autofocus.enfocar_dpc)
check("enfocar_dpc trae repeticiones>1 por defecto (antes faltaba del todo)",
      firma.parameters["repeticiones"].default >= 3,
      f"default actual: {firma.parameters['repeticiones'].default}")

print("\n=== AUTOFOCO DPC: etapa 1 (direccion) ===")
# La calibracion se persiste; en las pruebas va a un temporal para no
# ensuciar profiles/ del repo.
import core.autofocus as mod_af
mod_af.ARCHIVO_CALIBRACION = Path(tempfile.mkdtemp()) / "autofoco_dpc.json"
af = Autofocus(cam_fase, motores, luces_f)
check("arranca sin calibracion", not af.calibrado(0))

_ir(foco_fase + 500)
m = af.medir_par(0, settle=0)
check("mide corrimiento entre las dos medias aperturas al desenfocar",
      abs(m["delta_px"]) > 5 and m["respuesta"] > 0.1,
      f"{m['delta_px']:+.2f} px, confianza {m['respuesta']:.2f}")
check("la correlacion de un objeto de fase da respuesta NEGATIVA en crudo "
      "(y aun asi el pico sirve)", m["respuesta_cruda"] < 0,
      f"cruda {m['respuesta_cruda']:+.2f} -> confianza {m['respuesta']:.2f}")

_ir(foco_fase)
check("en el foco el corrimiento es ~0",
      abs(af.medir_par(0, settle=0)["delta_px"]) < 1.0)
_ir(foco_fase - 500); d_menos = af.medir_par(0, settle=0)["delta_px"]
_ir(foco_fase + 500); d_mas = af.medir_par(0, settle=0)["delta_px"]
check("el corrimiento cambia de signo al cruzar el foco",
      d_menos * d_mas < 0, f"{d_menos:+.2f} px vs {d_mas:+.2f} px")

print("\n=== AUTOFOCO DPC: calibracion de ganancia ===")
luces_f[0].patrones.clear()
_ir(foco_fase)   # se calibra cerca del foco, como pide la interfaz
cal = af.calibrar_dpc(0, amplitud=800, puntos=5, delay=0, settle=0)
check("la luz se prende ANTES del salto ciego al inicio del barrido "
      "(regresion: antes se movia primero y encendia despues)",
      luces_f[0].patrones and luces_f[0].patrones[0] == "on",
      f"primer comando de luz: {luces_f[0].patrones[:1]}")
esperado = 1.0 / (2 * CamaraDesenfocable.PX_POR_MICROPASO)
medido = abs(cal["calibracion"]["micropasos_por_pixel"])
check("recupera la constante real de la simulacion", abs(medido - esperado) < 3,
      f"{medido:.1f} micropasos/px (real {esperado:.1f})")
check("el ajuste lineal es bueno", cal["r2"] > 0.98, f"r2={cal['r2']:.4f}")
check("la marca como confiable", cal["confiable"])
check("calibrar deja la camara enfocada",
      abs(cal["posicion"] - foco_fase) < 40,
      f"error {abs(cal['posicion'] - foco_fase)} micropasos")
check("guarda tambien la escala en micras",
      cal["calibracion"]["um_por_pixel"] > 0)
check("la calibracion queda persistida", mod_af.ARCHIVO_CALIBRACION.exists())
check("se relee del disco al reconstruir",
      Autofocus(cam_fase, motores, luces_f).calibrado(0))

# Calibrar fuera del rango lineal sesga la ganancia: es la advertencia de
# TODO_HW 2.4b, y conviene que quede demostrada y no solo escrita.
cal_ancha = af.calibrar_dpc(0, amplitud=6000, puntos=5, delay=0, settle=0)
check("calibrar fuera del regimen lineal sobreestima la ganancia",
      abs(cal_ancha["calibracion"]["micropasos_por_pixel"]) > medido * 1.1,
      f"{abs(cal_ancha['calibracion']['micropasos_por_pixel']):.1f} vs "
      f"{medido:.1f} micropasos/px")
_ir(foco_fase)
af.calibrar_dpc(0, amplitud=800, puntos=5, delay=0, settle=0)   # recalibrar bien

print("\n=== AUTOFOCO DPC: etapa 2 (ajuste fino) ===")
# Se lo deja 10 micropasos (3 um) fuera de foco, que es el orden de lo
# que deja la etapa 1, y se barre un rango que contenga al foco.
_ir(foco_fase + 10)
fino = af._ajuste_fino(0, motores[0], "lr", rango=48, puntos=7,
                       delay=0, settle=0, roi=0.8, backlash=64)
err_fino = abs(fino["posicion"] - foco_fase)
check("la parabola sobre el Tenengrad del DPC clava el foco", err_fino <= 8,
      f"error {err_fino} micropasos ({err_fino * um_por_micropaso(16):.1f} um), "
      f"con paso de barrido de {fino['paso_micropasos']}")
check("no reporta el maximo pegado a un borde", not fino["en_borde"])
check("interpola por debajo del paso del barrido",
      err_fino < fino["paso_micropasos"],
      f"{err_fino} < {fino['paso_micropasos']}")

# Si el foco queda fuera del rango fino, lo dice en vez de mentir.
_ir(foco_fase + 200)
check("avisa si el foco cae fuera del rango fino",
      af._ajuste_fino(0, motores[0], "lr", rango=48, puntos=7,
                      delay=0, settle=0, roi=0.8, backlash=64)["en_borde"])

print("\n=== AUTOFOCO DPC: enfoque completo ===")
_ir(foco_fase - 900)
r = af.enfocar_dpc(0, settle=0, delay=0)
err = abs(r["posicion"] - foco_fase)
check("enfoca desde 900 micropasos (280 um) de desenfoque", err <= 20,
      f"error {err} micropasos ({err * um_por_micropaso(16):.1f} um)")
check("informa el desplazamiento tambien en micras",
      abs(r["desplazamiento_um"] -
          r["desplazamiento"] * um_por_micropaso(16)) < 0.1)
check("la etapa 1 no barre: 1 o 2 mediciones",
      len(r["iteraciones"]) <= 2, f"{len(r['iteraciones'])} mediciones")
# La etapa 2 no tiene por que mover siempre: en la simulacion, sin ruido,
# la etapa 1 ya cae casi encima. Lo que se exige es que corra, que mida
# de verdad, y que no empeore lo que habia.
check("la etapa 2 corre y barre sus 7 planos",
      "fino" in r and len(r["fino"]["curva"]) == 7)
check("la etapa 2 no empeora lo que dejo la etapa 1",
      abs(r["posicion"] - foco_fase) <=
      abs(r["posicion_grueso"] - foco_fase) + 1,
      f"grueso {abs(r['posicion_grueso'] - foco_fase)} -> "
      f"fino {abs(r['posicion'] - foco_fase)} micropasos")
check("informa si la etapa 1 llego a converger sola",
      isinstance(r["convergio"], bool))
check("apaga la luz al terminar", not luces_f[0].encendida)

_ir(foco_fase + 700)
r = af.enfocar_auto(0, metodo="auto", delay=0, settle=0)
check("enfocar_auto usa DPC si la camara esta calibrada", r["metodo"] == "dpc")
r = af.enfocar_auto(1, metodo="auto", rango=3200, puntos=13,
                    refinamientos=2, delay=0, settle=0)
check("enfocar_auto cae al barrido si no hay calibracion",
      r["metodo"] == "barrido")
try:
    af.enfocar_auto(1, metodo="dpc"); exigio = False
except RuntimeError:
    exigio = True
check("metodo dpc explicito falla claro si no hay calibracion", exigio)

print("\n=== TIMELAPSE CON AUTOFOCO ===")
emuladores.SerialFake._tmc.clear()
emuladores.SerialFake._tmc_direcciones = (0, 1)
motores2, bus2 = mf.crear_motores(camaras=(0, 1), max_current_ma=550, microsteps=16)
foco2 = {0: 300, 1: -200}
luces2 = {0: LuzFake(), 1: LuzFake()}
camara2 = CamaraDesenfocable(motores2, foco2, luces2)
tl = TimelapseManager(camara2, luces2,
                      autofocus=Autofocus(camara2, motores2, luces2))

os.chdir(tempfile.mkdtemp(prefix="tl_motores_"))
tl.start(modo="blanco", interval_seconds=1, duration_seconds=2.5,
         stabilization_time=0, camaras=[0, 1], autofocus=True,
         autofocus_cada=2,
         autofocus_opts={"rango": 800, "puntos": 7, "refinamientos": 1,
                         "delay": 0, "settle": 0})
while tl.is_running():
    time.sleep(0.05)

log = open(os.path.join(tl.base_folder, "timelapse.log")).read()
n_af = log.count("autofoco cam")
check("el log deja constancia de cada cuanto reenfoca",
      "autofoco=cada 2 ciclo(s)" in log)
check("reenfoca 1 de cada 2 ciclos, no todos", n_af == 4,
      f"{n_af} enfoques en {tl.ciclo_actual} ciclos")
csv = os.path.join(tl.base_folder, "autofoco.csv")
check("registra la deriva del foco en autofoco.csv", os.path.exists(csv))
if os.path.exists(csv):
    filas = open(csv).read().strip().split("\n")
    check("una fila por enfoque, con cabecera y el metodo usado",
          filas[0].startswith("timestamp,ciclo,camara,metodo,posicion") and
          len(filas) - 1 == n_af and
          any(",dpc," in f for f in filas[1:]) and
          any(",barrido," in f for f in filas[1:]),
          filas[0])
check("sigue capturando normalmente", len(camara2.capturas) >= 4)
check("termina con las dos camaras enfocadas",
      abs(motores2[0].position - foco2[0]) < 60 and
      abs(motores2[1].position - foco2[1]) < 60,
      f"cam0={motores2[0].position} (real {foco2[0]}), "
      f"cam1={motores2[1].position} (real {foco2[1]})")

tl2 = TimelapseManager(camara2, luces2, autofocus=None)
tl2.start(modo="blanco", interval_seconds=1, duration_seconds=1.2,
          stabilization_time=0, camaras=[0], autofocus=True)
while tl2.is_running():
    time.sleep(0.05)
log2 = open(os.path.join(tl2.base_folder, "timelapse.log")).read()
check("si piden autofoco sin motores, avisa y sigue en vez de romperse",
      "no hay motores de enfoque" in log2 and "autofoco=no" in log2)

print("\n" + "=" * 50)
print(f"PASS: {ok}   FAIL: {fail}")
print("=" * 50)
sys.exit(1 if fail else 0)
