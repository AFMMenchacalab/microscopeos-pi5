# Acceso remoto al microscopio: túnel y seguridad

*Configurado el 2 de octubre de 2026.*

## Resumen

La interfaz del microscopio (MicroscopeOS, en la Raspberry Pi
`Microscopio2`) se puede abrir desde cualquier lugar con internet en
**https://microscopio.lmimenchacalab.com/ui**. Desde ahí se ve el video en
vivo, se mueve el enfoque y se controla el equipo. Solo entran las
personas autorizadas del laboratorio.

## Cómo funciona

La red de la UANL no deja que nadie de fuera se conecte a los equipos del
laboratorio, porque no hay puertos de entrada abiertos. Por eso la
conexión se hace al revés:

1. En la Pi corre un programa llamado **cloudflared**. Al encenderse, abre
   por su cuenta una conexión **de salida** hacia Cloudflare, igual que
   cuando un navegador entra a una página web. A esa conexión permanente
   se le llama **túnel** (nombre: `lmi-gateway`).
2. Alguien escribe `microscopio.lmimenchacalab.com` en su navegador y la
   petición llega a Cloudflare, no a la Pi.
3. Cloudflare revisa primero que la persona esté autorizada (ver
   [Seguridad](#seguridad)).
4. Si sí lo está, Cloudflare manda la petición por el túnel a la Pi, que
   contesta con la interfaz.

```
Navegador ──► Cloudflare (revisa acceso) ──► túnel cifrado ──► Pi del microscopio
                                                                (localhost:8000)
```

**Ventajas:**

- No se abrió ningún puerto en la red de la universidad.
- La dirección IP del laboratorio no queda expuesta.
- Funciona aunque la Pi esté detrás del router del laboratorio.

## Seguridad

- **Cloudflare Access protege todo el sitio.** MicroscopeOS no trae
  contraseña propia, así que Cloudflare hace de portero: nada llega a la
  Pi sin pasar por el login. Eso incluye la página, el video, la
  telemetría en vivo y los comandos de movimiento.
- **Inicio de sesión con código por correo.** La persona escribe su
  correo. Si está en la lista, le llega un código de un solo uso para
  entrar. No hay contraseñas que se puedan robar o adivinar.
- **Lista cerrada de miembros.** Solo entran los correos de la política
  *Miembros LMI*. Cualquier otro correo es rechazado.
- **Probado sin sesión iniciada.** La página principal, la interfaz, el
  video, los datos en vivo y un comando de movimiento devolvieron la
  redirección al login, sin dar acceso.
- **Todo va cifrado (HTTPS)**, desde el navegador hasta Cloudflare y por
  el túnel hasta la Pi.
- El panel de administración de la página del laboratorio tiene la misma
  protección.

### Lo que el túnel no cubre: la red local

MicroscopeOS escucha en `0.0.0.0:8000` (`codigo/MicroscopeOS/run_web.py`),
así que **desde la misma red del laboratorio** se puede abrir
`http://<ip-de-la-pi>:8000/ui` sin pasar por Cloudflare ni por el login.
Es lo que permite usarlo en el laboratorio sin internet, y mientras en
esa red solo esté gente del laboratorio es aceptable. Si se comparte con
más personas, hay que cerrarlo: no basta con escuchar solo en
`127.0.0.1`, porque el envío a la PC encuentra la computadora por
difusión UDP en la red local (`core/envio.py`).

## Cómo entrar

1. Abrir **https://microscopio.lmimenchacalab.com/ui**. También se llega
   desde el panel del sitio del laboratorio, en la tarjeta *Microscopio*.
2. Escribir tu correo autorizado.
3. Copiar el código que llega al correo.
4. Listo, se abre la interfaz.

Para dar acceso a alguien nuevo, se agrega su correo a la política
*Miembros LMI* en Cloudflare Zero Trust (Access › Applications ›
*Microscopio LMI*).

## Detalles técnicos

| Concepto | Valor |
|---|---|
| Equipo | Raspberry Pi 5 (8 GB) `Microscopio2`, por Wi-Fi |
| Servicio | `cloudflared` como servicio del sistema; arranca solo al encender la Pi |
| Túnel | `lmi-gateway`, protocolo QUIC (puerto de salida 7844) |
| Ruta | `microscopio.lmimenchacalab.com` → `http://localhost:8000` (MicroscopeOS) |
| Control de acceso | App de Access *Microscopio LMI*, política *Miembros LMI*, código por correo |
| Dominio | `lmimenchacalab.com` en Cloudflare (plan gratuito) |

Un ejemplo de la configuración del túnel, sin credenciales, está en
[`configs/cloudflared/config.yml.ejemplo`](../configs/cloudflared/config.yml.ejemplo).

### Volver a instalar el túnel en otra Pi

```bash
# cloudflared para arm64 (Raspberry Pi OS de 64 bits)
curl -L -o cloudflared.deb \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb
sudo dpkg -i cloudflared.deb

cloudflared tunnel login                 # abre el navegador, elegir lmimenchacalab.com
cloudflared tunnel list                  # el túnel lmi-gateway ya existe
# copiar configs/cloudflared/config.yml.ejemplo a ~/.cloudflared/config.yml
# y poner el ID del túnel; después instalarlo como servicio:
sudo cloudflared --config ~/.cloudflared/config.yml service install
sudo systemctl enable --now cloudflared
```

El archivo `<ID-del-túnel>.json` que crea `cloudflared` es la credencial
del túnel: **no se sube al repositorio**. Si se pierde, se vuelve a
generar con `cloudflared tunnel token --cred-file ~/.cloudflared/<ID-del-túnel>.json lmi-gateway`.
