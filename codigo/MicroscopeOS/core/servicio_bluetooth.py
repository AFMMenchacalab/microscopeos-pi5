"""Servicio Bluetooth (BLE) para configurar la red desde el teléfono.

La lógica (cuándo se puede, el código de colores, NetworkManager) está en
core/configuracion_red.py. Aquí solo está el Bluetooth, hablando con BlueZ
por D-Bus (paquete dbus-next).

EL SERVICIO
===========

Se anuncia como «MOS-XXXX» (el nombre completo, «MicroscopeOS-XXXX», va en
el estado) con el servicio UUID_SERVICIO. Tres características:

- ESTADO  (leer, notificar): JSON de ConfiguracionRed.estado(), más
  "ultimo" (id del último comando atendido). La app lo lee para saber la
  IP («resincronizar») y lo escucha para ver el resultado de cada comando.
- REDES   (leer): JSON [[ssid, señal, segura], ...] de las redes visibles.
- COMANDO (escribir, CIFRADO): JSON {"id": n, "cmd": ..., ...}. Exige que
  el teléfono esté emparejado: así la contraseña del Wi-Fi no viaja en
  claro. El emparejamiento es «Just Works» (sin PIN); la prueba de que se
  está frente al microscopio es el código de colores.

Los errores de un comando no vuelven como error de Bluetooth (no llevan
texto): quedan en "error" del ESTADO, que se notifica.

Si no hay Bluetooth o BlueZ (la laptop, las pruebas), no hace nada: el
microscopio funciona igual sin esto.
"""

import asyncio
import functools
import json
import threading

from core.configuracion_red import ErrorConfiguracion

UUID_SERVICIO = "6d6f732d-0001-4c4d-8000-6d6963726f73"
UUID_ESTADO = "6d6f732d-0002-4c4d-8000-6d6963726f73"
UUID_REDES = "6d6f732d-0003-4c4d-8000-6d6963726f73"
UUID_COMANDO = "6d6f732d-0004-4c4d-8000-6d6963726f73"

RUTA_APP = "/mx/lmimenchacalab/microscopeos"
RUTA_ANUNCIO = RUTA_APP + "/anuncio0"
RUTA_AGENTE = RUTA_APP + "/agente"
BLUEZ = "org.bluez"


def iniciar(configuracion, adaptador="hci0", modo_pi=True, agente=None):
    """Arranca el servicio en un hilo propio. Devuelve el hilo, o None si
    no hay con qué (sin dbus-next)."""
    try:
        import dbus_next  # noqa: F401
    except ImportError:
        print("[bluetooth] sin el paquete dbus-next: configuración por Bluetooth apagada")
        return None
    hilo = threading.Thread(target=_correr, args=(configuracion, adaptador, modo_pi, modo_pi if agente is None else agente), daemon=True,
                            name="servicio-bluetooth")
    hilo.start()
    return hilo


def _correr(configuracion, adaptador, modo_pi, agente):
    try:
        asyncio.run(ServicioBluetooth(configuracion, adaptador, modo_pi, agente).correr())
    except Exception as e:
        print(f"[bluetooth] apagado: {e}")


