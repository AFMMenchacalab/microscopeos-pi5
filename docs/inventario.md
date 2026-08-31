# Inventario — MicroscopeOS (export desde Raspberry Pi 4B)

Generado a partir de la microSD montada en solo lectura. Sistema origen:
Raspberry Pi OS (Bookworm/Trixie, kernel `6.12.47+rpt-rpi-v8`/`-2712`),
hostname `Microscope1`, usuario `microscope1`.

## Qué hace el proyecto

Controlador de un microscopio de dos cámaras (IMX219, vía un HAT
multiplexor de 4 puertos) con iluminación tipo DPC (Differential Phase
Contrast) mediante matrices de LEDs, timelapses largos (hasta 48h) con
captura raw 16-bit, y control de temperatura/CO2 de una incubadora vía un
Arduino externo con PID. Se opera principalmente vía interfaz web
(FastAPI + HTML estático); existe también una GUI de escritorio en
PyQt6 que actualmente **no se usa** en producción (ver más abajo).

## Módulos (codigo/MicroscopeOS/)

- **`main.py`** — punto de entrada con varios `MODE` posibles
  (`preview`/`timelapse`/`both`/`gui`/`web`). El modo activo commiteado es
  `"web"`, que simplemente arranca el servidor FastAPI (equivalente a
  `run_web.py`). El modo `"gui"` (PyQt6, `interfaces/desktop_gui.py`)
  existe en el código pero **no está siendo lanzado por ningún servicio
  activo** — es código funcional pero huérfano en este momento.
- **`run_web.py`** — arranque directo del servidor web; es lo que
  ejecuta `microscopeos.service` en producción.
- **`core/camera.py`** — `CameraController`, envuelve `picamera2`.
  Captura still raw `SBGGR10` a 3280×2464 (resolución nativa IMX219),
  debayer a gris con OpenCV, guarda TIFF 16-bit con `tifffile`. Preview
  en modo video de baja resolución para streaming MJPEG.
- **`core/illumination.py`** — `IlluminationController`. **No usa GPIO
  directo**: habla por serial (115200 baud) con una placa RP2040 por
  cámara (`/dev/matriz_cam0`, `/dev/matriz_cam1`), enviando comandos de
  texto `ON/OFF/BRIGHT n/LEFT/RIGHT/TOP/BOTTOM`.
- **`core/timelapse.py`** — `TimelapseManager`. Corre en un hilo,
  genera `timelapse_YYYYMMDD_HHMMSS/camN/img_*.tif`, un log, un
  `temperatura.csv` (leyendo `temperature_controller`), y al finalizar
  intenta graficar `temperatura.png` con matplotlib.
  ⚠️ **matplotlib no está instalado** (ni en el venv ni a nivel sistema)
  — esa parte falla silenciosamente (hay un `except Exception` que solo
  loguea). El CSV sí se genera bien.
- **`core/preview.py`** — preview con ventana OpenCV local (modo
  standalone, no es el preview que usa la web).
- **`core/profile_manager.py`** / **`core/config.py`** — perfiles de
  cámara/timelapse guardados como JSON en `profiles/`.
  Nota: `IlluminationSettings.gpio_pin=17` es un campo vestigial de un
  esquema anterior (ver `extras/matrices_led_rp2040/led_test.py` y
  `capture_led.py`, que sí controlaban un LED simple por GPIO 17 con
  `gpiozero` antes de migrar a las matrices RP2040 por serial). Ya no se
  usa en el flujo actual.
- **`server/api.py`** — API FastAPI: preview, live MJPEG stream, luz,
  captura única, exposición/brillo, control de timelapse, y proxy de
  temperatura/CO2 (`/api/temperature/*`, incluye SSE en
  `/api/temperature/stream`). Sirve `server/static/index.html` como UI.
  ⚠️ Tiene un path absoluto hardcodeado:
  `sys.path.insert(0, '/home/microscope1/MicroscopeOS')` — si en la Pi 5
  cambias de usuario o de ruta del proyecto, hay que actualizar esta
  línea.
- **`interfaces/desktop_gui.py`** — GUI PyQt6 completa (preview,
  histograma, perfiles, timelapse, atajos de teclado). Funcional pero no
  se lanza actualmente (ver `main.py` arriba).
- **`temperature_controller.py`** — habla por serial (9600 baud) con un
  Arduino (autodetectado por descripción USB: "arduino"/"ch340"/"cp210"),
  protocolo JSON de línea (`{"temp":..,"setpoint":..,"pwm":..,"co2":..,
  ...}`), comandos `SET:xx.x` y `SET_CO2:xxxxx`.
- **`motortest.py`** — script suelto de prueba de un motor paso a paso
  (pines BCM 20/21) usando `RPi.GPIO` directo. Ver nota GPIO más abajo.

