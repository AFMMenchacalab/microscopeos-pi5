"""Reproducir y exportar un timelapse: cuadros por ciclo, video y OME-TIFF.

CUADROS
=======

Un timelapse guarda, por camara y por ciclo, una o varias imagenes segun
el modo: una sola (campo claro, oscuro, Rheinberg), las 4 medias
aperturas (_L _R _T _B) o lo que sale del DPC (_dpcLR, _dpcTB, _suma,
_fase). Cada uno de esos sufijos es un "canal". cuadros() arma, para un
canal, la lista de imagenes en orden de ciclo con su hora: es lo que usa
el reproductor de la pagina y lo que se exporta.

VIDEO (MP4 / GIF)
=================

Para la reunion de grupo. Todos los cuadros con el MISMO estiramiento de
contraste (sacado de varios cuadros a lo largo del experimento): si cada
cuadro se normalizara por su cuenta el video parpadearia y un cambio real
de brillo quedaria escondido. Con rotulos: tiempo desde el inicio, hora,
barra de escala y las notas del experimento en el momento en que se
escribieron. MP4 con ffmpeg (H.264, se abre en cualquier lado); si no hay
ffmpeg, MP4 de OpenCV (mp4v).

OME-TIFF
========

El formato abierto de microscopia (Open Microscopy Environment): un solo
archivo por camara con todo el timelapse como una pila T-Y-X, la escala
en micras y el tiempo de cada cuadro adentro. Fiji (Bio-Formats), napari
y QuPath lo abren directo con las unidades bien puestas. Se escribe como
BigTIFF (puede pasar de 4 GB) y comprimido sin perdida.

Las exportaciones van a <experimento>/exportados/ y corren en un hilo:
Trabajos lleva el progreso para la pagina. Una a la vez, para no ahogar a
la Pi mientras hay un timelapse corriendo.
"""

import json
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import tifffile

from core import metadatos
from core.experimentos import CARPETA_EXPORTADOS, leer_notas

# Orden de preferencia del canal que se muestra por defecto.
PREFERENCIA_CANAL = ["", "_dpcLR", "_suma", "_dpcTB", "_L", "_R", "_T", "_B", "_fase"]
NOMBRE_CANAL = {
    "": "foto", "_dpcLR": "relieve izq.-der.", "_dpcTB": "relieve arriba-abajo",
    "_suma": "foto normal (del relieve)", "_fase": "fase", "_L": "luz izquierda",
    "_R": "luz derecha", "_T": "luz arriba", "_B": "luz abajo",
}
_NOMBRE_RE = re.compile(
    r"^(\d{4})_(\d{4})-(\d\d)-(\d\d)_(\d\d)-(\d\d)-(\d\d)(_[A-Za-z]+)?\.tif$")


def _canal_y_hora(nombre):
    m = _NOMBRE_RE.match(nombre)
    if not m:
        return None
    g = m.groups()
    hora = datetime(int(g[1]), int(g[2]), int(g[3]), int(g[4]), int(g[5]), int(g[6]))
    return int(g[0]), hora, g[7] or ""


def canales(imagenes):
    """{camara: [canales disponibles, en orden de preferencia]}."""
    por_cam = {}
    for rel in imagenes:
        cam, _, nombre = rel.partition("/")
        info = _canal_y_hora(nombre)
        if info is None or not cam.startswith("cam"):
            continue
        por_cam.setdefault(int(cam[3:]), set()).add(info[2])
    orden = {c: i for i, c in enumerate(PREFERENCIA_CANAL)}
    return {cam: sorted(cs, key=lambda c: orden.get(c, 99)) for cam, cs in por_cam.items()}


def cuadros(imagenes, cam, canal=None):
    """[{ciclo, hora (iso), rel}] de esa camara y canal, en orden de ciclo.
    canal=None elige el preferido de los disponibles."""
    disponibles = canales(imagenes).get(cam, [])
    if canal is None:
        canal = disponibles[0] if disponibles else ""
    out = []
    for rel in imagenes:
        c, _, nombre = rel.partition("/")
        if c != f"cam{cam}":
            continue
        info = _canal_y_hora(nombre)
        if info is None or info[2] != canal:
            continue
        out.append({"ciclo": info[0], "hora": info[1].isoformat(timespec="seconds"),
                    "rel": rel})
    out.sort(key=lambda x: x["ciclo"])
    return canal, out


def _leer_gris(ruta):
    img = tifffile.imread(str(ruta))
    if img.ndim == 3:
        img = img[..., 0] if img.shape[-1] == 1 else cv2.cvtColor(
            img[..., :3].astype(np.float32), cv2.COLOR_RGB2GRAY)
    return img


