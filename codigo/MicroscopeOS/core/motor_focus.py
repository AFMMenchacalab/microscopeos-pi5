r"""Control de los motores NEMA11 de enfoque (eje Z) via drivers TMC2209.

Hay UN motor de enfoque por camara: cada canal optico tiene su propia
plataforma lineal, asi que son dos ejes Z independientes.

Hardware por eje: modulo TMC2209 generico "V985" (mismo pinout que las
placas BIGTREETECH TMC2209 V1.2/V1.3), motor NEMA11 28mm 28HB30-401A
(0.6A/fase, 1.8 grados/paso), montado en una plataforma lineal 28T0601-50.

CABLEADO
========

Los dos drivers comparten el MISMO UART (un solo hilo, modo multi-esclavo
del datasheet: cada driver escucha en su propia direccion, fijada por los
pines MS1/AD0 y MS2/AD1). Del lado de la Pi no hay que cablear nada nuevo
para el segundo eje: solo los 3 pines de control (STEP/DIR/EN) y la
direccion.

    Pi 5 (BCM)          Driver enfoque cam0      Driver enfoque cam1
    ------------------  -----------------------  -----------------------
    GPIO21 (pin 40)     STEP                     --
    GPIO20 (pin 38)     DIR                      --
    GPIO16 (pin 36)     EN (activo en LOW)       --
    GPIO26 (pin 37)     --                       STEP
    GPIO19 (pin 35)     --                       DIR
    GPIO13 (pin 33)     --                       EN (activo en LOW)
    GPIO14 (TXD0)       -- via 1k --> nodo PDN compartido (ver abajo) --
    GPIO15 (RXD0)       -- directo --> mismo nodo PDN --------------------
    3.3V                VIO                      VIO
    GND                 GND                      GND
    --                  MS1/AD0 -> GND           MS1/AD0 -> 3.3V (VIO)
    --                  MS2/AD1 -> GND           MS2/AD1 -> GND
    --                  = direccion UART 0       = direccion UART 1

Nodo PDN compartido (half-duplex de un solo hilo, topologia confirmada
en la puesta en marcha del primer eje -- NO conectar RX y TX del driver
por separado, el chip real solo tiene PDN_UART):

    GPIO14 (TXD0) --[1k]--+-- PDN driver cam0
    GPIO15 (RXD0) --------+-- PDN driver cam1

Alimentacion de potencia: VM/VMOT + GND de CADA driver a la fuente de
12V dedicada, NUNCA a un pin de la Pi. Los dos drivers pueden colgar de
la misma fuente. **VM tiene que estar conectado para que el UART
responda**: sin VM el chip contesta version 0x00 en todas las
direcciones, indistinguible de "driver muerto" (fue exactamente el
sintoma que costo el primer diagnostico).

Bobinas: 1A/1B -> bobina A del motor, 2A/2B -> bobina B.
    IMPORTANTE: nunca mezclar un cable de la bobina A con uno de la B en
    el mismo par (1A/1B o 2A/2B) -- eso pone en corto dos fases del
    driver. Y si el motor VIBRA sin avanzar, con UART respondiendo y sin
    flags termicos, sospechar primero de un par invertido (los dos cables
    de UNA bobina cruzados entre si): eso rompe la cuadratura entre fases
    y no es lo mismo que invertir el sentido de giro. Ya paso una vez con
    el eje de cam0 (rojo/azul invertidos en 1A/1B).

Requiere `dtparam=uart0=on` en config.txt (expone /dev/ttyAMA0 en
GPIO14/15) y que la consola serie NO este ocupando esos pines
(`console=serial0,115200` fuera de cmdline.txt).

Si al agregar el segundo driver el primero deja de contestar, poner una
1k propia en serie con el PDN de CADA driver en vez de la unica que va
en TXD0: con dos esclavos colgando del mismo nodo la carga capacitiva
sube y el flanco puede quedar lento.

PROTOCOLO
=========

Protocolo UART del TMC2209 (datasheet Trinamic/Analog Devices, seccion
UART): datagrama de un solo hilo con byte de sincronismo 0x05 y CRC8
propio -- NO es el mismo protocolo simplificado que usa
pytrinamic.connections.uart_ic_interface (esa clase habla con el puente
de registros de las placas de evaluacion Landungsbruecke, no con un chip
pelado). Por eso el transporte se implementa aca directo sobre pyserial,
reusando unicamente las direcciones de registro documentadas (que si
coinciden con pytrinamic.ic.TMC2209).

Seguridad de corriente: igual criterio que IlluminationController.max_value
en core/illumination.py -- max_current_ma es un tope duro por software,
bien por debajo de los 0.6A/fase nominales del motor hasta confirmar
termicamente que no calienta. Ver TODO_HW.
"""

