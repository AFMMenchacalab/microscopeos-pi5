import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.camera import CameraController
from core.illumination import IlluminationController
from core.timelapse import TimelapseManager
from core.motor_focus import crear_motores
from core.autofocus import Autofocus
from core.autofocus_ia import AutofocoIA
from core.analisis import Contador, ContadorEnVivo
from server.api import create_app
import uvicorn

print("Inicializando hardware...")
camera = CameraController()
camera.set_exposure(12000, 1.2)

# Una matriz por camara, identificadas por udev (nombres fijos).
# Placas Waveshare ESP32-S3-Matrix (8x8), antes RP2040 (5x5).
# Los symlinks no cambian; la regla udev si (VID:PID 303a:1001).
print("Conectando matrices de iluminacion...")
# max_value=46: tope de corriente para alimentacion USB (README de la
# matriz, "Potencia" -- FULL:255 son ~3.8A, un puerto USB da 0.5-0.9A).
# Resuelto 2026-08-31: este era el TODO-HW de prioridad 1 de mas abajo.
# Con las RP2040 de 5x5 el 80% de brillo era inofensivo; con las 8x8
# actuales no lo es, asi que ahora el tope vive en max_value, no en el
# porcentaje -- set_brightness(80) sigue pidiendo "80%" pero _valor()
# lo clampea a 46 igual que en verificar_pi5.py.
luz_cam0 = IlluminationController(port="/dev/matriz_cam0", max_value=46)
luz_cam1 = IlluminationController(port="/dev/matriz_cam1", max_value=46)
illuminations = {0: luz_cam0, 1: luz_cam1}

luz_cam0.set_brightness(80)
luz_cam1.set_brightness(80)

# Un motor de enfoque por camara, los dos en el mismo bus UART con
# direcciones distintas (ver el docstring de core/motor_focus.py).
# crear_motores no tira excepcion si un eje no contesta: el microscopio
# tiene que arrancar igual con un solo motor cableado, o con ninguno,
# porque esto corre como servicio en el boot.
print("Conectando motores de enfoque...")
try:
    motores, bus_motores = crear_motores(camaras=(0, 1),
                                         max_current_ma=550, microsteps=16)
except Exception as e:
    print(f"[motor] bus UART no disponible -> {e}")
    motores, bus_motores = {}, None

# 450mA: corriente con la que el eje de cam0 giro limpio y sin avisos
# termicos. El tope duro sigue siendo max_current_ma=550.
for _m in motores.values():
    _m.set_current(irun_ma=450)

# Autofoco IA: se construye siempre pero solo se activa si hay un
# modelo entrenado en profiles/autofoco_ia.onnx. Sin ese archivo,
# disponible() da False y el autofoco usa el metodo analitico de
# siempre, sin cambiar nada. Ver core/autofocus_ia.py.
ia = AutofocoIA()
if ia.error:
    print(f"[autofoco IA] {ia.error} -- se usa el metodo analitico")

autofocus = Autofocus(camera, motores, illuminations, ia=ia) if motores else None

# Conteo de celulas. No depende de ningun hardware extra: son las
# mismas capturas, analizadas. Arranca APAGADO en el vivo (se enciende
# por camara desde la interfaz) porque cuesta CPU y no siempre se
# quiere; el contador en si se crea siempre.
contador = Contador()
conteo = ContadorEnVivo(contador, periodo=0.6)

timelapse = TimelapseManager(camera, illuminations, autofocus=autofocus,
                             contador=contador)

app = create_app(camera, illuminations, timelapse,
                 motores=motores, autofocus=autofocus, conteo=conteo)
print("Servidor en http://0.0.0.0:8000")
uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
