# MicroscopeOS en el teléfono

App nativa para controlar el microscopio **MicroscopeOS** (LMI Menchaca Lab)
desde el teléfono. Es un **segundo cliente de la misma API** que usa la
interfaz web (`codigo/MicroscopeOS/server/api.py`): no cambia nada en la
Raspberry.

Está hecha con **Flutter** (Dart 3), así que el mismo código sirve para
Android y para iPhone. Esta primera versión se compiló y probó **en Android**.
iPhone está preparado, pero falta compilarlo desde una Mac (ver más abajo).

- [Qué hace](#qué-hace)
- [Compilar e instalar](#compilar-e-instalar)
- [Servidor falso para desarrollar](#servidor-falso-para-desarrollar)
- [Pruebas](#pruebas)
- [Cómo está organizado el código](#cómo-está-organizado-el-código)
- [Rutas de la API que usa](#rutas-de-la-api-que-usa)
- [Lo que quedó fuera](#lo-que-quedó-fuera)
- [iPhone](#iphone)
- [Acceso desde internet](#acceso-desde-internet)
- [Pendientes del backend](#pendientes-del-backend)

---

## Qué hace

| Pantalla | Qué hay |
|---|---|
| **Microscopios** | Lista de microscopios guardados, agregar uno por IP (red del laboratorio) o `microscopio.lmimenchacalab.com` (internet, entrando con el correo), «Probar conexión» (`/api/version`). Recuerda el último. |
| **Inicio** | Quién controla el microscopio (y «Tomar el control»); incubadora en vivo (temperatura y CO₂, verde/naranja/rojo con las mismas tolerancias que la web) y una hoja para cambiarlas; el timelapse en curso (foto N, progreso, próxima foto, última foto de cada cámara, pausar/seguir, anotar, detener con confirmación). |
| **Vivo** | Video MJPEG de la cámara 0 o 1 con zoom y pantalla completa. Debajo, tres paneles: **Foco** (Subir/Bajar: un toque = un paso, mantener = movimiento continuo; paso de 1, 5 o 25 µm; altura; autofoco), **Luz** (encender, tipo de luz, brillo, colores del campo claro, del relieve y de Rheinberg —centro y anillo—, misma luz en las dos cámaras) y **Foto** (nombre del experimento, campo claro / relieve DPC / fondo negro / de colores, esta cámara o las dos). Con un timelapse tomando fotos muestra la última foto de cada ciclo. |
| **Nuevo timelapse** | Nombre, tipo de foto, cada cuánto, «durante N horas» o «hasta tal día y hora», cámaras, reenfocar y, en relieve DPC, qué hacer con las 4 fotos (calcular el relieve, borrar las originales, guardar la foto normal, la fase, la vista JPG); cuántas fotos se tomarán y cuánto espacio ocupará (igual que la web y que el servidor). |
| **Experimentos** | Lista con portada, fecha, fotos y tamaño → detalle con cuadrícula de miniaturas por cámara y notas → visor con zoom y **Compartir** (baja la copia JPEG con la marca de agua y abre el menú de compartir del teléfono). |
| **Ajustes** | Microscopios, cerrar la sesión de internet, tema (oscuro por defecto, claro o automático), control, «Abrir en la web», versión de la app y del microscopio. |

Las capturas de cada pantalla están en [`docs/capturas/`](docs/capturas/).

### Diseño

La idea es que se sienta como una app de Apple: controles de iOS
(Cupertino) en los dos sistemas, listas agrupadas, títulos grandes,
cápsulas de aviso arriba, vibración suave al tocar. **Oscuro por defecto**
(el microscopio se usa con poca luz) y **todo lo que se toca mide al menos
56 puntos** (Apple pide 44): se usa con guantes. Todos los textos están en
`lib/textos.dart`, en un solo lugar, para poder traducirla.

### Seguridad del foco

Un motor que sigue girando puede romper el portaobjetos o el objetivo. El
movimiento continuo (`lib/features/vivo/control_jog.dart`):

- manda `POST /api/focus/jog` cada 250 ms mientras el dedo está apretado
  (el servidor para el motor solo si deja de recibirlos 1.5 s);
- manda `POST /api/focus/jog/stop` **enseguida** al soltar, al cancelarse el
  gesto, al cambiar de pestaña o abrir otra pantalla encima, al salir de la
  pantalla, cuando la app pasa a segundo plano, cuando se pierde la conexión
  y cuando arranca un timelapse;
- si un jog devuelve error deja de reenviar y manda el stop;
- nunca tiene dos jog en vuelo (con la red lenta no se apilan), y si un jog
  que salió antes de soltar contesta después del stop, manda otro stop.

Todo eso tiene pruebas (`test/vivo/control_jog_test.dart` y
`test/widgets/vivo_test.dart`), y la prueba de punta a punta comprueba con
el servidor falso que el motor quedó parado al soltar.

---

## Compilar e instalar

### Lo que hace falta en la PC

- **Flutter** estable (se usó 3.47.6 / Dart 3.13). En esta PC: `~/flutter`.
- **JDK 17**. En esta PC: `~/android-tc/jdk-17.0.20.1+1`.
- **SDK de Android** con la plataforma 36. En esta PC: `~/Android/Sdk`.

```bash
export PATH=~/flutter/bin:$PATH
export JAVA_HOME=~/android-tc/jdk-17.0.20.1+1
export ANDROID_HOME=~/Android/Sdk
cd app_movil
flutter pub get
flutter build apk --debug
```

El APK queda en `app_movil/build/app/outputs/flutter-apk/app-debug.apk`.

### Instalarlo en un teléfono Android

**Con cable (adb):** en el teléfono, activar *Opciones de desarrollador →
Depuración por USB*, conectarlo y:

```bash
~/Android/Sdk/platform-tools/adb install -r build/app/outputs/flutter-apk/app-debug.apk
```

**Sin adb:** copiar el `.apk` al teléfono (por USB, Drive, correo…), abrirlo
desde *Archivos* y aceptar «Instalar apps de origen desconocido» para esa
app cuando lo pida.

Después: abrir **MicroscopeOS**, escribir la IP de la Raspberry
(p. ej. `192.168.1.50`; el puerto 8000 se agrega solo) y tocar **Conectar**.
El teléfono tiene que estar en la red del laboratorio.

> Es un APK **de depuración**: pesa más (unos 150 MB) y va algo más lento
> que uno de producción. Para repartirlo en el laboratorio conviene un APK
> *release* firmado con una llave propia (pendiente).

---

## Servidor falso para desarrollar

`dev_server/servidor_falso.py` levanta **la app FastAPI real**
(`create_app` de `server/api.py`) con hardware falso: dos cámaras que dan
video con células sintéticas (se desenfoca según el motor y cambia con la
luz), dos matrices de luz, dos motores con jog y watchdog **como los
reales**, un autofoco que tarda unos segundos, la incubadora y cuatro
experimentos de ejemplo con imágenes de verdad. No modifica nada de
`codigo/MicroscopeOS/`: lo importa. Todo lo que escribe va a una carpeta
temporal nueva en cada arranque.

```bash
cd app_movil/dev_server
uv venv .venv && uv pip install -p .venv -r requirements.txt   # una vez
.venv/bin/python servidor_falso.py
```

Escucha en `0.0.0.0:8000`. Desde el emulador de Android la PC es
`10.0.2.2:8000`; desde un teléfono en la misma red, la IP de la PC.

Opciones:

| Opción | Para probar |
|---|---|
| `--con-timelapse` | arrancar con un timelapse corriendo (una foto cada 20 s) |
| `--sin-incubadora` | el Arduino desconectado |
| `--version-vieja` | un microscopio sin `POST /api/temperature/co2_setpoint` (404) |
| `--cloudflare` | ponerse delante como Cloudflare Access: login (página falsa), cookie `CF_Authorization` HttpOnly, redirecciones y el correo de la persona al microscopio |
| `--registrar` | con `--cloudflare`: imprimir cada pedido (sirve para ver qué manda la app) |
| `--autofoco-s 20` | cuánto tarda el autofoco (por defecto 8 s) |
| `--carpeta DIR` | guardar los datos en DIR en vez de una carpeta temporal |

Rutas que **solo** tiene el servidor falso (la Raspberry no):

- `POST /dev/otra_persona`: «ana@lab.mx» toma el control (para ver el 423 y «Tomar el control»). `POST /dev/otra_persona/soltar` lo suelta.
- `GET /dev/motores`: posición de cada motor y si el jog sigue activo.
- `POST /dev/vencer_sesion`: con `--cloudflare`, vence todas las sesiones (para ver el aviso «Tu sesión venció»).

Si la app alguna vez deja de mandar el stop del jog, el servidor imprime
`⚠⚠⚠ motor camN: PARADO POR EL WATCHDOG`.

---

## Pruebas

```bash
flutter analyze        # sin errores
flutter test           # 95 pruebas
```

- `test/api/cliente_api_test.dart`: el cliente contra respuestas falsas:
  `200 + error`, `ok:false`, `ok:true` con un error viejo, 423, 409, 404,
  422, 500, sin red (las lecturas se reintentan, las escrituras **nunca**),
  tiempo agotado; forma de los pedidos (nombre de la foto en la query,
  cuerpo del jog, autofoco con espera ≥ 90 s).
- `test/api/lectores_test.dart`: lector MJPEG (cuadros cortados por
  cualquier lado, marcadores partidos, cuadros rotos) y lector SSE (eventos
  cortados, CRLF, UTF-8 partido), reconexión con espera creciente y paso a
  `GET /api/temperature/status`.
- `test/vivo/control_jog_test.dart`: las reglas del jog, con el reloj simulado.
- `test/widgets/`: Inicio, Conexión, Experimentos, Luz, Foco (gestos
  reales: mantener, soltar, cancelar, segundo plano, salir de la pantalla,
  perder la conexión, timelapse que arranca) y el visor del vivo.

**De punta a punta**, en el emulador y contra el servidor falso (¡nunca
contra la Pi: mueve el motor, toma fotos e inicia un timelapse!):

```bash
flutter drive -d emulator-5554 \
  --driver=test_driver/integration_test.dart \
  --target=integration_test/app_test.dart
```

Recorre todas las pantallas y guarda las capturas en `docs/capturas/`. Antes
de empezar comprueba que el servidor tiene `/dev/motores` (o sea, que es el
falso) y si no, se detiene.

Y el **acceso desde internet**, contra el servidor falso en modo Cloudflare:

```bash
dev_server/.venv/bin/python dev_server/servidor_falso.py --puerto 8001 --cloudflare
flutter drive -d emulator-5554 \
  --driver=test_driver/integration_test.dart \
  --target=integration_test/acceso_remoto_test.dart
```

Entra con el correo, usa la app (incluido el video), vence la sesión,
comprueba el aviso y vuelve a entrar.

---

## Cómo está organizado el código

```
lib/
  api/            EL ÚNICO lugar que habla HTTP (dio). Ninguna pantalla hace HTTP.
    cliente_api.dart    todas las rutas, tiempos de espera y el mapeo de errores
    modelos.dart        la forma de cada respuesta
    errores.dart        ErrorDelMicroscopio, ErrorSinControl (423), ErrorDeRed…
    lector_mjpeg.dart   separa el video MJPEG en cuadros JPEG
    lector_sse.dart     separa el SSE de la incubadora en eventos
    imagen_api.dart     miniaturas como ImageProvider, bajadas por el cliente
    direccion.dart      IP -> URL; HTTP sin cifrar solo en la red local
  estado/         Riverpod: microscopios guardados, tema, consultas periódicas
                  (solo en primer plano), incubadora por SSE, avisos
  features/<pantalla>/   conexion, inicio, vivo, timelapse, experimentos, ajustes
  ui/             tema al estilo Apple, componentes, formatos, acciones comunes
  textos.dart     todos los textos
dev_server/       servidor falso (Python)
integration_test/ prueba de punta a punta
```

Cuando el backend pase a `/api/v1/`, solo cambia `lib/api/`.

**Consultas periódicas** (los mismos intervalos que la web): `/status` cada
2 s, `/api/control` cada 4 s, foco cada 3 s y luz cada 4 s (estas dos solo
con la pantalla de Vivo abierta). Todo se detiene cuando la app pasa a
segundo plano o la pestaña no se ve, y se recarga al volver.

**Video y fotos:** al tomar una foto, la app suelta el vivo de esa cámara y
lo retoma al terminar, para que la cámara no cambie de modo entre las 4
fotos de un relieve DPC. Al abrir la pantalla completa no se manda un
stop/start de más (`coordinador_vivo.dart` cuenta quién está mirando cada
cámara).

**Red:** Android no deja expresar rangos de IP en
`network_security_config.xml`, así que no se puede limitar ahí el HTTP sin
cifrar a `192.168.x.x`/`10.x.x.x`. Se permite HTTP en general y **la app
misma rechaza cualquier dirección HTTP que no sea de red local**
(`lib/api/direccion.dart`); el dominio del laboratorio queda obligado a
HTTPS. En iPhone sí existe esa excepción (`NSAllowsLocalNetworking`).

---

## Rutas de la API que usa

| Para | Rutas |
|---|---|
| Conexión y estado | `GET /api/version` · `GET /status` |
| Control | `GET /api/control` · `POST /api/control/tomar` · `/soltar` · `/visto_aviso` |
| Incubadora | `GET /api/temperature/stream` (SSE) · `GET /api/temperature/status` · `POST /api/temperature/setpoint` · `POST /api/temperature/co2_setpoint` |
| Vivo | `POST /live/start/{cam}` · `GET /live/stream/{cam}` (MJPEG) · `POST /live/stop/{cam}` · `GET /timelapse/vista/{cam}` |
| Luz | `GET /light/estado` · `POST /light/set` (con `color_centro`/`color_anillo` en Rheinberg) · `POST /light/off` · `POST /light/colores` |
| Foco | `GET /api/focus/status` · `POST /api/focus/jog` · `/jog/stop` · `/move` · `/config` · `/auto` |
| Fotos | `POST /capture/{cam}/{modo}?nombre=` · `POST /capture/both/{modo}?nombre=` |
| Timelapse | `POST /timelapse/start` (con `dpc_*` en relieve) · `/stop` · `/pausar` · `/continuar` · `/nota` |
| Experimentos | `GET /api/experimentos` · `GET /api/exp/{id}` · `/mini/{rel}` · `/compartir/{rel}` · `/notas` |

`/api/focus/config` no estaba en la lista original: la web la usa para que
cada tamaño de paso tenga su velocidad de movimiento continuo (fino = lento),
y la app hace lo mismo. Nunca se usa `GET /preview/{cam}` (apaga el vivo) ni
se bajan los TIFF originales (pesan decenas de MB).

---

## Lo que quedó fuera

Desde **Ajustes → Abrir en la web** (abre la página del microscopio en el
navegador del teléfono):

- respaldo en NAS, memorias USB y envío a la PC;
- marca de agua, óptica, perfiles y alertas;
- calibraciones (imagen, autofoco DPC, pila de foco);
- exportar video y OME-TIFF;
- actualizar el programa;
- renombrar, borrar o recuperar experimentos;
- reservas y bitácora;
- en el timelapse: guardar en USB, contar células, enviar a la PC o al NAS,
  autofoco cada N fotos;
- luz: un color totalmente libre (la app ofrece 16; la web, cualquiera);
- temperatura pedida por encima de 45 °C (la web permite hasta 80; en la app
  se limitó a 45 para que un dedo no la mande a una temperatura que mata
  las células);
- la gráfica de condiciones del experimento (`/api/exp/{id}/ambiente`) y la
  saturación del vivo (`/api/vivo/estadisticas`): el cliente ya tiene la
  segunda, falta dibujarla.

---

## iPhone

El proyecto ya trae la carpeta `ios/` y en `ios/Runner/Info.plist`:

- `NSAllowsLocalNetworking` (HTTP sin cifrar solo en la red local),
- `NSLocalNetworkUsageDescription` (el permiso de «red local» de iOS 14+).

Lo que falta, y **no se puede hacer desde Linux**:

1. Una **Mac con Xcode** y una cuenta de Apple Developer (gratis para
   probar en un iPhone propio; de pago para TestFlight/App Store).
2. `flutter build ios`, o abrir `ios/Runner.xcworkspace`, elegir el equipo
   de firma y correrla en un iPhone.
3. Repasar en un iPhone de verdad: el permiso de red local, el menú de
   compartir y la pantalla completa del video.

No se usó nada exclusivo de Android salvo `network_security_config.xml`.

---

## Acceso desde internet

Desde fuera del laboratorio el microscopio está en
`https://microscopio.lmimenchacalab.com`, detrás de **Cloudflare Access**
(código por correo). La app hace lo mismo que el navegador:

1. En **Microscopios → Desde fuera del laboratorio** se toca
   `microscopio.lmimenchacalab.com` (o se escribe; sin `https://` la app lo
   completa). Al probarlo, Cloudflare contesta con una redirección a su
   página de login y la app ofrece **Entrar con mi correo**.
2. Se abre la página de Cloudflare dentro de la app (`webview_flutter`, el
   navegador integrado oficial de Flutter). La persona escribe su correo del
   laboratorio y el código que le llega.
3. Cloudflare deja la cookie `CF_Authorization` (HttpOnly) en el dominio del
   microscopio. La app la lee del navegador integrado, **comprueba que el
   microscopio la acepta** (una cookie vieja también estaría ahí) y la manda
   en cada pedido, incluidos el video y la incubadora. El microscopio
   reconoce a la persona por su correo, igual que en la web.
4. Cuando la sesión vence (lo decide Cloudflare), Inicio muestra «Tu sesión
   desde fuera del laboratorio venció» con un botón **Entrar**, y cualquier
   acción ofrece entrar de nuevo y la repite. **Ajustes → Cerrar sesión de
   internet** la olvida.

**Seguridad del login:** el login empieza en `/api/version` (solo lee) y el
navegador integrado **no abre ninguna página del microscopio** salvo esa y
las de Cloudflare (`/cdn-cgi/`). Si cargara la interfaz web, esta mandaría
órdenes al abrirse (p. ej. `POST /api/focus/config`, que además toma el
control): se vio en las pruebas con el servidor falso y se bloqueó.

Notas:

- El token se guarda en las preferencias de la app (como la cookie del
  navegador). Para mayor protección se podría pasar al llavero del sistema
  (`flutter_secure_storage`).
- Se probó con el servidor falso en modo `--cloudflare`, que imita lo que se
  midió del Cloudflare real (redirección 302 a
  `small-lake-1bcd.cloudflareaccess.com`, cookie HttpOnly). **Falta probarlo
  con el Cloudflare real**: necesita que una persona ponga el código de su
  correo.
- La alternativa de **tokens de servicio** de Access no se usó: requiere
  cambiar la configuración de Cloudflare y el microscopio no sabría quién es
  quién.

---

## Pendientes del backend

Cosas que convendría cambiar en el backend para la app. No se tocó el
backend.

0. **⚠ Seguridad: `stop_jog()` puede tardar hasta 1.5 s en parar el motor**
   (`core/motor_focus.py`). Afecta igual a la web y a la app. El hilo del
   jog (`_jog_loop`) suelta el candado `self._lock` y lo vuelve a tomar
   enseguida, en cada tanda de 8 micropasos; `stop_jog()` necesita ese
   mismo candado para leer `_jog_stop`, y como los candados de Python no
   son «justos», a veces no consigue su turno hasta que vence el watchdog.
   Medido con el `FocusMotorController` real y el GPIO de
   `tests/emuladores.py`: mediana 100 ms, pero 5 de 30 veces más de 300 ms
   y el peor caso **1.55 s**. Con el paso grueso (8 micropasos) eso son
   ~150 µm de más después de soltar el botón. La app manda el stop al
   instante (lo verifican sus pruebas); el arreglo es en el servidor:
   `stop_jog()` tiene que marcar el `Event` de parada **sin** esperar el
   candado (un `threading.Event` ya es seguro entre hilos), y el bucle ya
   lo revisa en cada tanda. El servidor falso reproduce el mismo
   comportamiento a propósito.
1. **Versionar la API** (`/api/v1/...`) y decir en `/api/version` qué versión
   de la API y qué funciones tiene. Hoy la única forma de saber si una ruta
   existe es probarla y recibir 404 (pasó con el CO₂).
2. **Errores con códigos HTTP.** Casi todo responde `200` con
   `{"error": ...}`, y algunas rutas usan `{"ok": false, ...}`. Además, en
   `/api/temperature/status`, `/api/focus/status` y `/api/version` el campo
   `error` es un *dato* y no un fallo, así que el cliente tiene que saber
   ruta por ruta cómo leerlo. Propuesta: 4xx/5xx con `{"error", "codigo"}`
   (p. ej. `"timelapse_en_curso"`) y renombrar el dato a `estado_arduino`.
3. **`POST /api/temperature/setpoint` responde `ok:false` sin mensaje** cuando
   el valor está fuera de 20–80 °C (el CO₂ sí lo explica desde el PR #4), y
   puede responder `ok:true` con un `error` viejo del controlador.
4. **Un canal único de eventos por SSE** (estado, control, luz, foco,
   incubadora) en vez de consultar 4 rutas cada 2–4 s. Ahorra batería y
   datos, y los cambios llegan al instante.
5. **Autenticación por dispositivo** en la red local. Hoy se identifica por
   IP: si el teléfono cambia de IP (DHCP, otra red WiFi) «pierde» el control
   y aparece como otra persona.
6. **El autofoco como trabajo** (`POST` devuelve un id; `GET` da el progreso)
   en vez de un pedido que tarda 15–30 s o más. Un teléfono que se bloquea o
   cambia de red a mitad pierde el resultado.
7. **`GET /api/experimentos` vacía la papelera** como efecto secundario. Un
   GET no debería borrar nada; mejor una tarea periódica o un POST.
8. **El MJPEG no manda `Content-Length`** en cada parte: obliga a buscar los
   marcadores del JPEG. Con el largo, separar los cuadros es trivial.
9. **Una foto corta el vivo** de esa cámara sin avisar (el flujo MJPEG
   termina). La app reconecta sola, pero sería más claro que el servidor lo
   retome o lo diga.