import math
import struct
import threading
import time

import serial

try:
    import RPi.GPIO as GPIO
except ImportError:  # permite importar el modulo para pruebas sin hardware
    GPIO = None


# =============================
# Registros TMC2209 (direcciones verificadas contra pytrinamic.ic.TMC2209.REG)
# =============================
REG_GCONF = 0x00
REG_IHOLD_IRUN = 0x10
REG_CHOPCONF = 0x6C
REG_DRV_STATUS = 0x6F

_SYNC = 0x05

# Formula de corriente del datasheet TMC2209:
#   I_RMS = (CS+1)/32 * Vfs/(Rsense+0.02) / sqrt(2)
# Vfs depende del bit VSENSE del CHOPCONF: 0.325V si VSENSE=0 (menos
# sensible, corrientes altas), 0.180V si VSENSE=1 (mas sensible / mejor
# resolucion a corrientes bajas). Usamos VSENSE=1 porque el motor es
# chico (0.6A nominal) y con eso el maximo alcanzable (~0.98A con
# Rsense=0.11) sigue por encima de lo que vamos a usar.
_RSENSE_EXTRA = 0.02
_VFS_ALTA_SENSIBILIDAD = 0.180

# MRES (CHOPCONF bits 24-27): resolucion de micropasos -> codigo.
MRES_MAP = {256: 0, 128: 1, 64: 2, 32: 3, 16: 4, 8: 5, 4: 6, 2: 7, 1: 8}

# Pines por eje. La clave es el numero de camara: cada canal optico tiene
# su propio motor de enfoque. Ver la tabla de cableado del docstring.
PINES_POR_CAMARA = {
    0: {"step_pin": 21, "dir_pin": 20, "en_pin": 16, "uart_address": 0},
    1: {"step_pin": 26, "dir_pin": 19, "en_pin": 13, "uart_address": 1},
}


def _crc8(datos):
    """CRC8 propio de Trinamic para el datagrama UART (polinomio 0x07,
    procesado bit a bit LSB-first por byte)."""
    crc = 0
    for byte in datos:
        b = byte
        for _ in range(8):
            if ((crc >> 7) & 1) != (b & 1):
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
            b >>= 1
    return crc


def _corriente_a_cs(i_rms_amp, rsense_ohm):
    vfs = _VFS_ALTA_SENSIBILIDAD
    cs = round(i_rms_amp * 32 * math.sqrt(2) * (rsense_ohm + _RSENSE_EXTRA) / vfs) - 1
    return max(0, min(31, cs))


def _cs_a_corriente(cs, rsense_ohm):
    vfs = _VFS_ALTA_SENSIBILIDAD
    return (cs + 1) / 32 * vfs / (rsense_ohm + _RSENSE_EXTRA) / math.sqrt(2)


class TMCUartError(RuntimeError):
    """Fallo de comunicacion UART con el TMC2209 (CRC invalido, sin
    respuesta, registro inesperado, etc.) -- normalmente cableado
    VIO/GND/PDN mal hecho o VM(12V) sin conectar, no un problema de
    corriente."""


