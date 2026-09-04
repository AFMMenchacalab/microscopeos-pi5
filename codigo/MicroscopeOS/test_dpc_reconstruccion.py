"""Prueba de captura DPC (cam0, un solo ciclo) + reconstruccion.

No existia todavia un algoritmo que combine las 4 capturas (L/R/T/B) en
una imagen de contraste de fase -- MODOS["dpc"] en core/timelapse.py solo
las captura por separado. Este script hace las dos cosas para poder ver
si el resultado tiene sentido antes de meterlo al pipeline real.

DPC clasico (par opuesto de iluminacion oblicua): la diferencia
normalizada (L-R)/(L+R) resalta los bordes/gradientes de indice de
refraccion en el eje izquierda-derecha; (T-B)/(T+B) hace lo mismo en el
eje arriba-abajo. Una region uniforme (sin gradiente de fase) da ~0 en
ambos ejes -> gris parejo. Esto es DPC *cualitativo*: no corrige por
la funcion de transferencia optica del objetivo (para eso hace falta su
NA y la geometria real del cono de iluminacion), pero ya deberia mostrar
si el motor optico works: bordes marcados en direcciones opuestas al
espejar L<->R o T<->B.
"""
import os
import sys
from datetime import datetime

import numpy as np
import tifffile
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.camera import CameraController
from core.illumination import IlluminationController
from core.timelapse import TimelapseManager, MODOS

CARPETA = "dpc_prueba"


def _normalizada(a, b):
    """(a-b)/(a+b) en float, con un epsilon para no dividir por 0 en
    pixeles negros. Recorta a [-1, 1] por si algun pixel esta saturado
    o muy desbalanceado entre las dos iluminaciones."""
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    eps = 1.0
    dpc = (a - b) / (a + b + eps)
    return np.clip(dpc, -1.0, 1.0)


def _a_png(dpc, ruta):
    """Mapea [-1,1] -> [0,255] con 0 (sin gradiente) en gris medio (128)."""
    img8 = ((dpc * 0.5 + 0.5) * 255).astype(np.uint8)
    Image.fromarray(img8).save(ruta)


def main():
    print("Inicializando camara e iluminacion (solo cam0)...")
    camera = CameraController(camera_nums=(0,))
    camera.set_exposure(12000, 1.2)

    # max_value=46: mismo tope de corriente que el resto del proyecto
    # (alimentacion USB, ver README de la matriz / TODO_HW.md).
    luz0 = IlluminationController(port="/dev/matriz_cam0", max_value=46)
    luz0.set_brightness(5)

    timelapse = TimelapseManager(camera, {0: luz0})
    timelapse.base_folder = CARPETA
    os.makedirs(os.path.join(CARPETA, "cam0"), exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    print("Capturando ciclo DPC (L/R/T/B) en cam0...")
    try:
        timelapse._capturar_secuencial(
            MODOS["dpc"], camaras=[0], ts=ts, stabilization_time=0.3)
    finally:
        luz0.close()
        camera.stop()

    print("Reconstruyendo...")
    cam_folder = os.path.join(CARPETA, "cam0")
    L = tifffile.imread(os.path.join(cam_folder, f"img_{ts}_L.tif"))
    R = tifffile.imread(os.path.join(cam_folder, f"img_{ts}_R.tif"))
    T = tifffile.imread(os.path.join(cam_folder, f"img_{ts}_T.tif"))
    B = tifffile.imread(os.path.join(cam_folder, f"img_{ts}_B.tif"))

    for nombre, img in (("L", L), ("R", R), ("T", T), ("B", B)):
        print(f"  {nombre}: shape={img.shape} dtype={img.dtype} "
              f"promedio={img.mean():.1f}")

    dpc_lr = _normalizada(L, R)
    dpc_tb = _normalizada(T, B)

    _a_png(dpc_lr, os.path.join(CARPETA, f"dpc_LR_{ts}.png"))
    _a_png(dpc_tb, os.path.join(CARPETA, f"dpc_TB_{ts}.png"))

    # Composite rapido para ver los dos ejes de una: R=eje LR, G=eje TB,
    # B fijo en gris medio. Es solo para inspeccion visual, no un DPC
    # cuantitativo a color.
    compuesto = np.zeros((*dpc_lr.shape, 3), dtype=np.uint8)
    compuesto[..., 0] = ((dpc_lr * 0.5 + 0.5) * 255).astype(np.uint8)
    compuesto[..., 1] = ((dpc_tb * 0.5 + 0.5) * 255).astype(np.uint8)
    compuesto[..., 2] = 128
    Image.fromarray(compuesto).save(os.path.join(CARPETA, f"dpc_compuesto_{ts}.png"))

    print(f"\nListo. Revisa en {CARPETA}/:")
    print(f"  dpc_LR_{ts}.png, dpc_TB_{ts}.png, dpc_compuesto_{ts}.png")
    print("  Gris parejo (~128) = sin gradiente de fase ahi.")
    print("  Si se ven bordes marcados con un lado claro/oscuro invertido")
    print("  entre L y R (o T y B), el DPC esta funcionando.")


if __name__ == "__main__":
    main()
