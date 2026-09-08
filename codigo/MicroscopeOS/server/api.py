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
from core.autofocus import micropasos_por_um
from core.profile_manager import ProfileManager
from core.config import SystemConfig, CameraSettings, TimelapseSettings

# ===============================
# Galeria de archivos: solo lectura, con nombres validados por regex
# para no aceptar un path arbitrario del cliente (ver _ruta_captura /
# _ruta_timelapse mas abajo).
# ===============================
_FOLDER_RE = re.compile(r'^timelapse_\d{8}_\d{6}$')
_FILE_RE = re.compile(r'^[A-Za-z0-9_.\-]+\.tif$')
_PERFIL_RE = re.compile(r'^[A-Za-z0-9 _\-]{1,40}$')


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
    autofocus_rango: int = 1600
    autofocus_puntos: int = 11
    # Contar celulas en las capturas de cada ciclo -> conteo.csv,
    # poblacion.png y eventos.csv al terminar.
    contar: bool = False
    contar_cada: int = 1
    contar_overlay: bool = True

class SetpointPayload(BaseModel):
    value: float

# Hay un motor de enfoque por camara: "motor" es el numero de camara
# (ver PINES_POR_CAMARA en core/motor_focus.py).
class FocusMoveReq(BaseModel):
    motor: int = 0
    direction: int = 1        # 1 = abajo, -1 = arriba (ver core/motor_focus.py)
    pasos: int = 200          # en la resolucion de microstepping actual
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

class CalibrarDpcReq(BaseModel):
    camera: int = 0
    amplitud: int = 1200      # recorrido barrido para ajustar la recta
    puntos: int = 5
    eje: str = "lr"           # lr (izquierda/derecha) o tb (arriba/abajo)
    repeticiones: int = 2     # mediciones por punto de calibracion, por mediana


