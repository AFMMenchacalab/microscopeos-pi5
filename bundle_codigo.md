# bundle_codigo.md — MicroscopeOS (export Pi4 -> Pi5)

Concatenacion de todo el codigo fuente y configs relevantes para pegar
en una conversacion de Claude. Rutas originales en la Raspberry Pi 4B
indicadas en cada encabezado. Generado el 2026-08-30.

No incluye: venv/, timelapses, capturas TIFF/PNG, MicroscopeOS.zip,
cache de Arduino IDE, ni las librerias de terceros en
codigo/extras/arduino_sketches/libraries/ (ver docs/estructura.txt).

---

## `codigo/MicroscopeOS/config.json`

Ruta original en la Pi4: `/home/microscope1/MicroscopeOS/config.json`

```json
{
    "camera": {
        "exposure_us": 12000,
        "gain": 1.2,
        "resolution_width": 3280,
        "resolution_height": 2464
    },
    "timelapse": {
        "interval_seconds": 50,
        "duration_seconds": 3600,
        "stabilization_time": 0.3
    },
    "illumination": {
        "gpio_pin": 17
    }
}
```

---

## `codigo/MicroscopeOS/core/camera.py`

Ruta original en la Pi4: `/home/microscope1/MicroscopeOS/core/camera.py`

```python
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

```

---

## `codigo/MicroscopeOS/core/config.py`

Ruta original en la Pi4: `/home/microscope1/MicroscopeOS/core/config.py`

```python
import json
from dataclasses import dataclass, asdict


@dataclass
class CameraSettings:
    exposure_us: int = 12000
    gain: float = 1.2
    resolution_width: int = 3280
    resolution_height: int = 2464


@dataclass
class TimelapseSettings:
    interval_seconds: int = 60
    duration_seconds: int = 3600
    stabilization_time: float = 0.3
    led_on_time: float = 0.3 

    


@dataclass
class IlluminationSettings:
    gpio_pin: int = 17


class SystemConfig:

    def __init__(self):
        self.camera = CameraSettings()
        self.timelapse = TimelapseSettings()
        self.illumination = IlluminationSettings()

    def save(self, filename="config.json"):
        data = {
            "camera": asdict(self.camera),
            "timelapse": asdict(self.timelapse),
            "illumination": asdict(self.illumination),
        }

        with open(filename, "w") as f:
            json.dump(data, f, indent=4)

        print(f"Configuración guardada en {filename}")

    def load(self, filename="config.json"):
        with open(filename, "r") as f:
            data = json.load(f)

        self.camera = CameraSettings(**data["camera"])
        self.timelapse = TimelapseSettings(**data["timelapse"])
        self.illumination = IlluminationSettings(**data["illumination"])

        print(f"Configuración cargada desde {filename}")

```

---

## `codigo/MicroscopeOS/core/illumination.py`

Ruta original en la Pi4: `/home/microscope1/MicroscopeOS/core/illumination.py`

```python
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

```

---

## `codigo/MicroscopeOS/core/preview.py`

Ruta original en la Pi4: `/home/microscope1/MicroscopeOS/core/preview.py`

```python
import threading
import cv2
import time


class PreviewManager:

    def __init__(self, camera):
        self.camera = camera
        self.running = False
        self.thread = None

    def _run(self):

        print("Preview iniciado.")
        cv2.namedWindow("Microscope Preview", cv2.WINDOW_NORMAL)

        while self.running:

            frame = self.camera.get_frame()

            cv2.imshow("Microscope Preview", frame)

            # Espera 1 ms y permite detectar tecla
            if cv2.waitKey(1) & 0xFF == ord('q'):
                self.stop()
                break

        cv2.destroyAllWindows()
        print("Preview detenido.")

    def start(self):
        if self.running:
            return

        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread is not None:
            self.thread.join()

```

---

## `codigo/MicroscopeOS/core/profile_manager.py`

Ruta original en la Pi4: `/home/microscope1/MicroscopeOS/core/profile_manager.py`

```python
import os
from core.config import SystemConfig


class ProfileManager:

    def __init__(self, profiles_dir="profiles"):
        self.profiles_dir = profiles_dir
        os.makedirs(self.profiles_dir, exist_ok=True)

    def list_profiles(self):
        return [
            f.replace(".json", "")
            for f in os.listdir(self.profiles_dir)
            if f.endswith(".json")
        ]

    def load_profile(self, profile_name):
        config = SystemConfig()
        filepath = os.path.join(self.profiles_dir, f"{profile_name}.json")
        config.load(filepath)
        return config

    def save_profile(self, profile_name, config):
        filepath = os.path.join(self.profiles_dir, f"{profile_name}.json")
        config.save(filepath)
        print(f"Perfil '{profile_name}' guardado.")

    def delete_profile(self, profile_name):
        filepath = os.path.join(self.profiles_dir, f"{profile_name}.json")
        if os.path.exists(filepath):
            os.remove(filepath)
            print(f"Perfil '{profile_name}' eliminado.")

```

---

## `codigo/MicroscopeOS/core/timelapse.py`

Ruta original en la Pi4: `/home/microscope1/MicroscopeOS/core/timelapse.py`

```python
import time
import os
import threading
from datetime import datetime
from enum import Enum


class TimelapseState(Enum):
    STOPPED = 0
    RUNNING = 1
    ERROR = 2


MODOS = {
    "blanco": [("", "on")],
    "dpc":    [("_L", "left"), ("_R", "right"),
               ("_T", "top"), ("_B", "bottom")],
}


class TimelapseManager:

    def __init__(self, camera, illuminations):
        self.camera = camera
        self.illuminations = illuminations
        self.state = TimelapseState.STOPPED
        self.thread = None
        self.base_folder = None
        self.ciclo_actual = 0

    def _log(self, mensaje):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        linea = f"[{ts}] {mensaje}"
        print(linea)
        if self.base_folder:
            try:
                with open(os.path.join(self.base_folder, "timelapse.log"), "a") as f:
                    f.write(linea + "\n")
            except Exception:
                pass

    def _log_temp(self, ciclo, timestamp):
        """Lee temperatura del controlador y la loguea en temp.csv"""
        try:
            from temperature_controller import temperature_controller
            temp = temperature_controller.temperature
            sp   = temperature_controller.setpoint
            pwm  = temperature_controller.pwm
            if temp is None:
                return
            linea = f"{timestamp},{ciclo},{temp:.2f},{sp:.1f},{pwm}\n"
            with open(os.path.join(self.base_folder, "temperatura.csv"), "a") as f:
                # Escribir cabecera si el archivo es nuevo
                if os.path.getsize(os.path.join(self.base_folder, "temperatura.csv")) == 0:
                    f.write("timestamp,ciclo,temperatura,setpoint,pwm\n")
                f.write(linea)
            self._log(f"  temp: {temp:.2f}°C (sp={sp:.1f}, pwm={pwm})")
        except Exception as e:
            self._log(f"  temp: no disponible ({e})")

    def _graficar_temperatura(self):
        """Genera temperatura.png al finalizar el timelapse."""
        try:
            import csv
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            csv_path = os.path.join(self.base_folder, "temperatura.csv")
            if not os.path.exists(csv_path):
                return

            ciclos, temps, setpoints = [], [], []
            with open(csv_path) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    ciclos.append(int(row["ciclo"]))
                    temps.append(float(row["temperatura"]))
                    setpoints.append(float(row["setpoint"]))

            if not ciclos:
                return

            fig, ax = plt.subplots(figsize=(10, 4))
            fig.patch.set_facecolor("#0a0e0d")
            ax.set_facecolor("#111816")

            ax.plot(ciclos, temps, color="#3ddc84", linewidth=2, label="Temperatura")
            ax.plot(ciclos, setpoints, color="#ffb454", linewidth=1.5,
                    linestyle="--", label="Setpoint")

            ax.set_xlabel("Ciclo", color="#5f7269")
            ax.set_ylabel("°C", color="#5f7269")
            ax.set_title("Temperatura durante timelapse", color="#c8d6d0")
            ax.tick_params(colors="#5f7269")
            ax.legend(facecolor="#111816", labelcolor="#c8d6d0")
            for spine in ax.spines.values():
                spine.set_edgecolor("#1f2e2a")

            plt.tight_layout()
            out = os.path.join(self.base_folder, "temperatura.png")
            plt.savefig(out, dpi=120, facecolor=fig.get_facecolor())
            plt.close()
            self._log(f"Gráfica guardada: {out}")

        except Exception as e:
            self._log(f"Error generando gráfica: {e}")

    def _run(self, modo, interval_seconds, duration_seconds,
             stabilization_time, camaras):

        self.state = TimelapseState.RUNNING
        patrones = MODOS[modo]

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.base_folder = f"timelapse_{stamp}"
        os.makedirs(self.base_folder, exist_ok=True)
        for cam in camaras:
            os.makedirs(os.path.join(self.base_folder, f"cam{cam}"), exist_ok=True)

        # Crear CSV con cabecera
        csv_path = os.path.join(self.base_folder, "temperatura.csv")
        with open(csv_path, "w") as f:
            f.write("timestamp,ciclo,temperatura,setpoint,pwm\n")

        self._log(f"Timelapse iniciado | modo={modo} | camaras={camaras} | "
                  f"intervalo={interval_seconds}s | duracion={duration_seconds}s")

        start_time = time.monotonic()
        next_capture_time = start_time
        ciclo = 0

        while self.state == TimelapseState.RUNNING:
            current_time = time.monotonic()

            if current_time - start_time >= duration_seconds:
                break

            if current_time >= next_capture_time:
                ciclo += 1
                self.ciclo_actual = ciclo
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                self._log(f"--- Ciclo {ciclo} ({ts}) ---")

                # Registrar temperatura al inicio de cada ciclo
                self._log_temp(ciclo, ts)

                for cam in camaras:
                    cam_folder = os.path.join(self.base_folder, f"cam{cam}")
                    luz = self.illuminations.get(cam)

                    for sufijo, metodo_luz in patrones:
                        try:
                            if luz is not None:
                                getattr(luz, metodo_luz)()
                                time.sleep(stabilization_time)

                            filename = os.path.join(
                                cam_folder, f"img_{ts}{sufijo}.tif")
                            self.camera.capture_image(
                                camera_num=cam,
                                folder=cam_folder,
                                filename=filename)

                            self._log(f"  cam{cam}{sufijo}: OK")

                        except Exception as e:
                            self._log(f"  cam{cam}{sufijo}: ERROR -> {e}")
                        finally:
                            if luz is not None:
                                try:
                                    luz.off()
                                except Exception:
                                    pass

                next_capture_time += interval_seconds

            else:
                time.sleep(0.05)

        for luz in self.illuminations.values():
            try:
                luz.off()
            except Exception:
                pass

        self._log(f"Timelapse finalizado. Ciclos completados: {ciclo}")
        self._graficar_temperatura()
        self.state = TimelapseState.STOPPED

    def start(self, modo="blanco", interval_seconds=300, duration_seconds=3600,
              stabilization_time=0.3, camaras=[0, 1]):
        if self.state == TimelapseState.RUNNING:
            print("Timelapse ya esta corriendo.")
            return
        if modo not in MODOS:
            print(f"Modo invalido: {modo}. Usa 'blanco' o 'dpc'.")
            return

        self.thread = threading.Thread(
            target=self._run,
            args=(modo, interval_seconds, duration_seconds,
                  stabilization_time, camaras),
            daemon=True
        )
        self.thread.start()

    def stop(self):
        if self.state == TimelapseState.RUNNING:
            self.state = TimelapseState.STOPPED
            if self.thread is not None:
                self.thread.join()

    def is_running(self):
        return self.state == TimelapseState.RUNNING

```

---

## `codigo/MicroscopeOS/interfaces/desktop_gui.py`

Ruta original en la Pi4: `/home/microscope1/MicroscopeOS/interfaces/desktop_gui.py`

