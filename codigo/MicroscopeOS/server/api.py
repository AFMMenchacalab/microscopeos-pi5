from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import os
import re
import io
import zipfile
import time
import numpy as np
import cv2
import tifffile
import asyncio
import json
import sys
import threading
from pathlib import Path

# Raiz del proyecto, deducida de la ubicacion de este archivo.
# Antes era sys.path.insert(0, '/home/microscope1/MicroscopeOS'), que ataba
# el codigo al usuario y la ruta de la Pi 4.
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

STATIC_DIR = BASE_DIR / "server" / "static"

from temperature_controller import temperature_controller
from core.timelapse import MODOS
from core.profile_manager import ProfileManager
from core.config import SystemConfig, CameraSettings, TimelapseSettings
from core.experimentos import Experimentos, ID_RE, LEGADO_RE, BYTES_POR_FOTO
from core import experimentos as exp_mod
from core import marca_agua
from core import metadatos as metadatos_mod
from core import dpc
from core.optica import Optica, OBJETIVOS
from core.actualizar import Actualizador, ErrorActualizar, reiniciar

# ===============================
# Galeria de archivos: solo lectura, con nombres validados por regex
# para no aceptar un path arbitrario del cliente (ver _ruta_captura /
# _ruta_timelapse mas abajo).
# ===============================
_FOLDER_RE = re.compile(r'^timelapse_\d{8}_\d{6}$')
_FILE_RE = re.compile(r'^[A-Za-z0-9_.\-]+\.tif$')
_PERFIL_RE = re.compile(r'^[A-Za-z0-9 _\-]{1,40}$')
ARCHIVO_ILUM = BASE_DIR / "profiles" / "iluminacion.json"
# Calibracion de imagen (exposicion + balance de blancos por camara) y el
# campo plano de cada camara en profiles/flat_cam{n}.npy.
ARCHIVO_CALIB = BASE_DIR / "profiles" / "calibracion_imagen.json"


def _ruta_flat(cam):
    return ARCHIVO_CALIB.parent / f"flat_cam{cam}.npy"


def _ruta_captura(filename):
    if not _FILE_RE.match(filename):
        return None
    carpeta = (BASE_DIR / "capturas_unicas").resolve()
    p = (carpeta / filename).resolve()
    if p.parent != carpeta or not p.is_file():
        return None
    return p


def _ruta_timelapse(folder, cam, filename):
    if not _FOLDER_RE.match(folder) or cam not in (0, 1) or not _FILE_RE.match(filename):
        return None
    carpeta = (BASE_DIR / folder / f"cam{cam}").resolve()
    p = (carpeta / filename).resolve()
    if p.parent != carpeta or not p.is_file():
        return None
    return p


