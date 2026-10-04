"""Alertas por Telegram o correo cuando algo sale mal sin nadie mirando.

QUE AVISA
=========

- Temperatura o CO2 fuera de rango durante mas de N minutos (un pico de
  un minuto al abrir la puerta no es alerta).
- Se perdio la conexion con el Arduino de la incubadora.
- Poco espacio en disco.
- El autofoco fallo varias veces seguidas en una camara.
- Una captura fallo.
- El timelapse se detuvo por un error, o termino (opcional).

Cuando un problema de rango se resuelve, manda un segundo mensaje
("Resuelto"). Cada alerta tiene una pausa minima entre repeticiones para
no llenar el telefono si el problema sigue.

Por defecto las de temperatura, CO2 y conexion solo se revisan con un
timelapse corriendo: con la incubadora apagada a proposito no tiene
sentido avisar.

CANALES
=======

- Telegram: un bot propio (se crea gratis hablando con @BotFather) y el
  chat al que manda. buscar_chats_telegram() encuentra el chat despues de
  mandarle cualquier mensaje al bot.
- Correo: SMTP con usuario y contrasena (en Gmail, una "contrasena de
  aplicacion").

La configuracion vive en profiles/alertas.json, que tiene contrasenas y
NO va al repositorio (.gitignore).
"""

import json
import smtplib
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

ARCHIVO = Path(__file__).resolve().parent.parent / "profiles" / "alertas.json"

POR_DEFECTO = {
    "activo": False,
    "nombre_equipo": "Microscopio",
    "url_pagina": "",
    "telegram": {"activo": False, "token": "", "chat_id": ""},
    "correo": {"activo": False, "servidor": "smtp.gmail.com", "puerto": 587,
               "usuario": "", "contrasena": "", "de": "", "para": ""},
    "temperatura_margen_c": 1.0,     # |T - consigna| que cuenta como fuera de rango
    "co2_margen_pct": 1.0,           # |CO2 - consigna| en % de CO2
    "minutos_fuera": 10,             # cuanto tiene que durar para avisar
    "sin_datos_min": 10,             # sin datos de la incubadora
    "disco_min_gb": 5.0,
    "autofoco_fallos": 3,            # fallos seguidos por camara
    "avisar_fin": True,              # avisar cuando termina un timelapse
    "solo_con_timelapse": True,      # temperatura/CO2/conexion solo con timelapse
    "repetir_min": 60,               # pausa minima entre repeticiones de una alerta
}
SECRETOS = (("telegram", "token"), ("correo", "contrasena"))
HISTORIAL_MAX = 50


def _mezclar(base, cambios):
    out = json.loads(json.dumps(base))
    for k, v in (cambios or {}).items():
        if k not in base:
            continue
        if isinstance(base[k], dict) and isinstance(v, dict):
            for k2, v2 in v.items():
                if k2 in base[k]:
                    out[k][k2] = v2
        else:
            out[k] = v
    return out