```python
from PyQt6.QtWidgets import (
    QMainWindow, QPushButton, QLabel,
    QVBoxLayout, QWidget, QHBoxLayout,
    QSpinBox, QDoubleSpinBox,
    QFrame, QInputDialog, QTextEdit,
    QCheckBox, QComboBox
)
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QImage, QPixmap, QFont
import cv2
import numpy as np
import time


class MicroscopeGUI(QMainWindow):

    def __init__(self, camera, illumination, timelapse_manager, config, profile_manager):
        super().__init__()

        self.camera = camera
        self.illumination = illumination
        self.timelapse = timelapse_manager
        self.config = config
        self.profile_manager = profile_manager

        self.led_state = False
        self.dark_mode = True
        self.last_time = time.time()
        self.frame_count = 0
        self.timelapse_active = False
        self.capture_counter = 0

        self.setWindowTitle("MicroscopeOS")
        self.resize(1700, 950)

        self.apply_dark_theme()
        self.init_ui()
        self.init_timer()

    # =============================
    # THEME
    # =============================
    def apply_dark_theme(self):
        self.setStyleSheet("""
            QWidget { background-color: #1e1e1e; color: #dddddd; }
            QPushButton { background-color: #3a3a3a; padding: 6px; border-radius: 6px; }
            QPushButton:hover { background-color: #505050; }
            QSpinBox, QDoubleSpinBox, QComboBox { background-color: #2b2b2b; border-radius: 6px; }
            QTextEdit { background-color: #111111; }
        """)
        self.dark_mode = True

    def toggle_theme(self):
        if self.dark_mode:
            self.setStyleSheet("")
            self.dark_mode = False
        else:
            self.apply_dark_theme()

    # =============================
    # UI
    # =============================
    def init_ui(self):

        root_layout = QVBoxLayout()

        # ===== TOP BAR =====
        top_layout = QHBoxLayout()

        self.status_label = QLabel("IDLE")
        self.status_label.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        self.status_label.setStyleSheet("color: lime;")

        self.fps_label = QLabel("FPS: 0")
        self.sat_label = QLabel("Saturation: 0%")
        self.counter_label = QLabel("Frames: 0")

        top_layout.addWidget(self.status_label)
        top_layout.addStretch()
        top_layout.addWidget(self.counter_label)
        top_layout.addWidget(self.fps_label)
        top_layout.addWidget(self.sat_label)

        root_layout.addLayout(top_layout)

        # ===== MAIN =====
        main_layout = QHBoxLayout()

        # ==== LEFT PANEL ====
        side_layout = QVBoxLayout()

        # Profile
        side_layout.addWidget(QLabel("Profile"))
        self.profile_combo = QComboBox()
        self.profile_combo.addItems(self.profile_manager.list_profiles())
        side_layout.addWidget(self.profile_combo)

        load_btn = QPushButton("Load Profile")
        load_btn.clicked.connect(self.load_profile)
        side_layout.addWidget(load_btn)

        save_btn = QPushButton("Save Profile As...")
        save_btn.clicked.connect(self.save_profile)
        side_layout.addWidget(save_btn)

        # Exposure
        side_layout.addWidget(QLabel("Exposure (µs)"))
        self.exposure_spin = QSpinBox()
        self.exposure_spin.setRange(100, 100000)
        self.exposure_spin.setValue(self.config.camera.exposure_us)
        self.exposure_spin.valueChanged.connect(self.update_camera)
        side_layout.addWidget(self.exposure_spin)

        # Gain
        side_layout.addWidget(QLabel("Gain"))
        self.gain_spin = QDoubleSpinBox()
        self.gain_spin.setRange(0.1, 10.0)
        self.gain_spin.setValue(self.config.camera.gain)
        self.gain_spin.valueChanged.connect(self.update_camera)
        side_layout.addWidget(self.gain_spin)

        # Interval
        side_layout.addWidget(QLabel("Interval (s)"))
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 86400)
        self.interval_spin.setValue(self.config.timelapse.interval_seconds)
        side_layout.addWidget(self.interval_spin)

        # Duration
        side_layout.addWidget(QLabel("Duration (s)"))
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(1, 86400)
        self.duration_spin.setValue(self.config.timelapse.duration_seconds)
        side_layout.addWidget(self.duration_spin)

        # LED ON time
        side_layout.addWidget(QLabel("LED On Time (s)"))
        self.led_time_spin = QDoubleSpinBox()
        self.led_time_spin.setRange(0.01, 5.0)
        self.led_time_spin.setSingleStep(0.05)
        self.led_time_spin.setValue(self.config.timelapse.led_on_time)
        side_layout.addWidget(self.led_time_spin)

        # Histogram toggle
        self.hist_toggle = QCheckBox("Show Histogram (H)")
        self.hist_toggle.setChecked(True)
        side_layout.addWidget(self.hist_toggle)

        # LED
        self.led_btn = QPushButton("LED OFF (L)")
        self.led_btn.clicked.connect(self.toggle_led)
        side_layout.addWidget(self.led_btn)

        # Capture
        capture_btn = QPushButton("Capture (C)")
        capture_btn.clicked.connect(self.capture_image)
        side_layout.addWidget(capture_btn)

        # Timelapse
        self.start_btn = QPushButton("Start Timelapse (T)")
        self.start_btn.clicked.connect(self.start_timelapse)
        side_layout.addWidget(self.start_btn)

        self.stop_btn = QPushButton("Stop Timelapse (S)")
        self.stop_btn.clicked.connect(self.stop_timelapse)
        self.stop_btn.setEnabled(False)
        side_layout.addWidget(self.stop_btn)

        theme_btn = QPushButton("Toggle Dark/Light (D)")
        theme_btn.clicked.connect(self.toggle_theme)
        side_layout.addWidget(theme_btn)

        side_layout.addStretch()
        main_layout.addLayout(side_layout, 1)

        # ==== PREVIEW ====
        preview_layout = QVBoxLayout()

        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_layout.addWidget(self.preview_label, 5)

        self.hist_label = QLabel()
        self.hist_label.setFixedHeight(120)
        preview_layout.addWidget(self.hist_label, 1)

        main_layout.addLayout(preview_layout, 4)

        root_layout.addLayout(main_layout)

        # ==== LOG ====
        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setFixedHeight(110)
        root_layout.addWidget(self.log_console)

        container = QWidget()
        container.setLayout(root_layout)
        self.setCentralWidget(container)

        self.log("System Ready.")

    # =============================
    # KEYBOARD SHORTCUTS
    # =============================
    def keyPressEvent(self, event):
        key = event.key()

        if key == Qt.Key.Key_C:
            self.capture_image()
        elif key == Qt.Key.Key_L:
            self.toggle_led()
        elif key == Qt.Key.Key_T:
            self.start_timelapse()
        elif key == Qt.Key.Key_S:
            self.stop_timelapse()
        elif key == Qt.Key.Key_H:
            self.hist_toggle.toggle()
        elif key == Qt.Key.Key_D:
            self.toggle_theme()
        elif key == Qt.Key.Key_F:
            if self.isFullScreen():
                self.showNormal()
            else:
                self.showFullScreen()

    # =============================
    # LOG
    # =============================
    def log(self, message):
        timestamp = time.strftime("%H:%M:%S")
        self.log_console.append(f"[{timestamp}] {message}")

    # =============================
    # CAMERA UPDATE
    # =============================
    def update_camera(self):
        if not self.timelapse_active:
            self.config.camera.exposure_us = self.exposure_spin.value()
            self.config.camera.gain = self.gain_spin.value()
            self.camera.set_exposure(
                self.config.camera.exposure_us,
                self.config.camera.gain
            )

    # =============================
    # TIMER
    # =============================
    def init_timer(self):
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_frame)
        self.timer.start(30)

    def update_frame(self):
        frame = self.camera.get_frame()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # FPS
        self.frame_count += 1
        now = time.time()
        if now - self.last_time >= 1:
            self.fps_label.setText(f"FPS: {self.frame_count}")
            self.frame_count = 0
            self.last_time = now

        # Saturation
        percent = (np.sum(gray > 250) / gray.size) * 100
        self.sat_label.setText(f"Saturation: {percent:.2f}%")

        # Histogram
        if self.hist_toggle.isChecked():
            hist = cv2.calcHist([gray], [0], None, [256], [0, 256])
            hist = hist / hist.max()
            hist_img = np.zeros((120, 256), dtype=np.uint8)
            for x in range(256):
                cv2.line(hist_img, (x, 120),
                         (x, 120 - int(hist[x] * 120)), 255)
            hist_qimg = QImage(hist_img.data, 256, 120, 256,
                               QImage.Format.Format_Grayscale8)
            self.hist_label.setPixmap(QPixmap.fromImage(hist_qimg))
        else:
            self.hist_label.clear()

        # Preview
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qt_img = QImage(rgb.data, w, h, ch*w,
                        QImage.Format.Format_RGB888)
        scaled = QPixmap.fromImage(qt_img).scaled(
            self.preview_label.width(),
            self.preview_label.height(),
            Qt.AspectRatioMode.KeepAspectRatio
        )
        self.preview_label.setPixmap(scaled)

        # Timelapse finished detection
        if self.timelapse_active and not self.timelapse.is_running():
            self.timelapse_active = False
            self.status_label.setText("IDLE")
            self.status_label.setStyleSheet("color: lime;")
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.log("Timelapse completed.")

    # =============================
    # LED
    # =============================
    def toggle_led(self):
        self.led_state = not self.led_state
        if self.led_state:
            self.illumination.on()
            self.led_btn.setText("LED ON (L)")
            self.led_btn.setStyleSheet("background-color: green;")
            self.log("LED ON.")
        else:
            self.illumination.off()
            self.led_btn.setText("LED OFF (L)")
            self.led_btn.setStyleSheet("")
            self.log("LED OFF.")

    # =============================
    # CAPTURE
    # =============================
    def capture_image(self):
        filename = self.camera.capture_image()
        self.log(f"Captured: {filename}")

    # =============================
    # TIMELAPSE
    # =============================
    def start_timelapse(self):
        interval = self.interval_spin.value()
        duration = self.duration_spin.value()
        led_time = self.led_time_spin.value()

        self.config.timelapse.led_on_time = led_time

        self.timelapse.start(interval, duration, led_time)
        self.timelapse_active = True
        self.capture_counter = 0

        self.status_label.setText("RUNNING")
        self.status_label.setStyleSheet("color: red;")
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

        self.log(f"Timelapse started (interval={interval}s, duration={duration}s, LED={led_time}s).")

    def stop_timelapse(self):
        self.timelapse.stop()
        self.timelapse_active = False

        self.status_label.setText("IDLE")
        self.status_label.setStyleSheet("color: lime;")
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

        self.log("Timelapse stopped.")
    # =============================
    # PROFILE
    # =============================
    def load_profile(self):
        name = self.profile_combo.currentText()
        config = self.profile_manager.load_profile(name)
        self.config = config

        # Actualizar controles GUI
        self.exposure_spin.setValue(config.camera.exposure_us)
        self.gain_spin.setValue(config.camera.gain)
        self.interval_spin.setValue(config.timelapse.interval_seconds)
        self.duration_spin.setValue(config.timelapse.duration_seconds)

        # Si existe led_on_time en el perfil
        if hasattr(config.timelapse, "led_on_time"):
            self.led_time_spin.setValue(config.timelapse.led_on_time)

        self.update_camera()

        self.log(f"Profile '{name}' loaded.")
    def save_profile(self):
        name, ok = QInputDialog.getText(self, "Save Profile", "Profile name:")
        if ok and name:
            self.profile_manager.save_profile(name, self.config)
            self.profile_combo.clear()
            self.profile_combo.addItems(self.profile_manager.list_profiles())
            self.log(f"Profile '{name}' saved.")


```

---

## `codigo/MicroscopeOS/main.py`

Ruta original en la Pi4: `/home/microscope1/MicroscopeOS/main.py`

```python
from core.camera import CameraController
from core.illumination import IlluminationController
from core.timelapse import TimelapseManager
from core.preview import PreviewManager
from core.config import SystemConfig
from core.profile_manager import ProfileManager

from interfaces.desktop_gui import MicroscopeGUI

from PyQt6.QtWidgets import QApplication
import sys
import time
import threading
import uvicorn
from server.api import create_app


def run_server(camera, illumination, timelapse):
    app = create_app(camera, illumination, timelapse)
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")


def run_preview(camera):
    preview = PreviewManager(camera)
    preview.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        preview.stop()


def run_timelapse(camera, illumination, config):
    timelapse = TimelapseManager(camera, illumination)

    print("Iniciando timelapse con perfil cargado...")

    timelapse.start(
        interval_seconds=config.timelapse.interval_seconds,
        duration_seconds=config.timelapse.duration_seconds,
        stabilization_time=config.timelapse.stabilization_time
    )

    try:
        while timelapse.is_running():
            print("Timelapse corriendo...")
            time.sleep(1)
    except KeyboardInterrupt:
        timelapse.stop()

    print("Timelapse terminado.")


def run_both(camera, illumination, config):
    preview = PreviewManager(camera)
    timelapse = TimelapseManager(camera, illumination)

    preview.start()
    time.sleep(2)

    print("Iniciando modo combinado con perfil cargado...")

    timelapse.start(
        interval_seconds=config.timelapse.interval_seconds,
        duration_seconds=config.timelapse.duration_seconds,
        stabilization_time=config.timelapse.stabilization_time
    )

    try:
        while timelapse.is_running():
            print("Preview + Timelapse activos")
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    timelapse.stop()
    preview.stop()

    print("Modo combinado finalizado.")


def run_gui(camera, illumination, config, profile_manager):

    from core.timelapse import TimelapseManager
    timelapse = TimelapseManager(camera, illumination)

    # Iniciar servidor en hilo separado
    server_thread = threading.Thread(
        target=run_server,
        args=(camera, illumination, timelapse),
        daemon=True
    )
    server_thread.start()

    app = QApplication(sys.argv)

    window = MicroscopeGUI(
        camera=camera,
        illumination=illumination,
        timelapse_manager=timelapse,
        config=config,
        profile_manager=profile_manager
    )

    window.show()
    app.exec()



if __name__ == "__main__":

    # ==============================
    # PERFIL ACTIVO
    # ==============================
    ACTIVE_PROFILE = "default"

    profile_manager = ProfileManager()

    if ACTIVE_PROFILE not in profile_manager.list_profiles():
        print(f"Perfil '{ACTIVE_PROFILE}' no encontrado. Creando perfil por defecto.")
        default_config = SystemConfig()
        profile_manager.save_profile(ACTIVE_PROFILE, default_config)

    config = profile_manager.load_profile(ACTIVE_PROFILE)

    print(f"Perfil activo cargado: {ACTIVE_PROFILE}")

    # ==============================
    # INICIALIZAR HARDWARE
    # ==============================
    camera = CameraController()
    illumination = IlluminationController()

    camera.set_exposure(
        config.camera.exposure_us,
        config.camera.gain
    )

    # ==============================
    # MODO DE OPERACIÓN
    # ==============================
    MODE = "web"   # "preview" | "timelapse" | "both" | "gui"

    try:
        if MODE == "preview":
            run_preview(camera)

        elif MODE == "timelapse":
            run_timelapse(camera, illumination, config)

        elif MODE == "both":
            run_both(camera, illumination, config)

        elif MODE == "gui":
            run_gui(camera, illumination, config, profile_manager)
        elif MODE == "web":
            run_server(camera, illumination, TimelapseManager(camera, illumination))

    finally:
        camera.stop()

```

