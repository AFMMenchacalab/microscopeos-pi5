import serial
import time


class IlluminationController:

    def __init__(self, port="/dev/matriz_cam0", baudrate=115200):
        self.port = port
        self.state = False
        self.brightness_percent = 100
        self.ser = serial.Serial(port, baudrate, timeout=2)
        time.sleep(1)  # dar tiempo a que la placa este lista
        self.off()

    def _enviar(self, comando):
        self.ser.write((comando + "\r\n").encode())
        time.sleep(0.05)
        return self.ser.read(100).decode(errors="ignore").strip()

    # --- Blanco / general ---
    def on(self):
        self._enviar("ON")
        self.state = True

    def off(self):
        self._enviar("OFF")
        self.state = False

    def set_brightness(self, percent):
        """Ajusta el brillo en porcentaje (0-100)."""
        percent = max(0, min(100, percent))
        value_255 = round(percent * 255 / 100)
        self._enviar(f"BRIGHT {value_255}")
        self.brightness_percent = percent

    # --- Patrones DPC ---
    def left(self):
        self._enviar("LEFT")
        self.state = True

    def right(self):
        self._enviar("RIGHT")
        self.state = True

    def top(self):
        self._enviar("TOP")
        self.state = True

    def bottom(self):
        self._enviar("BOTTOM")
        self.state = True

    # --- Utilidades ---
    def pulse(self, duration=0.3):
        self.on()
        time.sleep(duration)
        self.off()

    def is_on(self):
        return self.state

    def close(self):
        if self.ser is not None:
            self.off()
            self.ser.close()
