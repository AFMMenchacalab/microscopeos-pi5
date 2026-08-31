import sys, os, time, tempfile
sys.path.insert(0, os.environ.get("SP", os.path.dirname(os.path.abspath(__file__))))
import emuladores
tif = emuladores.instalar()
sys.path.insert(0, os.environ.get("PROY", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "codigo", "MicroscopeOS")))

from core.illumination import IlluminationController, IlluminationError
from core.camera import CameraController
from core.timelapse import TimelapseManager

ok = fail = 0
def check(nombre, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print(f"  PASS  {nombre}")
    else:    fail += 1; print(f"  FAIL  {nombre}  {extra}")

print("\n=== ILUMINACION: protocolo ESP32-S3 ===")
luz = IlluminationController(port="/dev/matriz_cam0")
m = luz.ser.matriz
check("no reinicia la placa al abrir (DTR/RTS)", m.resets == 0, f"resets={m.resets}")
check("abre con dtr=False y rts=False", luz.ser._abierto_con == (False, False))
check("apaga al conectar", m.patron == "OFF")
check("primer comando es OFF:0", m.recibidos[0] == "OFF:0", m.recibidos[:1])

luz.set_brightness(80)
luz.on()
check("on() manda FULL, no ON", m.patron == "FULL", m.recibidos[-1])
check("80% -> 204/255", m.brillo == 204, f"brillo={m.brillo}")
check("sin error en la respuesta", luz.last_error is None, luz.last_error)

luz.left();   check("left() -> LEFT",     m.patron == "LEFT")
luz.right();  check("right() -> RIGHT",   m.patron == "RIGHT")
luz.top();    check("top() -> TOP",       m.patron == "TOP")
luz.bottom(); check("bottom() -> BOTTOM", m.patron == "BOTTOM")

# brillo en caliente: debe reenviar el patron actual
n = len(m.recibidos)
luz.set_brightness(20)
check("set_brightness reenvia el patron si esta encendida", len(m.recibidos) > n and m.brillo == 51,
      f"brillo={m.brillo} recibidos={m.recibidos[-1:]}")
check("patron se conserva tras cambiar brillo", m.patron == "BOTTOM")

luz.off()
check("off() manda OFF:0", m.patron == "OFF" and m.brillo == 0)
n = len(m.recibidos)
luz.set_brightness(50)
check("set_brightness apagada NO enciende", len(m.recibidos) == n)

check("id() devuelve la calibracion", luz.id() == "ID:MATRIZ:A0:F2:62:EB:21:A4:ROT90:FX0:FY1", luz.id())
check("is_on() falso tras off", luz.is_on() is False)

# tope de potencia
luz2 = IlluminationController(port="/dev/matriz_test_tope", max_value=46)
luz2.set_brightness(100); luz2.on()
check("max_value limita el brillo a 46", luz2.ser.matriz.brillo == 46, luz2.ser.matriz.brillo)
luz2.close()

# modo estricto
luz3 = IlluminationController(port="/dev/matriz_test_strict", strict=True)
try:
    luz3._enviar("NOEXISTE", 10); estricto = False
except IlluminationError: estricto = True
check("strict=True lanza en ERR:UNKNOWN_PATTERN", estricto)
luz3.close()

print("\n=== CAMARA: dos instancias persistentes, sin mux ===")
emuladores.Picamera2Fake.instancias_creadas = 0
cam = CameraController()
d = tempfile.mkdtemp()
cam.capture_image(0, folder=d, filename=f"{d}/a0.tif")
cam.capture_image(1, folder=d, filename=f"{d}/a1.tif")
check("una instancia Picamera2 por camara", emuladores.Picamera2Fake.instancias_creadas == 2,
      emuladores.Picamera2Fake.instancias_creadas)

i0 = cam._cams[0]
nconf = i0.n_configure
for k in range(4):
    cam.capture_image(0, folder=d, filename=f"{d}/b{k}.tif")
check("no reconfigura entre capturas del mismo modo", i0.n_configure == nconf,
      f"configure {nconf} -> {i0.n_configure}")
check("no cierra la camara tras capturar", not i0.cerrada and i0.corriendo)
check("libera todos los requests", i0.requests_liberadas == 5, i0.requests_liberadas)
check("TIFF sale 2D uint16 (2464,3280)", tif.escritos[f"{d}/a0.tif"] == (2464, 3280),
      tif.escritos.get(f"{d}/a0.tif"))

nconf = i0.n_configure
cam.start_preview(0)
check("preview reconfigura una vez", i0.n_configure == nconf + 1)
check("get_preview_frame devuelve JPEG", cam.get_preview_frame()[:2] == b"\xff\xd8")
check("get_frame existe y devuelve array", cam.get_frame(0).shape == (480, 640, 3))
cam.stop_preview()
check("stop_preview no cierra la instancia", not i0.cerrada)

print("\n=== CAMARA: captura simultanea ===")
t0 = time.monotonic(); cam.capture_image(0, d, f"{d}/s0.tif"); cam.capture_image(1, d, f"{d}/s1.tif")
secuencial = time.monotonic() - t0
t0 = time.monotonic(); res = cam.capture_both(folder=d, filenames={0: f"{d}/p0.tif", 1: f"{d}/p1.tif"})
paralelo = time.monotonic() - t0
check("capture_both devuelve las dos rutas", set(res) == {0, 1}, res)
check("capture_both crea los dos ficheros", os.path.exists(f"{d}/p0.tif") and os.path.exists(f"{d}/p1.tif"))
check(f"paralelo mas rapido ({paralelo:.2f}s vs {secuencial:.2f}s)", paralelo < secuencial * 0.75)
cam.stop()
check("stop() cierra las dos camaras", i0.cerrada and len(cam._cams) == 0)

print("\n=== TIMELAPSE: secuencial vs simultaneo ===")
for modo_sim in (False, True):
    os.chdir(tempfile.mkdtemp())
    c = CameraController()
    luces = {0: IlluminationController(port=f"/dev/mtl0_{modo_sim}"),
             1: IlluminationController(port=f"/dev/mtl1_{modo_sim}")}
    tl = TimelapseManager(c, luces)
    tl.start(modo="dpc", interval_seconds=1, duration_seconds=1,
             stabilization_time=0.01, camaras=[0, 1], simultaneo=modo_sim)
    while tl.is_running(): time.sleep(0.05)
    base = tl.base_folder
    tifs = sorted(os.path.basename(f) for cn in (0, 1)
                  for f in os.listdir(os.path.join(base, f"cam{cn}")))
    etiqueta = "simultaneo" if modo_sim else "secuencial"
    en_log  = "simultanea" if modo_sim else "secuencial"   # concuerda con "captura"
    check(f"{etiqueta}: 8 TIFF (2 cam x 4 patrones DPC)", len(tifs) == 8, tifs)
    check(f"{etiqueta}: sufijos DPC correctos",
          sorted({t[-6:-4] for t in tifs}) == ["_B", "_L", "_R", "_T"], sorted({t[-6:-4] for t in tifs}))
    check(f"{etiqueta}: apaga las luces al terminar",
          all(l.ser.matriz.patron == "OFF" for l in luces.values()))
    check(f"{etiqueta}: temperatura.csv con cabecera",
          open(os.path.join(base, "temperatura.csv")).readline().startswith("timestamp,ciclo"))
    log = open(os.path.join(base, "timelapse.log")).read()
    check(f"{etiqueta}: log dice el modo de captura", en_log in log, log.splitlines()[:1])
    check(f"{etiqueta}: sin ERROR en el log", "ERROR" not in log,
          [l for l in log.splitlines() if "ERROR" in l][:2])
    c.stop()

print(f"\n{'='*50}\nPASS: {ok}   FAIL: {fail}\n{'='*50}")
sys.exit(1 if fail else 0)
