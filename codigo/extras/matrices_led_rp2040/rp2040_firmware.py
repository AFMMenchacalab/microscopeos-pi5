# Firmware RP2040-Matrix v3: iluminacion blanco + patrones DPC
import sys
from machine import Pin
from neopixel import NeoPixel

NUM_LEDS = 25
LED_PIN = 16

np = NeoPixel(Pin(LED_PIN), NUM_LEDS)
brightness = 255

# Mapas de patrones (confirmados visualmente)
PATRONES = {
    "ALL":    list(range(25)),
    "LEFT":   [0,1,2,3,4, 5,6,7,8,9],
    "RIGHT":  [15,16,17,18,19, 20,21,22,23,24],
    "TOP":    [3,4, 8,9, 13,14, 18,19, 23,24],
    "BOTTOM": [0,1, 5,6, 10,11, 15,16, 20,21],
}

def limpiar():
    for j in range(NUM_LEDS):
        np[j] = (0, 0, 0)
    np.write()

def prender(lista):
    limpiar()
    for j in lista:
        np[j] = (brightness, brightness, brightness)
    np.write()

def procesar(cmd):
    global brightness
    cmd = cmd.strip().upper()

    if cmd == "ON" or cmd == "ALL":
        prender(PATRONES["ALL"])
        return "OK " + cmd
    elif cmd == "OFF":
        limpiar()
        return "OK OFF"
    elif cmd in PATRONES:
        prender(PATRONES[cmd])
        return "OK " + cmd
    elif cmd.startswith("BRIGHT"):
        try:
            n = int(cmd.split()[1])
            brightness = max(0, min(255, n))
            return "OK BRIGHT " + str(brightness)
        except (IndexError, ValueError):
            return "ERR brillo invalido"
    elif cmd == "":
        return None
    else:
        return "ERR desconocido: " + cmd

# Apagar al arrancar
limpiar()

# Lectura serial robusta (caracter por caracter)
buffer = ""
while True:
    c = sys.stdin.read(1)
    if c:
        if c == "\n" or c == "\r":
            if buffer:
                resp = procesar(buffer)
                if resp:
                    print(resp)
                buffer = ""
        else:
            buffer += c
