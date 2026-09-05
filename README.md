# MicroscopeOS

Controlador de un microscopio de dos cámaras para timelapses largos (hasta
48 h) de cultivos celulares vivos dentro de una incubadora, sobre
**Raspberry Pi 5**. Captura en **TIFF 16-bit** a resolución nativa del
sensor, ilumina en campo claro / campo oscuro / **DPC (Differential Phase
Contrast)** / **Rheinberg**, enfoca y reenfoca solo con un autofoco de dos
etapas, y registra temperatura y CO₂ sincronizados con cada ciclo de
captura.

Se opera desde una interfaz web (no hace falta instalar nada del lado del
usuario) y corre como servicio de arranque automático en la Pi.

## Por qué DPC

Las células vivas sin teñir no absorben luz: solo retrasan el frente de
onda que las atraviesa. Son objetos de **fase**, invisibles en campo claro
salvo por el contraste que aporta el propio desenfoque. El DPC ilumina con
mitades opuestas de una matriz de LEDs y resta las dos capturas —
`(I_izq − I_der)/(I_izq + I_der)` — convirtiendo ese gradiente de fase en
contraste de amplitud real, sin teñir ni dañar la muestra.

Esa misma iluminación oblicua es la base del autofoco: bajo media
apertura, un plano fuera de foco se proyecta *de lado*, y hacia lados
opuestos según qué mitad de la matriz esté encendida. Ese corrimiento
tiene signo — dice hacia dónde mover el eje, no solo cuánto está
desenfocado — así que enfocar no requiere barrer a ciegas.

## Arquitectura

```
Navegador  ──HTTP/SSE──>  FastAPI (run_web.py + server/api.py)
                                │
        ┌───────────────┬───────┼────────────────┬──────────────────┐
        │               │       │                │                  │
 CameraController  IlluminationController   FocusMotorController  TemperatureController
        │               │       │                │                  │
    picamera2         serial USB             UART compartido      serial USB
        │               │                        │                  │
  IMX219 cam0/cam1  2× ESP32-S3-Matrix     2× TMC2209 + NEMA11    Arduino
  (CSI nativo)      (DPC/oscuro/Rheinberg)  (un eje Z por cámara) (PID temp + CO₂)
                                                                    │      │
                                                                DS18B20  SCD30
                                                              calefactor válvula
```

- **Cámaras**: dos `Picamera2` persistentes (una instancia por cámara,
  abierta una sola vez), no se reabren en cada captura. Pueden disparar
  en paralelo (`capture_both`) porque cada una tiene su propio puerto CSI.
- **Iluminación**: no es GPIO directo — cada matriz es una placa
  ESP32-S3 aparte, identificada por regla udev
  (`/dev/matriz_cam0`/`/dev/matriz_cam1`) sin importar el orden de
  enumeración USB. Protocolo de línea por serial: `core/illumination.py`
  y el firmware en
  [`codigo/extras/matrices_esp32s3_matrix/`](codigo/extras/matrices_esp32s3_matrix/).
- **Enfoque**: un motor NEMA11 + driver TMC2209 por cámara, los dos
  drivers en un único bus UART (modo multi-esclavo, cada uno en su
  dirección) — cableado completo en el docstring de
  [`core/motor_focus.py`](codigo/MicroscopeOS/core/motor_focus.py).
- **Temperatura/CO₂**: lazo PID en un **Arduino separado**, no en la Pi,
  para que no dependa del timing de la Pi mientras escribe TIFFs de
  16 MB.

## Funciones

- **Captura raw 16-bit** sin pérdida, a la resolución nativa del sensor
  (recorte de stride del pipeline PiSP ya resuelto).
- **Cuatro modos de iluminación**: campo claro, DPC (4 capturas L/R/T/B),
  campo oscuro (anillo exterior) y Rheinberg (dos colores simultáneos,
  centro + anillo).
- **Timelapse** con captura secuencial o simultánea de las dos cámaras,
  telemetría de temperatura/CO₂ por ciclo, y reenfoque automático
  opcional cada N ciclos.
- **Enfoque motorizado por cámara**: joystick continuo desde la web
  (con watchdog: si se corta la conexión, el motor se frena solo),
  saltos de N micropasos, resolución ajustable en caliente entre 1/1 y
  1/256 de paso.