def _thumb_jpeg(path, max_dim=320):
    """Miniatura 8-bit de un .tif crudo de 16 bits, igual normalizacion
    que _preview_png (cv2.NORM_MINMAX) para que se vea consistente con
    el vivo. JPEG (no PNG) porque es solo para previsualizar -- el
    archivo original de 16 bits siempre se sirve intacto por separado
    via /files/raw/... para quien necesite los datos crudos."""
    img = tifffile.imread(str(path))
    norm = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    h, w = norm.shape[:2]
    escala = max_dim / max(h, w)
    if escala < 1:
        norm = cv2.resize(norm, (max(1, int(w * escala)), max(1, int(h * escala))))
    ok, buf = cv2.imencode(".jpg", norm, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return buf.tobytes()


class ProfileSaveReq(BaseModel):
    name: str
    interval_seconds: int = 300
    duration_seconds: int = 3600


class ExposureReq(BaseModel):
    exposure: int
    gain: float

class BrightnessReq(BaseModel):
    percent: int

class LightReq(BaseModel):
    # full|left|right|top|bottom|ring|rheinberg
    modo: str = "full"
    percent: int | None = None
    color_centro: str = "0000FF"
    color_anillo: str = "FF6A00"
    camaras: list = [0, 1]

class NombreReq(BaseModel):
    nombre: str

class CodigoReq(BaseModel):
    codigo: str

class UsbPuntoReq(BaseModel):
    punto: str

class OpticaReq(BaseModel):
    camara: int = 0
    objetivo: str | None = None
    aumento: float | None = None
    na: float | None = None
    aumento_adicional: float | None = None
    pixel_um: float | None = None
    um_por_pixel_medido: float | None = None
    borrar_medido: bool = False

class MarcaReq(BaseModel):
    config: dict | None = None
    preset: str | None = None

class LogoReq(BaseModel):
    png_base64: str

class ColoresReq(BaseModel):
    camaras: list = [0, 1]
    campo: str | None = None      # RRGGBB del campo claro ("FFFFFF" = blanco)
    dpc: str | None = None        # RRGGBB del relieve DPC

class LightOffReq(BaseModel):
    camaras: list | None = None   # None = todas

class ColorDpcReq(BaseModel):
    color: str = "00FF00"     # RRGGBB; "FFFFFF" = blanco

class TimelapseReq(BaseModel):
    modo: str = "blanco"
    interval: int = 300
    duration: int = 3600
    stabilization: float = 0.3
    nombre: str = ""
    camaras: list = [0, 1]
    # Captura de las dos camaras a la vez (solo Pi 5). Por defecto False:
    # requiere que los canales opticos esten aislados. Ver TODO_HW.md.
    simultaneo: bool = False
    # Reenfocar antes de capturar. autofocus_cada=N reenfoca 1 de cada N
    # ciclos: el barrido cuesta ~20 s por camara, que en un intervalo de
    # 30 s no entra, pero en uno de 5 min es despreciable.
    autofocus: bool = False
    autofocus_cada: int = 1
    # Rango en MICRAS (amplitud total, centrada donde quedo el ciclo
    # anterior). Si no encuentra el foco, reintenta con el doble hasta
    # autofocus_rango_max_um. Ver Autofocus.enfocar_auto.
    autofocus_rango_um: float = 40.0
    autofocus_rango_max_um: float = 200.0
    autofocus_puntos: int = 11
    # Contar celulas en las capturas de cada ciclo -> conteo.csv,
    # poblacion.png y eventos.csv al terminar.
    contar: bool = False
    contar_cada: int = 1
    contar_overlay: bool = True
    # Donde guardar: "local" (directorio del servicio, como siempre) o
    # "usb" (en usb_punto, que tiene que ser una memoria USB detectada
    # por core/usb.py -- la API no acepta rutas arbitrarias del cliente).
    destino: str = "local"
    usb_punto: str = ""
    # Mandar cada imagen a la PC que segmenta en vivo (core/envio.py).
    enviar_pc: bool = False
    # Copia de respaldo de cada imagen en el NAS (core/respaldo_nas.py),
    # en paralelo con el envio a la PC.
    respaldar_nas: bool = False
    # Solo en modo dpc (core/dpc.py): al terminar cada ciclo calcular el
    # DPC (dos TIFF de 16 bits comprimidos) y borrar las 4 crudas, que
    # son 64 MB por camara y por ciclo. Opcional: campo claro reducido,
    # fase y vista JPEG.
    dpc_procesar: bool = True
    dpc_borrar_crudas: bool = True
    dpc_suma: bool = True
    dpc_fase: bool = False
    dpc_jpg: bool = True


class UsbAccionReq(BaseModel):
    punto: str

class UsbCopiarReq(BaseModel):
    carpeta: str              # nombre timelapse_YYYYMMDD_HHMMSS (local)
    punto: str

class EnvioConfigReq(BaseModel):
    url: str | None = None
    token: str | None = None
    activo: bool | None = None
    nombre_pc: str | None = None

class NasConfigReq(BaseModel):
    modo: str | None = None           # smb | carpeta
    servidor: str | None = None
    recurso: str | None = None        # carpeta compartida del NAS
    subcarpeta: str | None = None
    usuario: str | None = None
    contrasena: str | None = None     # vacia = conservar la guardada
    ruta_local: str | None = None     # modo carpeta
    activo: bool | None = None

class EnvioReenviarReq(BaseModel):
    carpeta: str              # timelapse_... local, o ruta dentro de una USB detectada

class SetpointPayload(BaseModel):
    value: float

# Hay un motor de enfoque por camara: "motor" es el numero de camara
# (ver PINES_POR_CAMARA en core/motor_focus.py).
class FocusMoveReq(BaseModel):
    motor: int = 0
    direction: int = 1        # 1 = abajo, -1 = arriba (ver core/motor_focus.py)
    pasos: int = 200          # en la resolucion de microstepping actual
    # Alternativa en micras (la usa la interfaz): si viene, pisa a pasos.
    um: float | None = None
    velocidad: float = 0.003  # delay entre flancos STEP -- mas chico = mas rapido

class FocusJogReq(BaseModel):
    """Movimiento continuo del joystick. La interfaz reenvia este mismo
    pedido cada pocas decimas mientras el joystick esta apretado: cada
    uno refresca el watchdog y puede cambiar sentido y velocidad en
    caliente. Si dejan de llegar (WiFi caido, pestania cerrada), el
    motor se para solo al vencer el watchdog."""
    motor: int = 0
    direction: int = 1
    velocidad: float = 0.003
    watchdog: float = 1.5

class FocusStopReq(BaseModel):
    motor: int | None = None  # None = parar todos

class FocusConfigReq(BaseModel):
    motor: int = 0
    microsteps: int = 16      # 1,2,4,8,16,32,64,128,256 (resolucion/precision)
    corriente_ma: int | None = None

class AutofocusReq(BaseModel):
    camera: int = 0
    # auto = IA si hay modelo, si no DPC si esa camara esta calibrada,
    # si no barrido a ciegas.
    metodo: str = "auto"      # auto | ia | dpc | barrido
    rango: int = 800          # amplitud total del barrido, en micropasos
    # Alternativa en unidades fisicas: si se manda, pisa a `rango`
    # convertido a la resolucion de microstepping ACTUAL de esa camara.
    # Es lo que usa el boton "Autofoco" de la interfaz -- pensar el
    # rango en micras (cuan lejos del punto donde el usuario ya enfoco a
    # mano puede llegar a estar el foco real) es mucho mas intuitivo que
    # en micropasos, que dependen de la resolucion configurada.
    rango_um: float | None = None
    # Si en rango_um no aparece el foco, reintentar con el doble hasta
    # este tope (micras). None = un solo intento.
    rango_max_um: float | None = None
    puntos: int = 13
    refinamientos: int = 2
    iteraciones: int = 2      # correcciones sucesivas del metodo DPC
    usar_luz: bool = True     # solo barrido: el DPC necesita L/R si o si
    patron: str = "on"        # solo barrido: metodo de IlluminationController
    # Cuantas mediciones L/R rapidas se combinan por mediana antes de
    # decidir. None = usa el default de cada metodo (3 para DPC, 1 para
    # el barrido de respaldo -- este ultimo ya samplea muchos puntos del
    # barrido, promediar cada uno ademas lo hace demasiado lento).
    repeticiones: int | None = None

class PilaFocoReq(BaseModel):
    """Grabacion de una pila de foco (dataset para el autofoco IA).

    Ver core/pila_foco.py: barre Z alrededor de la posicion actual, que
    se toma como el foco, y guarda el par de medias aperturas de cada
    plano con su desenfoque real como etiqueta."""
    camera: int = 0
    rango: int = 1600         # recorrido total en micropasos
    puntos: int = 25
    eje: str = "lr"
    notas: str = ""

class ConteoConfigReq(BaseModel):
    """Encendido/apagado del conteo en vivo y sus perillas.

    Todos los campos son opcionales: la interfaz manda solo lo que
    cambia (el boton manda `camara`+`activo`, los deslizadores mandan
    el parametro suelto) y el resto queda como estaba."""
    camara: int | None = None
    activo: bool | None = None
    # manual = mide solo cuando se lo pide (/api/analisis/medir) y deja
    # el resultado congelado; auto = vuelve a medir cada `periodo`.
    modo: str | None = None
    diametro_px: float | None = None
    umbral: float | None = None
    separar: bool | None = None
    periodo: float | None = None

class ConteoMedirReq(BaseModel):
    """Medicion puntual sobre el vivo (modo manual).

    Pone media apertura un instante, mide, y devuelve la iluminacion a
    donde estaba. El dibujo queda congelado sobre el vivo hasta la
    proxima medicion."""
    camera: int = 0
    # Patron de media apertura a usar durante la medicion. None = no
    # tocar la luz (para muestras tenidas, que se ven en campo claro).
    patron: str | None = "left"
    settle: float = 0.3

class ConteoFotoReq(BaseModel):
    camera: int = 0
    # dpc = captura el par L/R y cuenta sobre la imagen DPC (lo correcto
    # para celulas vivas sin tenir); blanco = una sola captura.
    modo: str = "dpc"
    ancho_max: int = 1200
    guardar_overlay: bool = True

class ConteoCarpetaReq(BaseModel):
    carpeta: str
    intervalo_s: int | None = None

class CalibrarImagenReq(BaseModel):
    camaras: list = [0, 1]


class CalibrarDpcReq(BaseModel):
    camera: int = 0
    amplitud: int | None = None   # micropasos; si viene, pisa a amplitud_um
    amplitud_um: float = 24.0     # recorrido total para ajustar la recta
    enfocar_antes: bool = True    # buscar el foco primero y calibrar ahi
    puntos: int = 9
    eje: str = "lr"           # lr (izquierda/derecha) o tb (arriba/abajo)
    repeticiones: int = 2     # mediciones por punto de calibracion, por mediana


def create_app(camera, illuminations, timelapse, motores=None,
               autofocus=None, motor=None, conteo=None, usb=None,
               enviador=None, respaldo_nas=None, experimentos=None, optica=None,
               actualizador=None):
    """motores: {numero_de_camara: FocusMotorController}. `motor` se
    acepta todavia como un solo eje suelto (compatibilidad con la
    version de un motor) y se mapea a la camara 0.

    conteo: core.analisis.ContadorEnVivo, o None para arrancar sin
    conteo de celulas."""
    experimentos = experimentos or Experimentos()
    optica = optica or Optica()
    actualizador = actualizador or Actualizador()

    app = FastAPI()
    if motores is None:
        motores = {0: motor} if motor is not None else {}
    # "luz": ultimo modo aplicado a cada matriz, para poder restaurarlo
    # despues de una medicion puntual de celulas (ver _luz_aplicar).
    estado = {"camara_activa": 0, "luz": {}}
    # Serializa los comandos a las matrices: el deslizador de brillo manda
    # varios pedidos seguidos, FastAPI los atiende en hilos distintos, y
    # dos escrituras a la vez por el mismo puerto serie se mezclan.
    luz_lock = threading.RLock()
    profile_manager = ProfileManager()
    # Serializa el autofoco: mueve motor Y camara a la vez, asi que dos
    # corridas simultaneas se pisarian el modo de la camara.
    autofocus_lock = threading.Lock()

    def _preview_png(camera_num):
        # Un archivo temporal por camara: con las dos vistas en vivo/foto
        # pudiendo pedirse al mismo tiempo, compartir un unico nombre era
        # una condicion de carrera (una pisaba el .tif de la otra).
        tmp = f"/tmp/preview_cam{camera_num}.tif"
        camera.capture_image(camera_num=camera_num, folder="/tmp", filename=tmp)
        img = tifffile.imread(tmp)
        norm = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        ok, buf = cv2.imencode(".png", norm)
        return buf.tobytes()

    # ===============================
    # Preview
    # ===============================
    @app.get("/preview/{camera_num}")
    def preview(camera_num: int):
        if timelapse.is_running():
            return Response(status_code=409)
        # Solo para la camara pedida: la Pi 5 puede tener la otra en vivo
        # al mismo tiempo, y una foto suelta no debe cortarle el stream.
        camera.stop_preview(camera_num)
        estado["camara_activa"] = camera_num
        png = _preview_png(camera_num)
        return Response(content=png, media_type="image/png")

    # ===============================
    # Vivo -- las dos camaras pueden estar activas a la vez (Pi 5, sin mux)
    # ===============================
    @app.post("/live/start/{camera_num}")
    def live_start(camera_num: int):
        if timelapse.is_running():
            return {"error": "Timelapse en curso"}
        camera.start_preview(camera_num)
        return {"status": "live", "cam": camera_num}

    @app.post("/live/stop/{camera_num}")
    def live_stop_one(camera_num: int):
        camera.stop_preview(camera_num)
        return {"status": "stopped", "cam": camera_num}

    @app.post("/live/stop")
    def live_stop_all():
        camera.stop_preview()
        return {"status": "stopped"}

    @app.get("/live/stream/{camera_num}")
    def live_stream(camera_num: int):
        def gen():
            # El gancho se pasa siempre; ContadorEnVivo devuelve el frame
            # intacto para las camaras que tienen el conteo apagado, asi
            # que encender/apagar desde la interfaz no obliga a
            # reconectar el stream.
            anotar = conteo.anotar if conteo is not None else None
            while camera_num in camera._preview_cams:
                frame = camera.get_preview_frame(camera_num, anotar=anotar)
                if frame is None:
                    break
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
                time.sleep(0.06)
        return StreamingResponse(
            gen(), media_type='multipart/x-mixed-replace; boundary=frame')

    # ===============================
    # Luz
    # ===============================
    # Metodos de IlluminationController por nombre de modo (ver
    # core/illumination.py). "rheinberg" no entra en este dict porque
    # necesita los dos colores como argumento, no llamada sin parametros.
    _METODOS_LUZ = {
        "full": "on", "left": "left", "right": "right",
        "top": "top", "bottom": "bottom", "ring": "ring",
    }

    def _luz_aplicar(cam, modo, percent=None, color_centro="0000FF",
                     color_anillo="FF6A00"):
        """Aplica un modo a una matriz y RECUERDA cual quedo puesto.

        Hace falta recordarlo para poder devolver la iluminacion a donde
        estaba despues de una medicion puntual de celulas, que necesita
        media apertura por un instante. Sin este registro, la unica
        forma de "restaurar" seria apagar la luz -- y dejarle la muestra
        a oscuras a alguien que la estaba mirando es peor que no
        restaurar nada.
        """
        luz = illuminations.get(cam)
        if luz is None:
            return
        with luz_lock:
            if percent is not None:
                # Sin reenviar el patron: lo manda la llamada de abajo.
                luz.brightness_percent = max(0, min(100, percent))
            if modo == "off":
                luz.off()
            elif modo == "rheinberg":
                luz.rheinberg(color_centro, color_anillo)
            else:
                getattr(luz, _METODOS_LUZ[modo])()
            previo = estado["luz"].get(cam) or {}
            estado["luz"][cam] = {
                "modo": modo,
                # Al apagar se recuerda el brillo que tenia, para mostrarlo.
                "percent": percent if percent is not None else previo.get("percent"),
                # y el ultimo modo encendido, para que al volver a prender
                # la interfaz ofrezca el mismo.
                "modo_previo": (previo.get("modo") if previo.get("modo") not in (None, "off")
                                else previo.get("modo_previo")),
                "color_centro": color_centro,
                "color_anillo": color_anillo}

    def _luz_restaurar(cam, previo):
        """Vuelve a como estaba. Si nunca se supo, apaga."""
        if previo is None:
            luz = illuminations.get(cam)
            if luz:
                luz.off()
            estado["luz"][cam] = {"modo": "off", "percent": None}
            return
        _luz_aplicar(cam, previo.get("modo", "off"), previo.get("percent"),
                     previo.get("color_centro", "0000FF"),
                     previo.get("color_anillo", "FF6A00"))

    @app.post("/light/set")
    def light_set(req: LightReq):
        """Enciende un modo de iluminacion (campo claro/DPC/campo oscuro/
        Rheinberg) en las matrices indicadas. Reemplaza a /light/on para
        control desde la vista en vivo -- ese endpoint solo sabia FULL."""
        if timelapse.is_running():
            return {"error": "Timelapse en curso"}
        if req.modo != "rheinberg" and req.modo not in _METODOS_LUZ:
            return {"error": f"Modo invalido: {req.modo}"}
        for cam in req.camaras:
            _luz_aplicar(cam, req.modo, req.percent,
                         req.color_centro, req.color_anillo)
        if req.modo == "rheinberg":
            _guardar_colores()      # los colores de Rheinberg tambien se recuerdan
        return {"status": "ok", "modo": req.modo}

    # Colores de cada matriz: se guardan en profiles/iluminacion.json
    # (ARCHIVO_ILUM, a nivel de modulo) para que sobrevivan a un reinicio.

    def _color_dpc_actual():
        for luz in illuminations.values():
            if luz is not None:
                return getattr(luz, "color_dpc", None) or "FFFFFF"
        return None

    def _guardar_colores():
        """profiles/iluminacion.json: colores de cada matriz. "color_dpc"
        queda por compatibilidad con versiones anteriores (un solo color)."""
        datos = {"color_dpc": _color_dpc_actual(), "camaras": {}}
        for cam, luz in illuminations.items():
            if luz is not None:
                datos["camaras"][str(cam)] = {
                    "dpc": getattr(luz, "color_dpc", None) or "FFFFFF",
                    "campo": getattr(luz, "color_campo", None) or "FFFFFF",
                    "rheinberg": list(getattr(luz, "_rheinberg_colors", ("0000FF", "FF6A00")))}
        try:
            ARCHIVO_ILUM.parent.mkdir(parents=True, exist_ok=True)
            ARCHIVO_ILUM.write_text(json.dumps(datos, indent=2))
        except OSError:
            pass

    @app.post("/light/colores")
    def light_colores(req: ColoresReq):
        """Color del campo claro y/o del relieve DPC de las matrices
        indicadas (cada camara puede tener los suyos). Si la matriz esta
        encendida en ese modo, cambia al momento."""
        if timelapse.is_running():
            return {"error": "Timelapse en curso"}
        try:
            with luz_lock:
                for cam in req.camaras:
                    luz = illuminations.get(cam)
                    if luz is None:
                        continue
                    if req.campo is not None and hasattr(luz, "set_color_campo"):
                        luz.set_color_campo(req.campo)
                    if req.dpc is not None:
                        luz.set_color_dpc(req.dpc)
        except ValueError as e:
            return {"error": str(e)}
        _guardar_colores()
        return light_estado()

    @app.get("/light/color_dpc")
    def color_dpc_get():
        return {"color": _color_dpc_actual()}

    @app.post("/light/color_dpc")
    def color_dpc_set(req: ColorDpcReq):
        if timelapse.is_running():
            return {"error": "Timelapse en curso"}
        try:
            for luz in illuminations.values():
                if luz is not None:
                    luz.set_color_dpc(req.color)
        except ValueError as e:
            return {"error": str(e)}
        _guardar_colores()
        return {"color": _color_dpc_actual()}

    @app.post("/light/on")
    def light_on():
        if timelapse.is_running():
            return {"error": "Timelapse en curso"}
        _luz_aplicar(estado["camara_activa"], "full")
        return {"status": "on"}

    @app.post("/light/off")
    def light_off(req: LightOffReq | None = None):
        camaras = illuminations if req is None or req.camaras is None else req.camaras
        for cam in camaras:
            _luz_aplicar(cam, "off")
        return {"status": "off"}

    @app.get("/light/estado")
    def light_estado():
        """Como quedo cada matriz (para que la interfaz muestre lo real al
        abrirse, o despues de una captura o un conteo que tocaron la luz)."""
        out = {}
        for cam, luz in illuminations.items():
            if luz is None:
                continue
            e = estado["luz"].get(cam) or {}
            out[str(cam)] = {
                "encendida": bool(getattr(luz, "state", False)),
                "modo": (e.get("modo") if e.get("modo") not in (None, "off")
                         else e.get("modo_previo") or "full"),
                "percent": getattr(luz, "brightness_percent", e.get("percent")),
                "color_campo": getattr(luz, "color_campo", None) or "FFFFFF",
                "color_dpc": getattr(luz, "color_dpc", None) or "FFFFFF",
                "rheinberg": list(getattr(luz, "_rheinberg_colors", ("0000FF", "FF6A00"))),
            }
        return {"matrices": out}

    # ===============================
    # Captura
    # ===============================
    # /capture/both/{modo} DEBE declararse antes que /capture/{camera_num}/
    # {modo}: FastAPI prueba las rutas en orden de declaracion, y con el
    # orden invertido "both" se intentaba parsear como camera_num:int y
    # tiraba 422 antes de llegar siquiera a esta ruta.
    def _captura_meta(nombre_exp, carpeta, sufijo):
        canales = {"_L": "izquierda", "_R": "derecha", "_T": "arriba", "_B": "abajo"}
        m = {"experimento": {"nombre": nombre_exp, "id": carpeta.name, "tipo": "foto"}}
        if sufijo:
            m["canal_dpc"] = canales.get(sufijo, sufijo)
        return m

    @app.post("/capture/both/{modo}")
    def capture_both(modo: str, nombre: str = ""):
        """Captura las dos camaras en paralelo (Pi 5, sin mux).

        Enciende AMBAS matrices a la vez. Ver TODO_HW.md (prioridad 2)
        antes de usarlo con datos que importen.

        Las fotos van al experimento de hoy con ese nombre (o a «Fotos
        sueltas»), y al terminar la luz vuelve a como estaba.
        """
        if timelapse.is_running():
            return {"error": "Timelapse en curso"}
        from datetime import datetime
        carpeta = experimentos.carpeta_fotos(nombre)
        nombre_exp = experimentos.info(carpeta)["nombre"]
        ahora = datetime.now()
        patrones = MODOS.get(modo, MODOS["blanco"])
        cams = sorted(illuminations.keys())
        previos = {c: estado["luz"].get(c) for c in cams}
        guardados = []
        try:
            for sufijo, metodo in patrones:
                with luz_lock:
                    for cam in cams:
                        luz = illuminations.get(cam)
                        if luz:
                            getattr(luz, metodo)()
                time.sleep(0.3)
                res = camera.capture_both(
                    folder=str(carpeta),
                    filenames={c: str(carpeta / f"cam{c}" / exp_mod.nombre_foto(ahora, sufijo=sufijo))
                               for c in cams},
                    camera_nums=cams, meta=_captura_meta(nombre_exp, carpeta, sufijo))
                guardados += [str(Path(v).relative_to(carpeta)) for v in res.values()]
        finally:
            for cam in cams:
                _luz_restaurar(cam, previos[cam])
        return {"saved": guardados, "experimento": carpeta.name, "nombre": nombre_exp}

    @app.post("/capture/{camera_num}/{modo}")
    def capture(camera_num: int, modo: str, nombre: str = ""):
        if timelapse.is_running():
            return {"error": "Timelapse en curso"}
        from datetime import datetime
        carpeta = experimentos.carpeta_fotos(nombre)
        nombre_exp = experimentos.info(carpeta)["nombre"]
        ahora = datetime.now()
        guardados = []
        patrones = MODOS.get(modo, MODOS["blanco"])
        luz = illuminations.get(camera_num)
        previo = estado["luz"].get(camera_num)
        try:
            for sufijo, metodo in patrones:
                if luz:
                    with luz_lock:
                        getattr(luz, metodo)()
                    time.sleep(0.3)
                fn = carpeta / f"cam{camera_num}" / exp_mod.nombre_foto(ahora, sufijo=sufijo)
                camera.capture_image(camera_num=camera_num, folder=str(fn.parent),
                                     filename=str(fn),
                                     meta=_captura_meta(nombre_exp, carpeta, sufijo))
                guardados.append(str(fn.relative_to(carpeta)))
        finally:
            _luz_restaurar(camera_num, previo)
        return {"saved": guardados, "experimento": carpeta.name, "nombre": nombre_exp}

    # ===============================
    # Exposicion / Brillo
    # ===============================
    @app.post("/exposure")
    def set_exposure(req: ExposureReq):
        camera.set_exposure(req.exposure, req.gain)
        return {"status": "ok"}

    # ===============================
    # Calibracion de imagen (EN PRUEBAS)
    # ===============================
    # Ver Camera.calibrar_imagen(). Se guarda solo si TODAS las camaras
    # pedidas convergen: en la Pi el balance llego una vez al tope (8.0) y
    # dejar eso puesto pinta toda la imagen de un color.
    def _cargar_calibracion():
        try:
            datos = json.loads(ARCHIVO_CALIB.read_text())
        except (OSError, ValueError):
            return
        if not hasattr(camera, "set_colour_gains"):
            return
        for cam, gains in (datos.get("colour_gains") or {}).items():
            cam = int(cam)
            camera.set_colour_gains(cam, gains)
            try:
                camera.set_flat(cam, np.load(_ruta_flat(cam)))
            except (OSError, ValueError):
                pass
        if datos.get("exposure_us"):
            camera.set_exposure(int(datos["exposure_us"]), camera.gain)

    _cargar_calibracion()

    @app.get("/camera/calibracion")
    def calibracion_get():
        try:
            datos = json.loads(ARCHIVO_CALIB.read_text())
        except (OSError, ValueError):
            return {"calibrada": False}
        return {"calibrada": True, **datos}

    @app.post("/camera/calibrar")
    def calibrar_imagen(req: CalibrarImagenReq):
        """Con un portaobjetos vacio: enciende campo claro BLANCO en cada
        camara, calibra y deja la luz como estaba."""
        if timelapse.is_running():
            return {"error": "Timelapse en curso"}
        if not hasattr(camera, "calibrar_imagen"):
            return {"error": "Esta camara no se puede calibrar"}
        resultados, exposiciones = {}, []
        for cam in req.camaras:
            luz = illuminations.get(cam)
            previo = estado["luz"].get(cam)
            color = getattr(luz, "color_campo", None)
            try:
                if luz is not None and hasattr(luz, "set_color_campo"):
                    luz.set_color_campo("FFFFFF")   # blanco, solo mientras
                _luz_aplicar(cam, "full")
                time.sleep(0.3)
                r = camera.calibrar_imagen(cam)
            except Exception as e:
                return {"error": f"Camara {cam}: {e}"}
            finally:
                if luz is not None and hasattr(luz, "set_color_campo"):
                    luz.set_color_campo(color or "FFFFFF")
                _luz_restaurar(cam, previo)
            resultados[str(cam)] = r
            exposiciones.append(r["exposure_us"])
        if not all(r["converge"] for r in resultados.values()):
            # Se vuelve a lo que habia guardado (o a nada).
            for cam in req.camaras:
                camera.set_colour_gains(cam, None)
                camera.set_flat(cam, None)
            _cargar_calibracion()
            return {"error": "La calibracion no convergio (el color quedo en "
                             "el limite). No se guardo nada: revisa que el "
                             "campo este vacio y la luz en blanco.",
                    "camaras": resultados}
        # La exposicion es comun: la menor, para que ninguna sature.
        exposicion = min(exposiciones)
        camera.set_exposure(exposicion, camera.gain)
        try:
            datos = json.loads(ARCHIVO_CALIB.read_text())
        except (OSError, ValueError):
            datos = {}
        datos["exposure_us"] = exposicion
        datos.setdefault("colour_gains", {})
        datos["fecha"] = time.strftime("%Y-%m-%d %H:%M")
        try:
            ARCHIVO_CALIB.parent.mkdir(parents=True, exist_ok=True)
            for cam in req.camaras:
                datos["colour_gains"][str(cam)] = resultados[str(cam)]["colour_gains"]
                flat = camera.get_flat(cam)
                if flat is not None:
                    # 160x120 basta: el mapa es suave y se reescala al vivo
                    np.save(_ruta_flat(cam),
                            cv2.resize(flat, (160, 120), interpolation=cv2.INTER_AREA))
            ARCHIVO_CALIB.write_text(json.dumps(datos, indent=2))
        except OSError as e:
            return {"error": f"No se pudo guardar: {e}", "camaras": resultados}
        return {"status": "ok", "exposure_us": exposicion, "camaras": resultados}

    @app.post("/camera/calibracion/borrar")
    def calibracion_borrar():
        for cam in list(getattr(camera, "_colour_gains", {})) + list(illuminations):
            if hasattr(camera, "set_colour_gains"):
                camera.set_colour_gains(cam, None)
                camera.set_flat(cam, None)
            try:
                _ruta_flat(cam).unlink()
            except OSError:
                pass
        try:
            ARCHIVO_CALIB.unlink()
        except OSError:
            pass
        return {"calibrada": False}

    @app.post("/brightness")
    def set_brightness(req: BrightnessReq):
        for luz in illuminations.values():
            luz.set_brightness(req.percent)
        return {"status": "ok"}

    # ===============================
    # Timelapse
    # ===============================
    def _dpc_opts(req):
        if req.modo != "dpc" or not req.dpc_procesar:
            return None
        return {"borrar_crudas": req.dpc_borrar_crudas, "suma": req.dpc_suma,
                "fase": req.dpc_fase, "jpg": req.dpc_jpg}

    def _bytes_por_ciclo(req):
        """Lo que queda en disco por camara y por ciclo (igual que
        tlBytesPorCiclo en index_uiux.html)."""
        opts = _dpc_opts(req)
        if opts is None:
            return len(MODOS.get(req.modo, [1])) * BYTES_POR_FOTO
        return dpc.bytes_por_ciclo(opts)

    @app.post("/timelapse/start")
    def start_timelapse(req: TimelapseReq):
        if timelapse.is_running():
            return {"error": "Ya hay un timelapse corriendo"}
        carpeta_raiz = ""
        if req.destino == "usb":
            d = usb.punto_valido(req.usb_punto) if usb is not None else None
            if d is None:
                return {"error": "La memoria USB elegida no esta disponible "
                        "o es de solo lectura"}
            # Avisar antes de empezar, no a mitad de la noche.
            ciclos = max(1, req.duration // max(1, req.interval))
            necesario = ciclos * len(req.camaras) * _bytes_por_ciclo(req)
            if d.get("libre_bytes") is not None and necesario > d["libre_bytes"]:
                return {"error": f"La memoria no alcanza: el timelapse ocupa "
                        f"~{necesario / 1e9:.1f} GB y hay "
                        f"{d['libre_bytes'] / 1e9:.1f} GB libres"}
            carpeta_raiz = d["punto"]
        elif req.destino != "local":
            return {"error": f"destino invalido: {req.destino}"}
        else:
            ciclos = max(1, req.duration // max(1, req.interval))
            necesario = ciclos * len(req.camaras) * _bytes_por_ciclo(req)
            libre = experimentos.espacio()["libre_bytes"]
            if necesario > libre * 0.95:
                return {"error": f"No alcanza el espacio en la Raspberry: el timelapse "
                        f"ocupa ~{necesario / 1e9:.1f} GB y hay {libre / 1e9:.1f} GB libres. "
                        f"Borra experimentos viejos, guárdalo en una memoria USB, o toma "
                        f"fotos menos seguido."}
        if req.enviar_pc and (enviador is None or not enviador.url):
            return {"error": "Falta configurar la direccion de la PC "
                    "(panel 'Envío a computadora')"}
        if req.respaldar_nas and (respaldo_nas is None or not respaldo_nas.configurado()):
            return {"error": "Falta configurar el NAS (panel 'Respaldo en NAS')"}
        timelapse.start(
            modo=req.modo,
            interval_seconds=req.interval,
            duration_seconds=req.duration,
            stabilization_time=req.stabilization,
            camaras=req.camaras,
            simultaneo=req.simultaneo,
            autofocus=req.autofocus,
            autofocus_cada=req.autofocus_cada,
            autofocus_opts={"rango_um": req.autofocus_rango_um,
                            "rango_max_um": req.autofocus_rango_max_um,
                            "puntos": req.autofocus_puntos},
            contar=req.contar,
            contar_cada=req.contar_cada,
            contar_opts={"overlay": req.contar_overlay},
            carpeta_raiz=carpeta_raiz,
            enviar_pc=req.enviar_pc,
            nombre=req.nombre,
            respaldar_nas=req.respaldar_nas,
            dpc_opts=_dpc_opts(req),
        )
        return {"status": "started", "autofocus": req.autofocus,
                "contar": req.contar, "destino": carpeta_raiz or "local",
                "enviar_pc": req.enviar_pc, "respaldar_nas": req.respaldar_nas,
                "dpc": _dpc_opts(req)}

    @app.post("/timelapse/stop")
    def stop_timelapse():
        timelapse.stop()
        return {"status": "stopped"}

    @app.get("/status")
    def status():
        return {
            "running": timelapse.is_running(),
            "camara_activa": estado["camara_activa"],
            "ciclo": getattr(timelapse, "ciclo_actual", 0),
            "carpeta": getattr(timelapse, "base_folder", None),
        }

    # ===============================
    # Temperatura (Arduino PID)
    # ===============================
    @app.on_event("startup")
    async def start_temp():
        asyncio.create_task(temperature_controller.run())

    @app.get("/api/temperature/status")
    def temp_status():
        return temperature_controller.status()

    @app.post("/api/temperature/setpoint")
    def temp_setpoint(payload: SetpointPayload):
        ok = temperature_controller.set_target_temperature(payload.value)
        return {"ok": ok, "error": temperature_controller.error_msg}

    @app.get("/api/temperature/stream")
    async def temp_stream():
        async def gen():
            while True:
                data = temperature_controller.status()
                yield f"data: {json.dumps(data)}\n\n"
                await asyncio.sleep(1.0)
        return StreamingResponse(gen(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ===============================
    # Foco motorizado (NEMA11 + TMC2209, ver core/motor_focus.py)
    # ===============================
    def _motor(num):
        return motores.get(num)

    def _ocupado():
        """Mientras el software mueve el eje por su cuenta (autofoco,
        calibracion, pila, timelapse), los controles manuales no pueden
        tocarlo: un paso a mano o un cambio de resolucion a mitad de un
        barrido lo arruina."""
        if timelapse.is_running():
            return "Timelapse en curso"
        if autofocus_lock.locked():
            return "Autofoco en curso, espera a que termine"
        return None

    @app.post("/api/focus/move")
    def focus_move(req: FocusMoveReq):
        """Salto puntual (los botones de paso fijo): `um` micras, o
        `pasos` micropasos de la resolucion actual."""
        ocupado = _ocupado()
        if ocupado:
            return {"error": ocupado}
        motor_ = _motor(req.motor)
        if motor_ is None:
            return {"error": f"cam{req.motor} no tiene motor de enfoque"}
        motor_.stop_jog()
        if req.um is not None:
            pos = motor_.mover_um(abs(req.um) * (1 if req.direction > 0 else -1),
                                  delay=req.velocidad)
        else:
            pos = motor_.mover(req.pasos, direction=req.direction,
                               delay=req.velocidad)
        return {"status": "ok", "motor": req.motor, "posicion": pos,
                "posicion_um": round(motor_.posicion_um, 2)}

    @app.post("/api/focus/jog")
    def focus_jog(req: FocusJogReq):
        """Arranca (o mantiene vivo) el movimiento continuo."""
        ocupado = _ocupado()
        if ocupado:
            return {"error": ocupado}
        motor_ = _motor(req.motor)
        if motor_ is None:
            return {"error": f"cam{req.motor} no tiene motor de enfoque"}
        motor_.start_jog(direction=req.direction, delay=req.velocidad,
                         watchdog=req.watchdog)
        return {"status": "jog", "motor": req.motor,
                "posicion": motor_.position,
                "posicion_um": round(motor_.posicion_um, 2)}

    @app.post("/api/focus/jog/stop")
    def focus_jog_stop(req: FocusStopReq):
        objetivos = motores.values() if req.motor is None \
            else [m for m in [_motor(req.motor)] if m is not None]
        posiciones = {}
        for m in objetivos:
            posiciones[m.nombre] = m.stop_jog()
        return {"status": "stopped", "posiciones": posiciones}

    @app.post("/api/focus/config")
    def focus_config(req: FocusConfigReq):
        """Cambia resolucion de micropasos y/o corriente en caliente.

        Antes esto recreaba el FocusMotorController entero (reabriendo
        GPIO y puerto serie) para cambiar de resolucion; ahora MRES se
        reescribe por UART sobre el CHOPCONF que ya esta cargado, que es
        lo que el chip espera y ademas conserva la posicion acumulada.
        """
        ocupado = _ocupado()
        if ocupado:
            return {"error": ocupado}
        motor_ = _motor(req.motor)
        if motor_ is None:
            return {"error": f"cam{req.motor} no tiene motor de enfoque"}
        motor_.stop_jog()
        try:
            motor_.set_microsteps(req.microsteps)
            if req.corriente_ma is not None:
                motor_.set_current(irun_ma=req.corriente_ma)
        except Exception as e:
            return {"error": str(e)}
        return {"status": "ok", "motor": req.motor,
                "microsteps": motor_.microsteps,
                "irun_ma": motor_.irun_ma_real,
                "posicion": motor_.position}

    @app.get("/api/focus/status")
    def focus_status():
        if not motores:
            return {"error": "Sin motores de enfoque", "motores": {}}
        estados = {}
        for num, m in motores.items():
            info = m.estado_completo()
            if autofocus is not None:
                info["calibracion_dpc"] = autofocus.calibracion.get(num)
            estados[str(num)] = info
        ia = getattr(autofocus, "ia", None) if autofocus else None
        return {"motores": estados, "autofocus": autofocus is not None,
                "ia": ia.estado() if ia is not None else None}

    # ===============================
    # Autofoco (barrido de nitidez, ver core/autofocus.py)
    # ===============================
    @app.post("/api/focus/auto")
    def focus_auto(req: AutofocusReq):
        """Enfoca una camara. Tarda del orden de 15-30 s: es sincrono a
        proposito (FastAPI corre los endpoints sync en su threadpool,
        asi que no bloquea al resto del servidor) y devuelve la curva de
        nitidez completa para poder ver el barrido en la interfaz."""
        if autofocus is None:
            return {"error": "Autofoco no disponible (sin motores)"}
        if timelapse.is_running():
            return {"error": "Timelapse en curso"}
        if not autofocus.disponible(req.camera):
            return {"error": f"cam{req.camera} no tiene motor de enfoque"}
        if not autofocus_lock.acquire(blocking=False):
            return {"error": "Ya hay un autofoco corriendo"}
        try:
            # rango_um se convierte DENTRO de enfocar_auto, ya en la
            # resolucion interna fija (no en la que dejo el usuario).
            opciones = dict(
                metodo=req.metodo, rango=req.rango, rango_um=req.rango_um,
                rango_max_um=req.rango_max_um, puntos=req.puntos,
                refinamientos=req.refinamientos, iteraciones=req.iteraciones,
                usar_luz=req.usar_luz, patron=req.patron)
            # Solo se manda si el pedido lo trae explicito: cada metodo
            # (enfocar_dpc / enfocar de respaldo) tiene su propio default
            # sensato, y no queremos pisarlo con el mismo numero para los
            # dos casos.
            if req.repeticiones is not None:
                opciones["repeticiones"] = req.repeticiones
            return autofocus.enfocar_auto(req.camera, **opciones)
        except Exception as e:
            return {"error": str(e)}
        finally:
            autofocus_lock.release()

    @app.post("/api/focus/calibrar")
    def focus_calibrar(req: CalibrarDpcReq):
        """Calibra el autofoco DPC de una camara: cuantos micropasos de
        desenfoque equivalen a un pixel de corrimiento entre las dos
        medias iluminaciones. Se hace una vez por objetivo y queda
        guardado en profiles/autofoco_dpc.json.

        De paso deja la camara enfocada: el foco es donde la recta
        corrimiento-vs-posicion cruza el cero."""
        if autofocus is None:
            return {"error": "Autofoco no disponible (sin motores)"}
        if timelapse.is_running():
            return {"error": "Timelapse en curso"}
        if not autofocus.disponible(req.camera):
            return {"error": f"cam{req.camera} no tiene motor de enfoque"}
        if not autofocus_lock.acquire(blocking=False):
            return {"error": "Ya hay un autofoco corriendo"}
        try:
            return autofocus.calibrar_dpc(
                req.camera, amplitud=req.amplitud, amplitud_um=req.amplitud_um,
                enfocar_antes=req.enfocar_antes, puntos=req.puntos,
                eje=req.eje, repeticiones=req.repeticiones)
        except Exception as e:
            return {"error": str(e)}
        finally:
            autofocus_lock.release()

    @app.post("/api/focus/pila")
    def focus_pila(req: PilaFocoReq):
        """Graba una pila de foco para entrenar el autofoco IA.

        Requiere que la camara YA este enfocada: la posicion actual se
        toma como el cero de las etiquetas. Tarda del orden de un minuto
        (dos capturas por plano) y deja el eje donde estaba.
        """
        if autofocus is None:
            return {"error": "Sin motores de enfoque"}
        if timelapse.is_running():
            return {"error": "Timelapse en curso"}
        if not autofocus.disponible(req.camera):
            return {"error": f"cam{req.camera} no tiene motor de enfoque"}
        if not autofocus_lock.acquire(blocking=False):
            return {"error": "Ya hay un autofoco corriendo"}
        try:
            from core.pila_foco import grabar_pila
            with autofocus.motores[req.camera].resolucion():
                return grabar_pila(autofocus, req.camera, rango=req.rango,
                                   puntos=req.puntos, eje=req.eje,
                                   notas=req.notas)
        except Exception as e:
            return {"error": str(e)}
        finally:
            autofocus_lock.release()

    # ===============================
    # Conteo de celulas (ver core/analisis.py)
    # ===============================
    @app.get("/api/analisis/estado")
    def analisis_estado():
        if conteo is None:
            return {"error": "Conteo no disponible", "activas": []}
        return conteo.estado()

    @app.post("/api/analisis/config")
    def analisis_config(req: ConteoConfigReq):
        """Enciende/apaga el conteo en vivo de una camara y ajusta las
        perillas. Solo toca lo que viene en el pedido."""
        if conteo is None:
            return {"error": "Conteo no disponible"}
        if req.camara is not None and req.activo is not None:
            conteo.activar(req.camara, req.activo)
        if req.modo is not None:
            try:
                conteo.set_modo(req.modo)
            except ValueError as e:
                return {"error": str(e)}
        if req.periodo is not None:
            conteo.periodo = max(0.1, float(req.periodo))
        conteo.contador.configurar(
            diametro_px=req.diametro_px, umbral=req.umbral,
            separar=req.separar)
        return conteo.estado()

    @app.post("/api/analisis/medir")
    def analisis_medir(req: ConteoMedirReq):
        """Medicion puntual: media apertura un instante, mide, restaura.

        Es el modo manual, y es el que tiene sentido por defecto: las
        celulas no cambian en segundos, asi que no hace falta medir dos
        veces por segundo, y sobre todo no hace falta dejar la matriz en
        oblicua todo el rato. Se mide, se devuelve la luz a como estaba,
        y el numero queda dibujado sobre el vivo.
        """
        if conteo is None:
            return {"error": "Conteo no disponible"}
        if timelapse.is_running():
            return {"error": "Timelapse en curso"}
        if req.patron is not None and req.patron not in _METODOS_LUZ:
            return {"error": f"Patron invalido: {req.patron}"}

        previo = estado["luz"].get(req.camera)
        try:
            if req.patron is not None:
                _luz_aplicar(req.camera, req.patron,
                             (previo or {}).get("percent"))
                time.sleep(req.settle)
            # descartar=2: la camara viene corriendo en continuo, asi que
            # el primer frame que devuelve puede haberse EXPUESTO antes
            # del cambio de iluminacion. Contarlo seria contar sobre la
            # luz anterior (campo claro), justo lo que se quiere evitar.
            frame = camera.get_focus_frame(req.camera, descartar=2)
            resultado = conteo.medir(req.camera, frame)
        except Exception as e:
            return {"error": str(e)}
        finally:
            if req.patron is not None:
                try:
                    _luz_restaurar(req.camera, previo)
                except Exception:
                    pass

        return {k: v for k, v in resultado.items()
                if k not in ("contornos", "objetos", "imagen")} | {
            "camara": req.camera,
            "luz_restaurada": (previo or {}).get("modo", "off"),
        }

    @app.post("/api/analisis/foto")
    def analisis_foto(req: ConteoFotoReq):
        """Captura a resolucion nativa y cuenta sobre esa captura.

        Es el camino de precision, no el del vivo: usa el par L/R
        completo (imagen DPC de verdad, no el frame de preview del ISP)
        y guarda un PNG con las celulas marcadas al lado del TIFF.
        """
        if conteo is None:
            return {"error": "Conteo no disponible"}
        if timelapse.is_running():
            return {"error": "Timelapse en curso"}
        from datetime import datetime
        from core import analisis

        folder = "capturas_unicas"
        os.makedirs(folder, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        patrones = MODOS.get(req.modo, MODOS["dpc"])
        luz = illuminations.get(req.camera)

        rutas = {}
        try:
            for sufijo, metodo in patrones:
                if luz:
                    getattr(luz, metodo)()
                    time.sleep(0.3)
                fn = f"{folder}/cam{req.camera}_{ts}{sufijo}.tif"
                camera.capture_image(camera_num=req.camera, folder=folder,
                                     filename=fn)
                rutas[sufijo] = fn
        finally:
            if luz:
                luz.off()

        try:
            resultado, imagen = analisis.analizar_capturas(
                rutas, conteo.contador, ancho_max=req.ancho_max)
        except Exception as e:
            return {"error": str(e), "capturas": list(rutas.values())}

        salida = None
        if req.guardar_overlay:
            salida = analisis.guardar_overlay(
                f"{folder}/cam{req.camera}_{ts}_conteo.png", imagen, resultado)

        return {k: v for k, v in resultado.items()
                if k not in ("contornos", "objetos", "imagen")} | {
            "capturas": [os.path.basename(v) for v in rutas.values()],
            "overlay": os.path.basename(salida) if salida else None,
        }

    @app.post("/api/analisis/timelapse")
    def analisis_timelapse(req: ConteoCarpetaReq):
        """Regenera curva de poblacion y eventos de un experimento ya
        terminado, a partir de su conteo.csv (no relee las imagenes)."""
        from core import analisis
        if not os.path.isdir(req.carpeta):
            return {"error": f"no existe la carpeta {req.carpeta}"}
        try:
            return analisis.resumir_timelapse(req.carpeta, req.intervalo_s)
        except Exception as e:
            return {"error": str(e)}

    # ===============================
    # Memorias USB (core/usb.py)
    # ===============================
    @app.get("/api/usb/estado")
    def usb_estado():
        if usb is None:
            return {"dispositivos": [], "evento": 0, "error": "monitor USB no disponible"}
        return usb.estado()

    @app.post("/api/usb/expulsar")
    def usb_expulsar(req: UsbAccionReq):
        if usb is None:
            return {"error": "monitor USB no disponible"}
        if timelapse.is_running() and str(getattr(timelapse, "base_folder", "")).startswith(req.punto):
            return {"error": "Hay un timelapse guardando en esa memoria: detenlo antes de expulsar"}
        return usb.expulsar(req.punto)

    @app.post("/api/usb/copiar")
    def usb_copiar(req: UsbCopiarReq):
        if usb is None:
            return {"error": "monitor USB no disponible"}
        origen = experimentos.resolver(req.carpeta)
        if origen is None:
            return {"error": f"No existe {req.carpeta}"}
        if timelapse.is_running() and Path(str(timelapse.base_folder)).resolve() == origen:
            return {"error": "Ese timelapse todavia esta corriendo"}
        return usb.copiar(origen, req.punto)

    # ===============================
    # Envio a la PC de segmentacion (core/envio.py)
    # ===============================
    @app.get("/api/envio/estado")
    def envio_estado():
        if enviador is None:
            return {"error": "envio no disponible"}
        return enviador.estado()

    @app.post("/api/envio/config")
    def envio_config(req: EnvioConfigReq):
        if enviador is None:
            return {"error": "envio no disponible"}
        return enviador.configurar(url=req.url, token=req.token, activo=req.activo,
                                   nombre_pc=req.nombre_pc)

    @app.post("/api/envio/buscar")
    def envio_buscar():
        """PCs con el receptor activo en la red local (UDP broadcast)."""
        from core.envio import buscar_pcs
        try:
            return {"pcs": buscar_pcs()}
        except OSError as e:
            return {"pcs": [], "error": str(e)}

    @app.post("/api/envio/probar")
    def envio_probar():
        if enviador is None:
            return {"ok": False, "error": "envio no disponible"}
        return enviador.probar()

    @app.post("/api/envio/reenviar")
    def envio_reenviar(req: EnvioReenviarReq):
        """Completa en la PC lo que falto (corte de red, reinicio). Lo
        que la PC ya tiene con el mismo hash no se vuelve a mandar."""
        if enviador is None:
            return {"error": "envio no disponible"}
        carpeta = experimentos.resolver(req.carpeta)
        if carpeta is None:
            carpeta = Path(req.carpeta).resolve()
            puntos = [Path(d["punto"]).resolve() for d in usb.estado()["dispositivos"]
                      if d.get("punto")] if usb is not None else []
            en_usb = any(carpeta.parent in (p, p / "MicroscopeOS") for p in puntos)
            if not (en_usb and (ID_RE.match(carpeta.name) or LEGADO_RE.match(carpeta.name))):
                return {"error": "Carpeta invalida"}
        if not carpeta.is_dir():
            return {"error": f"No existe {req.carpeta}"}
        return enviador.reenviar_carpeta(carpeta)

    # ===============================
    # Respaldo en NAS (core/respaldo_nas.py)
    # ===============================
    @app.get("/api/nas/estado")
    def nas_estado():
        if respaldo_nas is None:
            return {"error": "respaldo en NAS no disponible"}
        return respaldo_nas.estado()

    @app.post("/api/nas/config")
    def nas_config(req: NasConfigReq):
        if respaldo_nas is None:
            return {"error": "respaldo en NAS no disponible"}
        return respaldo_nas.configurar(**req.model_dump())

    @app.post("/api/nas/buscar")
    def nas_buscar():
        """Equipos de la red con carpetas compartidas (SMB)."""
        from core.respaldo_nas import buscar_nas
        try:
            return {"equipos": buscar_nas()}
        except Exception as e:
            return {"equipos": [], "error": str(e)}

    @app.post("/api/nas/probar")
    def nas_probar():
        if respaldo_nas is None:
            return {"ok": False, "error": "respaldo en NAS no disponible"}
        return respaldo_nas.probar()

    @app.post("/api/nas/reenviar")
    def nas_reenviar(req: EnvioReenviarReq):
        """Respalda un timelapse completo; lo que ya esta en el NAS se saltea."""
        if respaldo_nas is None:
            return {"error": "respaldo en NAS no disponible"}
        if not _FOLDER_RE.match(req.carpeta):
            return {"error": "Carpeta invalida"}
        carpeta = (BASE_DIR / req.carpeta).resolve()
        if carpeta.parent != BASE_DIR.resolve() or not carpeta.is_dir():
            return {"error": f"No existe {req.carpeta}"}
        return respaldo_nas.reenviar_carpeta(carpeta)

    # ===============================
    # Galeria / descarga (solo lectura sobre lo ya guardado en disco)
    # ===============================
    @app.get("/files/list")
    def files_list():
        capturas_dir = BASE_DIR / "capturas_unicas"
        capturas = []
        if capturas_dir.is_dir():
            archivos = sorted(capturas_dir.glob("*.tif"),
                               key=lambda p: p.stat().st_mtime, reverse=True)
            capturas = [{"filename": p.name, "mtime": p.stat().st_mtime,
                         "size": p.stat().st_size} for p in archivos[:60]]

        timelapses = []
        for folder in sorted(BASE_DIR.glob("timelapse_*"),
                              key=lambda p: p.stat().st_mtime, reverse=True):
            if not folder.is_dir() or not _FOLDER_RE.match(folder.name):
                continue
            cams, total_bytes, total_files = {}, 0, 0
            for camdir in sorted(folder.glob("cam*")):
                if not camdir.is_dir():
                    continue
                arch = sorted(camdir.glob("*.tif"),
                              key=lambda p: p.stat().st_mtime, reverse=True)
                total_files += len(arch)
                total_bytes += sum(p.stat().st_size for p in arch)
                cams[camdir.name.replace("cam", "")] = [p.name for p in arch[:12]]
            timelapses.append({"folder": folder.name, "mtime": folder.stat().st_mtime,
                                "cams": cams, "n_files": total_files,
                                "size_bytes": total_bytes})
        return {"capturas": capturas, "timelapses": timelapses}

    @app.get("/files/thumb/capturas/{filename}")
    def thumb_captura(filename: str, size: int = 320):
        p = _ruta_captura(filename)
        if p is None:
            return Response(status_code=404)
        return Response(content=_thumb_jpeg(p, size), media_type="image/jpeg")

    @app.get("/files/thumb/timelapse/{folder}/{cam}/{filename}")
    def thumb_timelapse(folder: str, cam: int, filename: str, size: int = 320):
        p = _ruta_timelapse(folder, cam, filename)
        if p is None:
            return Response(status_code=404)
        return Response(content=_thumb_jpeg(p, size), media_type="image/jpeg")

    @app.get("/files/raw/capturas/{filename}")
    def raw_captura(filename: str):
        p = _ruta_captura(filename)
        if p is None:
            return Response(status_code=404)
        return FileResponse(str(p), media_type="image/tiff", filename=filename)

    @app.get("/files/raw/timelapse/{folder}/{cam}/{filename}")
    def raw_timelapse(folder: str, cam: int, filename: str):
        p = _ruta_timelapse(folder, cam, filename)
        if p is None:
            return Response(status_code=404)
        return FileResponse(str(p), media_type="image/tiff", filename=filename)

    @app.get("/files/zip/capturas")
    def zip_capturas():
        folder = BASE_DIR / "capturas_unicas"
        if not folder.is_dir():
            return Response(status_code=404)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(folder.glob("*.tif")):
                zf.write(p, arcname=p.name)
        return Response(content=buf.getvalue(), media_type="application/zip",
                         headers={"Content-Disposition": 'attachment; filename="capturas_unicas.zip"'})

    @app.get("/files/zip/timelapse/{folder}")
    def zip_timelapse(folder: str):
        if not _FOLDER_RE.match(folder):
            return Response(status_code=400)
        base = (BASE_DIR / folder).resolve()
        if base.parent != BASE_DIR.resolve() or not base.is_dir():
            return Response(status_code=404)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(base.rglob("*")):
                if p.is_file():
                    zf.write(p, arcname=str(p.relative_to(base)))
        return Response(content=buf.getvalue(), media_type="application/zip",
                         headers={"Content-Disposition": f'attachment; filename="{folder}.zip"'})

    # ===============================
    # Perfiles (exposicion/ganancia + defaults de timelapse). Pensado
    # para un microscopio compartido: cada persona guarda su propia
    # configuracion y la vuelve a cargar sin pisar la del resto.
    # ===============================
    @app.get("/profiles/list")
    def profiles_list():
        return {"perfiles": profile_manager.list_profiles()}

    @app.post("/profiles/load/{name}")
    def profiles_load(name: str):
        if not _PERFIL_RE.match(name):
            return {"error": "Nombre de perfil inválido"}
        if name not in profile_manager.list_profiles():
            return {"error": f"No existe el perfil '{name}'"}
        config = profile_manager.load_profile(name)
        camera.set_exposure(config.camera.exposure_us, config.camera.gain)
        return {"status": "ok",
                "camera": {"exposure_us": config.camera.exposure_us,
                           "gain": config.camera.gain},
                "timelapse": {"interval_seconds": config.timelapse.interval_seconds,
                              "duration_seconds": config.timelapse.duration_seconds}}

    @app.post("/profiles/save")
    def profiles_save(req: ProfileSaveReq):
        if not _PERFIL_RE.match(req.name):
            return {"error": "Nombre de perfil inválido"}
        config = SystemConfig()
        config.camera = CameraSettings(exposure_us=camera.exposure_time, gain=camera.gain)
        config.timelapse = TimelapseSettings(interval_seconds=req.interval_seconds,
                                              duration_seconds=req.duration_seconds)
        profile_manager.save_profile(req.name, config)
        return {"status": "ok"}

    @app.post("/profiles/delete/{name}")
    def profiles_delete(name: str):
        if not _PERFIL_RE.match(name):
            return {"error": "Nombre de perfil inválido"}
        if name == "default":
            return {"error": "No se puede borrar el perfil 'default'"}
        profile_manager.delete_profile(name)
        return {"status": "ok"}

    # ===============================
    # Interfaz
    # ===============================
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # ===============================
    # Experimentos: galeria, descargas, renombrar, papelera
    # (core/experimentos.py)
    # ===============================
    from starlette.background import BackgroundTask
    import tempfile
    import hashlib
    import base64

    MINIATURAS = experimentos.raiz / ".miniaturas"

    def _en_curso():
        if timelapse.is_running() and getattr(timelapse, "base_folder", None):
            return Path(timelapse.base_folder).resolve()
        return None

    def _exp(ident):
        c = experimentos.resolver(ident)
        if c is None:
            raise ValueError("Ese experimento ya no existe")
        return c

    def _miniatura(p, size):
        """Miniatura JPEG con cache en disco: decodificar un TIFF de 16 MP
        en la Pi tarda; la galeria pide muchas."""
        st = p.stat()
        clave = hashlib.sha1(f"{p}|{st.st_mtime_ns}|{st.st_size}|{size}".encode()).hexdigest()
        cache = MINIATURAS / f"{clave}.jpg"
        if cache.is_file():
            return cache.read_bytes()
        datos = _thumb_jpeg(p, size)
        try:
            MINIATURAS.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(datos)
        except OSError:
            pass
        return datos

    # ===============================
    # Actualizar el programa desde GitHub (ver core/actualizar.py)
    # ===============================
    @app.get("/api/version")
    def version():
        return actualizador.version()

    @app.get("/api/actualizacion")
    def actualizacion(forzar: bool = False):
        return actualizador.revisar(forzar=forzar)

    def _apagar_hardware():
        for luz in illuminations.values():
            if luz is not None:
                luz.off()
        for m in motores.values():
            if m is not None:
                try:
                    m.close()
                except Exception:
                    pass
        camera.stop()

    @app.post("/api/actualizar")
    def actualizar():
        if timelapse.is_running():
            return {"error": "Hay un timelapse en curso. Actualiza cuando termine."}
        if autofocus_lock.locked():
            return {"error": "Autofoco en curso, espera a que termine"}
        try:
            r = actualizador.actualizar()
        except ErrorActualizar as e:
            return {"error": str(e)}
        if r.get("actualizado"):
            reiniciar(_apagar_hardware)
        return r

    @app.get("/api/experimentos")
    def exp_listar():
        experimentos.vaciar_papelera()
        en_curso = _en_curso()
        lista = experimentos.listar()
        for e in lista:
            e["en_curso"] = bool(en_curso and en_curso.name == e["id"])
        dispositivos = (usb.estado().get("dispositivos", []) if usb is not None else [])
        return {"experimentos": lista, "espacio": experimentos.espacio(),
                "usb": [{"punto": d["punto"], "etiqueta": d.get("etiqueta"),
                         "libre_bytes": d.get("libre_bytes")}
                        for d in dispositivos if d.get("montado") and d.get("escribible")]}

    @app.get("/api/exp/{ident}")
    def exp_detalle(ident: str):
        try:
            c = _exp(ident)
        except ValueError as e:
            return {"error": str(e)}
        info = experimentos.info(c)
        info["imagenes"] = experimentos.imagenes(c)
        info["en_curso"] = c == _en_curso()
        return info

    @app.get("/api/exp/{ident}/mini/{rel:path}")
    def exp_mini(ident: str, rel: str, size: int = 320):
        p = experimentos.ruta_imagen(ident, rel)
        if p is None:
            return Response(status_code=404)
        return Response(content=_miniatura(p, max(64, min(size, 1600))), media_type="image/jpeg",
                        headers={"Cache-Control": "max-age=86400"})

    @app.get("/api/exp/{ident}/original/{rel:path}")
    def exp_original(ident: str, rel: str):
        p = experimentos.ruta_imagen(ident, rel)
        if p is None:
            return Response(status_code=404)
        nombre = f"{ident}_{rel.replace('/', '_')}"
        return FileResponse(str(p), media_type="image/tiff", filename=nombre)

    @app.get("/api/exp/{ident}/info/{rel:path}")
    def exp_info_foto(ident: str, rel: str):
        p = experimentos.ruta_imagen(ident, rel)
        if p is None:
            return {"error": "no existe"}
        return {"metadatos": metadatos_mod.leer(p), "bytes": p.stat().st_size}

    def _compartir_bytes(p, formato="jpg", ancho_max=None):
        img = tifffile.imread(str(p))
        meta = metadatos_mod.leer(p)
        return marca_agua.exportar(img, meta, marca_agua.cargar(), formato, ancho_max)

    @app.get("/api/exp/{ident}/compartir/{rel:path}")
    def exp_compartir(ident: str, rel: str, formato: str = "jpg"):
        """Copia 8 bits con la marca de agua configurada (el original no
        se toca)."""
        p = experimentos.ruta_imagen(ident, rel)
        if p is None:
            return Response(status_code=404)
        formato = "png" if formato == "png" else "jpg"
        try:
            datos = _compartir_bytes(p, formato)
        except Exception as e:
            return Response(content=str(e), status_code=500)
        nombre = f"{ident}_{Path(rel).stem}.{formato}"
        return Response(content=datos, media_type="image/png" if formato == "png" else "image/jpeg",
                        headers={"Content-Disposition": f'attachment; filename="{nombre}"'})

    @app.get("/api/exp/{ident}/zip")
    def exp_zip(ident: str, tipo: str = "originales"):
        """Todo el experimento en un .zip, armado en un archivo temporal
        (en memoria, uno grande se comia la RAM). tipo=compartir: JPG con
        marca de agua en vez de los .tif."""
        try:
            c = _exp(ident)
        except ValueError:
            return Response(status_code=404)
        fd, tmp = tempfile.mkstemp(suffix=".zip", dir=str(experimentos.raiz))
        os.close(fd)
        try:
            if tipo == "compartir":
                with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
                    for rel in experimentos.imagenes(c):
                        zf.writestr(f"{ident}/{Path(rel).with_suffix('.jpg')}",
                                    _compartir_bytes(c / rel, "jpg"))
                nombre = f"{ident}_para_compartir.zip"
            else:
                experimentos.zip(ident, tmp)
                nombre = f"{ident}.zip"
        except Exception as e:
            os.unlink(tmp)
            return Response(content=str(e), status_code=500)
        return FileResponse(tmp, media_type="application/zip", filename=nombre,
                            background=BackgroundTask(os.unlink, tmp))

    @app.post("/api/exp/{ident}/renombrar")
    def exp_renombrar(ident: str, req: NombreReq):
        try:
            c = _exp(ident)
            if c == _en_curso():
                return {"error": "No se puede renombrar mientras el timelapse está corriendo"}
            return {"id": experimentos.renombrar(ident, req.nombre)}
        except (ValueError, OSError) as e:
            return {"error": str(e)}

    @app.post("/api/exp/{ident}/borrar")
    def exp_borrar(ident: str):
        try:
            c = _exp(ident)
            if c == _en_curso():
                return {"error": "No se puede borrar mientras el timelapse está corriendo"}
            return {"codigo": experimentos.borrar(ident)}
        except (ValueError, OSError) as e:
            return {"error": str(e)}

    @app.post("/api/papelera/restaurar")
    def papelera_restaurar(req: CodigoReq):
        try:
            return {"id": experimentos.restaurar(req.codigo)}
        except (ValueError, OSError) as e:
            return {"error": str(e)}

    @app.post("/api/exp/{ident}/usb")
    def exp_a_usb(ident: str, req: UsbPuntoReq):
        if usb is None:
            return {"error": "No hay memorias USB disponibles"}
        try:
            c = _exp(ident)
        except ValueError as e:
            return {"error": str(e)}
        if c == _en_curso():
            return {"error": "Espera a que termine el timelapse para copiarlo"}
        return usb.copiar(c, req.punto, subcarpeta="MicroscopeOS")

    # ===============================
    # Optica: objetivo y escala de cada camara (core/optica.py)
    # ===============================
    @app.get("/api/optica")
    def optica_get():
        return {"camaras": optica.todas(), "objetivos": OBJETIVOS}

    @app.post("/api/optica")
    def optica_set(req: OpticaReq):
        cambios = {k: v for k, v in req.model_dump().items()
                   if k not in ("camara", "borrar_medido") and v is not None}
        if req.borrar_medido:
            cambios["um_por_pixel_medido"] = None
        try:
            return {"camara": optica.cambiar(req.camara, cambios)}
        except ValueError as e:
            return {"error": str(e)}

    # ===============================
    # Marca de agua de las copias para compartir (core/marca_agua.py)
    # ===============================
    def _marca_estado():
        cfg = marca_agua.cargar()
        return {"config": cfg, "preset": marca_agua.preset_de(cfg),
                "presets": list(marca_agua.PRESETS), "campos": marca_agua.CAMPOS,
                "logo_propio": marca_agua.LOGO.is_file(),
                "disponible": marca_agua.Image is not None}

    @app.get("/api/marca")
    def marca_get():
        return _marca_estado()

    @app.post("/api/marca")
    def marca_set(req: MarcaReq):
        cfg = marca_agua.cargar()
        try:
            if req.preset:
                cfg = marca_agua.con_preset(cfg, req.preset)
            if req.config:
                c = dict(cfg)
                c.update({k: v for k, v in req.config.items() if k != "campos"})
                if "campos" in req.config:
                    c["campos"] = dict(cfg["campos"], **req.config["campos"])
                cfg = c
            marca_agua.guardar(cfg)
        except ValueError as e:
            return {"error": str(e)}
        return _marca_estado()

    @app.get("/api/marca/vista")
    def marca_vista(ancho: int = 900, t: str = ""):
        """Vista previa con la ultima foto guardada (o una de ejemplo)."""
        ultima = None
        for e in experimentos.listar():
            if e.get("portada"):
                ultima = experimentos.ruta_imagen(e["id"], e["portada"])
                if ultima:
                    break
        try:
            if ultima is not None:
                img, meta = tifffile.imread(str(ultima)), metadatos_mod.leer(ultima)
            else:
                img = marca_agua.muestra_sintetica()
                meta = {"experimento": {"nombre": "Ejemplo"},
                        "fecha_hora": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "optica": optica.de(0), "camara": {"numero": 0},
                        "iluminacion": {"nombre": "Normal (campo claro)"}}
            if not meta.get("optica"):
                # foto de una version anterior, sin metadatos: se usa la
                # optica actual de la camara 0 para poder previsualizar
                meta["optica"] = optica.de(0)
            datos = marca_agua.exportar(img, meta, marca_agua.cargar(), "jpg",
                                        max(300, min(ancho, 1600)))
        except Exception as e:
            return Response(content=str(e), status_code=500)
        return Response(content=datos, media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})

    @app.post("/api/marca/logo")
    def marca_logo(req: LogoReq):
        """Logo propio: PNG en base64 (lo manda la pagina al elegir un archivo)."""
        try:
            datos = base64.b64decode(req.png_base64.split(",", 1)[-1], validate=True)
            if len(datos) > 5_000_000:
                return {"error": "El logo pesa demasiado (máximo 5 MB)"}
            from PIL import Image as _I
            im = _I.open(io.BytesIO(datos)).convert("RGBA")
            im.thumbnail((1200, 1200))
            marca_agua.LOGO.parent.mkdir(parents=True, exist_ok=True)
            im.save(marca_agua.LOGO, "PNG")
        except Exception as e:
            return {"error": f"No es una imagen válida ({e})"}
        return _marca_estado()

    @app.post("/api/marca/logo/quitar")
    def marca_logo_quitar():
        try:
            marca_agua.LOGO.unlink()
        except FileNotFoundError:
            pass
        return _marca_estado()

    # La pagina nueva es la principal: la anterior ya no muestra las
    # fotos guardadas en datos/. Queda en /clasica por si hace falta.
    @app.get("/clasica", response_class=HTMLResponse)
    def index_clasica():
        with open(STATIC_DIR / "index.html", "r") as f:
            return f.read()

    @app.get("/", response_class=HTMLResponse)
    @app.get("/ui", response_class=HTMLResponse)
    def index_uiux():
        with open(STATIC_DIR / "index_uiux.html", "r") as f:
            return f.read()

    return app
