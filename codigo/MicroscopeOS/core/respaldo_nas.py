"""Respaldo de cada imagen en un NAS, en paralelo con el envio a la PC.

QUE HACE
========

Mientras corre el timelapse, cada imagen guardada se copia tambien a una
carpeta del NAS. Tiene su propia cola y su propio hilo, independiente del
envio a la PC (core/envio.py): las dos cosas pasan al mismo tiempo y un NAS
lento o apagado nunca frena a la PC.

PREFERENCIA A LA PC: si la PC esta recibiendo bien y todavia tiene
imagenes pendientes, el respaldo espera su turno para no competir por el
ancho de banda (en Wi-Fi importa). Si la PC esta caida o al dia, el
respaldo avanza normal. El respaldo nunca se pierde: solo se demora.

COMO ESCRIBE EN EL NAS
======================

Dos modos:

- "smb" (recomendado): carpeta compartida por SMB, que es lo que ofrecen
  Synology, QNAP, TrueNAS, Windows, etc. Se escribe directo con usuario y
  contraseña (biblioteca `smbprotocol`, pip), sin montar nada ni usar sudo.
- "carpeta": una ruta ya montada en la Pi (p. ej. /mnt/nas por NFS o CIFS
  en /etc/fstab). Se copia como a cualquier carpeta.

Cada archivo se escribe con nombre temporal y se renombra al final: en el
NAS nunca queda una imagen a medias con su nombre real. Si ya existe con el
mismo tamaño, no se vuelve a copiar (reenviar una carpeta completa es
seguro). Estructura en el NAS:

    <recurso>/<subcarpeta>/timelapse_<fecha>/cam0/img_..._L.tif

BUSCAR EL NAS
=============

`buscar_nas()` junta dos fuentes: los equipos que se anuncian por mDNS como
servidores SMB (avahi-browse, si esta instalado) y un barrido rapido del
puerto 445 en la red local de la Pi. Devuelve IP y nombre; la carpeta
compartida, usuario y contraseña se escriben a mano (son los del NAS).

La contraseña queda guardada en profiles/respaldo_nas.json (permisos 600,
fuera de git). Conviene crear en el NAS un usuario solo para esto, con
permiso de escritura en una sola carpeta.
"""

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ARCHIVO_CONFIG = (Path(__file__).resolve().parent.parent / "profiles" /
                  "respaldo_nas.json")
CAMPOS = ("modo", "servidor", "recurso", "subcarpeta", "usuario", "ruta_local", "activo", "puerto")


# ---------------------------------------------------------------------------
# Busqueda en la red
# ---------------------------------------------------------------------------
def _redes_locales():
    """[(ip_propia, prefijo)] de las interfaces IPv4 de la Pi."""
    redes = []
    try:
        salida = subprocess.run(["ip", "-o", "-4", "addr", "show"], capture_output=True,
                                text=True, timeout=3).stdout
        for linea in salida.splitlines():
            partes = linea.split()
            if "inet" in partes:
                ip, pref = partes[partes.index("inet") + 1].split("/")
                if not ip.startswith("127."):
                    redes.append((ip, int(pref)))
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return redes


def _hosts(ip, prefijo):
    """Direcciones de la red (maximo una /22 = 1022 equipos, para que el
    barrido tarde pocos segundos)."""
    import ipaddress
    red = ipaddress.ip_network(f"{ip}/{max(prefijo, 22)}", strict=False)
    return [str(h) for h in red.hosts() if str(h) != ip]


def _puerto_abierto(ip, puerto=445, timeout=0.4):
    try:
        with socket.create_connection((ip, puerto), timeout=timeout):
            return True
    except OSError:
        return False


def _mdns_smb():
    """{ip: nombre} de los que se anuncian como servidor SMB por mDNS."""
    encontrados = {}
    try:
        salida = subprocess.run(["avahi-browse", "-rtp", "_smb._tcp"], capture_output=True,
                                text=True, timeout=6).stdout
    except (OSError, subprocess.SubprocessError):
        return encontrados
    for linea in salida.splitlines():
        p = linea.split(";")
        if len(p) > 7 and p[0] == "=" and p[2] == "IPv4":
            encontrados[p[7]] = p[3].replace("\\032", " ")
    return encontrados


def buscar_nas(tiempo_max=8.0):
    """Equipos de la red local con carpetas compartidas (SMB):
    [{"ip", "nombre", "anunciado"}]; "anunciado" = se presenta como
    servidor por mDNS (los NAS suelen hacerlo)."""
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=128) as ex:
        f_mdns = ex.submit(_mdns_smb)
        candidatos = [h for ip, pref in _redes_locales() for h in _hosts(ip, pref)]
        abiertos = [ip for ip, ok in zip(candidatos, ex.map(_puerto_abierto, candidatos)) if ok]
        mdns = f_mdns.result(timeout=max(0.5, tiempo_max - (time.time() - t0)))

        def nombre(ip):
            try:
                return socket.gethostbyaddr(ip)[0]
            except OSError:
                return ""
        ips = sorted(set(abiertos) | set(mdns), key=lambda s: tuple(int(x) for x in s.split(".")))
        nombres = dict(zip(ips, ex.map(nombre, ips)))
    return [{"ip": ip, "nombre": mdns.get(ip) or nombres.get(ip) or ip, "anunciado": ip in mdns}
            for ip in ips]


