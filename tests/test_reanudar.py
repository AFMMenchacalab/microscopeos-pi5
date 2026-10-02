"""Timelapse que se reanuda solo despues de un corte de luz, y la vista
previa de la ultima foto que muestra la pagina.

Corre sin hardware: camara y matrices falsas que escriben TIFF chicos.
El corte de luz se simula dejando el archivo de estado tal como queda
cuando la Pi se apaga de golpe (nadie llega a borrarlo ni a cerrar el
experimento).
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
sys.path.insert(0, os.path.join(AQUI, "..", "codigo", "MicroscopeOS"))

import cv2
import numpy as np
import tifffile

from core import experimentos as E
from core import timelapse as T

ok = fail = 0
def check(nombre, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print(f"  PASS  {nombre}")
    else:    fail += 1; print(f"  FAIL  {nombre}  {extra}")


class Camara:
    """Escribe un TIFF chico de 16 bits por foto; el patron de luz queda
    en la imagen para poder armar un relieve DPC de verdad."""
    def __init__(self, luces):
        self.luces = luces
    def _imagen(self, cam):
        pat = self.luces[cam].current_pattern
        x = np.linspace(0, 1, 160, dtype=np.float32)[None, :].repeat(120, 0)
        base = {"LEFT": 1 - x, "RIGHT": x}.get(pat, np.full_like(x, 0.5))
        return (1000 + 20000 * base).astype(np.uint16)
    def capture_image(self, camera_num, folder, filename, meta=None):
        tifffile.imwrite(filename, self._imagen(camera_num))
        return filename
    def capture_both(self, folder, filenames, camera_nums, meta=None):
        for c in camera_nums:
            self.capture_image(c, folder, filenames[c], meta)


class Luz:
    def __init__(self): self.current_pattern = "OFF"
    def _p(self, p): self.current_pattern = p
    def on(self): self._p("FULL")
    def off(self): self._p("OFF")
    def left(self): self._p("LEFT")
    def right(self): self._p("RIGHT")
    def top(self): self._p("TOP")
    def bottom(self): self._p("BOTTOM")
    def ring(self): self._p("RING")
    def rheinberg(self, *a): self._p("RHEINBERG")


def esperar(cond, tope=20):
    t0 = time.time()
    while not cond() and time.time() - t0 < tope:
        time.sleep(0.05)
    return cond()


def manager(raiz):
    luces = {0: Luz(), 1: Luz()}
    return T.TimelapseManager(Camara(luces), luces,
                              experimentos=E.Experimentos(raiz=raiz / "datos", legado=raiz))


def cortar_la_luz(tl):
    """Lo que queda en disco si la Pi se apaga de golpe: el hilo muere sin
    llegar a borrar el estado ni a cerrar experimento.json."""
    tl._borrar_estado = lambda: None
    tl.experimentos.finalizar = lambda *a, **k: None
    tl._graficar_temperatura = lambda: None
    tl.stop()


tmp = Path(tempfile.mkdtemp(prefix="reanudar_"))

print("\n=== MIENTRAS CORRE, QUEDA ANOTADO COMO SEGUIRLO ===")
tl = manager(tmp)
estado = tl.archivo_estado
check("el archivo de estado vive en datos/", estado == tmp / "datos" / T.ARCHIVO_REANUDAR, estado)
tl.start(modo="dpc", interval_seconds=1, duration_seconds=600, stabilization_time=0,
         camaras=[0, 1], nombre="Corte de luz", autofocus=False)
check("is_running() da True apenas se llama start()", tl.is_running())
esperar(lambda: tl.ciclo_actual >= 2 and estado.is_file()
        and json.loads(estado.read_text())["ciclo"] >= 2)
d = json.loads(estado.read_text())
carpeta = d["carpeta"]
check("anota carpeta, inicio, ciclo y parametros",
      Path(carpeta).is_dir() and d["ciclo"] >= 2 and d["params"]["modo"] == "dpc"
      and d["params"]["interval_seconds"] == 1 and d["params"]["nombre"] == "Corte de luz", d)
r = tl.resumen()
check("resumen para la pagina: nombre, modo, proxima y ultimas fotos",
      r["nombre"] == "Corte de luz" and r["modo"] == "dpc" and r["proxima"]
      and set(r["ultimas"]) == {"0", "1"} and r["ultimas"]["0"]["ciclo"] >= 1, r)
check("ultimas trae las 4 fotos DPC del ciclo", set(tl.ultimas[0]["rutas"]) == {"_L", "_R", "_T", "_B"})

print("\n=== VISTA PREVIA ===")
jpg = T.vista_previa(tl.ultimas[0]["rutas"], "dpc", size=100)
img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_GRAYSCALE)
check("DPC: JPEG achicado al tamaño pedido", img is not None and max(img.shape) == 100, None if img is None else img.shape)
check("DPC: muestra el relieve (L-R)/(L+R), no una foto suelta",
      img is not None and img[:, :10].mean() > 200 and img[:, -10:].mean() < 60,
      None if img is None else (img[:, :10].mean(), img[:, -10:].mean()))
jpg2 = T.vista_previa({"": tl.ultimas[0]["rutas"]["_T"]}, "blanco", size=80)
img2 = cv2.imdecode(np.frombuffer(jpg2, np.uint8), cv2.IMREAD_GRAYSCALE)
check("campo claro: la foto tal cual", img2 is not None and max(img2.shape) == 80)

print("\n=== SE CORTA LA LUZ ===")
fotos_antes = sorted(p.name for p in (Path(carpeta) / "cam0").glob("*.tif"))
cortar_la_luz(tl)
ciclos_antes = json.loads(estado.read_text())["ciclo"]
check("el archivo de estado sobrevive al corte", estado.is_file())
fotos_antes = sorted(p.name for p in (Path(carpeta) / "cam0").glob("*.tif"))

print("\n=== VUELVE LA LUZ: SE REANUDA SOLO ===")
tl2 = manager(tmp)
t0 = time.time()
r = tl2.reanudar_pendiente(en_hilo=False, espera_s=5, sincronizado=lambda: True)
check("reanuda", r is not None and tl2.is_running())
check("con la hora bien no espera", time.time() - t0 < 2, time.time() - t0)
check("en la MISMA carpeta", tl2.base_folder == carpeta)
check("la pagina ve la foto de antes del corte mientras tanto",
      tl2.ultimas.get(0, {}).get("ciclo") == ciclos_antes, tl2.ultimas.get(0))
esperar(lambda: tl2.ciclo_actual > ciclos_antes and tl2.ultimas.get(0, {}).get("ciclo", 0) > ciclos_antes)
nuevas = sorted(p.name for p in (Path(carpeta) / "cam0").glob("*.tif") if p.name not in fotos_antes)
check("la numeracion sigue donde quedo", nuevas and nuevas[0].startswith(f"{ciclos_antes + 1:04d}_"),
      (ciclos_antes, nuevas[:4]))
log = (Path(carpeta) / "timelapse.log").read_text()
check("el log cuenta que se reanudo", "Reanudado despues de un corte de luz" in log)
ej = json.loads((Path(carpeta) / "experimento.json").read_text())
check("experimento.json: en curso y con la hora de la reanudacion",
      ej["estado"] == "en curso" and len(ej.get("reanudaciones", [])) == 1, ej)
check("resumen() avisa que se reanudo", len(tl2.resumen()["reanudaciones"]) == 1)
csv = (Path(carpeta) / "temperatura.csv").read_text()
check("temperatura.csv no se pisa (una sola cabecera)", csv.count("timestamp,ciclo") == 1)
tl2.stop()
check("al detenerlo desde la pagina ya no queda nada que reanudar", not estado.is_file())
ej = json.loads((Path(carpeta) / "experimento.json").read_text())
check("y se cierra como siempre", ej["estado"] == "detenido antes de tiempo" and ej["n_fotos"] > 0, ej)
check("sin archivo de estado, al arrancar no hace nada", manager(tmp).reanudar_pendiente(en_hilo=False) is None)

print("\n=== SIGUE EN LA GRILLA ORIGINAL ===")
tl3 = manager(tmp)
tl3.start(modo="blanco", interval_seconds=4, duration_seconds=600, stabilization_time=0,
          camaras=[0], nombre="Grilla")
esperar(lambda: estado.is_file() and json.loads(estado.read_text())["ciclo"] >= 1)
cortar_la_luz(tl3)
d = json.loads(estado.read_text())
d["inicio_ts"] = time.time() - 10.4      # 10.4 s desde el inicio, intervalo 4 s
d["ultimo_ts"] = time.time() - 9
estado.write_text(json.dumps(d))
tl4 = manager(tmp)
tl4.reanudar_pendiente(en_hilo=False, espera_s=5, sincronizado=lambda: True)
esperar(lambda: tl4.ciclo_actual >= 2)
falta = tl4.proxima_wall - time.time()
# grilla: 0, 4, 8, 12, 16... la de 12 queda a 1.6 s (menos de medio
# intervalo de la foto de recien) -> se salta y la proxima es la de 16
check("una foto al volver y la siguiente en la grilla (16 s, no 12 ni 14.4)", 4.6 < falta < 6.2, round(falta, 2))
check("las fotos que se perdieron quedan en el log", "se perdieron ~2 foto(s)" in
      (Path(tl4.base_folder) / "timelapse.log").read_text())
tl4.stop()

print("\n=== SI LA DURACION SE CUMPLIO DURANTE EL CORTE ===")
tl5 = manager(tmp)
tl5.start(modo="blanco", interval_seconds=1, duration_seconds=600, stabilization_time=0,
          camaras=[0], nombre="Se cumplio")
esperar(lambda: estado.is_file() and json.loads(estado.read_text())["ciclo"] >= 1)
cortar_la_luz(tl5)
d = json.loads(estado.read_text())
d["inicio_ts"] = time.time() - 700
estado.write_text(json.dumps(d))
tl6 = manager(tmp)
r = tl6.reanudar_pendiente(en_hilo=False, espera_s=5, sincronizado=lambda: True)
ej = json.loads((Path(d["carpeta"]) / "experimento.json").read_text())
check("no saca mas fotos", r is None and not tl6.is_running())
check("cierra el experimento contando que fue durante el corte",
      "apagada" in ej.get("estado", "") and ej.get("fin"), ej.get("estado"))
check("y borra el estado", not estado.is_file())

print("\n=== CASOS RAROS ===")
estado.write_text(json.dumps({"carpeta": str(tmp / "no_existe"), "inicio_ts": time.time(),
                              "ciclo": 1, "params": {"modo": "blanco", "duration_seconds": 600}}))
t0 = time.time()
r = manager(tmp).reanudar_pendiente(en_hilo=False, espera_s=1, sincronizado=lambda: True)
check("carpeta que no aparece (memoria USB sacada): se rinde y lo borra",
      r is None and not estado.is_file() and time.time() - t0 < 4)
estado.write_text("{roto")
check("archivo roto: se descarta sin romper el arranque",
      manager(tmp).reanudar_pendiente(en_hilo=False) is None and not estado.is_file())

tl7 = manager(tmp)
tl7.start(modo="blanco", interval_seconds=1, duration_seconds=600, stabilization_time=0,
          camaras=[0], nombre="Reloj atrasado")
esperar(lambda: estado.is_file() and json.loads(estado.read_text())["ciclo"] >= 1)
cortar_la_luz(tl7)
d = json.loads(estado.read_text())
d["ultimo_ts"] = time.time() + 3600      # la Pi arranco con la hora vieja
d["params"]["duration_seconds"] = 100000
estado.write_text(json.dumps(d))
tl8 = manager(tmp)
t0 = time.time()
tl8.reanudar_pendiente(en_hilo=False, espera_s=2, sincronizado=lambda: True)
check("hora atrasada y sin red: espera un rato y reanuda igual", tl8.is_running() and time.time() - t0 >= 1.9)
tl8.stop()

print("\n=== DPC CALCULADO EN LA PI (feature/dpc-al-ciclo) + CORTE ===")
OPC = {"borrar_crudas": True, "suma": True, "fase": False, "jpg": True}
tla = manager(tmp)
tla.start(modo="dpc", interval_seconds=1, duration_seconds=600, stabilization_time=0,
          camaras=[0], nombre="DPC en la Pi", dpc_opts=OPC)
esperar(lambda: tla.ultimas.get(0, {}).get("dpc"))
cam0 = Path(tla.base_folder) / "cam0"
check("el DPC se calcula y las crudas se borran", list(cam0.glob("0001_*_dpcLR.tif"))
      and not list(cam0.glob("0001_*_L.tif")), sorted(p.name for p in cam0.iterdir())[:8])
check("la pagina se entera de que ya esta el relieve", tla.resumen()["ultimas"]["0"]["dpc"])
u = tla.ultimas[0]
jpg = T.vista_previa(u["rutas"], "dpc", size=90, base=u["base"])
img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
check("con las crudas borradas, la vista usa el _dpc.jpg a color",
      img is not None and max(img.shape[:2]) == 90, None if img is None else img.shape)
os.remove(u["base"] + "_dpc.jpg")
jpg = T.vista_previa(u["rutas"], "dpc", size=90, base=u["base"])
check("sin el jpg, usa el _dpcLR.tif", cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_GRAYSCALE) is not None)
check("las opciones de DPC quedan para reanudar",
      json.loads(estado.read_text())["params"]["dpc_opts"] == OPC)
tla.stop()
# Corte con el DPC atrasado: la Pi se apaga antes de procesar los ultimos
# ciclos (aca, un hilo de calculo que nunca llega a hacer nada).
tlc = manager(tmp)
tlc._procesar_dpc = lambda cola, op: [None for _ in iter(cola.get, None)]
tlc.start(modo="dpc", interval_seconds=1, duration_seconds=600, stabilization_time=0,
          camaras=[0], nombre="DPC atrasado", dpc_opts=OPC)
esperar(lambda: tlc.ciclo_actual >= 2 and json.loads(estado.read_text())["ciclo"] >= 2)
cam0 = Path(tlc.base_folder) / "cam0"
cortar_la_luz(tlc)
crudas = sorted(cam0.glob("*_L.tif"))
d = json.loads(estado.read_text())
check("(quedan ciclos sin procesar, con sus 4 crudas)", crudas and d["params"]["dpc_opts"] == OPC, len(crudas))
tlb = manager(tmp)
tlb.reanudar_pendiente(en_hilo=False, espera_s=5, sincronizado=lambda: True)
check("al reanudar, la vista arranca con la ultima de antes del corte",
      tlb.ultimas.get(0, {}).get("ciclo") == d["ciclo"], tlb.ultimas.get(0))
esperar(lambda: not list(cam0.glob("*_L.tif")) or tlb.ciclo_actual > d["ciclo"] + 3, tope=30)
tlb.stop()
log = (cam0.parent / "timelapse.log").read_text()
check("procesa lo que quedo pendiente del corte", "DPC pendiente de antes del corte" in log)
check("y al final no quedan crudas sueltas", not list(cam0.glob("*_[LRTB].tif")),
      sorted(p.name for p in cam0.glob("*_[LRTB].tif"))[:6])
nums = sorted({p.name[:4] for p in cam0.glob("*_dpcLR.tif")})
check("cada ciclo tiene su DPC, sin huecos", nums == [f"{i:04d}" for i in range(1, len(nums) + 1)], nums)

tl9 = manager(tmp)
hilo = tl9.reanudar_pendiente(en_hilo=True)
check("sin nada pendiente, en el arranque no lanza hilos", hilo is None)

print(f"\n{ok} PASS, {fail} FAIL")
sys.exit(1 if fail else 0)
