# Pruebas de MicroscopeOS (sin hardware)

Se ejecutan **en la laptop, sin hardware**. Sustituyen `picamera2`,
`tifffile` y `serial.Serial` por emuladores; el de la matriz implementa el
protocolo del firmware ESP32-S3 tal como lo describe
`codigo/extras/matrices_esp32s3_matrix/README.md`.

    cd tests
    SP=$PWD PROY=$PWD/../codigo/MicroscopeOS python3 test_migracion.py
    SP=$PWD PROY=$PWD/../codigo/MicroscopeOS python3 test_motores.py

Requiere `numpy`, `opencv` y `pyserial`. 48 + 98 comprobaciones.

**Qué validan:** que el protocolo serial nuevo se habla bien (incluido el
no-reset por DTR/RTS), que cada cámara mantiene una sola instancia
persistente sin reabrirse por captura, que `capture_both` es realmente
paralelo, y que el timelapse produce el mismo conjunto de TIFF en modo
secuencial y simultáneo.

`test_motores.py` cubre los dos ejes de enfoque: el datagrama UART del
TMC2209 sobre un bus compartido (dos drivers, un solo cable PDN, cada
uno en su dirección), el conteo de micropasos contra los flancos de STEP
que realmente se emiten, la compensación de backlash, el cambio de
resolución en caliente, el jog continuo con su watchdog, y los dos
métodos de autofoco contra una cámara simulada con la física del
problema: un objeto de **fase** que no absorbe (su contraste en campo
claro es proporcional al desenfoque y se anula en el foco) más una
textura de amplitud; bajo media apertura todo se proyecta de lado, con
un corrimiento que satura lejos del foco, y el desplazamiento subpíxel
se hace por rampa de fase en Fourier para no meter rizado artificial en
la curva de nitidez.

Con eso el test puede verificar lo que motiva el diseño y no sólo que el
código corra: que la métrica cruda tiene un **valle** en el foco y un
barrido que la maximiza se va 170 µm al plano equivocado; que la del DPC
tiene el pico donde corresponde; que con una muestra absorbente pasa
exactamente lo contrario; que la calibración recupera la constante que
la simulación usó para generar el corrimiento; y que calibrar fuera del
régimen lineal sobreestima la ganancia 4×. También comprueba
la degradación: con un solo eje cableado, o con ninguno, el servidor
tiene que arrancar igual.

**Qué NO validan:** nada de lo que está en `TODO_HW.md`. Los emuladores
asumen que el formato raw, el patrón Bayer, el consumo de las matrices y el
aislamiento óptico se comportan como en Pi 4 — que es exactamente lo que
hay que verificar en hardware.