# ---------------------------------------------------------------------------
# Respaldo
# ---------------------------------------------------------------------------
class RespaldoNAS:

    def __init__(self, ceder_a=None):
        """ceder_a: EnviadorPC (core/envio.py) al que se le da preferencia."""
        self.modo = "smb"
        self.servidor = ""
        self.recurso = ""
        self.subcarpeta = "microscopio"
        self.usuario = ""
        self.contrasena = ""
        self.ruta_local = ""
        self.activo = False
        self.puerto = 445             # SMB estándar; se cambia solo para pruebas
        self.ceder_a = ceder_a
        self._cola = deque()
        self._evento = threading.Event()
        self._lock = threading.Lock()
        self._sesion = None
        self.enviados = 0
        self.ya_estaban = 0
        self.fallos = 0
        self.ultimo_error = None
        self.ultimo_ok = None
        self.en_curso = None
        self.esperando_pc = False
        self._cargar_config()
        threading.Thread(target=self._bucle, daemon=True).start()

    # ---------- configuracion ----------
    def _cargar_config(self):
        try:
            with open(ARCHIVO_CONFIG, encoding="utf-8") as f:
                c = json.load(f)
            for k in CAMPOS:
                if k in c:
                    setattr(self, k, c[k])
            self.contrasena = c.get("contrasena", "")
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[nas] configuracion ilegible ({e}), se ignora")

    def configurar(self, **kw):
        for k in CAMPOS:
            if kw.get(k) is not None:
                v = kw[k]
                setattr(self, k, v.strip() if isinstance(v, str) else bool(v) if k == "activo" else v)
        if kw.get("contrasena"):
            self.contrasena = kw["contrasena"]
        self.servidor = self.servidor.strip("\\/ ")
        self.recurso = self.recurso.strip("\\/ ")
        self.subcarpeta = self.subcarpeta.strip("\\/ ")
        if self.modo not in ("smb", "carpeta"):
            self.modo = "smb"
        self._sesion = None                      # con datos nuevos, reconectar
        try:
            ARCHIVO_CONFIG.parent.mkdir(parents=True, exist_ok=True)
            datos = {k: getattr(self, k) for k in CAMPOS}
            datos["contrasena"] = self.contrasena
            with open(ARCHIVO_CONFIG, "w", encoding="utf-8") as f:
                json.dump(datos, f, indent=2, ensure_ascii=False)
            os.chmod(ARCHIVO_CONFIG, 0o600)
        except Exception as e:
            print(f"[nas] no se pudo guardar la configuracion: {e}")
        return self.estado()

    def configurado(self):
        if self.modo == "carpeta":
            return bool(self.ruta_local)
        return bool(self.servidor and self.recurso)

    # ---------- escritura ----------
    def _raiz(self):
        if self.modo == "carpeta":
            return os.path.join(self.ruta_local, self.subcarpeta) if self.subcarpeta else self.ruta_local
        partes = [f"\\\\{self.servidor}", self.recurso] + ([self.subcarpeta] if self.subcarpeta else [])
        return "\\".join(partes)

    def _kw(self):
        """Cada operacion de smbclient busca la conexion por servidor:puerto;
        con un puerto distinto de 445 hay que pasarlo siempre."""
        return {"port": int(self.puerto or 445), "username": self.usuario or None,
                "password": self.contrasena or None}

    def _smb(self):
        try:
            import smbclient
        except ImportError:
            raise RuntimeError("falta la biblioteca smbprotocol: pip install smbprotocol "
                               "(ver docs/USB_Y_ENVIO_PC.md)")
        if self._sesion != (self.servidor, self.usuario):
            smbclient.register_session(self.servidor, username=self.usuario or None,
                                       password=self.contrasena or None, port=int(self.puerto or 445),
                                       connection_timeout=10)
            self._sesion = (self.servidor, self.usuario)
        return smbclient

    def _copiar(self, origen, experimento, camara):
        origen = Path(origen)
        tam = origen.stat().st_size
        subdirs = [experimento] + ([] if camara in ("", "_") else [camara])
        if self.modo == "carpeta":
            destino_dir = os.path.join(self._raiz(), *subdirs)
            destino = os.path.join(destino_dir, origen.name)
            if os.path.exists(destino) and os.path.getsize(destino) == tam:
                return "ya_estaba"
            os.makedirs(destino_dir, exist_ok=True)
            tmp = os.path.join(destino_dir, f".{origen.name}.parcial")
            shutil.copyfile(origen, tmp)
            os.replace(tmp, destino)
            return "enviado"
        smb = self._smb()
        destino_dir = "\\".join([self._raiz()] + subdirs)
        destino = f"{destino_dir}\\{origen.name}"
        try:
            if smb.stat(destino, **self._kw()).st_size == tam:
                return "ya_estaba"
        except OSError:
            pass
        smb.makedirs(destino_dir, exist_ok=True, **self._kw())
        tmp = f"{destino_dir}\\.{origen.name}.parcial"
        with open(origen, "rb") as fi, smb.open_file(tmp, mode="wb", **self._kw()) as fo:
            for bloque in iter(lambda: fi.read(1 << 20), b""):
                fo.write(bloque)
        smb.replace(tmp, destino, **self._kw())
        return "enviado"

    def probar(self):
        """Escribe y borra un archivo de prueba en la carpeta del NAS."""
        if not self.configurado():
            return {"ok": False, "error": "falta el servidor y la carpeta compartida"}
        try:
            if self.modo == "carpeta":
                os.makedirs(self._raiz(), exist_ok=True)
                p = os.path.join(self._raiz(), ".prueba_microscopio")
                with open(p, "w") as f:
                    f.write("ok")
                os.remove(p)
                libre = shutil.disk_usage(self._raiz()).free
                return {"ok": True, "destino": self._raiz(), "libre_gb": round(libre / 1e9, 1)}
            smb = self._smb()
            smb.makedirs(self._raiz(), exist_ok=True, **self._kw())
            p = self._raiz() + "\\.prueba_microscopio"
            with smb.open_file(p, mode="w", **self._kw()) as f:
                f.write("ok")
            smb.remove(p, **self._kw())
            return {"ok": True, "destino": self._raiz()}
        except Exception as e:
            self._sesion = None
            msj = str(e)
            if "STATUS_LOGON_FAILURE" in msj or "LogonFailure" in msj:
                msj = "usuario o contraseña incorrectos"
            elif "STATUS_BAD_NETWORK_NAME" in msj:
                msj = f"el NAS no tiene una carpeta compartida llamada «{self.recurso}»"
            elif "STATUS_ACCESS_DENIED" in msj:
                msj = "el usuario no tiene permiso de escritura en esa carpeta"
            return {"ok": False, "error": msj}

    # ---------- cola ----------
    def encolar(self, ruta, experimento, camara):
        with self._lock:
            self._cola.append((str(ruta), experimento, camara or "_"))
        self._evento.set()

    def reenviar_carpeta(self, carpeta):
        carpeta = Path(carpeta)
        n = 0
        for p in sorted(carpeta.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(carpeta)
            self.encolar(p, carpeta.name, rel.parts[0] if len(rel.parts) > 1 else "_")
            n += 1
        return {"encolados": n}

    def _debe_ceder(self):
        """True si la PC esta recibiendo bien y tiene imagenes esperando."""
        pc = self.ceder_a
        if pc is None:
            return False
        e = pc.estado()
        return bool(e["url"]) and e["pendientes"] > 0 and e["ultimo_error"] is None

    def _bucle(self):
        espera = 1.0
        while True:
            self._evento.wait(timeout=5)
            with self._lock:
                if not self._cola:
                    self._evento.clear()
                    continue
                item = self._cola[0]
            if not self.configurado():
                time.sleep(5)
                continue
            if self._debe_ceder():
                self.esperando_pc = True
                time.sleep(0.5)
                continue
            self.esperando_pc = False
            self.en_curso = Path(item[0]).name
            try:
                r = self._copiar(*item)
                with self._lock:
                    self._cola.popleft()
                if r == "enviado":
                    self.enviados += 1
                else:
                    self.ya_estaban += 1
                self.ultimo_ok = time.time()
                self.ultimo_error = None
                espera = 1.0
            except FileNotFoundError as e:
                if not Path(item[0]).exists():      # el archivo local ya no existe: descartar
                    with self._lock:
                        self._cola.popleft()
                self.fallos += 1
                self.ultimo_error = str(e)
            except Exception as e:
                # NAS apagado, red caida, disco lleno: reintentar el MISMO archivo luego
                self.fallos += 1
                self.ultimo_error = str(e)
                self._sesion = None
                time.sleep(espera)
                espera = min(espera * 2, 60.0)
            finally:
                self.en_curso = None

    def estado(self):
        with self._lock:
            pendientes = len(self._cola)
        return {**{k: getattr(self, k) for k in CAMPOS},
                "contrasena_configurada": bool(self.contrasena),
                "configurado": self.configurado(), "destino": self._raiz() if self.configurado() else "",
                "pendientes": pendientes, "enviados": self.enviados, "ya_estaban": self.ya_estaban,
                "fallos": self.fallos, "en_curso": self.en_curso, "esperando_pc": self.esperando_pc,
                "ultimo_error": self.ultimo_error, "ultimo_ok": self.ultimo_ok}