- **Autofoco de dos etapas**:
  1. *Salto grueso con dirección* — mide el corrimiento con signo entre
     las dos medias iluminaciones (correlación de fase, precisión de
     subpíxel) y corrige de una, sin barrer. Requiere calibrar una vez
     por objetivo (`/api/focus/calibrar`): la calibración ya deja la
     cámara enfocada, porque el foco es donde la recta
     corrimiento-vs-posición cruza el cero.
  2. *Ajuste fino* — cerca del foco el corrimiento se vuelve ruidoso, así
     que la métrica pasa a ser Tenengrad sobre la imagen DPC (que tiene
     pico en el foco, no valle, a diferencia de la imagen cruda con
     muestras vivas sin teñir): 7 planos + ajuste parabólico dan
     resolución por debajo del paso del motor.

  Sin calibrar, o si la correlación no engancha (campo vacío), cae solo
  a un barrido grueso-a-fino de respaldo. Todo movimiento final se
  alcanza siempre desde el mismo sentido, para no arrastrar el juego
  mecánico del husillo entre una medición y la siguiente.

  Hay además una tercera vía **opcional y todavía sin entrenar**: un
  regresor que estima el desenfoque directamente del par de medias
  aperturas, sin calibración y sin el límite del rango lineal. La
  inferencia y la grabación del dataset están implementadas; el modelo
  no existe hasta grabar pilas de foco en el microscopio
  (`/api/focus/pila` → `extras/ia/entrenar_autofoco.py`). Sin el archivo
  `profiles/autofoco_ia.onnx` nada de esto se activa y el autofoco
  sigue siendo el analítico.
- **Conteo de células**, sobre el vivo y sobre las capturas, dibujado
  encima del stream. Dos modos, porque las células no cambian en
  segundos: **manual** (por defecto) mide una vez al apretar el botón,
  devuelve la iluminación a como estaba y deja el número congelado en
  pantalla; **automático** vuelve a medir cada pocas décimas mientras
  enfocás o barrés el campo, a cambio de dejar la matriz en media
  apertura todo el rato. Ni uno ni otro analizan cada frame: la
  segmentación cuesta más que un frame de stream. En un timelapse se
  cuenta cada N ciclos y queda una curva de población con su tiempo de
  duplicación, más la marca de los ciclos donde algo cambió de golpe.

  La segmentación es clásica, no aprendida: se busca **energía local a
  escala de célula**, no brillo. En la imagen DPC una célula sale en
  relieve, con el centro al mismo gris que el fondo, así que cualquier
  umbral de brillo cuenta los dos lóbulos por separado y devuelve el
  doble de objetos. Trae además el filtro que distingue un campo vacío
  (o la luz apagada) de un cultivo confluente, que es el error que
  arruinaría una curva de crecimiento.

  **El vivo necesita iluminación oblicua.** El stream es un solo frame
  con un solo patrón encendido, así que no puede hacer DPC (eso son dos
  capturas), y en campo claro una célula sin teñir se anula justo en el
  foco: el contador diría «campo vacío» sobre un cultivo lleno. Medido
  sobre 40 células de fase en foco: campo claro **0**, media apertura
  **40**, DPC **40**. La media apertura lo resuelve sin costo —es un
  patrón estático, no baja los fps— así que el conteo pone la matriz en
  `left` para medir. En modo manual sólo durante ese instante y después
  la devuelve; en automático la deja puesta mientras cuente. Las fotos
  no tienen el problema, porque usan el par L/R completo.
- **Interfaz web** con vivo simultáneo de las dos cámaras, selector de
  modo de iluminación, panel de foco por eje, conteo de células y
  control de temperatura/CO₂ en tiempo real (SSE).

## Instalación

Raspberry Pi OS (Bookworm o Trixie) de 64 bits en Pi 5.

Las dependencias de hardware (`picamera2`, `opencv`, `numpy`, `pyserial`,
GPIO) van **con apt**, no con pip — los paquetes de pip no traen los
bindings nativos de libcamera ni del chip GPIO:

```bash
sudo apt install -y python3-picamera2 python3-opencv python3-numpy \
                    python3-serial python3-rpi-lgpio python3-gpiozero \
                    python3-venv git
```

El entorno virtual hereda esos paquetes del sistema:

```bash
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install -r docs/requirements.txt
```

Además hace falta, en `config.txt`/`cmdline.txt`: habilitar las dos
cámaras IMX219 (`camera_auto_detect=0` + overlays `imx219`), liberar
GPIO14/15 sacando la consola serie (`dtparam=uart0=on` para los drivers
de enfoque), y las reglas udev de las matrices de iluminación
(`configs/udev/`) y del usuario en el grupo `uucp`.

## Uso

