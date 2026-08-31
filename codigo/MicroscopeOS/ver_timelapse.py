import sys, os, glob
import tifffile, cv2, numpy as np

# Buscar la carpeta de timelapse mas reciente
carpetas = sorted(glob.glob("timelapse_2026*"), key=os.path.getmtime)
if not carpetas:
    print("No se encontraron carpetas de timelapse.")
    sys.exit()

base = carpetas[-1]
print(f"Convirtiendo: {base}")

salida = os.path.join(base, "previews")
os.makedirs(salida, exist_ok=True)

tifs = glob.glob(os.path.join(base, "cam*", "*.tif"))
print(f"Encontradas {len(tifs)} imagenes")

for tif in sorted(tifs):
    img = tifffile.imread(tif)
    norm = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    # Nombre: cam0_img_xxxx.png
    cam = os.path.basename(os.path.dirname(tif))
    nombre = cam + "_" + os.path.basename(tif).replace(".tif", ".png")
    cv2.imwrite(os.path.join(salida, nombre), norm)

print(f"Listo. PNGs en: {salida}")
