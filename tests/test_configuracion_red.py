"""Pruebas sin hardware de la configuración de red por Bluetooth
(core/configuracion_red.py y el protocolo de core/servicio_bluetooth.py).

    cd tests
    SP=$PWD PROY=$PWD/../codigo/MicroscopeOS python3 test_configuracion_red.py
"""
import json
import os
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("SP", AQUI))
sys.path.insert(0, os.environ.get("PROY", os.path.join(AQUI, "..", "codigo", "MicroscopeOS")))

from core import configuracion_red as CR
from core import servicio_bluetooth as SB

ok = fail = 0


def check(nombre, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {nombre}")
    else:
        fail += 1
        print(f"  FAIL  {nombre}  {extra}")


def falla(f, *a):
    try:
        f(*a)
    except CR.ErrorConfiguracion as e:
        return str(e)
    return None


class Reloj:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class RedFalsa:
    def __init__(self, conectado=False):
        self.conectado = conectado
        self.conexiones = []
        self.clave_buena = "clave-del-lab"

    def estado(self):
        if self.conectado:
            return {"conectado": True, "red": "Lab", "ip": "192.168.1.50"}
        return {"conectado": False, "red": None, "ip": None}

    def redes(self):
        return [["Lab", 80, True], ["Invitados", 40, False]]

    def conectar(self, ssid, clave):
        self.conexiones.append((ssid, clave))
        if clave != self.clave_buena:
            raise CR.ErrorConfiguracion("Error: Connection activation failed: Secrets were required, but not provided")
        self.conectado = True


class MostradorFalso:
    def __init__(self):
        self.mostrando = None
        self.veces = 0

    def mostrar(self, colores):
        self.mostrando = list(colores)
        self.veces += 1

    def detener(self):
        self.mostrando = None


def nueva(conectado=False, timelapse=False):
    reloj = Reloj()
    red, mostrador = RedFalsa(conectado), MostradorFalso()
    cfg = CR.ConfiguracionRed(red, mostrador, nombre="MicroscopeOS-TEST", reloj=reloj,
                              timelapse_corriendo=lambda: timelapse)
    return cfg, red, mostrador, reloj


print("\n=== CUÁNDO SE PUEDE CONFIGURAR ===")
cfg, red, mos, reloj = nueva(conectado=False)
e = cfg.estado("tel")
check("sin red: configurable", e["configurable"] and e["motivo"] == "Sin red")
check("el estado trae nombre, IP y puerto", e["nombre"] == "MicroscopeOS-TEST" and e["ip"] is None and e["puerto"] == 8000)
check("el estado entra en una lectura de Bluetooth", len(json.dumps(e).encode()) < 500)

cfg, red, mos, reloj = nueva(conectado=True)
check("con red y recién encendido: configurable", cfg.estado()["configurable"])
reloj.t += CR.VENTANA_ARRANQUE_S + 1
e = cfg.estado()
check("con red y pasados 10 min: NO", not e["configurable"] and "10 minutos" in e["motivo"])
check("pero la IP se puede leer siempre (resincronizar)", e["ip"] == "192.168.1.50" and e["red"] == "Lab")
check("y pedir el código falla con el motivo", "10 minutos" in (falla(cfg.pedir_codigo, "tel") or ""))

cfg, red, mos, reloj = nueva(conectado=False, timelapse=True)
check("con un timelapse corriendo, nunca", not cfg.estado()["configurable"]
      and falla(cfg.pedir_codigo, "tel") == "Hay un timelapse en curso")
check("y la luz no se toca", mos.veces == 0)

print("\n=== CÓDIGO DE COLORES ===")
cfg, red, mos, reloj = nueva()
cfg.pedir_codigo("tel")
codigo = list(mos.mostrando)
check("muestra 3 colores en la luz", len(codigo) == 3 and all(c in CR.COLORES for c in codigo))
check("el estado no revela el código", all(c not in json.dumps(cfg.estado()) for c in ("rojo", "verde", "azul", "amarillo", "magenta", "cian"))
      and cfg.estado()["paso"] == "codigo")
check("sin el código no se puede configurar el Wi-Fi",
      falla(cfg.configurar_wifi, "Lab", "clave-del-lab", "tel") == "Primero confirma los colores de la luz")
otro = [c for c in CR.COLORES if c != codigo[0]][0]
mal = [otro] + codigo[1:]
check("colores equivocados: quedan 2", "Quedan 2" in (falla(cfg.verificar, mal, "tel") or ""))
check("todavía se muestra", mos.mostrando == codigo)
check("acertar autoriza a ESA conexión", cfg.verificar([c.upper() for c in codigo], "tel")["autorizado"])
check("y apaga el código de la luz", mos.mostrando is None)
check("otra conexión no queda autorizada", not cfg.estado("otro")["autorizado"])
check("el código no sirve dos veces", "venció" in (falla(cfg.verificar, codigo, "otro") or ""))

print("\n=== BLOQUEO Y VENCIMIENTO ===")
cfg, red, mos, reloj = nueva()
cfg.pedir_codigo("x")
codigo = list(mos.mostrando)
mal = [[c for c in CR.COLORES if c != codigo[0]][0]] + codigo[1:]
falla(cfg.verificar, mal, "x")
falla(cfg.verificar, mal, "x")
check("al 3er error se bloquea 10 min", "Espera 10 min" in (falla(cfg.verificar, mal, "x") or ""))
check("bloqueado: no da otro código", "Demasiados intentos" in (falla(cfg.pedir_codigo, "x") or ""))
check("el estado dice cuánto falta", cfg.estado()["bloqueado_s"] > 500)
reloj.t += CR.BLOQUEO_S + 1
cfg.pedir_codigo("x")
check("pasados 10 min, se puede de nuevo", mos.mostrando is not None)
reloj.t += CR.CODIGO_VENCE_S + 1
check("el código vence a los 2 min", "venció" in (falla(cfg.verificar, mos.mostrando or ["rojo"] * 3, "x") or ""))

print("\n=== WI-FI ===")
cfg, red, mos, reloj = nueva()
cfg.pedir_codigo("tel")
cfg.verificar(list(mos.mostrando), "tel")
check("contraseña corta: se avisa sin intentar", "8 y 63" in (falla(cfg.configurar_wifi, "Lab", "123", "tel") or "")
      and red.conexiones == [])
check("contraseña equivocada: mensaje claro",
      falla(cfg.configurar_wifi, "Lab", "otra-clave", "tel") == "La contraseña del Wi-Fi no es correcta")
check("y el estado lo muestra", cfg.estado("tel")["paso"] == "error")
e = cfg.configurar_wifi("Lab", "clave-del-lab", "tel")
check("conectado: devuelve la IP", e["conectado"] and e["ip"] == "192.168.1.50" and e["paso"] == "conectado")
check("la autorización se usa una vez", not e["autorizado"])
cfg2, red2, mos2, _ = nueva()
red2.clave_buena = ""
cfg2.pedir_codigo("t")
cfg2.verificar(list(mos2.mostrando), "t")
check("red abierta (sin clave) se acepta", cfg2.configurar_wifi("Invitados", "", "t")["conectado"]
      and red2.conexiones == [("Invitados", "")])

print("\n=== COMANDOS (lo que manda la app) ===")
cfg, red, mos, reloj = nueva()
check("estado", cfg.comando({"cmd": "estado"}, "t")["configurable"])
cfg.comando({"cmd": "pedir_codigo"}, "t")
cfg.comando({"cmd": "codigo", "colores": mos.mostrando}, "t")
check("wifi", cfg.comando({"cmd": "wifi", "ssid": "Lab", "clave": "clave-del-lab"}, "t")["conectado"])
check("comando desconocido", "desconocido" in (falla(cfg.comando, {"cmd": "borrar_todo"}, "t") or ""))

print("\n=== NMCLI (salida real, sin ejecutarlo) ===")
salidas = {
    ("-t", "-f", "STATE", "general"): "connected\n",
    ("-t", "-f", "ACTIVE,SSID", "dev", "wifi"): "no:Vecinos\nyes:Lab\\:2\n",
    ("-t", "-f", "IP4.ADDRESS", "dev", "show", "wlan0"): "IP4.ADDRESS[1]:192.168.1.50/24\n",
    ("-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list"): "Lab:70:WPA2\nLab:85:WPA2\n:30:WPA2\nCafe:50:--\n",
}
pedidos = []


def nmcli_falso(args, tiempo=30):
    pedidos.append(tuple(args))
    return salidas.get(tuple(args), "")


nm = CR.RedNM(ejecutar=nmcli_falso)
check("estado: SSID con ':' escapado e IP", nm.estado() == {"conectado": True, "red": "Lab:2", "ip": "192.168.1.50"})
check("redes: sin repetir, sin ocultas, de mayor a menor señal",
      nm.redes() == [["Lab", 85, True], ["Cafe", 50, False]])
nm.conectar("Lab", "clave-del-lab")
check("conectar usa nmcli dev wifi connect",
      pedidos[-1] == ("dev", "wifi", "connect", "Lab", "ifname", "wlan0", "password", "clave-del-lab"))

print("\n=== SERVICIO BLUETOOTH (sin BlueZ) ===")
cfg, red, mos, reloj = nueva()
svc = SB.ServicioBluetooth(cfg)


class LoopFalso:
    def __init__(self):
        self.tareas = []

    def run_in_executor(self, _, f, *a):
        self.tareas.append((f, a))

    def call_soon_threadsafe(self, f, *a):
        f(*a)


svc._loop = LoopFalso()
pedido = json.dumps({"id": 7, "cmd": "pedir_codigo"}).encode()
svc._escribir(pedido[:10], {"device": "/org/bluez/hci0/dev_AA"})
check("una escritura cortada espera el resto", svc._loop.tareas == [])
svc._escribir(pedido[10:], {"device": "/org/bluez/hci0/dev_AA", "offset": 10})
check("con el resto (offset) se atiende", len(svc._loop.tareas) == 1)
f, a = svc._loop.tareas.pop()
f(*a)
estado = json.loads(svc._json_estado("/org/bluez/hci0/dev_AA"))
check("el estado dice qué comando se atendió", estado["ultimo"] == 7 and estado["paso"] == "codigo")
svc._escribir(json.dumps({"id": 8, "cmd": "codigo", "colores": ["x", "x", "x"]}).encode(), {"device": "d"})
f, a = svc._loop.tareas.pop()
f(*a)
estado = json.loads(svc._json_estado())
check("un error vuelve en el estado, con texto", estado["ultimo"] == 8 and "no coinciden" in estado["error"])
check("la lista de redes entra en una lectura", len(svc._json_redes()) <= 500)
check("importar no exige dbus-next ni BlueZ", SB.UUID_SERVICIO.startswith("6d6f732d"))

print("\n=== MOSTRADOR DE COLORES (luces falsas) ===")


class LuzFalsa:
    def __init__(self):
        self.color_campo, self.current_pattern, self.state = None, "FULL", True
        self.historial = []

    def set_color_campo(self, c):
        self.color_campo = None if c.upper() == "FFFFFF" else c.upper()

    def on(self):
        self.current_pattern, self.state = "FULL", True
        self.historial.append(self.color_campo)

    def off(self):
        self.current_pattern, self.state = "OFF", False


luz = LuzFalsa()
m = CR.MostradorColores({0: luz}, encendido_s=0.02, apagado_s=0.01, pausa_s=0.02)
m.mostrar(["rojo", "verde", "azul"])
time.sleep(0.2)
m.detener()
check("muestra los colores en orden", luz.historial[:3] == ["FF0000", "00FF00", "0000FF"], luz.historial[:4])
check("y deja la luz como estaba (campo claro blanco encendido)",
      luz.color_campo is None and luz.state and luz.current_pattern == "FULL")

print(f"\n{ok} pasaron, {fail} fallaron")
sys.exit(1 if fail else 0)
