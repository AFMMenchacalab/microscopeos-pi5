# Prueba de motor paso a paso.
#
# Pi 5: NO instalar RPi.GPIO con pip (la libreria clasica no funciona sobre
# el southbridge RP1). El import de abajo lo resuelve rpi-lgpio, el shim que
# reimplementa la API de RPi.GPIO sobre lgpio:
#
#     sudo apt install python3-rpi-lgpio
#     sudo apt remove python3-rpi.gpio      # si estuviera instalada
#
# La Pi 4 de origen ya corria rpi-lgpio 0.6, asi que este codigo no cambia.
#
# TODO-HW: verificar que BCM 20/21 siguen libres. En Pi 4 los ocupaba (o no)
# el overlay camera-mux-4port, ya eliminado; comprobar con `pinctrl get 20,21`.
# TODO-HW: `delay = 0.005` es temporizado por software. rpi-lgpio tiene otra
# latencia por llamada que RPi.GPIO nativa; si el motor pierde pasos o suena
# distinto, recalibrar este valor.
import RPi.GPIO as GPIO
import time

DIR = 20
STEP = 21

GPIO.setmode(GPIO.BCM)
GPIO.setup(DIR, GPIO.OUT)
GPIO.setup(STEP, GPIO.OUT)

GPIO.output(DIR, GPIO.HIGH)

delay = 0.005  # más estable

try:
    for i in range(800):
        GPIO.output(STEP, GPIO.HIGH)
        time.sleep(delay)
        GPIO.output(STEP, GPIO.LOW)
        time.sleep(delay)

    time.sleep(1)

    GPIO.output(DIR, GPIO.LOW)

    for i in range(800):
        GPIO.output(STEP, GPIO.HIGH)
        time.sleep(delay)
        GPIO.output(STEP, GPIO.LOW)
        time.sleep(delay)

finally:
    GPIO.cleanup()
