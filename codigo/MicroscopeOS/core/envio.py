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

ENCONTRAR LA PC SIN ESCRIBIR SU IP
==================================

`buscar_pcs()` manda por difusion (UDP 8766) "MICROSCOPIO_BUSCAR"; cada PC
con la recepcion activa responde con su nombre, puerto y GPU. La interfaz
muestra la lista, se elige una y se escribe el codigo de 6 digitos que esa
PC muestra en pantalla (la respuesta no incluye la clave).

Si la PC cambia de IP (el router se la reasigna), despues de varios fallos
seguidos se la vuelve a buscar por su NOMBRE y se actualiza la direccion
sola. La difusion no cruza routers: si la Pi y la PC estan en redes
distintas, se escribe la IP a mano.

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
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path

ARCHIVO_CONFIG = (Path(__file__).resolve().parent.parent / "profiles" /
                  "envio_pc.json")


PUERTO_DESCUBRIMIENTO = 8766
MENSAJE_BUSCAR = b"MICROSCOPIO_BUSCAR"


def _direcciones_broadcast():
    """Broadcast de cada interfaz (`ip -o -4 addr`), mas 255.255.255.255.
    En una Pi con Wi-Fi y cable puede haber dos redes: se pregunta en ambas."""
    dirs = {"255.255.255.255"}
    try:
        salida = subprocess.run(["ip", "-o", "-4", "addr", "show"], capture_output=True,
                                text=True, timeout=3).stdout
        for linea in salida.splitlines():
            partes = linea.split()
            if "brd" in partes:
                dirs.add(partes[partes.index("brd") + 1])
    except (OSError, subprocess.SubprocessError):
        pass
    return sorted(dirs)


def buscar_pcs(tiempo=2.0):
    """PCs con el receptor activo en la red local:
    [{"nombre", "ip", "puerto", "url", "gpu"}]."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(0.3)
    encontradas = {}
    try:
        for d in _direcciones_broadcast():
            try:
                s.sendto(MENSAJE_BUSCAR, (d, PUERTO_DESCUBRIMIENTO))
            except OSError:
                pass
        fin = time.time() + tiempo
        while time.time() < fin:
            try:
                datos, (ip, _) = s.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                info = json.loads(datos)
            except ValueError:
                continue
            if info.get("servicio") != "receptor-microscopio":
                continue
            puerto = int(info.get("puerto", 8765))
            encontradas[(ip, puerto)] = {"nombre": str(info.get("nombre", ip))[:80], "ip": ip,
                                         "puerto": puerto, "url": f"http://{ip}:{puerto}",
                                         "gpu": str(info.get("gpu", ""))[:80]}
    finally:
        s.close()
    return sorted(encontradas.values(), key=lambda x: x["nombre"])


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
        self.nombre_pc = ""       # para volver a encontrarla si cambia de IP
        self.activo = False
        self._fallos_seguidos = 0
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
            self.nombre_pc = c.get("nombre_pc", "")
            self.activo = bool(c.get("activo", False))
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[envio] configuracion ilegible ({e}), se ignora")

    def configurar(self, url=None, token=None, activo=None, nombre_pc=None):
        if nombre_pc is not None:
            self.nombre_pc = nombre_pc.strip()[:80]
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
                           "nombre_pc": self.nombre_pc, "activo": self.activo}, f, indent=2)
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
                return {"ok": False, "error": "el código no coincide con el que muestra la PC"}
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
                self._fallos_seguidos = 0
                espera = 1.0
            except FileNotFoundError:
                with self._lock:
                    self._cola.popleft()     # el archivo ya no existe: descartar
                self.fallos += 1
                self.ultimo_error = f"no existe {item[0]}"
            except Exception as e:
                # red caida o PC apagada: reintentar el MISMO archivo luego
                self.fallos += 1
                self._fallos_seguidos += 1
                self.ultimo_error = str(e)
                if self._fallos_seguidos % 4 == 0:
                    self._reencontrar()
                time.sleep(espera)
                espera = min(espera * 2, 60.0)
            finally:
                self.en_curso = None

    def _reencontrar(self):
        """Si la PC cambio de IP, buscarla por nombre y actualizar la URL."""
        if not self.nombre_pc:
            return
        try:
            for pc in buscar_pcs(tiempo=1.5):
                if pc["nombre"] == self.nombre_pc and pc["url"] != self.url:
                    print(f"[envio] {self.nombre_pc} cambio de direccion: {self.url} -> {pc['url']}")
                    self.configurar(url=pc["url"])
                    return
        except OSError:
            pass

    def estado(self):
        with self._lock:
            pendientes = len(self._cola)
        return {"url": self.url, "activo": self.activo, "nombre_pc": self.nombre_pc,
                "token_configurado": bool(self.token),
                "pendientes": pendientes, "enviados": self.enviados,
                "ya_estaban": self.ya_estaban, "fallos": self.fallos,
                "en_curso": self.en_curso, "ultimo_error": self.ultimo_error,
                "ultimo_ok": self.ultimo_ok}
