"""Servidor falso de MicroscopeOS para desarrollar la app sin la Raspberry.

Levanta la app FastAPI REAL (create_app de codigo/MicroscopeOS/server/api.py)
con el hardware reemplazado por objetos falsos definidos en este archivo:

- dos camaras que devuelven cuadros JPEG con celulas sinteticas, la hora y
  un numero de cuadro; la imagen se desenfoca segun donde este el motor y
  cambia con la luz (campo claro, relieve, fondo negro, Rheinberg);
- dos matrices de luz que recuerdan modo, brillo y colores;
- dos motores de enfoque con jog continuo y watchdog, como el real: si la
  app deja de mandar /api/focus/jog, el motor se para solo a los 1.5 s y
  aqui se imprime un aviso bien visible (es lo que NO tiene que pasar);
- un autofoco que tarda unos segundos y deja la imagen nitida;
- la incubadora, con temperatura y CO2 que se acercan al valor pedido;
- experimentos de ejemplo con imagenes reales chicas para la galeria.

No se modifica nada de codigo/MicroscopeOS/: se importa desde ahi. Todo lo
que el programa escribe (experimentos, perfiles, bitacora, ajustes) va a
una carpeta temporal nueva en cada arranque.

    cd app_movil/dev_server
    uv venv .venv && uv pip install -p .venv -r requirements.txt
    .venv/bin/python servidor_falso.py

Desde el emulador de Android la PC es http://10.0.2.2:8000.
"""

import argparse
import asyncio
import contextlib
import math
import os
import random
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

AQUI = Path(__file__).resolve().parent
REPO = AQUI.parent.parent
PROYECTO = REPO / "codigo" / "MicroscopeOS"
sys.path.insert(0, str(PROYECTO))

# OJO: se usa el tifffile REAL. tests/emuladores.py:instalar() lo reemplaza
# por uno que escribe archivos vacios, y con eso las miniaturas fallan.
import tifffile  # noqa: E402,F401

import temperature_controller as tc_mod  # noqa: E402
from core import experimentos as exp_mod  # noqa: E402
from core import marca_agua  # noqa: E402
from core import metadatos  # noqa: E402
from core.experimentos import Experimentos, agregar_nota, nombre_foto  # noqa: E402
from core.optica import Optica  # noqa: E402
from core.timelapse import CABECERA_TEMP, TimelapseManager  # noqa: E402
from core.usuarios import Usuarios  # noqa: E402

UM_POR_PASO_COMPLETO = 5.0      # igual que core/motor_focus.py (husillo T6x1)
MICROSTEPS_INTERNO = 16
ANCHO_VIVO, ALTO_VIVO = 640, 480
ANCHO_FOTO, ALTO_FOTO = 1024, 768


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# ======================================================================
# Muestra sintetica
# ======================================================================
def celulas(semilla, n, ancho, alto, crecer=0.0):
    """Imagen float32 0-1 de celulas en campo claro. Con la misma semilla
    las primeras n celulas quedan en el mismo lugar: un timelapse con n
    creciente se ve como un cultivo que prolifera."""
    rng = random.Random(semilla)
    img = np.full((alto, ancho), 0.72, np.float32)
    yy, xx = np.mgrid[0:alto, 0:ancho].astype(np.float32)
    img -= 0.10 * (((xx - ancho / 2) ** 2 + (yy - alto / 2) ** 2) / (ancho * ancho / 2))
    esc = ancho / 640
    for _ in range(n):
        x, y = rng.random() * ancho, rng.random() * alto
        rx, ry = (9 + rng.random() * 14) * esc, (7 + rng.random() * 11) * esc
        rx, ry = rx * (1 + crecer), ry * (1 + crecer)
        ang = rng.random() * 180
        cv2.ellipse(img, (int(x), int(y)), (int(rx + 2 * esc), int(ry + 2 * esc)), ang, 0, 360, 0.92, -1)
        cv2.ellipse(img, (int(x), int(y)), (int(rx), int(ry)), ang, 0, 360, 0.48, -1)
        cv2.ellipse(img, (int(x + rx * 0.15), int(y - ry * 0.1)), (int(rx * 0.38), int(ry * 0.42)),
                    ang, 0, 360, 0.30, -1)
    img = cv2.GaussianBlur(img, (0, 0), 1.0 * esc)
    ruido = np.random.default_rng(semilla).normal(0, 0.012, img.shape).astype(np.float32)
    return np.clip(img + ruido, 0, 1)


