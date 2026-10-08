"""Quien usa el microscopio: conectados, control, bitacora y reservas.

QUIEN ES QUIEN
==============

Desde internet se entra por Cloudflare Access, que despues del login
agrega a cada pedido el encabezado Cf-Access-Authenticated-User-Email
con el correo de la persona. Desde la red del laboratorio no hay login,
asi que se identifica por la IP ("Red local 192.168.1.23").

OJO: esto sirve para saber quien hizo que y para no pisarse, NO es
seguridad. En la red local cualquiera podria inventar ese encabezado; la
proteccion de verdad es Cloudflare Access (docs/ACCESO_REMOTO.md).

CONTROL
=======

Una sola persona a la vez puede mover el foco, cambiar la luz, tomar
fotos o iniciar/detener un timelapse. Las demas pueden mirar el vivo, la
galeria y escribir notas. El primero que hace algo toma el control solo;
si deja de usar la pagina por INACTIVO_S segundos, lo suelta. Otra
persona puede tomarlo cuando quiera con "Tomar el control" (queda en la
bitacora y a quien lo tenia le aparece un aviso).

BITACORA
========

Cada accion (POST) queda en datos/bitacora.jsonl: hora, quien y que. Las
repetidas seguidas (el deslizador de brillo, el joystick) se juntan.

RESERVAS
========

Una lista simple de turnos (quien, desde, hasta, para que). No bloquea
nada: avisa en la pagina si alguien toma el control durante el turno de
otra persona.
"""

import json
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

ENCABEZADO_CORREO = "cf-access-authenticated-user-email"
INACTIVO_S = 90          # sin usar la pagina este tiempo, se suelta el control
CONECTADO_S = 45         # visto hace menos de esto = conectado
BITACORA_MAX_BYTES = 5_000_000
JUNTAR_S = 10            # misma accion de la misma persona: una sola linea

# Pedidos POST que cambian el estado del equipo y piden tener el control.
CON_CONTROL = (
    "/light/", "/capture/", "/exposure", "/brightness", "/timelapse/start",
    "/timelapse/stop", "/timelapse/pausar", "/timelapse/continuar",
    "/api/temperature/setpoint", "/api/temperature/co2_setpoint", "/api/focus/",
    "/camera/calibrar",
    "/camera/calibracion/borrar", "/api/analisis/config", "/api/analisis/medir",
    "/api/analisis/foto", "/api/actualizar", "/api/optica", "/profiles/",
    "/api/marca", "/api/alertas/config", "/api/usb/expulsar",
)
# Los que no se anotan solos en la bitacora: o son internos de la pagina
# o los anota este modulo con un texto mas claro (tomar el control,
# reservar).
NO_REGISTRAR = ("/api/control/", "/api/reservas")
# Los que se juntan en la bitacora (llegan muchos seguidos).
RUIDOSOS = ("/api/focus/jog", "/light/set", "/brightness", "/light/colores",
            "/api/focus/move")


def requiere_control(metodo, ruta):
    if metodo != "POST":
        return False
    if ruta.startswith("/api/exp/"):
        return ruta.endswith("/renombrar") or ruta.endswith("/borrar")
    return ruta.startswith(CON_CONTROL)


