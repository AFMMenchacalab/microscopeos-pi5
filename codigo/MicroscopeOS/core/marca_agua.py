"""Marca de agua para las copias que se descargan «para compartir».

Los .tif originales NUNCA se tocan: son datos. La marca se dibuja solo
sobre la copia JPG/PNG que se genera al descargar para compartir (una
presentacion, una publicacion, publicidad del microscopio).

Elementos, cada uno se prende o apaga por separado:
- logo:   el de MicroscopeOS, o una imagen propia (profiles/logo.png).
- escala: barra de escala con su longitud en micras (necesita la escala
          de la optica, que viene en los metadatos de la foto).
- datos:  un recuadro con lo que se elija: experimento, fecha, objetivo,
          luz, camara, escala.

Hay combinaciones listas (PRESETS) para no tener que pensar.
"""

import io
import json
from datetime import datetime
from pathlib import Path

import numpy as np

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:          # sin Pillow se exporta sin marca
    Image = None

ARCHIVO = Path(__file__).resolve().parent.parent / "profiles" / "marca_agua.json"
LOGO = Path(__file__).resolve().parent.parent / "profiles" / "logo.png"

CAMPOS = ["experimento", "fecha", "objetivo", "escala", "luz", "camara"]
POSICIONES = ["arriba-izquierda", "arriba-derecha", "abajo-izquierda", "abajo-derecha"]
TAMANOS = {"pequeno": 0.75, "mediano": 1.0, "grande": 1.45}

POR_DEFECTO = {
    "activa": True,
    "logo": False,
    "escala": True,
    "datos": True,
    "campos": {"experimento": True, "fecha": True, "objetivo": True,
               "escala": True, "luz": True, "camara": False},
    "posicion": "abajo-derecha",
    "tamano": "mediano",
    "estilo": "oscuro",          # oscuro (fondo negro) | claro (fondo blanco)
}

PRESETS = {
    "ninguna":    {"activa": False},
    "escala":     {"activa": True, "logo": False, "escala": True, "datos": False},
    "cientifica": {"activa": True, "logo": False, "escala": True, "datos": True,
                   "campos": {"experimento": True, "fecha": True, "objetivo": True,
                              "escala": True, "luz": True, "camara": True}},
    "publicidad": {"activa": True, "logo": True, "escala": True, "datos": False},
}

MESES_CORTOS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago",
                "sep", "oct", "nov", "dic"]
LONGITUDES_UM = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000]


# ---------------- configuracion ----------------
def limpiar(cfg):
    out = json.loads(json.dumps(POR_DEFECTO))
    cfg = cfg or {}
    for k in ("activa", "logo", "escala", "datos"):
        if k in cfg:
            out[k] = bool(cfg[k])
    for k in CAMPOS:
        if k in (cfg.get("campos") or {}):
            out["campos"][k] = bool(cfg["campos"][k])
    if cfg.get("posicion") in POSICIONES:
        out["posicion"] = cfg["posicion"]
    if cfg.get("tamano") in TAMANOS:
        out["tamano"] = cfg["tamano"]
    if cfg.get("estilo") in ("oscuro", "claro"):
        out["estilo"] = cfg["estilo"]
    return out


def preset_de(cfg):
    """Que combinacion lista coincide con cfg ('personalizada' si ninguna):
    la que, aplicada, no cambia nada."""
    if not cfg["activa"]:
        return "ninguna"
    for nombre in ("escala", "cientifica", "publicidad"):
        if con_preset(cfg, nombre) == cfg and (
                nombre != "publicidad" or not cfg["datos"]) and (
                nombre != "escala" or not cfg["logo"]):
            return nombre
    return "personalizada"


def cargar(archivo=None):
    try:
        return limpiar(json.loads(Path(archivo or ARCHIVO).read_text()))
    except (OSError, ValueError):
        return limpiar({})


def guardar(cfg, archivo=None):
    cfg = limpiar(cfg)
    archivo = Path(archivo or ARCHIVO)
    archivo.parent.mkdir(parents=True, exist_ok=True)
    archivo.write_text(json.dumps(cfg, indent=2))
    return cfg


def con_preset(cfg, nombre):
    p = PRESETS.get(nombre)
    if p is None:
        raise ValueError(f"preset invalido: {nombre}")
    nuevo = dict(cfg, **{k: v for k, v in p.items() if k != "campos"})
    if "campos" in p:
        nuevo["campos"] = dict(cfg["campos"], **p["campos"])
    return limpiar(nuevo)


# ---------------- dibujo ----------------
def _fuente(px, negrita=False):
    nombres = (["DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "FreeSansBold.ttf"] if negrita
               else ["DejaVuSans.ttf", "LiberationSans-Regular.ttf", "FreeSans.ttf"])
    carpetas = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/liberation",
                "/usr/share/fonts/truetype/freefont", "/usr/share/fonts/truetype"]
    for n in nombres:
        for c in carpetas:
            p = Path(c) / n
            if p.is_file():
                return ImageFont.truetype(str(p), px)
    try:
        return ImageFont.load_default(px)
    except TypeError:
        return ImageFont.load_default()


