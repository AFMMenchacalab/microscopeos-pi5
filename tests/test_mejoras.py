"""Pruebas sin hardware de las funciones de uso diario:

- zona del autofoco
- alertas (Telegram/correo, con el envio reemplazado)
- pausa, notas y hora de termino del timelapse; CO2 en temperatura.csv
- reproductor y exportaciones (video MP4/GIF y OME-TIFF)
- grafica de condiciones del experimento
- saturacion e histograma del vivo
- quien esta conectado, control, bitacora y reservas (por la API)

    cd tests
    SP=$PWD PROY=$PWD/../codigo/MicroscopeOS python3 test_mejoras.py
"""
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("SP", AQUI))
sys.path.insert(0, os.environ.get("PROY", os.path.join(AQUI, "..", "codigo", "MicroscopeOS")))

import numpy as np
import tifffile

from core import alertas as AL
from core import autofocus as AF
from core import experimentos as E
from core import exportar as X
from core import metadatos as M
from core import timelapse as T
from core import usuarios as U

ok = fail = 0


def check(nombre, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {nombre}")
    else:
        fail += 1
        print(f"  FAIL  {nombre}  {extra}")


def esperar(cond, tope=20):
    t0 = time.time()
    while not cond() and time.time() - t0 < tope:
        time.sleep(0.05)
    return cond()


tmp = Path(tempfile.mkdtemp(prefix="mejoras_"))

# ---------------------------------------------------------------- zona
print("\n=== ZONA DEL AUTOFOCO ===")
check("normaliza y ordena la zona", AF.normalizar_zona([0.8, 0.9, 0.2, 0.1]) == [0.2, 0.1, 0.8, 0.9])
check("recorta a [0, 1]", AF.normalizar_zona([-1, -1, 0.5, 2]) == [0.0, 0.0, 0.5, 1.0])
check("rechaza una zona demasiado chica", AF.normalizar_zona([0.1, 0.1, 0.12, 0.5]) is None)
check("sin zona -> None", AF.normalizar_zona(None) is None and AF.normalizar_zona([1, 2]) is None)
img = np.arange(100 * 200, dtype=np.float32).reshape(100, 200)
r = AF.recortar(img, roi=0.8, zona=[0.5, 0.0, 1.0, 0.5])
check("con zona recorta exactamente ese rectangulo", r.shape == (50, 100) and r[0, 0] == img[0, 100])
check("sin zona usa el recorte centrado", AF.recortar(img, roi=0.5).shape == (50, 100))


class CamFoco:
    def __init__(self):
        self.frame = img

    def get_focus_frame(self, cam, descartar=1):
        return self.frame


AF.ARCHIVO_ZONA = tmp / "zona" / "autofoco_zona.json"
AF.ARCHIVO_CALIBRACION = tmp / "zona" / "autofoco_dpc.json"
af = AF.Autofocus(CamFoco(), {0: object()})
af.set_zona(0, [0.0, 0.0, 0.5, 0.5])
check("el frame del autofoco sale de la zona", af._frame(0, 0.8).shape == (50, 100))
check("salvo que se pida el campo completo (autofoco IA)",
      af._frame(0, 1.0, usar_zona=False).shape == (100, 200))
af2 = AF.Autofocus(CamFoco(), {0: object()})
check("la zona se guarda y se carga al reiniciar", af2.zonas.get(0) == [0.0, 0.0, 0.5, 0.5])
af2.set_zona(0, None)
check("se puede quitar", AF.Autofocus(CamFoco(), {}).zonas == {})
check("la nitidez tambien respeta la zona",
      AF.medir_nitidez(img, roi=0.6, zona=[0, 0, 0.5, 1]) ==
      AF.medir_nitidez(img[:, :100], roi=None))

# ---------------------------------------------------------------- alertas
print("\n=== ALERTAS ===")
enviados = []


class Incubadora:
    temperature, setpoint, co2, co2_setpoint, error_msg = 37.0, 37.0, 40000, 40000, None


inc = Incubadora()
corriendo = [True]
libre = [50e9]
al = AL.Alertas(archivo=tmp / "alertas.json", incubadora=inc, espacio=lambda: libre[0],
                timelapse_corriendo=lambda: corriendo[0],
                enviar=lambda canal, asunto, texto: enviados.append((canal, asunto, texto)))
al.revisar()
check("apagadas por defecto: no manda nada", enviados == [])
pub = al.actualizar({"activo": True, "telegram": {"activo": True, "token": "123:ABC", "chat_id": "42"},
                     "correo": {"activo": True, "servidor": "smtp.x", "para": "a@b.c",
                                "contrasena": "secreta"}})
check("la configuracion publica no muestra secretos",
      pub["telegram"]["token"] == "" and pub["telegram"]["token_guardado"]
      and pub["correo"]["contrasena"] == "" and pub["correo"]["contrasena_guardado"])
al.actualizar({"telegram": {"token": ""}, "correo": {"contrasena": ""}})
check("un secreto vacio conserva el guardado",
      al.config["telegram"]["token"] == "123:ABC" and al.config["correo"]["contrasena"] == "secreta")
check("guardado en disco",
      json.loads((tmp / "alertas.json").read_text())["telegram"]["chat_id"] == "42")
check("dos canales activos", al.canales() == ["telegram", "correo"])

inc.temperature = 39.5
al.revisar()
check("fuera de rango pero hace poco: todavia no avisa", enviados == [])
al._fuera_desde["temperatura"] -= 11 * 60
al.revisar()
time.sleep(0.2)
check("fuera de rango mas de 10 min: avisa por los dos canales",
      len(enviados) == 2 and "temperatura" in enviados[0][1] and "39.5" in enviados[0][2], enviados)
al._fuera_desde["temperatura"] -= 11 * 60
al.revisar()
time.sleep(0.2)
check("no repite enseguida", len(enviados) == 2)
inc.temperature = 37.1
al.revisar()
time.sleep(0.2)
check("al volver a rango manda «Resuelto»", len(enviados) == 4 and "Resuelto" in enviados[-1][1])
enviados.clear()
corriendo[0] = False
inc.temperature = 45
al._fuera_desde["temperatura"] = time.monotonic() - 3600
al.revisar()
time.sleep(0.2)
check("sin timelapse no vigila la incubadora (solo_con_timelapse)", enviados == [])
inc.temperature = 37
libre[0] = 1e9
al.revisar()
time.sleep(0.2)
check("poco disco avisa aunque no haya timelapse",
      len(enviados) == 2 and "espacio" in enviados[0][1])
enviados.clear()
al.autofoco_fallo(1, 2, 10, "no encontró el foco")
time.sleep(0.1)
check("2 fallos de autofoco no avisan", enviados == [])
al.autofoco_fallo(1, 3, 11, "no encontró el foco")
time.sleep(0.2)
check("3 fallos seguidos si", len(enviados) == 2 and "cámara 1" in enviados[0][2])
enviados.clear()
r = al.probar()
check("probar manda ya y dice como le fue a cada canal",
      r["resultados"] == {"telegram": "ok", "correo": "ok"} and len(enviados) == 2)
falla = AL.Alertas(archivo=tmp / "al2.json",
                   enviar=lambda c, a, t: (_ for _ in ()).throw(RuntimeError("sin red")))
falla.actualizar({"activo": True, "telegram": {"activo": True, "token": "t", "chat_id": "1"}})
check("si un canal falla, probar lo dice", falla.probar()["resultados"]["telegram"] == "sin red")

# ---------------------------------------------------------------- timelapse
print("\n=== TIMELAPSE: PAUSA, NOTAS, CO2 ===")


class Camara:
    def __init__(self, luces):
        self.luces = luces
        self.fotos = 0

    def capture_image(self, camera_num, folder, filename, meta=None):
        x = np.linspace(0, 1, 160, dtype=np.float32)[None, :].repeat(120, 0)
        tifffile.imwrite(filename, (1000 + 20000 * x).astype(np.uint16))
        self.fotos += 1
        return filename

    def stop_preview(self, *a):
        pass


class Luz:
    current_pattern = "OFF"
    color_dpc = color_campo = None

    def __getattr__(self, n):
        if n.startswith("_"):
            raise AttributeError(n)
        return lambda *a: None


import temperature_controller as TCmod
tc = TCmod.temperature_controller
tc.temperature, tc.setpoint, tc.pwm = 36.9, 37.0, 120
tc.co2, tc.co2_setpoint, tc.humidity = 41000, 40000, 88.5

luces = {0: Luz()}
cam = Camara(luces)
ex = E.Experimentos(raiz=tmp / "datos", legado=tmp)
tl = T.TimelapseManager(cam, luces, experimentos=ex)
avisos = []


class AlertasFalsas:
    def __getattr__(self, n):
        return lambda *a, **k: avisos.append((n, a))


tl.alertas = AlertasFalsas()
tl.start(modo="blanco", interval_seconds=1, duration_seconds=60, camaras=[0], nombre="Pausa")
check("arranca", esperar(lambda: cam.fotos >= 2, 10))
nota = tl.agregar_nota("agregué el fármaco", autor="ana@lab")
check("nota con ciclo y autor", nota["autor"] == "ana@lab" and nota["ciclo"] >= 1 and nota["tipo"] == "nota")
check("pausar", tl.pausar(autor="ana@lab") and tl.en_pausa())
check("no se puede pausar dos veces", tl.pausar() is False)
fotos_pausa = cam.fotos
time.sleep(2.5)
check("en pausa no toma fotos", cam.fotos == fotos_pausa, f"{fotos_pausa} -> {cam.fotos}")
res = tl.resumen()
check("el resumen dice que esta en pausa", res["pausado"] and res["pausas"][-1][1] is None)
check("continuar", tl.continuar(autor="ana@lab") and not tl.en_pausa())
check("al continuar toma una foto enseguida", esperar(lambda: cam.fotos > fotos_pausa, 2))
tl.stop()
carpeta = Path(tl.base_folder)
notas = E.leer_notas(carpeta)
check("notas.csv: nota, pausa y reanudar, en orden",
      [n["tipo"] for n in notas] == ["nota", "pausa", "reanudar"], notas)
exp_json = json.loads((carpeta / "experimento.json").read_text())
check("la pausa queda en experimento.json con inicio y fin",
      len(exp_json["pausas"]) == 1 and exp_json["pausas"][0][1])
cab = (carpeta / "temperatura.csv").read_text().splitlines()
check("temperatura.csv guarda CO2 y humedad",
      cab[0].endswith("co2_ppm,co2_setpoint_ppm,humedad") and cab[1].endswith(",41000,40000,88.5"), cab[:2])
check("avisa a las alertas al terminar", any(a[0] == "timelapse_fin" for a in avisos))
check("LEEME menciona las notas", "notas.csv" in (carpeta / "LEEME.txt").read_text())

amb = X.leer_ambiente(carpeta)
check("grafica: serie de temperatura y CO2 en %",
      amb["temperatura"]["temperatura"][0] == 36.9 and amb["temperatura"]["co2_pct"][0] == 4.1)
check("grafica: con notas y pausas", len(amb["notas"]) == 3 and len(amb["pausas"]) == 1)
(carpeta / "temperatura_vieja.csv").write_text("x")
viejo = tmp / "viejo"
viejo.mkdir()
(viejo / "temperatura.csv").write_text("timestamp,ciclo,temperatura,setpoint,pwm\n"
                                      "20261002_103000,1,37.00,37.0,100\n")
check("lee tambien el formato viejo sin CO2",
      X.leer_ambiente(viejo)["temperatura"]["co2_pct"] == [None])

print("\n=== TIMELAPSE: si el hilo se cae ===")
tl2 = T.TimelapseManager(cam, luces, experimentos=ex)
tl2.alertas = AlertasFalsas()
avisos.clear()


def explota(*a, **k):
    raise RuntimeError("disco lleno (simulado)")


tl2._escribir_metadatos = explota
tl2.start(modo="blanco", interval_seconds=1, duration_seconds=60, camaras=[0], nombre="Error")
check("queda parado, no 'corriendo' para siempre", esperar(lambda: not tl2.is_running(), 5))
check("avisa el error a las alertas", any(a[0] == "timelapse_error" for a in avisos))
check("el experimento queda cerrado con el motivo",
      "disco lleno" in json.loads((Path(tl2.base_folder) / "experimento.json").read_text())
      .get("estado", ""))

# ---------------------------------------------------------------- exportar
print("\n=== REPRODUCTOR Y EXPORTACIONES ===")
expdir = tmp / "datos" / "2026-10-02_1030_Video"
(expdir / "cam0").mkdir(parents=True)
(expdir / "cam1").mkdir()
t0 = datetime(2026, 10, 2, 10, 30, 0)
yy, xx = np.mgrid[0:120, 0:160]
for i in range(6):
    h = t0 + timedelta(minutes=5 * i)
    base = f"{i + 1:04d}_{h:%Y-%m-%d_%H-%M-%S}"
    frame = (5000 + 3000 * np.sin((xx + 8 * i) / 9.0) + 200 * i).astype(np.uint16)
    M.escribir(expdir / "cam0" / f"{base}_dpcLR.tif", frame,
               {"optica": {"um_por_pixel": 0.5, "objetivo": "20x"}})
    tifffile.imwrite(expdir / "cam0" / f"{base}_suma.tif", frame[::2, ::2])
    tifffile.imwrite(expdir / "cam1" / f"{base}.tif", frame)
E.agregar_nota(expdir, "cambio de medio", hora=t0 + timedelta(minutes=12))
imgs = ex.imagenes(expdir)
can = X.canales(imgs)
check("canales por camara, el relieve primero", can == {0: ["_dpcLR", "_suma"], 1: [""]}, can)
canal, cs = X.cuadros(imgs, 0)
check("cuadros del canal preferido, en orden y con hora",
      canal == "_dpcLR" and [c["ciclo"] for c in cs] == [1, 2, 3, 4, 5, 6]
      and cs[1]["hora"] == "2026-10-02T10:35:00")
check("otro canal a pedido", len(X.cuadros(imgs, 0, "_suma")[1]) == 6)

prog = []
mp4 = X.exportar_video(expdir, imgs, 0, formato="mp4", fps=5, ancho=160,
                       progreso=lambda a, b: prog.append((a, b)))
check("MP4 creado en exportados/", mp4.is_file() and mp4.parent.name == "exportados"
      and mp4.stat().st_size > 1000)
check("con progreso", prog[-1] == (6, 6))
import cv2
v = cv2.VideoCapture(str(mp4))
n_frames = int(v.get(cv2.CAP_PROP_FRAME_COUNT))
v.release()
check("el MP4 tiene un cuadro por ciclo", n_frames == 6, n_frames)
gif = X.exportar_video(expdir, imgs, 1, formato="gif", fps=4, ancho=160)
from PIL import Image
check("GIF animado", Image.open(gif).n_frames == 6)
check("los exportados no aparecen como fotos de la galeria",
      not any(r.startswith("exportados/") for r in ex.imagenes(expdir)))
try:
    X.exportar_video(expdir, imgs, 3)
    check("sin fotos de esa camara da un error claro", False)
except ValueError as e:
    check("sin fotos de esa camara da un error claro", "2 fotos" in str(e))

ome = X.exportar_ome(expdir, imgs, 0, nombre="Video", intervalo_s=300)
with tifffile.TiffFile(ome) as t:
    pila = t.asarray()
    om = t.ome_metadata
check("OME-TIFF: una pila T,Y,X con los 6 cuadros", pila.shape == (6, 120, 160) and pila.dtype == np.uint16)
check("OME-TIFF: escala en micras", 'PhysicalSizeX="0.5"' in om and "PhysicalSizeXUnit" in om, om[:300])
check("OME-TIFF: intervalo y tiempo de cada cuadro",
      'TimeIncrement="300' in om and 'DeltaT="1500' in om)
check("OME-TIFF: los datos son los originales, sin tocar",
      np.array_equal(pila[2], tifffile.imread(expdir / cs[2]["rel"])))
ome2 = X.exportar_ome(expdir, imgs, 0, reducir=2)
with tifffile.TiffFile(ome2) as t:
    check("OME-TIFF reducido a la mitad, con la escala corregida",
          t.series[0].shape == (6, 60, 80) and 'PhysicalSizeX="1.0"' in t.ome_metadata)
check("lista de exportados", {e["nombre"] for e in X.listar_exportados(expdir)} ==
      {mp4.name, gif.name, ome.name, ome2.name})

tr = X.Trabajos()
t = tr.lanzar("video", X.exportar_video, carpeta=expdir, imagenes=imgs, cam=0, formato="gif", ancho=120)
check("los trabajos corren en segundo plano y terminan",
      esperar(lambda: tr.estado(t["id"])["estado"] == "listo", 30), tr.estado(t["id"]))
t = tr.lanzar("video", X.exportar_video, carpeta=expdir, imagenes=imgs, cam=7)
check("un trabajo que falla queda con el error",
      esperar(lambda: tr.estado(t["id"])["estado"] == "error", 10)
      and "2 fotos" in tr.estado(t["id"])["error"])

# ---------------------------------------------------------------- saturacion
print("\n=== SATURACION DEL VIVO ===")
import types
import emuladores
_pc = types.ModuleType("picamera2")       # solo la camara falsa: tifffile sigue siendo el real
_pc.Picamera2 = emuladores.Picamera2Fake
sys.modules["picamera2"] = _pc
from core.camera import CameraController
cc = CameraController()
frame = np.full((480, 640, 3), 100, np.uint8)
frame[:48, :] = 255
cc._estadisticas_vivo(0, frame)
e0 = cc.estadisticas_vivo()[0]
check("% saturado", abs(e0["saturados_pct"] - 10) < 1, e0["saturados_pct"])
check("histograma de 32 barras", len(e0["histograma"]) == 32 and sum(e0["histograma"]) > 0)
check("sin marcar no devuelve mascara", cc._estadisticas_vivo(0, frame) is None)
cc.marcar_saturados(0, True)
m = cc._estadisticas_vivo(0, frame)
check("marcando devuelve la mascara de lo saturado", m is not None and m.sum() == 48 * 640)
check("y el estado lo dice", cc.estadisticas_vivo()[0]["marcar"])

# ---------------------------------------------------------------- usuarios
print("\n=== QUIEN ESTA CONECTADO, CONTROL Y BITACORA ===")
u = U.Usuarios(bitacora=tmp / "bit.jsonl", reservas=tmp / "res.json", inactivo_s=1)
ana = U.Usuarios.identificar({"cf-access-authenticated-user-email": "Ana@Lab.mx"}, "1.2.3.4")
beto = U.Usuarios.identificar({}, "192.168.1.9")
check("identifica por el correo de Cloudflare Access", ana["id"] == "ana@lab.mx" and ana["remoto"])
check("sin correo, por la IP de la red local", beto["id"] == "local:192.168.1.9" and not beto["remoto"])
check("requiere control: mover el foco si", U.requiere_control("POST", "/api/focus/move"))
check("requiere control: mirar o anotar no",
      not U.requiere_control("GET", "/api/focus/status")
      and not U.requiere_control("POST", "/api/exp/x/nota")
      and not U.requiere_control("POST", "/live/start/0")
      and not U.requiere_control("POST", "/timelapse/nota"))
check("borrar un experimento si", U.requiere_control("POST", "/api/exp/x/borrar"))
check("cambiar el CO2 si", U.requiere_control("POST", "/api/temperature/co2_setpoint"))
u.visto(ana); u.visto(beto)
check("el primero que hace algo toma el control", u.puede(ana) == (True, None))
check("el segundo no puede", u.puede(beto) == (False, "ana@lab.mx"))
st = u.tomar(beto)
check("tomar el control", st["tengo_control"] and u.puede(ana)[0] is False)
check("a quien se lo quitaron le avisa", u.estado(ana)["me_lo_quitaron"] == beto["nombre"])
time.sleep(1.2)
check("si quien lo tiene se va, se suelta solo", u.puede(ana) == (True, None))
for _ in range(5):
    u.registrar(ana, "/api/focus/jog")
u.registrar(ana, "/timelapse/start")
b = u.bitacora()
check("bitacora: el joystick repetido es una sola linea",
      [x["accion"] for x in b][:3] == ["/timelapse/start", "/api/focus/jog", "control"], b[:3])
manana = (datetime.now() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
r1 = u.reservar(ana, manana.isoformat(), (manana + timedelta(hours=3)).isoformat(), "migración")
check("reservar un turno", r1["usuario"] == "ana@lab.mx" and len(u.reservas()) == 1)
try:
    u.reservar(beto, (manana + timedelta(hours=2)).isoformat(), (manana + timedelta(hours=4)).isoformat())
    check("no deja reservar encima de otro turno", False)
except ValueError as e:
    check("no deja reservar encima de otro turno", "Se cruza" in str(e))
ahora = datetime.now()
u.reservar(beto, (ahora - timedelta(minutes=5)).isoformat(), (ahora + timedelta(hours=1)).isoformat())
check("turno en curso", u.reserva_actual()["usuario_id"] == beto["id"]
      and u.estado(ana)["reserva_de_otro"])
u.cancelar_reserva(ana, r1["id"])
check("cancelar", len(u.reservas()) == 1)

# ---------------------------------------------------------------- API
print("\n=== API ===")
from fastapi.testclient import TestClient
import server.api as A


class CamApi:
    _preview_cams = set()

    def __init__(self):
        self.sat = {}

    def estadisticas_vivo(self):
        return {0: {"saturados_pct": 3.5, "histograma": [1] * 32, "media": 90}}

    def marcar_saturados(self, cam, activo):
        self.sat[cam] = activo

    def stop_preview(self, *a):
        pass

    def start_preview(self, cam):
        pass


class LuzApi(Luz):
    brightness_percent = 80


luces_api = {0: LuzApi()}
cam_api = Camara(luces_api)
cam_api.estadisticas_vivo = CamApi().estadisticas_vivo
cam_api._preview_cams = set()
cam_api.marcados = {}
cam_api.marcar_saturados = lambda c, a: cam_api.marcados.__setitem__(c, a)
cam_api.start_preview = lambda c: None
ex_api = E.Experimentos(raiz=tmp / "api" / "datos", legado=tmp / "api")
tl_api = T.TimelapseManager(cam_api, luces_api, experimentos=ex_api)
us_api = U.Usuarios(bitacora=tmp / "api_bit.jsonl", reservas=tmp / "api_res.json")
al_api = AL.Alertas(archivo=tmp / "api_alertas.json", enviar=lambda *a: None)
app = A.create_app(cam_api, luces_api, tl_api, experimentos=ex_api, usuarios=us_api,
                   alertas=al_api)
cl = TestClient(app)
ANA = {"cf-access-authenticated-user-email": "ana@lab.mx"}
BETO = {"cf-access-authenticated-user-email": "beto@lab.mx"}

r = cl.post("/light/off", headers=ANA)
check("ana cambia la luz", r.status_code == 200)
r = cl.post("/light/off", headers=BETO)
check("beto no puede mientras ana tiene el control (423)",
      r.status_code == 423 and "ana@lab.mx" in r.json()["error"])
check("pero beto puede mirar", cl.get("/api/vivo/estadisticas", headers=BETO).json()
      ["camaras"]["0"]["saturados_pct"] == 3.5)
st = cl.post("/api/control/tomar", headers=BETO).json()
check("beto toma el control", st["tengo_control"] and st["control"]["nombre"] == "beto@lab.mx")
check("y ahora ana no", cl.post("/light/off", headers=ANA).status_code == 423)
est = cl.get("/api/control", headers=ANA).json()
check("ana ve quien controla y que se lo quitaron",
      est["control"]["id"] == "beto@lab.mx" and est["me_lo_quitaron"] == "beto@lab.mx"
      and {c["id"] for c in est["conectados"]} >= {"ana@lab.mx", "beto@lab.mx"})
bit = cl.get("/api/bitacora").json()["bitacora"]
check("la bitacora tiene las acciones con su autor",
      any(x["usuario"] == "beto@lab.mx" and x["accion"] == "control"
          and "lo tenía ana@lab.mx" in x["detalle"] for x in bit)
      and not any(x["accion"].startswith("/api/control/") for x in bit))

r = cl.post("/api/vivo/saturacion", json={"camera": 0, "marcar": True}, headers=BETO).json()
check("marcar saturacion por la API", r["marcar"] and cam_api.marcados == {0: True})

fin = (datetime.now() + timedelta(hours=2)).isoformat(timespec="minutes")
r = cl.post("/timelapse/start", json={"modo": "blanco", "interval": 60, "fin": fin, "camaras": [0],
                                      "nombre": "API"}, headers=BETO).json()
check("hora de termino: arranca", r.get("status") == "started", r)
dur = tl_api.config["duration_seconds"]
check("la duracion sale de la hora de termino (~2 h)", 7100 < dur <= 7200, dur)
r = cl.post("/timelapse/nota", json={"texto": "  pipeteé  "}, headers=ANA).json()
check("cualquiera puede anotar, queda su nombre", r["nota"]["autor"] == "ana@lab.mx"
      and r["nota"]["texto"] == "pipeteé")
check("pausar por la API", cl.post("/timelapse/pausar", headers=BETO).json()["status"] == "pausado")
check("en pausa se puede encender el vivo",
      "error" not in cl.post("/live/start/0", headers=BETO).json())
st = cl.get("/status").json()
check("/status dice que esta en pausa", st["timelapse"]["pausado"])
check("continuar", cl.post("/timelapse/continuar", headers=BETO).json()["status"] == "continuando")
ident = Path(tl_api.base_folder).name
cl.post("/timelapse/stop", headers=BETO)
notas = cl.get(f"/api/exp/{ident}/notas").json()["notas"]
check("las notas se leen por la API", [n["tipo"] for n in notas] == ["nota", "pausa", "reanudar"])
r = cl.post(f"/api/exp/{ident}/nota", json={"texto": "revisado"}, headers=ANA).json()
check("nota en un experimento ya terminado", r["nota"]["ciclo"] == "")
amb = cl.get(f"/api/exp/{ident}/ambiente").json()
check("condiciones del experimento por la API", len(amb["temperatura"]["hora"]) >= 1)
r = cl.post("/timelapse/start", json={"interval": 300,
                                      "fin": (datetime.now() + timedelta(minutes=1)).isoformat()},
            headers=BETO).json()
check("una hora de termino antes del primer intervalo se rechaza", "error" in r)

# reproductor y exportaciones por la API
shutil.copytree(expdir, ex_api.raiz / expdir.name)
shutil.rmtree(ex_api.raiz / expdir.name / "exportados")
iv = expdir.name
c = cl.get(f"/api/exp/{iv}/cuadros", params={"camara": 0}).json()
check("cuadros por la API", c["canal"] == "_dpcLR" and len(c["cuadros"]) == 6
      and c["canales"]["0"][0]["nombre"].startswith("relieve"))
t = cl.post(f"/api/exp/{iv}/video", json={"camara": 0, "formato": "mp4", "ancho": 160}).json()
check("video por la API: termina",
      esperar(lambda: cl.get(f"/api/trabajos/{t['id']}").json()["estado"] == "listo", 30))
nombre = cl.get(f"/api/trabajos/{t['id']}").json()["nombre"]
d = cl.get(f"/api/exp/{iv}/exportado/{nombre}")
check("y se descarga", d.status_code == 200 and d.headers["content-type"] == "video/mp4"
      and len(d.content) > 1000)
t = cl.post(f"/api/exp/{iv}/ome", json={"camara": 0, "reducir": 2}).json()
check("OME-TIFF por la API",
      esperar(lambda: cl.get(f"/api/trabajos/{t['id']}").json()["estado"] == "listo", 30))
check("no deja descargar fuera de exportados/",
      cl.get(f"/api/exp/{iv}/exportado/..%2Fexperimento.json").status_code == 404)
info = cl.get(f"/api/exp/{iv}/info/" + c["cuadros"][0]["rel"]).json()
check("info de la foto con su tamaño en pixeles (para medir)",
      info["ancho_px"] == 160 and info["alto_px"] == 120)

# alertas por la API
r = cl.post("/api/alertas/config", json={"config": {"activo": True, "minutos_fuera": 0}},
            headers=BETO).json()
check("config de alertas por la API (y valida minimos)", r["activo"] and r["minutos_fuera"] == 1)
check("alertas: lectura sin secretos", cl.get("/api/alertas").json()["telegram"]["token"] == "")
check("sin canales, probar dice que falta configurar",
      "error" in cl.post("/api/alertas/probar", headers=BETO).json())

# reservas por la API
r = cl.post("/api/reservas", json={"inicio": manana.isoformat(), "fin": (manana + timedelta(hours=1)).isoformat(),
                                   "nota": "x"}, headers=ANA).json()
check("reservar por la API", r["reserva"]["usuario"] == "ana@lab.mx")
check("listar reservas", len(cl.get("/api/reservas").json()["reservas"]) == 1)

# CO2 de la incubadora: la pagina llamaba a esta ruta y daba 404
r = cl.post("/api/temperature/co2_setpoint", json={"value": 50000}, headers=BETO)
check("co2_setpoint existe y sin Arduino dice por que",
      r.status_code == 200 and r.json()["ok"] is False
      and r.json()["error"] == "Arduino no conectado", r.text)
r = cl.post("/api/temperature/co2_setpoint", json={"value": 200000}, headers=BETO).json()
check("co2_setpoint fuera de rango explica el rango",
      r["ok"] is False and "400" in r["error"] and "100000" in r["error"], r)
check("co2_setpoint pide el control",
      cl.post("/api/temperature/co2_setpoint", json={"value": 50000},
              headers=ANA).status_code == 423)

shutil.rmtree(tmp, ignore_errors=True)
print("\n" + "=" * 50)
print(f"PASS: {ok}   FAIL: {fail}")
print("=" * 50)
sys.exit(1 if fail else 0)
