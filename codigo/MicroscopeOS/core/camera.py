"""Control de las dos camaras del microscopio.

Pi 5: las dos IMX219 cuelgan de los DOS puertos CSI nativos de la placa.
Ya no hay multiplexor Arducam de por medio, asi que:

  - cada camara tiene su propia instancia Picamera2 PERSISTENTE, en vez
    de abrir/cerrar una unica instancia en cada captura como en Pi 4;
  - las dos pueden capturar SIMULTANEAMENTE (ver capture_both).

En Pi 4 cada captura hacia Picamera2(camera_num=n) + configure + start +
close, ocho veces por ciclo DPC de dos camaras. Eso desaparece: la camara
solo se reconfigura cuando cambia de modo (preview <-> still).
"""

from picamera2 import Picamera2
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
import tempfile
import time
import threading
import numpy as np
import cv2
import tifffile

from core import metadatos

# Resolucion nativa del IMX219.
STILL_SIZE = (3280, 2464)
PREVIEW_SIZE = (640, 480)
# Modo del sensor para el vivo: el IMX219 completo, binneado 2x2. Si no se
# pide explicitamente, libcamera elige para 640x480 el modo nativo de
# 640x480, que lee solo un recorte central de ~1280x960 del sensor: el
# vivo mostraba ~40 % del ancho de la foto, como si tuviera zoom. Con
# este modo el vivo y las capturas ven el MISMO campo.
PREVIEW_RAW_SIZE = (1640, 1232)

# TODO-HW: formato raw. En Pi 4 (Unicam) "SBGGR10" devolvia un buffer que
# .view(np.uint16) interpretaba correctamente. El Pi 5 usa PiSP/CFE y puede
# entregar el mismo stream como SBGGR10_CSI2P (empaquetado 10-bit en 5 bytes
# por cada 4 pixeles) o ya desempaquetado a 16 bit. Verificar con:
#     picam2.sensor_modes
#     request.make_array("raw").shape / .dtype
# antes de dar por buena la primera captura. Ver TODO_HW.md (prioridad 1).
RAW_FORMAT = "SBGGR10"


def _tuning_microscopio():
    """Calibracion del ISP para el microscopio: la de fabrica del IMX219
    pero SIN rpi.alsc (correccion de sombreado de lente). Devuelve la ruta
    de un JSON para Picamera2(tuning=...).

    Las tablas de ALSC estan medidas para la lentecita de fabrica del
    modulo, no para la optica del microscopio: aplicadas aqui
    sobrecorregian rojo y azul hacia las orillas y el vivo salia con un
    anillo magenta y el centro de otro color, aunque la muestra fuera
    uniforme. Sin ALSC el ISP no toca el color por posicion; queda solo
    la vinieta real de la optica. Las capturas raw no pasan por el ISP,
    asi que esto solo cambia el vivo y los JPEG/PNG procesados.
    (Probado en la Pi el 2026-10-01: el centro paso de azul puro a casi
    blanco y la orilla de magenta a lila claro.)

    OJO: libcamera lee el tuning UNA vez, cuando arranca su CameraManager
    (el primer Picamera2 del proceso), y lo usa para las dos camaras. Por
    eso aqui no se consulta nada de la camara (global_camera_info ya
    arrancaria el manager con el tuning de fabrica): el sensor se fija a
    mano, igual que STILL_SIZE.

    Si algo falla se devuelve None y Picamera2 usa la calibracion de
    fabrica, como antes."""
    try:
        tuning = Picamera2.load_tuning_file("imx219.json")
        tuning["algorithms"] = [a for a in tuning["algorithms"]
                                if "rpi.alsc" not in a]
        ruta = os.path.join(tempfile.gettempdir(),
                            f"microscopeos_imx219_{os.getuid()}.json")
        with open(ruta, "w") as f:
            json.dump(tuning, f)
        return ruta
    except Exception as e:
        print(f"AVISO: sin tuning propio de la camara ({e}); "
              f"se usa el de fabrica")
        return None