---

## `codigo/MicroscopeOS/motortest.py`

Ruta original en la Pi4: `/home/microscope1/MicroscopeOS/motortest.py`

```python
import RPi.GPIO as GPIO
import time

DIR = 20
STEP = 21

GPIO.setmode(GPIO.BCM)
GPIO.setup(DIR, GPIO.OUT)
GPIO.setup(STEP, GPIO.OUT)

GPIO.output(DIR, GPIO.HIGH)

delay = 0.005  # más estable

try:
    for i in range(800):
        GPIO.output(STEP, GPIO.HIGH)
        time.sleep(delay)
        GPIO.output(STEP, GPIO.LOW)
        time.sleep(delay)

    time.sleep(1)

    GPIO.output(DIR, GPIO.LOW)

    for i in range(800):
        GPIO.output(STEP, GPIO.HIGH)
        time.sleep(delay)
        GPIO.output(STEP, GPIO.LOW)
        time.sleep(delay)

finally:
    GPIO.cleanup()

```

---

## `codigo/MicroscopeOS/profiles/alex 1.json`

Ruta original en la Pi4: `/home/microscope1/MicroscopeOS/profiles/alex 1.json`

```json
{
    "camera": {
        "exposure_us": 12000,
        "gain": 1.2,
        "resolution_width": 3280,
        "resolution_height": 2464
    },
    "timelapse": {
        "interval_seconds": 60,
        "duration_seconds": 3600,
        "stabilization_time": 0.3
    },
    "illumination": {
        "gpio_pin": 17
    }
}
```

---

## `codigo/MicroscopeOS/profiles/Alex prueba 1.json`

Ruta original en la Pi4: `/home/microscope1/MicroscopeOS/profiles/Alex prueba 1.json`

```json
{
    "camera": {
        "exposure_us": 100000,
        "gain": 1.2,
        "resolution_width": 3280,
        "resolution_height": 2464
    },
    "timelapse": {
        "interval_seconds": 60,
        "duration_seconds": 3600,
        "stabilization_time": 0.3
    },
    "illumination": {
        "gpio_pin": 17
    }
}
```

---

## `codigo/MicroscopeOS/profiles/default.json`

Ruta original en la Pi4: `/home/microscope1/MicroscopeOS/profiles/default.json`

```json
{
    "camera": {
        "exposure_us": 12000,
        "gain": 1.2,
        "resolution_width": 3280,
        "resolution_height": 2464
    },
    "timelapse": {
        "interval_seconds": 60,
        "duration_seconds": 3600,
        "stabilization_time": 0.3
    },
    "illumination": {
        "gpio_pin": 17
    }
}
```

---

## `codigo/MicroscopeOS/run_web.py`

Ruta original en la Pi4: `/home/microscope1/MicroscopeOS/run_web.py`

```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.camera import CameraController
from core.illumination import IlluminationController
from core.timelapse import TimelapseManager
from server.api import create_app
import uvicorn

print("Inicializando hardware...")
camera = CameraController()
camera.set_exposure(12000, 1.2)

# Una matriz por camara, identificadas por udev (nombres fijos)
print("Conectando matrices de iluminacion...")
luz_cam0 = IlluminationController(port="/dev/matriz_cam0")
luz_cam1 = IlluminationController(port="/dev/matriz_cam1")
illuminations = {0: luz_cam0, 1: luz_cam1}

# Brillo inicial en ambas
luz_cam0.set_brightness(80)
luz_cam1.set_brightness(80)

timelapse = TimelapseManager(camera, illuminations)

app = create_app(camera, illuminations, timelapse)
print("Servidor en http://0.0.0.0:8000")
uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")

```

---

## `codigo/MicroscopeOS/server/api.py`

Ruta original en la Pi4: `/home/microscope1/MicroscopeOS/server/api.py`

```python
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import os
import time
import numpy as np
import cv2
import tifffile
import asyncio
import json
import sys
sys.path.insert(0, '/home/microscope1/MicroscopeOS')
from temperature_controller import temperature_controller


class ExposureReq(BaseModel):
    exposure: int
    gain: float

class BrightnessReq(BaseModel):
    percent: int

class TimelapseReq(BaseModel):
    modo: str = "blanco"
    interval: int = 300
    duration: int = 3600
    stabilization: float = 0.3
    nombre: str = ""
    camaras: list = [0, 1]

class SetpointPayload(BaseModel):
    value: float


def create_app(camera, illuminations, timelapse):

    app = FastAPI()
    estado = {"camara_activa": 0}

    def _preview_png(camera_num):
        tmp = "/tmp/preview_cam.tif"
        camera.capture_image(camera_num=camera_num, folder="/tmp", filename=tmp)
        img = tifffile.imread(tmp)
        norm = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        ok, buf = cv2.imencode(".png", norm)
        return buf.tobytes()

    # ===============================
    # Preview
    # ===============================
    @app.get("/preview/{camera_num}")
    def preview(camera_num: int):
        if timelapse.is_running():
            return Response(status_code=409)
        if camera.preview_cam is not None:
            camera.stop_preview()
        estado["camara_activa"] = camera_num
        png = _preview_png(camera_num)
        return Response(content=png, media_type="image/png")

    # ===============================
    # Vivo
    # ===============================
    @app.post("/live/start/{camera_num}")
    def live_start(camera_num: int):
        if timelapse.is_running():
            return {"error": "Timelapse en curso"}
        estado["camara_activa"] = camera_num
        camera.start_preview(camera_num)
        return {"status": "live", "cam": camera_num}

    @app.post("/live/stop")
    def live_stop():
        camera.stop_preview()
        return {"status": "stopped"}

    @app.get("/live/stream")
    def live_stream():
        def gen():
            while camera.preview_cam is not None:
                frame = camera.get_preview_frame()
                if frame is None:
                    break
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
                time.sleep(0.06)
        return StreamingResponse(
            gen(), media_type='multipart/x-mixed-replace; boundary=frame')

    # ===============================
    # Luz
    # ===============================
    @app.post("/light/on")
    def light_on():
        if timelapse.is_running():
            return {"error": "Timelapse en curso"}
        luz = illuminations.get(estado["camara_activa"])
        if luz:
            luz.on()
        return {"status": "on"}

    @app.post("/light/off")
    def light_off():
        for luz in illuminations.values():
            luz.off()
        return {"status": "off"}

    # ===============================
    # Captura
    # ===============================
    @app.post("/capture/{camera_num}/{modo}")
    def capture(camera_num: int, modo: str):
        if timelapse.is_running():
            return {"error": "Timelapse en curso"}
        from datetime import datetime
        folder = "capturas_unicas"
        os.makedirs(folder, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        guardados = []
        if modo == "dpc":
            patrones = [("_L", "left"), ("_R", "right"),
                        ("_T", "top"), ("_B", "bottom")]
        else:
            patrones = [("", "on")]
        luz = illuminations.get(camera_num)
        for sufijo, metodo in patrones:
            if luz:
                getattr(luz, metodo)()
                time.sleep(0.3)
            fn = f"{folder}/cam{camera_num}_{ts}{sufijo}.tif"
            camera.capture_image(camera_num=camera_num, folder=folder, filename=fn)
            guardados.append(os.path.basename(fn))
        if luz:
            luz.off()
        return {"saved": guardados}

    # ===============================
    # Exposicion / Brillo
    # ===============================
    @app.post("/exposure")
    def set_exposure(req: ExposureReq):
        camera.set_exposure(req.exposure, req.gain)
        return {"status": "ok"}

    @app.post("/brightness")
    def set_brightness(req: BrightnessReq):
        for luz in illuminations.values():
            luz.set_brightness(req.percent)
        return {"status": "ok"}

    # ===============================
    # Timelapse
    # ===============================
    @app.post("/timelapse/start")
    def start_timelapse(req: TimelapseReq):
        if timelapse.is_running():
            return {"error": "Ya hay un timelapse corriendo"}
        timelapse.start(
            modo=req.modo,
            interval_seconds=req.interval,
            duration_seconds=req.duration,
            stabilization_time=req.stabilization,
            camaras=req.camaras
        )
        return {"status": "started"}

    @app.post("/timelapse/stop")
    def stop_timelapse():
        timelapse.stop()
        return {"status": "stopped"}

    @app.get("/status")
    def status():
        return {
            "running": timelapse.is_running(),
            "camara_activa": estado["camara_activa"],
            "ciclo": getattr(timelapse, "ciclo_actual", 0),
            "carpeta": getattr(timelapse, "base_folder", None),
        }

    # ===============================
    # Temperatura (Arduino PID)
    # ===============================
    @app.on_event("startup")
    async def start_temp():
        asyncio.create_task(temperature_controller.run())

    @app.get("/api/temperature/status")
    def temp_status():
        return temperature_controller.status()

    @app.post("/api/temperature/setpoint")
    def temp_setpoint(payload: SetpointPayload):
        ok = temperature_controller.set_target_temperature(payload.value)
        return {"ok": ok, "error": temperature_controller.error_msg}

    @app.get("/api/temperature/stream")
    async def temp_stream():
        async def gen():
            while True:
                data = temperature_controller.status()
                yield f"data: {json.dumps(data)}\n\n"
                await asyncio.sleep(1.0)
        return StreamingResponse(gen(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ===============================
    # Interfaz
    # ===============================
    app.mount("/static", StaticFiles(directory="server/static"), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index():
        with open("server/static/index.html", "r") as f:
            return f.read()

    return app

```

---

## `codigo/MicroscopeOS/server/static/index.html`

Ruta original en la Pi4: `/home/microscope1/MicroscopeOS/server/static/index.html`

