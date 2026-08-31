import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.camera import CameraController
from core.illumination import IlluminationController
from core.timelapse import TimelapseManager

print("Inicializando hardware...")
camera = CameraController()
camera.set_exposure(12000, 1.2)

# TimelapseManager indexa por camara: {0: ..., 1: ...}
# max_value=46: tope de corriente para alimentacion USB (ver README de la
# matriz, seccion "Potencia" -- FULL:255 son ~3.8A y un puerto USB da
# 0.5-0.9A). Antes esto quedaba en 255 sin tope y set_brightness(100) abajo
# lo mandaba a FULL:255 sin supervision.
illuminations = {
    0: IlluminationController(port="/dev/matriz_cam0", max_value=46),
    1: IlluminationController(port="/dev/matriz_cam1", max_value=46),
}
for luz in illuminations.values():
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

