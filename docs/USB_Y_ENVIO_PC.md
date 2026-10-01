# Memoria USB, envío a la computadora y respaldo en NAS

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

## Respaldo en NAS

Además de mandarlas a la PC, cada imagen se puede **respaldar en un NAS al
mismo tiempo** (casilla *Respaldar cada imagen en el NAS* del timelapse).

- **Van en paralelo:** el NAS tiene su propia cola; un NAS lento o apagado
  nunca frena el envío a la PC.
- **Preferencia a la PC:** si la PC está recibiendo bien y tiene imágenes
  esperando, el respaldo espera su turno para no competir por la red. Si la
  PC está caída, el respaldo avanza igual. Nada se pierde, solo se demora.
- **Cómo se conecta:** panel *Respaldo en NAS* → *Buscar NAS en la red*
  (encuentra los equipos que comparten carpetas, por mDNS y por el puerto
  445) → elegir el NAS → escribir la **carpeta compartida**, el **usuario** y
  la **contraseña** del NAS → *Probar escritura*.
- **Dos modos:** *carpeta compartida (SMB)*, que es lo que ofrecen Synology,
  QNAP, TrueNAS o Windows, sin montar nada ni usar sudo; o *carpeta ya
  montada en la Pi* (por ejemplo NFS en `/etc/fstab`).
- En el NAS queda `<carpeta>/<subcarpeta>/timelapse_<fecha>/cam0/…`. Cada
  archivo se escribe con nombre temporal y se renombra al final; lo que ya
  está con el mismo tamaño no se vuelve a copiar
  (`POST /api/nas/reenviar` completa un timelapse entero).
- La contraseña queda en `profiles/respaldo_nas.json` (permisos 600, fuera
  de git). Conviene crear en el NAS un usuario solo para esto, con permiso
  de escritura en una sola carpeta.
- Requiere `pip install smbprotocol` en el venv de la Pi (ya está en
  `docs/requirements.txt`).

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

Lo más simple es la **interfaz gráfica** (rama `experimental/interfaz-cuda`
del pipeline, ver su `INSTALAR.md`): `./iniciar_interfaz.sh` y *Iniciar
recepción*. También existe en consola:

```bash
# 1) receptor: recibe lo que manda la Pi (muestra el código de 6 dígitos)
venv/bin/python scripts/31_receptor_microscopio.py

# 2) segmentación en vivo: procesa cada ciclo en cuanto está completo
venv/bin/python scripts/32_segmentar_en_vivo.py
```

- Las imágenes quedan en `datasets/microscopio_propio/<experimento>/cam<N>/`.
- Las máscaras en `~/microscopio_cache/masks/propio/<experimento>/cam<N>/`,
  con `ultima.png` (contornos de la última imagen, para revisar a ojo) y
  `segmentacion.csv` (células y segundos por ciclo).
- Si la PC tiene cortafuegos, abrir en la red local el puerto **8765/tcp**
  (imágenes) y el **8766/udp** (búsqueda automática).

## Uso

1. En la PC, iniciar la recepción: muestra un **código de 6 dígitos**.
2. En la interfaz de la Pi, panel **Envío a computadora**: *Buscar
   computadoras en la red* → elegir la PC → escribir su código → *Probar
   conexión*. Se hace una sola vez (queda guardado).
3. En **Timelapse**: elegir *Guardar en* (Raspberry o la memoria USB) y
   marcar *Enviar cada imagen a la computadora*.
4. Al terminar, **Expulsar** la memoria desde el panel antes de retirarla.

## Cómo se encuentran la Pi y la PC

- **Búsqueda automática.** La Pi manda por difusión (UDP 8766) un mensaje
  de búsqueda a su red; cada PC con la recepción activa responde con su
  nombre, puerto y GPU. No hace falta saber la IP.
- **Emparejamiento con código.** La respuesta no incluye la clave: la PC
  muestra un código de 6 dígitos que se escribe en la Pi. Otro equipo de la
  red puede ver que la PC existe, pero no mandarle archivos.
- **Si la PC cambia de IP** (el router se la reasigna), tras varios fallos
  seguidos la Pi la vuelve a buscar por su **nombre** y actualiza la
  dirección sola, sin perder imágenes (quedan en cola).
- **Respaldo manual.** La difusión no cruza routers ni funciona en redes
  Wi-Fi que aíslan a los clientes: en ese caso se escribe la dirección que
  muestra la PC (`http://IP:8765`).

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
- Respaldo en NAS: probada la lógica completa (envío simultáneo a PC y NAS,
  preferencia a la PC, PC caída, reenvío sin duplicados, carpeta montada) y
  las llamadas SMB con un sustituto local. **Falta probar contra un NAS
  real**; la conexión SMB la hace la biblioteca smbprotocol, probada con
  Samba y Windows.
