# Pruebas de la migración a Pi 5

Se ejecutan **en la laptop, sin hardware**. Sustituyen `picamera2`,
`tifffile` y `serial.Serial` por emuladores; el de la matriz implementa el
protocolo del firmware ESP32-S3 tal como lo describe
`codigo/extras/matrices_esp32s3_matrix/README.md`.

    cd tests
    SP=$PWD PROY=$PWD/../codigo/MicroscopeOS python3 test_migracion.py

Requiere `numpy`, `opencv` y `pyserial`. 44 comprobaciones.

**Qué validan:** que el protocolo serial nuevo se habla bien (incluido el
no-reset por DTR/RTS), que cada cámara mantiene una sola instancia
persistente sin reabrirse por captura, que `capture_both` es realmente
paralelo, y que el timelapse produce el mismo conjunto de TIFF en modo
secuencial y simultáneo.

**Qué NO validan:** nada de lo que está en `TODO_HW.md`. Los emuladores
asumen que el formato raw, el patrón Bayer, el consumo de las matrices y el
aislamiento óptico se comportan como en Pi 4 — que es exactamente lo que
hay que verificar en hardware.
