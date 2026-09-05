# TODO-HW — validaciones pendientes con hardware real

Puntos del código marcados `# TODO-HW`, listados acá **por prioridad de
validación**: cosas que solo se pueden confirmar con la Pi, las cámaras,
las matrices o los motores presentes, no leyendo el código.

Orden recomendado: prioridad 1 en bloque, luego 2, luego 3-4. Los de
prioridad 1 pueden corromper datos o dañar hardware; los de prioridad 4 son
comodidad.

---

## Prioridad 1 — pueden corromper datos o dañar hardware

### 1.1 Formato RAW bajo PiSP — el riesgo más grave de toda la captura
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

### 1.5 Recorrido del eje Z sin finales de carrera
`codigo/MicroscopeOS/core/motor_focus.py`, `core/autofocus.py`

Ninguno de los dos ejes tiene final de carrera ni encoder: el software no
sabe dónde está el tope mecánico. `position` es relativa al arranque del
servidor, no una coordenada absoluta.

Consecuencia práctica: un autofoco con `rango` grande, o el joystick
mantenido apretado, puede llevar la plataforma **contra la muestra o
contra el tope del husillo**. El motor no tiene fuerza para romper gran
cosa a 450 mA, pero sí para rayar una muestra o forzar el objetivo.

Antes de dejar el autofoco corriendo solo en un timelapse largo:

1. Medir a mano, con el joystick, cuántos micropasos hay desde el foco
   hasta cada tope mecánico en el montaje real.
2. Ajustar el `rango` por defecto (3200 micropasos = 1 mm en la
   interfaz, 1600 = 0.5 mm en el timelapse) a algo que entre cómodo
   dentro de ese recorrido. Con el husillo T6×1 y 1/16 de paso, un
   micropaso son 0.31 µm.
3. Si el margen es chico, agregar límites blandos por software en
   `FocusMotorController` (`position` mínima/máxima) — hoy **no existen**.

### 1.6 Polaridad de las bobinas del segundo motor
`codigo/MicroscopeOS/test_motor_enfoque.py 1`

El eje de cam0 llegó a "vibrar sin avanzar" porque los dos cables de la
bobina A estaban cruzados entre sí: eso rompe la cuadratura entre fases
y no es lo mismo que invertir el sentido de giro. Al cablear el segundo
motor, correr `python3 test_motor_enfoque.py 1` **antes** de montarlo en
la plataforma y confirmar que gira limpio en los dos sentidos.

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

### 2.4 Qué métrica corresponde a tu muestra
`codigo/MicroscopeOS/core/autofocus.py`

El default es `metrica="dpc"` (Tenengrad sobre `(L−R)/(L+R)`), que es lo
correcto para **objetos de fase**: células vivas sin teñir. Está
verificado en simulación que sobre ese tipo de muestra la métrica cruda
tiene un valle en el foco y un barrido que la maximiza se va ~170 µm al
plano equivocado, mientras que la del DPC enfoca dentro de un par de
micras.

**El caso espejo también está verificado y hay que tenerlo presente:**
con una muestra que absorbe (teñida, pigmentada, material opaco) en el
foco las dos medias aperturas dan la misma imagen, el DPC se anula y su
Tenengrad tiene el valle. Si alguna vez se mira una muestra teñida, hay
que pasar `metrica="bruta"`.

Lo que falta comprobar en el microscopio real:

- Que la curva de Tenengrad sobre el DPC tenga pico distinguible con el
  ruido de lectura real del IMX219. En la simulación no hay ruido, y
  cerca del foco esa curva es MUY plana (0.1 % de variación en ±6 µm).
  Si en la práctica el ajuste fino salta de un lado a otro, promediar
  varias mediciones por plano antes que agrandar el rango.
- Que enfocar en campo claro/DPC deje bien enfocadas también las
  capturas en campo oscuro y Rheinberg del mismo ciclo.

### 2.4b Rango lineal y calibración de la ganancia
`codigo/MicroscopeOS/core/autofocus.py` (`calibrar_dpc`)

`δ = 2·Δz·tanθ` es lineal **sólo dentro de un rango**: lejos del foco el
corrimiento deja de crecer y la correlación termina perdiendo el
enganche. Calibrar con un barrido más ancho que ese régimen sesga la
ganancia, y el `r²` no lo delata (sigue saliendo alto porque la curva es
suave). En la simulación, calibrar con ±940 µm en vez de ±125 µm
sobreestimó la ganancia **4×** (100.7 contra 25.5 micropasos/px).

