# Memoria USB y envío de imágenes a la computadora

Dos funciones nuevas de la interfaz web (`/`):

1. **Memoria USB.** Al conectar una memoria, la interfaz muestra un aviso:
   *guardar el próximo timelapse en la memoria*, *copiar un timelapse ya
   hecho* o *nada*. El panel "Memoria USB" muestra el espacio libre y
   permite **expulsarla de forma segura**.
2. **Envío a computadora.** Con la casilla *Enviar cada imagen a la
   computadora*, cada foto del timelapse se manda a la PC apenas se guarda.
   En la PC, un programa la recibe y otro la segmenta con Cellpose-SAM en
   cuanto llega (~2–3 s por ciclo en la GPU, muy por debajo del intervalo
   entre fotos).

La imagen **siempre se guarda primero en la Pi o en la USB**; el envío es
una copia. Si se corta la red o la PC está apagada, las imágenes quedan en
cola y se mandan solas al reconectar, sin frenar el timelapse.

---

## Instalación en la Raspberry Pi (una sola vez)

En Pi OS **con escritorio** la memoria se monta sola y no hace falta nada.
En Pi OS **Lite**:

```bash
sudo apt install udisks2
sudo cp configs/polkit/50-microscopeos-usb.rules /etc/polkit-1/rules.d/
sudo systemctl restart polkit
```

La regla de polkit permite al usuario del servicio (`microscope2`) montar,
desmontar y apagar memorias USB sin contraseña. Si el servicio corre con
otro usuario, cambiarlo en el archivo.

No hay dependencias nuevas de Python: la detección lee `/proc/mounts` y
`/sys`, y el envío usa `urllib` de la biblioteca estándar.

## Instalación en la PC (repositorio `pipeline-migracion-celular`)

Dos programas, en dos terminales:

```bash
# 1) receptor: recibe lo que manda la Pi
venv/bin/python scripts/31_receptor_microscopio.py --token UNA_CLAVE

# 2) segmentación en vivo: procesa cada ciclo en cuanto está completo
venv/bin/python scripts/32_segmentar_en_vivo.py
```

- Las imágenes quedan en `datasets/microscopio_propio/<experimento>/cam<N>/`.
- Las máscaras en `~/microscopio_cache/masks/propio/<experimento>/cam<N>/`,
  con `ultima.png` (contornos de la última imagen, para revisar a ojo) y
  `segmentacion.csv` (células y segundos por ciclo).
- Si la PC tiene cortafuegos, abrir el puerto **8765/tcp** para la red local.

## Uso

1. En la PC, arrancar los dos programas.
2. En la interfaz de la Pi, panel **Envío a computadora**: dirección
   `http://<IP de la PC>:8765` y la misma clave → *Probar conexión*.
3. En **Timelapse**: elegir *Guardar en* (Raspberry o la memoria USB) y
   marcar *Enviar cada imagen a la computadora*.
4. Al terminar, **Expulsar** la memoria desde el panel antes de retirarla.

## Detalles de diseño

- **Seguridad.** La API solo acepta como destino una memoria USB que el
  monitor detectó (nunca una ruta que venga del navegador). En la PC, los
  nombres de experimento, cámara y archivo se validan con expresiones
  regulares y se exige la clave compartida (`X-Token`).
- **Integridad.** La Pi calcula el SHA-256 de cada archivo; la PC escribe
  en un temporal, verifica el hash y recién entonces renombra. El
  segmentador nunca ve una imagen incompleta.
- **Reenvío sin duplicados.** Antes de mandar, la Pi pregunta si la PC ya
  tiene el archivo con el mismo hash. `POST /api/envio/reenviar` con el
  nombre del timelapse completa lo que haya faltado tras un corte.
- **Espacio.** Antes de empezar un timelapse en la USB se estima lo que va
  a ocupar (~16 MB por TIFF) y se avisa si no alcanza.
- **Ciclos DPC.** La Pi manda al empezar `experimento.json` con el modo y
  sus sufijos (`_L _R _T _B`); la PC espera las cuatro fotos de cada tiempo
  antes de segmentar.
- **Qué imagen segmenta la PC** (`--entrada`): `suma` (equivale a campo
  claro; por defecto), `dpc` (magnitud del DPC de los dos ejes) o
  `combinada` (tres canales). Cuál funciona mejor se decide en el piloto,
  comparando contra contornos dibujados a mano.

## Pendiente

- La interfaz alternativa `/ui` (`index_uiux.html`) todavía no tiene estos
  paneles; solo la interfaz principal `/`.
- Probado en la PC con un timelapse simulado (cámara falsa), con un corte de
  red a mitad del experimento y con una memoria USB simulada. Falta probarlo
  en la Pi con la cámara real y una memoria física.