```html
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MicroscopeOS</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Space+Grotesk:wght@500;700&display=swap');
  :root{
    --bg:#0a0e0d;--panel:#111816;--panel2:#0d1413;--line:#1f2e2a;
    --txt:#c8d6d0;--dim:#5f7269;--green:#3ddc84;--amber:#ffb454;--red:#ff5c5c;
    --glow:rgba(61,220,132,.15);
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--txt);font-family:'JetBrains Mono',monospace;font-size:14px;
    background-image:radial-gradient(circle at 20% 10%,#0f1a17 0%,var(--bg) 55%);min-height:100vh;padding:18px}
  .head{display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line);padding-bottom:14px;margin-bottom:18px}
  .head h1{font-family:'Space Grotesk',sans-serif;font-size:22px;letter-spacing:1px;color:#fff;font-weight:700}
  .head h1 span{color:var(--green)}
  .badge{display:flex;align-items:center;gap:8px;font-size:12px;padding:6px 14px;border:1px solid var(--line);border-radius:20px;background:var(--panel)}
  .dot{width:9px;height:9px;border-radius:50%;background:var(--dim);transition:.3s}
  .dot.idle{background:var(--green);box-shadow:0 0 8px var(--green)}
  .dot.run{background:var(--red);box-shadow:0 0 10px var(--red);animation:pulse 1.2s infinite}
  @keyframes pulse{50%{opacity:.35}}
  .grid{display:grid;grid-template-columns:1fr 360px;gap:18px}
  @media(max-width:820px){.grid{grid-template-columns:1fr}}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px}
  .panel h2{font-family:'Space Grotesk',sans-serif;font-size:12px;letter-spacing:2px;text-transform:uppercase;color:var(--dim);margin-bottom:14px;display:flex;align-items:center;gap:8px}
  .panel h2::before{content:'';width:14px;height:2px;background:var(--green)}
  .view{position:relative;background:var(--panel2);border:1px solid var(--line);border-radius:10px;aspect-ratio:4/3;overflow:hidden;display:flex;align-items:center;justify-content:center}
  .view img{width:100%;height:100%;object-fit:contain;display:none}
  .view .ph{color:var(--dim);font-size:13px;text-align:center;padding:20px}
  .scan{position:absolute;inset:0;pointer-events:none;background:repeating-linear-gradient(0deg,transparent 0 3px,rgba(0,0,0,.12) 3px 4px);opacity:.4}
  .camtag{position:absolute;top:10px;left:10px;font-size:11px;background:rgba(0,0,0,.6);padding:4px 10px;border-radius:5px;color:var(--green);border:1px solid var(--line);letter-spacing:1px}
  .live{position:absolute;top:10px;right:10px;font-size:11px;background:rgba(0,0,0,.6);padding:4px 10px;border-radius:5px;color:var(--red);border:1px solid var(--line);display:none;align-items:center;gap:6px}
  .live::before{content:'';width:7px;height:7px;border-radius:50%;background:var(--red);animation:pulse 1s infinite}
  .camsel{display:flex;gap:8px;margin-top:12px}
  .camsel button{flex:1}
  button{font-family:'JetBrains Mono',monospace;font-size:13px;cursor:pointer;background:var(--panel2);color:var(--txt);border:1px solid var(--line);border-radius:8px;padding:11px 14px;transition:.15s;letter-spacing:.5px}
  button:hover:not(:disabled){border-color:var(--green);color:#fff;background:#16211d}
  button:disabled{opacity:.3;cursor:not-allowed}
  button.active{border-color:var(--green);color:var(--green);background:var(--glow)}
  button.primary{background:var(--green);color:#04130a;border-color:var(--green);font-weight:700}
  button.primary:hover:not(:disabled){background:#5ef29a}
  button.danger{background:transparent;color:var(--red);border-color:#3a1f1f}
  button.danger:hover:not(:disabled){background:var(--red);color:#fff;border-color:var(--red)}
  button.lit{background:var(--amber);color:#1a1100;border-color:var(--amber);font-weight:700}
  button.lit:hover:not(:disabled){background:#ffc673}
  .field{margin-bottom:14px}
  .field label{display:block;font-size:11px;color:var(--dim);margin-bottom:6px;letter-spacing:1px;text-transform:uppercase}
  .field input,.field select{width:100%;background:var(--panel2);border:1px solid var(--line);border-radius:7px;padding:10px;color:var(--txt);font-family:'JetBrains Mono',monospace;font-size:13px}
  .field input:focus,.field select:focus{outline:none;border-color:var(--green)}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
  .slider{display:flex;align-items:center;gap:10px}
  .slider input[type=range]{flex:1;accent-color:var(--amber)}
  .slider .val{min-width:42px;text-align:right;color:var(--amber);font-weight:700}
  .stat{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--line);font-size:12px}
  .stat:last-child{border:0}
  .stat .k{color:var(--dim)}
  .stat .v.hot{color:var(--amber)}
  .actions{display:flex;flex-direction:column;gap:10px;margin-top:6px}
  .toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%) translateY(80px);background:var(--panel);border:1px solid var(--green);color:var(--green);padding:12px 22px;border-radius:8px;font-size:13px;transition:.3s;z-index:50}
  .toast.show{transform:translateX(-50%) translateY(0)}
  .valve-pill{display:inline-flex;align-items:center;gap:6px;font-size:11px;font-weight:700;letter-spacing:.5px;padding:3px 10px;border-radius:20px;border:1px solid var(--line)}
  .valve-pill .vdot{width:6px;height:6px;border-radius:50%;background:currentColor}
  .valve-pill.open{color:var(--green);border-color:var(--green);background:var(--glow)}
  .valve-pill.closed{color:var(--dim);border-color:var(--line)}
</style>
</head>
<body>
<div class="head">
  <h1>MICROSCOPE<span>OS</span></h1>
  <div class="badge"><span class="dot idle" id="dot"></span><span id="state">IDLE</span></div>
</div>
<div class="grid">
  <div class="panel">
    <h2>Vista / Posicionamiento</h2>
    <div class="view">
      <span class="ph" id="ph">Selecciona una cámara para ver</span>
      <img id="img">
      <div class="scan"></div>
      <div class="camtag" id="camtag" style="display:none">CAM 0</div>
      <div class="live" id="liveTag">EN VIVO</div>
    </div>
    <div class="camsel">
      <button id="cam0" onclick="verCam(0)">◉ Cámara 0</button>
      <button id="cam1" onclick="verCam(1)">◉ Cámara 1</button>
    </div>
    <div class="camsel">
      <button id="liveBtn" onclick="toggleLive()">▶ En vivo</button>
      <button id="refresh" onclick="refrescar()">⟳ Refrescar</button>
    </div>

    <h2 style="margin-top:22px">Luz manual</h2>
    <div class="camsel">
      <button id="lightBtn" onclick="toggleLuz()">💡 Encender luz</button>
    </div>
    <div class="field" style="margin-top:12px">
      <label>Intensidad LED</label>
      <div class="slider">
        <input type="range" id="bright" min="0" max="100" value="80" oninput="brightLabel()">
        <span class="val" id="brightVal">80%</span>
      </div>
    </div>

    <h2 style="margin-top:14px">Captura única</h2>
    <div class="field">
      <label>Modo de foto</label>
      <select id="capModo"><option value="normal">Normal (1 foto)</option><option value="dpc">DPC (4 fotos L/R/T/B)</option></select>
    </div>
    <button id="snap" onclick="capturar()" style="width:100%">📷 Tomar foto</button>
  </div>

  <div>
    <div class="panel" style="margin-bottom:18px">
      <h2>Cámara</h2>
      <div class="row">
        <div class="field"><label>Exposición µs</label><input type="number" id="exp" value="12000"></div>
        <div class="field"><label>Ganancia</label><input type="number" step="0.1" id="gain" value="1.2"></div>
      </div>
      <button onclick="aplicarCamara()" style="width:100%">Aplicar exposición/ganancia</button>
    </div>
    <div class="panel" style="margin-bottom:18px">
      <h2>Timelapse</h2>
      <div class="field"><label>Nombre experimento</label><input type="text" id="nombre" placeholder="ej. celulas_dia1"></div>
      <div class="field"><label>Modo iluminación</label>
        <select id="modo"><option value="blanco">Blanco (1 foto)</option><option value="dpc">DPC (4 fotos)</option></select>
      </div>
      <div class="row">
        <div class="field"><label>Intervalo s</label><input type="number" id="intv" value="300"></div>
        <div class="field"><label>Duración s</label><input type="number" id="dur" value="3600"></div>
      </div>
      <div class="actions">
        <button class="primary" id="start" onclick="iniciar()">▶ Iniciar Timelapse</button>
        <button class="danger" id="stop" onclick="detener()" disabled>■ STOP de emergencia</button>
      </div>
    </div>
    <div class="panel" style="margin-bottom:18px">
      <h2>Temperatura</h2>
      <div style="display:flex;align-items:baseline;gap:6px;margin-bottom:12px">
        <span id="tVal" style="font-size:2.2rem;font-weight:700;color:var(--txt);min-width:5ch;text-align:right">--.-</span>
        <span style="color:var(--dim)">°C</span>
        <span id="tDot" style="width:8px;height:8px;border-radius:50%;background:var(--dim);margin-left:8px;display:inline-block"></span>
      </div>
      <div class="stat"><span class="k">Setpoint</span><span class="v" id="tSp">-- °C</span></div>
      <div class="stat"><span class="k">PWM calefactor</span><span class="v" id="tPwm">--</span></div>
      <div style="background:var(--panel2);border-radius:3px;height:4px;margin:10px 0">
        <div id="tBar" style="background:var(--amber);height:100%;width:0%;border-radius:3px;transition:width .4s"></div>
      </div>
      <div class="field" style="margin-top:10px;margin-bottom:0">
        <label>Setpoint objetivo</label>
        <div class="slider">
          <input type="range" id="spSlider" min="20" max="80" step="0.5" value="37" oninput="document.getElementById('spPrev').textContent=parseFloat(this.value).toFixed(1)">
          <span class="val" id="spPrev">37.0</span>
        </div>
      </div>
      <button onclick="aplicarSetpoint()" style="width:100%;margin-top:8px">Aplicar setpoint</button>
    </div>
    <div class="panel" style="margin-bottom:18px">
      <h2>CO2</h2>
      <div style="display:flex;align-items:baseline;gap:6px;margin-bottom:12px">
        <span id="cVal" style="font-size:2.2rem;font-weight:700;color:var(--txt);min-width:5ch;text-align:right">--.-</span>
        <span style="color:var(--dim)">ppm</span>
        <span id="cDot" style="width:8px;height:8px;border-radius:50%;background:var(--dim);margin-left:8px;display:inline-block"></span>
      </div>
      <div class="stat"><span class="k">Setpoint</span><span class="v" id="cSp">-- ppm</span></div>
      <div class="stat"><span class="k">Válvula solenoide</span><span class="v"><span class="valve-pill closed" id="valvePill"><span class="vdot"></span><span id="valveTxt">--</span></span></span></div>
      <div class="stat"><span class="k">Duty cycle válvula</span><span class="v" id="cDuty">--</span></div>
      <div style="background:var(--panel2);border-radius:3px;height:4px;margin:10px 0">
        <div id="cBar" style="background:#4dabf7;height:100%;width:0%;border-radius:3px;transition:width .4s"></div>
      </div>
      <div class="stat"><span class="k">Temp. ambiente (SCD30)</span><span class="v" id="cAmbient">-- °C</span></div>
      <div class="stat"><span class="k">Humedad</span><span class="v" id="cHum">-- %</span></div>
      <div class="field" style="margin-top:10px;margin-bottom:0">
        <label>Setpoint CO2</label>
        <div class="slider">
          <input type="range" id="co2Slider" min="400" max="100000" step="500" value="40000" oninput="document.getElementById('co2Prev').textContent=parseInt(this.value)">
          <span class="val" id="co2Prev">40000</span>
        </div>
      </div>
      <button onclick="aplicarCo2Setpoint()" style="width:100%;margin-top:8px">Aplicar setpoint CO2</button>
    </div>
    <div class="panel">
      <h2>Estado</h2>
      <div class="stat"><span class="k">Estado</span><span class="v" id="s_state">IDLE</span></div>
      <div class="stat"><span class="k">Ciclo actual</span><span class="v" id="s_ciclo">—</span></div>
      <div class="stat"><span class="k">Cámara activa</span><span class="v" id="s_cam">—</span></div>
      <div class="stat"><span class="k">Carpeta</span><span class="v" id="s_folder" style="font-size:10px">—</span></div>
    </div>
  </div>
</div>
<div class="toast" id="toast"></div>
<script>
let running=false,activeCam=0,liveOn=false,luzOn=false,liveTimer=null;

function toast(m){const t=document.getElementById('toast');t.textContent=m;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2500);}
function brightLabel(){document.getElementById('brightVal').textContent=document.getElementById('bright').value+'%';}

async function verCam(n){
  if(running)return;
  activeCam=n;
  document.getElementById('cam0').classList.toggle('active',n===0);
  document.getElementById('cam1').classList.toggle('active',n===1);
  await refrescar();
}
async function refrescar(){
  if(running){toast('Timelapse en curso');return;}
  const ph=document.getElementById('ph'),img=document.getElementById('img');
  if(img.style.display!=='block'){ph.textContent='Capturando...';ph.style.display='block';}
  try{
    const r=await fetch('/preview/'+activeCam+'?t='+Date.now());
    if(r.status===409)return;
    const blob=await r.blob();
    img.src=URL.createObjectURL(blob);img.style.display='block';ph.style.display='none';
    const tag=document.getElementById('camtag');tag.textContent='CAM '+activeCam;tag.style.display='block';
  }catch(e){ph.textContent='Error';}
}
async function toggleLive(){
  if(running)return;
  liveOn=!liveOn;
  const btn=document.getElementById('liveBtn');
  const img=document.getElementById('img');
  const ph=document.getElementById('ph');
  btn.classList.toggle('active',liveOn);
  btn.textContent=liveOn?'⏸ Detener vivo':'▶ En vivo';
  document.getElementById('liveTag').style.display=liveOn?'flex':'none';
  document.getElementById('cam0').disabled=liveOn;
  document.getElementById('cam1').disabled=liveOn;
  document.getElementById('refresh').disabled=liveOn;
  document.getElementById('snap').disabled=liveOn;

  if(liveOn){
    await fetch('/live/start/'+activeCam,{method:'POST'});
    ph.style.display='none';
    img.style.display='block';
    img.src='/live/stream?t='+Date.now();   // stream MJPEG continuo
    const tag=document.getElementById('camtag');
    tag.textContent='CAM '+activeCam;tag.style.display='block';
  }else{
    img.src='';
    await fetch('/live/stop',{method:'POST'});
    document.getElementById('cam'+activeCam).disabled=false;
    document.getElementById('cam'+(activeCam===0?1:0)).disabled=false;
    document.getElementById('refresh').disabled=false;
    document.getElementById('snap').disabled=false;
    refrescar();
  }
}
async function toggleLuz(){
  if(running)return;
  luzOn=!luzOn;
  const b=document.getElementById('lightBtn');
  if(luzOn){
    await fetch('/brightness',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({percent:+document.getElementById('bright').value})});
    await fetch('/light/on',{method:'POST'});
    b.classList.add('lit');b.textContent='💡 Apagar luz';
  }else{
    await fetch('/light/off',{method:'POST'});
    b.classList.remove('lit');b.textContent='💡 Encender luz';
  }
}
async function capturar(){
  if(running)return;
  if(liveOn)await toggleLive();   // apaga el vivo antes de capturar
  const modo=document.getElementById('capModo').value;
  toast('Capturando ('+modo+')...');
  const r=await fetch('/capture/'+activeCam+'/'+modo,{method:'POST'});
  const d=await r.json();
  if(d.error){toast(d.error);return;}
  toast('Guardado: '+(Array.isArray(d.saved)?d.saved.length+' fotos':d.saved));
  luzOn=false;document.getElementById('lightBtn').classList.remove('lit');document.getElementById('lightBtn').textContent='💡 Encender luz';
}
async function aplicarCamara(){
  await fetch('/exposure',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({exposure:+document.getElementById('exp').value,gain:+document.getElementById('gain').value})});
  toast('Exposición aplicada');
}
async function iniciar(){
  if(liveOn)toggleLive();
  const body={modo:document.getElementById('modo').value,interval:+document.getElementById('intv').value,duration:+document.getElementById('dur').value,nombre:document.getElementById('nombre').value,camaras:[0,1]};
  const r=await fetch('/timelapse/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const d=await r.json();
  if(d.error){toast(d.error);return;}
  toast('Timelapse iniciado');
}
async function detener(){await fetch('/timelapse/stop',{method:'POST'});toast('Detenido');}
function setRunning(on){
  running=on;
  document.getElementById('dot').className='dot '+(on?'run':'idle');
  document.getElementById('state').textContent=on?'RUNNING':'IDLE';
  document.getElementById('s_state').textContent=on?'RUNNING':'IDLE';
  ['cam0','cam1','refresh','snap','start','liveBtn','lightBtn'].forEach(id=>document.getElementById(id).disabled=on);
  document.getElementById('stop').disabled=!on;
  if(on&&liveOn){liveOn=false;clearInterval(liveTimer);document.getElementById('liveTag').style.display='none';document.getElementById('liveBtn').textContent='▶ En vivo';document.getElementById('liveBtn').classList.remove('active');}
}
async function poll(){
  try{
    const r=await fetch('/status');const s=await r.json();
    if(s.running!==running)setRunning(s.running);
    document.getElementById('s_ciclo').textContent=s.ciclo||'—';
    document.getElementById('s_ciclo').className='v'+(s.running?' hot':'');
    document.getElementById('s_cam').textContent='CAM '+s.camara_activa;
    document.getElementById('s_folder').textContent=s.carpeta||'—';
  }catch(e){}
}
setInterval(poll,2000);poll();verCam(0);

// --- Temperatura SSE ---
(function(){
  const evtSource = new EventSource('/api/temperature/stream');
  evtSource.onmessage = function(e){
    try{
      const d = JSON.parse(e.data);
      document.getElementById('tDot').style.background = d.connected ? 'var(--green)' : 'var(--red)';
      if(d.temperature !== null && d.temperature !== undefined){
        const t = parseFloat(d.temperature).toFixed(1);
        const el = document.getElementById('tVal');
        el.textContent = t;
        const diff = Math.abs(d.temperature - d.setpoint);
        el.style.color = diff < 0.5 ? 'var(--green)' : diff < 2 ? 'var(--amber)' : 'var(--red)';
      }
      if(d.setpoint !== undefined){
        document.getElementById('tSp').textContent = parseFloat(d.setpoint).toFixed(1) + ' °C';
        if(document.activeElement !== document.getElementById('spSlider')){
          document.getElementById('spSlider').value = d.setpoint;
          document.getElementById('spPrev').textContent = parseFloat(d.setpoint).toFixed(1);
        }
      }
      if(d.pwm !== null && d.pwm !== undefined){
        document.getElementById('tPwm').textContent = d.pwm + ' / 255';
        document.getElementById('tBar').style.width = ((d.pwm/255)*100).toFixed(1) + '%';
      }

      // --- CO2 / válvula (mismo stream) ---
      const hasCo2 = d.co2 !== null && d.co2 !== undefined && !isNaN(d.co2);
      document.getElementById('cDot').style.background = (d.connected && hasCo2) ? 'var(--green)' : 'var(--dim)';
      const cVal = document.getElementById('cVal');
      if(hasCo2){
        cVal.textContent = parseFloat(d.co2).toFixed(0);
        cVal.style.color = 'var(--txt)';
      }else{
        cVal.textContent = '--.-';
      }
      if(d.co2_setpoint !== undefined && d.co2_setpoint !== null){
        document.getElementById('cSp').textContent = parseFloat(d.co2_setpoint).toFixed(0) + ' ppm';
        if(document.activeElement !== document.getElementById('co2Slider')){
          document.getElementById('co2Slider').value = d.co2_setpoint;
          document.getElementById('co2Prev').textContent = parseInt(d.co2_setpoint);
        }
      }
      const pill = document.getElementById('valvePill'), vtxt = document.getElementById('valveTxt');
      if(d.valve_open !== null && d.valve_open !== undefined){
        const isOpen = !!d.valve_open;
        pill.className = 'valve-pill ' + (isOpen ? 'open' : 'closed');
        vtxt.textContent = isOpen ? 'ABIERTA' : 'CERRADA';
      }else{
        pill.className = 'valve-pill closed';
        vtxt.textContent = '--';
      }
      if(d.co2_duty !== null && d.co2_duty !== undefined){
        document.getElementById('cDuty').textContent = parseFloat(d.co2_duty).toFixed(1) + ' %';
        document.getElementById('cBar').style.width = Math.min(100,parseFloat(d.co2_duty)).toFixed(1) + '%';
      }
      if(d.ambient_temp !== null && d.ambient_temp !== undefined){
        document.getElementById('cAmbient').textContent = parseFloat(d.ambient_temp).toFixed(1) + ' °C';
      }
      if(d.humidity !== null && d.humidity !== undefined){
        document.getElementById('cHum').textContent = parseFloat(d.humidity).toFixed(1) + ' %';
      }
    }catch(err){}
  };
  evtSource.onerror = function(){
    document.getElementById('tDot').style.background = 'var(--red)';
    document.getElementById('tVal').textContent = 'ERR';
    document.getElementById('cDot').style.background = 'var(--red)';
  };
})();
async function aplicarSetpoint(){
  const val = parseFloat(document.getElementById('spSlider').value);
  const r = await fetch('/api/temperature/setpoint',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({value:val})});
  const d = await r.json();
  toast(d.ok ? 'Setpoint: '+val+'°C' : 'Error: '+d.error);
}
async function aplicarCo2Setpoint(){
  const val = parseFloat(document.getElementById('co2Slider').value);
  const r = await fetch('/api/temperature/co2_setpoint',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({value:val})});
  const d = await r.json();
  toast(d.ok ? 'Setpoint CO2: '+val+' ppm' : 'Error: '+d.error);
}
</script>
</body>
</html>

```