class TMC2209Bus:
    """Puerto serie compartido por todos los drivers del mismo hilo PDN.

    El TMC2209 es multi-esclavo sobre un unico cable: hasta 4 drivers
    escuchan el mismo nodo y solo contesta el que coincide con la
    direccion del datagrama. Por eso el puerto se abre UNA vez y se
    comparte -- dos objetos serial sobre /dev/ttyAMA0 se pisarian los
    ecos y las respuestas.

    El lock cubre el par escritura+lectura completo: el eco del propio
    pedido vuelve por el mismo hilo, asi que intercalar dos
    transacciones corrompe las dos.
    """

    def __init__(self, port="/dev/ttyAMA0", baudrate=115200, timeout=0.5):
        self.port = port
        self.ser = serial.Serial(port, baudrate, timeout=timeout)
        self.lock = threading.RLock()
        self.ser.reset_input_buffer()

    def write(self, address, register, value):
        datagram = bytes([_SYNC, address & 0x03, register | 0x80])
        datagram += struct.pack(">I", value & 0xFFFFFFFF)
        datagram += bytes([_crc8(datagram)])
        with self.lock:
            self.ser.reset_input_buffer()
            self.ser.write(datagram)
            # El propio hilo hace eco de lo que mandamos (bus compartido);
            # se descarta para no contaminar la siguiente lectura.
            self.ser.read(len(datagram))

    def read(self, address, register):
        request = bytes([_SYNC, address & 0x03, register & 0x7F])
        request += bytes([_crc8(request)])
        with self.lock:
            self.ser.reset_input_buffer()
            self.ser.write(request)
            self.ser.read(len(request))  # eco del propio pedido
            reply = self.ser.read(8)
        if len(reply) != 8:
            raise TMCUartError(
                f"sin respuesta del TMC2209 (dir {address & 0x03}) al leer "
                f"0x{register:02X} -- revisar VM(12V), VIO(3.3V)/GND, nodo PDN "
                f"y los pines de direccion MS1/MS2")
        if _crc8(reply[:7]) != reply[7]:
            raise TMCUartError(f"CRC invalido leyendo 0x{register:02X}")
        if reply[2] != register:
            raise TMCUartError(
                f"registro inesperado: pedi 0x{register:02X}, "
                f"recibi 0x{reply[2]:02X} en la respuesta")
        return struct.unpack(">I", reply[3:7])[0]

    def close(self):
        with self.lock:
            if self.ser.is_open:
                self.ser.close()


class _TMC2209Uart:
    """Vista de un driver concreto sobre el bus: fija la direccion y
    delega el transporte.

    Acepta un TMC2209Bus ya abierto o, por compatibilidad con los scripts
    de diagnostico (test_uart_only.py), la ruta de un puerto serie, en
    cuyo caso abre un bus propio.
    """

    def __init__(self, bus_o_puerto="/dev/ttyAMA0", address=0,
                 baudrate=115200, timeout=0.5):
        self.address = address & 0x03
        if isinstance(bus_o_puerto, TMC2209Bus):
            self.bus = bus_o_puerto
            self._bus_propio = False
        else:
            self.bus = TMC2209Bus(bus_o_puerto, baudrate, timeout)
            self._bus_propio = True

    def write(self, register, value):
        self.bus.write(self.address, register, value)

    def read(self, register):
        return self.bus.read(self.address, register)

    def close(self):
        # Solo se cierra el puerto si lo abrio esta instancia: si el bus
        # es compartido, cerrarlo dejaria mudo al otro eje.
        if self._bus_propio:
            self.bus.close()