```bash
cd codigo/MicroscopeOS
python3 run_web.py
# abrir http://<ip-de-la-pi>:8000
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
| `POST` | `/live/start/{cam}` · `/live/stop/{cam}` · `/live/stop` | Inicia/detiene el modo vivo, por cámara o todas |
| `GET` | `/live/stream/{cam}` | Stream MJPEG de esa cámara |
| `POST` | `/light/set` | Modo de iluminación (claro/DPC/oscuro/Rheinberg), brillo, colores |
| `POST` | `/light/on` · `/light/off` | Encendido/apagado directo |
| `POST` | `/capture/{cam}/{modo}` | Captura única en una cámara |
| `POST` | `/capture/both/{modo}` | Captura las dos cámaras **en paralelo** |
| `POST` | `/exposure` · `/brightness` | Exposición/ganancia y brillo de las matrices |
| `POST` | `/timelapse/start` · `/timelapse/stop` | Control de timelapse, con autofoco opcional por ciclo |
| `GET` | `/status` | Estado del timelapse y cámara activa |
| `POST` | `/api/focus/move` | Salto de N micropasos en un eje de enfoque |
| `POST` | `/api/focus/jog` · `/jog/stop` | Movimiento continuo del joystick (watchdog de 1,5 s) |
| `POST` | `/api/focus/config` | Resolución de micropasos y corriente, en caliente |
| `GET` | `/api/focus/status` | Posición, resolución y flags del driver de cada eje |
| `POST` | `/api/focus/auto` | Autofoco (DPC si la cámara está calibrada, barrido si no) |
| `POST` | `/api/focus/calibrar` | Calibra el autofoco DPC de una cámara (una vez por objetivo) |
| `POST` | `/api/focus/pila` | Graba una pila de foco (dataset para el autofoco aprendido) |
| `GET` | `/api/analisis/estado` | Último conteo en vivo de cada cámara |
| `POST` | `/api/analisis/config` | Enciende/apaga el conteo en vivo, su modo y sus parámetros |
| `POST` | `/api/analisis/medir` | Medición puntual: media apertura un instante, cuenta, restaura la luz |
| `POST` | `/api/analisis/foto` | Captura a resolución nativa, cuenta y guarda un PNG marcado |
| `POST` | `/api/analisis/timelapse` | Regenera curva de población y eventos de un experimento |
| `GET` | `/api/temperature/status` · `/stream` | Telemetría ambiental (JSON / SSE) |
| `POST` | `/api/temperature/setpoint` | Cambia el setpoint de temperatura |

### Salida de un timelapse

```
timelapse_20260831_121729/
├── cam0/img_20260831_121729_L.tif   # patrones DPC: _L _R _T _B (modo "dpc")
│   └── conteo_20260831_121729.png    # con conteo: PNG con las células marcadas
├── cam1/...
├── temperatura.csv                   # timestamp,ciclo,temperatura,setpoint,pwm
├── autofoco.csv                      # solo con autofoco: deriva del foco por ciclo
├── conteo.csv                        # solo con conteo: n de células y calidad por ciclo
├── poblacion.png                     # curva de población + tiempo de duplicación
├── eventos.csv                       # ciclos con saltos, caídas o pérdida de foco
└── timelapse.log
```

## Estructura

| Ruta | Contenido |
|---|---|
| `codigo/MicroscopeOS/core/` | Cámara, iluminación, timelapse, enfoque, autofoco, análisis de imagen, perfiles |
| `codigo/MicroscopeOS/server/` | API FastAPI + interfaz web estática |
| `codigo/MicroscopeOS/interfaces/` | GUI de escritorio en PyQt6 (no usada en producción; el modo activo es la web) |
| `codigo/extras/ia/` | Entrenamiento del autofoco aprendido (corre fuera de la Pi) |
| `codigo/extras/matrices_esp32s3_matrix/` | Firmware de las matrices de iluminación + protocolo |
| `codigo/extras/incubadora_temperatura/` | Sketches del PID de temperatura/CO₂ |
| `configs/` | Unidad systemd, reglas udev, `config.txt`/`cmdline.txt` de arranque |
| `tests/` | Suites sin hardware (emuladores de cámara, matrices y drivers) |
| `TODO_HW.md` | Validaciones pendientes que requieren hardware físico conectado |

## Tests

Corren en cualquier máquina, sin Pi ni hardware — sustituyen `picamera2`,
`tifffile` y `serial.Serial` por emuladores que hablan el protocolo real
(incluido el datagrama UART del TMC2209 sobre un bus compartido, y una
cámara simulada con la física del DPC: desenfoque, corrimiento con signo
bajo media apertura, y objetos de fase que se anulan en campo claro):

```bash
cd tests
SP=$PWD PROY=$PWD/../codigo/MicroscopeOS python3 test_migracion.py
SP=$PWD PROY=$PWD/../codigo/MicroscopeOS python3 test_motores.py
SP=$PWD PROY=$PWD/../codigo/MicroscopeOS python3 test_analisis.py
```

Detalle de qué cubre cada suite en [`tests/README.md`](tests/README.md).

## Hardware

- Raspberry Pi 5 Model B
- 2× cámara IMX219 (8 MP), CSI nativo
- 2× Waveshare ESP32-S3-Matrix (8×8 WS2812B) para iluminación
- 2× NEMA11 28HB30-401A + driver TMC2209, uno por cámara, sobre
  plataforma lineal T6×1 — bus UART compartido, cableado en
  `core/motor_focus.py`
- Arduino (Uno/Nano) + DS18B20 + SCD30 + calefactor PWM + válvula
  solenoide, para el control ambiental de la incubadora

## Estado

Validado en hardware real: las dos cámaras capturan TIFF 16-bit a
resolución nativa, los cuatro modos de iluminación responden y se
confirmaron visualmente en el microscopio, y los dos ejes de enfoque
mueven limpio con UART y motor probados de punta a punta. El detalle de
qué falta validar y por qué está en **[TODO_HW.md](TODO_HW.md)**.
