# MicroscopeOS — firmware DPC para Waveshare ESP32-S3-Matrix

Reemplazo de las placas RP2040 del sistema de iluminación DPC. Habla el mismo
protocolo de línea por serial, así que el backend no distingue una placa de otra.

## Estado

Validado sobre las dos placas el 2026-08-29: protocolo 16/16 casos correctos en
cada una, y los cuatro patrones DPC confirmados físicamente en el microscopio.

## Protocolo

    Entrada   PATRON:brillo\n        LEFT:128, FULL:255, OFF:0
    Salida    OK:PATRON:brillo\n
    Extra     ID\n  ->  ID:MATRIZ:A0:F2:62:EB:21:A4\n

    ERR:UNKNOWN_PATTERN:<token>    patrón no reconocido
    ERR:BAD_BRIGHTNESS:<token>     no numérico o fuera de 0-255
    ERR:BAD_FORMAT:<línea>         sin ':', campo vacío, o campos de más
    ERR:BAD_FORMAT:LINE_TOO_LONG   línea > 47 caracteres (se descarta entera)

Patrones: FULL, LEFT, RIGHT, TOP, BOTTOM, OFF.
El parseo es case-insensitive; el eco siempre sale en mayúsculas. Acepta CRLF y
espacios sobrantes. `OFF` admite cualquier brillo y lo devuelve en el eco.
Arranca a oscuras para que un reinicio no meta un destello en una captura.

El baudrate 9600 es virtual: en USB-CDC nativo se ignora. No hay que cambiarlo.

## Orientación (calibrada placa a placa, no deducida)

| Placa | Serie               | Symlink       | ROT | FLIP_X | FLIP_Y |
|-------|---------------------|---------------|-----|--------|--------|
| cam0  | A0:F2:62:EB:21:A4   | matriz_cam0   | 90  | 0      | 1      |
| cam1  | A0:F2:62:EB:2B:48   | matriz_cam1   | 90  | 1      | 0      |

Los valores del fuente son los de cam0. Para cam1 se sobreescriben al compilar,
sin tocar el fichero (ver más abajo).

Dos cosas que la calibración reveló y que no eran deducibles sobre el papel:

- El montaje ve la matriz **reflejada**: ninguna rotación pura corrige ambos
  ejes a la vez. Con rotación 90 y sin espejo, LEFT caía bien pero TOP caía
  abajo. Ese reflejo es del camino óptico y lo comparten las dos placas.
- cam1 tiene **los dos espejos invertidos** respecto a cam0, lo que equivale a
  un giro de 180 grados. Está montada boca abajo respecto a cam0, aunque a
  simple vista pareciera solo un reflejo lateral.

`ID` devuelve la calibración del binario cargado, para saber qué lleva cada
placa sin abrir el código:

    ID  ->  ID:MATRIZ:A0:F2:62:EB:21:A4:ROT90:FX0:FY1

Si remontas una placa, recalíbrala encendiendo LEFT y TOP y mirando la matriz.
No lo deduzcas sobre el papel: entre las dos placas nos costó tres iteraciones.

## Compilar y subir

    export PATH="$HOME/.local/bin:$PATH"
    FQBN="esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=cdc,FlashSize=4M,PSRAM=enabled"

    arduino-cli board list                          # confirmar el puerto real

    # cam0 (A0:F2:62:EB:21:A4) - usa los valores por defecto del fuente
    arduino-cli compile --fqbn "$FQBN" \
      --build-property "compiler.cpp.extra_flags=-DMATRIX_ROTATION=90 -DMATRIX_FLIP_X=0 -DMATRIX_FLIP_Y=1" \
      dpc_matrix
    arduino-cli upload -p /dev/ttyACM0 --fqbn "$FQBN" dpc_matrix

    # cam1 (A0:F2:62:EB:2B:48)
    arduino-cli compile --fqbn "$FQBN" \
      --build-property "compiler.cpp.extra_flags=-DMATRIX_ROTATION=90 -DMATRIX_FLIP_X=1 -DMATRIX_FLIP_Y=0" \
      dpc_matrix
    arduino-cli upload -p /dev/ttyACM1 --fqbn "$FQBN" dpc_matrix

Confirma siempre el puerto con `board list` antes de subir: ttyACM0/1 bailan.
Verifica con `ID` que subiste la calibración correcta a la placa correcta.

`CDCOnBoot=cdc` es obligatorio: el core trae `CDCOnBoot=default` (Disabled), y
sin el flag el sketch sube bien pero el puerto USB queda mudo.
`PSRAM=enabled` también hace falta (el default es `disabled`).

Toolchain: arduino-cli 1.5.1, core esp32:esp32 3.3.11, Adafruit NeoPixel 1.15.5.

## udev

`99-microscopeos-matriz.rules` ya tiene las dos placas. Instalar:

    sudo cp 99-microscopeos-matriz.rules /etc/udev/rules.d/
    sudo udevadm control --reload-rules && sudo udevadm trigger
    ls -l /dev/matriz_cam*

Para una placa nueva, leer su serie con:

    udevadm info -a -n /dev/ttyACM0 | grep -m1 'ATTRS{serial}'

## Potencia

Sin tope de brillo, por decisión explícita: se admite 0-255 entero.
FULL:255 son ~3.8 A a 5 V y un puerto USB da 0.5-0.9 A. En las pruebas la placa
aguantó FULL:255 sin reiniciarse, pero es un margen que no conviene explotar.
Con alimentación USB, mantente por debajo de ~46 en FULL y ~92 en medios patrones.

## Backend

Los dos `backup-waveshare-original-*.bin` son el firmware Waveshare de fábrica
de cada placa (esp-idf v4.4.7, 5-mar-2024). Restaurar con:

    esptool --port /dev/ttyACM0 --baud 921600 write-flash 0x0 \
      backup-waveshare-original-A0F262EB21A4.bin

Riesgos al migrar el cliente Python desde el RP2040:

- **VID:PID**: el ESP32-S3 es `303a:1001`, no `2e8a:*`. Usa los symlinks.
- **DTR/RTS**: pyserial afirma DTR al abrir, y en el USB-Serial/JTAG del
  ESP32-S3 la combinación DTR+RTS es la secuencia de reset. Abre con
  `dsrdtr=False` y pon `rts=False, dtr=False`, o cada conexión reinicia la placa.
- **Basura al conectar**: el ROM escribe mensajes de arranque por el mismo CDC.
  Haz `reset_input_buffer()` y espera ~300 ms antes del primer comando.