Consecuencias prácticas:

1. **Enfocar a ojo antes de calibrar.** El diálogo de la interfaz ya lo
   pide. Calibrar lejos del foco, con la respuesta ya saturada, sesga la
   pendiente igual que un rango demasiado ancho.
2. Medir en el microscopio real hasta qué desenfoque δ sigue creciendo
   proporcionalmente, y ajustar `amplitud` (1200 micropasos = 375 µm por
   defecto) para quedar adentro.
3. **Recalibrar al cambiar de objetivo:** la constante depende de la
   magnificación y del cono de iluminación. El archivo guarda la fecha,
   el `r²` y la escala en µm/px.

No hace falta preocuparse por el signo: la calibración mide la pendiente
con su signo, así que si `LEFT`/`RIGHT` estuvieran espejados respecto de
lo que uno cree (los flags `ROT90:FX:FY` difieren entre las dos placas),
el método se corrige solo. El `eje` es configurable (`lr` o `tb`): si la
muestra tiene textura marcadamente direccional, conviene el de más
contraste.

### 2.4c Confianza de la correlación: el signo NO es un error
`codigo/MicroscopeOS/core/autofocus.py` (`medir_par`)

Con un objeto de fase las dos medias aperturas dan contraste de signo
opuesto — eso es justamente lo que hace visible la fase — así que la
superficie de correlación queda globalmente invertida y OpenCV devuelve
una **respuesta negativa** aunque la posición del pico sea correcta. El
código toma el valor absoluto a propósito: sin eso el método se
rechazaría a sí mismo justo en las muestras para las que existe. Lo
detectó la simulación (respuesta cruda ≈ −0.94 con el foco bien medido).

Lo que sí descarta una respuesta cerca de cero es un campo vacío o sin
textura, y ahí el pico efectivamente no significa nada.

### 2.5 Costo en tiempo del autofoco dentro del timelapse
`codigo/MicroscopeOS/core/timelapse.py`

Por el método DPC son ~2 s por cámara (4 imágenes de preview y 4
cambios de iluminación). Por barrido son ~20-30 s (13 puntos gruesos + 2
pasadas finas, con reconfiguración de la cámara a modo preview y
vuelta): con las dos cámaras y `autofocus_cada=1`, un intervalo de 60 s
se come casi entero en enfocar. Medir el tiempo real en el log —queda
anotado en cada línea de autofoco— y, si se está cayendo al barrido,
calibrar el DPC antes que subir `autofocus_cada`.

### 2.6 Diámetro de célula del conteo — la única perilla que hay que acertar
`codigo/MicroscopeOS/core/analisis.py`

`diametro_px` fija la escala a la que se buscan las células, declarado
para una imagen de 640 px de ancho (el código lo reescala solo para las
fotos a resolución nativa). Medido contra campos sintéticos, el conteo
aguanta bien de 1× a 3× el valor correcto, pero **si se lo pone a la
mitad del tamaño real cuenta el doble**: deja de fundir los dos lóbulos
del relieve DPC y cuenta cada uno como una célula.

Cómo ajustarlo: con una muestra real, "Contar en una foto nueva" y
mirar el PNG marcado. Si cada célula tiene dos marcas, subirlo; si
varias células vecinas caen dentro de una sola marca, bajarlo. El valor
por defecto (14) sale de los campos sintéticos, no de una muestra real
en este objetivo, así que **hay que revisarlo la primera vez**.

Verificar también el campo `descartados` de la respuesta: si es alto y
`n` es bajo, el diámetro no tiene nada que ver con la muestra. Es la
única forma en que este método falla en silencio.

### 2.6b Iluminación del conteo en vivo — confirmar con muestra real
`codigo/MicroscopeOS/core/analisis.py` (`ContadorEnVivo`)

El conteo en vivo mira **un frame con una sola iluminación**: no puede
hacer DPC. Con células sin teñir (objetos de fase) en campo claro y en
foco, no hay nada que contar, y el contador reporta «campo vacío» sobre
un cultivo lleno. En simulación, con 40 células de fase en foco: campo
claro **0**, media apertura **40**, DPC **40**.

