from picamera2 import Picamera2
from datetime import datetime
import os
import time
import threading
import numpy as np
import cv2
import tifffile


class CameraController:

    def __init__(self):
        self.picam2 = None
        self.current_cam = None
        self.exposure_time = 12000
        self.gain = 1.2
        self.lock = threading.Lock()      # evita que preview y captura choquen
        self.preview_cam = None           # cual camara esta en modo vivo (None = ninguna)

    # =============================
    # ABRIR / CERRAR (modo captura)
    # =============================
    def _open(self, camera_num, preview=False):
        self._close()
        self.picam2 = Picamera2(camera_num=camera_num)
        self.current_cam = camera_num

        if preview:
            # Baja resolucion, rapido, para video fluido
            config = self.picam2.create_video_configuration(
                main={"size": (640, 480), "format": "RGB888"}
            )
        else:
            # Full res, raw, para captura cientifica
            config = self.picam2.create_still_configuration(
                raw={"size": (3280, 2464), "format": "SBGGR10"}
            )

        self.picam2.configure(config)
        self.picam2.start()
        self.picam2.set_controls({
            "ExposureTime": self.exposure_time,
            "AnalogueGain": self.gain,
            "AeEnable": False,
            "AwbEnable": False
        })
        time.sleep(0.3)

    def _close(self):
        if self.picam2 is not None:
            self.picam2.close()
            self.picam2 = None
            self.current_cam = None

    # =============================
    # EXPOSURE
    # =============================
    def set_exposure(self, exposure_us, gain):
        self.exposure_time = exposure_us
        self.gain = gain
        if self.picam2 is not None:
            self.picam2.set_controls({
                "ExposureTime": exposure_us,
                "AnalogueGain": gain
            })

    # =============================
    # PREVIEW EN VIVO (baja resolucion)
    # =============================
    def start_preview(self, camera_num):
        with self.lock:
            self._open(camera_num, preview=True)
            self.preview_cam = camera_num

    def get_preview_frame(self):
        """Devuelve un JPEG del frame actual, o None si no hay preview."""
        with self.lock:
            if self.picam2 is None or self.preview_cam is None:
                return None
            frame = self.picam2.capture_array("main")
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if not ok:
                return None
            return buf.tobytes()

    def stop_preview(self):
        with self.lock:
            self.preview_cam = None
            self._close()

    # =============================
    # CAPTURE (debayer a gris 16-bit)
    # =============================
    def capture_image(self, camera_num, folder="captures", filename=None):
        os.makedirs(folder, exist_ok=True)

        with self.lock:
            # Si habia preview activo, lo cerramos para capturar
            self.preview_cam = None
            self._open(camera_num, preview=False)

            if filename is None:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{folder}/img_{timestamp}.tif"

            request = self.picam2.capture_request()
            raw = request.make_array("raw")
            request.release()

            raw16 = np.ascontiguousarray(raw).view(np.uint16)
            gray = cv2.cvtColor(raw16, cv2.COLOR_BayerRG2GRAY)
            tifffile.imwrite(filename, gray)

            self._close()

        return filename

    # =============================
    def stop(self):
        with self.lock:
            self.preview_cam = None
            self._close()
