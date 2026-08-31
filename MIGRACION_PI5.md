# Migración a Raspberry Pi 5 — pasos a ejecutar en la Pi

Todo lo de este documento se ejecuta **en la Pi 5 nueva**, no en la laptop.
Asume Raspberry Pi OS Bookworm o Trixie de 64 bits.

Las variables del principio se usan en el resto del documento; ajústalas si
cambias de usuario o ruta.

```bash
export MS_USER=microscope1
export MS_DIR=/home/$MS_USER/MicroscopeOS
```

---

## 1. Sistema base

```bash
sudo apt update && sudo apt full-upgrade -y
sudo rpi-eeprom-update -a      # firmware al día: importante para el stack de cámara
sudo reboot
```

## 2. Paquetes del sistema

Estos van por **apt**, nunca por pip: son los que traen los bindings nativos
(libcamera, chip GPIO, aceleración de OpenCV).

```bash
sudo apt install -y \
  python3-picamera2 python3-opencv python3-numpy python3-serial \
  python3-rpi-lgpio python3-gpiozero python3-venv git
```

**GPIO — el punto que rompe si te equivocas.** `RPi.GPIO` clásica no funciona
en Pi 5 (southbridge RP1). El paquete `python3-rpi-lgpio` provee un módulo
llamado `RPi.GPIO` que sí funciona, así que `motortest.py` no necesita
cambios de código. Los dos paquetes son mutuamente excluyentes:

```bash
sudo apt remove -y python3-rpi.gpio     # si estuviera instalada
sudo apt install -y python3-rpi-lgpio
python3 -c "import RPi.GPIO as G; print(G.RPI_INFO)"   # debe funcionar
```

Nunca `pip install RPi.GPIO` dentro del venv: sobreescribe el shim y rompe
el GPIO en silencio.

Opcionales:

```bash
sudo apt install -y python3-matplotlib   # temperatura.png del timelapse
sudo apt install -y python3-pyqt6        # solo si quieres el modo GUI
```

## 3. `config.txt` — cámaras y UART

Editar `/boot/firmware/config.txt`. El archivo de referencia ya migrado está
en `configs/boot/config.txt` de este repo.

**Quitar** la línea del multiplexor (ya no hay mux):

```
dtoverlay=camera-mux-4port,cam0-imx219,cam1-imx219
```

**Poner** las dos cámaras en los puertos CSI nativos:

```
camera_auto_detect=0
dtoverlay=imx219,cam0
dtoverlay=imx219,cam1
```

**Quitar** `enable_uart=1` para liberar GPIO14/15 (ver sección 8).

## 4. `cmdline.txt` — liberar el UART

Editar `/boot/firmware/cmdline.txt` (una sola línea, sin saltos).
Quitar `console=serial0,115200`, conservar el resto.

Antes:
```
console=serial0,115200 console=tty1 root=PARTUUID=... cfg80211.ieee80211_regdom=MX
```
Después:
```
console=tty1 root=PARTUUID=... cfg80211.ieee80211_regdom=MX
```

⚠️ El `PARTUUID` es el de **tu** tarjeta nueva. No copies el del repo
(`957c2719-02`, de la microSD de la Pi 4) o la Pi no arranca.

Reinicia y verifica que las dos cámaras aparecen:

```bash
sudo reboot
rpicam-hello --list-cameras
```

Debes ver **dos** IMX219. Si solo ves una, revisa el cableado CSI antes de
tocar nada de software: el Pi 5 usa conectores FPC de 22 pines (más
estrechos que los 15 pines de la Pi 4), así que necesitas **cables
adaptadores 15→22**, no los de la Pi 4.

## 5. Interfaces

`i2c-dev` se carga igual que en Pi 4:

```bash
sudo cp configs/modules-load.d/modules.conf /etc/modules-load.d/
```

`dtparam=i2c_arm` y `dtparam=spi` están comentados en `config.txt` y se
dejan así: nada del proyecto los usa desde la Pi. El DS18B20 cuelga del
Arduino, no de la Raspberry, por eso tampoco hay overlay `w1-gpio`.

## 6. Código y venv