Por eso el conteo pone la matriz en `left` para medir (checkbox "Usar
media apertura al medir", encendido por defecto). En **modo manual**
sólo durante la medición, y después devuelve la iluminación a la que
estaba; en **automático** la deja puesta mientras el conteo siga
encendido. **Falta confirmarlo con una muestra real**: que con la matriz
en media apertura las células se vean y se cuenten en el vivo, y que en
campo claro efectivamente no. Si el montaje tiene bastante aberración o
la muestra queda algo desenfocada, en campo claro se van a ver igual
—por transporte de intensidad— pero el conteo va a inflarse (en
simulación, 67 en vez de 40 a 20 px de desenfoque), así que la media
apertura sigue siendo lo correcto.

Con muestras **teñidas** esto no aplica: absorben, se ven en campo claro
y se pueden contar con cualquier iluminación.

### 2.7 Umbral de campo vacío contra el ruido real del IMX219
`codigo/MicroscopeOS/core/analisis.py` (`nitidez_ruido`)

El filtro de campo vacío distingue "no hay nada" de "hay células" por
en qué banda de frecuencia está la energía: el ruido de lectura es
blanco y da `nitidez` ~40, mientras que cualquier imagen que pasó por el
objetivo está limitada por difracción y da 0.01-0.05. El corte está en
5, en medio de tres órdenes de magnitud de brecha.

Eso se midió con ruido gaussiano sintético. Falta confirmarlo con
capturas reales de la Pi en los dos extremos que importan: **tapa
puesta / luz apagada** (tiene que dar `vacio=True`) y **un pozo
confluente de verdad** (tiene que dar `vacio=False` y `confluente=True`).
El segundo es el que importa: si un pozo lleno se reportara como vacío,
la curva de población caería a cero justo cuando el cultivo está al
máximo.

Ojo con el ISP: la reducción de ruido del preview aplasta la banda fina
y baja `nitidez`, así que un preview muy denoiseado podría no detectarse
como vacío. `_controles_planos()` en `core/camera.py` la apaga cuando la
build de libcamera lo soporta — confirmar que efectivamente se aplicó
(`picam2.camera_controls`).

### 2.8 Costo del conteo por ciclo en la Pi
`codigo/MicroscopeOS/core/analisis.py` (`reducir`, `ancho_max`)

Analizar un TIFF a resolución nativa (3280 px) tarda ~7 s **en una
laptop**, porque los desenfoques son con sigmas enormes; por eso el
análisis se hace sobre una copia reducida a 1200 px (~0.2 s ahí mismo).
La Pi 5 es varias veces más lenta: medir el valor real en el log del
timelapse (cada línea de conteo trae los ms) y, si no entra en el
intervalo, subir `contar_cada` o bajar `ancho_max`. Reducir no cambia el
conteo, porque el diámetro se reescala con el ancho.

### 2.9 Autofoco aprendido: no hay modelo todavía
`codigo/MicroscopeOS/core/autofocus_ia.py`, `core/pila_foco.py`

Todo el camino está implementado y probado con un predictor inyectado,
pero **el modelo no existe**: hace falta grabar pilas de foco reales
(`POST /api/focus/pila`) y entrenarlo con
`extras/ia/entrenar_autofoco.py`. Sin `profiles/autofoco_ia.onnx` el
autofoco sigue siendo el analítico y nada de esto se activa.

Al grabar las pilas:

- la posición actual se toma como **el foco**, así que hay que enfocar
  bien antes (mejor todavía: correr el autofoco DPC y grabar desde ahí);
- hacen falta **muchas pilas de campos distintos**, no una larga. Con
  una sola, la red memoriza ese campo; el script avisa y separa la
  validación por pila justamente por eso;
- `onnxruntime` no está instalado en la Pi todavía (`pip install
  onnxruntime`); sin él, `AutofocoIA.disponible()` da False y se sigue
  usando el método analítico.

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

### 3.2b GPIO del segundo eje de enfoque
`codigo/MicroscopeOS/core/motor_focus.py` (`PINES_POR_CAMARA`)

El eje de cam1 usa BCM 26/19/13 (pines 37/35/33 del header), elegidos
por estar libres y pegados a los del primer eje. Confirmar que nada más
los toca antes de cablear:

```bash
pinctrl get 13,19,26
```

Y correr `python3 test_uart_only.py`, que ahora barre las 4 direcciones
del bus y dice cuál contesta: es la forma rápida de ver si MS1/AD0 y
MS2/AD1 del driver nuevo quedaron en la dirección 1 y no pisando la 0.

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
(ver docs/historia/MIGRACION_PI5.md §7).

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
