"""Memorias USB: deteccion, montaje, copia y expulsion.

QUE RESUELVE
============

Al conectar una memoria USB a la Pi, la interfaz web ofrece guardar ahi
el proximo timelapse (o copiar uno ya hecho) y expulsarla de forma
segura al terminar, sin tener que entrar por SSH.

COMO DETECTA
============

Sin dependencias nuevas: cada `periodo` segundos se leen /proc/mounts y
/sys/class/block. Una particion cuenta como "USB" si su ruta real en
/sys pasa por el bus usb (asi entran tambien los SSD por USB, que el
kernel no marca como removibles). Si aparece una particion USB sin
montar, se intenta montar con `udisksctl` (paquete udisks2), que la deja
en /media/<usuario>/<etiqueta>.

Requisito en Pi OS Lite: udisks2 instalado y una regla de polkit que
permita al usuario del servicio montar sin sesion grafica (ver
configs/polkit/50-microscopeos-usb.rules y docs/USB_Y_ENVIO_PC.md). En
Pi OS con escritorio el propio escritorio ya la monta y aca solo se
detecta.

Cada vez que aparece una memoria nueva se incrementa `evento`: la
interfaz compara ese numero con el ultimo que vio y, si cambio, muestra
el aviso "se conectó una memoria, ¿qué quieres hacer?".
"""

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path


def _decodificar(ruta):
    """/proc/mounts escapa espacio, tab, salto de linea y barra invertida
    en octal (\\040 ...); /dev/disk/by-label los escapa en hex (\\x20).
    Se reemplazan solo esos, para no romper etiquetas con acentos."""
    for escapado, real in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"),
                           ("\\x20", " "), ("\\134", "\\")):
        ruta = ruta.replace(escapado, real)
    return ruta


def _es_usb(dispositivo):
    """True si /dev/sdX o /dev/sdXN cuelga del bus USB."""
    nombre = os.path.basename(dispositivo)
    sys_block = Path("/sys/class/block") / nombre
    try:
        return "/usb" in os.path.realpath(sys_block)
    except OSError:
        return False


def _etiqueta(dispositivo):
    por_label = Path("/dev/disk/by-label")
    if por_label.is_dir():
        for enlace in por_label.iterdir():
            try:
                if os.path.realpath(enlace) == os.path.realpath(dispositivo):
                    return _decodificar(enlace.name)
            except OSError:
                pass
    return os.path.basename(dispositivo)


def _montajes():
    """{dispositivo: (punto_de_montaje, tipo_fs)} de /proc/mounts."""
    resultado = {}
    try:
        with open("/proc/mounts") as f:
            for linea in f:
                partes = linea.split()
                if len(partes) >= 3 and partes[0].startswith("/dev/"):
                    resultado[partes[0]] = (_decodificar(partes[1]), partes[2])
    except OSError:
        pass
    return resultado


def _particiones_usb():
    """Particiones (o discos sin tabla de particiones) en el bus USB."""
    salida = []
    base = Path("/sys/class/block")
    if not base.is_dir():
        return salida
    for entrada in sorted(base.iterdir()):
        nombre = entrada.name
        if not nombre.startswith("sd"):
            continue
        dev = f"/dev/{nombre}"
        if not _es_usb(dev):
            continue
        es_particion = (entrada / "partition").exists()
        tiene_particiones = any(p.name.startswith(nombre) and p.name != nombre
                                for p in entrada.iterdir())
        # disco entero solo si no tiene particiones (memorias formateadas "en crudo")
        if es_particion or not tiene_particiones:
            salida.append(dev)
    return salida


