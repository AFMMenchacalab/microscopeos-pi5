# CHANGELOG — MicroscopeOS: Raspberry Pi 4B → Raspberry Pi 5

Adaptación del código extraído de la Pi 4B (`MicroscopeOS_Pi4_export/`, sin
modificar) a Raspberry Pi 5 con multiplexor de cámaras eliminado y matrices
LED sustituidas.

**Nada de esto se ha probado en hardware.** Ver `TODO_HW.md`.

Commit base con el árbol original intacto: `43ff254`.

---

## Cambio 1 — GPIO: RPi.GPIO / pigpio → rpi-lgpio

**Resultado: cero líneas de código Python modificadas.** Es un cambio de
instalación, no de código.

- `pigpio`: **no se usa en ningún archivo del proyecto**. Nada que migrar.
- `RPi.GPIO`: un solo uso, `motortest.py`. Y la Pi 4 de origen **ya corría
  `rpi-lgpio 0.6`** (`docs/requirements_pi4.txt`), que es precisamente el
  shim que provee el módulo `RPi.GPIO` sobre `lgpio`. El `import RPi.GPIO as
  GPIO` funciona tal cual en Pi 5.
- `gpiozero`: solo en `extras/matrices_led_rp2040/{led_test,capture_led}.py`,
  scripts históricos ya fuera del flujo activo. Backend lgpio, compatible.

### Modificado
- `codigo/MicroscopeOS/motortest.py` — **solo comentarios**. Advertencia de
  no instalar `RPi.GPIO` con pip, y dos `TODO-HW` (BCM 20/21 libres, timing
  del paso). Lógica sin tocar.
- `docs/requirements_pi5.txt` *(nuevo)* — deriva del de Pi 4, marca `[PI5]`
  en cada diferencia. `docs/requirements_pi4.txt` se conserva.

### Casos no triviales
Ninguno. La migración de GPIO era el cambio de menor riesgo del encargo, al
contrario de lo esperado.

---

## Cambio 2 — Eliminación del multiplexor de cámaras

**El mux no tenía lógica en Python.** Búsqueda de `mux|arducam|multiplex|
select_camera|switch_cam|i2c_switch` en todo el árbol: cero coincidencias en
código. El multiplexor vivía **solo** en una línea de device tree, y
`core/camera.py` ya llamaba `Picamera2(camera_num=n)` directo — libcamera
hacía la conmutación por debajo.

El trabajo real fue reescribir el acceso a cámaras para explotar los dos
puertos CSI nativos.

### Modificado
- `configs/boot/config.txt`
  - **Eliminado** `dtoverlay=camera-mux-4port,cam0-imx219,cam1-imx219`.
  - **Añadido** `dtoverlay=imx219,cam0` + `dtoverlay=imx219,cam1`.
  - **Eliminado** `enable_uart=1` → libera GPIO14/15.
  - Bloques `[cm4]`/`[cm5]` comentados (solo aplican a Compute Modules).
- `configs/boot/cmdline.txt` — eliminado `console=serial0,115200` (la otra
  mitad de lo que ocupaba GPIO14/15). Conservado
  `cfg80211.ieee80211_regdom=MX`.
- `codigo/MicroscopeOS/core/camera.py` — **reescrito**:
  - Una instancia `Picamera2` **persistente por cámara**, en vez de una única
    instancia global con `close()`/`open()` en cada captura. En Pi 4 un ciclo
    DPC de dos cámaras hacía 8 ciclos completos de construcción, configuración,
    arranque y cierre; ahora la cámara solo se reconfigura al cambiar de modo
    (preview ↔ still).
  - Locks **por cámara** (`RLock`) en vez de un lock global, que serializaba
    las dos cámaras innecesariamente.
  - `capture_both()` nuevo — captura paralela con `ThreadPoolExecutor`.
    Preconfigura ambas cámaras antes de disparar, para que el paralelismo
    mida solo la captura.
  - `get_frame()` nuevo — ver «Correcciones» abajo.
  - Constantes `STILL_SIZE`, `PREVIEW_SIZE`, `RAW_FORMAT` extraídas.
  - **API pública sin cambios**: `capture_image`, `start_preview`,
    `get_preview_frame`, `stop_preview`, `set_exposure`, `stop` y el atributo
    `preview_cam` conservan firma y semántica, para no tocar `server/api.py`
    ni `core/timelapse.py`.
