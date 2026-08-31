#!/usr/bin/env python3
"""Preflight de la migracion a Pi 5. Ejecutar EN LA PI, con el hardware puesto.

    cd ~/MicroscopeOS && source venv/bin/activate && python3 verificar_pi5.py

Recorre en orden las validaciones de TODO_HW.md que no se pudieron hacer sin
hardware, y dice cual falla. No modifica nada: solo lee y captura una imagen
de prueba en /tmp.

Codigo de salida 0 si todo lo critico pasa.
"""
import os
import subprocess
import sys
import glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CRITICO, AVISO, INFO = "CRITICO", "AVISO", "INFO"
resultados = []


def check(nombre, nivel, ref=""):
    def deco(fn):
        etiqueta = f"{nombre}" + (f"  [{ref}]" if ref else "")
        try:
            ok, detalle = fn()
        except Exception as e:
            ok, detalle = False, f"{type(e).__name__}: {e}"
        marca = "OK  " if ok else ("FALLA" if nivel == CRITICO else "REVISA")
        print(f"  {marca:6} {etiqueta}")
        if detalle:
            for linea in str(detalle).splitlines():
                print(f"           {linea}")
        resultados.append((nombre, nivel, ok, detalle))
        return fn
    return deco


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()


print("\n=== 1. Plataforma y paquetes ===")

@check("Es una Raspberry Pi 5", CRITICO)
def _():
    modelo = open("/proc/device-tree/model").read().strip("\x00")
    return "Pi 5" in modelo, modelo

@check("rpi-lgpio instalado (no la RPi.GPIO clasica)", CRITICO, "TODO_HW 3.4")
def _():
    import RPi.GPIO as GPIO
    ruta = getattr(GPIO, "__file__", "?")
    # rpi-lgpio se apoya en lgpio; la clasica no lo importa
    import importlib
    tiene_lgpio = importlib.util.find_spec("lgpio") is not None
    return tiene_lgpio, f"RPi.GPIO desde {ruta} | lgpio presente: {tiene_lgpio}"

@check("venv ve los paquetes del sistema", CRITICO)
def _():
    import picamera2, cv2, numpy, serial, tifffile
    return True, (f"picamera2 {getattr(picamera2,'__version__','?')} | "
                  f"cv2 {cv2.__version__} | numpy {numpy.__version__}")


print("\n=== 2. Camaras sin multiplexor ===")

@check("Las dos camaras detectadas", CRITICO, "TODO_HW 3.1")
def _():
    from picamera2 import Picamera2
    info = Picamera2.global_camera_info()
    detalle = "\n".join(f"cam{i}: {c.get('Model')} @ {c.get('Id')}"
                        for i, c in enumerate(info))
    return len(info) >= 2, detalle or "ninguna camara"

@check("Overlay del mux ausente", AVISO, "TODO_HW 3.2")
def _():
    cfg = ""
    for p in ("/boot/firmware/config.txt", "/boot/config.txt"):
        if os.path.exists(p):
            cfg = open(p).read()
            break
    return "camera-mux-4port" not in cfg, \
        "sigue el dtoverlay del mux en config.txt" if "camera-mux-4port" in cfg else ""

@check("GPIO14/15 libres (UART fuera)", AVISO, "TODO_HW 3.3")
def _():
    salida = sh("pinctrl get 14,15") or sh("raspi-gpio get 14,15")
    ocupados = "uart" in salida.lower() or "a0" in salida.lower()
    return not ocupados, salida or "pinctrl no disponible"


print("\n=== 3. Captura RAW bajo PiSP  <-- el paso critico ===")

@check("Formato raw y debayer producen imagen valida", CRITICO, "TODO_HW 1.1 y 1.2")
def _():
    import numpy as np
    from core.camera import CameraController
    cam = CameraController()
    try:
        ruta = cam.capture_image(0, folder="/tmp", filename="/tmp/verif_cam0.tif")
        import tifffile
        img = tifffile.imread(ruta)
        problemas = []
        if img.ndim != 2:
            problemas.append(f"no es 2D: shape={img.shape} (el debayer fallo)")
        if img.dtype != np.uint16:
            problemas.append(f"dtype={img.dtype}, se esperaba uint16")
        if img.shape != (2464, 3280):
            problemas.append(f"shape={img.shape}, se esperaba (2464, 3280)")
        if img.max() == img.min():
            problemas.append("imagen uniforme: sin senal")
        elif img.max() == 0:
            problemas.append("imagen toda en negro")
        detalle = (f"shape={img.shape} dtype={img.dtype} "
                   f"min/max={img.min()}/{img.max()} media={img.mean():.1f}")
        if problemas:
            detalle += "\n-> " + "\n-> ".join(problemas)
            detalle += ("\n-> NO ajustes el codigo a ciegas. Imprime primero "
                        "raw.shape/.dtype y sensor_modes. Ver TODO_HW.md 1.1")
        return not problemas, detalle
    finally:
        cam.stop()

