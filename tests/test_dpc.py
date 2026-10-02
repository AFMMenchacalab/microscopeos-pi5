"""Pruebas del DPC por ciclo (core/dpc.py) y su paso por el timelapse.

    cd tests
    SP=$PWD PROY=$PWD/../codigo/MicroscopeOS python3 test_dpc.py

Sin Pi ni hardware, pero con el tifffile DE VERDAD (no el emulador de
emuladores.py): lo que se prueba es justamente que los TIFF que quedan en
disco se pueden leer y valen lo que se calculo, porque eso es lo que
autoriza a borrar las 4 crudas.

Las 4 capturas se sintetizan con la fisica del montaje: un objeto de fase
(no absorbe) bajo media apertura se ve como su gradiente, con signo
opuesto en cada mitad; ademas cada mitad de la matriz tiene distinto
brillo y un degradado propio (en el montaje real R llega a ~1.8 veces L),
y hay una mancha que absorbe igual con cualquier luz.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.environ.get(
    "PROY", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "codigo", "MicroscopeOS")))

import numpy as np
import tifffile

from core import dpc, metadatos
from core.experimentos import Experimentos
from core.timelapse import TimelapseManager

ok = fail = 0
def check(nombre, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print(f"  PASS  {nombre}  {extra}".rstrip())
    else:    fail += 1; print(f"  FAIL  {nombre}  {extra}")


# ---------------- capturas sinteticas ----------------
FORMA = (600, 800)
yy, xx = np.mgrid[0:FORMA[0], 0:FORMA[1]].astype(np.float32)
FASE = sum(0.6 * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * 25.0 ** 2))
           for cx, cy in ((250, 200), (520, 380), (400, 150)))
GY, GX = np.gradient(FASE)
ABSORCION = 1 - 0.4 * np.exp(-((xx - 150) ** 2 + (yy - 450) ** 2) / (2 * 12.0 ** 2))
BRILLO = {"LEFT": 6000, "RIGHT": 11000, "TOP": 7500, "BOTTOM": 10000}
K = 8.0     # contraste del relieve por radian/pixel


def captura(patron, ruido=True, semilla=0):
    g = {"LEFT": GX, "RIGHT": -GX, "TOP": GY, "BOTTOM": -GY}[patron]
    degradado = 1 + 0.15 * (xx / FORMA[1]) * (1 if patron in ("LEFT", "TOP") else -1)
    img = BRILLO[patron] * degradado * ABSORCION * (1 + K * g)
    if ruido:
        img = img + np.random.default_rng(semilla).normal(0, 30, FORMA)
    return np.clip(img, 0, 65535).astype(np.uint16)


META = {"optica": {"um_por_pixel": 0.2159, "na": 0.40},
        "iluminacion": {"patron": "LEFT", "color": "00FF00"}}


def escribir_ciclo(carpeta, ciclo=1, meta=META):
    rutas = {}
    for s, p in zip(dpc.SUFIJOS_CRUDAS, ("LEFT", "RIGHT", "TOP", "BOTTOM")):
        r = os.path.join(carpeta, f"{ciclo:04d}_2026-10-01_16-27-09{s}.tif")
        metadatos.escribir(r, captura(p, semilla=ciclo), dict(meta, canal_dpc=s))
        rutas[s] = r
    return rutas


# El fondo se estima con un suavizado de sigma 150 px: a menos de ~sigma
# del borde se curva con el degradado de la iluminacion y deja un error
# de ~1-2 % en el DPC. Por eso las comparaciones van sobre el interior y
# el borde se prueba aparte, con su propia tolerancia.
INTERIOR = (slice(150, -150), slice(150, -150))


def corr(a, b):
    m = INTERIOR
    return float(np.corrcoef(a[m].ravel(), b[m].ravel())[0, 1])


# ---------------- 1. calculo ----------------
print("\n1. DPC sobre capturas sinteticas")
L, R, T, B = (captura(p, ruido=False) for p in ("LEFT", "RIGHT", "TOP", "BOTTOM"))
lr, tb = dpc.calcular_dpc(L, R, T, B)
check("L-R sigue al gradiente en x", corr(lr, GX) > 0.98, f"r={corr(lr, GX):.4f}")
check("T-B sigue al gradiente en y", corr(tb, GY) > 0.98, f"r={corr(tb, GY):.4f}")
check("L-R no ve el gradiente en y", abs(corr(lr, GY)) < 0.1, f"r={corr(lr, GY):.4f}")
lejos = (slice(160, 300), slice(620, 650))   # interior, sin celulas cerca
check("brillo distinto de cada mitad corregido (fondo ~0)",
      abs(float(lr[lejos].mean())) < 0.01, f"media={lr[lejos].mean():+.4f}")
borde = (slice(250, 350), slice(0, 40))
check("cerca del borde el fondo queda corrido menos de 3 %",
      abs(float(lr[borde].mean())) < 0.03, f"media={lr[borde].mean():+.4f}")
mancha = (slice(440, 460), slice(140, 160))
check("la absorcion se cancela", abs(float(lr[mancha].mean())) < 0.01,
      f"media={lr[mancha].mean():+.4f}")
# Sin la normalizacion por fondo, el desbalance de la matriz domina
crudo = (L.astype(np.float32) - R) / (L.astype(np.float32) + R)
check("(sin normalizar el fondo saldria corrido)",
      abs(float(crudo[lejos].mean())) > 0.1, f"media={crudo[lejos].mean():+.3f}")

v = np.linspace(-1, 1, 1001, dtype=np.float32)
check("uint16 ida y vuelta (DPC): error de a lo mas medio paso",
      np.abs(dpc.desde_uint16(dpc.a_uint16(v, dpc.ESCALA_DPC), dpc.ESCALA_DPC) - v).max()
      <= dpc.ESCALA_DPC / 2 + 1e-7)
# El paso tiene que quedar muy por debajo del ruido real del DPC (~0.008
# por pixel en cam0): cuantizar suma ESCALA/sqrt(12) de ruido.
check("el paso del DPC no agrega ruido (<2 % del de cam0)",
      dpc.ESCALA_DPC / np.sqrt(12) < 0.02 * 0.008, f"paso={dpc.ESCALA_DPC:.2e}")
s4, f4 = dpc.calcular_suma(L, R, T, B, ancho=400)
media = (L.astype(np.float32) + R + T + B) / 4
check("suma: promedio de las 4, reducido", s4.dtype == np.uint16 and s4.shape == (300, 400)
      and f4 == 2.0, f"{s4.shape} x{f4}")
check("suma: vale el promedio (en bloques de 2x2)",
      np.abs(s4.astype(np.float32) - media.reshape(300, 2, 400, 2).mean((1, 3))).max() <= 1)
check("suma: la absorcion se ve (es lo que el DPC no guarda)",
      s4[225, 75] < 0.8 * s4[100, 300], f"mancha={s4[225, 75]} fondo={s4[100, 300]}")
s0, f0 = dpc.calcular_suma(L, R, T, B, ancho=0)
check("suma: ancho 0 = resolucion completa", s0.shape == FORMA and f0 == 1.0)
check("salidas por defecto", dpc.salidas() == ["_dpcLR", "_dpcTB", "_suma"])
check("salidas con fase y sin suma", dpc.salidas({"fase": True, "suma": False}) == ["_dpcLR", "_dpcTB", "_fase"])
crudas_b = 4 * 3280 * 2464 * 2
por_ciclo = dpc.bytes_por_ciclo()
check("espacio: un ciclo procesado ocupa menos de la mitad que las crudas",
      por_ciclo < 0.5 * crudas_b, f"{por_ciclo / 1e6:.1f} MB vs {crudas_b / 1e6:.1f} MB")
check("espacio: la estimacion no queda corta (medido en cam0: ~25 MB)", por_ciclo > 25e6)
check("espacio: sin borrar suma las crudas",
      dpc.bytes_por_ciclo({"borrar_crudas": False}) == por_ciclo + crudas_b)
check("uint16: el cero cae en 32768", int(dpc.a_uint16(np.zeros(1), dpc.ESCALA_DPC)[0]) == 32768)
check("longitud de onda: verde", dpc.longitud_onda_um("00FF00") == 0.525)
check("longitud de onda: blanco o sin dato", dpc.longitud_onda_um("FFFFFF") == 0.55
      and dpc.longitud_onda_um(None) == 0.55)


# ---------------- 2. un ciclo en disco ----------------
print("\n2. procesar_ciclo: escribe, verifica y recien ahi borra")
with tempfile.TemporaryDirectory() as tmp:
    rutas = escribir_ciclo(tmp)
    r = dpc.procesar_ciclo(rutas, {"fase": True, "suma_ancho": 400})
    nombres = sorted(os.path.basename(p) for p in os.listdir(tmp))
    check("quedan dpcLR, dpcTB, fase, suma y jpg", nombres == [
        "0001_2026-10-01_16-27-09_dpc.jpg", "0001_2026-10-01_16-27-09_dpcLR.tif",
        "0001_2026-10-01_16-27-09_dpcTB.tif", "0001_2026-10-01_16-27-09_fase.tif",
        "0001_2026-10-01_16-27-09_suma.tif"],
        str(nombres))
    check("las 4 crudas se borraron", len(r["borradas"]) == 4
          and not any(os.path.exists(p) for p in rutas.values()))
    a = tifffile.imread(os.path.join(tmp, "0001_2026-10-01_16-27-09_dpcLR.tif"))
    check("TIFF uint16 del mismo tamano", a.dtype == np.uint16 and a.shape == FORMA)
    with tifffile.TiffFile(os.path.join(tmp, "0001_2026-10-01_16-27-09_dpcLR.tif")) as t:
        comp, pred = t.pages[0].compression, t.pages[0].predictor
    check("TIFF comprimido sin perdida (deflate + predictor)", int(comp) == 8 and int(pred) == 2,
          f"{comp} {pred}")
    check("el TIFF vale lo calculado",
          corr(dpc.desde_uint16(a, dpc.ESCALA_DPC), GX) > 0.95)
    m = metadatos.leer(os.path.join(tmp, "0001_2026-10-01_16-27-09_dpcLR.tif"))
    check("metadatos: escala y formula del valor",
          m.get("optica", {}).get("um_por_pixel") == 0.2159
          and m.get("dpc", {}).get("valor") == "(pixel - 32768) / 4096"
          and m["dpc"].get("cero") == 32768 and m["dpc"].get("escala") == 1 / 4096, str(m.get("dpc")))
    ruta_s = os.path.join(tmp, "0001_2026-10-01_16-27-09_suma.tif")
    s = tifffile.imread(ruta_s)
    ms = metadatos.leer(ruta_s)
    check("suma en disco: reducida y con la escala en micras corregida",
          s.shape == (300, 400) and abs(ms["optica"]["um_por_pixel"] - 0.4318) < 1e-9
          and ms["suma"]["reduccion"] == 2.0, f"{s.shape} {ms.get('optica')}")
    with tifffile.TiffFile(ruta_s) as t:
        check("suma en disco: Fiji ve la escala de la foto reducida",
              abs(t.pages[0].tags["XResolution"].value[0] / t.pages[0].tags["XResolution"].value[1]
                  - 1 / 0.4318) < 1e-3)
    f = dpc.desde_uint16(tifffile.imread(
        os.path.join(tmp, "0001_2026-10-01_16-27-09_fase.tif")), dpc.ESCALA_FASE)
        # Solo |r|: el signo depende de como ve la matriz el montaje (esta
    # reflejada) y se fijo con una muestra real (dpc.SIGNOS_FASE), no con
    # esta convencion sintetica. Y el modelo sintetico (relieve = K *
    # gradiente) no es la funcion de transferencia real, asi que no se
    # espera un calce perfecto.
    check("la fase reconstruida se parece al objeto", abs(corr(f, FASE)) > 0.6,
          f"r={corr(f, FASE):.3f}")
    check("sin archivos .parcial", not any(n.endswith(".parcial") for n in os.listdir(tmp)))

with tempfile.TemporaryDirectory() as tmp:
    rutas = escribir_ciclo(tmp)
    dpc.procesar_ciclo(rutas, {"borrar_crudas": False, "jpg": False})
    check("borrar_crudas=False conserva las 4",
          all(os.path.exists(p) for p in rutas.values()))

with tempfile.TemporaryDirectory() as tmp:
    rutas = escribir_ciclo(tmp)
    dpc.procesar_ciclo(rutas, {"comprimir": False, "suma": False, "jpg": False})
    nombres = sorted(os.listdir(tmp))
    with tifffile.TiffFile(os.path.join(tmp, nombres[0])) as t:
        comp = t.pages[0].compression
    check("comprimir=False y suma=False: solo los 2 DPC, sin comprimir",
          len(nombres) == 2 and int(comp) == 1, f"{nombres} {comp}")

with tempfile.TemporaryDirectory() as tmp:
    rutas = escribir_ciclo(tmp, meta={})        # sin optica: la fase no se puede
    try:
        dpc.procesar_ciclo(rutas, {"fase": True})
        error = None
    except Exception as e:
        error = e
    check("si falla, lanza el error...", error is not None, str(error)[:60])
    check("...y NO borra las crudas", all(os.path.exists(p) for p in rutas.values()))

with tempfile.TemporaryDirectory() as tmp:
    rutas = escribir_ciclo(tmp)
    os.remove(rutas["_T"])
    try:
        dpc.procesar_ciclo(rutas)
        error = None
    except Exception as e:
        error = e
    check("falta una captura -> error", error is not None)
    check("falta una captura -> las otras 3 siguen",
          all(os.path.exists(rutas[s]) for s in ("_L", "_R", "_B")))


# ---------------- 3. dentro del timelapse ----------------
print("\n3. Timelapse en modo dpc")


class Luz:
    def __init__(self):
        self.current_pattern, self.color_dpc, self.brightness_percent = "OFF", "00FF00", 100
    def _p(self, p): self.current_pattern = p
    def on(self): self._p("FULL")
    def off(self): self._p("OFF")
    def left(self): self._p("LEFT")
    def right(self): self._p("RIGHT")
    def top(self): self._p("TOP")
    def bottom(self): self._p("BOTTOM")


class Camara:
    def __init__(self, luces, fallar=None):
        self.luces, self.fallar = luces, fallar
    def capture_image(self, camera_num, folder, filename, meta=None):
        p = self.luces[camera_num].current_pattern
        if p == self.fallar:
            raise RuntimeError("captura fallida simulada")
        m = dict(META, **(meta or {}))
        m["iluminacion"] = {"patron": p, "color": "00FF00"}
        metadatos.escribir(filename, captura(p), m)


class Enviador:
    def __init__(self): self.rutas = []
    def encolar(self, ruta, exp, cam): self.rutas.append(os.path.basename(ruta))


def correr(dpc_opts, fallar=None, camaras=(0, 1), duracion=2.5):
    tmp = tempfile.mkdtemp()
    luces = {c: Luz() for c in camaras}
    env = Enviador()
    tl = TimelapseManager(Camara(luces, fallar), luces, enviador=env,
                          experimentos=Experimentos(raiz=Path(tmp), legado=Path(tmp) / "x"))
    tl.start(modo="dpc", interval_seconds=1, duration_seconds=duracion,
             stabilization_time=0, camaras=list(camaras), enviar_pc=True,
             dpc_opts=dpc_opts)
    tl.thread.join(timeout=60)
    carpeta = Path(tl.base_folder)
    archivos = sorted(str(p.relative_to(carpeta)) for p in carpeta.rglob("*") if p.is_file())
    return carpeta, archivos, env.rutas


carpeta, archivos, enviados = correr({"borrar_crudas": True})
fotos = [a for a in archivos if a.startswith("cam")]
crudas = [a for a in fotos if a.endswith(("_L.tif", "_R.tif", "_T.tif", "_B.tif"))]
ciclos = len([a for a in fotos if a.startswith("cam0/") and a.endswith("_dpcLR.tif")])
check("3 ciclos, dos camaras, cada uno con dpcLR+dpcTB+suma+jpg",
      ciclos == 3 and len(fotos) == 3 * 2 * 4, f"{len(fotos)} archivos")
check("no queda ninguna cruda", not crudas, str(crudas[:2]))
check("a la PC no se mando ninguna cruda (ya no existirian)",
      not any(n.endswith(("_L.tif", "_R.tif", "_T.tif", "_B.tif")) for n in enviados))
check("a la PC se mandaron los DPC y la suma", sum(n.endswith("_dpcLR.tif") for n in enviados) == 6
      and sum(n.endswith("_suma.tif") for n in enviados) == 6)
meta = json.loads((carpeta / "experimento.json").read_text())
check("experimento.json dice que se proceso",
      meta.get("dpc_procesado", {}).get("borrar_crudas") is True)
check("experimento.json dice que archivos deja cada ciclo y como leerlos",
      meta["dpc_procesado"].get("sufijos") == ["_dpcLR", "_dpcTB", "_suma"]
      and meta["dpc_procesado"].get("valor_dpc") == "(pixel - 32768) / 4096", str(meta["dpc_procesado"]))
leeme = (carpeta / "LEEME.txt").read_text()
check("LEEME explica los archivos y la formula",
      "_dpcLR.tif" in leeme and "(píxel - 32768) / 4096" in leeme and "_suma.tif" in leeme
      and "se borraron" in leeme)
log = (carpeta / "timelapse.log").read_text()
check("el log registra cada ciclo procesado", log.count("crudas borradas") == 6)
check("la galeria cuenta los TIFF que quedan",
      Experimentos(raiz=carpeta.parent).info(carpeta)["n_fotos"] == 18)

carpeta, archivos, enviados = correr({"borrar_crudas": False, "jpg": False}, camaras=(0,))
crudas = [a for a in archivos if a.endswith(("_L.tif", "_R.tif", "_T.tif", "_B.tif"))]
check("sin borrar: quedan crudas y DPC", len(crudas) == 12
      and sum(a.endswith("_dpcTB.tif") for a in archivos) == 3)
check("sin borrar: se mandan crudas y DPC",
      sum(n.endswith("_L.tif") for n in enviados) == 3
      and sum(n.endswith("_dpcLR.tif") for n in enviados) == 3)

carpeta, archivos, enviados = correr({"borrar_crudas": True}, fallar="TOP", camaras=(0,))
crudas = [a for a in archivos if a.endswith(("_L.tif", "_R.tif", "_B.tif"))]
check("si falta una captura no se calcula ni se borra nada",
      len(crudas) == 9 and not any("dpc" in a for a in archivos), f"{len(crudas)} crudas")
check("y se mandan las crudas que si estan", sum(n.endswith("_L.tif") for n in enviados) == 3)
log = (carpeta / "timelapse.log").read_text()
check("el log avisa que se conservan", "se conservan las crudas" in log)

carpeta, archivos, enviados = correr(None, camaras=(0,))
check("dpc_opts=None: como siempre, solo las 4 crudas",
      len([a for a in archivos if a.startswith("cam")]) == 12
      and not any("dpc" in a for a in archivos))


print(f"\n{ok} PASS, {fail} FAIL")
sys.exit(1 if fail else 0)