- `codigo/MicroscopeOS/core/timelapse.py`
  - Bloque de captura extraído a `_capturar_secuencial()` (comportamiento
    idéntico al de Pi 4, byte por byte) y `_capturar_simultaneo()` (nuevo).
  - `start(..., simultaneo=False)`. **Por defecto sigue siendo secuencial.**
  - El log de inicio indica qué modo se usa.
- `codigo/MicroscopeOS/server/api.py`
  - `TimelapseReq.simultaneo: bool = False`.
  - Endpoint `POST /capture/both/{modo}` nuevo.

### Enfoque de paralelización elegido

Se evaluaron tres opciones: hilos, asyncio y disparo por hardware.

**Elegido: `ThreadPoolExecutor` con dos workers.** `picamera2` bloquea en C
y libera el GIL durante la captura, así que dos hilos capturan de verdad en
paralelo sin necesidad de asyncio. Es también lo que menos altera el código
existente: `TimelapseManager` ya corre en su propio hilo y `capture_image`
sigue siendo síncrona.

Se descartó **asyncio** porque obligaría a convertir toda la cadena
(`TimelapseManager`, `capture_image`, los endpoints) a `async` sin ganar nada:
el cuello de botella es E/S de C, no coordinación de corrutinas.

Se descartó el **disparo por hardware** (sincronización a nivel de sensor)
porque los IMX219 del montaje no tienen las líneas de trigger cableadas.

Ganancia esperada con `simultaneo=True`: el ciclo DPC pasa de **8 capturas
secuenciales a 4 pasos paralelos**. Más la eliminación del open/close por
captura, que aplica **en los dos modos**.

⚠️ `simultaneo=True` enciende **las dos matrices a la vez**. Por eso está
desactivado por defecto: requiere validar que no hay diafonía óptica entre
canales (`TODO_HW.md` §2.1). El defecto sin validar es el comportamiento
seguro y conocido.

### GPIO liberados por el mux — corrección al encargo

**El mux nunca usó GPIO14/15.** Lo que los ocupaba era la consola serie:
`enable_uart=1` en `config.txt` más `console=serial0,115200` en
`cmdline.txt`. Ambos eliminados, así que **GPIO14/15 quedan libres**, pero
por haber quitado la consola, no el multiplexor.

Qué GPIOs usaba realmente `camera-mux-4port` no se puede determinar sin la
Pi (`dtoverlay -h camera-mux-4port`): `TODO_HW.md` §3.2. Importa porque
`motortest.py` usa BCM 20/21.

---

## Cambio 3 — Matrices RP2040 → Waveshare ESP32-S3-Matrix

### El protocolo SÍ cambió

El encargo decía que el protocolo se mantenía idéntico. **No es así.** El
código de Pi 4 hablaba el protocolo del firmware RP2040, que es otro:

| | RP2040 (código Pi 4) | ESP32-S3 (firmware nuevo) |
|---|---|---|
| Baudrate | 115200 | 9600 (virtual, USB-CDC lo ignora) |
| Terminador | `\r\n` | `\n` |
| Encendido | `ON` / `ALL` | `FULL` |
| Brillo | comando aparte `BRIGHT 204` | dentro del comando: `FULL:204` |
| Respuesta | `OK ON` | `OK:FULL:204` |
| Errores | `ERR desconocido: X` | `ERR:UNKNOWN_PATTERN:X`, etc. |
| Geometría | 5×5, 25 LEDs | 8×8, 64 LEDs |

**Respuesta a la pregunta del encargo: sí había que tocar
`IlluminationController`, y bastante.** El protocolo que describe el
encargo es el correcto — es el código el que estaba desactualizado.

