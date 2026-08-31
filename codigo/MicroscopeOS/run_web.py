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
