"""Metadatos dentro de cada imagen .tif.

Cada foto lleva adentro, sin que se vea en la imagen, todo lo necesario
para interpretarla despues: objetivo, escala, luz, exposicion, foco,
temperatura y de que experimento sale.

Se guardan de tres formas, para que los lea cualquier programa:

1. Formato ImageJ: resolucion en pixeles por micra y unidad "um". Fiji e
   ImageJ abren la imagen ya con la escala puesta (Analyze > Set Scale
   aparece lleno) y muestran el resto en Image > Show Info.
2. Etiquetas TIFF estandar: Software, Make/Model (camara), DateTime.
3. Una etiqueta privada (65000) con todo en JSON, que es la que lee
   MicroscopeOS (galeria, marca de agua) con leer().

La imagen en si NO se toca: son los mismos datos de 16 bits de siempre.
"""

import json
from datetime import datetime

import numpy as np
import tifffile

ETIQUETA_JSON = 65000
SOFTWARE = "MicroscopeOS"

# Nombres para personas de los patrones de luz (lo que manda la matriz).
NOMBRES_LUZ = {
    "FULL": "Normal (campo claro)",
    "LEFT": "Relieve DPC, desde la izquierda",
    "RIGHT": "Relieve DPC, desde la derecha",
    "TOP": "Relieve DPC, desde arriba",
    "BOTTOM": "Relieve DPC, desde abajo",
    "RING": "Fondo negro (campo oscuro)",
    "RHEINBERG": "De colores (Rheinberg)",
    "OFF": "Apagada",
}


def _texto_info(meta, prefijo=""):
    """Aplana el dict a lineas 'clave = valor' (Image > Show Info)."""
    lineas = []
    for k, v in meta.items():
        if isinstance(v, dict):
            lineas += _texto_info(v, f"{prefijo}{k}.")
        elif v is not None:
            if isinstance(v, (list, tuple)):
                v = " x ".join(str(x) for x in v)
            lineas.append(f"{prefijo}{k} = {v}")
    return lineas


def escribir(ruta, imagen, meta=None, comprimir=False):
    """Guarda `imagen` (uint16 2D) en `ruta` con los metadatos.

    comprimir: deflate con predictor horizontal, sin perdida. Lo abren
    Fiji/ImageJ y tifffile sin imagecodecs. Las crudas de la camara se
    guardan sin comprimir (escribirlas tiene que ser instantaneo); lo que
    se calcula despues, en un hilo aparte, si se comprime."""
    meta = dict(meta or {})
    meta.setdefault("software", SOFTWARE)
    meta.setdefault("fecha_hora", datetime.now().isoformat(timespec="seconds"))
    umpx = (meta.get("optica") or {}).get("um_por_pixel")

    ij = {"Info": "\n".join(_texto_info(meta))}
    kw = {"compression": "zlib", "predictor": True} if comprimir else {}
    if umpx:
        ij["unit"] = "um"
        kw["resolution"] = (1.0 / umpx, 1.0 / umpx)
    camara = meta.get("camara") or {}
    extras = [
        (271, "s", 0, "MicroscopeOS", True),                             # Make
        (272, "s", 0, str(camara.get("sensor", "camara")), True),        # Model
        (306, "s", 0, datetime.now().strftime("%Y:%m:%d %H:%M:%S"), True),  # DateTime
        (ETIQUETA_JSON, "s", 0, json.dumps(meta, ensure_ascii=True), True),
    ]
    tifffile.imwrite(ruta, np.ascontiguousarray(imagen), imagej=True,
                     metadata=ij, extratags=extras, software=SOFTWARE, **kw)
    return ruta


def leer(ruta):
    """Los metadatos de MicroscopeOS de un .tif ({} si no tiene)."""
    try:
        with tifffile.TiffFile(ruta) as t:
            tag = t.pages[0].tags.get(ETIQUETA_JSON)
            if tag is not None:
                return json.loads(tag.value)
    except Exception:
        pass
    return {}


class Contexto:
    """Junta, en el momento de cada foto, lo que saben las distintas
    partes del microscopio. Lo usa CameraController.capture_image: asi
    toda foto (suelta o de timelapse) sale con metadatos sin que cada
    llamador tenga que armarlos."""

    def __init__(self, optica=None, illuminations=None, motores=None,
                 camera=None, temperatura=None):
        self.optica = optica
        self.illuminations = illuminations or {}
        self.motores = motores or {}
        self.camera = camera
        self.temperatura = temperatura      # funcion -> dict (status())

    def para(self, cam, extra=None):
        meta = {"software": SOFTWARE,
                "fecha_hora": datetime.now().isoformat(timespec="seconds")}
        if self.camera is not None:
            meta["camara"] = {"numero": cam, "sensor": "IMX219",
                              "exposicion_us": getattr(self.camera, "exposure_time", None),
                              "ganancia": getattr(self.camera, "gain", None)}
        if self.optica is not None:
            o = self.optica.de(cam)
            meta["optica"] = {k: o[k] for k in (
                "objetivo", "aumento", "na", "aumento_adicional", "pixel_um",
                "um_por_pixel", "origen_escala", "campo_um", "resolucion_px")}
        luz = self.illuminations.get(cam)
        if luz is not None:
            patron = getattr(luz, "current_pattern", None) or "OFF"
            i = {"patron": patron, "nombre": NOMBRES_LUZ.get(patron, patron),
                 "brillo_pct": getattr(luz, "brightness_percent", None)}
            color = getattr(luz, "current_color", None)
            if patron in ("LEFT", "RIGHT", "TOP", "BOTTOM"):
                color = color or getattr(luz, "color_dpc", None)
            if patron == "RHEINBERG":
                c = getattr(luz, "_rheinberg_colors", None)
                if c:
                    i["color_centro"], i["color_anillo"] = c[0], c[1]
            elif color:
                i["color"] = color
            meta["iluminacion"] = i
        m = self.motores.get(cam)
        if m is not None:
            meta["foco"] = {"posicion_um": round(getattr(m, "posicion_um", 0.0), 2)}
        if self.temperatura is not None:
            try:
                t = self.temperatura() or {}
                meta["incubadora"] = {k: t.get(k) for k in (
                    "temperature", "setpoint", "co2", "humidity") if t.get(k) is not None}
            except Exception:
                pass
        if extra:
            for k, v in extra.items():
                if isinstance(v, dict) and isinstance(meta.get(k), dict):
                    meta[k].update(v)
                else:
                    meta[k] = v
        return meta
