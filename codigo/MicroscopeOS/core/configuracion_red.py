"""Configurar la red del microscopio desde el teléfono (por Bluetooth).

Para un microscopio nuevo (todavía sin Wi-Fi) o para volver a encontrarlo
si cambió de IP. La parte de Bluetooth está en core/servicio_bluetooth.py;
aquí está todo lo demás, sin Bluetooth, para poder probarlo en la laptop
(tests/test_configuracion_red.py).

QUÉ SE PUEDE HACER Y CUÁNDO
===========================

- Leer el nombre, la red y la IP: siempre. Es lo que usa la app para
  «resincronizar» (volver a encontrar el microscopio si su IP cambió).
- Configurar el Wi-Fi: solo
    * si el microscopio NO tiene red, o en los primeros 10 minutos después
      de encenderlo (VENTANA_ARRANQUE_S), y
    * nunca con un timelapse corriendo, y
    * después de demostrar que se está frente al microscopio: la matriz de
      luz muestra 3 colores seguidos (de 6 posibles) y la persona los toca
      en el mismo orden en la app.

  3 errores seguidos bloquean 10 minutos. El código vence a los 2 minutos.
  Quien lo acierta queda autorizado 5 minutos, y solo desde esa conexión
  Bluetooth.

QUIÉN CAMBIA LA RED
===================

NetworkManager (nmcli), el que trae Raspberry Pi OS desde Bookworm. El
servicio corre como el usuario del microscopio, así que hace falta la
regla de polkit configs/polkit/51-microscopeos-red.rules (ver
docs/CONFIGURACION_BLUETOOTH.md).
"""

import random
import re
import secrets
import subprocess
import threading
import time

VENTANA_ARRANQUE_S = 10 * 60
CODIGO_VENCE_S = 120
AUTORIZACION_S = 5 * 60
INTENTOS = 3
BLOQUEO_S = 10 * 60
LARGO_CODIGO = 3

# Colores del código: distintos a simple vista en la matriz.
COLORES = {
    "rojo": "FF0000",
    "verde": "00FF00",
    "azul": "0000FF",
    "amarillo": "FFFF00",
    "magenta": "FF00FF",
    "cian": "00FFFF",
}


class ErrorConfiguracion(Exception):
    """Un mensaje para mostrar tal cual en la app."""


# ======================================================================
# NetworkManager
# ======================================================================
class RedNM:
    """La red de la Raspberry vía nmcli (NetworkManager)."""

    def __init__(self, interfaz="wlan0", ejecutar=None):
        self.interfaz = interfaz
        self._ejecutar = ejecutar or self._nmcli

    @staticmethod
    def _nmcli(args, tiempo=30):
        r = subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=tiempo)
        if r.returncode != 0:
            raise ErrorConfiguracion((r.stderr or r.stdout).strip() or f"nmcli falló ({r.returncode})")
        return r.stdout

    @staticmethod
    def _campos(linea):
        """nmcli -t separa con ':' y escapa ':' dentro de los valores como '\\:'."""
        return [c.replace("\\:", ":") for c in re.split(r"(?<!\\):", linea)]

    def estado(self):
        """{"conectado": bool, "red": ssid|None, "ip": "x.x.x.x"|None}"""
        try:
            general = self._ejecutar(["-t", "-f", "STATE", "general"]).strip()
        except (ErrorConfiguracion, OSError, subprocess.SubprocessError):
            return {"conectado": False, "red": None, "ip": None}
        red = ip = None
        try:
            for linea in self._ejecutar(["-t", "-f", "ACTIVE,SSID", "dev", "wifi"]).splitlines():
                c = self._campos(linea)
                if len(c) >= 2 and c[0] == "yes":
                    red = c[1] or None
            for linea in self._ejecutar(["-t", "-f", "IP4.ADDRESS", "dev", "show", self.interfaz]).splitlines():
                m = re.search(r"(\d+\.\d+\.\d+\.\d+)", linea)
                if m:
                    ip = m.group(1)
                    break
        except (ErrorConfiguracion, OSError, subprocess.SubprocessError):
            pass
        # «connected (site only)» = hay red local pero no internet: para
        # el microscopio eso basta.
        return {"conectado": general.startswith("connected") and ip is not None, "red": red, "ip": ip}

    def redes(self):
        """Redes Wi-Fi visibles, de la más fuerte a la más débil, sin repetir."""
        try:
            self._ejecutar(["dev", "wifi", "rescan"])
        except (ErrorConfiguracion, OSError, subprocess.SubprocessError):
            pass  # rescan falla si se pidió hace poco: se usa lo que hay
        vistas = {}
        for linea in self._ejecutar(["-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list"]).splitlines():
            c = self._campos(linea)
            if len(c) < 3 or not c[0]:
                continue
            senal = int(c[1]) if c[1].isdigit() else 0
            segura = c[2] not in ("", "--")
            if c[0] not in vistas or senal > vistas[c[0]][0]:
                vistas[c[0]] = (senal, segura)
        return sorted(([s, v[0], v[1]] for s, v in vistas.items()), key=lambda x: -x[1])

    def conectar(self, ssid, clave):
        """Se conecta y deja la red guardada (vuelve sola al reiniciar)."""
        args = ["dev", "wifi", "connect", ssid, "ifname", self.interfaz]
        if clave:
            args += ["password", clave]
        self._ejecutar(args, tiempo=60)


