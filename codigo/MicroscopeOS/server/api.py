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
sys.path.insert(0, '/home/microscope1/MicroscopeOS')
from temperature_controller import temperature_controller


class ExposureReq(BaseModel):
    exposure: int
    gain: float

class BrightnessReq(BaseModel):
    percent: int

class TimelapseReq(BaseModel):
    modo: str = "blanco"
    interval: int = 300
    duration: int = 3600
    stabilization: float = 0.3
    nombre: str = ""
    camaras: list = [0, 1]

class SetpointPayload(BaseModel):
    value: float


def create_app(camera, illuminations, timelapse):

    app = FastAPI()
    estado = {"camara_activa": 0}

    def _preview_png(camera_num):
        tmp = "/tmp/preview_cam.tif"
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
        if camera.preview_cam is not None:
            camera.stop_preview()
        estado["camara_activa"] = camera_num
        png = _preview_png(camera_num)
        return Response(content=png, media_type="image/png")

    # ===============================
    # Vivo
    # ===============================
    @app.post("/live/start/{camera_num}")
    def live_start(camera_num: int):
        if timelapse.is_running():
            return {"error": "Timelapse en curso"}
        estado["camara_activa"] = camera_num
        camera.start_preview(camera_num)
        return {"status": "live", "cam": camera_num}

    @app.post("/live/stop")
    def live_stop():
        camera.stop_preview()
        return {"status": "stopped"}

    @app.get("/live/stream")
    def live_stream():
        def gen():
            while camera.preview_cam is not None:
                frame = camera.get_preview_frame()
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
    @app.post("/capture/{camera_num}/{modo}")
    def capture(camera_num: int, modo: str):
        if timelapse.is_running():
            return {"error": "Timelapse en curso"}
        from datetime import datetime
        folder = "capturas_unicas"
        os.makedirs(folder, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        guardados = []
        if modo == "dpc":
            patrones = [("_L", "left"), ("_R", "right"),
                        ("_T", "top"), ("_B", "bottom")]
        else:
            patrones = [("", "on")]
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
            camaras=req.camaras
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
    app.mount("/static", StaticFiles(directory="server/static"), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index():
        with open("server/static/index.html", "r") as f:
            return f.read()

    return app