def _rango_contraste(rutas, muestras=7):
    """Percentiles 0.5-99.5 de varios cuadros repartidos en el tiempo."""
    if not rutas:
        return 0.0, 1.0
    idx = np.unique(np.linspace(0, len(rutas) - 1, min(muestras, len(rutas))).astype(int))
    valores = []
    for i in idx:
        try:
            img = _leer_gris(rutas[i]).astype(np.float32)
            valores.append(img[::4, ::4].ravel())
        except Exception:
            continue
    if not valores:
        return 0.0, 1.0
    todo = np.concatenate(valores)
    lo, hi = np.percentile(todo, [0.5, 99.5])
    if hi <= lo:
        hi = lo + 1
    return float(lo), float(hi)


def _barra_escala_um(ancho_um):
    """Longitud "redonda" de la barra: ~20 % del ancho del cuadro."""
    objetivo = ancho_um * 0.2
    for v in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000):
        if v >= objetivo:
            return v
    return 5000


def _texto(img, texto, org, escala, grosor=1):
    cv2.putText(img, texto, org, cv2.FONT_HERSHEY_SIMPLEX, escala, 0, grosor + 2, cv2.LINE_AA)
    cv2.putText(img, texto, org, cv2.FONT_HERSHEY_SIMPLEX, escala, 255, grosor, cv2.LINE_AA)


