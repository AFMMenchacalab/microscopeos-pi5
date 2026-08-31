# TODO-HW — validaciones que requieren la Pi 5 y el hardware conectado

Nada de esta migración se pudo probar: la adaptación se hizo sin Pi 5, sin
cámaras, sin matrices y sin motor. Cada punto marcado con `# TODO-HW` en el
código está listado aquí, **ordenado por prioridad de validación**.

Orden recomendado: prioridad 1 en bloque, luego 2, luego 3-4. Los de
prioridad 1 pueden corromper datos o dañar hardware; los de prioridad 4 son
comodidad.

---

## Prioridad 1 — pueden corromper datos o dañar hardware

### 1.1 Formato RAW bajo PiSP — el riesgo más grave de toda la migración
`core/camera.py` (`RAW_FORMAT`, y el bloque de debayer en `capture_image`)

El Pi 4 usa Unicam; el Pi 5 usa CFE/PiSP. La cadena
`request.make_array("raw")` → `.view(np.uint16)` → `cv2.COLOR_BayerRG2GRAY`
asume un empaquetado y un stride concretos.

**Por qué es lo primero:** si el formato cambia, esto **no lanza ninguna
excepción**. Escribe un TIFF de basura y el timelapse sigue corriendo 48
horas guardando ruido. No hay forma de detectarlo salvo mirando la imagen.

```bash
python3 -c "
from picamera2 import Picamera2
p = Picamera2(camera_num=0)
for m in p.sensor_modes: print(m)
"
python3 test_captura.py
```

Verificar: `shape == (2464, 3280)`, `dtype == uint16`, imagen con contenido.
Si `make_array('raw')` devuelve `SBGGR10_CSI2P`, el raw viene empaquetado a
10 bits en 5 bytes por cada 4 píxeles y el `.view(np.uint16)` produce basura:
hay que desempaquetar antes.

**No ajustar a ciegas.** Imprimir primero `raw.shape`, `raw.dtype` y
`request.get_metadata()` y decidir con esos datos.

### 1.2 Orden del patrón Bayer
`core/camera.py`, línea del `cv2.cvtColor`

El buffer se pide como `SBGGR10` (patrón **BG**) pero se debayerea con
`COLOR_BayerRG2GRAY` (**RG**). Esa inconsistencia venía de la Pi 4, donde
daba imagen correcta. Con el ISP nuevo hay que reconfirmarla.

Capturar un objetivo de contraste conocido y comparar con una captura buena
de la Pi 4. Si el gris sale invertido o con artefactos de rejilla, probar
`COLOR_BayerBG2GRAY`.

### 1.3 Consumo de las matrices 8×8 por USB
`run_web.py` (`set_brightness(80)`), `test_timelapse.py`, `core/illumination.py` (`max_value`)

Las RP2040 tenían 25 LEDs; las ESP32-S3-Matrix tienen **64**. El brillo 80%
heredado de Pi 4 son 204/255. El README de la placa recomienda **no pasar de
~46 en FULL (≈18%) ni ~92 en medios patrones (≈36%)** con alimentación USB:
`FULL:255` son ~3.8 A y un puerto USB da 0.5-0.9 A.

Se dejó el valor de Pi 4 a propósito, para no alterar la exposición de las
capturas sin medir. **Medir consumo real** y luego decidir entre bajar
`set_brightness` o poner `max_value` en el constructor.

Las pruebas del README aguantaron `FULL:255` sin reiniciarse, pero es margen
que no conviene explotar en un timelapse de 48 h sin supervisión.

### 1.4 Reset por DTR/RTS al abrir el puerto
`core/illumination.py`, constructor

En el USB-Serial/JTAG del ESP32-S3 la combinación DTR+RTS es la secuencia de
reset, y pyserial afirma DTR al abrir. El código abre con `dtr=False`,
`rts=False`, `dsrdtr=False` puestos **antes** de `open()`, más
`reset_input_buffer()` y 300 ms de espera.

Verificar que abrir el puerto **no** reinicia la placa (si reinicia, la
matriz parpadea y se apaga). Si la primera orden tras conectar devuelve
`ERR` o da timeout, subir los 300 ms.

---

## Prioridad 2 — cambian resultados científicos

### 2.1 Diafonía óptica entre los dos canales
`core/timelapse.py` (`_capturar_simultaneo`), `core/camera.py` (`capture_both`)

La captura simultánea enciende **las dos matrices a la vez**. Solo es válida
si la matriz de cam0 no ilumina el sensor de cam1 ni al revés.

**Está desactivada por defecto** (`simultaneo=False`): el comportamiento por
defecto es idéntico al de Pi 4. Para validar:

1. Tapar la muestra de cam1, encender solo la matriz de cam1.
2. Capturar con cam0. Si cam0 ve señal, hay diafonía.
3. Repetir al revés.