class ServicioBluetooth:
    def __init__(self, configuracion, adaptador="hci0", modo_pi=True, agente=None):
        self.cfg = configuracion
        self.agente = modo_pi if agente is None else agente
        # En la Pi el adaptador se llama como el microscopio y este
        # servicio acepta los emparejamientos. En una computadora de prueba
        # no se toca su nombre ni se le quita el agente al escritorio.
        self.modo_pi = modo_pi
        self.adaptador = adaptador
        self.ruta_adaptador = f"/org/bluez/{adaptador}"
        self.ultimo = 0
        self._estado = None
        self._buffer = {}

    def _json_estado(self, conexion=None):
        e = self.cfg.estado(conexion)
        e["ultimo"] = self.ultimo
        return json.dumps(e, ensure_ascii=False, separators=(",", ":")).encode()

    def _json_redes(self):
        redes = self.cfg.red.redes()
        out = []
        for r in redes:  # que entre en una lectura (512 bytes)
            prueba = json.dumps(out + [r], ensure_ascii=False, separators=(",", ":")).encode()
            if len(prueba) > 500:
                break
            out.append(r)
        return json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode()

    async def correr(self):
        from dbus_next import BusType, Variant
        from dbus_next.aio import MessageBus

        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        loop = asyncio.get_running_loop()
        self._loop = loop
        servicio = self

        # ---- objetos que exporta el microscopio ----
        app = _Aplicacion(self)
        svc = _Servicio(UUID_SERVICIO)
        self._estado = _Caracteristica(UUID_ESTADO, ["read", "notify"], svc.ruta(0),
                                       leer=lambda o: servicio._json_estado(_conexion(o)))
        redes = _Caracteristica(UUID_REDES, ["read"], svc.ruta(0), leer=lambda o: servicio._json_redes())
        comando = _Caracteristica(UUID_COMANDO, ["encrypt-write"], svc.ruta(0), escribir=self._escribir)
        app.agregar(svc, [self._estado, redes, comando])
        app.exportar(bus)

        agente = _Agente()
        bus.export(RUTA_AGENTE, agente)
        anuncio = _Anuncio(self.cfg.nombre)
        bus.export(RUTA_ANUNCIO, anuncio)

        intro = await bus.introspect(BLUEZ, self.ruta_adaptador)
        obj = bus.get_proxy_object(BLUEZ, self.ruta_adaptador, intro)
        props = obj.get_interface("org.freedesktop.DBus.Properties")
        await props.call_set("org.bluez.Adapter1", "Powered", Variant("b", True))
        if self.modo_pi:
            await props.call_set("org.bluez.Adapter1", "Alias", Variant("s", self.cfg.nombre))
        await props.call_set("org.bluez.Adapter1", "Pairable", Variant("b", True))

        raiz = bus.get_proxy_object(BLUEZ, "/org/bluez", await bus.introspect(BLUEZ, "/org/bluez"))
        gestor_agentes = raiz.get_interface("org.bluez.AgentManager1")
        if self.agente:
            await gestor_agentes.call_register_agent(RUTA_AGENTE, "NoInputNoOutput")
            try:
                await gestor_agentes.call_request_default_agent(RUTA_AGENTE)
            except Exception as e:
                print(f"[bluetooth] agente no es el predeterminado: {e}")

        await obj.get_interface("org.bluez.GattManager1").call_register_application(RUTA_APP, {})
        await obj.get_interface("org.bluez.LEAdvertisingManager1").call_register_advertisement(RUTA_ANUNCIO, {})
        print(f"[bluetooth] anunciándose como {anuncio.nombre_corto} ({self.cfg.nombre})")

        # Cuando cambia el estado (un comando, el código, la red), avisar.
        self.cfg.oyentes.append(lambda: loop.call_soon_threadsafe(self._notificar))
        await asyncio.Event().wait()  # para siempre

    def _notificar(self):
        if self._estado is not None and self._estado.notificando:
            self._estado.cambiar_valor(self._json_estado())

    def _escribir(self, valor, opciones):
        """WriteValue de COMANDO. Junta escrituras largas (offset) y
        atiende el comando en otro hilo: conectarse al Wi-Fi tarda."""
        conexion = _conexion(opciones)
        desplazamiento = int(_valor(opciones.get("offset"), 0))
        previo = b"" if desplazamiento == 0 else self._buffer.get(conexion, b"")
        datos = previo[:desplazamiento] + bytes(valor)
        self._buffer[conexion] = datos
        try:
            pedido = json.loads(datos.decode())
        except (ValueError, UnicodeDecodeError):
            return  # todavía falta un pedazo
        self._buffer.pop(conexion, None)
        self._loop.run_in_executor(None, self._atender, pedido, conexion)

    def _atender(self, pedido, conexion):
        ident = pedido.get("id", 0)
        try:
            self.cfg.comando(pedido, conexion)
            self.cfg.error = None
        except ErrorConfiguracion as e:
            self.cfg.error = str(e)
        except Exception as e:  # que un error raro no tumbe el servicio
            self.cfg.error = f"Error inesperado: {e}"
        self.ultimo = ident
        self._loop.call_soon_threadsafe(self._notificar)


def _valor(v, por_defecto=None):
    return getattr(v, "value", v) if v is not None else por_defecto


def _conexion(opciones):
    """El teléfono que pide (ruta del dispositivo en BlueZ)."""
    return str(_valor((opciones or {}).get("device"), "?"))


