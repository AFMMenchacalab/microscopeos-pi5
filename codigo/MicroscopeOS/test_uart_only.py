"""Prueba minima de UART con los TMC2209, sin tocar GPIO de STEP/DIR/EN
ni habilitar ningun motor. Solo confirma que los drivers contestan por
PDN antes de arriesgar nada mecanico.

Es lo PRIMERO que hay que correr al cablear un driver nuevo: barre las 4
direcciones posibles del bus y dice cual contesta, asi se ve de una si
los pines MS1/AD0 y MS2/AD1 quedaron en la direccion que se pretendia.

    python3 test_uart_only.py

Requiere: VIO 3.3V + VM 12V conectados (sin VM el chip NO contesta) y el
cableado del docstring de core/motor_focus.py (nodo unico PDN con
resistencia 1k en TXD0, los dos drivers colgados del mismo nodo).
"""
from core.motor_focus import (TMC2209Bus, TMCUartError, REG_GCONF,
                              PINES_POR_CAMARA)

ESPERADAS = {p["uart_address"]: f"foco cam{cam}"
             for cam, p in PINES_POR_CAMARA.items()}

bus = TMC2209Bus("/dev/ttyAMA0")
encontradas = []
try:
    for direccion in range(4):
        etiqueta = ESPERADAS.get(direccion, "(sin asignar)")
        try:
            valor = bus.read(direccion, REG_GCONF)
            encontradas.append(direccion)
            print(f"  direccion {direccion} {etiqueta:16s} RESPONDE  "
                  f"GCONF = 0x{valor:08X}")
        except TMCUartError:
            print(f"  direccion {direccion} {etiqueta:16s} sin respuesta")
finally:
    bus.close()

print()
faltan = [d for d in ESPERADAS if d not in encontradas]
if not faltan:
    print("Los dos ejes contestan: el bus multi-esclavo esta bien armado.")
elif encontradas:
    print(f"Contestan {encontradas} pero falta(n) {faltan} "
          f"({', '.join(ESPERADAS[d] for d in faltan)}).")
    print("Si el que falta es un driver recien cableado, revisar en este "
          "orden: VM(12V) conectado, MS1/AD0 y MS2/AD1 en la combinacion "
          "de su direccion, PDN al MISMO nodo que el otro driver, GND "
          "comun con la Pi.")
else:
    print("No contesta ninguno. Revisar: VM(12V) puesto (sin el, el chip "
          "no responde y parece muerto), nodo PDN (resistencia 1k en "
          "TXD0, RXD0 directo al mismo nodo), GND comun, VIO 3.3V, "
          "dtparam=uart0=on aplicado y la Pi reiniciada.")
