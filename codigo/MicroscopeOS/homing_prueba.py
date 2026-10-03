"""Prueba del homing sin sensor (StallGuard del TMC2209). EXPERIMENTAL.

El objetivo baja despacio hasta el piso del microscopio; el driver nota
que el motor se frena y ese punto queda como cero del eje. Ver
FocusMotorController.homing() en core/motor_focus.py.

ANTES DE EMPEZAR
  - Detener el servidor (usa los mismos GPIO y el mismo UART):
        sudo systemctl stop microscopeos
  - QUITAR LA MUESTRA. Si "+" resultara ser hacia la muestra y no hacia
    el piso, el objetivo iria contra ella.
  - Tener la mano cerca del enchufe de los 12 V de los motores: si algo
    suena mal, cortar la alimentacion.

PASO 1: ver cuanto da SG_RESULT con el motor libre (no busca el tope).
  Baja MEDIR_UM y vuelve a subir lo mismo, asi que hace falta al menos
  ese espacio libre por debajo:

    python3 homing_prueba.py medir 0

PASO 2: el homing. Arrancar a 1-3 mm del piso (las primeras lecturas
  sirven para calcular el umbral, asi que no puede chocar enseguida):

    python3 homing_prueba.py homing 0
    python3 homing_prueba.py homing 0 --umbral 80     # umbral a mano

Cada corrida guarda las lecturas en homing_cam<N>_<fecha>.csv
(micras recorridas, SG_RESULT) para graficarlas.

Que mirar:
  - En "medir", SG_RESULT tiene que ser estable y bien por encima de 0.
    Si sale cerca de 0 o salta mucho, el motor va demasiado lento para
    StallGuard: subir --velocidad.
  - En "homing", que pare al tocar el piso, sin que el motor "salte"
    pasos con ruido de golpeteo. Si golpetea antes de parar, bajar
    --fraccion (umbral mas alto) o --corriente.
  - Repetir el homing varias veces y anotar cuanto cambia la posicion
    del piso: esa es la repetibilidad del cero.
"""
import argparse
import csv
import time
from datetime import datetime

from core.motor_focus import FocusMotorController, PINES_POR_CAMARA

MEDIR_UM = 1000.0

p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
p.add_argument("modo", choices=["medir", "homing"])
p.add_argument("cam", type=int, nargs="?", default=0)
p.add_argument("--sentido", type=int, choices=[1, -1], default=1,
               help="1 = la plataforma BAJA (por defecto), -1 = SUBE")
p.add_argument("--velocidad", type=float, default=1000.0, help="um/s")
p.add_argument("--corriente", type=int, default=250, help="mA durante el homing")
p.add_argument("--umbral", type=float, default=None,
               help="SG_RESULT para considerar frenado (por defecto, automatico)")
p.add_argument("--fraccion", type=float, default=0.5,
               help="umbral automatico = fraccion x linea base")
p.add_argument("--max", type=float, default=15000.0,
               help="recorrido maximo en um antes de rendirse")
a = p.parse_args()

if a.cam not in PINES_POR_CAMARA:
    raise SystemExit(f"No hay eje para la camara {a.cam}")

motor = FocusMotorController(max_current_ma=550, microsteps=16,
                             nombre=f"foco_cam{a.cam}", **PINES_POR_CAMARA[a.cam])
lado = "BAJA" if a.sentido > 0 else "SUBE"
try:
    print("DRV_STATUS:", motor.leer_estado())
    if a.modo == "medir":
        input(f"Va a moverse {MEDIR_UM:.0f} um (la plataforma {lado}) y "
              f"volver. Sin muestra y con ese espacio libre. ENTER para seguir...")
        r = motor.homing(direction=a.sentido, velocidad_um_s=a.velocidad,
                         irun_ma=a.corriente, recorrido_max_um=MEDIR_UM,
                         solo_medir=True)
        motor.mover_um(-a.sentido * r["recorrido_um"])
    else:
        input(f"HOMING: la plataforma {lado} hasta que el motor se frene "
              f"(maximo {a.max:.0f} um). SIN MUESTRA. ENTER para seguir...")
        r = motor.homing(direction=a.sentido, velocidad_um_s=a.velocidad,
                         irun_ma=a.corriente, umbral=a.umbral,
                         fraccion_umbral=a.fraccion, recorrido_max_um=a.max)

    print()
    for clave in ("ok", "motivo", "recorrido_um", "velocidad_um_s_real",
                  "linea_base", "umbral", "sg_min", "sg_mediana", "sg_max",
                  "posicion_um", "referenciado"):
        print(f"  {clave:20s} {r[clave]}")
    print("DRV_STATUS:", motor.leer_estado())

    nombre = f"homing_cam{a.cam}_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(nombre, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["um_recorridos", "sg_result"])
        w.writerows(r["lecturas"])
    print(f"Lecturas en {nombre}")
finally:
    time.sleep(0.2)
    motor.close()