def create_app(camera, illuminations, timelapse, motores=None,
               autofocus=None, motor=None, conteo=None):
    """motores: {numero_de_camara: FocusMotorController}. `motor` se
    acepta todavia como un solo eje suelto (compatibilidad con la
    version de un motor) y se mapea a la camara 0.

    conteo: core.analisis.ContadorEnVivo, o None para arrancar sin
    conteo de celulas."""

    app = FastAPI()
    if motores is None:
        motores = {0: motor} if motor is not None else {}
    # "luz": ultimo modo aplicado a cada matriz, para poder restaurarlo
    # despues de una medicion puntual de celulas (ver _luz_aplicar).
    estado = {"camara_activa": 0, "luz": {}}
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
        if percent is not None:
            luz.set_brightness(percent)
        if modo == "off":
            luz.off()
        elif modo == "rheinberg":
            luz.rheinberg(color_centro, color_anillo)
        else:
            getattr(luz, _METODOS_LUZ[modo])()
        estado["luz"][cam] = {"modo": modo, "percent": percent,
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
        return {"status": "ok", "modo": req.modo}

    @app.post("/light/on")
    def light_on():
        if timelapse.is_running():
            return {"error": "Timelapse en curso"}
        _luz_aplicar(estado["camara_activa"], "full")
        return {"status": "on"}

    @app.post("/light/off")
    def light_off():
        for cam in illuminations:
            _luz_aplicar(cam, "off")
        return {"status": "off"}

    # ===============================
    # Captura
    # ===============================
    # /capture/both/{modo} DEBE declararse antes que /capture/{camera_num}/
    # {modo}: FastAPI prueba las rutas en orden de declaracion, y con el
    # orden invertido "both" se intentaba parsear como camera_num:int y
    # tiraba 422 antes de llegar siquiera a esta ruta.
    @app.post("/capture/both/{modo}")
    def capture_both(modo: str):
        """Captura las dos camaras en paralelo (Pi 5, sin mux).

        Enciende AMBAS matrices a la vez. Ver TODO_HW.md (prioridad 2)
        antes de usarlo con datos que importen.
        """
        if timelapse.is_running():
            return {"error": "Timelapse en curso"}
        from datetime import datetime
        folder = "capturas_unicas"
        os.makedirs(folder, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        patrones = MODOS.get(modo, MODOS["blanco"])
        cams = sorted(illuminations.keys())
        guardados = []
        for sufijo, metodo in patrones:
            for cam in cams:
                luz = illuminations.get(cam)
                if luz:
                    getattr(luz, metodo)()
            time.sleep(0.3)
            res = camera.capture_both(
                folder=folder,
                filenames={c: f"{folder}/cam{c}_{ts}{sufijo}.tif" for c in cams},
                camera_nums=cams)
            guardados += [os.path.basename(v) for v in res.values()]
            for cam in cams:
                luz = illuminations.get(cam)
                if luz:
                    luz.off()
        return {"saved": guardados}

    @app.post("/capture/{camera_num}/{modo}")
    def capture(camera_num: int, modo: str):
        if timelapse.is_running():
            return {"error": "Timelapse en curso"}
        from datetime import datetime
        folder = "capturas_unicas"
        os.makedirs(folder, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        guardados = []
        patrones = MODOS.get(modo, MODOS["blanco"])
        luz = illuminations.get(camera_num)
        for sufijo, metodo in patrones:
            if luz:
                getattr(luz, metodo)()
                time.sleep(0.3)
            fn = f"{folder}/cam{camera_num}_{ts}{sufijo}.tif"
            camera.capture_image(camera_num=camera_num, folder=folder, filename=fn)
            guardados.append(os.path.basename(fn))
        if luz:
            luz.off()
        return {"saved": guardados}

    # ===============================
    # Exposicion / Brillo
    # ===============================
    @app.post("/exposure")
    def set_exposure(req: ExposureReq):
        camera.set_exposure(req.exposure, req.gain)
        return {"status": "ok"}

    @app.post("/brightness")
    def set_brightness(req: BrightnessReq):
        for luz in illuminations.values():
            luz.set_brightness(req.percent)
        return {"status": "ok"}

    # ===============================
    # Timelapse
    # ===============================
    @app.post("/timelapse/start")
    def start_timelapse(req: TimelapseReq):
        if timelapse.is_running():
            return {"error": "Ya hay un timelapse corriendo"}
        timelapse.start(
            modo=req.modo,
            interval_seconds=req.interval,
            duration_seconds=req.duration,
            stabilization_time=req.stabilization,
            camaras=req.camaras,
            simultaneo=req.simultaneo,
            autofocus=req.autofocus,
            autofocus_cada=req.autofocus_cada,
            autofocus_opts={"rango": req.autofocus_rango,
                            "puntos": req.autofocus_puntos},
            contar=req.contar,
            contar_cada=req.contar_cada,
            contar_opts={"overlay": req.contar_overlay},
        )
        return {"status": "started", "autofocus": req.autofocus,
                "contar": req.contar}

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

    @app.post("/api/focus/move")
    def focus_move(req: FocusMoveReq):
        """Salto puntual de N micropasos (los botones de paso fijo)."""
        if timelapse.is_running():
            return {"error": "Timelapse en curso"}
        motor_ = _motor(req.motor)
        if motor_ is None:
            return {"error": f"cam{req.motor} no tiene motor de enfoque"}
        motor_.stop_jog()
        pos = motor_.mover(req.pasos, direction=req.direction,
                           delay=req.velocidad)
        return {"status": "ok", "motor": req.motor, "posicion": pos}

    @app.post("/api/focus/jog")
    def focus_jog(req: FocusJogReq):
        """Arranca (o mantiene vivo) el movimiento continuo."""
        if timelapse.is_running():
            return {"error": "Timelapse en curso"}
        motor_ = _motor(req.motor)
        if motor_ is None:
            return {"error": f"cam{req.motor} no tiene motor de enfoque"}
        motor_.start_jog(direction=req.direction, delay=req.velocidad,
                         watchdog=req.watchdog)
        return {"status": "jog", "motor": req.motor,
                "posicion": motor_.position}

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
            rango = req.rango
            if req.rango_um is not None:
                motor_ref = autofocus.motores.get(req.camera)
                if motor_ref is None:
                    return {"error": f"cam{req.camera} no tiene motor de enfoque"}
                rango = micropasos_por_um(req.rango_um, motor_ref.microsteps)
            opciones = dict(
                metodo=req.metodo, rango=rango, puntos=req.puntos,
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
                req.camera, amplitud=req.amplitud, puntos=req.puntos,
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

    @app.get("/", response_class=HTMLResponse)
    def index():
        with open(STATIC_DIR / "index.html", "r") as f:
            return f.read()

    @app.get("/ui", response_class=HTMLResponse)
    def index_uiux():
        with open(STATIC_DIR / "index_uiux.html", "r") as f:
            return f.read()

    return app
