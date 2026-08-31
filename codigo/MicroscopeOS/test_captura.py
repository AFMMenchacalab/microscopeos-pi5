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