### Modificado
- `codigo/MicroscopeOS/core/illumination.py` — **reescrito**:
  - Protocolo `PATRON:brillo\n` / `OK:PATRON:brillo\n`.
  - `ON` → `FULL`; el brillo se mantiene como estado y viaja en cada comando.
  - Apertura del puerto con `dtr=False`, `rts=False`, `dsrdtr=False`
    aplicados **antes** de `open()`: en el USB-Serial/JTAG del ESP32-S3
    DTR+RTS es la secuencia de reset y pyserial afirma DTR al abrir. Sin
    esto, cada conexión reiniciaba la placa.
  - `reset_input_buffer()` + 300 ms: el ROM del ESP32-S3 escribe mensajes de
    arranque por el mismo CDC. (El `sleep(1)` del RP2040 no cubría esto.)
  - Validación de la respuesta contra el eco esperado; `ERR:*` queda en
    `last_error`, o lanza `IlluminationError` con `strict=True`. Antes la
    respuesta se leía y se descartaba.
  - `id()` nuevo — devuelve la calibración del firmware cargado
    (`ID:MATRIZ:A0:F2:62:EB:21:A4:ROT90:FX0:FY1`).
  - `max_value` nuevo — tope duro opcional del brillo (defecto 255, sin
    tope, como el firmware).
  - Guarda contra `ERR:BAD_FORMAT:LINE_TOO_LONG` (límite de 47 caracteres).
  - **API pública sin cambios**: `on/off/left/right/top/bottom/set_brightness/
    pulse/is_on/close` conservan firma. `set_brightness` sigue siendo 0-100 y
    mapea internamente a 0-255, así que `server/api.py` y el slider de
    `index.html` **no se tocaron**.
- `configs/udev/99-microscopeos-matriz.rules` *(nuevo)* — la regla que
  aportaste, con VID:PID `303a:1001` y seriales MAC reales.
  `99-matrices.rules` renombrado a `.rp2040-obsoleta`.
  **Sin `TODO-HW`**: los valores ya venían completos y verificados, no hace
  falta correr `udevadm info`.
- `codigo/extras/matrices_esp32s3_matrix/` *(nuevo)* — README del firmware,
  regla udev, instalador y los dos backups de fábrica.
  `matrices_led_rp2040/` se conserva como referencia histórica.
- `codigo/extras/matrices_esp32s3_matrix/install-udev.sh` — la ruta
  hardcodeada `/home/alexflores/Proyectos/microscopeos-dpc-matrix/` se
  sustituye por una deducida de `BASH_SOURCE`, para que funcione igual en la
  laptop y en la Pi.

### Timings calibrados al RP2040 que sí fallarían con el ESP32
Era la pregunta del encargo. Encontrados tres, todos corregidos:
1. `sleep(1)` tras abrir el puerto — insuficiente/inadecuado: el problema
   real no era el tiempo sino el reset por DTR y la basura del ROM.
2. `sleep(0.05)` + `read(100)` — lectura por timeout fijo en vez de por
   línea. Sustituido por `readline()`.
3. `BRIGHT n` como comando con estado propio — no existe en el ESP32.

### Sobre los backups de firmware
Verificado que **ninguno de los dos `.bin` contiene el protocolo DPC**, así
que ambos sirven para volver a fábrica. Pero **no son la misma imagen**
(esp-idf v4.4.7/2024 vs v4.4.5/2023): ver `TODO_HW.md` §4.4.

**Falta el sketch `dpc_matrix`** en el repo.

---

## Cambio 4 — Verificación de captura RAW en PiSP

Sin cambios de lógica, según lo pedido. Cuatro `TODO-HW` en
`core/camera.py`, detallados en `TODO_HW.md` §1.1, §1.2, §2.2 y §2.3:

1. `RAW_FORMAT = "SBGGR10"` — PiSP puede devolver `SBGGR10_CSI2P`.
2. `.view(np.uint16)` — asume empaquetado y stride concretos. **Falla en
   silencio**: escribe basura sin lanzar excepción.
3. `cv2.COLOR_BayerRG2GRAY` sobre un buffer pedido como `SBGGR10` (BG) —
   inconsistencia heredada de Pi 4 que ahí funcionaba.
