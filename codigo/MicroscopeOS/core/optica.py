"""Optica de cada camara: objetivo, aumento y la escala micras/pixel.

La escala es lo que convierte pixeles en micras: la usan los metadatos de
cada .tif (Fiji/ImageJ la leen sola), la barra de escala de la marca de
agua y el campo de vision que muestra la interfaz.

Se calcula como

    um_por_pixel = tamano_pixel_sensor_um / (aumento_objetivo * aumento_adicional)

que es una ESTIMACION: el aumento real en el sensor depende del tubo y
del adaptador. Si se mide con un portaobjetos micrometrico, se guarda en
um_por_pixel_medido y pasa a usarse ese valor (origen "medido").

Cada camara tiene su propia entrada: hoy hay un solo objetivo, pero cada
canal optico es independiente y podrian llevar objetivos distintos.
"""

import json
from pathlib import Path

ARCHIVO = Path(__file__).resolve().parent.parent / "profiles" / "optica.json"

# IMX219 (Raspberry Pi Camera v2): pixel de 1.12 um, 3280 x 2464.
SENSOR = {"modelo": "IMX219", "pixel_um": 1.12, "ancho_px": 3280, "alto_px": 2464}

# Objetivos que ofrece la interfaz. La apertura numerica es la tipica de
# un acromatico de ese aumento; se puede corregir a mano.
OBJETIVOS = [
    {"nombre": "4x",   "aumento": 4,   "na": 0.10},
    {"nombre": "10x",  "aumento": 10,  "na": 0.25},
    {"nombre": "20x",  "aumento": 20,  "na": 0.40},
    {"nombre": "40x",  "aumento": 40,  "na": 0.65},
    {"nombre": "60x",  "aumento": 60,  "na": 0.85},
    {"nombre": "100x", "aumento": 100, "na": 1.25},
]

POR_DEFECTO = {
    "objetivo": "20x",          # el que tiene montado hoy el microscopio
    "aumento": 20.0,
    "na": 0.40,
    "aumento_adicional": 1.0,   # adaptador / lente de tubo, si hay
    "pixel_um": SENSOR["pixel_um"],
    "um_por_pixel_medido": None,
}


def _limpiar(c):
    out = dict(POR_DEFECTO)
    out.update({k: v for k, v in (c or {}).items() if k in POR_DEFECTO})
    for k in ("aumento", "na", "aumento_adicional", "pixel_um"):
        try:
            out[k] = float(out[k])
        except (TypeError, ValueError):
            out[k] = POR_DEFECTO[k]
    out["aumento"] = max(0.1, out["aumento"])
    out["aumento_adicional"] = max(0.01, out["aumento_adicional"])
    out["pixel_um"] = max(0.01, out["pixel_um"])
    m = out.get("um_por_pixel_medido")
    try:
        out["um_por_pixel_medido"] = float(m) if m not in (None, "", 0) else None
    except (TypeError, ValueError):
        out["um_por_pixel_medido"] = None
    out["objetivo"] = str(out["objetivo"])[:20] or "20x"
    return out


def describir(c):
    """La config con lo calculado: escala, de donde sale y campo de vision."""
    c = _limpiar(c)
    estimado = c["pixel_um"] / (c["aumento"] * c["aumento_adicional"])
    umpx = c["um_por_pixel_medido"] or estimado
    return dict(c,
                um_por_pixel=round(umpx, 5),
                origen_escala="medido" if c["um_por_pixel_medido"] else "estimado",
                campo_um=[round(SENSOR["ancho_px"] * umpx, 1),
                          round(SENSOR["alto_px"] * umpx, 1)],
                sensor=SENSOR["modelo"],
                resolucion_px=[SENSOR["ancho_px"], SENSOR["alto_px"]])


class Optica:
    def __init__(self, archivo=ARCHIVO, camaras=(0, 1)):
        self.archivo = Path(archivo)
        self.camaras = tuple(camaras)
        self.config = {c: dict(POR_DEFECTO) for c in self.camaras}
        try:
            datos = json.loads(self.archivo.read_text())
            for k, v in datos.items():
                if int(k) in self.config:
                    self.config[int(k)] = _limpiar(v)
        except (OSError, ValueError):
            pass

    def de(self, cam):
        return describir(self.config.get(cam, POR_DEFECTO))

    def todas(self):
        return {str(c): self.de(c) for c in self.camaras}

    def cambiar(self, cam, cambios):
        if cam not in self.config:
            raise ValueError(f"camara invalida: {cam}")
        nueva = dict(self.config[cam])
        nueva.update(cambios or {})
        # Elegir un objetivo de la lista trae su aumento y apertura
        # (salvo que el pedido los mande explicitos).
        obj = next((o for o in OBJETIVOS if o["nombre"] == nueva.get("objetivo")), None)
        if obj and "aumento" not in (cambios or {}):
            nueva["aumento"] = obj["aumento"]
        if obj and "na" not in (cambios or {}):
            nueva["na"] = obj["na"]
        self.config[cam] = _limpiar(nueva)
        self._guardar()
        return self.de(cam)

    def _guardar(self):
        try:
            self.archivo.parent.mkdir(parents=True, exist_ok=True)
            self.archivo.write_text(json.dumps(
                {str(c): v for c, v in self.config.items()}, indent=2))
        except OSError as e:
            print(f"[optica] no se pudo guardar: {e}")
