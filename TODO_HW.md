# Pendientes con el hardware

Lista corta de lo que falta comprobar en el microscopio real. El equipo
ya lleva **más de 24 h seguidas** con timelapse, autofoco e incubadora
funcionando juntos, así que lo básico está confirmado en uso.

La versión anterior, con el porqué de cada punto, está en
[docs/historia/TODO_HW_detallado.md](docs/historia/TODO_HW_detallado.md).
Los números que citan los comentarios del código (`TODO_HW 1.5`,
`2.4b`, …) se refieren a ese archivo.

## Ya funciona (confirmado en uso)

- Captura raw de 16 bits y debayer en la Pi 5 (antes 1.1 y 1.2).
- Las dos cámaras detectadas, cada una con su matriz (3.1, 2.3).
- Matrices: abrir el puerto no las reinicia; udev y permisos bien (1.4, 4.1, 4.2).
- Los dos motores de enfoque, el UART compartido y los GPIO (1.6, 3.2–3.4).
- Autofoco DPC dentro del timelapse, con la incubadora encendida (2.4, 2.5).
- El firmware `dpc_matrix` ya está en el repo (4.4).

## Pendiente

Por orden: lo de arriba puede dañar algo o arruinar un experimento sin
avisar; lo de abajo es afinar.

- [ ] **Consumo de las matrices por USB.** Medir con un amperímetro USB
      la corriente al brillo que usan de verdad. Las matrices de 64 LEDs
      pueden pedir más de lo que da un puerto USB; si pasa de ~0.5 A,
      bajar el tope `max_value` en `core/illumination.py`.
- [ ] **Recorrido del eje Z y homing.** No hay finales de carrera: la
      posición se pierde al reiniciar y nada impide llegar al tope.
      Probar el homing sin sensor (StallGuard del TMC2209, el motor
      detecta solo cuándo choca) en la rama `experimental/homing-stallguard`:
      bajar el objetivo con cuidado hasta el piso del microscopio y usar
      ese punto como cero. Si funciona, agregar límites por software.
- [ ] **Autofoco contra el foco visual.** Comprobar que donde el autofoco
      termina es donde uno enfocaría a ojo. Si siempre queda corrido
      hacia el mismo lado, agregar un desfase fijo. Revisar también que
      las fotos en campo oscuro y Rheinberg salgan enfocadas.
- [ ] **Diafonía entre los dos canales.** Solo si se quiere usar la
      captura simultánea (`simultaneo=True`): encender una sola matriz y
      ver si la otra cámara recibe luz.
- [ ] **Conteo de células con muestra real.**
  - Ajustar `diametro_px` (`core/analisis.py`): si cada célula sale
    con dos marcas, subirlo; si varias caen en una, bajarlo.
  - Confirmar que con la tapa puesta da "campo vacío" y que un pozo
    confluente no.
- [ ] **Tiempos por ciclo.** Mirar en `timelapse.log` cuánto tarda el
      autofoco y el conteo en cada ciclo; si no entran en el intervalo,
      espaciarlos (`autofocus_cada`, `contar_cada`).
- [ ] *(Opcional)* **Autofoco aprendido.** No hay modelo: grabar pilas
      de foco (`/api/focus/pila`) y entrenar con
      `extras/ia/entrenar_autofoco.py`.

Para el artículo hacen falta además las mediciones de validación
(resolución, repetibilidad del autofoco, estabilidad en 48 h,
viabilidad): ver [docs/articulo/LEEME.md](docs/articulo/LEEME.md).