class FocusMotorController:
    """Un eje Z. Todos los metodos son seguros de llamar desde varios
    hilos (el servidor web, el hilo del jog y el del timelapse conviven).
    """

    # Micropasos por tanda del jog continuo: suficientemente chico para
    # que stop_jog() y las lecturas de estado se intercalen rapido, y
    # suficientemente grande para no pagar el lock en cada paso.
    _JOG_CHUNK = 8

    def __init__(self, step_pin=21, dir_pin=20, en_pin=16,
                 uart_port="/dev/ttyAMA0", uart_address=0, rsense=0.11,
                 max_current_ma=550, microsteps=16, bus=None, nombre=None,
                 verificar=True):
        """
        max_current_ma: tope duro de IRUN en mA RMS. El motor
          (28HB30-401A) esta especificado a 0.6A/fase = 600mA; se deja el
          tope en 550 (no en 600) a proposito, con margen. Subir esto solo
          despues de correr el motor un rato y confirmar por DRV_STATUS
          (ver leer_estado()) que OTPW/OT siguen en False.
        microsteps: 1/2/4/8/16/32/64/128/256. INTPOL siempre interpola a
          256 micropasos internamente para el movimiento real del motor,
          esto solo cambia a que resolucion respondemos los pulsos STEP.
        bus: TMC2209Bus compartido. Si es None se abre uno propio sobre
          uart_port (caso de los scripts de prueba de un solo eje).
        verificar: releer GCONF despues de configurar para confirmar que
          hay un driver de verdad en esa direccion. Sin esto la
          construccion SIEMPRE parece exitosa aunque no haya nada
          conectado, porque la configuracion son puras escrituras y una
          escritura por UART no espera respuesta.
        """
        if GPIO is None:
            raise RuntimeError("RPi.GPIO (rpi-lgpio) no disponible")

        self.step_pin = step_pin
        self.dir_pin = dir_pin
        self.en_pin = en_pin
        self.rsense = rsense
        self.max_current_ma = max_current_ma
        self.nombre = nombre or f"motor@{uart_address}"
        self.irun_ma_real = 0
        self.ihold_ma_real = 0
        self.microsteps = microsteps
        # Posicion RELATIVA en micropasos desde el arranque: no hay
        # final de carrera ni encoder, asi que el cero es "donde estaba
        # cuando arranco el servidor". Sirve para el autofoco (volver a
        # un maximo) y para mostrar cuanto se movio, no como coordenada
        # absoluta de la plataforma.
        self.position = 0

        self._lock = threading.RLock()
        self._jog_thread = None
        self._jog_stop = threading.Event()
        self._jog_dir = 1
        self._jog_delay = 0.003
        self._jog_deadline = 0.0

        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.step_pin, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(self.dir_pin, GPIO.OUT, initial=GPIO.LOW)
        # EN empieza deshabilitado (HIGH) -- recien se habilita a proposito
        # con enable(), despues de fijar la corriente.
        GPIO.setup(self.en_pin, GPIO.OUT, initial=GPIO.HIGH)

        self._uart = _TMC2209Uart(bus if bus is not None else uart_port,
                                  address=uart_address)
        self.uart_address = uart_address
        self._chopconf = 0
        try:
            self._configurar(microsteps, verificar=verificar)
        except Exception:
            # Si el eje no existe, no dejar los GPIO tomados ni el puerto
            # abierto: crear_motores() sigue con el resto de los ejes.
            try:
                GPIO.cleanup((self.step_pin, self.dir_pin, self.en_pin))
                self._uart.close()
            except Exception:
                pass
            raise

    # =============================
    # CONFIGURACION UART
    # =============================
    def _configurar(self, microsteps, verificar=True):
        # GCONF: pdn_disable=1 (el pin PDN_UART queda solo para UART, no
        # se interpreta como power-down analogico) + mstep_reg_select=1
        # (el microstepping lo fija CHOPCONF.MRES por UART, no los pines
        # MS1/MS2 -- que en este montaje llevan la DIRECCION del driver).
        # i_scale_analog queda en 0: la corriente sale de IHOLD_IRUN, no
        # del trimpot/VREF de la placa.
        gconf = (1 << 6) | (1 << 7)
        self._uart.write(REG_GCONF, gconf)

        # Parametros de chopper: valores tipicos de arranque del datasheet
        # (afectan suavidad/ruido del paso, no la seguridad de corriente).
        # TOFF!=0 es obligatorio para que la etapa de potencia este activa.
        chopconf = 0
        chopconf |= 3            # TOFF=3
        chopconf |= (4 << 4)     # HSTRT=4
        chopconf |= (0 << 7)     # HEND=0
        chopconf |= (2 << 15)    # TBL=2
        chopconf |= (1 << 17)    # VSENSE=1 (alta sensibilidad, ver formula de corriente)
        chopconf |= (1 << 28)    # INTPOL=1
        self._chopconf = chopconf
        self.set_microsteps(microsteps)

        # Corriente de arranque deliberadamente baja -- ver test_motor_enfoque.py
        self.set_current(irun_ma=min(300, self.max_current_ma))

        if verificar:
            # Releer GCONF cierra el lazo: confirma que hay un chip en
            # esta direccion Y que acepto la configuracion. Es la unica
            # comprobacion posible, porque las escrituras del TMC2209 no
            # devuelven acuse -- sin esto un eje sin cablear (o con VM
            # sin conectar, que es lo mismo desde el UART) se daria por
            # bueno y solo se notaria al intentar moverlo.
            leido = self._uart.read(REG_GCONF)
            if (leido & gconf) != gconf:
                print(f"[motor] {self.nombre}: contesta por UART pero GCONF "
                      f"quedo en 0x{leido:08X} (esperaba los bits 6 y 7 "
                      f"puestos) -- revisar si otro driver comparte la "
                      f"misma direccion MS1/MS2")

    def set_microsteps(self, microsteps):
        """Cambia la resolucion en caliente reescribiendo MRES.

        No hace falta recrear el objeto (que reabriria GPIO y puerto
        serie): MRES vive en CHOPCONF y el resto de los campos del
        registro se conservan.

        La posicion acumulada se reescala para que siga representando el
        mismo desplazamiento fisico: 200 micropasos a 1/16 son la decima
        parte de 200 micropasos a 1/1.
        """
        if microsteps not in MRES_MAP:
            raise ValueError(
                f"microsteps invalido: {microsteps} (validos: "
                f"{sorted(MRES_MAP)})")
        with self._lock:
            anterior = self.microsteps
            self._chopconf &= ~(0xF << 24)
            self._chopconf |= (MRES_MAP[microsteps] << 24)
            self._uart.write(REG_CHOPCONF, self._chopconf)
            self.microsteps = microsteps
            if anterior:
                self.position = round(self.position * microsteps / anterior)
        return microsteps

    def set_current(self, irun_ma, ihold_ma=None, iholddelay=4):
        """Fija IRUN (corriente moviendose) e IHOLD (corriente en reposo,
        por defecto un tercio de IRUN) via UART. Ambos quedan topados por
        max_current_ma pase lo que pase."""
        irun_ma = max(0, min(irun_ma, self.max_current_ma))
        if ihold_ma is None:
            ihold_ma = irun_ma // 3
        ihold_ma = max(0, min(ihold_ma, self.max_current_ma))

        cs_irun = _corriente_a_cs(irun_ma / 1000, self.rsense)
        cs_ihold = _corriente_a_cs(ihold_ma / 1000, self.rsense)

        value = (cs_ihold & 0x1F) | ((cs_irun & 0x1F) << 8) | ((iholddelay & 0xF) << 16)
        with self._lock:
            self._uart.write(REG_IHOLD_IRUN, value)

        self.irun_ma_real = round(_cs_a_corriente(cs_irun, self.rsense) * 1000)
        self.ihold_ma_real = round(_cs_a_corriente(cs_ihold, self.rsense) * 1000)
        return self.irun_ma_real, self.ihold_ma_real

    def leer_estado(self):
        """DRV_STATUS: flags termicos y de cortocircuito, mas CS_ACTUAL
        (el escalon de corriente que esta usando de verdad en este
        instante, util para confirmar que StealthChop no lo esta
        reduciendo mas de lo esperado).

        OJO con bobina_a_abierta/bobina_b_abierta (OLA/OLB): el datasheet
        avisa que la deteccion de bobina abierta no es fiable a corriente
        baja ni con el motor quieto. A 300mA de IRUN aparecio como falso
        positivo repetible; a 450mA dejo de aparecer.
        """
        with self._lock:
            valor = self._uart.read(REG_DRV_STATUS)
        return {
            "sobretemp_aviso": bool(valor & (1 << 0)),   # OTPW
            "sobretemp_corte": bool(valor & (1 << 1)),   # OT
            "corto_fase_a": bool(valor & (1 << 2)),      # S2GA
            "corto_fase_b": bool(valor & (1 << 3)),      # S2GB
            "bobina_a_abierta": bool(valor & (1 << 6)),  # OLA
            "bobina_b_abierta": bool(valor & (1 << 7)),  # OLB
            "cs_actual": (valor >> 16) & 0x1F,
            "stealthchop_activo": bool(valor & (1 << 30)),
        }

    def estado_completo(self):
        """leer_estado() + lo que sabe el propio controlador (posicion,
        resolucion, corrientes, si esta habilitado). Es lo que consume la
        interfaz web."""
        info = {
            "nombre": self.nombre,
            "direccion_uart": self.uart_address,
            "microsteps": self.microsteps,
            "posicion": self.position,
            "irun_ma": self.irun_ma_real,
            "ihold_ma": self.ihold_ma_real,
            "habilitado": self.is_enabled(),
            "jog_activo": self.jog_activo(),
        }
        try:
            info.update(self.leer_estado())
            info["uart"] = "ok"
        except TMCUartError as e:
            info["uart"] = f"error: {e}"
        return info

    # =============================
    # MOVIMIENTO
    # =============================
    def enable(self):
        GPIO.output(self.en_pin, GPIO.LOW)
        time.sleep(0.01)

    def disable(self):
        GPIO.output(self.en_pin, GPIO.HIGH)

    def is_enabled(self):
        return GPIO.input(self.en_pin) == GPIO.LOW

    def move_steps(self, steps, direction=1, delay=0.001):
        """direction: 1 o -1. delay: segundos entre flancos STEP (no el
        periodo completo) -- igual convencion que motortest.py.

        No toca EN: el driver tiene que estar habilitado antes (ver
        mover(), que es lo que conviene usar desde afuera).

        Orientacion fisica confirmada en el eje de cam0: direction=1
        baja la plataforma, direction=-1 la sube.
        """
        with self._lock:
            GPIO.output(self.dir_pin, GPIO.HIGH if direction > 0 else GPIO.LOW)
            time.sleep(0.001)
            for _ in range(abs(steps)):
                GPIO.output(self.step_pin, GPIO.HIGH)
                time.sleep(delay)
                GPIO.output(self.step_pin, GPIO.LOW)
                time.sleep(delay)
            self.position += abs(steps) * (1 if direction > 0 else -1)
        return self.position

    def mover(self, pasos, direction=1, delay=0.003, mantener=False):
        """Movimiento puntual completo: habilita, mueve y vuelve a
        deshabilitar.

        mantener=True deja el driver habilitado al terminar -- lo usa el
        autofoco entre puntos del barrido, para no gastar el ciclo de
        enable/disable en cada paso y para que la plataforma no quede
        suelta a mitad de la medicion.
        """
        with self._lock:
            self.enable()
            try:
                return self.move_steps(pasos, direction=direction, delay=delay)
            finally:
                if not mantener:
                    self.disable()

    def mover_a(self, posicion, delay=0.003, backlash=0, mantener=False):
        """Va a una posicion (en la escala relativa de self.position).

        backlash: si es >0, la posicion final SIEMPRE se alcanza
        avanzando en sentido + (bajando). Para eso, si hay que subir, se
        pasa de largo `backlash` micropasos y se vuelve. Asi el juego
        mecanico del husillo queda siempre tomado del mismo lado, que es
        lo que hace repetible el autofoco.
        """
        with self._lock:
            delta = posicion - self.position
            if delta == 0 and backlash <= 0:
                return self.position
            self.enable()
            try:
                if backlash > 0:
                    if delta < 0:
                        # Sobrepasar por debajo del objetivo y volver.
                        self.move_steps(abs(delta) + backlash, direction=-1,
                                        delay=delay)
                        self.move_steps(backlash, direction=1, delay=delay)
                    else:
                        self.move_steps(delta, direction=1, delay=delay)
                elif delta:
                    self.move_steps(abs(delta), direction=1 if delta > 0 else -1,
                                    delay=delay)
            finally:
                if not mantener:
                    self.disable()
            return self.position

    # =============================
    # JOG CONTINUO (joystick de la interfaz web)
    # =============================
    # El joystick manda /api/focus/jog cada pocas decimas mientras esta
    # apretado y /api/focus/jog/stop al soltar. El watchdog existe porque
    # el control es por red: si se cae el WiFi, se cierra la pestania o
    # el navegador se congela con el joystick apretado, el "stop" nunca
    # llega. Sin watchdog el motor seguiria bajando la plataforma contra
    # la muestra indefinidamente.
    def start_jog(self, direction=1, delay=0.003, watchdog=1.5):
        with self._lock:
            self._jog_dir = 1 if direction > 0 else -1
            self._jog_delay = delay
            self._jog_deadline = time.monotonic() + watchdog
            if self._jog_thread is not None and self._jog_thread.is_alive():
                return  # ya corriendo: alcanza con haber corrido el deadline
            self._jog_stop = threading.Event()
            self._jog_thread = threading.Thread(
                target=self._jog_loop, args=(self._jog_stop,), daemon=True)
            self._jog_thread.start()

    def _jog_loop(self, stop_event):
        try:
            self.enable()
            while not stop_event.is_set():
                with self._lock:
                    if time.monotonic() >= self._jog_deadline:
                        break
                    direction, delay = self._jog_dir, self._jog_delay
                    self.move_steps(self._JOG_CHUNK, direction=direction,
                                    delay=delay)
        finally:
            self.disable()

    def jog_activo(self):
        t = self._jog_thread
        return t is not None and t.is_alive()

    def stop_jog(self):
        with self._lock:
            stop_event = self._jog_stop
            thread = self._jog_thread
        if stop_event is not None:
            stop_event.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        return self.position

    def close(self):
        self.stop_jog()
        try:
            self.disable()
        finally:
            GPIO.cleanup((self.step_pin, self.dir_pin, self.en_pin))
            self._uart.close()