# ======================================================================
# Objetos D-Bus (interfaces de BlueZ)
# ======================================================================
@functools.lru_cache(maxsize=1)
def _clases():
    """Se definen al usarse: así importar este módulo no exige dbus-next."""
    from dbus_next import Variant
    from dbus_next.service import PropertyAccess, ServiceInterface, dbus_property, method

    class Aplicacion(ServiceInterface):
        def __init__(self, servicio):
            super().__init__("org.freedesktop.DBus.ObjectManager")
            self.objetos = []

        def agregar(self, svc, caracteristicas):
            svc.caracteristicas = caracteristicas
            for i, c in enumerate(caracteristicas):
                c.ruta_propia = svc.ruta(0) + f"/char{i}"
            self.objetos.append(svc)

        def exportar(self, bus):
            bus.export(RUTA_APP, self)
            for svc in self.objetos:
                bus.export(svc.ruta(0), svc)
                for c in svc.caracteristicas:
                    bus.export(c.ruta_propia, c)

        @method()
        def GetManagedObjects(self) -> "a{oa{sa{sv}}}":
            out = {}
            for svc in self.objetos:
                out[svc.ruta(0)] = {"org.bluez.GattService1": {
                    "UUID": Variant("s", svc.uuid), "Primary": Variant("b", True)}}
                for c in svc.caracteristicas:
                    out[c.ruta_propia] = {"org.bluez.GattCharacteristic1": {
                        "UUID": Variant("s", c.uuid),
                        "Service": Variant("o", svc.ruta(0)),
                        "Flags": Variant("as", c.flags)}}
            return out

    class Servicio(ServiceInterface):
        def __init__(self, uuid):
            super().__init__("org.bluez.GattService1")
            self.uuid = uuid
            self.caracteristicas = []

        @staticmethod
        def ruta(i):
            return f"{RUTA_APP}/service{i}"

        @dbus_property(access=PropertyAccess.READ)
        def UUID(self) -> "s":
            return self.uuid

        @dbus_property(access=PropertyAccess.READ)
        def Primary(self) -> "b":
            return True

    class Caracteristica(ServiceInterface):
        def __init__(self, uuid, flags, ruta_servicio, leer=None, escribir=None):
            super().__init__("org.bluez.GattCharacteristic1")
            self.uuid, self.flags, self.ruta_servicio = uuid, flags, ruta_servicio
            self._leer, self._escribir = leer, escribir
            self.ruta_propia = None
            self.notificando = False
            self._valor = b""

        @method()
        def ReadValue(self, options: "a{sv}") -> "ay":
            desde = int(_valor(options.get("offset"), 0))
            # Una lectura larga llega en pedazos (offset > 0): todos tienen
            # que salir del mismo valor, no de uno recalculado a mitad.
            if desde == 0 and self._leer:
                self._valor = self._leer(options)
            return self._valor[desde:]

        @method()
        def WriteValue(self, value: "ay", options: "a{sv}"):
            if self._escribir:
                self._escribir(value, options)

        @method()
        def StartNotify(self):
            self.notificando = True

        @method()
        def StopNotify(self):
            self.notificando = False

        def cambiar_valor(self, valor):
            self._valor = valor
            self.emit_properties_changed({"Value": valor})

        @dbus_property(access=PropertyAccess.READ)
        def UUID(self) -> "s":
            return self.uuid

        @dbus_property(access=PropertyAccess.READ)
        def Service(self) -> "o":
            return self.ruta_servicio

        @dbus_property(access=PropertyAccess.READ)
        def Flags(self) -> "as":
            return self.flags

        @dbus_property(access=PropertyAccess.READ)
        def Value(self) -> "ay":
            return self._valor

    class Anuncio(ServiceInterface):
        def __init__(self, nombre):
            super().__init__("org.bluez.LEAdvertisement1")
            # Un anuncio son 31 bytes: con el UUID de 128 bits solo entra
            # un nombre corto.
            self.nombre_corto = "MOS-" + nombre.split("-")[-1][:4]

        @method()
        def Release(self):
            print("[bluetooth] anuncio liberado")

        @dbus_property(access=PropertyAccess.READ)
        def Type(self) -> "s":
            return "peripheral"

        @dbus_property(access=PropertyAccess.READ)
        def ServiceUUIDs(self) -> "as":
            return [UUID_SERVICIO]

        @dbus_property(access=PropertyAccess.READ)
        def LocalName(self) -> "s":
            return self.nombre_corto

    class Agente(ServiceInterface):
        """Emparejamiento «Just Works»: se acepta todo. La seguridad es el
        código de colores; el emparejamiento solo cifra la conexión."""

        def __init__(self):
            super().__init__("org.bluez.Agent1")

        @method()
        def Release(self):
            pass

        @method()
        def RequestAuthorization(self, device: "o"):
            print(f"[bluetooth] emparejando {device}")

        @method()
        def AuthorizeService(self, device: "o", uuid: "s"):
            pass

        @method()
        def RequestConfirmation(self, device: "o", passkey: "u"):
            print(f"[bluetooth] emparejando {device}")

        @method()
        def Cancel(self):
            pass

    return Aplicacion, Servicio, Caracteristica, Anuncio, Agente


def _Aplicacion(servicio):
    return _clases()[0](servicio)


def _Servicio(uuid):
    return _clases()[1](uuid)


def _Caracteristica(*a, **k):
    return _clases()[2](*a, **k)


def _Anuncio(nombre):
    return _clases()[3](nombre)


def _Agente():
    return _clases()[4]()
