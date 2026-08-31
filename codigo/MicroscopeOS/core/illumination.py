r"""Control de las matrices de iluminacion DPC.

Hardware: Waveshare ESP32-S3-Matrix (8x8 WS2812B), una por camara,
identificadas por udev como /dev/matriz_cam0 y /dev/matriz_cam1.
Sustituye a las placas RP2040 (5x5) del montaje de Pi 4.

Protocolo de linea (ver extras/matrices_esp32s3_matrix/README.md):

    Envio     PATRON:brillo\n     ej. LEFT:128, FULL:255, OFF:0
    Respuesta OK:PATRON:brillo\n
    Extra     ID\n -> ID:MATRIZ:<mac>:ROT90:FX0:FY1

    ERR:UNKNOWN_PATTERN:<token>   ERR:BAD_BRIGHTNESS:<token>
    ERR:BAD_FORMAT:<linea>        ERR:BAD_FORMAT:LINE_TOO_LONG

El brillo va DENTRO de cada comando (antes era el comando aparte
"BRIGHT n" del firmware RP2040), asi que esta clase lo mantiene como
estado y lo reenvia en cada patron.

La API publica sigue siendo la misma que la version RP2040
(on/off/left/right/top/bottom/set_brightness/pulse/is_on/close) para no
tocar server/api.py, core/timelapse.py ni la interfaz web.
"""

import serial
import time

# Patrones que acepta el firmware. "FULL" reemplaza al "ON"/"ALL" del RP2040.
PATRONES = ("FULL", "LEFT", "RIGHT", "TOP", "BOTTOM", "OFF")

# El firmware descarta lineas de mas de 47 caracteres (ERR:BAD_FORMAT:LINE_TOO_LONG).
MAX_LINEA = 47


class IlluminationError(RuntimeError):
    """La matriz respondio ERR:... o no respondio."""


class IlluminationController:

    def __init__(self, port="/dev/matriz_cam0", baudrate=9600, max_value=255,
                 strict=False):
        """
        max_value: tope duro del brillo enviado a la placa (0-255).
          255 = sin tope, que es el comportamiento por defecto del firmware
          (decision explicita, ver README de la matriz). Con alimentacion
          USB el README recomienda no pasar de ~46 en FULL y ~92 en medios
          patrones: FULL:255 son ~3.8 A y un puerto USB da 0.5-0.9 A.
          # TODO-HW: medir el consumo real del montaje y decidir si bajar
          # este tope. Ver TODO_HW.md (prioridad 1).
        strict: si True, una respuesta ERR:* lanza IlluminationError en vez
          de solo registrarse en self.last_error.
        """
        self.port = port
        self.state = False
        self.brightness_percent = 100
        self.max_value = max(0, min(255, max_value))
        self.strict = strict
        self.current_pattern = "OFF"
        self.last_error = None

        # El baudrate es virtual (USB-CDC nativo lo ignora), pero pyserial
        # exige uno. Se deja en 9600 por coherencia con el README.
        #
        # dtr/rts se ponen en False ANTES de abrir: en el USB-Serial/JTAG
        # del ESP32-S3 la combinacion DTR+RTS es la secuencia de reset, y
        # pyserial afirma DTR al abrir por defecto. Sin esto, cada conexion
        # reinicia la placa.
        self.ser = serial.Serial()
        self.ser.port = port
        self.ser.baudrate = baudrate
        self.ser.timeout = 2
        self.ser.write_timeout = 2
        self.ser.dsrdtr = False
        self.ser.rtscts = False
        self.ser.dtr = False
        self.ser.rts = False
        self.ser.open()

        # El ROM del ESP32-S3 escribe mensajes de arranque por el mismo CDC.
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()
        # TODO-HW: 0.3 s es lo que recomienda el README de la matriz. Si al
        # arrancar el servicio la primera matriz responde ERR o timeout,
        # subirlo (el RP2040 usaba 1 s).
        time.sleep(0.3)

        self.off()

    # =============================
    # TRANSPORTE
    # =============================
    def _valor(self):
        """Brillo actual en la escala 0-255 del firmware, con tope aplicado."""
        return min(round(self.brightness_percent * 255 / 100), self.max_value)

    def _enviar(self, patron, valor=None):
        """Envia 'PATRON:brillo' y devuelve la respuesta cruda de la placa."""
        if valor is None:
            valor = self._valor()

        linea = f"{patron}:{valor}"
        if len(linea) + 1 > MAX_LINEA:
            raise IlluminationError(f"comando demasiado largo: {linea!r}")

        self.ser.write((linea + "\n").encode())
        respuesta = self.ser.readline().decode(errors="ignore").strip()

        esperada = f"OK:{patron}:{valor}"
        if respuesta != esperada:
            self.last_error = respuesta or "sin respuesta (timeout)"
            if self.strict:
                raise IlluminationError(
                    f"{self.port}: envie {linea!r}, esperaba {esperada!r}, "
                    f"recibi {respuesta!r}")
        else:
            self.last_error = None

        return respuesta

    def _patron(self, nombre):
        self._enviar(nombre)
        self.current_pattern = nombre
        self.state = (nombre != "OFF")

    # =============================
    # BLANCO / GENERAL
    # =============================
    def on(self):
        """Enciende la matriz completa. 'FULL' en el firmware ESP32-S3."""
        self._patron("FULL")

    def off(self):
        # El firmware acepta cualquier brillo en OFF; se manda 0 por claridad.
        self._enviar("OFF", 0)
        self.current_pattern = "OFF"
        self.state = False

    def set_brightness(self, percent):
        """Ajusta el brillo en porcentaje (0-100).

        A diferencia del RP2040, el brillo no es un comando propio del
        firmware: viaja dentro de cada patron. Si la matriz esta encendida
        se reenvia el patron actual para que el cambio se vea al momento,
        que es como se comportaba la version anterior.
        """
        self.brightness_percent = max(0, min(100, percent))
        if self.state and self.current_pattern != "OFF":
            self._enviar(self.current_pattern)

    # =============================
    # PATRONES DPC
    # =============================
    def left(self):
        self._patron("LEFT")

    def right(self):
        self._patron("RIGHT")

    def top(self):
        self._patron("TOP")

    def bottom(self):
        self._patron("BOTTOM")

    # =============================
    # UTILIDADES
    # =============================
    def id(self):
        """Devuelve la identificacion y calibracion del firmware cargado.

        Formato: ID:MATRIZ:A0:F2:62:EB:21:A4:ROT90:FX0:FY1
        Sirve para confirmar que cada placa lleva su calibracion correcta
        sin abrir el codigo.
        """
        self.ser.write(b"ID\n")
        return self.ser.readline().decode(errors="ignore").strip()

    def pulse(self, duration=0.3):
        self.on()
        time.sleep(duration)
        self.off()

    def is_on(self):
        return self.state

    def close(self):
        if self.ser is not None:
            try:
                self.off()
            finally:
                self.ser.close()
