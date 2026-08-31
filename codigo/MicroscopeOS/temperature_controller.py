import asyncio
import json
import logging
import serial
import serial.tools.list_ports

logger = logging.getLogger("temperature_controller")

class TemperatureController:
    def __init__(self, port: str = None, baudrate: int = 9600):
        self.port = port
        self.baudrate = baudrate
        self._serial = None
        self._running = False
        self.temperature = None
        self.setpoint = 37.0
        self.pwm = None
        self.error_msg = None

        # --- CO2 / válvula solenoide (SCD30) ---
        self.co2 = None
        self.co2_setpoint = 40000.0   # 4% CO2, target biológico
        self.co2_duty = None          # % de apertura de la válvula en la ventana PID
        self.valve_open = None        # estado instantáneo de la válvula (bool)
        self.ambient_temp = None      # temperatura reportada por el propio SCD30
        self.humidity = None

    def _find_arduino_port(self):
        for port in serial.tools.list_ports.comports():
            desc = (port.description or "").lower()
            mfg  = (port.manufacturer or "").lower()
            if "arduino" in desc or "arduino" in mfg or "ch340" in desc or "cp210" in desc:
                return port.device
        return None

    def connect(self):
        port = self.port or self._find_arduino_port()
        if port is None:
            self.error_msg = "Arduino no encontrado"
            return False
        try:
            self._serial = serial.Serial(port, self.baudrate, timeout=2)
            self.error_msg = None
            logger.info(f"Arduino conectado en {port}")
            return True
        except serial.SerialException as e:
            self.error_msg = str(e)
            return False

    def disconnect(self):
        self._running = False
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._serial = None

    async def run(self):
        if not self._serial or not self._serial.is_open:
            if not self.connect():
                return
        self._running = True
        loop = asyncio.get_event_loop()
        while self._running:
            try:
                raw = await loop.run_in_executor(None, self._readline_safe)
                if raw is None:
                    await asyncio.sleep(0.1)
                    continue
                line = raw.strip()
                if line.startswith("{"):
                    data = json.loads(line)
                    self.temperature = data.get("temp")
                    self.setpoint    = data.get("setpoint", self.setpoint)
                    self.pwm         = data.get("pwm")
                    self.error_msg   = None

                    # Campos de CO2 — solo presentes si el SCD30 está
                    # conectado y respondiendo del lado del Arduino.
                    # Se usan valores previos si el campo no viene en
                    # este JSON (p. ej. Arduino corriendo sin CO2).
                    if "co2" in data:
                        self.co2           = data.get("co2")
                        self.co2_setpoint  = data.get("co2_set", self.co2_setpoint)
                        self.co2_duty      = data.get("co2_duty")
                        self.valve_open    = bool(data.get("valve"))
                        self.ambient_temp  = data.get("ambient_temp")
                        self.humidity      = data.get("humidity")
                # Avisos del Arduino que no son JSON (p. ej. sensor CO2 ausente)
                elif line.startswith("WARN:"):
                    logger.warning(f"Arduino: {line}")
                elif line in ("VALVE_OPEN", "VALVE_CLOSED"):
                    self.valve_open = (line == "VALVE_OPEN")
            except json.JSONDecodeError:
                pass
            except serial.SerialException as e:
                self.error_msg = f"Serial perdido: {e}"
                self._running = False
            except Exception as e:
                logger.warning(f"Error: {e}")

    def _readline_safe(self):
        try:
            if self._serial and self._serial.in_waiting:
                return self._serial.readline().decode("utf-8", errors="replace")
            return None
        except Exception:
            return None

    def set_target_temperature(self, value: float):
        if not (20.0 <= value <= 80.0):
            return False
        if self._serial is None or not self._serial.is_open:
            self.error_msg = "Arduino no conectado"
            return False
        try:
            self._serial.write(f"SET:{value:.1f}\n".encode())
            self.setpoint = value
            return True
        except serial.SerialException as e:
            self.error_msg = str(e)
            return False

    def set_target_co2(self, value: float):
        if not (400 <= value <= 100000):
            return False
        if self._serial is None or not self._serial.is_open:
            self.error_msg = "Arduino no conectado"
            return False
        try:
            self._serial.write(f"SET_CO2:{value:.0f}\n".encode())
            self.co2_setpoint = value
            return True
        except serial.SerialException as e:
            self.error_msg = str(e)
            return False

    def status(self):
        return {
            "connected": self._serial is not None and self._serial.is_open,
            "temperature": self.temperature,
            "setpoint": self.setpoint,
            "pwm": self.pwm,
            "error": self.error_msg,
            # CO2 — quedan en None si el SCD30 no está presente,
            # así el frontend puede ocultar esa sección sin errores.
            "co2": self.co2,
            "co2_setpoint": self.co2_setpoint,
            "co2_duty": self.co2_duty,
            "valve_open": self.valve_open,
            "ambient_temp": self.ambient_temp,
            "humidity": self.humidity,
        }

temperature_controller = TemperatureController()
