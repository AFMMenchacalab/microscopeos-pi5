"""Autofoco de dos etapas, un eje Z por camara.

EL PROBLEMA DE FONDO
====================

Las celulas vivas sin tenir no absorben luz: solo retrasan el frente de
onda. Son objetos de FASE. En campo claro es el propio desenfoque el que
convierte esa fase en intensidad, asi que **en el foco exacto la celula
casi desaparece**. Una metrica de nitidez clasica sobre la imagen cruda
tiene entonces un VALLE donde deberia tener el pico, y el autofoco se va
convencido a un plano equivocado.

Y hay un segundo problema: cualquier metrica de contraste es SIMETRICA
alrededor del foco. Dice cuanto estas desenfocado, nunca hacia donde.
Por eso los autofocos clasicos tienen que barrer a ciegas.

ETAPA 1 -- la iluminacion oblicua rompe la simetria
===================================================

Iluminando con la mitad izquierda de la matriz la luz llega inclinada un
angulo theta, y un plano fuera de foco se proyecta de lado; con la mitad
derecha la inclinacion es opuesta y se proyecta al otro lado. El
corrimiento entre las dos imagenes es

    delta = 2 * dz * tan(theta)

Se mide con correlacion de fase (una FFT: el pico de la correlacion
cruzada da el corrimiento con precision de subpixel) y, como delta tiene
SIGNO, se sabe de inmediato si hay que subir o bajar. Un solo
movimiento, sin barrido.

En la practica no se despeja con theta, porque no es un rayo sino un
promedio sobre media matriz de LEDs: `calibrar_dpc()` barre Z una vez,
mide delta en cada plano y ajusta la recta. La pendiente ya lleva la
geometria real adentro. La correlacion devuelve ademas un valor de
confianza: si el campo esta vacio se cae, y el algoritmo sabe que no
debe confiar.

ETAPA 2 -- el ajuste fino
=========================

La relacion delta = 2*dz*tan(theta) es lineal solo dentro de un rango.
Cerca del foco delta tiende a cero y la medicion se vuelve ruidosa
comparada con su propia magnitud, asi que el salto grueso deja a ~1-2 um,
no en el foco.

La etapa fina NO usa una metrica de nitidez. Se probo con el Tenengrad
sobre el DPC y no sirve como arbitro: cerca del foco su curva es plana
(0.1 % en +-6 um, menos que el ruido de lectura), asi que el "maximo"
caia en cualquier punto del barrido -- casi siempre en un borde -- y
movia la plataforma 4 um para un lado o para el otro cada vez. Y con una
muestra que absorbe (tenida, pigmentada) es peor: el Tenengrad del DPC
tiene un VALLE en el foco y la etapa fina se iba sistematicamente al
plano equivocado.

Lo que si es confiable en los dos tipos de muestra es el propio
corrimiento: cruza el cero en el foco, sea el objeto de fase o de
amplitud. La etapa fina barre 7 planos cercanos, mide delta en cada uno
(la misma medicion de la etapa 1, sin costo extra) y estima el cero con
todos a la vez (mediana de las 7 estimaciones). Promediar siete lecturas
es lo que la hace mas fina que la etapa 1.

EL DETALLE MECANICO
===================

El husillo T6x1 tiene holgura. Si el barrido sube y baja, el foco que se
encuentra subiendo y el que se encuentra bajando quedan separados por el
backlash -- facil 5-20 um, mucho mas que la profundidad de campo de un
objetivo decente. Por eso TODA posicion final se alcanza siempre desde la
misma direccion: se pasa de largo y se regresa (ver
FocusMotorController.mover_a con backlash > 0).

En resumen: la iluminacion oblicua da direccion y magnitud, y medir el
mismo corrimiento en varios planos cercanos da resolucion.
"""

import json
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

# Calibracion DPC persistida: el servicio arranca solo en cada boot, y
# recalibrar a mano cada vez no tiene sentido si no se movio la optica.
ARCHIVO_CALIBRACION = (Path(__file__).resolve().parent.parent /
                       "profiles" / "autofoco_dpc.json")

# Pares de iluminacion de media apertura y el eje de la imagen en el que
# se manifiesta su corrimiento.
EJES_DPC = {
    "lr": ("left", "right", 0),   # componente x del desplazamiento
    "tb": ("top", "bottom", 1),   # componente y
}

# Geometria del eje Z: husillo T6x1 (1 mm de paso por vuelta) movido por
# un motor de 1.8 grados/paso = 200 pasos completos por vuelta. De ahi
# salen 5 um por paso completo, y 5/microsteps um por micropaso. Es la
# unica constante que hace falta para hablar en micras en vez de en
# micropasos; si alguna vez se cambia el husillo, se cambia aca.
PASO_HUSILLO_MM = 1.0
PASOS_POR_VUELTA = 200

# Por debajo de esta respuesta de la correlacion de fase el pico no
# significa nada (campo vacio o sin textura) y la medicion se descarta.
RESPUESTA_MINIMA = 0.05

# Una calibracion por debajo de esto no se guarda ni se usa: con una
# pendiente mal medida la etapa 1 salta a cualquier lado (hasta el tope
# de max_micropasos) y el autofoco parece "hacer cosas distintas" cada
# vez.
R2_MINIMO = 0.9
RESPUESTA_MINIMA_CALIBRACION = 0.1


def um_por_micropaso(microsteps):
    return PASO_HUSILLO_MM * 1000.0 / (PASOS_POR_VUELTA * microsteps)


def micropasos_por_um(um, microsteps):
    return int(round(um / um_por_micropaso(microsteps)))


