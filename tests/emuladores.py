"""Stubs para probar la logica en la laptop, sin Pi ni hardware.

El emulador de la matriz sigue el README de extras/matrices_esp32s3_matrix
al pie de la letra, para que el test valide el protocolo real y no una
version idealizada del mismo.
"""
import sys, types, time, threading
import numpy as np

PATRONES = ("FULL", "LEFT", "RIGHT", "TOP", "BOTTOM", "OFF")


class MatrizESP32Fake:
    """Emula el firmware DPC de la Waveshare ESP32-S3-Matrix."""

    def __init__(self, mac="A0:F2:62:EB:21:A4", rot=90, fx=0, fy=1):
        self.mac, self.rot, self.fx, self.fy = mac, rot, fx, fy
        self.patron, self.brillo = "OFF", 0
        self.recibidos = []          # historial de lineas crudas
        self.resets = 0              # cuantas veces DTR+RTS reinicio la placa

    def procesar(self, linea):
        self.recibidos.append(linea)
        if len(linea) > 47:
            return "ERR:BAD_FORMAT:LINE_TOO_LONG"
        t = linea.strip()
        if t.upper() == "ID":
            return f"ID:MATRIZ:{self.mac}:ROT{self.rot}:FX{self.fx}:FY{self.fy}"
        if ":" not in t:
            return f"ERR:BAD_FORMAT:{t}"
        campos = t.split(":")
        if len(campos) != 2 or not campos[0].strip() or not campos[1].strip():
            return f"ERR:BAD_FORMAT:{t}"
        pat, bri = campos[0].strip().upper(), campos[1].strip()
        if pat not in PATRONES:
            return f"ERR:UNKNOWN_PATTERN:{pat}"
        try:
            n = int(bri)
        except ValueError:
            return f"ERR:BAD_BRIGHTNESS:{bri}"
        if not (0 <= n <= 255):
            return f"ERR:BAD_BRIGHTNESS:{bri}"
        self.patron, self.brillo = pat, n
        return f"OK:{pat}:{n}"


class SerialFake:
    """serial.Serial emulado, conectado a una MatrizESP32Fake."""
    _matrices = {}

    def __init__(self, *a, **kw):
        self.port = kw.get("port")
        self.baudrate = kw.get("baudrate", 9600)
        self.timeout = kw.get("timeout")
        self.write_timeout = None
        self.dsrdtr = kw.get("dsrdtr", True)
        self.rtscts = False
        self._dtr = True          # pyserial afirma DTR por defecto
        self._rts = True
        self.is_open = False
        self._buf = b""
        self._abierto_con = None

    # --- propiedades DTR/RTS: si ambas quedan afirmadas al abrir, reset ---
    @property
    def dtr(self): return self._dtr
    @dtr.setter
    def dtr(self, v): self._dtr = v
    @property
    def rts(self): return self._rts
    @rts.setter
    def rts(self, v): self._rts = v

    @property
    def matriz(self):
        return SerialFake._matrices.setdefault(self.port, MatrizESP32Fake())

    def open(self):
        self.is_open = True
        self._abierto_con = (self._dtr, self._rts)
        if self._dtr and self._rts:
            self.matriz.resets += 1
            # la placa reiniciada escupe basura del ROM por el CDC
            self._buf += b"ESP-ROM:esp32s3-20210327\r\nboot: ...\r\n"
        # el ROM escribe algo de arranque de todos modos
        self._buf += b"\x00"

    def close(self): self.is_open = False
    def reset_input_buffer(self): self._buf = b""
    def reset_output_buffer(self): pass

    def write(self, data):
        for linea in data.decode().split("\n"):
            if linea.strip() or linea == "":
                if linea == "" : continue
                r = self.matriz.procesar(linea.rstrip("\r"))
                if r: self._buf += (r + "\n").encode()
        return len(data)

    def readline(self):
        i = self._buf.find(b"\n")
        if i < 0:
            out, self._buf = self._buf, b""
            return out
        out, self._buf = self._buf[:i+1], self._buf[i+1:]
        return out

    def read(self, n=1):
        out, self._buf = self._buf[:n], self._buf[n:]
        return out


# ---------------- picamera2 stub ----------------
class RequestFake:
    def __init__(self, cam): self.cam = cam
    def make_array(self, nombre):
        # raw 10-bit visto como uint8: (alto, ancho*2) que .view(uint16) vuelve 2D
        h, w = 2464, 3280
        a = np.zeros((h, w * 2), dtype=np.uint8)
        a[::2, ::4] = self.cam.camera_num * 40 + 60   # patron distinguible por camara
        return a
    def release(self): self.cam.requests_liberadas += 1
    def get_metadata(self): return {"ExposureTime": self.cam.controles.get("ExposureTime")}


class Picamera2Fake:
    instancias_creadas = 0
    def __init__(self, camera_num=0):
        Picamera2Fake.instancias_creadas += 1
        self.camera_num = camera_num
        self.cerrada = False
        self.corriendo = False
        self.config = None
        self.controles = {}
        self.n_configure = 0
        self.n_start = 0
        self.requests_liberadas = 0
        self.sensor_modes = [{"format": "SBGGR10_CSI2P", "size": (3280, 2464)}]
    def create_video_configuration(self, **kw): return {"tipo": "video", **kw}
    def create_still_configuration(self, **kw): return {"tipo": "still", **kw}
    def configure(self, c): self.config = c; self.n_configure += 1
    def start(self): self.corriendo = True; self.n_start += 1
    def stop(self): self.corriendo = False
    def close(self): self.cerrada = True
    def set_controls(self, c): self.controles.update(c)
    def capture_array(self, nombre): return np.zeros((480, 640, 3), dtype=np.uint8)
    def capture_request(self):
        time.sleep(0.15)   # simula el coste real de una captura
        return RequestFake(self)


def instalar():
    m = types.ModuleType("picamera2"); m.Picamera2 = Picamera2Fake
    sys.modules["picamera2"] = m
    t = types.ModuleType("tifffile")
    t.escritos = {}
    def imwrite(fn, data):
        t.escritos[str(fn)] = data.shape
        open(fn, "wb").write(b"TIF")
    def imread(fn): return np.zeros((2464, 3280), dtype=np.uint16)
    t.imwrite, t.imread = imwrite, imread
    sys.modules["tifffile"] = t
    import serial
    serial.Serial = SerialFake
    return t
