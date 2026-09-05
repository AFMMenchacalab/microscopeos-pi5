# Pruebas de MicroscopeOS (sin hardware)

Se ejecutan **en la laptop, sin hardware**. Sustituyen `picamera2`,
`tifffile` y `serial.Serial` por emuladores; el de la matriz implementa el
protocolo del firmware ESP32-S3 tal como lo describe
`codigo/extras/matrices_esp32s3_matrix/README.md`.

    cd tests
    SP=$PWD PROY=$PWD/../codigo/MicroscopeOS python3 test_migracion.py
    SP=$PWD PROY=$PWD/../codigo/MicroscopeOS python3 test_motores.py
    SP=$PWD PROY=$PWD/../codigo/MicroscopeOS python3 test_analisis.py

Requiere `numpy`, `opencv`, `matplotlib` y `pyserial`.
48 + 98 + 96 comprobaciones.

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

`test_analisis.py` cubre el conteo de células, el filtro de calidad y el
andamiaje del autofoco aprendido. Los campos se sintetizan como objetos
de **fase**, así que en la imagen DPC cada célula aparece en relieve (un
lóbulo claro y uno oscuro, con el centro al mismo gris que el fondo) y
no como una mancha: probar sobre discos sólidos daría un resultado
bonito y engañoso, porque el error que este método evita —contar los dos
lóbulos por separado, es decir el doble de células— sólo aparece con la
forma real.

Verifica la exactitud del conteo de 5 a 400 células por campo, que la
viñeta y el gradiente de la matriz no inventen objetos, que un campo con
sólo ruido de lectura dé **cero** (y no los ~200 objetos que produce
Otsu si se lo deja decidir solo, en 8 y en 16 bits), que esa decisión
**no cambie con el diámetro configurado** —una regresión: con un
cociente entre bandas de frecuencia sí cambiaba, y subir el diámetro
declaraba vacía una foto real de la Pi—, que un cultivo confluente no se
reporte como campo vacío —el fallo que aplanaría la curva de población
justo en el máximo— sino marcado como cota inferior, que dos células
pegadas se separen y que un cúmulo que no se pudo partir se estime en
vez de descartarse. Comprueba además los dos modos del conteo en vivo:
que el **manual** mide una sola vez y deja el dibujo congelado —incluso
cuando después le llegan frames de campo claro, que es el punto del
modo—, que el **automático** mide una vez cada `periodo` y no 16 veces
por segundo, y que ninguno escribe sobre el buffer de la cámara. También
que el timelapse deja
`conteo.csv`, `poblacion.png` y `eventos.csv`, que el ajuste de
crecimiento recupera el tiempo de duplicación sintetizado, y que un
corte de corriente a mitad de una fila del CSV no desalinea la serie.

Fija además la física que decide si hay algo que contar, que no es del
algoritmo sino de la iluminación: con 40 células de fase **en foco**,
campo claro da **0** («campo vacío» sobre un cultivo lleno, porque un
objeto de fase se anula justo ahí), media apertura da **40** y el DPC da
**40**. Es la razón de que el conteo en vivo —que ve un solo frame y no
puede hacer DPC— ponga la matriz en media apertura.

Del autofoco aprendido prueba todo menos el modelo, que no existe hasta
grabar pilas en el microscopio: el preprocesado (mismo tensor con
entrada de 8 y de 16 bits, normalización por par que conserva la
diferencia entre mitades), la grabación de la pila con sus etiquetas
centradas en el foco, y el lazo completo medir → predecir → mover →
ajuste fino con un predictor inyectado, incluido el tope que impide que
una predicción disparatada mande la plataforma contra el objetivo.

**Qué NO validan:** nada de lo que está en `TODO_HW.md`. Los emuladores
asumen que el formato raw, el patrón Bayer, el consumo de las matrices y el
aislamiento óptico se comportan como en Pi 4 — que es exactamente lo que
hay que verificar en hardware.