class CameraController:

    def __init__(self, camera_nums=(0, 1)):
        self.camera_nums = tuple(camera_nums)
        self.exposure_time = 12000
        self.gain = 1.2

        self._cams = {}                      # camera_num -> Picamera2
        self._modes = {}                     # camera_num -> "still"|"preview"|None
        self._locks = {n: threading.RLock() for n in self.camera_nums}
        self._open_lock = threading.Lock()   # protege la creacion de instancias

        self.preview_cam = None              # compat: ultima camara activada
        self._preview_cams = set()           # camaras en vivo AHORA (puede haber 2)
        # core.metadatos.Contexto: si esta puesto, cada foto se guarda con
        # sus metadatos adentro (objetivo, escala, luz, foco...).
        self.metadatos = None
        self._tuning = _tuning_microscopio()  # ver docstring: sin ALSC
        # Calibracion de imagen por camara (ver calibrar_imagen()):
        #   _colour_gains[n] = (rojo, azul), balance de blancos fijo del ISP
        #   _flat[n]         = ganancia por pixel (h, w, 3) para el vivo
        self._colour_gains = {}
        self._flat = {}
        self._flat_cache = {}

    # =============================
    # CICLO DE VIDA DE LAS INSTANCIAS
    # =============================
    def _instance(self, camera_num):
        """Devuelve la Picamera2 de esa camara, creandola la primera vez."""
        with self._open_lock:
            if camera_num not in self._cams:
                # TODO-HW: en Pi 5, camera_num 0/1 corresponde a los conectores
                # CAM0/CAM1 de la placa. Confirmar con `rpicam-hello --list-cameras`
                # que el orden coincide con el cableado fisico; si estan
                # invertidos, se intercambian aqui y no en el resto del codigo.
                self._cams[camera_num] = Picamera2(
                    camera_num=camera_num, tuning=self._tuning)
                self._modes[camera_num] = None
            return self._cams[camera_num]

    def _config(self, picam2, mode):
        if mode == "preview":
            # Baja resolucion, rapido, para video fluido, pero leyendo el
            # sensor entero (ver PREVIEW_RAW_SIZE).
            return picam2.create_video_configuration(
                main={"size": PREVIEW_SIZE, "format": "RGB888"},
                raw={"size": PREVIEW_RAW_SIZE},
            )
        # (el modo still va mas abajo)
        # Full res, raw, para captura cientifica
        return picam2.create_still_configuration(
            raw={"size": STILL_SIZE, "format": RAW_FORMAT}
        )

    def _ensure_mode(self, camera_num, mode):
        """Deja la camara corriendo en el modo pedido, reconfigurando solo
        si hace falta. Si ya esta en ese modo no cuesta nada."""
        picam2 = self._instance(camera_num)

        if self._modes.get(camera_num) == mode:
            return picam2

        if self._modes.get(camera_num) is not None:
            picam2.stop()

        picam2.configure(self._config(picam2, mode))
        picam2.start()
        controles = {
            "ExposureTime": self.exposure_time,
            "AnalogueGain": self.gain,
            "AeEnable": False,
            "AwbEnable": False
        }
        if camera_num in self._colour_gains:
            controles["ColourGains"] = self._colour_gains[camera_num]
        if mode == "preview":
            # El stream de preview pasa por el ISP, que por defecto
            # realza bordes y hace reduccion de ruido. Las dos cosas
            # falsean lo que mide el autofoco: el realce inventa alto
            # contraste (nitidez donde no la hay) y el denoise borra
            # justo la textura fina con la que engancha la correlacion
            # de fase. Ademas el denoise es NO LINEAL, asi que actua
            # distinto en la imagen de la mitad izquierda que en la de
            # la derecha y contamina la resta del DPC.
            controles.update(self._controles_planos(picam2))
            # Y sin recorte digital: todo el campo que lee el sensor.
            maximo = (getattr(picam2, "camera_properties", {}) or {}).get(
                "ScalerCropMaximum")
            if maximo and "ScalerCrop" in (
                    getattr(picam2, "camera_controls", {}) or {}):
                controles["ScalerCrop"] = tuple(maximo)
        picam2.set_controls(controles)
        self._modes[camera_num] = mode
        # TODO-HW: 0.3 s heredado de Pi 4 para que los controles se apliquen.
        # En Pi 5 el pipeline es otro; si las primeras capturas salen con
        # exposicion incorrecta, subirlo o esperar por metadata real.
        time.sleep(0.3)
        return picam2

    @staticmethod
    def _controles_planos(picam2):
        """Controles de ISP que hay que apagar para medir foco, los que
        esta build de libcamera soporte.

        Se consultan contra picam2.camera_controls en vez de fijarlos a
        ciegas: los nombres y los enums de NoiseReductionMode cambiaron
        entre versiones de libcamera, y un control inexistente hace
        fallar el set_controls ENTERO -- incluida la exposicion.
        """
        disponibles = getattr(picam2, "camera_controls", {}) or {}
        controles = {}
        if "Sharpness" in disponibles:
            controles["Sharpness"] = 0.0
        if "NoiseReductionMode" in disponibles:
            try:
                from libcamera import controls as libcam_controls
                controles["NoiseReductionMode"] = \
                    libcam_controls.draft.NoiseReductionModeEnum.Off
            except Exception:
                pass
        return controles

    def _shutdown(self, camera_num):
        with self._open_lock:
            picam2 = self._cams.pop(camera_num, None)
            self._modes.pop(camera_num, None)
        if picam2 is not None:
            try:
                picam2.stop()
            except Exception:
                pass
            picam2.close()

    # =============================
    # CALIBRACION DE IMAGEN (campo vacio)
    # =============================
    # Con un portaobjetos vacio y campo claro, deja la imagen lo mas
    # pareja y neutra posible en tres pasos:
    #   1. exposicion: lo mas brillante del campo queda en ~80 % del
    #      rango. Un pixel saturado (255) no tiene informacion: una celula
    #      ahi desaparece, por eso esto va primero.
    #   2. balance de blancos: ColourGains fijos en el ISP, medidos en el
    #      centro, para que la luz blanca salga gris. Afecta al vivo y a
    #      las imagenes procesadas; el raw no pasa por el ISP.
    #   3. campo plano: un mapa de ganancia por pixel que iguala orillas y
    #      centro (vinieta de la optica + tinte del IMX219 en las orillas).
    #      Se aplica SOLO al JPEG del vivo: el autofoco (get_focus_frame),
    #      el conteo (gancho anotar, que recibe el frame sin corregir) y
    #      las capturas no lo ven, asi que no cambia ninguna medicion.
    # EN PRUEBAS: en la Pi (2026-10-01) el balance no convergio (las
    # ganancias llegaron al tope). Por eso, si una ganancia queda en el
    # tope, el resultado se marca "converge": False y no se guarda.
    OBJETIVO_BRILLO = 200       # de 255, para lo mas brillante del campo
    EXPOSICION_MIN_US = 100
    EXPOSICION_MAX_US = 200000
    GANANCIA_COLOR = (0.3, 8.0)

    @staticmethod
    def _promedio(picam2, n, descartar=4):
        """Promedio de n frames del preview (BGR float32), tirando antes
        los que pudieron exponerse con los controles anteriores."""
        for _ in range(descartar):
            picam2.capture_array("main")
        acum = None
        for _ in range(n):
            f = picam2.capture_array("main").astype(np.float32)
            acum = f if acum is None else acum + f
        return acum / n

    @staticmethod
    def _centro(img, frac=0.3):
        h, w = img.shape[:2]
        dh, dw = int(h * frac / 2), int(w * frac / 2)
        return img[h // 2 - dh:h // 2 + dh, w // 2 - dw:w // 2 + dw]

    def calibrar_imagen(self, camera_num):
        """Calibra exposicion, balance de blancos y campo plano de una
        camara. La luz de campo claro (blanca) tiene que estar encendida y
        el campo vacio; eso lo arregla quien llama (server/api.py).

        Devuelve {"exposure_us", "colour_gains", "brillo_max", "saturado",
        "converge"}. Solo si converge se dejan puestos el balance y el
        campo plano. La exposicion NO se aplica a las dos camaras aqui: es
        global, y quien llama decide (la menor, para que ninguna sature)."""
        lo, hi = self.GANANCIA_COLOR
        with self._locks[camera_num]:
            picam2 = self._ensure_mode(camera_num, "preview")
            exposicion = self.exposure_time
            meta = picam2.capture_metadata()
            rojo, azul = meta.get("ColourGains") or (1.0, 1.0)
            # Para volver atras si no converge: lo guardado, o lo que tenia.
            previos = self._colour_gains.get(camera_num) or (rojo, azul)

            for _ in range(5):
                # 1. exposicion: el p99.5 del canal mas alto al objetivo.
                img = self._promedio(picam2, 3)
                pico = float(np.percentile(img.max(axis=2), 99.5))
                if abs(pico - self.OBJETIVO_BRILLO) > 10:
                    if pico >= 250:
                        factor = 0.5        # saturado: no se sabe cuanto
                    else:
                        # valores con gamma (~2.2): pasar a lineal
                        factor = (self.OBJETIVO_BRILLO / max(pico, 1)) ** 2.2
                    exposicion = int(min(self.EXPOSICION_MAX_US, max(
                        self.EXPOSICION_MIN_US,
                        exposicion * min(4.0, max(0.25, factor)))))
                    picam2.set_controls({"ExposureTime": exposicion})
                    img = self._promedio(picam2, 3)

                # 2. balance de blancos en el centro, en lineal.
                b, g, r = (self._centro(img).reshape(-1, 3).mean(0)
                           / 255.0) ** 2.2
                rojo = float(min(hi, max(lo, rojo * g / max(r, 1e-4))))
                azul = float(min(hi, max(lo, azul * g / max(b, 1e-4))))
                picam2.set_controls({"ColourGains": (rojo, azul)})

                img = self._promedio(picam2, 3)
                pico = float(np.percentile(img.max(axis=2), 99.5))
                b, g, r = self._centro(img).reshape(-1, 3).mean(0)
                if abs(pico - self.OBJETIVO_BRILLO) <= 10 and \
                        max(abs(r - g), abs(b - g)) <= 3:
                    break

            converge = lo < rojo < hi and lo < azul < hi
            if converge:
                # 3. campo plano, con la imagen ya expuesta y balanceada.
                img = self._promedio(picam2, 16)
            else:
                picam2.set_controls({"ColourGains": previos})

        if converge:
            # Suavizado fuerte: el mapa tiene que seguir la vinieta, no el
            # polvo ni el ruido (eso se "pintaria" en el vivo).
            plano = cv2.GaussianBlur(img, (0, 0), sigmaX=img.shape[1] / 32)
            referencia = float(np.percentile(plano.mean(axis=2), 99))
            ganancia = np.clip(referencia / np.maximum(plano, 1.0), 0.5, 4.0)
            self._colour_gains[camera_num] = (rojo, azul)
            self.set_flat(camera_num, ganancia)
        return {"exposure_us": exposicion,
                "colour_gains": [round(rojo, 3), round(azul, 3)],
                "brillo_max": round(pico, 1),
                "saturado": pico >= 250,
                "converge": converge}

    def set_colour_gains(self, camera_num, gains):
        """Balance de blancos fijo (rojo, azul), o None para quitarlo."""
        if gains is None:
            self._colour_gains.pop(camera_num, None)
            return
        self._colour_gains[camera_num] = tuple(float(x) for x in gains)
        with self._locks[camera_num]:
            if self._modes.get(camera_num) is not None:
                self._cams[camera_num].set_controls(
                    {"ColourGains": self._colour_gains[camera_num]})

    def set_flat(self, camera_num, ganancia):
        """Mapa de ganancia (h, w, 3) para el vivo, o None para quitarlo."""
        if ganancia is None:
            self._flat.pop(camera_num, None)
        else:
            self._flat[camera_num] = np.asarray(ganancia, np.float32)
        self._flat_cache.pop(camera_num, None)

    def get_flat(self, camera_num):
        return self._flat.get(camera_num)

    def _aplicar_flat(self, camera_num, frame):
        ganancia = self._flat.get(camera_num)
        if ganancia is None or frame.ndim != 3:
            return frame
        cache = self._flat_cache.get(camera_num)
        if cache is None or cache.shape != frame.shape:
            cache = cv2.resize(ganancia, (frame.shape[1], frame.shape[0]),
                               interpolation=cv2.INTER_LINEAR)
            self._flat_cache[camera_num] = cache
        return cv2.convertScaleAbs(frame.astype(np.float32) * cache)

    # =============================
    # EXPOSURE
    # =============================
    def set_exposure(self, exposure_us, gain):
        self.exposure_time = exposure_us
        self.gain = gain
        for camera_num in list(self._cams):
            with self._locks[camera_num]:
                if self._modes.get(camera_num) is not None:
                    self._cams[camera_num].set_controls({
                        "ExposureTime": exposure_us,
                        "AnalogueGain": gain
                    })

    # =============================
    # PREVIEW EN VIVO (baja resolucion)
    # =============================
    # Pi 5: las dos camaras tienen puerto CSI propio, asi que a diferencia
    # de la Pi 4 (una preview a la vez, por el mux) las dos pueden estar en
    # vivo al mismo tiempo. self.preview_cam se mantiene como "la ultima
    # camara activada" solo por compatibilidad con get_frame() sin
    # argumentos (PreviewManager y la GUI PyQt6, que siguen pidiendo una
    # sola camara); el estado real de que esta en vivo es self._preview_cams.
    def start_preview(self, camera_num):
        with self._locks[camera_num]:
            self._ensure_mode(camera_num, "preview")
            self._preview_cams.add(camera_num)
            self.preview_cam = camera_num

    def get_preview_frame(self, camera_num, anotar=None):
        """Devuelve un JPEG del frame actual de esa camara, o None si no
        esta en vivo.

        `anotar(camera_num, frame)` es un gancho opcional que recibe el
        frame BGR antes de comprimirlo y devuelve el que se manda (lo
        usa el conteo de celulas para dibujar encima). Va aca y no en el
        endpoint porque el frame crudo solo existe dentro del lock: si
        el analisis lo pidiera por su cuenta seria una captura mas, y a
        16 fps eso duplica el trabajo de la camara.

        Si el gancho falla, se manda el frame sin anotar: una excepcion
        del analisis no tiene por que cortar el vivo.
        """
        if camera_num not in self._preview_cams:
            return None
        with self._locks[camera_num]:
            if camera_num not in self._preview_cams or \
                    self._modes.get(camera_num) != "preview":
                return None
            frame = self._cams[camera_num].capture_array("main")
        if anotar is not None:
            try:
                frame = anotar(camera_num, frame)
            except Exception:
                pass
        frame = self._aplicar_flat(camera_num, frame)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            return None
        return buf.tobytes()

    def get_focus_frame(self, camera_num, descartar=1):
        """Frame gris de baja resolucion para medir nitidez (autofoco).

        A diferencia de get_preview_frame() no comprime a JPEG (el
        artefacto de compresion se comeria justo el alto contraste que
        mide el autofoco) y a diferencia de get_frame() no marca la
        camara como "en vivo": el autofoco puede correr con el vivo
        apagado, incluso a mitad de un timelapse, y no debe dejar el
        stream MJPEG creyendo que hay alguien mirando.

        `descartar` tira los primeros N frames. NO es paranoia: el
        autofoco cambia la iluminacion (o mueve la plataforma) e
        inmediatamente pide una imagen, y la camara esta corriendo en
        continuo con requests ya en vuelo. El primer frame que devuelve
        capture_array() puede haberse EXPUESTO ANTES del cambio, asi
        que mediria la iluminacion anterior. Con dos medias aperturas
        que se comparan entre si, un frame viejo no da un error chico:
        da un corrimiento inventado.

        Deja la camara en modo preview; la proxima captura cientifica
        la devuelve a modo still sola (_ensure_mode).
        """
        with self._locks[camera_num]:
            self._ensure_mode(camera_num, "preview")
            picam2 = self._cams[camera_num]
            for _ in range(max(0, descartar)):
                picam2.capture_array("main")
            frame = picam2.capture_array("main")
        if frame.ndim == 3:
            return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return frame

    def get_frame(self, camera_num=None):
        """Frame RGB crudo del preview, para PreviewManager y la GUI PyQt6.

        Estos dos modulos ya lo llamaban en Pi 4 pero el metodo no existia
        en CameraController, asi que los modos 'preview'/'both'/'gui' de
        main.py estaban rotos. Se implementa aqui porque es donde va.
        """
        if camera_num is None:
            camera_num = self.preview_cam if self.preview_cam is not None \
                else self.camera_nums[0]
        with self._locks[camera_num]:
            self._ensure_mode(camera_num, "preview")
            self._preview_cams.add(camera_num)
            self.preview_cam = camera_num
            return self._cams[camera_num].capture_array("main")

    def stop_preview(self, camera_num=None):
        """Detiene el vivo de una camara, o de todas si no se especifica."""
        cams = [camera_num] if camera_num is not None else list(self._preview_cams)
        for n in cams:
            self._preview_cams.discard(n)
            # La instancia se deja viva y corriendo: en Pi 5 no hay mux que
            # liberar, y reabrirla costaria mas que mantenerla.
            with self._locks[n]:
                pass
        if self.preview_cam in cams:
            self.preview_cam = next(iter(self._preview_cams), None)

    # =============================
    # CAPTURE (debayer a gris 16-bit)
    # =============================
    def capture_image(self, camera_num, folder="captures", filename=None, meta=None):
        """meta: datos extra para los metadatos de esta foto (experimento,
        ciclo, canal). La luz y el resto los junta self.metadatos AHORA,
        antes de disparar, que es cuando valen."""
        os.makedirs(folder, exist_ok=True)
        info = (self.metadatos.para(camera_num, meta)
                if self.metadatos is not None else None)

        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{folder}/img_{timestamp}.tif"

        with self._locks[camera_num]:
            self._preview_cams.discard(camera_num)
            if self.preview_cam == camera_num:
                self.preview_cam = next(iter(self._preview_cams), None)
            self._ensure_mode(camera_num, "still")

            request = self._cams[camera_num].capture_request()
            try:
                raw = request.make_array("raw")
            finally:
                request.release()

        # TODO-HW: los dos pasos siguientes son el punto mas fragil de la
        # migracion. Si PiSP entrega el raw con otro empaquetado o stride,
        # esto NO lanza excepcion: produce una imagen de basura en silencio.
        # Validar con test_captura.py contra una muestra conocida.
        raw16 = np.ascontiguousarray(raw).view(np.uint16)
        # El CFE del Pi 5 alinea el stride de cada fila a un multiplo de 32
        # px (confirmado con picam2.camera_configuration()['raw']: stride=
        # 6592 bytes para un ancho real de 3280 px x 2 bytes = 6560 bytes,
        # 32 bytes = 16 px uint16 de relleno). Sin recortar, quedan 16
        # columnas de basura pegadas al borde derecho de cada fila.
        raw16 = raw16[:, :STILL_SIZE[0]]
        # TODO-HW: el buffer se pide como SBGGR10 (patron BG) pero se
        # debayerea como BayerRG. Esa inconsistencia venia de Pi 4 y ahi
        # daba imagen correcta; con el ISP nuevo hay que reconfirmar el
        # orden efectivo del patron Bayer antes de fiarse del resultado.
        gray = cv2.cvtColor(raw16, cv2.COLOR_BayerRG2GRAY)
        if info is not None:
            metadatos.escribir(filename, gray, info)
        else:
            tifffile.imwrite(filename, gray)

        return filename

    def capture_both(self, folder="captures", filenames=None, camera_nums=None, meta=None):
        """Captura de las dos camaras EN PARALELO.

        Solo es posible en Pi 5: con el mux de Pi 4 las capturas eran
        forzosamente secuenciales porque las dos camaras compartian un unico
        puerto CSI.

        OJO: dispara las dos a la vez, asi que ambas matrices de iluminacion
        estan encendidas simultaneamente. Solo es valido si los dos canales
        opticos estan aislados entre si. Ver TODO_HW.md (prioridad 2).

        Devuelve {camera_num: ruta}.
        """
        if camera_nums is None:
            camera_nums = self.camera_nums
        camera_nums = list(camera_nums)

        if filenames is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filenames = {n: os.path.join(folder, f"cam{n}_img_{timestamp}.tif")
                         for n in camera_nums}

        # Pre-configura las dos ANTES de disparar, para que el paralelo mida
        # solo la captura y no la reconfiguracion de una de ellas.
        for n in camera_nums:
            with self._locks[n]:
                self._preview_cams.discard(n)
                if self.preview_cam == n:
                    self.preview_cam = next(iter(self._preview_cams), None)
                self._ensure_mode(n, "still")

        os.makedirs(folder, exist_ok=True)
        resultados = {}
        with ThreadPoolExecutor(max_workers=len(camera_nums)) as pool:
            futuros = {
                n: pool.submit(self.capture_image, n, os.path.dirname(filenames[n]) or folder,
                               filenames[n], meta)
                for n in camera_nums
            }
            for n, fut in futuros.items():
                resultados[n] = fut.result()
        return resultados

    # =============================
    def stop(self):
        self.preview_cam = None
        self._preview_cams.clear()
        for camera_num in list(self._cams):
            with self._locks[camera_num]:
                self._shutdown(camera_num)