---

## `codigo/MicroscopeOS/start_microscope.py`

Ruta original en la Pi4: `/home/microscope1/MicroscopeOS/start_microscope.py`

```python
#!/bin/bash

cd /home/microscope1/MicroscopeOS

source venv/bin/activate

python3 main.py

```

---

## `codigo/MicroscopeOS/start_microscope.sh`

Ruta original en la Pi4: `/home/microscope1/MicroscopeOS/start_microscope.sh`

```bash
#!/bin/bash

cd /home/microscope1/MicroscopeOS

source venv/bin/activate

python3 main.py

```

---

## `codigo/MicroscopeOS/temperature_controller.py`

Ruta original en la Pi4: `/home/microscope1/MicroscopeOS/temperature_controller.py`

```python
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

```

---

## `codigo/MicroscopeOS/test_captura.py`

Ruta original en la Pi4: `/home/microscope1/MicroscopeOS/test_captura.py`

```python
import os
import sys
import numpy as np
import tifffile

# Para que encuentre el módulo core/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.camera import CameraController


def revisar(filename, cam_num):
    img = tifffile.imread(filename)
    print(f"\n--- Cámara {cam_num} ---")
    print(f"  Archivo: {filename}")
    print(f"  Dimensiones: {img.shape}")        # debe ser (2464, 3280), 2D = gris
    print(f"  Tipo de dato: {img.dtype}")       # debe ser uint16
    print(f"  Min / Max: {img.min()} / {img.max()}")
    print(f"  Promedio: {img.mean():.1f}")

    # Checks básicos
    if img.ndim != 2:
        print("  ⚠️  OJO: la imagen no es 2D, el debayer a gris no salió bien.")
    if img.max() == 0:
        print("  ⚠️  OJO: imagen toda en negro (¿exposición muy baja o tapada?).")
    if img.max() == img.min():
        print("  ⚠️  OJO: imagen uniforme, sin variación (¿sensor sin señal?).")
    else:
        print("  ✅ Imagen con contenido válido.")


if __name__ == "__main__":
    cam = CameraController()
    cam.set_exposure(12000, 1.2)

    os.makedirs("test_capturas", exist_ok=True)

    for cam_num in [0, 1]:
        print(f"\nCapturando de cámara {cam_num}...")
        archivo = cam.capture_image(camera_num=cam_num, folder="test_capturas")
        revisar(archivo, cam_num)

    cam.stop()
    print("\nListo. Revisa la carpeta test_capturas/")

```

---

## `codigo/MicroscopeOS/test_luz.py`

Ruta original en la Pi4: `/home/microscope1/MicroscopeOS/test_luz.py`

```python
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.illumination import IlluminationController

luz = IlluminationController()

print("Encendiendo...")
luz.on()
time.sleep(2)

print("Brillo a la mitad...")
luz.set_brightness(120)
luz.on()
time.sleep(2)

print("Apagando...")
luz.off()

luz.close()
print("Prueba terminada.")

```

---

## `codigo/MicroscopeOS/test_timelapse.py`

Ruta original en la Pi4: `/home/microscope1/MicroscopeOS/test_timelapse.py`

```python
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.camera import CameraController
from core.illumination import IlluminationController
from core.timelapse import TimelapseManager

print("Inicializando hardware...")
camera = CameraController()
camera.set_exposure(12000, 1.2)

illumination = IlluminationController()
illumination.set_brightness(100)   # 80%

timelapse = TimelapseManager(camera, illumination)

print("Arrancando timelapse de prueba (blanco, 2 camaras, cada 10s, 40s total)...")
timelapse.start(
    modo="dpc",
    interval_seconds=60,
    duration_seconds=120,
    stabilization_time=0.3,
    camaras=[0, 1]
)

# Esperar a que termine
while timelapse.is_running():
    time.sleep(1)

print("\nTimelapse terminado. Limpiando...")
illumination.close()
camera.stop()
print("Listo. Revisa la carpeta timelapse_* que se creo.")


```

---

## `codigo/MicroscopeOS/ver_timelapse.py`

Ruta original en la Pi4: `/home/microscope1/MicroscopeOS/ver_timelapse.py`

```python
import sys, os, glob
import tifffile, cv2, numpy as np

# Buscar la carpeta de timelapse mas reciente
carpetas = sorted(glob.glob("timelapse_2026*"), key=os.path.getmtime)
if not carpetas:
    print("No se encontraron carpetas de timelapse.")
    sys.exit()

base = carpetas[-1]
print(f"Convirtiendo: {base}")

salida = os.path.join(base, "previews")
os.makedirs(salida, exist_ok=True)

tifs = glob.glob(os.path.join(base, "cam*", "*.tif"))
print(f"Encontradas {len(tifs)} imagenes")

for tif in sorted(tifs):
    img = tifffile.imread(tif)
    norm = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    # Nombre: cam0_img_xxxx.png
    cam = os.path.basename(os.path.dirname(tif))
    nombre = cam + "_" + os.path.basename(tif).replace(".tif", ".png")
    cv2.imwrite(os.path.join(salida, nombre), norm)

print(f"Listo. PNGs en: {salida}")

```

---

## `codigo/extras/matrices_led_rp2040/capture_led.py`

Ruta original en la Pi4: `/home/microscope1/capture_led.py`

```python
from gpiozero import LED
from time import sleep
import subprocess
from datetime import datetime

led = LED(17)

try:
	print ("Led is on")
	led.on()

	sleep(0.3)

	#Fliename date&time
	timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
	filename = f"foto_{timestamp}.png"

	print ("Taking picture")
	subprocess.run(["rpicam-still", "-o", filename])

	print ("Led is off")
	led.off()

except KeyboardInterrupt:
	led.off()
	print ("led is off manually")

```

---

## `codigo/extras/matrices_led_rp2040/led_test.py`

Ruta original en la Pi4: `/home/microscope1/led_test.py`