@check("Modo raw que ofrece el sensor", INFO, "TODO_HW 1.1")
def _():
    from picamera2 import Picamera2
    p = Picamera2(camera_num=0)
    try:
        modos = {str(m.get("format")) for m in p.sensor_modes}
        empaquetado = any("CSI2P" in m or "_P" in m for m in modos)
        detalle = " | ".join(sorted(modos))
        if empaquetado:
            detalle += ("\n-> hay formatos empaquetados: si make_array('raw') "
                        "devuelve uno, .view(np.uint16) da basura")
        return True, detalle
    finally:
        p.close()


print("\n=== 4. Matrices ESP32-S3 ===")

@check("Symlinks udev presentes", CRITICO, "TODO_HW 4.1")
def _():
    enlaces = sorted(glob.glob("/dev/matriz_cam*"))
    return len(enlaces) >= 2, " | ".join(
        f"{e} -> {os.path.realpath(e)}" for e in enlaces) or "ninguno"

@check("Permisos de acceso al puerto", CRITICO, "TODO_HW 4.2")
def _():
    faltan = [e for e in sorted(glob.glob("/dev/matriz_cam*"))
              if not os.access(e, os.R_OK | os.W_OK)]
    grupos = sh("id -nG")
    return not faltan, (f"sin acceso a {faltan}\n-> sudo usermod -aG uucp $USER "
                        f"y volver a entrar\ngrupos actuales: {grupos}"
                        if faltan else f"grupos: {grupos}")

@check("Las dos placas responden y llevan su calibracion", CRITICO, "TODO_HW 1.4")
def _():
    from core.illumination import IlluminationController
    esperado = {
        "/dev/matriz_cam0": "A0:F2:62:EB:21:A4",
        "/dev/matriz_cam1": "A0:F2:62:EB:2B:48",
    }
    lineas, ok = [], True
    for puerto, mac in esperado.items():
        if not os.path.exists(puerto):
            lineas.append(f"{puerto}: NO EXISTE"); ok = False; continue
        luz = IlluminationController(port=puerto, max_value=40)
        try:
            ident = luz.id()
            lineas.append(f"{puerto}: {ident}")
            if mac not in ident:
                lineas.append(f"  -> esperaba {mac}: placas intercambiadas. "
                              f"Corrige la regla udev, NO el codigo.")
                ok = False
        finally:
            luz.close()
    return ok, "\n".join(lineas)

@check("Encendido de prueba a brillo seguro", AVISO, "TODO_HW 1.3")
def _():
    import time
    from core.illumination import IlluminationController
    # max_value=40 esta por debajo del ~46 que el README marca como techo USB
    luz = IlluminationController(port="/dev/matriz_cam0", max_value=40)
    try:
        luz.set_brightness(100)
        for metodo in ("on", "left", "right", "top", "bottom"):
            getattr(luz, metodo)()
            time.sleep(0.4)
            if luz.last_error:
                return False, f"{metodo}: {luz.last_error}"
        luz.off()
        return True, ("los 5 patrones respondieron OK a brillo 40/255.\n"
                      "-> Mira la matriz: LEFT debe iluminar la mitad izquierda "
                      "y TOP la superior. Si no, recalibra ROT/FLIP (README del "
                      "firmware), no el codigo Python.")
    finally:
        luz.close()


print("\n" + "=" * 60)
criticos = [r for r in resultados if r[1] == CRITICO and not r[2]]
avisos = [r for r in resultados if r[1] == AVISO and not r[2]]
print(f"criticos fallando: {len(criticos)}   avisos: {len(avisos)}")
for nombre, _, _, _ in criticos:
    print(f"  FALLA  {nombre}")
for nombre, _, _, _ in avisos:
    print(f"  REVISA {nombre}")
if not criticos and not avisos:
    print("Todo lo verificable automaticamente pasa.")
print("\nQueda por validar A MANO (no se puede automatizar):")
print("  - TODO_HW 2.1 diafonia optica, antes de usar simultaneo=True")
print("  - TODO_HW 1.2 orden Bayer, comparando con una captura buena de Pi 4")
print("  - TODO_HW 1.3 consumo real de las matrices con un amperimetro")
print("=" * 60)
sys.exit(1 if criticos else 0)
