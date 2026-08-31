from machine import Pin
from neopixel import NeoPixel
import time

np = NeoPixel(Pin(16), 25)

def limpiar():
    for j in range(25):
        np[j] = (0, 0, 0)
    np.write()

limpiar()
time.sleep(0.5)

for i in range(25):
    limpiar()
    np[i] = (50, 50, 50)
    np.write()
    print("LED", i)
    time.sleep(0.6)

limpiar()
print("Fin del mapeo")