def _duracion(segundos):
    segundos = int(max(0, segundos))
    h, m = divmod(segundos // 60, 60)
    return f"{h} h {m:02d} min" if h else f"{m} min {segundos % 60:02d} s"


class Trabajos:
    """Exportaciones en segundo plano, de a una."""

    def __init__(self):
        self.trabajos = {}
        self._lock = threading.Lock()
        self._uno_a_la_vez = threading.Lock()

    def lanzar(self, tipo, funcion, **kwargs):
        tid = uuid.uuid4().hex[:12]
        t = {"id": tid, "tipo": tipo, "estado": "en espera", "progreso": 0, "total": 0,
             "archivo": None, "error": None, "creado": time.time()}
        with self._lock:
            # Solo los ultimos 20.
            for viejo in sorted(self.trabajos, key=lambda k: self.trabajos[k]["creado"])[:-19]:
                self.trabajos.pop(viejo, None)
            self.trabajos[tid] = t

        def progreso(hecho, total):
            t["progreso"], t["total"] = hecho, total

        def correr():
            with self._uno_a_la_vez:
                t["estado"] = "en curso"
                try:
                    t["archivo"] = str(funcion(progreso=progreso, **kwargs))
                    t["estado"] = "listo"
                except Exception as e:
                    t["estado"], t["error"] = "error", str(e)
        threading.Thread(target=correr, daemon=True).start()
        return dict(t)

    def estado(self, tid):
        t = self.trabajos.get(tid)
        return dict(t) if t else None


def _salida(carpeta, cam, canal, extension):
    destino = Path(carpeta) / CARPETA_EXPORTADOS
    destino.mkdir(exist_ok=True)
    sufijo = canal.lstrip("_") or "foto"
    return destino / f"{Path(carpeta).name}_cam{cam}_{sufijo}{extension}"


def exportar_video(carpeta, imagenes, cam, canal=None, formato="mp4", fps=10,
                   ancho=1280, rotulos=True, progreso=None):
    """Arma el video y devuelve la ruta. formato: mp4 | gif."""
    carpeta = Path(carpeta)
    canal, lista = cuadros(imagenes, cam, canal)
    if len(lista) < 2:
        raise ValueError("Hacen falta al menos 2 fotos de esa cámara para un video")
    rutas = [carpeta / c["rel"] for c in lista]
    lo, hi = _rango_contraste(rutas)
    primero = _leer_gris(rutas[0])
    h0, w0 = primero.shape[:2]
    ancho = int(min(max(160, ancho), w0))
    if formato == "gif":
        ancho = min(ancho, 640)
    alto = int(round(h0 * ancho / w0))
    ancho -= ancho % 2
    alto -= alto % 2
    meta = metadatos.leer(rutas[0]) or {}
    umpx = ((meta.get("optica") or {}).get("um_por_pixel"))
    um_por_px_video = umpx * w0 / ancho if umpx else None
    t0 = datetime.fromisoformat(lista[0]["hora"])
    notas = []
    for n in leer_notas(carpeta):
        try:
            notas.append((datetime.fromisoformat(n["hora"]), n["texto"]))
        except (KeyError, ValueError):
            continue
    # Una nota se ve durante ~2 s de video desde el cuadro en que se escribio.
    cuadros_nota = max(1, int(round(fps * 2)))
    escala_txt = max(0.45, ancho / 1400)
    margen = max(8, ancho // 80)

    def cuadro(i):
        img = _leer_gris(rutas[i]).astype(np.float32)
        img = np.clip((img - lo) * (255.0 / (hi - lo)), 0, 255).astype(np.uint8)
        img = cv2.resize(img, (ancho, alto), interpolation=cv2.INTER_AREA)
        if rotulos:
            hora = datetime.fromisoformat(lista[i]["hora"])
            _texto(img, f"t = {_duracion((hora - t0).total_seconds())}",
                   (margen, margen + int(28 * escala_txt)), escala_txt * 1.1, 2)
            _texto(img, hora.strftime("%d/%m %H:%M"),
                   (margen, margen + int(60 * escala_txt)), escala_txt * 0.8)
            if um_por_px_video:
                largo_um = _barra_escala_um(ancho * um_por_px_video)
                largo_px = int(round(largo_um / um_por_px_video))
                y = alto - margen - 6
                cv2.rectangle(img, (margen - 1, y - 6), (margen + largo_px + 1, y + 1), 0, -1)
                cv2.rectangle(img, (margen, y - 5), (margen + largo_px, y), 255, -1)
                _texto(img, f"{largo_um:g} um", (margen, y - 12), escala_txt * 0.8)
            # Notas escritas entre el cuadro anterior y este, visibles un rato.
            visibles = []
            for j in range(max(0, i - cuadros_nota + 1), i + 1):
                ini = datetime.fromisoformat(lista[j - 1]["hora"]) if j > 0 else datetime.min
                fin = datetime.fromisoformat(lista[j]["hora"])
                visibles += [t for h, t in notas if ini < h <= fin]
            for k, t in enumerate(visibles[-2:]):
                _texto(img, t[:70], (margen, alto - margen - int((60 + 32 * k) * escala_txt)),
                       escala_txt * 0.9, 2)
        return img

    salida = _salida(carpeta, cam, canal, ".gif" if formato == "gif" else ".mp4")
    total = len(rutas)
    if formato == "gif":
        from PIL import Image
        imagenes_gif = []
        for i in range(total):
            imagenes_gif.append(Image.fromarray(cuadro(i)))
            if progreso:
                progreso(i + 1, total)
        imagenes_gif[0].save(salida, save_all=True, append_images=imagenes_gif[1:],
                             duration=int(1000 / max(1, fps)), loop=0, optimize=True)
        return salida

    tmp = salida.with_name(salida.stem + ".parcial.mp4")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        proc = subprocess.Popen(
            [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "gray",
             "-s", f"{ancho}x{alto}", "-r", str(fps), "-i", "-",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
             "-movflags", "+faststart", str(tmp)],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            for i in range(total):
                proc.stdin.write(cuadro(i).tobytes())
                if progreso:
                    progreso(i + 1, total)
        finally:
            proc.stdin.close()
            err = proc.stderr.read().decode(errors="replace")
            proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg falló: {err.strip()[:300]}")
    else:
        escritor = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), fps,
                                   (ancho, alto), True)
        if not escritor.isOpened():
            raise RuntimeError("OpenCV no pudo crear el MP4 (falta un codec)")
        try:
            for i in range(total):
                escritor.write(cv2.cvtColor(cuadro(i), cv2.COLOR_GRAY2BGR))
                if progreso:
                    progreso(i + 1, total)
        finally:
            escritor.release()
    tmp.replace(salida)
    return salida


def exportar_ome(carpeta, imagenes, cam, canal=None, reducir=1, nombre="",
                 intervalo_s=None, progreso=None):
    """Una pila OME-TIFF (T, Y, X) con la escala y el tiempo de cada cuadro."""
    carpeta = Path(carpeta)
    canal, lista = cuadros(imagenes, cam, canal)
    if not lista:
        raise ValueError("Esa cámara no tiene fotos en ese canal")
    reducir = max(1, int(reducir))
    rutas = [carpeta / c["rel"] for c in lista]
    primero = _leer_gris(rutas[0])
    dtype = primero.dtype
    h, w = primero.shape[:2]
    h2, w2 = h // reducir, w // reducir
    meta = metadatos.leer(rutas[0]) or {}
    umpx = (meta.get("optica") or {}).get("um_por_pixel")
    t0 = datetime.fromisoformat(lista[0]["hora"])
    delta_t = [(datetime.fromisoformat(c["hora"]) - t0).total_seconds() for c in lista]
    if intervalo_s is None and len(delta_t) > 1:
        intervalo_s = float(np.median(np.diff(delta_t)))

    def planos():
        for i, ruta in enumerate(rutas):
            try:
                img = _leer_gris(ruta)
            except Exception:
                img = None
            if img is None or img.shape[:2] != (h, w):
                img = np.zeros((h, w), dtype)
            img = img.astype(dtype, copy=False)
            if reducir > 1:
                img = cv2.resize(img, (w2, h2), interpolation=cv2.INTER_AREA)
            if progreso:
                progreso(i + 1, len(rutas))
            yield img

    om = {"axes": "TYX", "Name": f"{nombre or carpeta.name} · cámara {cam} · "
                                   f"{NOMBRE_CANAL.get(canal, canal)}"}
    if umpx:
        om.update(PhysicalSizeX=umpx * reducir, PhysicalSizeXUnit="µm",
                  PhysicalSizeY=umpx * reducir, PhysicalSizeYUnit="µm")
    if intervalo_s:
        om.update(TimeIncrement=float(intervalo_s), TimeIncrementUnit="s")
    salida = _salida(carpeta, cam, canal, f"{'_x' + str(reducir) if reducir > 1 else ''}.ome.tif")
    tmp = salida.with_name(salida.name.replace(".ome.tif", ".parcial.ome.tif"))

    def escribir(metadata):
        with tifffile.TiffWriter(tmp, bigtiff=True, ome=True) as tif:
            tif.write(planos(), shape=(len(rutas), h2, w2), dtype=dtype, metadata=metadata,
                      compression="zlib", photometric="minisblack")
    try:
        # El tiempo real de cada cuadro (no siempre es intervalo * n: hay
        # pausas y cortes de luz).
        escribir(dict(om, Plane={"DeltaT": delta_t, "DeltaTUnit": ["s"] * len(delta_t)}))
    except (TypeError, ValueError, KeyError):
        # Versiones viejas de tifffile no aceptan Plane: sin los tiempos
        # por cuadro, pero con el intervalo.
        escribir(om)
    tmp.replace(salida)
    return salida


def listar_exportados(carpeta):
    destino = Path(carpeta) / CARPETA_EXPORTADOS
    if not destino.is_dir():
        return []
    return [{"nombre": p.name, "bytes": p.stat().st_size,
             "hora": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")}
            for p in sorted(destino.iterdir())
            if p.is_file() and ".parcial." not in p.name]


def leer_ambiente(carpeta):
    """Series de temperatura, CO2, humedad y deriva del foco del
    experimento, mas sus notas y pausas, para la grafica de la pagina."""
    import csv
    carpeta = Path(carpeta)
    temp = {"hora": [], "temperatura": [], "setpoint": [], "co2_pct": [],
            "co2_setpoint_pct": [], "humedad": []}

    def num(v, factor=1.0):
        try:
            return round(float(v) * factor, 3) if v not in (None, "") else None
        except ValueError:
            return None

    def hora_ts(ts):
        try:
            return datetime.strptime(ts, "%Y%m%d_%H%M%S").isoformat(timespec="seconds")
        except (TypeError, ValueError):
            return None

    ruta = carpeta / "temperatura.csv"
    if ruta.is_file():
        with open(ruta, newline="") as f:
            for r in csv.DictReader(f):
                h = hora_ts(r.get("timestamp"))
                if h is None:
                    continue
                temp["hora"].append(h)
                temp["temperatura"].append(num(r.get("temperatura")))
                temp["setpoint"].append(num(r.get("setpoint")))
                temp["co2_pct"].append(num(r.get("co2_ppm"), 1e-4))
                temp["co2_setpoint_pct"].append(num(r.get("co2_setpoint_ppm"), 1e-4))
                temp["humedad"].append(num(r.get("humedad")))
    foco = {}
    ruta = carpeta / "autofoco.csv"
    if ruta.is_file():
        with open(ruta, newline="") as f:
            for r in csv.DictReader(f):
                h = hora_ts(r.get("timestamp"))
                if h is None:
                    continue
                cam = str(r.get("camara", ""))
                s = foco.setdefault(cam, {"hora": [], "deriva_um": [], "encontrado": []})
                s["hora"].append(h)
                s["deriva_um"].append(num(r.get("deriva_um")))
                s["encontrado"].append(r.get("encontrado", "1") != "0")
    try:
        datos_exp = json.loads((carpeta / "experimento.json").read_text())
    except Exception:
        datos_exp = {}
    return {"temperatura": temp, "foco": foco, "notas": leer_notas(carpeta),
            "pausas": datos_exp.get("pausas") or [],
            "inicio": datos_exp.get("inicio"), "fin": datos_exp.get("fin")}
