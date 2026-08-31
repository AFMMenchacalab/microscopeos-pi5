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