## Extras (codigo/extras/) — fuera de MicroscopeOS/, incluidos porque son parte del sistema real

- **`matrices_led_rp2040/`** — firmware MicroPython para las placas
  RP2040 de las matrices LED (25 NeoPixels, patrón 5×5):
  - `rp2040_firmware.py`: firmware activo, implementa el protocolo que
    espera `core/illumination.py` (ON/OFF/BRIGHT/LEFT/RIGHT/TOP/BOTTOM).
  - `mapeo_leds.py`: script de calibración (enciende un LED a la vez
    para mapear índice físico → posición).
  - `micropython_rp2040.uf2`: imagen de MicroPython para flashear el
    RP2040 (drag-and-drop en modo BOOTSEL).
  - `capture_led.py`, `led_test.py`: scripts de prueba **antiguos**, de
    cuando la iluminación era un LED simple por GPIO 17 (`gpiozero`),
    previos a las matrices RP2040. Se conservan como referencia
    histórica; ya no forman parte del flujo activo.
- **`incubadora_temperatura/`** — sketches Arduino (`.ino`):
  - `Control_Incubator_TempCO2.ino`: **el sketch activo** — coincide
    exactamente con el protocolo de `temperature_controller.py`
    (comentario en el propio archivo lo confirma). DS18B20 (1-Wire, pin
    D2 del Arduino) + PID de temperatura (PWM en D3) + sensor SCD30
    de CO2 (I2C) + válvula solenoide PID (D9, ventana PWM lenta de 10s).
  - `Temperature_PID_Modulo1_SerialControl.ino`: versión previa, solo
    temperatura, usa un termopar MAX6675 (SPI) en vez de DS18B20.
  - `Control_Incubator_William1.ino`, `Temperature_PID_DS18B20.ino`:
    variantes/versiones de desarrollo — no se determinó cuál exactamente
    corresponde al hardware final; revisar antes de usar en Pi 5.
  - **Importante**: el DS18B20 (1-Wire) cuelga del **Arduino**, no de la
    Raspberry Pi. Por eso no hay overlay `w1-gpio` en `config.txt` — es
    correcto, no falta nada ahí.
  - Librerías Arduino usadas (ver `docs/estructura.txt` para versiones):
    OneWire, DallasTemperature, Adafruit_SCD30 (+ BusIO, Unified_Sensor),
    PID (Brett Beauregard), Adafruit_SSD1306+GFX (display OLED, no vi
    que se use en el `.ino` activo — puede ser de una revisión anterior).
  - `Arduino/libraries/`: las librerías fuente de terceros (no hace
    falta revisarlas, se reinstalan con el Library Manager de Arduino
    IDE si se necesitan).

## Configuración del sistema (configs/)

- **`boot/config.txt`**: `dtoverlay=camera-mux-4port,cam0-imx219,cam1-imx219`
  — HAT multiplexor de 4 puertos para 2 cámaras IMX219.
  ⚠️ **Riesgo principal para Pi 5**: el stack de cámara cambió
  significativamente entre Pi 4 (Unicam) y Pi 5 (CFE / PiSP). Hay que
  verificar que el overlay `camera-mux-4port` exista y funcione igual en
  el firmware de Pi 5 — si el HAT es de un fabricante externo (parece
  Arducam u similar), revisar su documentación específica para Pi 5
  antes de asumir que es plug-and-play.
  - `dtparam=i2c_arm` y `dtparam=spi` están **comentados** (deshabilitados)
    en config.txt, pese a que `/etc/modules-load.d/modules.conf` carga
    `i2c-dev`. Puede ser intencional (el overlay de cámara ya habilita el
    I2C que necesita para controlar el sensor) o un descuido. Si algo en
    Pi 5 necesita I2C/SPI de usuario explícito, hay que descomentar estas
    líneas.
  - No hay overlay `w1-gpio` — correcto, ver nota de DS18B20 arriba.
- **`boot/cmdline.txt`**: incluye `cfg80211.ieee80211_regdom=MX`
  (dominio regulatorio WiFi México) — replicar si aplica.
- **`systemd/microscopeos.service`**: **activo** (enlazado en
  `multi-user.target.wants`). Corre `run_web.py` con el Python del venv
  como usuario `microscope1`, `WorkingDirectory=/home/microscope1/MicroscopeOS`.
  Esto es lo que realmente arranca en cada boot.
- **`systemd/microscope.service`**: **no está habilitado** (no hay
  symlink en ningún `*.target.wants/`). Lanza `start_microscope.sh`
  (→ `main.py`, modo GUI vía `DISPLAY=:0`). Parece un remanente de
  cuando se usaba la GUI de escritorio.
- **`systemd/getty@tty1.service.d/autologin.conf`**: autologin de
  `microscope1` en la consola de texto tty1.
