"""Probar la configuración por Bluetooth sin la Raspberry.

    python3 probar_bluetooth.py --simular

Corre el servicio Bluetooth de verdad (core/servicio_bluetooth.py) en esta
computadora, pero con:
- una red falsa: NO toca el Wi-Fi de esta computadora. «Conectarse» tarda
  3 s y acepta cualquier contraseña de 8 o más caracteres, salvo «mala1234»;
  después informa la IP real de esta computadora, para que el teléfono
  pueda seguir con el servidor falso (app_movil/dev_server).
- luces falsas: los colores del código se pintan aquí, en la terminal.

Necesita Bluetooth en la computadora y el paquete dbus-next. No cambia el
nombre Bluetooth de la computadora ni su agente de emparejamiento: al
emparejar, el escritorio puede pedir que lo confirmes.
Sin --simular no hace nada (para no cambiar la red de la Pi por error).
"""
import socket
import sys
import time

from core import configuracion_red as CR
from core import servicio_bluetooth as SB


def ip_local():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))  # no envía nada: solo elige la interfaz
        return s.getsockname()[0]
    finally:
        s.close()


class RedSimulada:
    def __init__(self):
        self.conectado, self.ssid = False, None

    def estado(self):
        return {"conectado": self.conectado, "red": self.ssid, "ip": ip_local() if self.conectado else None}

    def redes(self):
        return [["Laboratorio", 82, True], ["Laboratorio-5G", 64, True], ["Invitados", 41, False]]

    def conectar(self, ssid, clave):
        print(f"[simulado] conectando a «{ssid}»…")
        time.sleep(3)
        if clave == "mala1234":
            raise CR.ErrorConfiguracion("Secrets were required, but not provided")
        self.conectado, self.ssid = True, ssid
        print(f"[simulado] conectado: {ip_local()}")


class LuzTerminal:
    """Pinta el color de la «matriz» en la terminal."""
    color_campo, current_pattern, state = None, "OFF", False

    def set_color_campo(self, c):
        self.color_campo = None if c.upper() == "FFFFFF" else c.upper()

    def on(self):
        self.state = True
        c = self.color_campo or "FFFFFF"
        r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
        nombre = next((n for n, h in CR.COLORES.items() if h == c), c)
        print(f"\x1b[48;2;{r};{g};{b}m          \x1b[0m  {nombre}", flush=True)

    def off(self):
        self.state = False


if __name__ == "__main__":
    if "--simular" not in sys.argv:
        print(__doc__)
        sys.exit(1)
    cfg = CR.ConfiguracionRed(RedSimulada(), CR.MostradorColores({0: LuzTerminal()}),
                              nombre="MicroscopeOS-PRUEBA")
    if SB.iniciar(cfg, modo_pi=False) is None:
        sys.exit(1)
    print("Busca «MOS-PRUE» desde la app (Microscopios → Buscar por Bluetooth). Ctrl+C para salir.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