class Alertas:
    def __init__(self, archivo=ARCHIVO, incubadora=None, espacio=None,
                 timelapse_corriendo=None, enviar=None):
        """
        incubadora: objeto con temperature, setpoint, co2, co2_setpoint,
          error_msg (temperature_controller). None = no se revisa.
        espacio: funcion sin argumentos que devuelve los bytes libres.
        timelapse_corriendo: funcion sin argumentos -> bool.
        enviar: para pruebas, reemplaza el envio real: enviar(canal, asunto, texto).
        """
        self.archivo = Path(archivo)
        self.incubadora = incubadora
        self.espacio = espacio
        self.timelapse_corriendo = timelapse_corriendo or (lambda: False)
        self._enviar_override = enviar
        self.config = self._cargar()
        self.historial = []            # lo ultimo que se mando (para la pagina)
        self._ultimo_envio = {}        # clave -> time.time()
        self._activas = {}             # clave -> texto (para mandar "Resuelto")
        self._fuera_desde = {}         # clave -> time.monotonic()
        self._lock = threading.Lock()
        self._hilo = None

    # ---------- configuracion ----------
    def _cargar(self):
        try:
            return _mezclar(POR_DEFECTO, json.loads(self.archivo.read_text()))
        except FileNotFoundError:
            return _mezclar(POR_DEFECTO, {})
        except Exception as e:
            print(f"[alertas] configuracion ilegible ({e}), se usan los valores por defecto")
            return _mezclar(POR_DEFECTO, {})

    def _guardar(self):
        self.archivo.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.archivo.with_name(self.archivo.name + ".tmp")
        tmp.write_text(json.dumps(self.config, indent=2, ensure_ascii=False))
        tmp.replace(self.archivo)

    def publico(self):
        """La configuracion sin contrasenas, para la pagina."""
        c = json.loads(json.dumps(self.config))
        for seccion, campo in SECRETOS:
            c[seccion][campo + "_guardado"] = bool(c[seccion][campo])
            c[seccion][campo] = ""
        c["historial"] = list(self.historial)
        c["activas"] = sorted(self._activas)
        return c

    def actualizar(self, cambios):
        """Mezcla los cambios. Un secreto vacio conserva el guardado."""
        cambios = json.loads(json.dumps(cambios or {}))
        for seccion, campo in SECRETOS:
            if isinstance(cambios.get(seccion), dict) and not cambios[seccion].get(campo):
                cambios[seccion].pop(campo, None)
        nuevo = _mezclar(self.config, cambios)
        nuevo["correo"]["puerto"] = int(nuevo["correo"]["puerto"] or 587)
        for k in ("temperatura_margen_c", "co2_margen_pct", "disco_min_gb"):
            nuevo[k] = max(0.0, float(nuevo[k]))
        for k in ("minutos_fuera", "sin_datos_min", "autofoco_fallos", "repetir_min"):
            nuevo[k] = max(1, int(nuevo[k]))
        self.config = nuevo
        self._guardar()
        return self.publico()

    def canales(self):
        c = self.config
        out = []
        if c["telegram"]["activo"] and c["telegram"]["token"] and c["telegram"]["chat_id"]:
            out.append("telegram")
        if c["correo"]["activo"] and c["correo"]["servidor"] and c["correo"]["para"]:
            out.append("correo")
        return out

    # ---------- envio ----------
    def _mandar_telegram(self, asunto, texto):
        t = self.config["telegram"]
        datos = urllib.parse.urlencode({
            "chat_id": t["chat_id"], "text": f"{asunto}\n\n{texto}",
            "disable_web_page_preview": "true"}).encode()
        url = f"https://api.telegram.org/bot{t['token']}/sendMessage"
        with urllib.request.urlopen(url, data=datos, timeout=15) as r:
            respuesta = json.loads(r.read().decode())
        if not respuesta.get("ok"):
            raise RuntimeError(respuesta.get("description") or "Telegram no aceptó el mensaje")

    def _mandar_correo(self, asunto, texto):
        c = self.config["correo"]
        msg = EmailMessage()
        msg["Subject"] = asunto
        msg["From"] = c["de"] or c["usuario"]
        msg["To"] = c["para"]
        msg.set_content(texto)
        puerto = int(c["puerto"] or 587)
        if puerto == 465:
            servidor = smtplib.SMTP_SSL(c["servidor"], puerto, timeout=20)
        else:
            servidor = smtplib.SMTP(c["servidor"], puerto, timeout=20)
            servidor.starttls()
        try:
            if c["usuario"]:
                servidor.login(c["usuario"], c["contrasena"])
            servidor.send_message(msg)
        finally:
            servidor.quit()

    def _mandar(self, canal, asunto, texto):
        if self._enviar_override is not None:
            return self._enviar_override(canal, asunto, texto)
        if canal == "telegram":
            return self._mandar_telegram(asunto, texto)
        return self._mandar_correo(asunto, texto)

    def _redactar(self, texto):
        c = self.config
        partes = [texto, "", f"{c['nombre_equipo']} · {datetime.now():%d/%m/%Y %H:%M}"]
        if c["url_pagina"]:
            partes.append(c["url_pagina"])
        return "\n".join(partes)

    def _enviar_todos(self, asunto, texto, sincrono=False):
        """Manda por todos los canales activos. Devuelve {canal: "ok"|error}."""
        resultados = {}

        def trabajo():
            for canal in self.canales():
                try:
                    self._mandar(canal, asunto, texto)
                    resultados[canal] = "ok"
                except Exception as e:
                    resultados[canal] = str(e)
                    print(f"[alertas] no se pudo mandar por {canal}: {e}")
        if sincrono:
            trabajo()
        else:
            threading.Thread(target=trabajo, daemon=True).start()
        return resultados

    def notificar(self, clave, asunto, texto, repetir=True, sincrono=False):
        """Manda una alerta salvo que la misma clave se haya mandado hace
        menos de `repetir_min`. Devuelve True si se mando."""
        if not self.config["activo"] or not self.canales():
            return False
        with self._lock:
            ahora = time.time()
            ultimo = self._ultimo_envio.get(clave)
            if ultimo is not None and (not repetir or
                                       ahora - ultimo < self.config["repetir_min"] * 60):
                return False
            self._ultimo_envio[clave] = ahora
            self._activas[clave] = texto
            self.historial.insert(0, {"hora": datetime.now().isoformat(timespec="seconds"),
                                      "asunto": asunto, "texto": texto})
            del self.historial[HISTORIAL_MAX:]
        self._enviar_todos(f"⚠️ {self.config['nombre_equipo']}: {asunto}",
                           self._redactar(texto), sincrono=sincrono)
        return True

    def aviso(self, clave, asunto, texto, sincrono=False):
        """Mensaje informativo que no queda "activo" (p. ej. fin de timelapse)."""
        if not self.config["activo"] or not self.canales():
            return False
        with self._lock:
            self.historial.insert(0, {"hora": datetime.now().isoformat(timespec="seconds"),
                                      "asunto": asunto, "texto": texto})
            del self.historial[HISTORIAL_MAX:]
        self._enviar_todos(f"{self.config['nombre_equipo']}: {asunto}",
                           self._redactar(texto), sincrono=sincrono)
        return True

    def resuelto(self, clave, texto, sincrono=False):
        """Si esa alerta estaba activa, avisa que se resolvio."""
        with self._lock:
            if clave not in self._activas:
                return False
            self._activas.pop(clave, None)
            self._ultimo_envio.pop(clave, None)
        return self.aviso(clave, "Resuelto", texto, sincrono=sincrono)

    def probar(self):
        """Manda un mensaje de prueba ya (sin mirar si estan activas)."""
        if not self.canales():
            return {"error": "Falta activar y configurar Telegram o el correo"}
        return {"resultados": self._enviar_todos(
            f"{self.config['nombre_equipo']}: prueba de alertas",
            self._redactar("Si te llegó este mensaje, las alertas funcionan."),
            sincrono=True)}

    def buscar_chats_telegram(self, token=None):
        """Los chats que le escribieron al bot hace poco ({id, nombre})."""
        token = token or self.config["telegram"]["token"]
        if not token:
            return {"error": "Falta el token del bot"}
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                datos = json.loads(r.read().decode())
        except Exception as e:
            return {"error": f"No se pudo hablar con Telegram: {e}"}
        if not datos.get("ok"):
            return {"error": datos.get("description") or "Token inválido"}
        chats = {}
        for u in datos.get("result", []):
            m = u.get("message") or u.get("channel_post") or {}
            chat = m.get("chat") or {}
            if "id" in chat:
                nombre = (chat.get("title") or " ".join(
                    x for x in (chat.get("first_name"), chat.get("last_name")) if x)
                    or chat.get("username") or str(chat["id"]))
                chats[str(chat["id"])] = nombre
        return {"chats": [{"id": k, "nombre": v} for k, v in chats.items()]}

    # ---------- avisos del timelapse ----------
    def autofoco_fallo(self, cam, seguidos, ciclo, motivo, experimento=""):
        if seguidos >= self.config["autofoco_fallos"]:
            self.notificar(f"autofoco_cam{cam}", "el autofoco no encuentra el foco",
                           f"La cámara {cam} lleva {seguidos} autofocos seguidos sin encontrar "
                           f"el foco (ciclo {ciclo}, {motivo})."
                           + (f"\nExperimento: {experimento}" if experimento else "")
                           + "\nLas fotos pueden estar saliendo borrosas.")

    def captura_fallo(self, cam, sufijo, error, experimento=""):
        self.notificar(f"captura_cam{cam}", "falló una captura",
                       f"Cámara {cam}{sufijo}: {error}"
                       + (f"\nExperimento: {experimento}" if experimento else ""))

    def timelapse_error(self, experimento, error):
        self.notificar("timelapse_error", "el timelapse se detuvo",
                       f"El timelapse «{experimento}» se detuvo por un error: {error}",
                       repetir=False)
        # Que el siguiente timelapse pueda volver a avisar.
        with self._lock:
            self._ultimo_envio.pop("timelapse_error", None)

    def timelapse_fin(self, experimento, ciclos, detenido):
        if self.config["avisar_fin"]:
            self.aviso("timelapse_fin", "timelapse terminado",
                       f"«{experimento}» {'se detuvo' if detenido else 'terminó'} "
                       f"con {ciclos} ciclo(s).")

    # ---------- revision periodica ----------
    def _fuera_de_rango(self, clave, fuera, minutos, asunto, texto, texto_ok):
        if fuera:
            desde = self._fuera_desde.setdefault(clave, time.monotonic())
            if time.monotonic() - desde >= minutos * 60:
                self.notificar(clave, asunto, texto)
        else:
            self._fuera_desde.pop(clave, None)
            self.resuelto(clave, texto_ok)

    def revisar(self):
        """Una pasada de las condiciones que se miran solas (la llama el
        hilo cada 30 s)."""
        c = self.config
        if not c["activo"]:
            return
        vigilar = (not c["solo_con_timelapse"]) or self.timelapse_corriendo()
        tc = self.incubadora
        if tc is not None and vigilar:
            sin_datos = tc.temperature is None or bool(getattr(tc, "error_msg", None))
            self._fuera_de_rango(
                "incubadora_sin_datos", sin_datos, c["sin_datos_min"],
                "sin datos de la incubadora",
                "No llegan datos del Arduino de la incubadora"
                + (f" ({tc.error_msg})" if getattr(tc, "error_msg", None) else "")
                + ". No se está registrando ni controlando la temperatura.",
                "Volvieron a llegar datos de la incubadora.")
            if tc.temperature is not None and tc.setpoint is not None:
                dif = float(tc.temperature) - float(tc.setpoint)
                self._fuera_de_rango(
                    "temperatura", abs(dif) > c["temperatura_margen_c"], c["minutos_fuera"],
                    "temperatura fuera de rango",
                    f"La incubadora está a {float(tc.temperature):.1f} °C "
                    f"(pedido {float(tc.setpoint):.1f} °C) desde hace más de "
                    f"{c['minutos_fuera']} min.",
                    f"La temperatura volvió a rango: {float(tc.temperature):.1f} °C.")
            co2 = getattr(tc, "co2", None)
            co2_sp = getattr(tc, "co2_setpoint", None)
            if co2 is not None and co2_sp is not None:
                dif = (float(co2) - float(co2_sp)) / 10000
                self._fuera_de_rango(
                    "co2", abs(dif) > c["co2_margen_pct"], c["minutos_fuera"],
                    "CO₂ fuera de rango",
                    f"El CO₂ está en {float(co2) / 10000:.1f} % (pedido "
                    f"{float(co2_sp) / 10000:.1f} %) desde hace más de {c['minutos_fuera']} min.",
                    f"El CO₂ volvió a rango: {float(co2) / 10000:.1f} %.")
        if self.espacio is not None:
            try:
                libre = self.espacio()
            except Exception:
                libre = None
            if libre is not None:
                poco = libre < c["disco_min_gb"] * 1e9
                if poco:
                    self.notificar("disco", "queda poco espacio",
                                   f"Quedan {libre / 1e9:.1f} GB libres en la Raspberry.")
                else:
                    self.resuelto("disco", f"Hay espacio otra vez: {libre / 1e9:.1f} GB libres.")

    def iniciar(self, cada_s=30):
        if self._hilo is not None:
            return

        def bucle():
            while True:
                try:
                    self.revisar()
                except Exception as e:
                    print(f"[alertas] error revisando: {e}")
                time.sleep(cada_s)
        self._hilo = threading.Thread(target=bucle, daemon=True)
        self._hilo.start()
