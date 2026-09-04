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


class TMC2209Fake:
    """Emula un TMC2209 colgado del hilo PDN (UART de un solo cable).

    Sigue el datagrama del datasheet: sincronismo 0x05, direccion, bit 7
    del registro = escritura, CRC8 propio. Lo importante para las
    pruebas es que se comporta como un BUS: hace eco de todo lo que se
    escribe (los dos drivers y la Pi comparten un unico cable) y solo
    contesta el esclavo cuya direccion coincide.
    """

    # Valores de reset de fabrica reales (los que leyo el driver fisico
    # la primera vez que contesto: GCONF = 0x00000101).
    RESET = {0x00: 0x00000101, 0x6F: (12 << 16) | (1 << 30)}

    def __init__(self, direcciones=(0, 1)):
        self.regs = {a: dict(self.RESET) for a in direcciones}
        self.escrituras = []          # (direccion, registro, valor)

    @staticmethod
    def crc8(datos):
        crc = 0
        for byte in datos:
            b = byte
            for _ in range(8):
                if ((crc >> 7) & 1) != (b & 1):
                    crc = ((crc << 1) ^ 0x07) & 0xFF
                else:
                    crc = (crc << 1) & 0xFF
                b >>= 1
        return crc

    def procesar(self, datagrama):
        """Devuelve lo que aparece en el hilo: eco + respuesta si toca."""
        import struct
        salida = bytes(datagrama)                 # eco del bus
        direccion = datagrama[1] & 0x03
        registro = datagrama[2]
        if registro & 0x80:                       # escritura: sin acuse
            valor = struct.unpack(">I", datagrama[3:7])[0]
            self.escrituras.append((direccion, registro & 0x7F, valor))
            if direccion in self.regs:
                self.regs[direccion][registro & 0x7F] = valor
        elif direccion in self.regs:              # lectura: contesta el esclavo
            valor = self.regs[direccion].get(registro, 0)
            rep = bytes([0x05, 0xFF, registro]) + struct.pack(">I", valor)
            salida += rep + bytes([self.crc8(rep)])
        return salida


class GPIOFake:
    """RPi.GPIO emulado que cuenta flancos de subida por pin: asi se
    verifica que un movimiento de N micropasos emite exactamente N
    pulsos de STEP, y en que pin."""
    BCM = "BCM"; OUT = "OUT"; IN = "IN"; LOW = 0; HIGH = 1

    def __init__(self):
        self.estado = {}
        self.pulsos = {}

    def setmode(self, modo): pass
    def setup(self, pin, modo, initial=0): self.estado[pin] = initial
    def output(self, pin, valor):
        if self.estado.get(pin) == 0 and valor == 1:
            self.pulsos[pin] = self.pulsos.get(pin, 0) + 1
        self.estado[pin] = valor
    def input(self, pin): return self.estado.get(pin, 1)
    def cleanup(self, pines=None): pass
    def reset_pulsos(self): self.pulsos.clear()


class SerialFake:
    """serial.Serial emulado, conectado a una MatrizESP32Fake."""
    _matrices = {}

    # Puertos que corresponden al bus de los drivers de enfoque en vez
    # de a una matriz de iluminacion.
    _tmc = {}
    _tmc_direcciones = (0, 1)

    def __init__(self, *a, **kw):
        # pyserial acepta (port, baudrate, ...) posicionalmente, que es
        # como lo llama TMC2209Bus; IlluminationController usa keywords.
        self.port = a[0] if a else kw.get("port")
        self.baudrate = (a[1] if len(a) > 1 else kw.get("baudrate", 9600))
        self.timeout = kw.get("timeout")
        self.write_timeout = None
        self.dsrdtr = kw.get("dsrdtr", True)
        self.rtscts = False
        self._dtr = True          # pyserial afirma DTR por defecto
        self._rts = True
        self._buf = b""
        self._abierto_con = None
        self.es_motor = bool(self.port) and "ttyAMA" in str(self.port)
        # Con puerto, pyserial abre en el constructor; el codigo de las
        # matrices en cambio construye sin puerto y llama open().
        self.is_open = bool(self.port) and self.es_motor

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

    @property
    def tmc(self):
        return SerialFake._tmc.setdefault(
            self.port, TMC2209Fake(SerialFake._tmc_direcciones))

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
        if self.es_motor:
            self._buf += self.tmc.procesar(data)
            return len(data)
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
        self.n_capturas = 0
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
    def capture_array(self, nombre):
        # Contador: el autofoco descarta el primer frame tras cambiar la
        # iluminacion (puede haberse expuesto antes del cambio), y eso
        # tiene que ser verificable.
        self.n_capturas += 1
        return np.zeros((480, 640, 3), dtype=np.uint8)
    def capture_request(self):
        time.sleep(0.15)   # simula el coste real de una captura
        return RequestFake(self)


def instalar_gpio():
    """Inyecta RPi.GPIO falso. Se llama por separado de instalar()
    porque los tests de camara/matriz no lo necesitan."""
    gpio = GPIOFake()
    modulo = types.ModuleType("RPi.GPIO")
    for nombre in ("BCM", "OUT", "IN", "LOW", "HIGH"):
        setattr(modulo, nombre, getattr(GPIOFake, nombre))
    for nombre in ("setmode", "setup", "output", "input", "cleanup"):
        setattr(modulo, nombre, getattr(gpio, nombre))
    modulo.fake = gpio
    paquete = types.ModuleType("RPi"); paquete.GPIO = modulo
    sys.modules["RPi"] = paquete
    sys.modules["RPi.GPIO"] = modulo
    return gpio


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
