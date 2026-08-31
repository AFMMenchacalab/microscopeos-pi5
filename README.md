# MicroscopeOS — Pi 5

Migración a **Raspberry Pi 5** del sistema de control de un microscopio de
campo claro / campo oscuro / **DPC (Differential Phase Contrast)** /
**Rheinberg** de dos cámaras. Diseñado para timelapses largos (hasta 48 h)
de cultivos celulares dentro de una incubadora con control de temperatura
y CO₂.

Captura en **TIFF 16-bit** a resolución nativa del sensor, controla la
iluminación por patrones, y registra las condiciones ambientales
sincronizadas con cada ciclo de captura.

Este repo parte del código en producción sobre Pi 4B ([snapshot original
acá](https://github.com/Soyalexf/microscopeos)) y documenta, paso a paso, su
adaptación a la Pi 5: sin multiplexor de cámaras, con matrices de
iluminación nuevas y con captura RAW bajo el pipeline PiSP en vez de Unicam.

## Qué cambia respecto a la Pi 4

- **Dos cámaras nativas, sin multiplexor**: la Pi 5 tiene dos puertos CSI de
  fábrica, así que el HAT de 4 puertos que forzaba capturas secuenciales
  desaparece. Ahora las dos cámaras pueden capturar **en paralelo**
  (`capture_both`, endpoint `/capture/both/{modo}`).
- **Iluminación**: las matrices RP2040 de 25 LEDs (5×5) se reemplazan por
  **Waveshare ESP32-S3-Matrix** (8×8 WS2812B), con protocolo de línea por
  serial compatible en espíritu pero no en pines/VID:PID — ver
  [`codigo/extras/matrices_esp32s3_matrix/README.md`](codigo/extras/matrices_esp32s3_matrix/README.md).
  El firmware se reescribió para agregar dos modos nuevos:
  - **`RING`** — campo oscuro: solo el borde exterior de la matriz, centro
    apagado (iluminación oblicua fuera del cono de apertura del objetivo).
  - **`RHEINBERG`** — contraste de color falso: centro y anillo encendidos
    a la vez, cada uno con su propio color.

  Los cuatro modos (campo claro, DPC, campo oscuro, Rheinberg) están
  disponibles en `core/illumination.py`; `core/timelapse.py` todavía solo
  automatiza `blanco` (campo claro) y `dpc` en timelapse — enganchar
  `RING`/`RHEINBERG` ahí es trabajo pendiente.
- **Captura RAW**: el pipeline pasa de Unicam (Pi 4) a CFE/PiSP (Pi 5), que
  entrega el buffer con un stride alineado a 32 px por fila en vez del ancho
  exacto del sensor — sin recortarlo, el TIFF queda con columnas de basura
  pegadas al borde derecho. Ver `CHANGELOG.md` y el commit que lo corrige.
- **GPIO**: `RPi.GPIO` clásica no funciona en la Pi 5 (southbridge RP1); se
  usa `python3-rpi-lgpio`, que expone la misma API sobre `lgpio`. Cero
  cambios de código en `motortest.py`.

## Arquitectura

```
Navegador  ──HTTP/SSE──>  FastAPI (run_web.py + server/api.py)
                                │
                 ┌──────────────┼──────────────────┐
                 │              │                  │
          CameraController  TimelapseManager  TemperatureController
                 │              │                  │
             picamera2      IlluminationController  │
                 │              │                  │
       IMX219 cam0/cam1     serial USB          serial USB
        (CSI nativo,            │                  │
         sin mux)     2× ESP32-S3-Matrix        Arduino
                    (DPC/oscuro/Rheinberg)  (PID temp + CO₂)
                                              │      │
                                          DS18B20  SCD30
                                        calefactor válvula
```

- La iluminación sigue sin ser GPIO directo: es **serial** contra dos
  placas identificadas por reglas udev (`/dev/matriz_cam0`,
  `/dev/matriz_cam1`), sin importar el orden de enumeración USB.
- El control de temperatura/CO₂ sigue viviendo en un **Arduino separado**,
  no en la Pi, para que el lazo PID no dependa de que la Pi siga escribiendo
  TIFFs de 16 MB sin perder timing.
- Las dos `Picamera2` ahora son instancias persistentes (una por cámara), no
  se abren y cierran en cada captura como en la Pi 4 — ver
  `core/camera.py`.

## Estructura

| Ruta | Contenido |
|---|---|
| `codigo/MicroscopeOS/core/` | Lógica de dominio: cámara, iluminación, timelapse, perfiles, config |
| `codigo/MicroscopeOS/server/` | API FastAPI + interfaz web estática |
| `codigo/MicroscopeOS/interfaces/` | GUI de escritorio en PyQt6 (heredada de la Pi 4, sin validar acá) |
| `codigo/extras/matrices_esp32s3_matrix/` | Firmware de las matrices de iluminación (Pi 5) + README de protocolo |
| `codigo/extras/matrices_led_rp2040/` | Firmware viejo (Pi 4), queda como referencia histórica |
| `codigo/extras/incubadora_temperatura/` | Sketches del PID de temperatura/CO₂ |
| `configs/` | Unidades systemd, reglas udev, `config.txt`/`cmdline.txt` de arranque |
| `docs/` | Inventario de hardware, requirements de Pi 4 y Pi 5 |
| `tests/` | Scripts de verificación de hardware (no son tests unitarios) |
| `MIGRACION_PI5.md` | Pasos a ejecutar en la Pi para dejarla lista |
| `TODO_HW.md` | Validaciones pendientes que requieren hardware físico |
| `CHANGELOG.md` | Qué cambió respecto al árbol original de la Pi 4B |

## Instalación

Pensado para Raspberry Pi OS (Bookworm o Trixie) de 64 bits en Pi 5.

Las dependencias de hardware (`picamera2`, `opencv`, `numpy`, `pyserial`,
GPIO) van **con apt**, no con pip — los paquetes de pip no traen los
bindings nativos de libcamera ni del chip GPIO:

```bash
sudo apt install -y python3-picamera2 python3-opencv python3-numpy \
                    python3-serial python3-rpi-lgpio python3-gpiozero \
                    python3-venv git
```

El entorno virtual debe crearse **heredando esos paquetes del sistema**:

```bash
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install -r docs/requirements_pi5.txt
```

Pasos completos de hardware (overlays de cámara, `cmdline.txt`, reglas
udev, grupo `uucp`, servicio): ver **[MIGRACION_PI5.md](MIGRACION_PI5.md)**.

## Uso

```bash
cd codigo/MicroscopeOS
python3 run_web.py
# luego abrir http://<ip-de-la-pi>:8000
```

> Ejecutar siempre desde `codigo/MicroscopeOS/`: la API sirve
> `server/static/` con rutas relativas al directorio de trabajo.

Como servicio de arranque automático:

```bash
sudo cp configs/systemd/microscopeos.service /etc/systemd/system/
sudo systemctl enable --now microscopeos
```

### API

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/preview/{cam}` | Captura un frame y lo devuelve como PNG normalizado |
| `POST` | `/live/start/{cam}` · `/live/stop` | Inicia/detiene el modo vivo |
| `GET` | `/live/stream` | Stream MJPEG |
| `POST` | `/capture/{cam}/{modo}` | Captura única en una cámara (`blanco` o `dpc`) |
| `POST` | `/capture/both/{modo}` | Captura las dos cámaras **en paralelo** (exclusivo de Pi 5, sin mux) |
| `POST` | `/light/on` · `/light/off` | Control de iluminación |
| `POST` | `/exposure` · `/brightness` | Exposición/ganancia y brillo |
| `POST` | `/timelapse/start` · `/timelapse/stop` | Control de timelapse |
| `GET` | `/status` | Estado del timelapse y cámara activa |
| `GET` | `/api/temperature/status` · `/stream` | Telemetría ambiental (JSON / SSE) |
| `POST` | `/api/temperature/setpoint` | Cambia el setpoint de temperatura |

### Salida de un timelapse

```
timelapse_20260831_121729/
├── cam0/img_20260831_121729_L.tif   # patrones DPC: _L _R _T _B (modo "dpc")
├── cam1/...
├── temperatura.csv                   # timestamp,ciclo,temperatura,setpoint,pwm
└── timelapse.log
```

## Estado de la migración

Validado en hardware real (Pi 5 Model B, Debian Trixie) al 2026-08-31:
las dos cámaras capturan TIFF 16-bit a resolución nativa sin el bug de
stride, y las dos matrices de iluminación responden los cuatro modos
(campo claro, DPC, campo oscuro, Rheinberg), confirmados visualmente en el
microscopio.

**Todavía sin validar en esta Pi:** `test_timelapse.py` (corrida larga de
verdad), el servicio systemd, y la GUI de escritorio PyQt6 (heredada de la
Pi 4 sin cambios, con desajustes de API conocidos ahí también). El detalle
de qué falta y por qué está en **[TODO_HW.md](TODO_HW.md)**.

## Hardware

- Raspberry Pi 5 Model B
- 2× cámara IMX219 (8 MP), CSI nativo — **sin** HAT multiplexor
- 2× Waveshare ESP32-S3-Matrix (8×8 WS2812B) para iluminación DPC/campo
  oscuro/Rheinberg
- Arduino (Uno/Nano) + DS18B20 + SCD30 + calefactor PWM + válvula solenoide
- Motor paso a paso para enfoque (control básico, en desarrollo)

Inventario detallado: **[docs/inventario.md](docs/inventario.md)**.