```python
from gpiozero import LED
from time import sleep

led = LED(17)

while True:
	led.on()
	sleep(1)
	led.off()
	sleep(1)

```

---

## `codigo/extras/matrices_led_rp2040/mapeo_leds.py`

Ruta original en la Pi4: `/home/microscope1/mapeo_leds.py`

```python
from machine import Pin
from neopixel import NeoPixel
import time

np = NeoPixel(Pin(16), 25)

def limpiar():
    for j in range(25):
        np[j] = (0, 0, 0)
    np.write()

limpiar()
time.sleep(0.5)

for i in range(25):
    limpiar()
    np[i] = (50, 50, 50)
    np.write()
    print("LED", i)
    time.sleep(0.6)

limpiar()
print("Fin del mapeo")

```

---

## `codigo/extras/matrices_led_rp2040/rp2040_firmware.py`

Ruta original en la Pi4: `/home/microscope1/rp2040_firmware.py`

```python
# Firmware RP2040-Matrix v3: iluminacion blanco + patrones DPC
import sys
from machine import Pin
from neopixel import NeoPixel

NUM_LEDS = 25
LED_PIN = 16

np = NeoPixel(Pin(LED_PIN), NUM_LEDS)
brightness = 255

# Mapas de patrones (confirmados visualmente)
PATRONES = {
    "ALL":    list(range(25)),
    "LEFT":   [0,1,2,3,4, 5,6,7,8,9],
    "RIGHT":  [15,16,17,18,19, 20,21,22,23,24],
    "TOP":    [3,4, 8,9, 13,14, 18,19, 23,24],
    "BOTTOM": [0,1, 5,6, 10,11, 15,16, 20,21],
}

def limpiar():
    for j in range(NUM_LEDS):
        np[j] = (0, 0, 0)
    np.write()

def prender(lista):
    limpiar()
    for j in lista:
        np[j] = (brightness, brightness, brightness)
    np.write()

def procesar(cmd):
    global brightness
    cmd = cmd.strip().upper()

    if cmd == "ON" or cmd == "ALL":
        prender(PATRONES["ALL"])
        return "OK " + cmd
    elif cmd == "OFF":
        limpiar()
        return "OK OFF"
    elif cmd in PATRONES:
        prender(PATRONES[cmd])
        return "OK " + cmd
    elif cmd.startswith("BRIGHT"):
        try:
            n = int(cmd.split()[1])
            brightness = max(0, min(255, n))
            return "OK BRIGHT " + str(brightness)
        except (IndexError, ValueError):
            return "ERR brillo invalido"
    elif cmd == "":
        return None
    else:
        return "ERR desconocido: " + cmd

# Apagar al arrancar
limpiar()

# Lectura serial robusta (caracter por caracter)
buffer = ""
while True:
    c = sys.stdin.read(1)
    if c:
        if c == "\n" or c == "\r":
            if buffer:
                resp = procesar(buffer)
                if resp:
                    print(resp)
                buffer = ""
        else:
            buffer += c

```

---

## `codigo/extras/incubadora_temperatura/Control_Incubator_TempCO2/Control_Incubator_TempCO2.ino`

Ruta original en la Pi4: `/home/microscope1/Control_Incubator_TempCO2/Control_Incubator_TempCO2.ino`

```cpp
/*
 * =====================================================================
 *  Controlador PID Doble — MicroscopeOS
 * =====================================================================
 *  - Temperatura: DS18B20 + PWM calefactor (D3)
 *  - CO2: SCD30 + válvula solenoide (D9)
 *
 *  Protocolo serial (9600 baud, compatible con temperature_controller.py):
 *    Comandos recibidos:
 *      SET:xx.x\n          → setpoint de temperatura (°C)
 *      SET_CO2:xxxxx\n     → setpoint de CO2 (ppm)
 *    Telemetría enviada (JSON, cada ~1s):
 *      {"temp":..,"setpoint":..,"pwm":..,
 *       "co2":..,"co2_set":..,"co2_duty":..,"valve":..,
 *       "ambient_temp":..,"humidity":..}
 *
 *  Conexiones:
 *    DS18B20   → D2 (pull-up 4.7kΩ entre datos y VCC)
 *    Calefactor→ D3 (PWM → MOSFET)
 *    SCD30     → I2C (SDA/SCL)
 *    Válvula   → D9 (a través de driver/relevador correspondiente)
 * =====================================================================
 */

#include <Wire.h>
#include <Adafruit_SCD30.h>
#include <PID_v1.h>
#include <OneWire.h>
#include <DallasTemperature.h>

// ==========================================================
// DS18B20 (Temperatura) — igual al sketch ya afinado
// ==========================================================

#define ONEWIRE_PIN 2
int PWM_pin = 3;

OneWire oneWireBus(ONEWIRE_PIN);
DallasTemperature sensors(&oneWireBus);

float temperature_read = 0.0;
float set_temperature  = 37.0;

float PID_error      = 0;
float previous_error = 0;
float elapsedTime, Time, timePrev;
int   PID_value      = 0;

// Ganancias ya afinadas para evitar overshoot
float kp = 5.0;
float ki = 0.02;
float kd = 3.0;

float PID_p = 0;
float PID_i = 0;
float PID_d = 0;

// Límite de rampa del PWM (evita subida/bajada de golpe)
int   PID_value_prev  = 0;
const int MAX_PWM_STEP = 4;   // pasos de PWM por ciclo (~300ms)

unsigned long lastTempRead = 0;
const unsigned long tempInterval = 300;

// ==========================================================
// SCD30 (CO2) + válvula solenoide
// ==========================================================

Adafruit_SCD30 scd30;
bool scd30_ok = false;   // si no se encuentra el sensor, no se bloquea todo el sistema

const int VALVULA_PIN = 9;

double CO2_Setpoint = 40000.0;   // 4% CO2 — target biológico real
double CO2_Input    = 0;
double CO2_Output   = 0;

double CO2_Kp = 0.08;
double CO2_Ki = 0.02;
double CO2_Kd = 0.00;

PID CO2_PID(&CO2_Input, &CO2_Output, &CO2_Setpoint, CO2_Kp, CO2_Ki, CO2_Kd, DIRECT);

const unsigned long WindowSize = 10000;   // ventana de PWM lento para la válvula (10s)
unsigned long windowStartTime;

bool valveState         = false;
bool previousValveState = false;

float ambientTemp = 0;
float ambientHum  = 0;
float co2ppm      = 0;

// ==========================================================
// Telemetría
// ==========================================================

unsigned long lastTelemetry = 0;
const unsigned long telemetryInterval = 1000;


void setup() {
  Serial.begin(9600);

  // ---------- DS18B20 ----------
  sensors.begin();
  pinMode(PWM_pin, OUTPUT);
  TCCR2B = TCCR2B & B11111000 | 0x03;   // PWM ~980 Hz
  Time = millis();

  // ---------- SCD30 ----------
  Wire.begin();
  if (scd30.begin()) {
    scd30_ok = true;
  } else {
    Serial.println("WARN:SCD30_NOT_FOUND");
    // No se cuelga el programa: la temperatura sigue funcionando
    // aunque el sensor de CO2 no responda.
  }

  pinMode(VALVULA_PIN, OUTPUT);
  digitalWrite(VALVULA_PIN, LOW);

  CO2_PID.SetOutputLimits(0, WindowSize);
  CO2_PID.SetMode(AUTOMATIC);
  windowStartTime = millis();

  Serial.println("READY");
}


void loop() {
  recibirComandos();
  controlarTemperatura();
  if (scd30_ok) {
    controlarCO2();
  }
  enviarTelemetria();
}


// ==========================================================
// RECEPCION DE COMANDOS
// ==========================================================
void recibirComandos() {
  if (Serial.available() > 0) {
    String msg = Serial.readStringUntil('\n');
    msg.trim();

    // Temperatura — protocolo original: "SET:37.5"
    if (msg.startsWith("SET:")) {
      float new_sp = msg.substring(4).toFloat();
      if (new_sp >= 20.0 && new_sp <= 80.0) {
        set_temperature = new_sp;
      }
    }

    // CO2 — nuevo: "SET_CO2:40000"
    else if (msg.startsWith("SET_CO2:")) {
      float new_sp = msg.substring(8).toFloat();
      if (new_sp >= 400 && new_sp <= 100000) {
        CO2_Setpoint = new_sp;
      }
    }
  }
}


// ==========================================================
// PID TEMPERATURA (con rate limiter)
// ==========================================================
void controlarTemperatura() {
  if (millis() - lastTempRead < tempInterval) return;
  lastTempRead = millis();

  sensors.requestTemperatures();
  float t = sensors.getTempCByIndex(0);
  if (t == DEVICE_DISCONNECTED_C) return;
  temperature_read = t;

  PID_error = set_temperature - temperature_read;

  timePrev    = Time;
  Time        = millis();
  elapsedTime = (Time - timePrev) / 1000.0;

  PID_p = kp * PID_error;

  PID_i += ki * PID_error * elapsedTime;
  if (PID_i > 255) PID_i = 255;
  if (PID_i < 0)   PID_i = 0;

  if (elapsedTime > 0) {
    PID_d = kd * ((PID_error - previous_error) / elapsedTime);
  }

  PID_value = PID_p + PID_i + PID_d;
  if (PID_value < 0)   PID_value = 0;
  if (PID_value > 255) PID_value = 255;

  // Límite de rampa: evita saltos bruscos de PWM
  if (PID_value > PID_value_prev + MAX_PWM_STEP) {
    PID_value = PID_value_prev + MAX_PWM_STEP;
  } else if (PID_value < PID_value_prev - MAX_PWM_STEP) {
    PID_value = PID_value_prev - MAX_PWM_STEP;
  }
  PID_value_prev = PID_value;

  analogWrite(PWM_pin, PID_value);
  previous_error = PID_error;
}


// ==========================================================
// PID CO2 + VÁLVULA SOLENOIDE
// ==========================================================
void controlarCO2() {
  if (!scd30.dataReady()) return;
  if (!scd30.read())      return;

  co2ppm      = scd30.CO2;
  ambientTemp = scd30.temperature;
  ambientHum  = scd30.relative_humidity;

  CO2_Input = co2ppm;
  CO2_PID.Compute();

  unsigned long now = millis();
  if ((now - windowStartTime) > WindowSize) {
    windowStartTime += WindowSize;
  }

  valveState = ((now - windowStartTime) < CO2_Output);
  digitalWrite(VALVULA_PIN, valveState);

  if (valveState != previousValveState) {
    Serial.println(valveState ? "VALVE_OPEN" : "VALVE_CLOSED");
    previousValveState = valveState;
  }
}


// ==========================================================
// TELEMETRIA JSON
// ==========================================================
void enviarTelemetria() {
  if (millis() - lastTelemetry < telemetryInterval) return;
  lastTelemetry = millis();

  float dutyCycle = (CO2_Output / WindowSize) * 100.0;

  Serial.print("{\"temp\":");
  Serial.print(temperature_read, 2);

  Serial.print(",\"setpoint\":");
  Serial.print(set_temperature, 1);

  Serial.print(",\"pwm\":");
  Serial.print(PID_value);

  Serial.print(",\"co2\":");
  Serial.print(co2ppm, 1);

  Serial.print(",\"co2_set\":");
  Serial.print(CO2_Setpoint, 0);

  Serial.print(",\"co2_duty\":");
  Serial.print(dutyCycle, 1);

  Serial.print(",\"valve\":");
  Serial.print(valveState ? 1 : 0);

  Serial.print(",\"ambient_temp\":");
  Serial.print(ambientTemp, 2);

  Serial.print(",\"humidity\":");
  Serial.print(ambientHum, 2);

  Serial.println("}");
}

```

---

## `codigo/extras/incubadora_temperatura/Control_Incubator_William1/Control_Incubator_William1.ino`

Ruta original en la Pi4: `/home/microscope1/Control_Incubator_William1/Control_Incubator_William1.ino`