- **`lightdm/lightdm.conf`**: `autologin-user=microscope1` — autologin
  también a nivel de sesión gráfica (lightdm), aunque no se encontró
  ningún autostart en `~/.config/autostart/` que lance la GUI
  automáticamente. En la práctica el usuario probablemente accede por
  navegador a `http://<ip>:8000`.
- **`udev/99-matrices.rules`**: mapea 2 placas RP2040 (idVendor `2e8a`,
  Raspberry Pi Foundation — típico de placas RP2040) por número de
  serie a `/dev/matriz_cam0` y `/dev/matriz_cam1`. **Los números de
  serie son específicos de esas 2 placas físicas** — si migras el mismo
  hardware, esta regla se puede copiar tal cual a la Pi 5. Si vas a usar
  placas RP2040 nuevas, hay que regenerar los `serial=` con
  `udevadm info` sobre las placas reales.
- **`modules` / `modules-load.d/modules.conf`**: cargan `i2c-dev`.

## Cosas que probablemente rompan o necesiten atención en Pi 5

1. **GPIO (`motortest.py`, `RPi.GPIO`)** — ✅ buena noticia: el sistema
   origen **ya tiene instalado `python3-rpi-lgpio` (v0.6)**, el shim de
   compatibilidad que implementa la API de `RPi.GPIO` sobre `lgpio` y
   que sí funciona en el chip GPIO nuevo de la Pi 5 (RP1). No es la
   librería clásica `RPi.GPIO` (que NO funciona en Pi 5). Mientras en
   Pi 5 instales el mismo paquete `python3-rpi-lgpio` vía apt (no
   `pip install RPi.GPIO`), el código de `motortest.py` debería
   funcionar sin cambios.
2. **`gpiozero` (`capture_led.py`, `led_test.py`, scripts antiguos)** —
   gpiozero ya usa el backend `lgpio` por defecto en este sistema, así
   que también es compatible con Pi 5 sin cambios de código.
3. **Cámara (`picamera2`, overlay `camera-mux-4port`)** — el riesgo más
   grande de todos. Verificar disponibilidad del overlay del HAT
   multiplexor en el firmware de Pi 5 y que `picamera2`/`libcamera`
   reconozcan el mismo pipeline (Pi 5 usa el front-end PiSP, no Unicam).
   Puede requerir una versión más nueva de `picamera2`/`libcamera` de la
   que trae Pi OS para Pi 4.
4. **`matplotlib` ausente** — instalar si se quiere que
   `core/timelapse.py` genere `temperatura.png` (actualmente falla
   silenciosamente incluso en la Pi 4 origen).
5. **Path absoluto en `server/api.py`** — `/home/microscope1/MicroscopeOS`
   hardcodeado. Actualizar si cambia el usuario o la ruta en Pi 5.
6. **`venv` con `--system-site-packages=true`** — al recrear el venv en
   Pi 5 hay que crearlo igual con esa flag, porque `picamera2`, `cv2`,
   `numpy`, `RPi.GPIO`/`rpi-lgpio` y `pyserial` se resuelven desde los
   paquetes del sistema (apt), no desde pip. Ver `requirements_pi4.txt`
   para la lista completa con la distinción venv vs. sistema.
7. **UDEV por número de serie** — si reusas las mismas placas RP2040
   físicas, `99-matrices.rules` se copia tal cual. Si son placas nuevas,
   regenerar los seriales.
8. **`microscope.service` deshabilitado + GUI huérfana** — decidir si
   se quiere seguir manteniendo ese modo GUI (PyQt6) en Pi 5 o
   limpiarlo; hoy no se usa.
9. **`cfg80211.ieee80211_regdom=MX`** en `cmdline.txt` — replicar si el
   país regulatorio del WiFi debe mantenerse.

## Lo que NO se copió (por tamaño / ser regenerable)

- `venv/` completo (290MB, binarios `aarch64`, se recrea en Pi 5 con
  `python3 -m venv --system-site-packages` + `requirements_pi4.txt`)
- Todas las carpetas `timelapse_YYYYMMDD_HHMMSS/` (~96GB de TIFFs 16-bit)
- `captures/`, `capturas_unicas/`, `test_capturas/` (TIFFs/PNGs de
  datos experimentales)
- `__pycache__/`, `*.pyc`
- `MicroscopeOS.zip` (520MB, backup redundante con la carpeta ya
  extraída)
- `.arduino15/` (394MB, caché/toolchain del Arduino IDE, se
  regenera solo)
- Un archivo `.lgd-nfy0` (named pipe vacío, artefacto de runtime de
  `lgpio`, no es un archivo real)

No se encontró `.git` en el proyecto — no hay historial de versiones
que preservar.
