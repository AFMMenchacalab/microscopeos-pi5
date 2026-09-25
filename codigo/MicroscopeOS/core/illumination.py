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

import re
import serial
import time

_HEX = re.compile(r"^[0-9A-Fa-f]{6}$")


def canales_encendidos(color):
    """Cuantos de los 3 LEDs (R, G, B) de cada pixel se encienden con ese
    color. None = blanco (los 3)."""
    if not color:
        return 3
    return max(1, sum(int(color[i:i + 2], 16) > 0 for i in (0, 2, 4)))

# Patrones que acepta el firmware. "FULL" reemplaza al "ON"/"ALL" del RP2040.
# RING (campo oscuro, agregado en el firmware reescrito 2026-08-31) es un
# patron simple mas, mismo protocolo PATRON:brillo. RHEINBERG no entra aqui:
# es un comando aparte de 4 campos (dos colores a la vez), ver metodo
# rheinberg() mas abajo.
PATRONES = ("FULL", "LEFT", "RIGHT", "TOP", "BOTTOM", "OFF", "RING")

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
        # Colores del ultimo rheinberg(), para que set_brightness() pueda
        # reenviarlo con los mismos dos colores si esta activo.
        self._rheinberg_colors = ("0000FF", "FF6A00")
        self.current_pattern = "OFF"
        self.current_color = None
        # Color de los patrones DPC (LEFT/RIGHT/TOP/BOTTOM) en RRGGBB, o None
        # = blanco. Verde es lo recomendado: la fase depende de la longitud
        # de onda y la luz "blanca" de la WS2812B son tres LEDs (R, G, B) con
        # tres contrastes distintos sumados; el objetivo acromatico esta
        # mejor corregido en verde y el sensor tiene mas pixeles verdes.
        self.color_dpc = None
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

        # El ROM del ESP32-S3 escribe mensajes de arranque por el mismo CDC,
        # y en las placas probadas tarda bastante mas que los 0.3 s que
        # recomienda el README en terminar de bootear (el RP2040 usaba 1 s).
        # Si se flushea antes de esperar, el log de arranque sigue llegando
        # despues del flush y contamina la primera respuesta (readline()
        # devuelve una linea del log tipo "SPIWP:0xee" en vez de "OK:...").
        # Por eso el flush va DESPUES de esperar, no antes.
        time.sleep(2.0)
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()

        # Si la placa no responde, con strict=False todo lo demas seguiria
        # funcionando en silencio y la matriz nunca encenderia. Avisar aqui
        # es la unica oportunidad de detectarlo antes de un timelapse largo.
        self.off()
        if self.last_error is not None:
            print(f"AVISO: {port} no respondio al comando inicial "
                  f"({self.last_error}). La matriz puede no estar "
                  f"encendiendo. Comprueba con IlluminationController.id()")

    # =============================
    # TRANSPORTE
    # =============================
    def _valor(self, color=None):
        """Brillo actual en la escala 0-255 del firmware, con tope aplicado.

        El tope max_value protege la corriente del USB con los 3 LEDs de
        cada pixel encendidos (blanco). Con un solo color se enciende 1/3 de
        cada pixel, asi que el mismo consumo admite un brillo 3 veces mayor:
        el tope se escala por los canales encendidos. Asi una imagen en verde
        sale con una exposicion parecida a la de blanco, sin pasar del
        consumo que ya se consideraba seguro."""
        tope = min(255, round(self.max_value * 3 / canales_encendidos(color)))
        return min(round(self.brightness_percent * 255 / 100), tope)

    def _enviar(self, patron, valor=None, color=None):
        """Envia 'PATRON:brillo[:RRGGBB]' y devuelve la respuesta cruda."""
        if valor is None:
            valor = self._valor(color)

        linea = f"{patron}:{valor}" + (f":{color.upper()}" if color else "")
        if len(linea) + 1 > MAX_LINEA:
            raise IlluminationError(f"comando demasiado largo: {linea!r}")

        self.ser.write((linea + "\n").encode())
        respuesta = self.ser.readline().decode(errors="ignore").strip()

        esperada = f"OK:{linea}"
        if respuesta != esperada:
            self.last_error = respuesta or "sin respuesta (timeout)"
            if self.strict:
                raise IlluminationError(
                    f"{self.port}: envie {linea!r}, esperaba {esperada!r}, "
                    f"recibi {respuesta!r}")
        else:
            self.last_error = None

        return respuesta

    def _patron(self, nombre, color=None):
        self._enviar(nombre, color=color)
        self.current_pattern = nombre
        self.current_color = color
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
        if self.state and self.current_pattern == "RHEINBERG":
            self.rheinberg(*self._rheinberg_colors)
        elif self.state and self.current_pattern != "OFF":
            self._enviar(self.current_pattern, color=self.current_color)

    # =============================
    # PATRONES DPC
    # =============================
    def set_color_dpc(self, color):
        """Color de los patrones DPC: RRGGBB (ej. "00FF00") o None/"FFFFFF"
        para blanco. Se usa en el vivo, las capturas, el timelapse y el
        autofoco (todos llaman a left/right/top/bottom)."""
        if color and color.upper() != "FFFFFF":
            if not _HEX.match(color):
                raise ValueError(f"color invalido: {color!r} (usar RRGGBB)")
            self.color_dpc = color.upper()
        else:
            self.color_dpc = None
        if self.state and self.current_pattern in ("LEFT", "RIGHT", "TOP", "BOTTOM"):
            self._patron(self.current_pattern, self.color_dpc)

    def left(self):
        self._patron("LEFT", self.color_dpc)

    def right(self):
        self._patron("RIGHT", self.color_dpc)

    def top(self):
        self._patron("TOP", self.color_dpc)

    def bottom(self):
        self._patron("BOTTOM", self.color_dpc)

    # =============================
    # CAMPO OSCURO Y RHEINBERG
    # (firmware reescrito 2026-08-31, ver README de la matriz)
    # =============================
    def ring(self):
        """Campo oscuro: solo el borde exterior de la matriz (8x8), con el
        centro apagado. Aproxima iluminacion oblicua fuera del cono de
        apertura numerica del objetivo. Sin luz directa entrando al lente,
        solo se ve lo que la muestra dispersa: fondo oscuro, objeto claro."""
        self._patron("RING")

    def rheinberg(self, color_centro="0000FF", color_anillo="FF6A00"):
        """Contraste de color falso: enciende el bloque central (2x2) y el
        anillo exterior a la vez, cada uno con su propio color. El fondo y
        el objeto salen en colores distintos aunque la muestra sea
        transparente y sin tenir. Colores en RRGGBB hex, por defecto azul
        al centro / naranja al anillo (complementarios).

        A diferencia de los demas patrones (un solo comando PATRON:brillo),
        este manda RHEINBERG:brillo:color_centro:color_anillo -- no pasa
        por _enviar()/_patron() porque esos asumen 2 campos.
        """
        self._rheinberg_colors = (color_centro, color_anillo)
        valor = self._valor()
        linea = f"RHEINBERG:{valor}:{color_centro}:{color_anillo}"
        if len(linea) + 1 > MAX_LINEA:
            raise IlluminationError(f"comando demasiado largo: {linea!r}")

        self.ser.write((linea + "\n").encode())
        respuesta = self.ser.readline().decode(errors="ignore").strip()

        esperada = f"OK:{linea}"
        if respuesta != esperada:
            self.last_error = respuesta or "sin respuesta (timeout)"
            if self.strict:
                raise IlluminationError(
                    f"{self.port}: envie {linea!r}, esperaba {esperada!r}, "
                    f"recibi {respuesta!r}")
        else:
            self.last_error = None

        self.current_pattern = "RHEINBERG"
        self.state = True
        return respuesta

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