def crear_motores(camaras=(0, 1), uart_port="/dev/ttyAMA0", bus=None, **kwargs):
    """Crea un FocusMotorController por camara sobre un unico bus UART.

    Devuelve (motores, bus). Los ejes que no respondan por UART o cuyo
    GPIO falle se omiten del dict y se reporta el error, en vez de
    tumbar el arranque entero del servidor: con un solo eje cableado el
    resto del microscopio tiene que seguir funcionando (run_web.py corre
    como servicio en el arranque, antes de que nadie pueda mirar).
    """
    if bus is None:
        try:
            bus = TMC2209Bus(uart_port)
        except Exception as e:
            print(f"[motor] no se pudo abrir {uart_port} -> {e}")
            return {}, None
    motores = {}
    for cam in camaras:
        pines = PINES_POR_CAMARA.get(cam)
        if pines is None:
            print(f"[motor] cam{cam}: sin pines definidos en PINES_POR_CAMARA")
            continue
        try:
            motores[cam] = FocusMotorController(
                bus=bus, nombre=f"foco_cam{cam}", **pines, **kwargs)
            print(f"[motor] foco cam{cam}: OK (direccion UART "
                  f"{pines['uart_address']})")
        except Exception as e:
            print(f"[motor] foco cam{cam}: NO disponible -> {e}")
    return motores, bus