4. `sleep(0.3)` tras configurar — estabilización de exposición.

---

## Cambio 5 — Compatibilidad general

### Corregido
- `server/api.py` — eliminado `sys.path.insert(0,
  '/home/microscope1/MicroscopeOS')`. Ahora `BASE_DIR` se deduce de
  `__file__`.
- `server/api.py` — `StaticFiles(directory="server/static")` y el `open()` de
  `index.html` eran **relativos al CWD del proceso**: el servidor fallaba si
  se arrancaba desde otro directorio. Ahora absolutos vía `BASE_DIR`.
- `ver_timelapse.py` — `glob("timelapse_2026*")` → `glob("timelapse_*")`.
  Habría dejado de encontrar carpetas en 2027.
- `configs/systemd/microscopeos.service` — comentario indicando qué ajustar.
  Las rutas siguen ahí porque systemd las necesita absolutas, pero ya **no
  hay ninguna ruta absoluta en el código Python**.

### Reportado, no corregido
- `start_microscope.py` es un **script bash con extensión `.py`**, duplicado
  exacto de `start_microscope.sh`. No se tocó (rule: si funciona y no está
  afectado, no tocar), pero conviene borrarlo.
- `configs/boot/cmdline.txt` conserva el `PARTUUID` de la microSD de la Pi 4.
  Es referencia, no para copiar tal cual: `TODO_HW.md` §4.3.
- `PyQt6==6.10.2` es la dependencia con más riesgo de no tener wheel aarch64
  para el Python de Bookworm. Solo la usa el modo GUI, que no está en
  producción. Desfijada en `requirements_pi5.txt`.
- `matplotlib` sigue ausente: `_graficar_temperatura()` nunca ha funcionado,
  ni en Pi 4. Falla dentro de un `except` que solo loguea.
- `bundle_codigo.md` (raíz) es un volcado concatenado del código de Pi 4 y
  **ha quedado obsoleto** con estos cambios. Se dejó intacto por si sirve de
  referencia del estado original; conviene regenerarlo o borrarlo.

---

## Correcciones de bugs preexistentes

Bugs anteriores a esta migración, no causados por el cambio de plataforma.
Van en commit aparte para que puedas revertirlos por separado.

- `core/camera.py` — **`get_frame()` no existía**. `core/preview.py` y
  `interfaces/desktop_gui.py` lo llamaban, así que los modos
  `preview`/`both`/`gui` de `main.py` estaban rotos con `AttributeError`.
  Implementado.
- `main.py` — pasaba un único `IlluminationController` donde
  `TimelapseManager` y `server/api.py` esperan un dict `{0: ..., 1: ...}`
  (hacen `illuminations.get(cam)`). Solo `run_web.py` lo construía bien.
  Corregido, más cierre de los puertos en el `finally`.
- `test_timelapse.py` — mismo bug del dict. Corregido.
- `test_luz.py` — `set_brightness(120)` con el comentario `# 80%`. La
  función clampa a 100, así que pedía el máximo. Puesto a 50, que es lo que
  dice el `print` de la línea de arriba.

### Detectado y NO corregido
- `interfaces/desktop_gui.py` llama `camera.capture_image()` **sin
  `camera_num`**, que es obligatorio. Sigue roto. No se tocó porque la GUI
  es código huérfano (`microscope.service` está deshabilitado) y arreglarlo
  bien implica decidir qué cámara usa la GUI, que es una decisión de
  producto, no un bug mecánico.

---

## Archivos nuevos

| Archivo | Contenido |
|---|---|
| `CHANGELOG.md` | Este documento |
| `TODO_HW.md` | Los 15 `TODO-HW` por prioridad de validación |
| `MIGRACION_PI5.md` | Pasos a ejecutar en la Pi 5 |
| `docs/requirements_pi5.txt` | Dependencias, apt vs pip |
| `configs/udev/99-microscopeos-matriz.rules` | Regla udev de las ESP32-S3 |
| `codigo/extras/matrices_esp32s3_matrix/` | Firmware, backups, instalador |
