"""Prueba de un motor de enfoque (NEMA11 28HB30-401A + TMC2209 por UART).

    python3 test_motor_enfoque.py [numero_de_camara]

Sin argumento prueba el eje de la camara 0. Con `1`, el de la camara 1
(el segundo driver, direccion UART 1). Correr primero test_uart_only.py:
si el driver no contesta por UART, esto no llega ni a mover.

PASOS esta en MICROpasos a la resolucion que se configure abajo. Con
microsteps=1 (full step) son pasos completos: 200 = una vuelta del motor.

Requiere dtparam=uart0=on en config.txt y el cableado descrito en el
docstring de core/motor_focus.py.
"""
import sys
import time

from core.motor_focus import (FocusMotorController, TMCUartError,
                              PINES_POR_CAMARA)

CAM = int(sys.argv[1]) if len(sys.argv) > 1 else 0
CORRIENTE_PRUEBA_MA = 450
# full step (microsteps=1), pocas vueltas para confirmar orientacion.
PASOS = 200           # una vuelta completa
DELAY = 0.003

if CAM not in PINES_POR_CAMARA:
    raise SystemExit(f"No hay eje definido para la camara {CAM}. "
                     f"Definidos: {sorted(PINES_POR_CAMARA)}")

pines = PINES_POR_CAMARA[CAM]
print(f"Eje de enfoque de la camara {CAM}: STEP={pines['step_pin']} "
      f"DIR={pines['dir_pin']} EN={pines['en_pin']} "
      f"direccion UART={pines['uart_address']}")

motor = FocusMotorController(max_current_ma=550, microsteps=1,
                             nombre=f"foco_cam{CAM}", **pines)

try:
    irun, ihold = motor.set_current(irun_ma=CORRIENTE_PRUEBA_MA)
    print(f"IRUN real: {irun}mA  IHOLD real: {ihold}mA "
          f"(pedido: {CORRIENTE_PRUEBA_MA}mA)")

    estado = motor.leer_estado()
    print("DRV_STATUS antes de mover:", estado)
    if estado["sobretemp_corte"] or estado["corto_fase_a"] or estado["corto_fase_b"]:
        raise SystemExit("Fallo reportado por el driver ANTES de mover -- "
                          "revisar cableado de las bobinas antes de continuar.")

    input(f"Listo para mover el eje de cam{CAM}. Confirma que el motor "
          f"puede girar libremente (sin nada trabado) y presiona ENTER...")

    print(f"Moviendo {PASOS} pasos en sentido + (la plataforma BAJA)...")
    motor.mover(PASOS, direction=1, delay=DELAY)
    time.sleep(0.5)
    print("DRV_STATUS tras mover +:", motor.leer_estado())

    time.sleep(1)

    print(f"Moviendo {PASOS} pasos en sentido - (la plataforma SUBE)...")
    motor.mover(PASOS, direction=-1, delay=DELAY)
    time.sleep(0.5)
    estado = motor.leer_estado()
    print("DRV_STATUS tras mover -:", estado)
    print(f"Posicion relativa final: {motor.position} (deberia ser 0)")

    if estado["sobretemp_aviso"]:
        print("AVISO: OTPW activo -- el driver ya esta tibio. No subir "
              "la corriente sin dejarlo enfriar y sin verificar el "
              "cuerpo del motor a mano.")
    print("\nSi el motor VIBRA sin avanzar: casi seguro una bobina tiene "
          "los dos cables invertidos entre si (rompe la cuadratura entre "
          "fases). Paso exactamente eso con el eje de cam0.")

except TMCUartError as e:
    print(f"Error de comunicacion UART: {e}")
    print("Revisar: VM(12V) conectado, VIO->3.3V, GND comun, nodo PDN, "
          "MS1/MS2 en la direccion correcta, dtparam=uart0=on aplicado "
          "y la Pi reiniciada.")
finally:
    motor.close()
