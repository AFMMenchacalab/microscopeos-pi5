import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.camera import CameraController
from core.illumination import IlluminationController
from core.timelapse import TimelapseManager

print("Inicializando hardware...")
camera = CameraController()
camera.set_exposure(12000, 1.2)

# TimelapseManager indexa por camara: {0: ..., 1: ...}
illuminations = {
    0: IlluminationController(port="/dev/matriz_cam0"),
    1: IlluminationController(port="/dev/matriz_cam1"),
}
for luz in illuminations.values():
    # TODO-HW: 100% = 255/255. Con las matrices 8x8 por USB eso son ~3.8 A.
    # Bajar antes de dejarlo corriendo sin supervision. Ver TODO_HW.md.
    luz.set_brightness(100)

timelapse = TimelapseManager(camera, illuminations)

print("Arrancando timelapse de prueba (blanco, 2 camaras, cada 10s, 40s total)...")
timelapse.start(
    modo="dpc",
    interval_seconds=60,
    duration_seconds=120,
    stabilization_time=0.3,
    camaras=[0, 1],
    simultaneo=False   # True = las dos camaras a la vez (ver TODO_HW.md)
)

# Esperar a que termine
while timelapse.is_running():
    time.sleep(1)

print("\nTimelapse terminado. Limpiando...")
for luz in illuminations.values():
    luz.close()
camera.stop()
print("Listo. Revisa la carpeta timelapse_* que se creo.")

