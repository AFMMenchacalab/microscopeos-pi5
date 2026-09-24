"""Envio de cada imagen a una computadora mientras corre el timelapse.

PARA QUE
========

La segmentacion con Cellpose-SAM tarda ~2 s por imagen en la GPU de la
PC y ~20 min en la Pi 5, asi que no se puede hacer aca entre foto y foto.
La Pi adquiere y manda cada imagen apenas se guarda; en la PC corren
`31_receptor_microscopio.py` (recibe) y `32_segmentar_en_vivo.py`
(segmenta cada imagen cuando llega). Ambos estan en el repositorio del
pipeline (AFMMenchacalab/pipeline-migracion-celular).

COMO
====

HTTP simple, sin dependencias nuevas (urllib de la biblioteca estandar):

    PUT  {url}/subir/{experimento}/{camara}/{archivo}
         cuerpo = bytes del archivo
         X-Sha256 = hash del contenido, X-Token = clave compartida
    GET  {url}/existe/{experimento}/{camara}/{archivo}?sha256=...
    GET  {url}/salud

La PC guarda primero en un archivo temporal, verifica el hash y recien
ahi lo renombra: nunca queda una imagen a medias con su nombre final, y
el segmentador solo ve archivos completos.

NADA SE PIERDE SI SE CORTA LA RED
=================================

La imagen siempre se guarda primero en la Pi (o en la USB); el envio es
una copia. Si la PC no responde, la cola reintenta con espera creciente
(hasta 60 s) sin frenar el timelapse. Y como la PC responde "ya la
tengo" si el hash coincide, `reenviar_carpeta()` sirve para completar
lo que falto despues de un corte o de reiniciar la Pi, sin duplicar.
"""

import hashlib
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path

ARCHIVO_CONFIG = (Path(__file__).resolve().parent.parent / "profiles" /
                  "envio_pc.json")


def _sha256(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


class EnviadorPC:

    def __init__(self):
        self.url = ""
        self.token = ""
        self.activo = False
        self._cola = deque()
        self._evento = threading.Event()
        self._lock = threading.Lock()
        self.enviados = 0
        self.ya_estaban = 0
        self.fallos = 0
        self.ultimo_error = None
        self.ultimo_ok = None
        self.en_curso = None
        self._cargar_config()
        threading.Thread(target=self._bucle, daemon=True).start()

    # ---------- configuracion persistida ----------
    def _cargar_config(self):
        try:
            with open(ARCHIVO_CONFIG) as f:
                c = json.load(f)
            self.url = c.get("url", "")
            self.token = c.get("token", "")
            self.activo = bool(c.get("activo", False))
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[envio] configuracion ilegible ({e}), se ignora")

    def configurar(self, url=None, token=None, activo=None):
        if url is not None:
            url = url.strip().rstrip("/")
            if url and not url.startswith(("http://", "https://")):
                url = "http://" + url
            self.url = url
        if token is not None:
            self.token = token.strip()
        if activo is not None:
            self.activo = bool(activo)
        try:
            ARCHIVO_CONFIG.parent.mkdir(parents=True, exist_ok=True)
            with open(ARCHIVO_CONFIG, "w") as f:
                json.dump({"url": self.url, "token": self.token,
                           "activo": self.activo}, f, indent=2)
        except Exception as e:
            print(f"[envio] no se pudo guardar la configuracion: {e}")
        return self.estado()

    # ---------- HTTP ----------
    def _pedir(self, metodo, ruta, datos=None, encabezados=None, timeout=30):
        req = urllib.request.Request(self.url + ruta, data=datos, method=metodo)
        if self.token:
            req.add_header("X-Token", self.token)
        for k, v in (encabezados or {}).items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()

    def probar(self):
        if not self.url:
            return {"ok": False, "error": "falta la direccion de la PC"}
        try:
            status, cuerpo = self._pedir("GET", "/salud", timeout=5)
            return {"ok": status == 200, "respuesta": json.loads(cuerpo or b"{}")}
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return {"ok": False, "error": "la clave (token) no coincide"}
            return {"ok": False, "error": f"HTTP {e.code}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @staticmethod
    def _ruta_remota(experimento, camara, archivo):
        return "/".join(urllib.parse.quote(p, safe="")
                        for p in (experimento, camara, archivo))

    def _enviar_uno(self, ruta, experimento, camara):
        ruta = Path(ruta)
        sha = _sha256(ruta)
        remota = self._ruta_remota(experimento, camara, ruta.name)
        # Preguntar primero: si la PC ya lo tiene (reenvio tras un corte),
        # no se manda de nuevo.
        try:
            status, _ = self._pedir("GET", f"/existe/{remota}?sha256={sha}",
                                    timeout=10)
            if status == 200:
                return "ya_estaba"
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
        with open(ruta, "rb") as f:
            datos = f.read()
        self._pedir("PUT", f"/subir/{remota}", datos=datos,
                    encabezados={"X-Sha256": sha,
                                 "Content-Type": "application/octet-stream"},
                    timeout=120)
        return "enviado"

    # ---------- cola ----------
    def encolar(self, ruta, experimento, camara):
        """ruta: archivo ya guardado en disco. experimento: nombre de la
        carpeta del timelapse. camara: 'cam0', 'cam1' o '' para archivos
        de la raiz del experimento (p. ej. experimento.json)."""
        with self._lock:
            self._cola.append((str(ruta), experimento, camara or "_"))
        self._evento.set()

    def reenviar_carpeta(self, carpeta):
        """Encola todo el experimento; lo que la PC ya tenga se saltea."""
        carpeta = Path(carpeta)
        n = 0
        for p in sorted(carpeta.rglob("*")):
            if not p.is_file() or p.name.endswith((".log", ".png")):
                continue
            rel = p.relative_to(carpeta)
            camara = rel.parts[0] if len(rel.parts) > 1 else "_"
            self.encolar(p, carpeta.name, camara)
            n += 1
        return {"encolados": n}

    def _bucle(self):
        espera = 1.0
        while True:
            self._evento.wait(timeout=5)
            with self._lock:
                if not self._cola:
                    self._evento.clear()
                    continue
                item = self._cola[0]
            if not self.url:
                time.sleep(5)
                continue
            self.en_curso = Path(item[0]).name
            try:
                r = self._enviar_uno(*item)
                with self._lock:
                    self._cola.popleft()
                if r == "enviado":
                    self.enviados += 1
                else:
                    self.ya_estaban += 1
                self.ultimo_ok = time.time()
                self.ultimo_error = None
                espera = 1.0
            except FileNotFoundError:
                with self._lock:
                    self._cola.popleft()     # el archivo ya no existe: descartar
                self.fallos += 1
                self.ultimo_error = f"no existe {item[0]}"
            except Exception as e:
                # red caida o PC apagada: reintentar el MISMO archivo luego
                self.fallos += 1
                self.ultimo_error = str(e)
                time.sleep(espera)
                espera = min(espera * 2, 60.0)
            finally:
                self.en_curso = None

    def estado(self):
        with self._lock:
            pendientes = len(self._cola)
        return {"url": self.url, "activo": self.activo,
                "token_configurado": bool(self.token),
                "pendientes": pendientes, "enviados": self.enviados,
                "ya_estaban": self.ya_estaban, "fallos": self.fallos,
                "en_curso": self.en_curso, "ultimo_error": self.ultimo_error,
                "ultimo_ok": self.ultimo_ok}