Sin diafonía, activar `simultaneo=True` y el ciclo DPC pasa de 8 capturas
secuenciales a 4 pasos paralelos.

### 2.2 Estabilización tras reconfigurar la cámara
`core/camera.py`, `_ensure_mode` (`time.sleep(0.3)`)

0.3 s heredados de Pi 4 para que `ExposureTime`/`AnalogueGain` se apliquen.
El pipeline de Pi 5 es otro. Si las primeras capturas de cada ciclo salen con
exposición distinta a las siguientes, subirlo o esperar por metadata real
(`request.get_metadata()["ExposureTime"]`).

### 2.3 Orden de cam0/cam1
`core/camera.py`, `_instance`

Confirmar con `rpicam-hello --list-cameras` que `camera_num=0` es el sensor
cableado al conector CAM0 y que corresponde a `/dev/matriz_cam0`. Si están
cruzados, intercambiar **aquí** o en la regla udev, no repartido por el
código.

Con el mux este mapeo lo definía el overlay; ahora lo define el cableado
físico.

---

## Prioridad 3 — funcionalidad que puede no arrancar

### 3.1 Las dos cámaras detectadas sin mux
`configs/boot/config.txt`

```bash
rpicam-hello --list-cameras     # deben salir dos IMX219
```

Si solo sale una: revisar cables (el Pi 5 usa FPC de **22 pines**, hacen
falta adaptadores 15→22) antes de tocar software. Si el cableado está bien,
probar `camera_auto_detect=1` quitando los dos `dtoverlay=imx219`.

### 3.2 GPIO que liberaba el mux, y colisión con el motor
`configs/boot/config.txt`, `codigo/MicroscopeOS/motortest.py`

No se pudo determinar qué GPIOs ocupaba `camera-mux-4port` sin la Pi:

```bash
dtoverlay -h camera-mux-4port
pinctrl get 20,21          # los que usa motortest.py
```

Relevante porque `motortest.py` usa BCM 20/21 y podían estar en conflicto en
Pi 4. Ahora el overlay ya no está.

### 3.3 GPIO14/15 realmente libres
`configs/boot/config.txt`, `configs/boot/cmdline.txt`

Se quitaron `enable_uart=1` y `console=serial0,115200`. Confirmar:

```bash
pinctrl get 14,15
```

**Corrección al brief:** el mux nunca usó GPIO14/15. Lo que los ocupaba era
la consola serie. Quedan libres por haberla quitado, no por quitar el mux.

### 3.4 Timing del motor con rpi-lgpio
`codigo/MicroscopeOS/motortest.py`

`delay = 0.005` es temporizado por software. `rpi-lgpio` tiene otra latencia
por llamada que la `RPi.GPIO` nativa. Si el motor pierde pasos o suena
distinto, recalibrar.

---

## Prioridad 4 — comodidad

### 4.1 Symlinks udev de las matrices
`configs/udev/99-microscopeos-matriz.rules`

La regla ya trae VID:PID (`303a:1001`) y seriales reales. **No hay nada que
rellenar.** Solo comprobar:

```bash
ls -l /dev/matriz_cam*
```

Y que cada placa lleva su calibración con `IlluminationController.id()`
(ver MIGRACION_PI5.md §7).

### 4.2 Permisos del grupo `uucp`
La regla nueva usa `GROUP="uucp", MODE="0660"` (la de RP2040 no ponía
grupo). El usuario del servicio tiene que estar en `uucp`:

```bash
sudo usermod -aG uucp microscope1
```

Si no, el servicio arranca y falla al abrir `/dev/matriz_cam0`.

### 4.3 `PARTUUID` en cmdline.txt
`configs/boot/cmdline.txt` conserva `957c2719-02`, el de la microSD de la
Pi 4. **No copiar tal cual**: usar el de la tarjeta nueva o la Pi no arranca.
El archivo del repo es referencia de qué parámetros conservar, no para
copiarlo entero.

### 4.4 Restaurar firmware de fábrica de las matrices
`codigo/extras/matrices_esp32s3_matrix/`

Los dos `.bin` **no son idénticos entre sí**, pese a lo que dice el README de
la matriz:

| | `...21A4.bin` (cam0) | `...2B48.bin` (cam1) |
|---|---|---|
| esp-idf | v4.4.7 | **v4.4.5** |
| Build | Mar 5 2024 | **Jun 12 2023** |
| Máquina | `/home/alexflores/.platformio/…` | `C:\Users\ouyongqin\…` |

Verificado: ninguno contiene el protocolo DPC (`OK:`, `MATRIZ`, `ROT`), así
que ambos sirven para volver a fábrica. Pero no restauran el mismo firmware.

**Falta el sketch `dpc_matrix`**: el README documenta cómo compilarlo pero el
fuente no está en el repo. Sin él no se puede recompilar ni recalibrar.