```cpp
/*
 * ==========================================================
 * CONTROL PID DOBLE
 * - Temperatura (DS18B20 + PWM calefactor)
 * - CO2 (SCD30 + válvula solenoide)
 * ==========================================================
 */

#include <Wire.h>
#include <Adafruit_SCD30.h>
#include <PID_v1.h>

#include <OneWire.h>
#include <DallasTemperature.h>

// ==========================================================
// SCD30 (CO2)
// ==========================================================

Adafruit_SCD30 scd30;

const int VALVULA_PIN = 9;

double CO2_Setpoint = 5000.0;
double CO2_Input;
double CO2_Output;

double CO2_Kp = 0.08;
double CO2_Ki = 0.02;
double CO2_Kd = 0.00;

PID CO2_PID(
  &CO2_Input,
  &CO2_Output,
  &CO2_Setpoint,
  CO2_Kp,
  CO2_Ki,
  CO2_Kd,
  DIRECT
);

const unsigned long WindowSize = 10000;
unsigned long windowStartTime;

bool valveState = false;
bool previousValveState = false;

// ==========================================================
// DS18B20 (Temperatura)
// ==========================================================

const int oneWirePin = 2;
const int PWM_pin = 3;

OneWire oneWireBus(oneWirePin);
DallasTemperature sensors(&oneWireBus);

float temperature_read = 0.0;
float set_temperature = 37.0;

// PID temperatura

float PID_error = 0;
float previous_error = 0;

float elapsedTime;
float Time;
float timePrev;

float kp = 8.0;
float ki = 0.05;
float kd = 3.0;

float PID_p = 0;
float PID_i = 0;
float PID_d = 0;

int PID_value = 0;

// ==========================================================
// Variables ambientales SCD30
// ==========================================================

float ambientTemp = 0;
float ambientHum  = 0;
float co2ppm      = 0;

// ==========================================================
// Temporizadores
// ==========================================================

unsigned long lastTempRead = 0;
unsigned long lastTelemetry = 0;

const unsigned long tempInterval = 300;
const unsigned long telemetryInterval = 1000;

// ==========================================================

void setup()
{
  Serial.begin(115200);

  // ---------- DS18B20 ----------
  sensors.begin();

  pinMode(PWM_pin, OUTPUT);

  // PWM ~980 Hz
  TCCR2B = TCCR2B & B11111000 | 0x03;

  Time = millis();

  // ---------- SCD30 ----------

  if (!scd30.begin())
  {
    Serial.println("ERROR: SCD30 no encontrado");
    while (1);
  }

  pinMode(VALVULA_PIN, OUTPUT);
  digitalWrite(VALVULA_PIN, LOW);

  CO2_PID.SetOutputLimits(0, WindowSize);
  CO2_PID.SetMode(AUTOMATIC);

  windowStartTime = millis();

  Serial.println("READY");
}

// ==========================================================

void loop()
{
  recibirComandos();

  controlarTemperatura();

  controlarCO2();

  enviarTelemetria();
}

// ==========================================================
// RECEPCION DE COMANDOS
// ==========================================================

void recibirComandos()
{
  if (Serial.available())
  {
    String msg = Serial.readStringUntil('\n');
    msg.trim();

    // SET_TEMP:40.5

    if (msg.startsWith("SET_TEMP:"))
    {
      float new_sp = msg.substring(9).toFloat();

      if (new_sp >= 20.0 && new_sp <= 80.0)
      {
        set_temperature = new_sp;
      }
    }

    // SET_CO2:5000

    if (msg.startsWith("SET_CO2:"))
    {
      float new_sp = msg.substring(8).toFloat();

      if (new_sp >= 400 && new_sp <= 20000)
      {
        CO2_Setpoint = new_sp;
      }
    }
  }
}

// ==========================================================
// PID TEMPERATURA
// ==========================================================

void controlarTemperatura()
{
  if (millis() - lastTempRead < tempInterval)
    return;

  lastTempRead = millis();

  sensors.requestTemperatures();

  temperature_read =
    sensors.getTempCByIndex(0);

  if (temperature_read == DEVICE_DISCONNECTED_C)
    return;

  PID_error = set_temperature - temperature_read;

  timePrev = Time;
  Time = millis();

  elapsedTime =
    (Time - timePrev) / 1000.0;

  PID_p = kp * PID_error;

  PID_i += ki * PID_error * elapsedTime;

  if (PID_i > 255) PID_i = 255;
  if (PID_i < 0) PID_i = 0;

  if (elapsedTime > 0)
  {
    PID_d =
      kd *
      ((PID_error - previous_error)
      / elapsedTime);
  }

  PID_value =
    PID_p + PID_i + PID_d;

  if (PID_value < 0)
    PID_value = 0;

  if (PID_value > 255)
    PID_value = 255;

  analogWrite(PWM_pin, PID_value);

  previous_error = PID_error;
}

// ==========================================================
// PID CO2
// ==========================================================

void controlarCO2()
{
  if (!scd30.dataReady())
    return;

  if (!scd30.read())
    return;

  co2ppm      = scd30.CO2;
  ambientTemp = scd30.temperature;
  ambientHum  = scd30.relative_humidity;

  CO2_Input = co2ppm;

  CO2_PID.Compute();

  unsigned long now = millis();

  if ((now - windowStartTime) > WindowSize)
  {
    windowStartTime += WindowSize;
  }

  valveState =
    ((now - windowStartTime)
     < CO2_Output);

  digitalWrite(
    VALVULA_PIN,
    valveState
  );

  if (valveState != previousValveState)
  {
    if (valveState)
      Serial.println("VALVE_OPEN");
    else
      Serial.println("VALVE_CLOSED");

    previousValveState = valveState;
  }
}

// ==========================================================
// TELEMETRIA JSON
// ==========================================================

void enviarTelemetria()
{
  if (millis() - lastTelemetry
      < telemetryInterval)
    return;

  lastTelemetry = millis();

  float dutyCycle =
    (CO2_Output / WindowSize) * 100.0;

  Serial.print("{");

  Serial.print("\"temp\":");
  Serial.print(temperature_read, 2);

  Serial.print(",\"temp_set\":");
  Serial.print(set_temperature, 1);

  Serial.print(",\"heater_pwm\":");
  Serial.print(PID_value);

  Serial.print(",\"co2\":");
  Serial.print(co2ppm);

  Serial.print(",\"co2_set\":");
  Serial.print(CO2_Setpoint);

  Serial.print(",\"co2_pid\":");
  Serial.print(CO2_Output);

  Serial.print(",\"co2_duty\":");
  Serial.print(dutyCycle);

  Serial.print(",\"valve\":");
  Serial.print(valveState ? 1 : 0);

  Serial.print(",\"ambient_temp\":");
  Serial.print(ambientTemp, 2);

  Serial.print(",\"humidity\":");
  Serial.print(ambientHum, 2);

  Serial.println("}");
}
```

---

## `codigo/extras/incubadora_temperatura/Temperature_PID_DS18B20/Temperature_PID_DS18B20.ino`

Ruta original en la Pi4: `/home/microscope1/Temperature_PID_DS18B20/Temperature_PID_DS18B20.ino`

```cpp
/*
 * =====================================================================
 *  Controlador PID de Temperatura — MicroscopeOS
 * =====================================================================
 *  Lee temperatura con sensor DS18B20 (OneWire).
 *  Controla un calefactor mediante PWM en el pin D3 a través de un
 *  MOSFET de potencia.
 *  Se comunica con la Raspberry Pi por USB/Serial:
 *    - Envía telemetría JSON cada ~300 ms
 *    - Recibe comandos "SET:xx.x\n" para cambiar el setpoint
 *
 *  Conexiones DS18B20:
 *    Datos → D2 (con resistencia pull-up de 4.7kΩ entre datos y VCC)
 *    VCC → 5V | GND → GND
 *  Salida PWM: D3 → compuerta del MOSFET → carga a 12V
 * =====================================================================
 */

#include <OneWire.h>
#include <DallasTemperature.h>

// --- Pin de datos del DS18B20 ---
#define ONEWIRE_PIN 2

// --- Pin PWM para el calefactor ---
int PWM_pin = 3;

OneWire oneWireBus(ONEWIRE_PIN);
DallasTemperature sensors(&oneWireBus);

// --- Variables de temperatura ---
float temperature_read = 0.0;   // Temperatura medida (°C)
float set_temperature  = 37.0;  // Setpoint por defecto (°C)

// --- Variables PID ---
float PID_error      = 0;
float previous_error = 0;
float elapsedTime, Time, timePrev;
int   PID_value      = 0;   // Salida del PID (0–255, va al PWM)

// --- Ganancias del PID ---
float kp = 5.0;    // Proporcional (antes 8.0 — bajado para reducir el empuje inicial)
float ki = 0.02;   // Integral (antes 0.05 — bajado para reducir overshoot por acumulación)
float kd = 3.0;    // Derivativo

float PID_p = 0;
float PID_i = 0;
float PID_d = 0;

// --- Límite de rampa para el PWM ---
// Evita que la salida suba/baje de golpe: por ciclo (300ms), el PWM
// solo puede cambiar como máximo esta cantidad de pasos.
int   PID_value_prev  = 0;
const int MAX_PWM_STEP = 4;   // pasos de PWM permitidos por ciclo (~300ms)


void setup() {
  pinMode(PWM_pin, OUTPUT);

  // Cambia la frecuencia del Timer2 a ~980 Hz para suavizar el PWM
  // en el calefactor (evita ruido audible y mejora respuesta del PID)
  TCCR2B = TCCR2B & B11111000 | 0x03;

  sensors.begin();

  Time = millis();

  Serial.begin(9600);
  Serial.println("READY");   // Señal para que la Pi sepa que el Arduino está listo
}


void loop() {

  // --- Recibir nuevo setpoint desde la Raspberry Pi ---
  // Protocolo: la Pi manda "SET:40.5\n"
  if (Serial.available() > 0) {
    String msg = Serial.readStringUntil('\n');
    msg.trim();
    if (msg.startsWith("SET:")) {
      float new_sp = msg.substring(4).toFloat();
      // Rango seguro: 20–80 °C
      if (new_sp >= 20.0 && new_sp <= 80.0) {
        set_temperature = new_sp;
      }
    }
  }

  // --- Leer temperatura del DS18B20 ---
  sensors.requestTemperatures();
  float t = sensors.getTempCByIndex(0);

  // Si el sensor está desconectado o hay error de lectura, se mantiene
  // el último valor válido y se saltan los cálculos de este ciclo
  if (t == DEVICE_DISCONNECTED_C) {
    delay(300);
    return;
  }
  temperature_read = t;

  // --- Cálculo PID ---
  PID_error = set_temperature - temperature_read;

  // Tiempo transcurrido desde el ciclo anterior (en segundos)
  timePrev    = Time;
  Time        = millis();
  elapsedTime = (Time - timePrev) / 1000.0;

  PID_p = kp * PID_error;

  // Acumulador integral con anti-windup (limita entre 0 y 255)
  PID_i += ki * PID_error * elapsedTime;
  if (PID_i > 255) PID_i = 255;
  if (PID_i < 0)   PID_i = 0;

  if (elapsedTime > 0) {
    PID_d = kd * ((PID_error - previous_error) / elapsedTime);
  }

  // Suma de términos, saturada a rango PWM válido
  PID_value = PID_p + PID_i + PID_d;
  if (PID_value < 0)   PID_value = 0;
  if (PID_value > 255) PID_value = 255;

  // --- Límite de rampa (rate limit) ---
  // Aunque el PID pida un salto grande, el PWM real solo se mueve
  // como máximo MAX_PWM_STEP por ciclo. Esto suaviza la subida inicial
  // y evita el "golpe" de temperatura que luego se pasa y baja.
  if (PID_value > PID_value_prev + MAX_PWM_STEP) {
    PID_value = PID_value_prev + MAX_PWM_STEP;
  } else if (PID_value < PID_value_prev - MAX_PWM_STEP) {
    PID_value = PID_value_prev - MAX_PWM_STEP;
  }
  PID_value_prev = PID_value;

  // Aplicar salida al MOSFET vía PWM
  analogWrite(PWM_pin, (int)PID_value);

  previous_error = PID_error;

  delay(300);   // Periodo de muestreo ~300 ms

  // --- Enviar telemetría JSON a la Raspberry Pi ---
  // Formato: {"temp":36.75,"setpoint":37.0,"pwm":128}
  Serial.print("{\"temp\":");
  Serial.print(temperature_read, 2);
  Serial.print(",\"setpoint\":");
  Serial.print(set_temperature, 1);
  Serial.print(",\"pwm\":");
  Serial.print(PID_value);
  Serial.println("}");
}

```

---

## `codigo/extras/incubadora_temperatura/Temperature_PID_Modulo1_SerialControl/Temperature_PID_Modulo1_SerialControl.ino`

Ruta original en la Pi4: `/home/microscope1/Temperature_PID_Modulo1_SerialControl/Temperature_PID_Modulo1_SerialControl.ino`

