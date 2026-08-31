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
import os
import time
import threading
import numpy as np
import cv2
import tifffile

# Resolucion nativa del IMX219.
STILL_SIZE = (3280, 2464)
PREVIEW_SIZE = (640, 480)

# TODO-HW: formato raw. En Pi 4 (Unicam) "SBGGR10" devolvia un buffer que
# .view(np.uint16) interpretaba correctamente. El Pi 5 usa PiSP/CFE y puede
# entregar el mismo stream como SBGGR10_CSI2P (empaquetado 10-bit en 5 bytes
# por cada 4 pixeles) o ya desempaquetado a 16 bit. Verificar con:
#     picam2.sensor_modes
#     request.make_array("raw").shape / .dtype
# antes de dar por buena la primera captura. Ver TODO_HW.md (prioridad 1).
RAW_FORMAT = "SBGGR10"


class CameraController:

    def __init__(self, camera_nums=(0, 1)):
        self.camera_nums = tuple(camera_nums)
        self.exposure_time = 12000
        self.gain = 1.2

        self._cams = {}                      # camera_num -> Picamera2
        self._modes = {}                     # camera_num -> "still"|"preview"|None
        self._locks = {n: threading.RLock() for n in self.camera_nums}
        self._open_lock = threading.Lock()   # protege la creacion de instancias

        self.preview_cam = None              # cual camara esta en modo vivo

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
                self._cams[camera_num] = Picamera2(camera_num=camera_num)
                self._modes[camera_num] = None
            return self._cams[camera_num]

    def _config(self, picam2, mode):
        if mode == "preview":
            # Baja resolucion, rapido, para video fluido
            return picam2.create_video_configuration(
                main={"size": PREVIEW_SIZE, "format": "RGB888"}
            )
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
        picam2.set_controls({
            "ExposureTime": self.exposure_time,
            "AnalogueGain": self.gain,
            "AeEnable": False,
            "AwbEnable": False
        })
        self._modes[camera_num] = mode
        # TODO-HW: 0.3 s heredado de Pi 4 para que los controles se apliquen.
        # En Pi 5 el pipeline es otro; si las primeras capturas salen con
        # exposicion incorrecta, subirlo o esperar por metadata real.
        time.sleep(0.3)
        return picam2

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
    def start_preview(self, camera_num):
        with self._locks[camera_num]:
            self._ensure_mode(camera_num, "preview")
            self.preview_cam = camera_num

    def get_preview_frame(self):
        """Devuelve un JPEG del frame actual, o None si no hay preview."""
        camera_num = self.preview_cam
        if camera_num is None:
            return None
        with self._locks[camera_num]:
            if self.preview_cam is None or self._modes.get(camera_num) != "preview":
                return None
            frame = self._cams[camera_num].capture_array("main")
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            return None
        return buf.tobytes()

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
            self.preview_cam = camera_num
            return self._cams[camera_num].capture_array("main")

    def stop_preview(self):
        camera_num = self.preview_cam
        self.preview_cam = None
        if camera_num is None:
            return
        # La instancia se deja viva y corriendo: en Pi 5 no hay mux que
        # liberar, y reabrirla costaria mas que mantenerla.
        with self._locks[camera_num]:
            pass

    # =============================
    # CAPTURE (debayer a gris 16-bit)
    # =============================
    def capture_image(self, camera_num, folder="captures", filename=None):
        os.makedirs(folder, exist_ok=True)

        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{folder}/img_{timestamp}.tif"

        with self._locks[camera_num]:
            if self.preview_cam == camera_num:
                self.preview_cam = None
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
        tifffile.imwrite(filename, gray)

        return filename

    def capture_both(self, folder="captures", filenames=None, camera_nums=None):
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
                if self.preview_cam == n:
                    self.preview_cam = None
                self._ensure_mode(n, "still")

        os.makedirs(folder, exist_ok=True)
        resultados = {}
        with ThreadPoolExecutor(max_workers=len(camera_nums)) as pool:
            futuros = {
                n: pool.submit(self.capture_image, n, folder, filenames[n])
                for n in camera_nums
            }
            for n, fut in futuros.items():
                resultados[n] = fut.result()
        return resultados

    # =============================
    def stop(self):
        self.preview_cam = None
        for camera_num in list(self._cams):
            with self._locks[camera_num]:
                self._shutdown(camera_num)
