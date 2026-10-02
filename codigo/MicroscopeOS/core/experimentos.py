"""Donde y como se guardan las fotos.

Todo lo que toma el microscopio queda en un EXPERIMENTO: una carpeta
dentro de datos/ con un nombre que se entiende al verlo en cualquier
computadora, memoria USB o NAS:

    datos/
      2026-10-02_1030_Celulas_dia_1/      <- un timelapse
        LEEME.txt                         <- que es cada cosa, para personas
        experimento.json                  <- lo mismo, para programas
        cam0/0001_2026-10-02_10-30-00.tif
        cam0/0002_2026-10-02_10-35-00.tif
        cam1/...
        timelapse.log, temperatura.csv, autofoco.csv, ...
      2026-10-02_Fotos_sueltas/           <- fotos tomadas con «Tomar foto»
        cam0/2026-10-02_11-02-13.tif
      2026-10-02_Muestra_B/               <- fotos sueltas con nombre

- La fecha va primero para que cualquier explorador de archivos los
  ordene solo.
- Las fotos sueltas sin nombre de un mismo dia van juntas; con nombre,
  a una carpeta con ese nombre (la del mismo dia, si ya existe).
- Borrar no borra: mueve a datos/.papelera, de donde se puede deshacer.
  Lo que lleva mas de PAPELERA_DIAS en la papelera se borra de verdad.
- Las carpetas de versiones anteriores (timelapse_AAAAMMDD_HHMMSS y
  capturas_unicas, junto al codigo) se siguen mostrando; al renombrarlas
  se mudan a datos/.
"""

import json
import os
import re
import shutil
import time
import unicodedata
import zipfile
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
RAIZ = BASE / "datos"
PAPELERA = ".papelera"
PAPELERA_DIAS = 7
FOTOS_SUELTAS = "Fotos sueltas"

ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(_\d{4})?_[A-Za-z0-9_-]{1,60}$")
LEGADO_RE = re.compile(r"^(timelapse_\d{8}_\d{6}|capturas_unicas)$")
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
BYTES_POR_FOTO = 3280 * 2464 * 2          # TIFF de 16 bits sin comprimir