```cpp
#include <SPI.h>
#define MAX6675_CS   10
#define MAX6675_SO   12
#define MAX6675_SCK  13

int PWM_pin = 3;

float temperature_read = 0.0;
float set_temperature = 37.0;
float PID_error = 0;
float previous_error = 0;
float elapsedTime, Time, timePrev;
int PID_value = 0;

float kp = 8.0;
float ki = 0.05;
float kd = 3.0;
float PID_p = 0;
float PID_i = 0;
float PID_d = 0;

void setup() {
  pinMode(PWM_pin, OUTPUT);
  TCCR2B = TCCR2B & B11111000 | 0x03;
  Time = millis();
  Serial.begin(9600);
  Serial.println("READY");
}

void loop() {
  if (Serial.available() > 0) {
    String msg = Serial.readStringUntil('\n');
    msg.trim();
    if (msg.startsWith("SET:")) {
      float new_sp = msg.substring(4).toFloat();
      if (new_sp >= 20.0 && new_sp <= 80.0) {
        set_temperature = new_sp;
      }
    }
  }

  temperature_read = readThermocouple();

  PID_error = set_temperature - temperature_read;
  timePrev = Time;
  Time = millis();
  elapsedTime = (Time - timePrev) / 1000.0;

  PID_p = kp * PID_error;
  PID_i += ki * PID_error * elapsedTime;
  if (PID_i > 255) PID_i = 255;
  if (PID_i < 0)   PID_i = 0;
  PID_d = kd * ((PID_error - previous_error) / elapsedTime);

  PID_value = PID_p + PID_i + PID_d;
  if (PID_value < 0)   PID_value = 0;
  if (PID_value > 255) PID_value = 255;

  analogWrite(PWM_pin, (int)PID_value);
  previous_error = PID_error;

  delay(300);

  Serial.print("{\"temp\":");
  Serial.print(temperature_read, 2);
  Serial.print(",\"setpoint\":");
  Serial.print(set_temperature, 1);
  Serial.print(",\"pwm\":");
  Serial.print(PID_value);
  Serial.println("}");
}

double readThermocouple() {
  uint16_t v;
  pinMode(MAX6675_CS, OUTPUT);
  pinMode(MAX6675_SO, INPUT);
  pinMode(MAX6675_SCK, OUTPUT);

  digitalWrite(MAX6675_CS, LOW);
  delayMicroseconds(10);
  v = shiftIn(MAX6675_SO, MAX6675_SCK, MSBFIRST);
  v <<= 8;
  v |= shiftIn(MAX6675_SO, MAX6675_SCK, MSBFIRST);
  digitalWrite(MAX6675_CS, HIGH);

  if (v & 0x4) return NAN;
  v >>= 3;
  return v * 0.25 - 1.0;
}

```

---

## `configs/boot/cmdline.txt`

Ruta original en la Pi4: `/boot/firmware (particion bootfs)/cmdline.txt`

```text
console=serial0,115200 console=tty1 root=PARTUUID=957c2719-02 rootfstype=ext4 fsck.repair=yes rootwait quiet splash plymouth.ignore-serial-consoles cfg80211.ieee80211_regdom=MX
```

---

## `configs/boot/config.txt`

Ruta original en la Pi4: `/boot/firmware (particion bootfs)/config.txt`

```text
# For more options and information see
# http://rptl.io/configtxt
# Some settings may impact device functionality. See link above for details

# Uncomment some or all of these to enable the optional hardware interfaces
#dtparam=i2c_arm=on
#dtparam=i2s=on
#dtparam=spi=on

# Enable audio (loads snd_bcm2835)
dtparam=audio=on

# Additional overlays and parameters are documented
# /boot/firmware/overlays/README

# Automatically load overlays for detected cameras
camera_auto_detect=0

# Automatically load overlays for detected DSI displays
display_auto_detect=1

# Automatically load initramfs files, if found
auto_initramfs=1

# Enable DRM VC4 V3D driver
dtoverlay=vc4-kms-v3d
max_framebuffers=2

# Don't have the firmware create an initial video= setting in cmdline.txt.
# Use the kernel's default instead.
disable_fw_kms_setup=1

# Run in 64-bit mode
arm_64bit=1

# Disable compensation for displays with overscan
disable_overscan=1

# Run as fast as firmware / board allows
arm_boost=1

[cm4]
# Enable host mode on the 2711 built-in XHCI USB controller.
# This line should be removed if the legacy DWC2 controller is required
# (e.g. for USB device mode) or if USB support is not required.
otg_mode=1

[cm5]
dtoverlay=dwc2,dr_mode=host

[all]
enable_uart=1
dtoverlay=camera-mux-4port,cam0-imx219,cam1-imx219

```

---

## `configs/lightdm/lightdm.conf`

Ruta original en la Pi4: `/etc/lightdm/lightdm.conf`

```ini
#
# General configuration
#
# start-default-seat = True to always start one seat if none are defined in the configuration
# greeter-user = User to run greeter as
# minimum-display-number = Minimum display number to use for X servers
# minimum-vt = First VT to run displays on
# lock-memory = True to prevent memory from being paged to disk
# user-authority-in-system-dir = True if session authority should be in the system location
# guest-account-script = Script to be run to setup guest account
# logind-check-graphical = True to on start seats that are marked as graphical by logind
# log-directory = Directory to log information to
# run-directory = Directory to put running state in
# cache-directory = Directory to cache to
# sessions-directory = Directory to find sessions
# remote-sessions-directory = Directory to find remote sessions
# greeters-directory = Directory to find greeters
# backup-logs = True to move add a .old suffix to old log files when opening new ones
# dbus-service = True if LightDM provides a D-Bus service to control it
#
[LightDM]
#start-default-seat=true
#greeter-user=lightdm
#minimum-display-number=0
#minimum-vt=7
#lock-memory=true
#user-authority-in-system-dir=false
#guest-account-script=guest-account
#logind-check-graphical=true
#log-directory=/var/log/lightdm
#run-directory=/var/run/lightdm
#cache-directory=/var/cache/lightdm
#sessions-directory=/usr/share/lightdm/sessions:/usr/share/xsessions:/usr/share/wayland-sessions
#remote-sessions-directory=/usr/share/lightdm/remote-sessions
#greeters-directory=$XDG_DATA_DIRS/lightdm/greeters:$XDG_DATA_DIRS/xgreeters
#backup-logs=true
#dbus-service=true

#
# Seat configuration
#
# Seat configuration is matched against the seat name glob in the section, for example:
# [Seat:*] matches all seats and is applied first.
# [Seat:seat0] matches the seat named "seat0".
# [Seat:seat-thin-client*] matches all seats that have names that start with "seat-thin-client".
#
# type = Seat type (local, xremote)
# pam-service = PAM service to use for login
# pam-autologin-service = PAM service to use for autologin
# pam-greeter-service = PAM service to use for greeters
# xserver-command = X server command to run (can also contain arguments e.g. X -special-option)
# xmir-command = Xmir server command to run (can also contain arguments e.g. Xmir -special-option)
# xserver-config = Config file to pass to X server
# xserver-layout = Layout to pass to X server
# xserver-allow-tcp = True if TCP/IP connections are allowed to this X server
# xserver-share = True if the X server is shared for both greeter and session
# xserver-hostname = Hostname of X server (only for type=xremote)
# xserver-display-number = Display number of X server (only for type=xremote)
# xdmcp-manager = XDMCP manager to connect to (implies xserver-allow-tcp=true)
# xdmcp-port = XDMCP UDP/IP port to communicate on
# xdmcp-key = Authentication key to use for XDM-AUTHENTICATION-1 (stored in keys.conf)
# greeter-session = Session to load for greeter
# greeter-hide-users = True to hide the user list
# greeter-allow-guest = True if the greeter should show a guest login option
# greeter-show-manual-login = True if the greeter should offer a manual login option
# greeter-show-remote-login = True if the greeter should offer a remote login option
# user-session = Session to load for users
# allow-user-switching = True if allowed to switch users
# allow-guest = True if guest login is allowed
# guest-session = Session to load for guests (overrides user-session)
# session-wrapper = Wrapper script to run session with
# greeter-wrapper = Wrapper script to run greeter with
# guest-wrapper = Wrapper script to run guest sessions with
# display-setup-script = Script to run when starting a greeter session (runs as root)
# display-stopped-script = Script to run after stopping the display server (runs as root)
# greeter-setup-script = Script to run when starting a greeter (runs as root)
# session-setup-script = Script to run when starting a user session (runs as root)
# session-cleanup-script = Script to run when quitting a user session (runs as root)
# autologin-guest = True to log in as guest by default
# autologin-user = User to log in with by default (overrides autologin-guest)
# autologin-user-timeout = Number of seconds to wait before loading default user
# autologin-session = Session to load for automatic login (overrides user-session)
# autologin-in-background = True if autologin session should not be immediately activated
# exit-on-failure = True if the daemon should exit if this seat fails
#
[Seat:*]
#type=local
#pam-service=lightdm
#pam-autologin-service=lightdm-autologin
#pam-greeter-service=lightdm-greeter
#xserver-command=X
#xmir-command=Xmir
#xserver-config=
#xserver-layout=
#xserver-allow-tcp=false
#xserver-share=true
#xserver-hostname=
#xserver-display-number=
#xdmcp-manager=
#xdmcp-port=177
#xdmcp-key=
greeter-session=pi-greeter-labwc
greeter-hide-users=false
#greeter-allow-guest=true
#greeter-show-manual-login=false
#greeter-show-remote-login=true
user-session=rpd-labwc
#allow-user-switching=true
#allow-guest=true
#guest-session=
#session-wrapper=lightdm-session
#greeter-wrapper=
#guest-wrapper=
display-setup-script=/usr/share/dispsetup.sh
#display-stopped-script=
#greeter-setup-script=
#session-setup-script=
#session-cleanup-script=
#autologin-guest=false
autologin-user=microscope1
#autologin-user-timeout=0
#autologin-in-background=false
autologin-session=rpd-labwc
#exit-on-failure=false

#
# XDMCP Server configuration
#
# enabled = True if XDMCP connections should be allowed
# port = UDP/IP port to listen for connections on
# listen-address = Host/address to listen for XDMCP connections (use all addresses if not present)
# key = Authentication key to use for XDM-AUTHENTICATION-1 or blank to not use authentication (stored in keys.conf)
# hostname = Hostname to report to XDMCP clients (defaults to system hostname if unset)
#
# The authentication key is a 56 bit DES key specified in hex as 0xnnnnnnnnnnnnnn.  Alternatively
# it can be a word and the first 7 characters are used as the key.
#
[XDMCPServer]
#enabled=false
#port=177
#listen-address=
#key=
#hostname=

#
# VNC Server configuration
#
# enabled = True if VNC connections should be allowed
# command = Command to run Xvnc server with
# port = TCP/IP port to listen for connections on
# listen-address = Host/address to listen for VNC connections (use all addresses if not present)
# width = Width of display to use
# height = Height of display to use
# depth = Color depth of display to use
#
[VNCServer]
#enabled=false
#command=Xvnc
#port=5900
#listen-address=
#width=1024
#height=768
#depth=8

```

---

## `configs/modules`

Ruta original en la Pi4: `/etc/modules`

```text
# /etc/modules is obsolete and has been replaced by /etc/modules-load.d/.
# Please see modules-load.d(5) and modprobe.d(5) for details.
#
# Updating this file still works, but it is undocumented and unsupported.
i2c-dev

```

---

## `configs/modules-load.d/modules.conf`

Ruta original en la Pi4: `/etc/modules-load.d/modules.conf`

```ini
# /etc/modules is obsolete and has been replaced by /etc/modules-load.d/.
# Please see modules-load.d(5) and modprobe.d(5) for details.
#
# Updating this file still works, but it is undocumented and unsupported.
i2c-dev

```

---

## `configs/systemd/getty@tty1.service.d/autologin.conf`

Ruta original en la Pi4: `/etc/systemd/system/getty@tty1.service.d/autologin.conf`

```ini
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin microscope1 --noclear %I $TERM

```

---

## `configs/systemd/microscopeos.service`

Ruta original en la Pi4: `/etc/systemd/system/microscopeos.service`

```ini
[Unit]
Description=MicroscopeOS Web Server
After=network.target

[Service]
Type=simple
User=microscope1
WorkingDirectory=/home/microscope1/MicroscopeOS
ExecStart=/home/microscope1/MicroscopeOS/venv/bin/python3 /home/microscope1/MicroscopeOS/run_web.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target

```

---

## `configs/systemd/microscope.service`

Ruta original en la Pi4: `/etc/systemd/system/microscope.service`

```ini
[Unit]
Description=MicroscopeOS Service
After=graphical.target
Wants=graphical.target

[Service]
Type=simple
User=microscope1
WorkingDirectory=/home/microscope1/MicroscopeOS
ExecStart=/home/microscope1/MicroscopeOS/start_microscope.sh
Restart=always
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/microscope1/.Xauthority

[Install]
WantedBy=graphical.target

```

---

## `configs/udev/99-matrices.rules`

Ruta original en la Pi4: `/etc/udev/rules.d/99-matrices.rules`

```ini
SUBSYSTEM=="tty", ATTRS{idVendor}=="2e8a", ATTRS{serial}=="e663b03597616739", SYMLINK+="matriz_cam0"
SUBSYSTEM=="tty", ATTRS{idVendor}=="2e8a", ATTRS{serial}=="e663b03597762a39", SYMLINK+="matriz_cam1"

```

---

## `configs/udev/99-rpi-keyboard.rules`

Ruta original en la Pi4: `/etc/udev/rules.d/99-rpi-keyboard.rules`

```ini
# udev rules for RPi Keyboards

# RPi 500 Keyboard
KERNEL=="hidraw*", ATTRS{idVendor}=="2e8a", ATTRS{idProduct}=="0010", MODE="0660", GROUP="plugdev", TAG+="uaccess", TAG+="udev-acl"

# RPi 500+ Keyboard  
KERNEL=="hidraw*", ATTRS{idVendor}=="2e8a", ATTRS{idProduct}=="0011", MODE="0660", GROUP="plugdev", TAG+="uaccess", TAG+="udev-acl"
```