class MonitorUSB:

    def __init__(self, periodo=2.0, montar_auto=True):
        self.periodo = periodo
        self.montar_auto = montar_auto
        self.dispositivos = []      # lista de dicts, ver _escanear
        self.evento = 0             # sube cada vez que aparece una memoria nueva
        self.ultimo_nuevo = None    # dict del ultimo dispositivo que aparecio
        self.error = None
        self.copia = {"activa": False}
        self._vistos = set()
        self._intentos_montaje = {}
        self._lock = threading.Lock()
        self._hilo = threading.Thread(target=self._bucle, daemon=True)
        self._hilo.start()

    # ---------- deteccion ----------
    def _bucle(self):
        while True:
            try:
                self._escanear()
            except Exception as e:     # nunca tumbar el servidor por esto
                self.error = str(e)
            time.sleep(self.periodo)

    def _escanear(self):
        montajes = _montajes()
        encontrados = []
        for dev in _particiones_usb():
            punto, fs = montajes.get(dev, (None, None))
            if punto is None and self.montar_auto:
                punto = self._montar(dev)
                if punto:
                    fs = _montajes().get(dev, (None, None))[1]
            info = {"dispositivo": dev, "etiqueta": _etiqueta(dev),
                    "punto": punto, "fs": fs, "montado": punto is not None}
            if punto:
                try:
                    uso = shutil.disk_usage(punto)
                    info.update(libre_bytes=uso.free, total_bytes=uso.total,
                                escribible=os.access(punto, os.W_OK))
                except OSError:
                    info.update(libre_bytes=None, total_bytes=None, escribible=False)
            encontrados.append(info)

        actuales = {d["dispositivo"] for d in encontrados if d["montado"]}
        with self._lock:
            nuevos = actuales - self._vistos
            if nuevos:
                self.evento += 1
                self.ultimo_nuevo = next(d for d in encontrados
                                         if d["dispositivo"] in nuevos)
            self._vistos = actuales
            self.dispositivos = encontrados
        # si se desconecto, permitir reintentar el montaje cuando vuelva
        for dev in list(self._intentos_montaje):
            if dev not in {d["dispositivo"] for d in encontrados}:
                del self._intentos_montaje[dev]

    def _montar(self, dev):
        # Un intento por conexion: si falla (sin udisks2 o sin permiso de
        # polkit) no insistir cada 2 s llenando el log.
        if self._intentos_montaje.get(dev):
            return None
        self._intentos_montaje[dev] = True
        try:
            r = subprocess.run(["udisksctl", "mount", "-b", dev,
                                "--no-user-interaction"],
                               capture_output=True, text=True, timeout=20)
        except FileNotFoundError:
            self.error = ("udisksctl no esta instalado: sudo apt install "
                          "udisks2 (ver docs/USB_Y_ENVIO_PC.md)")
            return None
        except subprocess.TimeoutExpired:
            self.error = f"montar {dev}: tiempo agotado"
            return None
        if r.returncode != 0:
            self.error = f"montar {dev}: {r.stderr.strip() or r.stdout.strip()}"
            return None
        self.error = None
        return _montajes().get(dev, (None, None))[0]

    # ---------- consultas ----------
    def estado(self):
        with self._lock:
            return {"dispositivos": list(self.dispositivos),
                    "evento": self.evento,
                    "ultimo_nuevo": self.ultimo_nuevo,
                    "error": self.error,
                    "copia": dict(self.copia)}

    def punto_valido(self, punto):
        """Devuelve el dict del dispositivo si `punto` es una memoria USB
        montada y escribible en este momento; si no, None. Es la unica
        forma de que la API acepte una ruta de destino que viene del
        navegador."""
        with self._lock:
            for d in self.dispositivos:
                if d["montado"] and d["punto"] == punto and d.get("escribible"):
                    return dict(d)
        return None

    # ---------- acciones ----------
    def expulsar(self, punto):
        d = self.punto_valido(punto) or next(
            (x for x in self.dispositivos if x["punto"] == punto), None)
        if d is None:
            return {"error": "no hay una memoria USB montada en esa ruta"}
        if self.copia.get("activa") and self.copia.get("destino", "").startswith(punto):
            return {"error": "hay una copia en curso hacia esa memoria"}
        os.sync()
        r = subprocess.run(["udisksctl", "unmount", "-b", d["dispositivo"],
                            "--no-user-interaction"],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return {"error": r.stderr.strip() or "no se pudo desmontar "
                    "(¿un timelapse sigue escribiendo ahi?)"}
        disco = d["dispositivo"].rstrip("0123456789")
        subprocess.run(["udisksctl", "power-off", "-b", disco,
                        "--no-user-interaction"],
                       capture_output=True, text=True, timeout=30)
        self._escanear()
        return {"status": "ok", "mensaje": "Ya se puede retirar la memoria"}

    def copiar(self, origen, punto):
        """Copia una carpeta de timelapse a la memoria, en segundo plano."""
        if self.copia.get("activa"):
            return {"error": "ya hay una copia en curso"}
        d = self.punto_valido(punto)
        if d is None:
            return {"error": "memoria USB no disponible o de solo lectura"}
        origen = Path(origen)
        archivos = [p for p in origen.rglob("*") if p.is_file()]
        total = sum(p.stat().st_size for p in archivos)
        if d.get("libre_bytes") is not None and total > d["libre_bytes"]:
            return {"error": f"no alcanza el espacio: hacen falta "
                    f"{total / 1e9:.1f} GB y hay {d['libre_bytes'] / 1e9:.1f} GB"}
        destino = Path(punto) / origen.name
        self.copia = {"activa": True, "origen": str(origen),
                      "destino": str(destino), "copiados": 0,
                      "total": len(archivos), "bytes_total": total,
                      "error": None}

        def trabajar():
            try:
                for p in archivos:
                    rel = p.relative_to(origen)
                    (destino / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, destino / rel)
                    self.copia["copiados"] += 1
                os.sync()
            except Exception as e:
                self.copia["error"] = str(e)
            finally:
                self.copia["activa"] = False

        threading.Thread(target=trabajar, daemon=True).start()
        return {"status": "copiando", "archivos": len(archivos),
                "bytes": total}
