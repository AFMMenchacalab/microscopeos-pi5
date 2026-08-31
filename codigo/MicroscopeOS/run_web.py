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

# Una matriz por camara, identificadas por udev (nombres fijos).
# Placas Waveshare ESP32-S3-Matrix (8x8), antes RP2040 (5x5).
# Los symlinks no cambian; la regla udev si (VID:PID 303a:1001).
print("Conectando matrices de iluminacion...")
luz_cam0 = IlluminationController(port="/dev/matriz_cam0")
luz_cam1 = IlluminationController(port="/dev/matriz_cam1")
illuminations = {0: luz_cam0, 1: luz_cam1}

# Brillo inicial en ambas.
# TODO-HW (prioridad 1): 80% = 204/255 en la escala del firmware. Con las
# matrices de 8x8 alimentadas por USB, el README de la placa recomienda no
# pasar de ~46 en FULL (~18%) ni ~92 en medios patrones (~36%): FULL:255
# son ~3.8 A y un puerto USB da 0.5-0.9 A. En las RP2040 de 5x5 este 80%
# era inofensivo. Se deja el valor de Pi 4 para NO alterar la exposicion
# de las capturas sin medir antes; medir consumo y ajustar.
luz_cam0.set_brightness(80)
luz_cam1.set_brightness(80)

timelapse = TimelapseManager(camera, illuminations)

app = create_app(camera, illuminations, timelapse)
print("Servidor en http://0.0.0.0:8000")
uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