def _hex(c):
    c = (c or "FFFFFF").lstrip("#")
    return np.array([int(c[4:6], 16), int(c[2:4], 16), int(c[0:2], 16)], np.float32) / 255  # BGR


def iluminar(base, modo, brillo, color_campo=None, color_dpc=None, rheinberg=None):
    """Lo que veria la camara con esa luz. Devuelve BGR float 0-1."""
    if modo == "off":
        oscuro = base * 0.03
        return np.dstack([oscuro] * 3)
    if modo in ("left", "right", "top", "bottom"):
        dx, dy = {"left": (3, 0), "right": (-3, 0), "top": (0, 3), "bottom": (0, -3)}[modo]
        corr = np.roll(base, (dy, dx), axis=(0, 1))
        rel = np.clip(0.55 + 2.2 * (base - corr), 0, 1)
        img = np.dstack([rel] * 3) * _hex(color_dpc)
    elif modo == "ring":
        bordes = np.abs(cv2.Laplacian(base, cv2.CV_32F, ksize=3))
        img = np.dstack([np.clip(bordes * 6, 0, 1)] * 3)
    elif modo == "rheinberg":
        centro, anillo = rheinberg or ("0000FF", "FF6A00")
        bordes = np.clip(np.abs(cv2.Laplacian(base, cv2.CV_32F, ksize=3)) * 6, 0, 1)[..., None]
        img = 0.35 * _hex(centro) * (1 - bordes) + _hex(anillo) * bordes
    else:   # full
        img = np.dstack([base] * 3) * _hex(color_campo)
    return np.clip(img * (0.25 + 1.0 * brillo / 100), 0, 1)


# ======================================================================
# Hardware falso
# ======================================================================
class LuzFalsa:
    """Matriz de LED: la misma interfaz que IlluminationController."""

    def __init__(self, nombre):
        self.nombre = nombre
        self.current_pattern, self.state = "OFF", False
        self.brightness_percent = 60
        self.color_dpc = None
        self.color_campo = None
        self.current_color = None
        self._rheinberg_colors = ("0000FF", "FF6A00")

    def _p(self, p):
        self.current_pattern, self.state = p, p != "OFF"

    def on(self): self._p("FULL")
    def off(self): self._p("OFF")
    def left(self): self._p("LEFT")
    def right(self): self._p("RIGHT")
    def top(self): self._p("TOP")
    def bottom(self): self._p("BOTTOM")
    def ring(self): self._p("RING")

    def rheinberg(self, centro="0000FF", anillo="FF6A00"):
        self._rheinberg_colors = (centro, anillo)
        self._p("RHEINBERG")

    def set_brightness(self, p):
        self.brightness_percent = max(0, min(100, int(p)))

    @staticmethod
    def _color(c):
        import re
        if not re.fullmatch(r"[0-9A-Fa-f]{6}", c or ""):
            raise ValueError(f"color invalido: {c!r}")
        return None if c.upper() == "FFFFFF" else c.upper()

    def set_color_dpc(self, c): self.color_dpc = self._color(c)
    def set_color_campo(self, c): self.color_campo = self._color(c)

    @property
    def modo(self):
        return {"FULL": "full", "LEFT": "left", "RIGHT": "right", "TOP": "top",
                "BOTTOM": "bottom", "RING": "ring", "RHEINBERG": "rheinberg"}.get(
                    self.current_pattern, "off")


