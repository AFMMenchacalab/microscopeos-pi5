"""Guardado de archivos: experimentos, metadatos, optica y marca de agua.

Corre sin hardware: camara con el Picamera2 emulado y matrices falsas.
La API se prueba de punta a punta con el TestClient de FastAPI.
"""
import io
import json
import os
import sys
import tempfile
import time
import types
import zipfile
from datetime import datetime
from pathlib import Path

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
sys.path.insert(0, os.path.join(AQUI, "..", "codigo", "MicroscopeOS"))

import numpy as np
import tifffile
import emuladores

# Solo la camara emulada: aca hace falta el tifffile de verdad.
_m = types.ModuleType("picamera2"); _m.Picamera2 = emuladores.Picamera2Fake
sys.modules["picamera2"] = _m

from core import experimentos as E
from core import metadatos as M
from core import marca_agua as MA
from core import optica as O

ok = fail = 0
def check(nombre, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print(f"  PASS  {nombre}")
    else:    fail += 1; print(f"  FAIL  {nombre}  {extra}")

tmp = Path(tempfile.mkdtemp(prefix="archivos_"))

print("\n=== NOMBRES ===")
check("slug quita acentos y espacios", E.slug("Células día 1") == "Celulas_dia_1", E.slug("Células día 1"))
check("slug no deja pasar barras ni puntos", "/" not in E.slug("../../etc/x") and "." not in E.slug("a.b"))
t = datetime(2026, 10, 2, 10, 30, 5)
check("foto de timelapse: numero, fecha y hora", E.nombre_foto(t, 7, "_L") == "0007_2026-10-02_10-30-05_L.tif")
check("foto suelta: fecha y hora", E.nombre_foto(t) == "2026-10-02_10-30-05.tif")

print("\n=== EXPERIMENTOS ===")
ex = E.Experimentos(raiz=tmp / "datos", legado=tmp)
c1 = ex.crear_timelapse("Células día 1", {"intervalo_s": 300, "duracion_s": 3600}, ahora=t)
check("carpeta con fecha, hora y nombre", c1.name == "2026-10-02_1030_Celulas_dia_1", c1.name)
check("experimento.json con el nombre tal cual", json.loads((c1 / "experimento.json").read_text())["nombre"] == "Células día 1")
leeme = (c1 / "LEEME.txt").read_text()
check("LEEME.txt explica la carpeta", "Células día 1" in leeme and "cam0/" in leeme and "Fiji" in leeme)
c2 = ex.crear_timelapse("Células día 1", ahora=t)
check("mismo nombre y minuto: no pisa, agrega _2", c2.name.endswith("_2"), c2.name)
sin = ex.crear_timelapse("", ahora=datetime(2026, 10, 3, 9, 0))
check("sin nombre: «Timelapse» y un nombre legible", sin.name == "2026-10-03_0900_Timelapse"
      and ex.info(sin)["nombre"].startswith("Timelapse del 3 de octubre"))
f1 = ex.carpeta_fotos("", ahora=t)
f2 = ex.carpeta_fotos(None, ahora=datetime(2026, 10, 2, 18, 0))
check("fotos sueltas del mismo dia van juntas", f1 == f2 and f1.name == "2026-10-02_Fotos_sueltas")
fb = ex.carpeta_fotos("Muestra B", ahora=t)
check("fotos con nombre van a su carpeta", fb.name == "2026-10-02_Muestra_B")
for carpeta, n in ((c1, 3), (fb, 1)):
    (carpeta / "cam0").mkdir(exist_ok=True)
    for i in range(n):
        M.escribir(carpeta / "cam0" / E.nombre_foto(t, i + 1), np.zeros((40, 50), np.uint16), {})
# carpeta de una version anterior
viejo = tmp / "timelapse_20260901_080000" / "cam1"; viejo.mkdir(parents=True)
tifffile.imwrite(viejo / "img_20260901_080000.tif", np.zeros((8, 8), np.uint16))
lista = ex.listar()
ids = [e["id"] for e in lista]
check("lista los experimentos, el mas nuevo primero", ids[0] == "2026-10-03_0900_Timelapse", ids)
check("no muestra fotos sueltas vacias", "2026-10-02_Fotos_sueltas" not in ids)
check("muestra las carpetas de versiones anteriores", "timelapse_20260901_080000" in ids)
info = ex.info(c1)
check("cuenta fotos y tamaño, y elige portada", info["n_fotos"] == 3 and info["bytes"] > 0 and info["portada"].startswith("cam0/"))
check("resolver no sale de datos/", ex.resolver("../datos") is None and ex.resolver("2026-10-02_1030_../x") is None)
check("ruta_imagen no sale del experimento", ex.ruta_imagen(c1.name, "../../x.tif") is None)
nuevo = ex.renombrar(c1.name, "Células día 1 (repetición)")
check("renombrar cambia la carpeta y conserva fecha y hora", nuevo == "2026-10-02_1030_Celulas_dia_1_repeticion", nuevo)
check("y el nombre en experimento.json", ex.info(ex.resolver(nuevo))["nombre"] == "Células día 1 (repetición)")
nv = ex.renombrar("timelapse_20260901_080000", "Prueba vieja")
check("renombrar una carpeta anterior la muda a datos/", nv == "2026-09-01_0800_Prueba_vieja" and (tmp / "datos" / nv).is_dir(), nv)
cod = ex.borrar(nuevo)
check("borrar la saca de la lista", nuevo not in [e["id"] for e in ex.listar()])
check("pero queda en la papelera", (tmp / "datos" / ".papelera" / cod).is_dir())
check("deshacer la devuelve", ex.restaurar(cod) == nuevo and ex.resolver(nuevo) is not None)
cod2 = ex.borrar(sin.name)
viejo_cod = f"{int(time.time()) - 8 * 86400}__{sin.name}"
os.rename(tmp / "datos" / ".papelera" / cod2, tmp / "datos" / ".papelera" / viejo_cod)
check("la papelera se vacia sola despues de 7 dias", ex.vaciar_papelera() == 1)
z = tmp / "x.zip"; ex.zip(nuevo, z)
nombres = zipfile.ZipFile(z).namelist()
check("zip con todo, dentro de una carpeta con el nombre", all(n.startswith(nuevo + "/") for n in nombres)
      and any(n.endswith("LEEME.txt") for n in nombres) and sum(n.endswith(".tif") for n in nombres) == 3)
fin = ex.finalizar(ex.resolver(nuevo), estado="completo", fin="2026-10-02T11:30:00")
check("finalizar anota fotos y estado", fin["n_fotos"] == 3 and "completo" in (ex.resolver(nuevo) / "LEEME.txt").read_text())
check("espacio libre y cuantas fotos caben", ex.espacio()["fotos_que_caben"] > 0)

print("\n=== OPTICA ===")
op = O.Optica(archivo=tmp / "optica.json")
d = op.de(0)
check("por defecto 20x: 1.12/20 = 0.056 um/px", abs(d["um_por_pixel"] - 0.056) < 1e-9 and d["origen_escala"] == "estimado")
check("campo de vision en micras", d["campo_um"] == [183.7, 138.0], d["campo_um"])
d = op.cambiar(1, {"objetivo": "40x"})
check("elegir 40x trae aumento y apertura", d["aumento"] == 40 and d["na"] == 0.65)
check("cada camara tiene la suya", op.de(0)["objetivo"] == "20x")
d = op.cambiar(0, {"um_por_pixel_medido": 0.05})
check("un valor medido manda sobre el estimado", d["um_por_pixel"] == 0.05 and d["origen_escala"] == "medido")
check("se guarda y se relee", O.Optica(archivo=tmp / "optica.json").de(1)["objetivo"] == "40x")

print("\n=== METADATOS EN EL TIF ===")
img = (np.arange(60 * 80) % 4000).astype(np.uint16).reshape(60, 80)
ruta = tmp / "m.tif"
meta_in = {"optica": op.de(1), "experimento": {"nombre": "Células día 1", "ciclo": 3},
           "camara": {"numero": 1, "sensor": "IMX219"}}
M.escribir(ruta, img, meta_in)
check("la imagen queda identica (16 bits)", (tifffile.imread(ruta) == img).all() and tifffile.imread(ruta).dtype == np.uint16)
leido = M.leer(ruta)
check("se leen los metadatos de MicroscopeOS", leido["experimento"]["nombre"] == "Células día 1" and leido["experimento"]["ciclo"] == 3)
with tifffile.TiffFile(ruta) as tf:
    ij = tf.imagej_metadata
    xres = tf.pages[0].tags["XResolution"].value
    soft = tf.pages[0].tags["Software"].value
check("Fiji ve la unidad en micras", ij.get("unit") == "um")
check("y la escala (pixeles por micra)", abs(xres[0] / xres[1] - 1 / 0.028) < 0.01, xres)
check("Image > Show Info muestra los datos", "optica.objetivo = 40x" in ij.get("Info", ""))
check("etiqueta Software", soft == "MicroscopeOS")
check("un tif sin metadatos da {}", M.leer(viejo.parent.parent / "datos" / nv / "cam1" / "img_20260901_080000.tif") == {})

class LuzFake:
    current_pattern = "LEFT"; brightness_percent = 60; color_dpc = "00FF00"; current_color = None
class MotorFake:
    posicion_um = -12.345
class CamFake:
    exposure_time = 12000; gain = 1.2
ctx = M.Contexto(optica=op, illuminations={0: LuzFake()}, motores={0: MotorFake()}, camera=CamFake(),
                 temperatura=lambda: {"temperature": 36.9, "setpoint": 37.0, "co2": None})
mc = ctx.para(0, {"experimento": {"nombre": "X"}})
check("contexto: luz con nombre para personas", mc["iluminacion"]["nombre"] == "Relieve DPC, desde la izquierda"
      and mc["iluminacion"]["color"] == "00FF00" and mc["iluminacion"]["brillo_pct"] == 60)
check("contexto: foco, camara y escala", mc["foco"]["posicion_um"] == -12.35 and mc["camara"]["exposicion_us"] == 12000
      and mc["optica"]["um_por_pixel"] == 0.05)
check("contexto: incubadora sin valores vacios", mc["incubadora"] == {"temperature": 36.9, "setpoint": 37.0})

print("\n=== MARCA DE AGUA ===")
base = MA.limpiar({})
for n in ("ninguna", "escala", "cientifica", "publicidad"):
    check(f"preset «{n}» se reconoce", MA.preset_de(MA.con_preset(base, n)) == n)
cfg = MA.con_preset(base, "cientifica"); cfg["campos"]["camara"] = False
check("tocar un dato la vuelve «personalizada»", MA.preset_de(cfg) == "personalizada")
check("barra de escala: medida redonda que entra en ~20% del ancho", MA.elegir_barra(0.056, 3280) == (25, 25 / 0.056))
sint = MA.muestra_sintetica(400, 300)
meta = {"optica": {"um_por_pixel": 0.056, "objetivo": "20x", "resolucion_px": [400, 300]},
        "experimento": {"nombre": "Prueba"}, "fecha_hora": "2026-10-02T10:30:00"}
sin_marca = MA.exportar(sint, meta, MA.con_preset(base, "ninguna"))
con_marca = MA.exportar(sint, meta, MA.con_preset(base, "cientifica"))
from PIL import Image
a = np.asarray(Image.open(io.BytesIO(sin_marca)), dtype=int)
b = np.asarray(Image.open(io.BytesIO(con_marca)), dtype=int)
check("exporta JPEG", sin_marca[:3] == b"\xff\xd8\xff")
check("con marca la copia cambia, sin marca no", np.abs(a - b).mean() > 0.5)
check("la marca va en las esquinas, el centro queda igual", np.abs(a[120:180, 150:250] - b[120:180, 150:250]).max() < 12)
png = MA.exportar(sint, meta, MA.con_preset(base, "publicidad"), formato="png", ancho_max=200)
check("PNG y achicado", png[:4] == b"\x89PNG" and Image.open(io.BytesIO(png)).width == 200)

print("\n=== API: fotos, galeria, descargas ===")
from fastapi.testclient import TestClient
from core.camera import CameraController
from core.timelapse import TimelapseManager
from server.api import create_app
import server.api as _api
_api.ARCHIVO_ILUM = tmp / "iluminacion.json"     # no tocar profiles/ del repo
_api.ARCHIVO_CALIB = tmp / "calib" / "calibracion_imagen.json"

class Luz:
    def __init__(self):
        self.current_pattern, self.state, self.brightness_percent = "OFF", False, 80
        self.color_dpc, self.current_color = "00FF00", None
    def _p(self, p): self.current_pattern, self.state = p, p != "OFF"
    def on(self): self._p("FULL")
    def off(self): self._p("OFF")
    def left(self): self._p("LEFT")
    def right(self): self._p("RIGHT")
    def top(self): self._p("TOP")
    def bottom(self): self._p("BOTTOM")
    def ring(self): self._p("RING")
    def rheinberg(self, *a): self._p("RHEINBERG")
    def set_brightness(self, p): self.brightness_percent = p
    @staticmethod
    def _hex(c):
        import re as _re
        if not _re.fullmatch(r"[0-9A-Fa-f]{6}", c or ""):
            raise ValueError(f"color invalido: {c!r}")
        return None if c.upper() == "FFFFFF" else c.upper()
    def set_color_dpc(self, c): self.color_dpc = self._hex(c)
    color_campo = None
    _rheinberg_colors = ("0000FF", "FF6A00")
    def set_color_campo(self, c): self.color_campo = self._hex(c)

raiz_api = tmp / "api"
MA.ARCHIVO = raiz_api / "marca.json"     # no tocar profiles/ del repo
MA.LOGO = raiz_api / "logo.png"
ex2 = E.Experimentos(raiz=raiz_api / "datos", legado=raiz_api)
op2 = O.Optica(archivo=raiz_api / "optica.json")
luces = {0: Luz(), 1: Luz()}
cam = CameraController()
cam.metadatos = M.Contexto(optica=op2, illuminations=luces, camera=cam)
tl = TimelapseManager(cam, luces, experimentos=ex2)
app = create_app(cam, luces, tl, experimentos=ex2, optica=op2)
cl = TestClient(app)

cl.post("/light/set", json={"modo": "full", "percent": 70, "camaras": [0]})
r = cl.post("/capture/0/dpc", params={"nombre": "Muestra B"}).json()
check("foto DPC con nombre: 4 archivos en su experimento", len(r["saved"]) == 4 and r["experimento"].endswith("_Muestra_B")
      and r["saved"][0].startswith("cam0/") and r["saved"][0].endswith("_L.tif"), r)
check("al terminar la luz vuelve a como estaba (no se apaga)", luces[0].current_pattern == "FULL")
foto = ex2.ruta_imagen(r["experimento"], r["saved"][1])
mf = M.leer(foto)
check("la foto lleva el experimento, el canal y la luz de ESE momento",
      mf["experimento"]["nombre"] == "Muestra B" and mf["canal_dpc"] == "derecha"
      and mf["iluminacion"]["patron"] == "RIGHT", mf.get("iluminacion"))
check("y la escala de la optica", mf["optica"]["um_por_pixel"] == 0.056)
r2 = cl.post("/capture/both/blanco").json()
check("sin nombre van a «Fotos sueltas» de hoy", r2["experimento"].endswith("_Fotos_sueltas") and len(r2["saved"]) == 2)
lst = cl.get("/api/experimentos").json()
check("la galeria lista los dos con espacio libre", len(lst["experimentos"]) == 2 and lst["espacio"]["libre_bytes"] > 0)
det = cl.get(f"/api/exp/{r['experimento']}").json()
check("detalle con la lista de fotos", det["n_fotos"] == 4 and len(det["imagenes"]) == 4)
rel = det["imagenes"][0]
mini = cl.get(f"/api/exp/{r['experimento']}/mini/{rel}", params={"size": 200})
check("miniatura JPEG (y queda en cache)", mini.headers["content-type"] == "image/jpeg"
      and any((raiz_api / "datos" / ".miniaturas").iterdir()))
orig = cl.get(f"/api/exp/{r['experimento']}/original/{rel}")
check("descarga el original intacto", orig.content == foto.parent.joinpath(Path(rel).name).read_bytes()
      or orig.content == (ex2.resolver(r["experimento"]) / rel).read_bytes())
comp = cl.get(f"/api/exp/{r['experimento']}/compartir/{rel}")
check("copia para compartir en JPEG", comp.content[:3] == b"\xff\xd8\xff" and "attachment" in comp.headers["content-disposition"])
check("no deja salir del experimento", cl.get(f"/api/exp/{r['experimento']}/original/..%2F..%2Foptica.json").status_code == 404)
zr = cl.get(f"/api/exp/{r['experimento']}/zip")
check("zip de originales", sum(n.endswith(".tif") for n in zipfile.ZipFile(io.BytesIO(zr.content)).namelist()) == 4)
zc = cl.get(f"/api/exp/{r['experimento']}/zip", params={"tipo": "compartir"})
check("zip para compartir: JPG", sum(n.endswith(".jpg") for n in zipfile.ZipFile(io.BytesIO(zc.content)).namelist()) == 4)
check("los zip temporales se borran", not list((raiz_api / "datos").glob("*.zip")))
nuevo_id = cl.post(f"/api/exp/{r['experimento']}/renombrar", json={"nombre": "Muestra B, tinción"}).json()["id"]
check("renombrar desde la API", nuevo_id.endswith("_Muestra_B_tincion"), nuevo_id)
check("renombrar con un nombre vacio avisa", "error" in cl.post(f"/api/exp/{nuevo_id}/renombrar", json={"nombre": "  "}).json())
codigo = cl.post(f"/api/exp/{nuevo_id}/borrar").json()["codigo"]
check("borrar y deshacer", cl.post("/api/papelera/restaurar", json={"codigo": codigo}).json()["id"] == nuevo_id)

print("\n=== API: colores de la luz por camara ===")
import core.experimentos  # noqa
from server import api as api_mod
est = cl.post("/light/colores", json={"camaras": [1], "campo": "FF0000"}).json()["matrices"]
check("campo claro rojo solo en la camara 1", est["1"]["color_campo"] == "FF0000" and est["0"]["color_campo"] == "FFFFFF")
est = cl.post("/light/colores", json={"camaras": [0, 1], "dpc": "0000FF"}).json()["matrices"]
check("relieve azul en las dos", est["0"]["color_dpc"] == "0000FF" and est["1"]["color_dpc"] == "0000FF")
check("un color invalido avisa", "error" in cl.post("/light/colores", json={"camaras": [0], "dpc": "verde"}).json())
cl.post("/light/colores", json={"camaras": [0, 1], "dpc": "00FF00", "campo": "FFFFFF"})

print("\n=== API: optica y marca de agua ===")
o = cl.post("/api/optica", json={"camara": 1, "objetivo": "10x"}).json()["camara"]
check("cambiar el objetivo recalcula la escala", o["aumento"] == 10 and abs(o["um_por_pixel"] - 0.112) < 1e-9)
check("y la lista de objetivos", len(cl.get("/api/optica").json()["objetivos"]) == 6)
mk = cl.post("/api/marca", json={"preset": "publicidad"}).json()
check("elegir un preset", mk["preset"] == "publicidad" and mk["config"]["logo"])
mk = cl.post("/api/marca", json={"config": {"campos": {"luz": False}, "datos": True}}).json()
check("tocar una opcion la vuelve personalizada", mk["preset"] == "personalizada" and not mk["config"]["campos"]["luz"])
vista = cl.get("/api/marca/vista", params={"ancho": 500})
check("vista previa con la ultima foto", vista.content[:3] == b"\xff\xd8\xff"
      and Image.open(io.BytesIO(vista.content)).width == 500)
buf = io.BytesIO(); Image.new("RGBA", (60, 20), (255, 0, 0, 255)).save(buf, "PNG")
import base64
lg = cl.post("/api/marca/logo", json={"png_base64": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()}).json()
check("subir un logo propio", lg["logo_propio"] and MA.LOGO.is_file())
check("un archivo que no es imagen se rechaza", "error" in cl.post("/api/marca/logo", json={"png_base64": "aG9sYQ=="}).json())
check("quitar el logo", not cl.post("/api/marca/logo/quitar").json()["logo_propio"])

print("\n=== TIMELAPSE: carpeta, nombres y cierre ===")
tl.start(modo="blanco", interval_seconds=1, duration_seconds=600, stabilization_time=0,
         camaras=[0, 1], nombre="Células día 1")
while tl.is_running() and tl.ciclo_actual < 2:
    time.sleep(0.05)
time.sleep(0.6)
tl.stop()
carpeta = Path(tl.base_folder)
check("carpeta con fecha y el nombre del experimento", carpeta.parent == raiz_api / "datos"
      and carpeta.name.endswith("_Celulas_dia_1"), carpeta.name)
fotos0 = sorted(p.name for p in (carpeta / "cam0").glob("*.tif"))
check("fotos numeradas: 0001_..., 0002_...", fotos0[:2] and fotos0[0].startswith("0001_") and fotos0[1].startswith("0002_"), fotos0)
mt = M.leer(carpeta / "cam1" / sorted((carpeta / "cam1").glob("*.tif"))[0].name)
check("cada foto sabe su experimento y su ciclo", mt["experimento"]["nombre"] == "Células día 1" and mt["experimento"]["ciclo"] == 1)
ej = json.loads((carpeta / "experimento.json").read_text())
check("experimento.json conserva lo que lee la PC (modo, sufijos)", ej["modo"] == "blanco" and ej["sufijos"] == [""])
check("y al cerrar anota estado y fotos", ej["estado"] == "detenido antes de tiempo" and ej["n_fotos"] == len(fotos0) * 2, ej)
check("LEEME.txt dentro", "Células día 1" in (carpeta / "LEEME.txt").read_text())
lst = cl.get("/api/experimentos").json()["experimentos"]
check("aparece en la galeria como timelapse", any(e["id"] == carpeta.name and e["tipo"] == "timelapse" for e in lst))

print("\n=== CAMARA: tuning sin ALSC y calibracion de imagen ===")
ruta_t = emuladores.Picamera2Fake.tuning_recibido
algs = json.loads(Path(ruta_t).read_text())["algorithms"] if ruta_t else []
check("la camara se abre con un tuning propio sin rpi.alsc",
      algs and not any("rpi.alsc" in a for a in algs) and any("rpi.awb" in a for a in algs), ruta_t)
luces[0].on()
check("sin calibrar", cl.get("/camera/calibracion").json() == {"calibrada": False})
# Frames negros: el balance no puede converger (ganancias al tope).
r = cl.post("/camera/calibrar", json={"camaras": [0]}).json()
check("si el color queda en el limite NO se guarda nada",
      "error" in r and not r["camaras"]["0"]["converge"] and not _api.ARCHIVO_CALIB.exists()
      and 0 not in cam._colour_gains and cam.get_flat(0) is None, r)
check("y la luz vuelve a como estaba", luces[0].current_pattern == "FULL" and luces[0].color_campo is None)
check("y la camara no se queda con el color en el tope",
      tuple(cam._cams[0].controles.get("ColourGains")) == (1.0, 1.0), cam._cams[0].controles.get("ColourGains"))
# Campo vacio parejo y neutro: converge.
_orig = emuladores.Picamera2Fake.capture_array
emuladores.Picamera2Fake.capture_array = lambda self, n: np.full((480, 640, 3), 200, np.uint8)
luces[0].set_color_campo("FF0000")
try:
    r = cl.post("/camera/calibrar", json={"camaras": [0]}).json()
finally:
    emuladores.Picamera2Fake.capture_array = _orig
check("campo parejo: calibra y guarda", r.get("status") == "ok" and _api.ARCHIVO_CALIB.exists()
      and (tmp / "calib" / "flat_cam0.npy").exists(), r)
check("devuelve el color de campo claro que habia", luces[0].color_campo == "FF0000")
g = cl.get("/camera/calibracion").json()
check("queda registrada", g["calibrada"] and g["colour_gains"]["0"] == [1.0, 1.0] and g["exposure_us"] == r["exposure_us"], g)
check("el vivo se corrige con el campo plano", cam.get_flat(0) is not None
      and cam._aplicar_flat(0, np.full((48, 64, 3), 100, np.uint8)).shape == (48, 64, 3))
luces[0].set_color_campo("FFFFFF")
b = cl.post("/camera/calibracion/borrar").json()
check("quitar calibracion", b == {"calibrada": False} and not _api.ARCHIVO_CALIB.exists()
      and cam.get_flat(0) is None and 0 not in cam._colour_gains)

print("\n" + "=" * 50)
print(f"PASS: {ok}   FAIL: {fail}")
print("=" * 50)
sys.exit(1 if fail else 0)