```bash
sudo adduser $MS_USER          # si no existe
git clone <tu-repo> $MS_DIR    # o copiar codigo/MicroscopeOS/ a $MS_DIR
cd $MS_DIR

# --system-site-packages es OBLIGATORIO: picamera2, cv2, numpy, RPi.GPIO
# y pyserial vienen de apt y sin esta flag el venv no los ve.
python3 -m venv --system-site-packages venv
source venv/bin/activate

pip install fastapi uvicorn pydantic starlette tifffile anyio h11 \
            typing_extensions typing_inspection annotated-types annotated-doc

# Comprobar que el venv ve los paquetes del sistema:
python3 -c "import picamera2, cv2, numpy, serial, RPi.GPIO; print('todo visible')"
```

Si ese último comando falla, el venv se creó sin la flag. Bórralo y repite.

## 7. Matrices de iluminación (ESP32-S3)

Reglas udev (ya traen VID:PID y seriales reales, no hay nada que rellenar):

```bash
sudo cp configs/udev/99-microscopeos-matriz.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger --subsystem-match=tty
ls -l /dev/matriz_cam*
```

Deben aparecer `matriz_cam0` y `matriz_cam1`. El usuario tiene que estar en
el grupo del device (`GROUP="uucp"` en la regla):

```bash
sudo usermod -aG uucp $MS_USER    # cerrar sesión y volver a entrar
```

Verificar que cada placa lleva su calibración correcta:

```bash
python3 -c "
import sys; sys.path.insert(0,'$MS_DIR')
from core.illumination import IlluminationController
for p in ('/dev/matriz_cam0','/dev/matriz_cam1'):
    l = IlluminationController(port=p); print(p, l.id()); l.close()
"
```

Esperado:
```
/dev/matriz_cam0 ID:MATRIZ:A0:F2:62:EB:21:A4:ROT90:FX0:FY1
/dev/matriz_cam1 ID:MATRIZ:A0:F2:62:EB:2B:48:ROT90:FX1:FY0
```

Si los seriales salen cruzados, están intercambiadas físicamente: cambia los
symlinks en la regla udev, **no** el código.

⚠️ **Consumo.** Ver TODO_HW.md prioridad 1 antes de subir el brillo. Las
matrices nuevas son 8×8 (64 LEDs) frente a las 5×5 (25 LEDs) de las RP2040:
el brillo 80% que venía de Pi 4 son 204/255, muy por encima de lo que
aguanta la alimentación USB.

## 8. GPIO14/15 libres

Con `enable_uart=1` fuera de `config.txt` y `console=serial0` fuera de
`cmdline.txt`, GPIO14 (TXD) y GPIO15 (RXD) quedan disponibles. Confirmar:

```bash
pinctrl get 14,15      # deben salir como entradas sin función especial
```

Si los quieres como UART para otro periférico (en vez de libres), en Pi 5 el
UART de usuario es `/dev/ttyAMA0` con `dtparam=uart0=on`. Pero entonces
vuelven a estar ocupados.

## 9. Servicio systemd

```bash
sudo cp configs/systemd/microscopeos.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now microscopeos.service
systemctl status microscopeos.service
journalctl -u microscopeos.service -f
```

`microscope.service` (modo GUI) **no se habilita**: ya estaba deshabilitado
en la Pi 4.

Interfaz web en `http://<ip-de-la-pi>:8000`.

## 10. Validación en orden

Ejecutar en esta secuencia; cada paso depende del anterior.

```bash
cd $MS_DIR && source venv/bin/activate

rpicam-hello --list-cameras      # 1. las dos cámaras existen
python3 test_luz.py              # 2. matrices responden
python3 test_captura.py          # 3. captura RAW  <-- el paso crítico
python3 test_timelapse.py        # 4. ciclo completo
```

`test_captura.py` es el que decide si la migración funcionó. Debe imprimir
`Dimensiones: (2464, 3280)`, `Tipo de dato: uint16` e `Imagen con contenido
válido`. Si sale con otras dimensiones, `ndim != 2`, o una imagen
uniforme/ruidosa, el cambio de ISP alteró el formato raw: ver TODO_HW.md
prioridad 1, **no ajustes el código a ciegas**.

---

## Diferencias de hardware que no son de software

| | Pi 4B | Pi 5 |
|---|---|---|
| Conector CSI | 15 pines, uno | **22 pines, dos** — necesitas cables 15→22 |
| Cámaras | 2 vía mux Arducam V2.2 | 2 nativas, sin mux |
| Alimentación | 5V/3A | **5V/5A (PD)** para periféricos USB a tope |
| GPIO | BCM2711 | RP1 — `rpi-lgpio` obligatorio |

El mux Arducam, sus cables y su alimentación **se retiran del montaje**.