def imagen_dpc(izq, der, eps=1.0):
    """(I_izq - I_der)/(I_izq + I_der).

    Es lo que convierte el gradiente de fase en contraste de amplitud de
    verdad. El eps evita dividir por cero en pixeles negros, y el clip
    acota pixeles saturados o muy desbalanceados entre las dos mitades.
    """
    izq = izq.astype(np.float32)
    der = der.astype(np.float32)
    return np.clip((izq - der) / (izq + der + eps), -1.0, 1.0)


def tenengrad(img):
    """Media del cuadrado del gradiente (Sobel), la metrica de Tenengrad.

    Sobre la imagen DPC tiene maximo en el foco. Se usa esta y no la
    varianza del Laplaciano porque el Laplaciano es una segunda derivada
    y amplifica mucho mas el ruido de lectura del sensor, que en el DPC
    ya viene amplificado por la division.
    """
    img = img.astype(np.float32)
    gx = cv2.Sobel(img, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(img, cv2.CV_32F, 0, 1, ksize=3)
    return float(np.mean(gx * gx + gy * gy))


def medir_nitidez(gray, roi=0.6):
    """Varianza del Laplaciano sobre la imagen CRUDA.

    Solo es valida para muestras que absorben (tenidas, pigmentadas,
    material opaco). Sobre un objeto de fase da un valle en el foco --
    ver el encabezado del modulo. Queda disponible como metrica
    alternativa (metrica="bruta"), no como la de por defecto.
    """
    if roi and 0 < roi < 1:
        h, w = gray.shape[:2]
        dh, dw = int(h * (1 - roi) / 2), int(w * (1 - roi) / 2)
        gray = gray[dh:h - dh, dw:w - dw]
    lap = cv2.Laplacian(gray.astype(np.float32), cv2.CV_32F)
    return float(lap.var())


def _pico_parabolico(puntos, i):
    """Vertice de la parabola que pasa por los tres puntos alrededor del
    maximo muestreado.

    El maximo real casi nunca cae justo en una posicion medida; con tres
    muestras equiespaciadas el vertice sale gratis y da resolucion por
    debajo del paso del barrido. Si la curvatura es nula o positiva
    (meseta, ruido) se devuelve el punto medido tal cual.
    """
    if i <= 0 or i >= len(puntos) - 1:
        return puntos[i][0]
    x0, y0 = puntos[i - 1]
    x1, y1 = puntos[i][0], puntos[i][1]
    x2, y2 = puntos[i + 1]
    denom = (y0 - 2 * y1 + y2)
    if denom >= 0:
        return x1
    paso = x1 - x0
    desplazamiento = 0.5 * (y0 - y2) / denom * paso
    # Nunca mas de medio paso: si la parabola dispara lejos es ruido.
    desplazamiento = max(-paso / 2, min(paso / 2, desplazamiento))
    return int(round(x1 + desplazamiento))


def _cruce_cero(curva, respuesta_minima=RESPUESTA_MINIMA, lineal=False):
    """Donde el corrimiento L/R cruza el cero en un barrido.

    curva: [(posicion, delta_px, respuesta)] en orden de posicion.
    Devuelve (posicion, fuera_de_rango).

    Es la metrica del barrido que no depende del tipo de muestra: delta
    cruza el cero en el foco tanto con un objeto de fase como con uno
    que absorbe, y ademas tiene signo, asi que si no cruza dentro del
    rango se sabe de que lado quedo el foco.

    Si hay mas de un cambio de signo (ruido cerca del cero, o una
    lectura espuria de la correlacion) se queda con el mas consistente:
    el que deja de un lado y del otro la mayor cantidad de puntos con el
    signo que corresponde. Una sola lectura loca no lo mueve.

    lineal=True: ademas ajusta una recta por minimos cuadrados con todos
    los puntos validos y usa su cero. Solo vale cuando todo el barrido
    esta en el regimen lineal (un barrido fino alrededor del foco);
    promediar todas las lecturas es lo que le da resolucion.
    """
    validos = [(p, d) for p, d, r in curva if r >= respuesta_minima]
    if len(validos) < 2:
        return None, True

    def signo(v):
        return 1 if v > 0 else (-1 if v < 0 else 0)

    candidatos = []
    for i in range(len(validos) - 1):
        d0, d1 = validos[i][1], validos[i + 1][1]
        if d0 * d1 > 0:
            continue
        s_izq = signo(d0) or -signo(d1)
        if s_izq == 0:
            continue
        puntaje = (sum(signo(d) == s_izq for _, d in validos[:i + 1]) +
                   sum(signo(d) == -s_izq for _, d in validos[i + 1:]))
        candidatos.append((puntaje, abs(d0 - d1), i, s_izq))

    if not candidatos:
        # Todo el barrido del mismo lado: el foco esta afuera. Se queda
        # en el punto mas cercano al cero, que es el borde correcto.
        return min(validos, key=lambda v: abs(v[1]))[0], True

    _, _, i, s_izq = max(candidatos)
    (x0, d0), (x1, d1) = validos[i], validos[i + 1]
    cero = (x0 + x1) / 2 if d0 == d1 else x0 + (x1 - x0) * d0 / (d0 - d1)

    if lineal and len(validos) >= 3:
        x = np.array([v[0] for v in validos], dtype=float)
        y = np.array([v[1] for v in validos], dtype=float)
        pendiente, ordenada = np.polyfit(x, y, 1)
        # La recta tiene que bajar/subir en el mismo sentido que el cruce
        # observado; si no, el ajuste es ruido y vale la interpolacion.
        if pendiente != 0 and signo(pendiente) == -s_izq:
            cero_recta = -ordenada / pendiente
            if x[0] <= cero_recta <= x[-1]:
                cero = cero_recta
    return int(round(cero)), False


class Autofocus:

    def __init__(self, camera, motores, illuminations=None, ia=None):
        """
        camera: CameraController
        motores: {numero_de_camara: FocusMotorController}
        illuminations: {numero_de_camara: IlluminationController} o None
        ia: core.autofocus_ia.AutofocoIA o None. Solo se usa si trae un
            modelo cargado; sin el, todo sigue igual que antes.
        """
        self.camera = camera
        self.motores = motores or {}
        self.illuminations = illuminations or {}
        self.ia = ia
        self.ultimo = {}        # camera_num -> resultado del ultimo enfoque
        self.calibracion = {}   # camera_num -> constantes del metodo DPC
        self._cargar_calibracion()

    def disponible(self, camera_num):
        return camera_num in self.motores

    def calibrado(self, camera_num):
        return camera_num in self.calibracion

    # =============================
    # CALIBRACION DE GANANCIA (persistida)
    # =============================
    def _cargar_calibracion(self):
        try:
            with open(ARCHIVO_CALIBRACION) as f:
                datos = json.load(f)
            # Las claves de JSON son strings; adentro se usan enteros.
            self.calibracion = {}
            for k, v in datos.items():
                if v.get("r2", 0.0) < R2_MINIMO:
                    # Versiones anteriores guardaban la calibracion aunque
                    # el ajuste fuera malo, y despues el autofoco la usaba.
                    print(f"[autofoco] cam{k}: calibracion DPC con r2="
                          f"{v.get('r2', 0.0):.2f} < {R2_MINIMO}, se ignora "
                          f"-- recalibrar")
                    continue
                self.calibracion[int(k)] = v
        except FileNotFoundError:
            self.calibracion = {}
        except Exception as e:
            print(f"[autofoco] calibracion ilegible ({e}), se ignora")
            self.calibracion = {}

    def _guardar_calibracion(self):
        try:
            ARCHIVO_CALIBRACION.parent.mkdir(parents=True, exist_ok=True)
            with open(ARCHIVO_CALIBRACION, "w") as f:
                json.dump({str(k): v for k, v in self.calibracion.items()},
                          f, indent=2)
        except Exception as e:
            print(f"[autofoco] no se pudo guardar la calibracion: {e}")

    def _micropasos_por_pixel(self, camera_num, motor):
        """La calibracion se guarda junto con la resolucion a la que se
        midio: si despues se cambia el microstepping, un pixel de
        corrimiento equivale a otra cantidad de micropasos (mismo
        desplazamiento fisico, distinta unidad)."""
        cal = self.calibracion[camera_num]
        factor = motor.microsteps / cal.get("microsteps", motor.microsteps)
        return cal["micropasos_por_pixel"] * factor

    # =============================
    # MEDICION SOBRE EL PAR DE MEDIAS APERTURAS
    # =============================
    def _frame(self, camera_num, roi):
        gray = self.camera.get_focus_frame(camera_num).astype(np.float32)
        if roi and 0 < roi < 1:
            h, w = gray.shape[:2]
            dh, dw = int(h * (1 - roi) / 2), int(w * (1 - roi) / 2)
            gray = np.ascontiguousarray(gray[dh:h - dh, dw:w - dw])
        return gray

    @staticmethod
    def _para_correlacion(gray, ventana):
        """Normaliza a media 0 / desvio 1 y aplica la ventana de Hann.

        La normalizacion hace que no importe que una mitad de la matriz
        ilumine algo mas que la otra; la ventana evita que el borde del
        recorte, que es una discontinuidad brutal, domine la FFT.
        """
        g = gray - gray.mean()
        desvio = g.std()
        if desvio > 1e-6:
            g = g / desvio
        return g * ventana

    def medir_par(self, camera_num, eje="lr", settle=0.25, roi=0.8,
                 repeticiones=1):
        """Enciende las dos medias aperturas opuestas y saca de ESE MISMO
        par las dos cosas que necesitan las dos etapas:

          - el corrimiento con signo (las dos etapas y el barrido), por
            correlacion de fase;
          - la nitidez del DPC, por Tenengrad (solo para el barrido con
            metrica="dpc" y como diagnostico).

        Sacar las dos de un solo par de imagenes es lo que hace que la
        etapa 2 no cueste el doble de capturas.

        repeticiones > 1: repite el ciclo L/R esa cantidad de veces y
        combina por MEDIANA (no promedio -- una sola repeticion con un
        golpe de vibracion o un parpadeo no debe arrastrar el resultado).
        Cuesta proporcionalmente mas tiempo (cada repeticion es un ciclo
        completo de luz+captura), pero es barato comparado con el costo
        de una correccion mal dirigida por una lectura ruidosa -- sobre
        todo cerca del foco, donde delta tiende a cero y el ruido pesa
        mas en proporcion (ver TODO_HW.md).
        """
        luz = self.illuminations.get(camera_num)
        if luz is None:
            raise RuntimeError(f"cam{camera_num} no tiene matriz de iluminacion")
        if eje not in EJES_DPC:
            raise ValueError(f"eje invalido: {eje} (validos: {sorted(EJES_DPC)})")
        metodo_a, metodo_b, componente = EJES_DPC[eje]

        deltas, respuestas, respuestas_crudas, nitideces = [], [], [], []
        ventana = None
        for _ in range(max(1, int(repeticiones))):
            getattr(luz, metodo_a)()
            time.sleep(settle)
            a = self._frame(camera_num, roi)
            getattr(luz, metodo_b)()
            time.sleep(settle)
            b = self._frame(camera_num, roi)

            if ventana is None:
                ventana = cv2.createHanningWindow(
                    (a.shape[1], a.shape[0]), cv2.CV_32F)
            (dx, dy), respuesta = cv2.phaseCorrelate(
                self._para_correlacion(a, ventana),
                self._para_correlacion(b, ventana))

            deltas.append(float((dx, dy)[componente]))
            # Se toma el VALOR ABSOLUTO a proposito. Con un objeto de fase
            # las dos medias aperturas dan contraste de signo opuesto
            # (eso es justamente lo que hace visible la fase), asi que la
            # superficie de correlacion queda globalmente invertida y
            # OpenCV devuelve una respuesta negativa -- pero la POSICION
            # del pico, que es lo unico que se usa, sigue siendo la
            # correcta. Sin el abs(), el metodo se rechazaria a si mismo
            # justo en las muestras para las que existe. Lo que si
            # descarta es una respuesta cerca de cero: eso es campo vacio
            # o sin textura, y ahi el pico no significa nada.
            respuestas.append(abs(float(respuesta)))
            respuestas_crudas.append(float(respuesta))
            nitideces.append(tenengrad(imagen_dpc(a, b)))

        return {
            "delta_px": float(np.median(deltas)),
            "respuesta": float(np.median(respuestas)),
            "respuesta_cruda": float(np.median(respuestas_crudas)),
            "nitidez": float(np.median(nitideces)),
            "repeticiones": len(deltas),
        }

    def medir_desplazamiento_dpc(self, camera_num, eje="lr", settle=0.25,
                                 roi=0.8, repeticiones=1):
        """Solo el corrimiento (delta, respuesta), para diagnostico
        suelto. Envoltorio delgado de medir_par(), que ya hace el
        promedio por repeticiones."""
        m = self.medir_par(camera_num, eje=eje, settle=settle, roi=roi,
                           repeticiones=repeticiones)
        return m["delta_px"], m["respuesta"]

    # =============================
    # CALIBRACION: la pendiente de delta contra Z
    # =============================
    def calibrar_dpc(self, camera_num, amplitud=1200, puntos=5, eje="lr",
                     delay=0.003, settle=0.25, roi=0.8, backlash=64,
                     repeticiones=2, apagar_luz=True):
        """Barre Z una vez, mide delta en cada plano y ajusta la recta.

        La pendiente lleva adentro la geometria real del cono de
        iluminacion (no es un rayo, es el promedio sobre media matriz de
        LEDs), asi que no hace falta conocer theta.

        Devuelve tambien el foco: es donde la recta cruza el cero. O sea
        que calibrar YA enfoca.

        r2 es la bondad del ajuste. La relacion es lineal por fisica, asi
        que un r2 bajo significa que la medicion no sirve (campo vacio,
        iluminacion mal orientada, o amplitud tan grande que se sale del
        regimen lineal) y no que haga falta un modelo mejor.
        """
        motor = self.motores.get(camera_num)
        if motor is None:
            raise RuntimeError(f"cam{camera_num} no tiene motor de enfoque")
        luz = self.illuminations.get(camera_num)

        puntos = max(3, int(puntos))
        paso = max(1, int(round(amplitud / (puntos - 1))))
        inicial = motor.position
        inicio = int(round(inicial - paso * (puntos - 1) / 2))
        t0 = time.monotonic()

        posiciones, corrimientos, respuestas = [], [], []
        try:
            if luz is not None:
                luz.on()
            motor.mover_a(inicio, delay=delay, backlash=backlash, mantener=True)
            for i in range(puntos):
                if i:
                    motor.mover(paso, direction=1, delay=delay, mantener=True)
                m = self.medir_par(camera_num, eje=eje, settle=settle, roi=roi,
                                   repeticiones=repeticiones)
                posiciones.append(motor.position)
                corrimientos.append(m["delta_px"])
                respuestas.append(m["respuesta"])

            x = np.array(posiciones, dtype=float)
            y = np.array(corrimientos, dtype=float)
            pendiente, ordenada = np.polyfit(x, y, 1)   # pixeles por micropaso
            residuos = y - (pendiente * x + ordenada)
            varianza = ((y - y.mean()) ** 2).sum()
            r2 = 1.0 - (residuos ** 2).sum() / varianza if varianza > 0 else 0.0

            if abs(pendiente) < 1e-9:
                raise RuntimeError(
                    "el corrimiento no cambia con la posicion -- revisar que "
                    "la matriz este haciendo LEFT/RIGHT de verdad y que el "
                    "campo no este vacio")

            foco = int(round(-ordenada / pendiente))
            confiable = bool(r2 >= R2_MINIMO and
                             np.mean(respuestas) >= RESPUESTA_MINIMA_CALIBRACION)
            um = um_por_micropaso(motor.microsteps)
            cal = {
                "micropasos_por_pixel": float(1.0 / pendiente),
                "pendiente_px_por_micropaso": float(pendiente),
                "um_por_pixel": float(abs(1.0 / pendiente) * um),
                "eje": eje,
                "microsteps": motor.microsteps,
                "r2": float(r2),
                "respuesta_media": float(np.mean(respuestas)),
                "fecha": datetime.now().isoformat(timespec="seconds"),
            }
            if confiable:
                self.calibracion[camera_num] = cal
                self._guardar_calibracion()
                motor.mover_a(foco, delay=delay, backlash=backlash)
            else:
                # Una calibracion mala NO reemplaza a la anterior: si se
                # guardara, cada autofoco posterior saltaria segun una
                # pendiente que no corresponde, y el "foco" de esta recta
                # tampoco vale, asi que se vuelve a donde estaba.
                motor.mover_a(inicial, delay=delay, backlash=backlash)

            return {
                "camera": camera_num,
                "calibracion": cal,
                "guardada": confiable,
                "posicion": motor.position,
                "r2": float(r2),
                "confiable": confiable,
                "curva": [[int(p), float(c)] for p, c in
                          zip(posiciones, corrimientos)],
                "segundos": round(time.monotonic() - t0, 1),
            }
        finally:
            try:
                motor.disable()
            except Exception:
                pass
            if luz is not None and apagar_luz:
                try:
                    luz.off()
                except Exception:
                    pass

    # =============================
    # ETAPA 2: ajuste fino sobre la nitidez del DPC
    # =============================
    def _ajuste_fino(self, camera_num, motor, eje, rango, puntos,
                     delay, settle, roi, backlash):
        """Barre `puntos` planos alrededor de la posicion actual midiendo
        el corrimiento L/R en cada uno, y va a donde se anula.

        Con calibracion, cada plano da su propia estimacion del foco
        (posicion - delta * micropasos_por_pixel) y se toma la MEDIANA de
        todas: promedia el ruido de las siete lecturas y descarta una
        lectura espuria de la correlacion. Sin calibracion (autofoco IA
        sobre una camara no calibrada) se ajusta una recta y se usa su
        cero. Ver el encabezado del modulo: por que no es la parabola
        sobre el Tenengrad que se usaba antes.

        Nunca se aleja mas alla del barrido: si la estimacion cae afuera
        se queda en el borde y lo informa (en_borde), porque el que
        deberia haber llegado cerca es la etapa gruesa.

        Todos los puntos se miden avanzando en el mismo sentido y la
        posicion final se alcanza tambien desde el mismo lado, porque el
        backlash del husillo (5-20 um) es mayor que todo este rango.
        """
        puntos = max(3, int(puntos))
        paso = max(1, int(round(rango / (puntos - 1))))
        inicio = int(round(motor.position - paso * (puntos - 1) / 2))

        luz = self.illuminations.get(camera_num)
        if luz is not None:
            luz.on()
        centro = motor.position
        motor.mover_a(inicio, delay=delay, backlash=backlash, mantener=True)
        curva, nitideces = [], []
        for i in range(puntos):
            if i:
                motor.mover(paso, direction=1, delay=delay, mantener=True)
            m = self.medir_par(camera_num, eje=eje, settle=settle, roi=roi)
            curva.append((motor.position, m["delta_px"], m["respuesta"]))
            nitideces.append(m["nitidez"])

        validos = [(p, d) for p, d, r in curva if r >= RESPUESTA_MINIMA]
        if self.calibrado(camera_num) and validos:
            k = self._micropasos_por_pixel(camera_num, motor)
            estimado = int(round(float(np.median(
                [p - d * k for p, d in validos]))))
        else:
            estimado, _ = _cruce_cero(curva, lineal=True)
        if estimado is None:
            # Ninguna lectura confiable: no hay en que basarse para
            # moverse, se vuelve a donde dejo la etapa gruesa.
            estimado = centro

        lo, hi = curva[0][0], curva[-1][0]
        destino = max(lo, min(hi, estimado))
        motor.mover_a(destino, delay=delay, backlash=backlash, mantener=True)
        return {
            "posicion": destino,
            "estimado": estimado,
            "nitidez": max(nitideces),
            "en_borde": not (lo < estimado < hi),
            "paso_micropasos": paso,
            "curva": [[int(p), float(d)] for p, d, _ in curva],
            "nitidez_curva": [[int(p), float(v)]
                              for (p, _, _), v in zip(curva, nitideces)],
        }

    # =============================
    # ENFOQUE COMPLETO: etapa 1 + etapa 2
    # =============================
    def enfocar_dpc(self, camera_num, iteraciones=2, settle=0.25, roi=0.8,
                    delay=0.003, backlash=64, tolerancia_px=0.3,
                    max_micropasos=4000, respuesta_minima=RESPUESTA_MINIMA,
                    repeticiones=3, fino=True, rango_fino_um=8.0,
                    puntos_fino=7, apagar_luz=True, rango_max=None):
        """Etapa 1 (salto grueso con signo) + etapa 2 (ajuste fino).

        iteraciones son las correcciones de la etapa 1: la primera deja
        un residuo (la calibracion tiene error y el husillo juego) y la
        segunda lo limpia, sirviendo ademas de verificacion.

        repeticiones: cuantas mediciones L/R rapidas se combinan (por
        mediana, ver medir_par) en CADA iteracion de la etapa 1, antes de
        decidir una correccion. Una sola lectura es barata pero ruidosa
        -- justo cerca del foco, que es donde el salto grueso tiene que
        terminar de converger, delta tiende a cero y una lectura sola
        puede errar de signo. 3 repeticiones cuesta ~3x el tiempo de esa
        iteracion (sigue siendo un par de segundos), a cambio de no
        mandar una correccion basada en ruido.

        La etapa 2 existe porque cerca del foco delta tiende a cero y una
        sola medicion se vuelve ruido: el salto grueso deja a ~1-2 um, y
        la etapa fina lo afina midiendo delta en varios planos.

        max_micropasos topea cada correccion: sin finales de carrera, una
        correlacion mala no puede mandar la plataforma contra la muestra.

        rango_max (micropasos, amplitud total como en el barrido): la
        plataforma no se aleja mas de rango_max/2 de donde arranco. Es el
        mismo rango que respeta el barrido, para que los dos metodos se
        comporten igual: antes el DPC lo ignoraba y podia saltar hasta
        2 x max_micropasos (2.5 mm a 1/16) mientras el barrido se quedaba
        en +-30 um.

        Si una correccion EMPEORA el corrimiento en vez de achicarlo, la
        calibracion no corresponde (otro objetivo, otra muestra, o se
        calibro lejos del foco): vuelve a la posicion inicial y falla,
        para que enfocar_auto() caiga al barrido en vez de dejar la
        plataforma donde la mando una pendiente equivocada.
        """
        motor = self.motores.get(camera_num)
        if motor is None:
            raise RuntimeError(f"cam{camera_num} no tiene motor de enfoque")
        if camera_num not in self.calibracion:
            raise RuntimeError(
                f"cam{camera_num} sin calibrar -- correr calibrar_dpc() una "
                f"vez, o usar el barrido de nitidez")

        cal = self.calibracion[camera_num]
        eje = cal.get("eje", "lr")
        k = self._micropasos_por_pixel(camera_num, motor)
        luz = self.illuminations.get(camera_num)
        inicial = motor.position
        t0 = time.monotonic()
        pasos = []
        convergio = False
        topado = False
        if rango_max is not None:
            limite_bajo = inicial - abs(int(rango_max)) // 2
            limite_alto = inicial + abs(int(rango_max)) // 2
        else:
            limite_bajo, limite_alto = None, None

        try:
            # ---- Etapa 1: direccion y magnitud, sin barrer ----
            for _ in range(max(1, int(iteraciones))):
                m = self.medir_par(camera_num, eje=eje, settle=settle, roi=roi,
                                   repeticiones=repeticiones)
                pasos.append({"corrimiento_px": round(m["delta_px"], 3),
                              "respuesta": round(m["respuesta"], 3),
                              "posicion": motor.position})
                if m["respuesta"] < respuesta_minima:
                    raise RuntimeError(
                        f"correlacion sin enganche (respuesta "
                        f"{m['respuesta']:.3f}) -- campo vacio o desenfoque "
                        f"fuera del rango lineal; probar el barrido")
                # (Si la correccion anterior quedo acotada por el rango, es
                # normal que delta casi no haya bajado: no es divergencia.)
                if len(pasos) > 1 and not topado:
                    antes = abs(pasos[-2]["corrimiento_px"])
                    ahora = abs(m["delta_px"])
                    if antes > 2 * tolerancia_px and ahora > 0.8 * antes:
                        motor.mover_a(inicial, delay=delay, backlash=backlash,
                                      mantener=True)
                        raise RuntimeError(
                            f"la correccion DPC no acerco al foco "
                            f"({antes:.2f} px -> {ahora:.2f} px): la "
                            f"calibracion no corresponde a esta muestra u "
                            f"objetivo -- recalibrar cerca del foco")
                if abs(m["delta_px"]) <= tolerancia_px:
                    convergio = True
                    break
                if topado:
                    # Ya estaba contra el limite del rango y sigue pidiendo
                    # ir mas alla: el foco esta afuera.
                    break
                correccion = int(round(-m["delta_px"] * k))
                correccion = max(-max_micropasos, min(max_micropasos, correccion))
                destino = motor.position + correccion
                if limite_bajo is not None:
                    acotado = max(limite_bajo, min(limite_alto, destino))
                    topado = acotado != destino
                    destino = acotado
                if destino == motor.position:
                    break
                motor.mover_a(destino, delay=delay,
                              backlash=backlash, mantener=True)

            resultado = {
                "camera": camera_num,
                "metodo": "dpc",
                "posicion_inicial": inicial,
                # Ultima medicion de la etapa 1. Si convergio=True es el
                # residuo final; si no, es el que habia ANTES de la ultima
                # correccion (medirlo de nuevo costaria otro par de fotos,
                # y de eso se encarga la etapa fina).
                "corrimiento_px": pasos[-1]["corrimiento_px"],
                "convergio": convergio,
                # El foco que pidio la etapa 1 cae fuera de +-rango_max/2:
                # la plataforma quedo en el borde. Mismo nombre que en el
                # barrido para que la interfaz avise igual.
                "fuera_de_rango": topado,
                "respuesta": pasos[-1]["respuesta"],
                "iteraciones": pasos,
                "posicion_grueso": motor.position,
                "microsteps": motor.microsteps,
            }

            # ---- Etapa 2: cero del corrimiento medido en varios planos ----
            # Si la etapa 1 quedo contra el limite del rango, el foco esta
            # afuera: afinar ahi no tiene sentido y el barrido fino se
            # saldria del rango pedido.
            if fino and not topado:
                rango = max(puntos_fino - 1,
                            micropasos_por_um(rango_fino_um, motor.microsteps))
                f = self._ajuste_fino(camera_num, motor, eje, rango,
                                      puntos_fino, delay, settle, roi, backlash)
                resultado["fino"] = {
                    "correccion": f["posicion"] - resultado["posicion_grueso"],
                    "nitidez": f["nitidez"],
                    "en_borde": f["en_borde"],
                    "paso_micropasos": f["paso_micropasos"],
                    "curva": f["curva"],
                    "nitidez_curva": f["nitidez_curva"],
                }
                resultado["nitidez"] = f["nitidez"]

            resultado["posicion"] = motor.position
            resultado["desplazamiento"] = motor.position - inicial
            resultado["desplazamiento_um"] = round(
                resultado["desplazamiento"] * um_por_micropaso(motor.microsteps), 2)
            resultado["segundos"] = round(time.monotonic() - t0, 1)
            self.ultimo[camera_num] = resultado
            return resultado
        finally:
            try:
                motor.disable()
            except Exception:
                pass
            if luz is not None and apagar_luz:
                try:
                    luz.off()
                except Exception:
                    pass

    # =============================
    # RESPALDO: barrido a ciegas
    # =============================
    def _medir(self, camera_num, metrica, eje, settle, roi, patron,
              repeticiones=1):
        """Nitidez en la posicion actual, por el metodo pedido.

        metrica="corrimiento" (la de por defecto): no es una nitidez,
          es el corrimiento L/R con signo, y devuelve (delta_px,
          respuesta). El barrido busca donde cruza el cero (ver
          _cruce_cero). Es la unica de las tres que vale igual para
          muestras de fase y para muestras que absorben, y no necesita
          calibracion.
        metrica="dpc": Tenengrad sobre (L-R)/(L+R). Es la que hay que
          usar con objetos de FASE (celulas vivas sin tenir), donde la
          metrica cruda tiene un valle en el foco.
        metrica="bruta": varianza del Laplaciano sobre la imagen tal
          cual, con el patron de luz que se le pase. Mas barata (una sola
          imagen por punto).

        OJO, no es que una sea mejor: son para muestras distintas. Con un
        objeto puramente ABSORBENTE (tenido, pigmentado) pasa lo
        contrario que con uno de fase -- en el foco las dos medias
        aperturas dan la misma imagen, el DPC se anula y su Tenengrad
        tiene un valle justo donde deberia tener el pico. Para ese tipo
        de muestra hay que pasar metrica="bruta". Ademas, aun con objetos
        de fase, el Tenengrad del DPC es muy plano cerca del foco: con el
        ruido real del sensor el maximo cae en cualquier lado. Por las
        dos cosas el default paso a ser "corrimiento".
        """
        if metrica == "corrimiento":
            m = self.medir_par(camera_num, eje=eje, settle=settle, roi=roi,
                               repeticiones=repeticiones)
            return m["delta_px"], m["respuesta"]
        if metrica == "dpc":
            return self.medir_par(camera_num, eje=eje, settle=settle,
                                  roi=roi, repeticiones=repeticiones)["nitidez"]
        luz = self.illuminations.get(camera_num)
        if luz is not None and patron:
            getattr(luz, patron)()
        time.sleep(settle)
        return medir_nitidez(self.camera.get_focus_frame(camera_num), roi=roi)

    def _barrido(self, camera_num, motor, centro, rango, puntos, metrica,
                 eje, delay, settle, roi, backlash, patron, repeticiones=1):
        """Mide `puntos` posiciones equiespaciadas cubriendo `rango`
        micropasos centrados en `centro`. Devuelve [(posicion, nitidez)],
        o [(posicion, delta_px, respuesta)] con metrica="corrimiento".

        Arranca yendo al extremo inferior del rango con compensacion de
        backlash y de ahi avanza siempre en el mismo sentido.
        """
        puntos = max(3, int(puntos))
        paso = max(1, int(round(rango / (puntos - 1))))
        inicio = int(round(centro - paso * (puntos - 1) / 2))

        # El primer movimiento posiciona en el extremo del rango a barrer
        # -- puede ser varios cientos de micropasos, EN CIEGO, antes de
        # que ninguna medicion prenda la luz. Sin finales de carrera
        # (TODO_HW 1.5), moverse a ciegas y sin luz es mas riesgo del
        # necesario: se prende una referencia de campo claro ANTES de
        # este salto, para que haya señal visual durante todo el barrido,
        # no solo durante los puntos de medicion.
        luz = self.illuminations.get(camera_num)
        if luz is not None:
            luz.on()

        motor.mover_a(inicio, delay=delay, backlash=backlash, mantener=True)
        curva = []
        for i in range(puntos):
            if i:
                motor.mover(paso, direction=1, delay=delay, mantener=True)
            valor = self._medir(camera_num, metrica, eje, settle, roi, patron,
                                repeticiones=repeticiones)
            if isinstance(valor, tuple):
                curva.append((motor.position, *valor))
            else:
                curva.append((motor.position, valor))
        return curva, paso

    def enfocar(self, camera_num, rango=3200, puntos=13, refinamientos=2,
                delay=0.003, settle=0.15, roi=0.6, backlash=64,
                metrica="corrimiento", eje="lr", corriente_ma=None,
                usar_luz=True, patron="on", repeticiones=1, apagar_luz=True):
        """Barrido grueso-a-fino. Es el RESPALDO: se usa mientras una
        camara no tenga calibracion DPC, o cuando la etapa 1 del DPC no
        engancha o no converge.

        Con metrica="corrimiento" (default) busca donde el corrimiento
        L/R cruza el cero: el mismo criterio de foco que el metodo DPC,
        asi que los dos terminan en el mismo plano. Antes el respaldo
        maximizaba el Tenengrad del DPC, que con muestras que absorben
        tiene un valle en el foco y con objetos de fase es tan plano que
        el maximo cae en cualquier lado: por eso cada metodo dejaba la
        plataforma en un lugar distinto. "dpc" y "bruta" siguen
        disponibles para comparar.
        """
        motor = self.motores.get(camera_num)
        if motor is None:
            raise RuntimeError(f"cam{camera_num} no tiene motor de enfoque")

        luz = self.illuminations.get(camera_num) if usar_luz else None
        inicial = motor.position
        t0 = time.monotonic()

        if corriente_ma is not None:
            motor.set_current(irun_ma=corriente_ma)

        try:
            # Con metrica dpc/corrimiento la luz la maneja medir_par() en
            # cada punto (necesita las dos medias aperturas); con metrica
            # bruta se enciende una vez el patron pedido y se deja.
            if luz is not None and metrica not in ("dpc", "corrimiento"):
                getattr(luz, patron)()
                time.sleep(0.3)

            curvas = []
            centro, amplitud, n = inicial, rango, puntos
            mejor_pos, mejor_val, en_borde = inicial, None, False

            for etapa in range(1 + max(0, int(refinamientos))):
                curva, paso = self._barrido(
                    camera_num, motor, centro, amplitud, n, metrica, eje,
                    delay, settle, roi, backlash, patron,
                    repeticiones=repeticiones)
                curvas.append(curva)

                if metrica == "corrimiento":
                    # Los refinamientos son chicos y estan en el regimen
                    # lineal: ahi la recta por todos los puntos promedia
                    # el ruido. El barrido grueso puede tocar la zona
                    # saturada, asi que alli solo se interpola.
                    pos, en_borde = _cruce_cero(curva, lineal=etapa > 0)
                    if pos is None:
                        # Ninguna lectura engancho (campo vacio o muy
                        # lejos del foco): no hay base para moverse.
                        mejor_pos = inicial if etapa == 0 else mejor_pos
                        break
                    mejor_pos = pos
                    mejor_val = min(curva, key=lambda c: abs(c[0] - pos))[1]
                else:
                    i_max = max(range(len(curva)), key=lambda i: curva[i][1])
                    en_borde = i_max in (0, len(curva) - 1)
                    mejor_val = curva[i_max][1]
                    mejor_pos = _pico_parabolico(curva, i_max)

                if en_borde:
                    # El maximo esta fuera de lo barrido: refinar
                    # alrededor de un borde no aporta nada, es preferible
                    # cortar y avisar.
                    break

                centro = mejor_pos
                amplitud = paso * 2
                n = 9

            # Posicion final SIEMPRE alcanzada desde el mismo lado.
            motor.mover_a(mejor_pos, delay=delay, backlash=backlash)

            resultado = {
                "camera": camera_num,
                "metodo": "barrido",
                "metrica": metrica,
                "posicion": mejor_pos,
                "posicion_inicial": inicial,
                "desplazamiento": mejor_pos - inicial,
                "desplazamiento_um": round(
                    (mejor_pos - inicial) * um_por_micropaso(motor.microsteps), 2),
                "fuera_de_rango": en_borde,
                "etapas": len(curvas),
                "curva": [[c[0], c[1]] for c in curvas[0]],
                "curva_fina": ([[c[0], c[1]] for c in curvas[-1]]
                               if len(curvas) > 1 else []),
                "segundos": round(time.monotonic() - t0, 1),
                "microsteps": motor.microsteps,
            }
            # Con "corrimiento" la curva es delta (px), no nitidez.
            if metrica == "corrimiento":
                resultado["corrimiento_px"] = (
                    None if mejor_val is None else round(float(mejor_val), 3))
            else:
                resultado["nitidez"] = mejor_val
            self.ultimo[camera_num] = resultado
            return resultado

        finally:
            # Si algo falla a mitad del barrido, el driver quedaria
            # habilitado (los movimientos internos usan mantener=True).
            try:
                motor.disable()
            except Exception:
                pass
            if luz is not None and apagar_luz:
                try:
                    luz.off()
                except Exception:
                    pass

    def enfocar_auto(self, camera_num, metodo="auto", rango=3200, puntos=13,
                     refinamientos=2, iteraciones=2, usar_luz=True,
                     patron="on", metrica="corrimiento", **kw):
        """Punto de entrada unico: usa las dos etapas si hay calibracion
        y cae al barrido si no, o si la correlacion no engancha.

        Es lo que llaman la interfaz web y el timelapse, para que el
        comportamiento por defecto sea el rapido sin dejar de funcionar
        en una camara todavia sin calibrar.

        Si un metodo falla a mitad de camino, la plataforma VUELVE a la
        posicion inicial antes de probar el siguiente (antes el barrido
        arrancaba desde donde el otro la habia dejado), y el resultado
        trae "aviso" con el motivo, para que la interfaz lo muestre en
        vez de que parezca que el autofoco hace cosas distintas sin
        razon.
        """
        if metodo not in ("auto", "dpc", "barrido", "ia"):
            raise ValueError(f"metodo invalido: {metodo}")

        # La red va primero en "auto" cuando hay modelo: aprende la
        # curva completa (saturacion incluida), asi que arranca bien
        # desde mas lejos que la recta calibrada. Si falla, se cae al
        # metodo analitico, que no depende de ningun archivo.
        motor = self.motores.get(camera_num)
        inicial = motor.position if motor is not None else None
        avisos = []

        def volver():
            if motor is None or motor.position == inicial:
                return
            try:
                motor.mover_a(inicial, delay=kw.get("delay", 0.003),
                              backlash=kw.get("backlash", 64))
            except Exception as e:
                print(f"[autofoco] cam{camera_num}: no se pudo volver a "
                      f"la posicion inicial ({e})")

        def con_aviso(r):
            if avisos:
                r["aviso"] = "; ".join(avisos)
                r["posicion_inicial"] = inicial
                r["desplazamiento"] = r["posicion"] - inicial
                r["desplazamiento_um"] = round(
                    r["desplazamiento"] * um_por_micropaso(motor.microsteps), 2)
            return r

        tiene_ia = self.ia is not None and self.ia.disponible()
        if metodo == "ia" and not tiene_ia:
            raise RuntimeError(
                "autofoco IA no disponible: "
                + (self.ia.error if self.ia else "sin modelo cargado"))
        if tiene_ia and metodo in ("auto", "ia"):
            try:
                return self.ia.enfocar(self, camera_num,
                                       iteraciones=iteraciones, **kw)
            except Exception as e:
                if metodo == "ia":
                    raise
                print(f"[autofoco] cam{camera_num}: IA fallo ({e}); "
                      f"se cae al metodo analitico")
                avisos.append(f"IA fallo: {e}")
                volver()

        if metodo in ("auto", "dpc") and self.calibrado(camera_num):
            try:
                return con_aviso(self.enfocar_dpc(
                    camera_num, iteraciones=iteraciones, rango_max=rango, **kw))
            except Exception as e:
                if metodo == "dpc":
                    raise
                print(f"[autofoco] cam{camera_num}: DPC fallo ({e}); "
                      f"se cae al barrido")
                avisos.append(f"DPC fallo: {e}")
                volver()
        elif metodo == "dpc":
            raise RuntimeError(f"cam{camera_num} sin calibrar para DPC")

        # usar_luz/patron/metrica son del barrido: la etapa 1 necesita
        # forzosamente las medias aperturas, no un patron a eleccion.
        return con_aviso(self.enfocar(
            camera_num, rango=rango, puntos=puntos,
            refinamientos=refinamientos, usar_luz=usar_luz,
            patron=patron, metrica=metrica, **kw))
