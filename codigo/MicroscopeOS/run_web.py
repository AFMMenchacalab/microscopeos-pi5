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
# max_value=46: tope de corriente para alimentacion USB (README de la
# matriz, "Potencia" -- FULL:255 son ~3.8A, un puerto USB da 0.5-0.9A).
# Resuelto 2026-08-31: este era el TODO-HW de prioridad 1 de mas abajo.
# Con las RP2040 de 5x5 el 80% de brillo era inofensivo; con las 8x8
# actuales no lo es, asi que ahora el tope vive en max_value, no en el
# porcentaje -- set_brightness(80) sigue pidiendo "80%" pero _valor()
# lo clampea a 46 igual que en verificar_pi5.py.
luz_cam0 = IlluminationController(port="/dev/matriz_cam0", max_value=46)
luz_cam1 = IlluminationController(port="/dev/matriz_cam1", max_value=46)
illuminations = {0: luz_cam0, 1: luz_cam1}

luz_cam0.set_brightness(80)
luz_cam1.set_brightness(80)

timelapse = TimelapseManager(camera, illuminations)

app = create_app(camera, illuminations, timelapse)
print("Servidor en http://0.0.0.0:8000")
uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
