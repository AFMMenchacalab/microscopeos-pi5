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

