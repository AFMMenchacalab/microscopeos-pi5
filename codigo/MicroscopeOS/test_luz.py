import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.illumination import IlluminationController

luz = IlluminationController()
print("ID de la placa:", luz.id())

print("Encendiendo...")
luz.on()
time.sleep(2)

print("Brillo a la mitad...")
luz.set_brightness(50)   # era 120: la funcion clampa a 100, el comentario mentia
luz.on()
time.sleep(2)

print("Apagando...")
luz.off()

luz.close()
print("Prueba terminada.")
