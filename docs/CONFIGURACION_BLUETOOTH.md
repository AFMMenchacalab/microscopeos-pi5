# Configurar la red desde el teléfono (Bluetooth)

Para un microscopio **nuevo** (todavía sin Wi-Fi) o para **volver a
encontrarlo** si cambió de IP. Desde la app: *Microscopios → Buscar por
Bluetooth*.

## Cómo funciona

- El microscopio se anuncia por Bluetooth como **MOS-XXXX** (los últimos 4
  caracteres del número de serie de la Raspberry). En la app aparece como
  **MicroscopeOS-XXXX**.
- **Leer** su red y su IP se puede siempre: así la app lo vuelve a
  encontrar aunque el router le haya dado otra IP.
- **Configurar el Wi-Fi** solo se puede:
  - si el microscopio **no tiene red**, o en los **primeros 10 minutos**
    después de encenderlo (para cambiar de red: reiniciarlo y configurarlo
    en esos 10 minutos);
  - **nunca con un timelapse corriendo**;
  - después de confirmar el **código de colores**: la matriz de luz muestra
    3 colores seguidos (rojo, verde, azul, amarillo, magenta o cian) y en la
    app se tocan en el mismo orden. Prueba que quien configura está frente
    al microscopio. 3 errores bloquean 10 minutos; el código vence a los
    2 minutos.
- La contraseña del Wi-Fi viaja **cifrada**: el teléfono se empareja con el
  microscopio (Android pregunta «¿Vincular con MicroscopeOS?»).
- El Wi-Fi lo configura NetworkManager y queda guardado: al reiniciar, se
  conecta solo.

## Instalar en la Raspberry (una vez)

Con el microscopio sin timelapse:

```bash
# 1. El paquete de Python (en el venv del microscopio)
~/MicroscopeOS/venv/bin/pip install dbus-next==0.2.3

# 2. Bluetooth encendido
sudo systemctl enable --now bluetooth
sudo rfkill unblock bluetooth

# 3. Permiso para usar el Bluetooth (D-Bus de BlueZ)
sudo usermod -aG bluetooth microscope2

# 4. Permiso para configurar el Wi-Fi (NetworkManager)
sudo cp ~/MicroscopeOS/configs/polkit/51-microscopeos-red.rules /etc/polkit-1/rules.d/

# 5. Reiniciar el servicio
sudo systemctl restart microscopeos
```

En el registro (`journalctl -u microscopeos -f`) tiene que aparecer
`[bluetooth] anunciándose como MOS-XXXX`. Si falta algo, el microscopio
sigue funcionando igual y el registro dice qué falta.

Requiere Raspberry Pi OS Bookworm o posterior (usa NetworkManager).

## Probar sin la Raspberry

En una computadora con Bluetooth (no cambia su Wi-Fi):

```bash
cd codigo/MicroscopeOS
python3 probar_bluetooth.py --simular
```

Los colores del código aparecen en la terminal. Pruebas sin hardware:
`tests/test_configuracion_red.py`.