def a_8bits(img16):
    """16 bits -> 8 bits con un estiramiento robusto (percentiles 0.5-99.5),
    para que la copia se vea como en el vivo."""
    a = np.asarray(img16, dtype=np.float32)
    lo, hi = np.percentile(a, (0.5, 99.5))
    if hi <= lo:
        hi = lo + 1
    return np.clip((a - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)


def _colores(estilo):
    if estilo == "claro":
        return (255, 255, 255, 190), (15, 23, 32, 255)
    return (0, 0, 0, 150), (255, 255, 255, 255)


def _caja(capa, x, y, w, h, fondo, radio):
    ImageDraw.Draw(capa).rounded_rectangle([x, y, x + w, y + h], radius=radio, fill=fondo)


def _ubicar(pos, W, H, w, h, m):
    x = m if "izquierda" in pos else W - w - m
    y = m if "arriba" in pos else H - h - m
    return x, y


def elegir_barra(um_por_pixel, ancho_px, fraccion=0.2):
    """La longitud redonda mas grande que entra en ~20% del ancho."""
    tope = ancho_px * fraccion * um_por_pixel
    candidatas = [l for l in LONGITUDES_UM if l <= tope]
    largo = candidatas[-1] if candidatas else LONGITUDES_UM[0]
    return largo, largo / um_por_pixel


def _texto_um(um):
    return f"{um / 1000:g} mm" if um >= 1000 else f"{um:g} µm"


def _logo_propio(alto):
    if not LOGO.is_file():
        return None
    try:
        im = Image.open(LOGO).convert("RGBA")
        return im.resize((max(1, round(im.width * alto / im.height)), alto))
    except Exception:
        return None


def _icono(draw, x, y, s, color):
    """El microscopio del logo (mismo dibujo que el icono de la pagina,
    en una grilla de 24x24 escalada a s px)."""
    k = s / 24.0
    w = max(2, round(1.8 * k))
    P = lambda a, b: (x + a * k, y + b * k)
    draw.ellipse([P(6.8, 3.8), P(11.2, 8.2)], outline=color, width=w)
    draw.line([P(9, 8.2), P(9, 11.4)], fill=color, width=w)
    draw.line([P(6.5, 20), P(15.5, 20)], fill=color, width=w)
    draw.arc([P(4.4, 8.8), P(16.0, 20.0)], start=-90, end=90, fill=color, width=w)
    draw.line([P(6, 14.6), P(12, 14.6)], fill=color, width=w)
    draw.line([P(15.2, 17.6), P(18.5, 17.6)], fill=color, width=w)


def aplicar(img, meta, cfg):
    """Dibuja la marca sobre una imagen PIL RGB y la devuelve."""
    cfg = limpiar(cfg)
    if not cfg["activa"] or Image is None:
        return img
    W, H = img.size
    f = TAMANOS[cfg["tamano"]]
    fpx = max(10, round(W * 0.016 * f))
    m = round(fpx * 0.9)
    pad = round(fpx * 0.55)
    radio = round(fpx * 0.35)
    fondo, tinta = _colores(cfg["estilo"])
    capa = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(capa)
    fuente = _fuente(fpx)
    fuente_b = _fuente(fpx, negrita=True)

    pos_datos = cfg["posicion"]
    lado_datos = "izquierda" if "izquierda" in pos_datos else "derecha"
    pos_escala = ("abajo-derecha" if pos_datos == "abajo-izquierda" else "abajo-izquierda")
    pos_logo = ("arriba-derecha" if pos_datos == "arriba-izquierda" else "arriba-izquierda")
    if not cfg["datos"]:
        pos_escala, pos_logo = "abajo-izquierda", "abajo-derecha"

    optica = meta.get("optica") or {}
    umpx = optica.get("um_por_pixel")
    # Si la copia es mas chica que el original, la escala se ajusta.
    orig = optica.get("resolucion_px") or [W, H]
    if umpx and orig and orig[0]:
        umpx = umpx * orig[0] / W

    # --- datos ---
    if cfg["datos"]:
        lineas = []
        c = cfg["campos"]
        exp = (meta.get("experimento") or {}).get("nombre")
        if c["experimento"] and exp:
            lineas.append((exp, True))
        if c["fecha"] and meta.get("fecha_hora"):
            try:
                t = datetime.fromisoformat(meta["fecha_hora"])
                lineas.append((f"{t.day} {MESES_CORTOS[t.month - 1]} {t.year} · {t:%H:%M}", False))
            except ValueError:
                pass
        if c["objetivo"] and optica.get("objetivo"):
            na = optica.get("na")
            lineas.append((f"Objetivo {optica['objetivo']}" + (f" · NA {na:g}" if na else ""), False))
        if c["escala"] and optica.get("um_por_pixel"):
            lineas.append((f"{optica['um_por_pixel']:.3g} µm/píxel", False))
        luz = (meta.get("iluminacion") or {}).get("nombre")
        if c["luz"] and luz:
            lineas.append((f"Luz: {luz}", False))
        cam = (meta.get("camara") or {}).get("numero")
        if c["camara"] and cam is not None:
            lineas.append((f"Cámara {cam}", False))
        if lineas:
            alto_l = round(fpx * 1.32)
            anchos = [d.textlength(t, font=fuente_b if b else fuente) for t, b in lineas]
            w = round(max(anchos)) + 2 * pad
            h = alto_l * len(lineas) + 2 * pad - round(fpx * 0.25)
            x, y = _ubicar(pos_datos, W, H, w, h, m)
            _caja(capa, x, y, w, h, fondo, radio)
            for i, (t, b) in enumerate(lineas):
                tx = x + pad if lado_datos == "izquierda" else x + w - pad - anchos[i]
                d.text((tx, y + pad + i * alto_l), t, font=fuente_b if b else fuente, fill=tinta)

    # --- barra de escala ---
    if cfg["escala"] and umpx:
        largo_um, largo_px = elegir_barra(umpx, W)
        etiqueta = _texto_um(largo_um)
        grosor = max(3, round(fpx * 0.38))
        tw = d.textlength(etiqueta, font=fuente_b)
        w = round(max(largo_px, tw)) + 2 * pad
        h = round(fpx * 1.25) + grosor + 2 * pad
        x, y = _ubicar(pos_escala, W, H, w, h, m)
        _caja(capa, x, y, w, h, fondo, radio)
        bx = x + (w - largo_px) / 2
        d.text((x + (w - tw) / 2, y + pad - round(fpx * 0.1)), etiqueta, font=fuente_b, fill=tinta)
        d.rectangle([bx, y + h - pad - grosor, bx + largo_px, y + h - pad], fill=tinta)

    # --- logo ---
    if cfg["logo"]:
        alto = round(fpx * 2.2)
        propio = _logo_propio(alto)
        if propio is not None:
            w, h = propio.width + 2 * pad, alto + 2 * pad
            x, y = _ubicar(pos_logo, W, H, w, h, m)
            _caja(capa, x, y, w, h, fondo, radio)
            capa.alpha_composite(propio, (round(x + pad), round(y + pad)))
        else:
            grande = _fuente(round(fpx * 1.45), negrita=True)
            t1, t2 = "Microscope", "OS"
            w1 = d.textlength(t1, font=grande)
            w2 = d.textlength(t2, font=grande)
            icono = round(fpx * 1.9)
            w = round(icono + fpx * 0.5 + w1 + w2) + 2 * pad
            h = icono + 2 * pad
            x, y = _ubicar(pos_logo, W, H, w, h, m)
            _caja(capa, x, y, w, h, fondo, radio)
            verde = (34, 197, 94, 255)
            _icono(d, x + pad, y + pad, icono, verde)
            ty = y + pad + (icono - round(fpx * 1.45)) / 2 - round(fpx * 0.12)
            d.text((x + pad + icono + fpx * 0.5, ty), t1, font=grande, fill=tinta)
            d.text((x + pad + icono + fpx * 0.5 + w1, ty), t2, font=grande, fill=verde)

    return Image.alpha_composite(img.convert("RGBA"), capa).convert("RGB")


def exportar(img16, meta, cfg, formato="jpg", ancho_max=None):
    """Copia para compartir: 8 bits, con la marca de agua. Devuelve bytes."""
    if Image is None:
        raise RuntimeError("falta Pillow (sudo apt install python3-pil)")
    im = Image.fromarray(a_8bits(img16)).convert("RGB")
    if ancho_max and im.width > ancho_max:
        im = im.resize((ancho_max, round(im.height * ancho_max / im.width)), Image.LANCZOS)
    im = aplicar(im, meta, cfg)
    buf = io.BytesIO()
    if formato == "png":
        im.save(buf, "PNG", optimize=True)
    else:
        im.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def muestra_sintetica(ancho=1640, alto=1232, semilla=3):
    """Imagen de ejemplo para la vista previa cuando aun no hay fotos."""
    r = np.random.default_rng(semilla)
    yy, xx = np.mgrid[0:alto, 0:ancho]
    img = np.full((alto, ancho), 30000.0)
    img -= ((xx - ancho / 2) ** 2 + (yy - alto / 2) ** 2) / (ancho * ancho) * 12000
    for _ in range(60):
        cx, cy, rad = r.uniform(0, ancho), r.uniform(0, alto), r.uniform(18, 40)
        dd = np.hypot(xx - cx, yy - cy)
        img -= 9000 * np.exp(-((dd - rad) / 5) ** 2) - 2500 * (dd < rad)
    img += r.normal(0, 400, img.shape)
    return np.clip(img, 0, 65535).astype(np.uint16)
