from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import os
import time
import numpy as np
import cv2
import tifffile
import asyncio
import json
import sys
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

class SetpointPayload(BaseModel):
    value: float


def create_app(camera, illuminations, timelapse):

    app = FastAPI()
    estado = {"camara_activa": 0}

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
            while camera_num in camera._preview_cams:
                frame = camera.get_preview_frame(camera_num)
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
            luz = illuminations.get(cam)
            if luz is None:
                continue
            if req.percent is not None:
                luz.set_brightness(req.percent)
            if req.modo == "rheinberg":
                luz.rheinberg(req.color_centro, req.color_anillo)
            else:
                getattr(luz, _METODOS_LUZ[req.modo])()
        return {"status": "ok", "modo": req.modo}

    @app.post("/light/on")
    def light_on():
        if timelapse.is_running():
            return {"error": "Timelapse en curso"}
        luz = illuminations.get(estado["camara_activa"])
        if luz:
            luz.on()
        return {"status": "on"}

    @app.post("/light/off")
    def light_off():
        for luz in illuminations.values():
            luz.off()
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
            simultaneo=req.simultaneo
        )
        return {"status": "started"}

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
    # Interfaz
    # ===============================
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index():
        with open(STATIC_DIR / "index.html", "r") as f:
            return f.read()

    return app