# ======================================================================
# El código de colores
# ======================================================================
class MostradorColores:
    """Muestra el código en las matrices de luz, en bucle, hasta que se
    acierta o vence. Después las deja como estaban."""

    def __init__(self, luces, encendido_s=1.2, apagado_s=0.35, pausa_s=1.6):
        self.luces = [l for l in (luces or {}).values() if l is not None]
        self.encendido_s, self.apagado_s, self.pausa_s = encendido_s, apagado_s, pausa_s
        self._parar = threading.Event()
        self._hilo = None

    def mostrar(self, colores):
        self.detener()
        self._parar = threading.Event()
        self._hilo = threading.Thread(target=self._bucle, args=(list(colores), self._parar), daemon=True)
        self._hilo.start()

    def detener(self):
        self._parar.set()
        if self._hilo is not None and self._hilo is not threading.current_thread():
            self._hilo.join(timeout=5)
        self._hilo = None

    def _bucle(self, colores, parar):
        previos = [(getattr(l, "color_campo", None), getattr(l, "current_pattern", "OFF"),
                    getattr(l, "state", False)) for l in self.luces]
        try:
            while not parar.is_set():
                for nombre in colores:
                    for l in self.luces:
                        l.set_color_campo(COLORES[nombre])
                        l.on()
                    if parar.wait(self.encendido_s):
                        return
                    for l in self.luces:
                        l.off()
                    if parar.wait(self.apagado_s):
                        return
                if parar.wait(self.pausa_s):
                    return
        finally:
            for l, (color, patron, encendida) in zip(self.luces, previos):
                try:
                    l.set_color_campo(color or "FFFFFF")
                    if encendida and patron == "FULL":
                        l.on()
                    else:
                        l.off()
                except Exception as e:  # una matriz desconectada no tumba el resto
                    print(f"[configuracion] no se pudo restaurar la luz: {e}")