class Usuarios:
    def __init__(self, bitacora=None, reservas=None, inactivo_s=INACTIVO_S):
        base = Path(__file__).resolve().parent.parent
        self.archivo_bitacora = Path(bitacora or base / "datos" / "bitacora.jsonl")
        self.archivo_reservas = Path(reservas or base / "profiles" / "reservas.json")
        self.inactivo_s = inactivo_s
        self._vistos = {}          # id -> {nombre, remoto, visto}
        self.controlador = None    # id
        self._ultima_accion = 0.0
        self.tomado_por_otro = {}  # id que perdio el control -> quien lo tomo
        self._ultimo_log = None    # (id, ruta, time) de la ultima linea
        self._lock = threading.RLock()

    # ---------- identidad ----------
    @staticmethod
    def identificar(encabezados, ip):
        correo = (encabezados.get(ENCABEZADO_CORREO) or "").strip().lower()
        if correo:
            return {"id": correo, "nombre": correo, "remoto": True}
        ip = ip or "?"
        return {"id": f"local:{ip}", "nombre": f"Red local ({ip})", "remoto": False}

    def visto(self, usuario):
        with self._lock:
            self._vistos[usuario["id"]] = dict(usuario, visto=time.time())

    def conectados(self):
        ahora = time.time()
        with self._lock:
            return [{"id": k, "nombre": v["nombre"], "remoto": v["remoto"],
                     "hace_s": int(ahora - v["visto"]), "control": k == self._control()}
                    for k, v in sorted(self._vistos.items(), key=lambda kv: -kv[1]["visto"])
                    if ahora - v["visto"] < CONECTADO_S]

    def nombre(self, uid):
        v = self._vistos.get(uid)
        return v["nombre"] if v else uid

    # ---------- control ----------
    def _control(self):
        """Quien tiene el control ahora (lo suelta si quedo inactivo)."""
        if self.controlador is None:
            return None
        visto = (self._vistos.get(self.controlador) or {}).get("visto", 0)
        if time.time() - max(visto, self._ultima_accion) > self.inactivo_s:
            self.controlador = None
        return self.controlador

    def puede(self, usuario):
        """(True, None) si puede hacer cambios; si no, (False, nombre de
        quien tiene el control). Si nadie lo tiene, lo toma."""
        with self._lock:
            actual = self._control()
            if actual is None or actual == usuario["id"]:
                self.controlador = usuario["id"]
                self._ultima_accion = time.time()
                return True, None
            return False, self.nombre(actual)

    def tomar(self, usuario):
        with self._lock:
            anterior = self._control()
            if anterior and anterior != usuario["id"]:
                self.tomado_por_otro[anterior] = usuario["nombre"]
                self.registrar(usuario, "control", f"tomó el control (lo tenía {self.nombre(anterior)})")
            elif anterior != usuario["id"]:
                self.registrar(usuario, "control", "tomó el control")
            self.controlador = usuario["id"]
            self._ultima_accion = time.time()
            self.tomado_por_otro.pop(usuario["id"], None)
            return self.estado(usuario)

    def soltar(self, usuario):
        with self._lock:
            if self._control() == usuario["id"]:
                self.controlador = None
                self.registrar(usuario, "control", "soltó el control")
            return self.estado(usuario)

    def estado(self, usuario):
        with self._lock:
            actual = self._control()
            reserva = self.reserva_actual()
            return {
                "yo": usuario,
                "control": None if actual is None else {"id": actual, "nombre": self.nombre(actual)},
                "tengo_control": actual == usuario["id"],
                "me_lo_quitaron": self.tomado_por_otro.get(usuario["id"]),
                "conectados": self.conectados(),
                "reserva_actual": reserva,
                "reserva_de_otro": bool(reserva and reserva["usuario_id"] != usuario["id"]),
            }

    def visto_aviso(self, usuario):
        """La pagina ya mostro "te quitaron el control"."""
        with self._lock:
            self.tomado_por_otro.pop(usuario["id"], None)

    # ---------- bitacora ----------
    def registrar(self, usuario, ruta, detalle="", forzar=False):
        if not forzar and ruta.startswith(NO_REGISTRAR):
            return
        ahora = time.time()
        with self._lock:
            u = self._ultimo_log
            if (u and u[0] == usuario["id"] and u[1] == ruta and ruta.startswith(RUIDOSOS)
                    and ahora - u[2] < JUNTAR_S):
                self._ultimo_log = (u[0], u[1], ahora)
                return
            self._ultimo_log = (usuario["id"], ruta, ahora)
            linea = {"hora": datetime.now().isoformat(timespec="seconds"),
                     "usuario": usuario["nombre"], "accion": ruta, "detalle": detalle}
            try:
                self.archivo_bitacora.parent.mkdir(parents=True, exist_ok=True)
                if (self.archivo_bitacora.is_file()
                        and self.archivo_bitacora.stat().st_size > BITACORA_MAX_BYTES):
                    self.archivo_bitacora.replace(
                        self.archivo_bitacora.with_suffix(".anterior.jsonl"))
                with open(self.archivo_bitacora, "a", encoding="utf-8") as f:
                    f.write(json.dumps(linea, ensure_ascii=False) + "\n")
            except OSError as e:
                print(f"[usuarios] no se pudo escribir la bitacora: {e}")

    def bitacora(self, n=200):
        try:
            lineas = self.archivo_bitacora.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for l in reversed(lineas[-n:]):
            try:
                out.append(json.loads(l))
            except ValueError:
                continue
        return out

    # ---------- reservas ----------
    def _leer_reservas(self):
        try:
            return json.loads(self.archivo_reservas.read_text())
        except (OSError, ValueError):
            return []

    def _guardar_reservas(self, lista):
        self.archivo_reservas.parent.mkdir(parents=True, exist_ok=True)
        self.archivo_reservas.write_text(json.dumps(lista, indent=1, ensure_ascii=False))

    def reservas(self):
        """Las que no terminaron, en orden."""
        ahora = datetime.now().isoformat(timespec="minutes")
        return sorted((r for r in self._leer_reservas() if r["fin"] > ahora),
                      key=lambda r: r["inicio"])

    def reserva_actual(self):
        ahora = datetime.now().isoformat(timespec="minutes")
        return next((r for r in self.reservas() if r["inicio"] <= ahora < r["fin"]), None)

    def reservar(self, usuario, inicio, fin, nota=""):
        try:
            ini = datetime.fromisoformat(inicio)
            fi = datetime.fromisoformat(fin)
        except (TypeError, ValueError):
            raise ValueError("Fechas inválidas")
        if fi <= ini:
            raise ValueError("El turno tiene que terminar después de empezar")
        if fi <= datetime.now():
            raise ValueError("Ese turno ya pasó")
        ini_s, fin_s = ini.isoformat(timespec="minutes"), fi.isoformat(timespec="minutes")
        with self._lock:
            lista = self._leer_reservas()
            for r in lista:
                if r["inicio"] < fin_s and ini_s < r["fin"]:
                    raise ValueError(f"Se cruza con el turno de {r['usuario']} "
                                     f"({r['inicio'][11:16]}–{r['fin'][11:16]})")
            r = {"id": uuid.uuid4().hex[:10], "usuario": usuario["nombre"],
                 "usuario_id": usuario["id"], "inicio": ini_s, "fin": fin_s,
                 "nota": " ".join(str(nota or "").split())[:200]}
            lista.append(r)
            self._guardar_reservas(lista)
        self.registrar(usuario, "reserva", f"{ini_s} – {fin_s} {r['nota']}")
        return r

    def cancelar_reserva(self, usuario, rid):
        with self._lock:
            lista = self._leer_reservas()
            r = next((x for x in lista if x["id"] == rid), None)
            if r is None:
                raise ValueError("Esa reserva ya no existe")
            lista.remove(r)
            self._guardar_reservas(lista)
        self.registrar(usuario, "reserva cancelada",
                       f"{r['usuario']} {r['inicio']} – {r['fin']}")
        return True