class MotorFalso:
    """Eje Z con la interfaz de FocusMotorController (core/motor_focus.py).

    El movimiento lleva el tiempo que llevaria el de verdad (dos flancos
    de STEP por micropaso, `delay` segundos cada uno), asi que el jog
    avanza a la misma velocidad que en la Pi."""

    _JOG_CHUNK = 8

    def __init__(self, cam, foco_um):
        self.nombre = f"cam{cam}"
        self.uart_address = cam
        self.microsteps = MICROSTEPS_INTERNO
        self._pos256 = 0
        self.irun_ma_real, self.ihold_ma_real = 450, 120
        self.foco_um = foco_um          # donde esta "de verdad" el plano enfocado
        self._habilitado = False
        self._lock = threading.RLock()
        self._jog_thread = None
        self._jog_stop = None
        self._jog_deadline = 0.0
        self._jog_dir = 1
        self._jog_delay = 0.003

    # ---- posicion ----
    @property
    def position(self):
        return int(round(self._pos256 * self.microsteps / 256))

    @position.setter
    def position(self, valor):
        self._pos256 = int(round(valor * 256 / self.microsteps))

    @property
    def posicion_um(self):
        return self._pos256 * UM_POR_PASO_COMPLETO / 256

    def um_por_micropaso(self):
        return UM_POR_PASO_COMPLETO / self.microsteps

    # ---- configuracion ----
    def set_microsteps(self, microsteps):
        if microsteps not in (1, 2, 4, 8, 16, 32, 64, 128, 256):
            raise ValueError(f"microsteps invalido: {microsteps}")
        self.microsteps = microsteps

    @contextlib.contextmanager
    def resolucion(self, microsteps=MICROSTEPS_INTERNO):
        previa = self.microsteps
        self.set_microsteps(microsteps)
        try:
            yield self
        finally:
            self.set_microsteps(previa)

    def set_current(self, irun_ma, ihold_ma=None, iholddelay=4):
        self.irun_ma_real = int(irun_ma)
        if ihold_ma is not None:
            self.ihold_ma_real = int(ihold_ma)

    def estado_completo(self):
        return {"nombre": self.nombre, "direccion_uart": self.uart_address,
                "microsteps": self.microsteps, "posicion": self.position,
                "posicion_um": round(self.posicion_um, 2),
                "irun_ma": self.irun_ma_real, "ihold_ma": self.ihold_ma_real,
                "habilitado": self._habilitado, "jog_activo": self.jog_activo(),
                "sobretemp_aviso": False, "sobretemp_corte": False,
                "corto_fase_a": False, "corto_fase_b": False,
                "bobina_a_abierta": False, "bobina_b_abierta": False,
                "cs_actual": 14, "stealthchop_activo": True, "uart": "ok"}

    # ---- movimiento ----
    def enable(self): self._habilitado = True
    def disable(self): self._habilitado = False
    def reposo(self): self._habilitado = False
    def is_enabled(self): return self._habilitado

    def move_steps(self, steps, direction=1, delay=0.001):
        with self._lock:
            signo = 1 if direction > 0 else -1
            for _ in range(abs(int(steps))):
                time.sleep(2 * delay)
                self._pos256 += (256 // self.microsteps) * signo
        return self.position

    def mover(self, pasos, direction=1, delay=0.003, mantener=False):
        with self._lock:
            self.enable()
            try:
                return self.move_steps(pasos, direction=direction, delay=delay)
            finally:
                if not mantener:
                    self.reposo()

    def mover_um(self, um, delay=0.003, mantener=False):
        pasos = int(round(abs(um) / self.um_por_micropaso()))
        log(f"motor {self.nombre}: paso de {um:+.1f} µm "
            f"({'baja' if um > 0 else 'sube'} la plataforma)")
        if pasos == 0:
            return self.position
        return self.mover(pasos, direction=1 if um > 0 else -1, delay=delay, mantener=mantener)

    def mover_a(self, posicion, delay=0.003, backlash=0, mantener=False):
        delta = posicion - self.position
        if delta:
            self.mover(abs(delta), direction=1 if delta > 0 else -1, delay=delay, mantener=mantener)
        return self.position

    # ---- jog con watchdog, igual que el real ----
    def start_jog(self, direction=1, delay=0.003, watchdog=1.5):
        with self._lock:
            self._jog_dir = 1 if direction > 0 else -1
            self._jog_delay = delay
            self._jog_deadline = time.monotonic() + watchdog
            if self._jog_thread is not None and self._jog_thread.is_alive():
                return
            log(f"motor {self.nombre}: JOG {'↓ baja' if direction > 0 else '↑ sube'} (inicio)")
            self._jog_stop = threading.Event()
            self._jog_thread = threading.Thread(target=self._jog_loop, args=(self._jog_stop,),
                                                daemon=True)
            self._jog_thread.start()

    def _jog_loop(self, stop_event):
        por_watchdog = False
        try:
            self.enable()
            while not stop_event.is_set():
                with self._lock:
                    if time.monotonic() >= self._jog_deadline:
                        por_watchdog = True
                        break
                    self.move_steps(self._JOG_CHUNK, direction=self._jog_dir, delay=self._jog_delay)
        finally:
            self.reposo()
            if por_watchdog:
                log(f"⚠⚠⚠ motor {self.nombre}: PARADO POR EL WATCHDOG — la app no mandó "
                    f"/api/focus/jog/stop (posición {self.posicion_um:+.1f} µm)")

    def jog_activo(self):
        t = self._jog_thread
        return t is not None and t.is_alive()

    def stop_jog(self):
        with self._lock:
            stop_event, thread = self._jog_stop, self._jog_thread
        if thread is not None and thread.is_alive():
            log(f"motor {self.nombre}: STOP pedido (posición {self.posicion_um:+.1f} µm)")
        if stop_event is not None:
            stop_event.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        return self.position

    def close(self):
        self.stop_jog()


class CamaraFalsa:
    """Las dos camaras, con la interfaz de CameraController que usan la API
    y el timelapse."""

    def __init__(self, luces, motores):
        self.luces = luces
        self.motores = motores
        self._preview_cams = set()
        self.preview_cam = None
        self.exposure_time, self.gain = 12000, 1.0
        self.metadatos = None
        self._cuadro = {0: 0, 1: 0}
        self._base = {c: celulas(1237 if c == 0 else 7919, 55 if c == 0 else 38,
                                 ANCHO_VIVO, ALTO_VIVO) for c in (0, 1)}
        self._base_foto = {}
        self._estadisticas = {}
        self._marcar = set()
        self._lock = threading.Lock()

    # ---- vivo ----
    def start_preview(self, camera_num):
        self._preview_cams.add(camera_num)
        self.preview_cam = camera_num
        log(f"cámara {camera_num}: vivo encendido")

    def stop_preview(self, camera_num=None):
        if camera_num is None:
            self._preview_cams.clear()
        elif camera_num in self._preview_cams:
            self._preview_cams.discard(camera_num)
            log(f"cámara {camera_num}: vivo apagado")

    def stop(self):
        self.stop_preview()

    def _vista(self, cam, base):
        luz = self.luces.get(cam)
        m = self.motores.get(cam)
        d = abs(m.posicion_um - m.foco_um) if m else 0.0
        sigma = min(14.0, max(0.0, d - 1.0) * 0.45)
        img = cv2.GaussianBlur(base, (0, 0), sigma) if sigma > 0.3 else base
        if d > 3:
            img = img * (1 - min(0.5, d / 120)) + 0.6 * min(0.5, d / 120)
        return iluminar(img, luz.modo if luz else "full",
                        luz.brightness_percent if luz else 60,
                        getattr(luz, "color_campo", None), getattr(luz, "color_dpc", None),
                        getattr(luz, "_rheinberg_colors", None))

    def get_preview_frame(self, camera_num, anotar=None):
        if camera_num not in self._preview_cams:
            return None
        self._cuadro[camera_num] += 1
        n = self._cuadro[camera_num]
        img = (self._vista(camera_num, self._base[camera_num]) * 255).astype(np.uint8)
        gris = img.mean(axis=2)
        hist = np.histogram(gris, bins=32, range=(0, 256))[0]
        self._estadisticas[camera_num] = {
            "saturados_pct": round(float((img >= 250).any(axis=2).mean() * 100), 2),
            "histograma": [int(x) for x in hist], "media": round(float(gris.mean()), 1),
            "hora": time.time()}
        m = self.motores.get(camera_num)
        texto = f"CAM {camera_num}  {datetime.now():%H:%M:%S}  cuadro {n}"
        cv2.rectangle(img, (0, 0), (ANCHO_VIVO, 28), (0, 0, 0), -1)
        cv2.putText(img, texto, (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        if m is not None:
            cv2.putText(img, f"altura {-m.posicion_um + 0.0:+.1f} um", (ANCHO_VIVO - 190, 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (120, 220, 255), 1, cv2.LINE_AA)
        # una barra que gira: se nota a simple vista si el video esta vivo
        a = n * 0.25
        c = (ANCHO_VIVO - 30, ALTO_VIVO - 30)
        cv2.line(img, c, (int(c[0] + 18 * math.cos(a)), int(c[1] + 18 * math.sin(a))), (0, 255, 255), 3)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes()

    def estadisticas_vivo(self):
        return {n: dict(e, marcar=n in self._marcar) for n, e in self._estadisticas.items()}

    def marcar_saturados(self, cam, activo):
        (self._marcar.add if activo else self._marcar.discard)(cam)

    def get_focus_frame(self, cam, descartar=1):
        return (self._vista(cam, self._base[cam]).mean(axis=2) * 255).astype(np.uint8)

    def set_exposure(self, exposure, gain):
        self.exposure_time, self.gain = int(exposure), float(gain)

    # ---- fotos ----
    def capture_image(self, camera_num, folder="captures", filename=None, meta=None):
        os.makedirs(folder, exist_ok=True)
        info = self.metadatos.para(camera_num, meta) if self.metadatos is not None else None
        # Como la camara real: una foto corta el vivo de esa camara.
        self.stop_preview(camera_num)
        time.sleep(0.25)
        if filename is None:
            filename = f"{folder}/img_{datetime.now():%Y%m%d_%H%M%S}.tif"
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        base =self._base_foto.get(camera_num)
        if base is None:
            base = self._base_foto[camera_num] = celulas(
                1237 if camera_num == 0 else 7919, 55 if camera_num == 0 else 38, ANCHO_FOTO, ALTO_FOTO)
        gris = self._vista(camera_num, base).mean(axis=2)
        img16 = (gris * 60000).astype(np.uint16)
        if info is not None:
            metadatos.escribir(filename, img16, info)
        else:
            tifffile.imwrite(filename, img16)
        log(f"cámara {camera_num}: foto {Path(filename).name}")
        return filename

    def capture_both(self, folder="captures", filenames=None, camera_nums=None, meta=None):
        camera_nums = camera_nums or [0, 1]
        return {c: self.capture_image(c, folder, (filenames or {}).get(c), meta) for c in camera_nums}


class AutofocoFalso:
    """Lo que la API y el timelapse le piden a core.autofocus.Autofocus."""

    def __init__(self, motores, segundos=8.0):
        self.motores = motores
        self.segundos = segundos
        self.calibracion = {}
        self.zonas = {}
        self.ia = None

    def disponible(self, cam):
        return cam in self.motores

    def set_zona(self, cam, zona):
        if zona is None:
            self.zonas.pop(cam, None)
            return None
        x0, x1 = sorted((zona[0], zona[2]))
        y0, y1 = sorted((zona[1], zona[3]))
        if x1 - x0 < 0.05 or y1 - y0 < 0.05:
            return None
        self.zonas[cam] = [x0, y0, x1, y1]
        return self.zonas[cam]

    def enfocar_auto(self, camera_num, metodo="auto", rango=800, rango_um=None,
                     rango_max_um=None, **kw):
        m = self.motores[camera_num]
        t0 = time.monotonic()
        inicio = m.posicion_um
        rango_um = rango_um or rango * UM_POR_PASO_COMPLETO / MICROSTEPS_INTERNO
        tope = max(rango_um, rango_max_um or rango_um)
        encontrado = abs(m.foco_um - inicio) <= tope / 2
        log(f"autofoco cam{camera_num}: buscando (±{tope / 2:.0f} µm, ~{self.segundos:.0f} s)")
        # Un barrido que se ve en el vivo: baja, sube, y termina en el foco.
        recorrido = [inicio - rango_um / 2, inicio + rango_um / 2]
        recorrido.append(m.foco_um + random.uniform(-0.3, 0.3) if encontrado else inicio)
        tramo = self.segundos / len(recorrido)
        with m._lock:
            for destino in recorrido:
                desde = m.posicion_um
                for i in range(1, 21):
                    m._pos256 = int(round((desde + (destino - desde) * i / 20)
                                          * 256 / UM_POR_PASO_COMPLETO))
                    time.sleep(tramo / 20)
        r = {"metodo": "barrido", "encontrado": encontrado,
             "desplazamiento_um": round(m.posicion_um - inicio, 2),
             "segundos": round(time.monotonic() - t0, 1), "rango_um": tope,
             "nitidez": 812.5 if encontrado else 95.0,
             "posicion": m.position, "posicion_um": round(m.posicion_um, 2)}
        log(f"autofoco cam{camera_num}: {'enfocado' if encontrado else 'NO lo encontró'} "
            f"({r['desplazamiento_um']:+.1f} µm)")
        return r


class SerialIncubadora:
    """Puerto serie del Arduino de la incubadora: acepta lo que se escriba."""
    is_open = True

    def write(self, datos):
        log(f"incubadora ← {datos.decode().strip()}")
        return len(datos)

    def close(self):
        self.is_open = False


async def incubadora_falsa():
    """Reemplaza a TemperatureController.run(): en vez de leer el Arduino,
    simula una incubadora que se acerca al valor pedido."""
    tc = tc_mod.temperature_controller
    tc._running = True
    tc.temperature, tc.co2, tc.humidity, tc.ambient_temp = 35.8, 36500.0, 86.0, 24.3
    while tc._running:
        tc.temperature += (tc.setpoint - tc.temperature) * 0.08 + random.gauss(0, 0.03)
        tc.pwm = int(max(0, min(255, 120 + (tc.setpoint - tc.temperature) * 200)))
        tc.co2 += (tc.co2_setpoint - tc.co2) * 0.06 + random.gauss(0, 120)
        tc.valve_open = tc.co2 < tc.co2_setpoint
        tc.co2_duty = round(max(0.0, min(100.0, 20 + (tc.co2_setpoint - tc.co2) / 200)), 1)
        tc.humidity = max(70.0, min(95.0, tc.humidity + random.gauss(0, 0.2)))
        tc.error_msg = None
        await asyncio.sleep(1.0)


class ActualizadorFalso:
    """/api/version de verdad (commit del repo), pero sin actualizar nunca."""

    def version(self):
        try:
            sha, fecha, titulo = subprocess.run(
                ["git", "-C", str(REPO), "log", "-1", "--format=%h%x1f%cI%x1f%s"],
                capture_output=True, text=True, timeout=5).stdout.strip().split("\x1f")
            return {"git": True, "rama": "servidor-falso", "commit": sha, "fecha": fecha,
                    "titulo": titulo}
        except Exception:
            return {"git": False}

    def revisar(self, forzar=False):
        return {"hay_nueva": False, **self.version()}

    def actualizar(self):
        from core.actualizar import ErrorActualizar
        raise ErrorActualizar("El servidor falso no se actualiza")


# ======================================================================
# Experimentos de ejemplo
# ======================================================================
def _tif(ruta, img01, meta):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    metadatos.escribir(ruta, (img01 * 60000).astype(np.uint16), meta)


def crear_ejemplos(ex, ctx):
    ahora = datetime.now().replace(second=0, microsecond=0)

    def meta(cam, cuando, carpeta, nombre, tipo, ciclo=None, patron="FULL"):
        m = ctx.para(cam, {"experimento": {"nombre": nombre, "id": carpeta.name, "tipo": tipo}})
        m["fecha_hora"] = cuando.isoformat(timespec="seconds")
        m["iluminacion"] = {"patron": patron, "nombre": metadatos.NOMBRES_LUZ.get(patron, patron),
                            "brillo_pct": 60}
        if ciclo is not None:
            m["ciclo"] = ciclo
        return m

    # 1) Timelapse terminado, dos camaras, con notas y temperatura
    ini = ahora - timedelta(days=2, hours=3)
    nombre = "Células HeLa día 1"
    c = ex.crear_timelapse(nombre, {"intervalo_s": 1800, "duracion_s": 6 * 3600, "modo": "blanco",
                                    "camaras": [0, 1]}, ahora=ini)
    filas = []
    for ciclo in range(1, 13):
        t = ini + timedelta(minutes=30 * (ciclo - 1))
        for cam in (0, 1):
            img = celulas(100 + cam, 18 + ciclo * 4, ANCHO_FOTO, ALTO_FOTO, crecer=ciclo * 0.01)
            _tif(c / f"cam{cam}" / nombre_foto(t, ciclo), img, meta(cam, t, c, nombre, "timelapse", ciclo))
        temp = 37.0 + random.gauss(0, 0.08) - (0.6 if ciclo == 6 else 0)
        filas.append(f"{t:%Y%m%d_%H%M%S},{ciclo},{temp:.2f},37.0,118,"
                     f"{40000 + random.gauss(0, 300):.0f},40000,{88 + random.gauss(0, 0.5):.1f}\n")
    (c / "temperatura.csv").write_text(CABECERA_TEMP + "".join(filas))
    agregar_nota(c, "Sembré 20 000 células por pozo", autor="Red local (192.168.1.40)",
                 ciclo=1, hora=ini + timedelta(minutes=2))
    agregar_nota(c, "Agregué el fármaco (10 µM)", autor="ana@lab.mx", ciclo=6,
                 hora=ini + timedelta(hours=2, minutes=35))
    ex.finalizar(c, fin=(ini + timedelta(hours=6)).isoformat(timespec="seconds"), estado="terminado")

    # 2) Timelapse de relieve DPC (las 4 crudas por ciclo), solo cam0
    ini = ahora - timedelta(days=1, hours=5)
    nombre = "Levadura relieve DPC"
    c = ex.crear_timelapse(nombre, {"intervalo_s": 600, "duracion_s": 3600, "modo": "dpc",
                                    "camaras": [0]}, ahora=ini)
    for ciclo in range(1, 5):
        t = ini + timedelta(minutes=10 * (ciclo - 1))
        base = celulas(300, 30 + ciclo * 3, ANCHO_FOTO, ALTO_FOTO)
        for suf, modo, pat in (("_L", "left", "LEFT"), ("_R", "right", "RIGHT"),
                               ("_T", "top", "TOP"), ("_B", "bottom", "BOTTOM")):
            img = iluminar(base, modo, 60).mean(axis=2)
            _tif(c / "cam0" / nombre_foto(t, ciclo, suf), img,
                 meta(0, t, c, nombre, "timelapse", ciclo, pat))
    ex.finalizar(c, fin=(ini + timedelta(hours=1)).isoformat(timespec="seconds"), estado="terminado")

    # 3) Fotos sueltas con nombre, ayer
    ayer = ahora - timedelta(days=1, hours=1)
    c = ex.carpeta_fotos("Muestra B", ahora=ayer)
    for i, (cam, modo) in enumerate(((0, "full"), (1, "full"), (0, "ring"))):
        t = ayer + timedelta(minutes=3 * i)
        img = iluminar(celulas(500 + i, 40, ANCHO_FOTO, ALTO_FOTO), modo, 60).mean(axis=2)
        _tif(c / f"cam{cam}" / nombre_foto(t), img, meta(cam, t, c, "Muestra B", "foto"))

    # 4) Fotos sueltas de hoy
    hoy = ahora - timedelta(hours=1)
    c = ex.carpeta_fotos("", ahora=hoy)
    for i in range(3):
        t = hoy + timedelta(minutes=7 * i)
        _tif(c / "cam0" / nombre_foto(t), celulas(700 + i, 35, ANCHO_FOTO, ALTO_FOTO),
             meta(0, t, c, exp_mod.FOTOS_SUELTAS, "foto"))
    log(f"experimentos de ejemplo: {len(ex.listar())}")


# ======================================================================
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--puerto", type=int, default=8000)
    ap.add_argument("--carpeta", help="donde guardar los datos (por defecto, una temporal nueva)")
    ap.add_argument("--con-timelapse", action="store_true",
                    help="arrancar con un timelapse corriendo (una foto cada 20 s)")
    ap.add_argument("--sin-incubadora", action="store_true",
                    help="como si el Arduino de la incubadora no estuviera conectado")
    ap.add_argument("--version-vieja", action="store_true",
                    help="sin POST /api/temperature/co2_setpoint (responde 404), como antes del PR #4")
    ap.add_argument("--autofoco-s", type=float, default=8.0,
                    help="cuanto tarda el autofoco falso (en la Pi, 15-30 s)")
    args = ap.parse_args()

    raiz = Path(args.carpeta or tempfile.mkdtemp(prefix="microscopeos_falso_")).resolve()
    raiz.mkdir(parents=True, exist_ok=True)
    # ProfileManager y el conteo usan rutas relativas al directorio actual.
    os.chdir(raiz)

    import server.api as api
    from core import autofocus as af_mod
    # Nada de lo que escribe el programa tiene que caer dentro del repo.
    api.ARCHIVO_ILUM = raiz / "profiles" / "iluminacion.json"
    api.ARCHIVO_CALIB = raiz / "profiles" / "calibracion_imagen.json"
    marca_agua.ARCHIVO = raiz / "profiles" / "marca_agua.json"
    marca_agua.LOGO = raiz / "profiles" / "logo.png"
    af_mod.ARCHIVO_ZONA = raiz / "profiles" / "autofoco_zona.json"
    af_mod.ARCHIVO_CALIBRACION = raiz / "profiles" / "autofoco_dpc.json"

    luces = {0: LuzFalsa("cam0"), 1: LuzFalsa("cam1")}
    motores = {0: MotorFalso(0, foco_um=32.0), 1: MotorFalso(1, foco_um=-9.0)}
    camara = CamaraFalsa(luces, motores)
    optica = Optica(archivo=raiz / "profiles" / "optica.json")

    tc = tc_mod.temperature_controller
    if args.sin_incubadora:
        tc._serial = None

        async def sin_arduino():
            tc.error_msg = "Arduino no encontrado"
        tc.run = sin_arduino
    else:
        tc._serial = SerialIncubadora()
        tc.run = incubadora_falsa

    camara.metadatos = metadatos.Contexto(optica=optica, illuminations=luces, motores=motores,
                                          camera=camara, temperatura=tc.status)
    ex = Experimentos(raiz=raiz / "datos", legado=raiz)
    if not ex.listar():
        crear_ejemplos(ex, camara.metadatos)

    autofoco = AutofocoFalso(motores, segundos=args.autofoco_s)
    timelapse = TimelapseManager(camara, luces, autofocus=autofoco, experimentos=ex)
    usuarios = Usuarios(bitacora=raiz / "datos" / "bitacora.jsonl",
                        reservas=raiz / "profiles" / "reservas.json")

    app = api.create_app(camara, luces, timelapse, motores=motores, autofocus=autofoco,
                         experimentos=ex, optica=optica, actualizador=ActualizadorFalso(),
                         usuarios=usuarios)

    if args.version_vieja:
        app.router.routes = [r for r in app.router.routes
                             if getattr(r, "path", "") != "/api/temperature/co2_setpoint"]

    # ---- rutas SOLO del servidor falso, para probar casos dificiles ----
    ANA = {"id": "ana@lab.mx", "nombre": "ana@lab.mx", "remoto": True}

    @app.post("/dev/otra_persona")
    def dev_otra_persona():
        """Ana (desde internet) toma el control: la app tiene que mostrar
        «Controla ana» y recibir 423 al intentar cambiar algo."""
        usuarios.visto(ANA)
        return usuarios.tomar(ANA)

    @app.post("/dev/otra_persona/soltar")
    def dev_otra_persona_soltar():
        return usuarios.soltar(ANA)

    @app.get("/dev/motores")
    def dev_motores():
        return {str(c): {"posicion_um": round(m.posicion_um, 2), "jog_activo": m.jog_activo(),
                         "foco_um": m.foco_um} for c, m in motores.items()}

    if args.con_timelapse:
        timelapse.start(modo="blanco", interval_seconds=20, duration_seconds=4 * 3600,
                        camaras=[0, 1], nombre="Timelapse de prueba")

    log(f"datos en {raiz}")
    log(f"MicroscopeOS falso en http://{args.host}:{args.puerto} "
        f"(desde el emulador de Android: http://10.0.2.2:{args.puerto})")
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.puerto, log_level="warning")


if __name__ == "__main__":
    main()
