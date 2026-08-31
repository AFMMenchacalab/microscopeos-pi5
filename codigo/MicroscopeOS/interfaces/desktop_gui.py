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