def slug(nombre):
    """'Células día 1' -> 'Celulas_dia_1' (seguro en FAT, SMB y URLs)."""
    s = unicodedata.normalize("NFKD", str(nombre or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^A-Za-z0-9 _-]+", "", s).strip()
    s = re.sub(r"[\s_]+", "_", s).strip("_-")
    return s[:50]


def fecha_legible(iso):
    try:
        d = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    return f"{d.day} de {MESES[d.month - 1]} de {d.year}, {d:%H:%M}"


def nombre_foto(fecha, ciclo=None, sufijo=""):
    """0001_2026-10-02_10-30-00_L.tif (timelapse) o 2026-10-02_10-30-00.tif."""
    base = f"{fecha:%Y-%m-%d_%H-%M-%S}"
    if ciclo is not None:
        base = f"{ciclo:04d}_{base}"
    return f"{base}{sufijo}.tif"


def _leer_json(carpeta):
    try:
        return json.loads((Path(carpeta) / "experimento.json").read_text())
    except (OSError, ValueError):
        return {}


def _escribir_json(carpeta, datos):
    p = Path(carpeta) / "experimento.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(datos, indent=2, ensure_ascii=False))
    os.replace(tmp, p)


def escribir_leeme(carpeta, datos):
    """LEEME.txt: lo que es cada cosa, para quien abra la carpeta en una
    computadora sin saber nada del microscopio."""
    tipo = datos.get("tipo")
    lineas = [f"Experimento: {datos.get('nombre') or '(sin nombre)'}",
              f"Tipo: {'Timelapse (fotos automáticas)' if tipo == 'timelapse' else 'Fotos'}",
              f"Inicio: {fecha_legible(datos.get('inicio'))}"]
    if datos.get("fin"):
        lineas.append(f"Fin: {fecha_legible(datos.get('fin'))}"
                      + (f" ({datos['estado']})" if datos.get("estado") else ""))
    if tipo == "timelapse":
        cada = datos.get("intervalo_s")
        dur = datos.get("duracion_s")
        if cada and dur:
            lineas.append(f"Una foto cada {cada / 60:g} min durante {dur / 3600:g} h")
        if datos.get("camaras") is not None:
            lineas.append("Cámaras: " + ", ".join(str(c) for c in datos["camaras"]))
    if datos.get("n_fotos") is not None:
        lineas.append(f"Fotos guardadas: {datos['n_fotos']}")
    lineas += [
        "",
        "Qué hay en esta carpeta",
        "-----------------------",
        "cam0/, cam1/       Las fotos de cada cámara, en TIFF de 16 bits sin modificar.",
    ]
    if tipo == "timelapse":
        lineas += [
            "                   Nombre: número de foto _ fecha _ hora. Ej.: 0001_2026-10-02_10-30-00.tif",
            "                   Con luz de relieve (DPC) son 4 por vez: _L _R _T _B (izquierda,",
            "                   derecha, arriba, abajo).",
        ]
        dpc = datos.get("dpc_procesado")
        if dpc:
            lineas += [
                "                   El relieve se calculó al terminar cada ciclo:",
                "                     _dpcLR.tif  izquierda-derecha, (L-R)/(L+R)",
                "                     _dpcTB.tif  arriba-abajo, (T-B)/(T+B)",
                "                     valor = " + dpc.get("valor_dpc", "(pixel - 32768) / 32767").replace("pixel", "píxel")
                + ", de -1 a 1 (gris medio = 0)",
            ]
            if dpc.get("suma"):
                lineas += ["                     _suma.tif   foto normal (campo claro): promedio de las 4,",
                           "                                 a menor resolución (la escala en micras ya viene corregida)"]
            if dpc.get("fase"):
                lineas += ["                     _fase.tif   fase, radianes = (píxel - 32768) * 0.0001",
                           "                                 (en prueba: sirve para ver, no para medir)"]
            if dpc.get("jpg"):
                lineas.append("                     _dpc.jpg    vista previa en color de los dos ejes")
            if dpc.get("borrar_crudas", True):
                lineas.append("                   Las 4 fotos originales se borraron después de comprobar el resultado.")
        lineas += [
            "timelapse.log      Lo que pasó en cada ciclo (foco, errores).",
            "temperatura.csv    Temperatura de la incubadora en cada ciclo.",
            "autofoco.csv       Cuánto se movió el foco en cada ciclo, en micras.",
        ]
    else:
        lineas.append("                   Nombre: fecha _ hora. Ej.: 2026-10-02_10-30-00.tif")
    lineas += [
        "experimento.json   Los mismos datos de arriba, para programas.",
        "",
        "Cada foto lleva adentro (sin que se vea) la escala en micras por píxel, el",
        "objetivo, la luz, la exposición, la posición del foco, la temperatura y la",
        "hora. Fiji / ImageJ la abre ya con la escala puesta; Image > Show Info",
        "muestra el resto.",
    ]
    try:
        (Path(carpeta) / "LEEME.txt").write_text("\n".join(lineas) + "\n", encoding="utf-8")
    except OSError:
        pass


class Experimentos:
    def __init__(self, raiz=RAIZ, legado=BASE):
        self.raiz = Path(raiz)
        self.legado = Path(legado)

    # ---------- crear ----------
    def _id_libre(self, raiz, base):
        cand, n = base, 2
        while (raiz / cand).exists():
            cand = f"{base}_{n}"
            n += 1
        return cand

    def crear_timelapse(self, nombre, datos=None, raiz=None, ahora=None):
        """Carpeta nueva para un timelapse. raiz: otra raiz (una memoria
        USB); por defecto datos/."""
        ahora = ahora or datetime.now()
        raiz = Path(raiz) if raiz else self.raiz
        raiz.mkdir(parents=True, exist_ok=True)
        ident = self._id_libre(raiz, f"{ahora:%Y-%m-%d_%H%M}_{slug(nombre) or 'Timelapse'}")
        carpeta = raiz / ident
        carpeta.mkdir()
        meta = {"nombre": (nombre or "").strip() or f"Timelapse del {ahora.day} de {MESES[ahora.month - 1]}",
                "tipo": "timelapse", "inicio": ahora.isoformat(timespec="seconds"),
                "estado": "en curso"}
        meta.update(datos or {})
        _escribir_json(carpeta, meta)
        escribir_leeme(carpeta, meta)
        return carpeta

    def carpeta_fotos(self, nombre=None, ahora=None):
        """Donde va una foto suelta: la de hoy con ese nombre (o «Fotos
        sueltas»), creandola si hace falta."""
        ahora = ahora or datetime.now()
        nombre = (nombre or "").strip()
        ident = f"{ahora:%Y-%m-%d}_{slug(nombre) or slug(FOTOS_SUELTAS)}"
        carpeta = self.raiz / ident
        if not carpeta.is_dir():
            carpeta.mkdir(parents=True)
            meta = {"nombre": nombre or FOTOS_SUELTAS, "tipo": "fotos",
                    "inicio": ahora.isoformat(timespec="seconds")}
            _escribir_json(carpeta, meta)
            escribir_leeme(carpeta, meta)
        return carpeta

    def finalizar(self, carpeta, **campos):
        datos = _leer_json(carpeta)
        datos.update(campos)
        datos["n_fotos"] = len(self.imagenes(carpeta))
        _escribir_json(carpeta, datos)
        escribir_leeme(carpeta, datos)
        return datos

    def actualizar(self, carpeta, **campos):
        datos = _leer_json(carpeta)
        datos.update(campos)
        _escribir_json(carpeta, datos)
        return datos

    # ---------- buscar ----------
    def resolver(self, ident):
        """Carpeta de un experimento por su id, o None. Nunca sale de
        datos/ ni de la carpeta de versiones anteriores."""
        if ID_RE.match(ident or ""):
            base = self.raiz
        elif LEGADO_RE.match(ident or ""):
            base = self.legado
        else:
            return None
        p = (base / ident).resolve()
        if p.parent != base.resolve() or not p.is_dir():
            return None
        return p

    def imagenes(self, carpeta):
        """Rutas relativas de las fotos, por camara y en orden de toma; las
        cuatro de un relieve DPC en orden L, R, T, B (no alfabetico)."""
        carpeta = Path(carpeta)
        orden = {"_L": 0, "_R": 1, "_T": 2, "_B": 3}

        def clave(rel):
            carpeta_rel, _, nombre = rel.rpartition("/")
            raiz = nombre[:-4]
            suf = raiz[-2:] if raiz[-2:] in orden else ""
            return (carpeta_rel, raiz[:len(raiz) - len(suf)], orden.get(suf, -1))
        return sorted((str(p.relative_to(carpeta)).replace(os.sep, "/")
                       for p in carpeta.rglob("*.tif") if p.is_file()), key=clave)

    def ruta_imagen(self, ident, rel):
        carpeta = self.resolver(ident)
        if carpeta is None or not rel or not rel.endswith(".tif"):
            return None
        p = (carpeta / rel).resolve()
        if carpeta not in p.parents or not p.is_file():
            return None
        return p

    def info(self, carpeta):
        carpeta = Path(carpeta)
        datos = _leer_json(carpeta)
        imgs = self.imagenes(carpeta)
        tam = sum((carpeta / r).stat().st_size for r in imgs)
        if not datos:      # carpeta de una version anterior
            m = re.match(r"timelapse_(\d{8})_(\d{6})", carpeta.name)
            ini = (datetime.strptime("".join(m.groups()), "%Y%m%d%H%M%S") if m
                   else datetime.fromtimestamp(carpeta.stat().st_mtime))
            datos = {"nombre": "Fotos sueltas (anteriores)" if carpeta.name == "capturas_unicas"
                     else f"Timelapse del {ini.day} de {MESES[ini.month - 1]}",
                     "tipo": "fotos" if carpeta.name == "capturas_unicas" else "timelapse",
                     "inicio": ini.isoformat(timespec="seconds")}
        elif datos.get("nombre") is None:
            datos["nombre"] = carpeta.name
        camaras = sorted({r.split("/")[0][3:] for r in imgs if r.startswith("cam") and "/" in r})
        return {
            "id": carpeta.name,
            "nombre": datos.get("nombre"),
            "tipo": datos.get("tipo", "timelapse"),
            "inicio": datos.get("inicio"),
            "inicio_legible": fecha_legible(datos.get("inicio")),
            "fin": datos.get("fin"),
            "estado": datos.get("estado"),
            "intervalo_s": datos.get("intervalo_s"),
            "duracion_s": datos.get("duracion_s"),
            "modo": datos.get("modo"),
            "camaras": camaras,
            "n_fotos": len(imgs),
            "bytes": tam,
            "portada": self._portada(imgs),
            "anterior": bool(LEGADO_RE.match(carpeta.name)),
        }

    @staticmethod
    def _portada(imgs):
        """La ultima foto de la primera camara (y de un relieve DPC, la
        primera de las cuatro)."""
        if not imgs:
            return None
        primera = imgs[0].split("/")[0] if "/" in imgs[0] else ""
        de_esa = [r for r in imgs if r.startswith(primera + "/")] if primera else imgs
        ultima = de_esa[-1]
        for suf in ("_B", "_T", "_R"):
            if ultima.endswith(suf + ".tif"):
                cand = ultima[:-len(suf) - 4] + "_L.tif"
                if cand in de_esa:
                    return cand
        return ultima

    def listar(self):
        carpetas = []
        if self.raiz.is_dir():
            carpetas += [p for p in self.raiz.iterdir() if p.is_dir() and ID_RE.match(p.name)]
        if self.legado.is_dir():
            carpetas += [p for p in self.legado.iterdir() if p.is_dir() and LEGADO_RE.match(p.name)]
        out = [self.info(p) for p in carpetas]
        # carpetas de fotos sin fotos (p. ej. se borraron todas) no se muestran
        out = [e for e in out if e["n_fotos"] or e["tipo"] == "timelapse"]
        return sorted(out, key=lambda e: e.get("inicio") or "", reverse=True)

    # ---------- cambiar ----------
    def renombrar(self, ident, nombre):
        carpeta = self.resolver(ident)
        if carpeta is None:
            raise ValueError("no existe ese experimento")
        nombre = (nombre or "").strip()
        if not slug(nombre):
            raise ValueError("el nombre tiene que tener al menos una letra o número")
        info = self.info(carpeta)
        prefijo = (re.match(r"^\d{4}-\d{2}-\d{2}(_\d{4})?", ident) or [None])[0]
        if prefijo is None:
            ini = datetime.fromisoformat(info["inicio"])
            prefijo = f"{ini:%Y-%m-%d}" + (f"_{ini:%H%M}" if info["tipo"] == "timelapse" else "")
        self.raiz.mkdir(parents=True, exist_ok=True)
        nuevo_id = f"{prefijo}_{slug(nombre)}"
        if nuevo_id != ident:
            nuevo_id = self._id_libre(self.raiz, nuevo_id)
            destino = self.raiz / nuevo_id
            shutil.move(str(carpeta), str(destino))
        else:
            destino = carpeta
        datos = _leer_json(destino) or {k: info[k] for k in ("tipo", "inicio")}
        datos["nombre"] = nombre
        _escribir_json(destino, datos)
        escribir_leeme(destino, dict(datos, n_fotos=len(self.imagenes(destino))))
        return nuevo_id

    def borrar(self, ident):
        """Mueve a la papelera. Devuelve el codigo para deshacer."""
        carpeta = self.resolver(ident)
        if carpeta is None:
            raise ValueError("no existe ese experimento")
        pap = self.raiz / PAPELERA
        pap.mkdir(parents=True, exist_ok=True)
        codigo = f"{int(time.time())}__{ident}"
        shutil.move(str(carpeta), str(pap / codigo))
        return codigo

    def restaurar(self, codigo):
        if not re.match(r"^\d+__[A-Za-z0-9_-]+$", codigo or ""):
            raise ValueError("código inválido")
        origen = (self.raiz / PAPELERA / codigo).resolve()
        if origen.parent != (self.raiz / PAPELERA).resolve() or not origen.is_dir():
            raise ValueError("ya no está en la papelera")
        ident = codigo.split("__", 1)[1]
        base = self.legado if LEGADO_RE.match(ident) else self.raiz
        destino = base / ident
        if destino.exists():
            destino = self.raiz / self._id_libre(self.raiz, ident)
        shutil.move(str(origen), str(destino))
        return destino.name

    def vaciar_papelera(self, dias=PAPELERA_DIAS):
        pap = self.raiz / PAPELERA
        if not pap.is_dir():
            return 0
        limite = time.time() - dias * 86400
        n = 0
        for p in pap.iterdir():
            try:
                if int(p.name.split("__", 1)[0]) < limite:
                    shutil.rmtree(p, ignore_errors=True)
                    n += 1
            except ValueError:
                continue
        return n

    def zip(self, ident, destino):
        """Escribe el experimento entero en el .zip `destino` (en disco:
        en memoria, uno grande se comia la RAM de la Pi)."""
        carpeta = self.resolver(ident)
        if carpeta is None:
            raise ValueError("no existe ese experimento")
        with zipfile.ZipFile(destino, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
            for p in sorted(carpeta.rglob("*")):
                if p.is_file():
                    zf.write(p, arcname=f"{carpeta.name}/{p.relative_to(carpeta)}")
        return destino

    def espacio(self):
        self.raiz.mkdir(parents=True, exist_ok=True)
        u = shutil.disk_usage(self.raiz)
        return {"total_bytes": u.total, "libre_bytes": u.free,
                "fotos_que_caben": int(u.free // BYTES_POR_FOTO)}