# ======================================================================
# La lógica
# ======================================================================
class ConfiguracionRed:
    def __init__(self, red, mostrador, nombre="MicroscopeOS", puerto=8000,
                 timelapse_corriendo=lambda: False, reloj=time.monotonic, azar=None,
                 arranque=None):
        self.red = red
        self.mostrador = mostrador
        self.nombre = nombre
        self.puerto = puerto
        self.timelapse_corriendo = timelapse_corriendo
        self.reloj = reloj
        self._azar = azar or random.SystemRandom()
        self.arranque = self.reloj() if arranque is None else arranque
        self._lock = threading.RLock()
        self._codigo = None          # (colores, vence)
        self._errores = 0
        self._bloqueado_hasta = 0.0
        self._autorizado = {}        # conexion -> vence
        self.paso = "listo"          # listo | codigo | conectando | conectado | error
        self.error = None
        self.oyentes = []            # funciones que se llaman cuando cambia el estado

    # ---------------- estado ----------------
    def ventana_abierta(self, red=None):
        """(bool, motivo) — ¿se puede configurar el Wi-Fi ahora?"""
        if self.timelapse_corriendo():
            return False, "Hay un timelapse en curso"
        red = red or self.red.estado()
        if not red["conectado"]:
            return True, "Sin red"
        if self.reloj() - self.arranque < VENTANA_ARRANQUE_S:
            return True, "Recién encendido"
        return False, "Ya tiene red. Para cambiarla, reinícialo y configúralo en los primeros 10 minutos"

    def estado(self, conexion=None):
        """Lo que lee la app (JSON corto: entra en una lectura de Bluetooth)."""
        with self._lock:
            red = self.red.estado()
            abierta, motivo = self.ventana_abierta(red)
            ahora = self.reloj()
            return {
                "v": 1,
                "nombre": self.nombre,
                "conectado": red["conectado"],
                "red": red["red"],
                "ip": red["ip"],
                "puerto": self.puerto,
                "configurable": abierta,
                "motivo": motivo,
                "paso": self.paso,
                "error": self.error,
                "intentos": max(0, INTENTOS - self._errores),
                "bloqueado_s": max(0, int(self._bloqueado_hasta - ahora)),
                "autorizado": self._autorizada(conexion),
            }

    def _autorizada(self, conexion):
        vence = self._autorizado.get(conexion)
        return vence is not None and self.reloj() < vence

    def _cambio(self, paso=None, error=None):
        if paso is not None:
            self.paso = paso
        self.error = error
        for f in list(self.oyentes):
            try:
                f()
            except Exception as e:
                print(f"[configuracion] oyente: {e}")

    # ---------------- comandos ----------------
    def comando(self, datos, conexion):
        """Un comando de la app (dict). Devuelve el estado o lanza
        ErrorConfiguracion con un mensaje para mostrar."""
        cmd = datos.get("cmd")
        if cmd == "estado":
            return self.estado(conexion)
        if cmd == "pedir_codigo":
            return self.pedir_codigo(conexion)
        if cmd == "codigo":
            return self.verificar(datos.get("colores") or [], conexion)
        if cmd == "wifi":
            return self.configurar_wifi(datos.get("ssid", ""), datos.get("clave", ""), conexion)
        raise ErrorConfiguracion(f"Comando desconocido: {cmd!r}")

    def _exigir_ventana(self):
        abierta, motivo = self.ventana_abierta()
        if not abierta:
            raise ErrorConfiguracion(motivo)

    def _exigir_no_bloqueado(self):
        falta = int(self._bloqueado_hasta - self.reloj())
        if falta > 0:
            raise ErrorConfiguracion(f"Demasiados intentos. Espera {falta // 60 + 1} min")

    def pedir_codigo(self, conexion):
        with self._lock:
            self._exigir_ventana()
            self._exigir_no_bloqueado()
            colores = [self._azar.choice(list(COLORES)) for _ in range(LARGO_CODIGO)]
            self._codigo = (colores, self.reloj() + CODIGO_VENCE_S)
            self.mostrador.mostrar(colores)
            print("[configuracion] mostrando el código de colores en la luz")
            self._cambio("codigo")
            return self.estado(conexion)

    def verificar(self, colores, conexion):
        with self._lock:
            self._exigir_ventana()
            self._exigir_no_bloqueado()
            if self._codigo is None or self.reloj() > self._codigo[1]:
                self._codigo = None
                self.mostrador.detener()
                raise ErrorConfiguracion("El código venció. Pide uno nuevo")
            if [str(c).lower() for c in colores] != self._codigo[0]:
                self._errores += 1
                if self._errores >= INTENTOS:
                    self._bloqueado_hasta = self.reloj() + BLOQUEO_S
                    self._errores = 0
                    self._codigo = None
                    self.mostrador.detener()
                    self._cambio("listo")
                    raise ErrorConfiguracion("Demasiados intentos. Espera 10 min")
                raise ErrorConfiguracion(f"Los colores no coinciden. Quedan {INTENTOS - self._errores} intentos")
            self._codigo = None
            self._errores = 0
            self.mostrador.detener()
            self._autorizado[conexion] = self.reloj() + AUTORIZACION_S
            self._cambio("listo")
            return self.estado(conexion)

    def configurar_wifi(self, ssid, clave, conexion):
        ssid = (ssid or "").strip()
        with self._lock:
            self._exigir_ventana()
            if not self._autorizada(conexion):
                raise ErrorConfiguracion("Primero confirma los colores de la luz")
            if not ssid or len(ssid) > 32:
                raise ErrorConfiguracion("Nombre de red inválido")
            if clave and not (8 <= len(clave) <= 63):
                raise ErrorConfiguracion("La contraseña del Wi-Fi tiene entre 8 y 63 caracteres")
            self._cambio("conectando")
        # Fuera del lock: conectarse tarda varios segundos.
        try:
            self.red.conectar(ssid, clave)
        except (ErrorConfiguracion, OSError, subprocess.SubprocessError) as e:
            self._cambio("error", _mensaje_nmcli(str(e)))
            raise ErrorConfiguracion(self.error)
        # La IP puede tardar un poco en aparecer.
        for _ in range(20):
            if self.red.estado()["ip"]:
                break
            time.sleep(0.5)
        with self._lock:
            self._autorizado.pop(conexion, None)
            self._cambio("conectado")
            return self.estado(conexion)

    def olvidar(self, conexion):
        """La conexión Bluetooth se cerró."""
        with self._lock:
            self._autorizado.pop(conexion, None)


def _mensaje_nmcli(texto):
    t = texto.lower()
    if "secrets were required" in t or "password" in t or "802-11-wireless-security" in t:
        return "La contraseña del Wi-Fi no es correcta"
    if "no network with ssid" in t:
        return "No se encontró esa red"
    return f"No se pudo conectar: {texto[:120]}"


def nombre_del_equipo():
    """«MicroscopeOS-3F2A»: los últimos 4 del número de serie de la Pi (o al
    azar si no se puede leer), para distinguir dos microscopios."""
    try:
        with open("/proc/device-tree/serial-number") as f:
            serie = f.read().strip("\x00\n ")
        if serie:
            return f"MicroscopeOS-{serie[-4:].upper()}"
    except OSError:
        pass
    return f"MicroscopeOS-{secrets.token_hex(2).upper()}"
