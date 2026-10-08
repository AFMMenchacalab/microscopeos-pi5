/// Todos los textos de la app, en un solo lugar para poder traducirla
/// después. Los nombres son los de la interfaz web («Campo claro»,
/// «Relieve DPC», «Tomar el control»...).
abstract final class Textos {
  static const app = 'MicroscopeOS';
  static const laboratorio = 'LMI Menchaca Lab';
  static const versionApp = '0.1.0';

  // ---------------------------------------------------------------- general
  static const cancelar = 'Cancelar';
  static const aceptar = 'Aceptar';
  static const listo = 'Listo';
  static const guardar = 'Guardar';
  static const reintentar = 'Reintentar';
  static const cerrar = 'Cerrar';
  static const borrar = 'Borrar';
  static const camara = 'Cámara';
  static String camaraN(int n) => 'Cámara $n';
  static const lasDos = 'Las dos';
  static const abrirEnLaWeb = 'Abrir en la web';
  static const cargando = 'Cargando…';

  // ---------------------------------------------------------------- pestañas
  static const tabInicio = 'Inicio';
  static const tabVivo = 'Vivo';
  static const tabExperimentos = 'Experimentos';
  static const tabAjustes = 'Ajustes';

  // ---------------------------------------------------------------- errores
  static const errorSinRed =
      'No hay conexión con el microscopio. Revisa que el teléfono esté en la red del laboratorio.';
  static const errorTiempoAgotado = 'El microscopio tardó demasiado en contestar.';
  static const errorOcupadoTimelapse = 'La cámara está ocupada: hay un timelapse en curso.';
  static const errorNoDisponible = 'Esta versión del microscopio no tiene esa función.';
  static const errorDatosInvalidos = 'Datos inválidos';
  static const errorRespuestaRara = 'El microscopio respondió algo que la app no entiende.';
  static const errorNoEsMicroscopio = 'En esa dirección responde algo, pero no es un MicroscopeOS.';
  static const errorSinDetalle = 'El microscopio no pudo hacerlo.';
  static const errorCertificado = 'El certificado de la conexión segura no es válido.';
  static const errorTemperaturaRango =
      'No se pudo cambiar la temperatura. Tiene que estar entre 20 y 80 °C y la incubadora conectada.';
  static const errorCo2Rango = 'No se pudo cambiar el CO₂. Revisa que la incubadora esté conectada.';
  static const errorCo2NoSoportado = 'Esta versión del microscopio no permite cambiar el CO₂.';
  static const errorNecesitaLogin = 'Este microscopio está protegido: hay que entrar con tu correo.';
  static const errorSesionVencida = 'Tu sesión desde fuera del laboratorio venció. Entra de nuevo con tu correo.';
  static String errorServidor(int codigo) => 'El microscopio tuvo un problema (error $codigo).';
  static String errorSinControl(String quien) => 'Ahora controla el microscopio $quien.';

  // ---------------------------------------------------------------- conexión
  static const conexionTitulo = 'Microscopios';
  static const conexionGuardados = 'Guardados';
  static const conexionNinguno = 'Todavía no hay microscopios guardados.';
  static const conexionAgregar = 'Agregar microscopio';
  static const conexionNombre = 'Nombre';
  static const conexionNombreEjemplo = 'Microscopio 1';
  static const conexionDireccion = 'Dirección';
  static const conexionDireccionEjemplo = '192.168.1.50';
  static const conexionAyuda =
      'En el laboratorio, escribe la IP de la Raspberry (la ves en tu router) o búscalo por Bluetooth.';
  static const conexionAyudaEmulador = 'En el emulador de Android, la PC es 10.0.2.2:8000.';
  static const conexionProbar = 'Probar conexión';
  static const conexionProbando = 'Probando…';
  static const conexionConectar = 'Conectar';
  static const conexionDireccionInvalida =
      'Esa dirección no sirve. Escribe una IP de la red local, por ejemplo 192.168.1.50.';
  static String conexionOk(String version) => 'Hay un MicroscopeOS ($version).';
  static const conexionRemotaTitulo = 'Desde fuera del laboratorio';
  static const conexionDominio = 'microscopio.lmimenchacalab.com';
  static const conexionNombreInternet = 'Microscopio (internet)';
  static const conexionRemotaSub = 'Entrar con tu correo del laboratorio';
  static const conexionRemotaTexto =
      'Funciona desde cualquier red. Te llega un código a tu correo, como en el navegador, y la app '
      'recuerda la sesión hasta que venza.';
  static const conexionEntrarCorreo = 'Entrar con mi correo';
  static const conexionBorrarPregunta = '¿Quitar este microscopio de la lista?';

  static const conexionCerca = 'Cerca de ti';
  static const conexionBuscarBt = 'Buscar por Bluetooth';
  static const conexionBuscarBtDetalle = 'Microscopio nuevo (configurarle el Wi-Fi) o que cambió de IP';

  // ---------------------------------------------------------------- bluetooth
  static const btTitulo = 'Microscopios cerca';
  static const btBuscando = 'Buscando…';
  static const btBuscandoDetalle =
      'Acércate al microscopio (a unos metros). Aparece como «MOS-» y cuatro letras o números.';
  static const btErrorBuscar = 'No se pudo buscar por Bluetooth.';
  static const btErrorConexion = 'Se perdió la conexión Bluetooth con el microscopio. Acércate y vuelve a intentar.';
  static const btApagado = 'El Bluetooth está apagado';
  static const btApagadoDetalle = 'Enciéndelo en el teléfono para buscar el microscopio.';
  static const btSinPermiso = 'Falta permiso para usar Bluetooth';
  static const btSinPermisoDetalle = 'La app solo lo usa para encontrar y configurar microscopios cerca.';
  static const btNoDisponible = 'Este teléfono no tiene Bluetooth de bajo consumo';
  static const btConectando = 'Conectando con el microscopio…';
  static String btSenal(int rssi) => rssi > -60 ? 'Muy cerca' : (rssi > -80 ? 'Cerca' : 'Lejos');
  static String btConectadoA(String red, String ip) => 'Conectado a «$red» · $ip';
  static const btSinRed = 'Sin red Wi-Fi';
  static const btGuardarYUsar = 'Guardar y usar este microscopio';
  static const btGuardarDetalle = 'Si ya lo tenías guardado con otra IP, se actualiza.';
  static const btConfigurarWifi = 'Configurar el Wi-Fi';
  static const btCambiarRed = 'Cambiar de red Wi-Fi';
  static const btVincularAviso = 'Si el teléfono pregunta «¿Vincular?», acepta: así la contraseña viaja cifrada.';
  static const btCodigoTitulo = 'Mira la luz del microscopio';
  static const btCodigoDetalle =
      'Muestra 3 colores seguidos y luego una pausa. Tócalos en el mismo orden. Así se comprueba que estás frente al microscopio.';
  static const btBorrar = 'Borrar';
  static const btOtroCodigo = 'Otros colores';
  static const btElegirRed = 'Elige la red Wi-Fi';
  static const btOtraRed = 'Otra red (escribe el nombre)';
  static String btClaveTitulo(String red) => 'Contraseña de «$red»';
  static const btClave = 'Contraseña del Wi-Fi';
  static const btRedAbierta = 'Esta red no tiene contraseña.';
  static const btConectarRed = 'Conectar';
  static const btOtraRedVolver = 'Elegir otra red';
  static String btConfigurando(String red) => 'Conectando el microscopio a «$red»… tarda unos segundos.';
  static const btListo = 'El microscopio está en la red';

  // ---------------------------------------------------------------- login remoto
  static const loginTitulo = 'Entrar con tu correo';
  static String loginAyuda(String host) =>
      'Página de acceso de $host (Cloudflare). Escribe tu correo del laboratorio y el código que te llega.';
  static const loginSinRed = 'No se pudo abrir la página de acceso. Revisa la conexión a internet.';
  static const loginEntrar = 'Entrar';
  static const loginPregunta = '¿Entrar de nuevo con tu correo?';

  // ---------------------------------------------------------------- control
  static const controlTuyo = 'Controlas tú el microscopio';
  static String controlDeOtro(String quien) => 'Controla $quien';
  static const controlTomar = 'Tomar el control';
  static const controlSoltar = 'Soltar el control';
  static const controlNadie = 'Nadie controla el microscopio';
  static String controlConectados(int n) => n == 1 ? '1 persona conectada' : '$n personas conectadas';
  static String controlTomarPregunta(String quien) => 'Ahora controla el microscopio $quien.';
  static const controlTomarDetalle = '¿Tomar el control? Le va a aparecer un aviso.';
  static String controlTeLoQuitaron(String quien) => '$quien tomó el control del microscopio';
  static const controlAhoraTuyo = 'Ahora controlas el microscopio';
  static const controlSoltado = 'Soltaste el control';
  static String controlTurnoDe(String quien) => 'Turno reservado de $quien';
  static const controlExplicacion =
      'Solo una persona a la vez puede mover el foco, cambiar la luz o tomar fotos. '
      'El control se suelta solo después de 90 s sin usarlo.';

  // ---------------------------------------------------------------- inicio
  static const sinConexion = 'Sin conexión con el microscopio';
  static const reconectando = 'Volviendo a intentar…';
  static const incubadora = 'Incubadora';
  static const temperatura = 'Temperatura';
  static const co2 = 'CO₂';
  static const humedad = 'Humedad';
  static String pedido(String v) => 'pedido $v';
  static const incubadoraSinConexion = 'La incubadora no está conectada';
  static const incubadoraSinDatos = 'Sin datos de la incubadora';
  static const incubadoraCambiar = 'Cambiar';
  static const incubadoraTemperaturaPedida = 'Temperatura que quieres';
  static const incubadoraCo2Pedido = 'CO₂ que quieres';
  static const incubadoraAplicar = 'Aplicar';
  static String incubadoraTemperaturaOk(String v) => 'Temperatura pedida: $v °C';
  static String incubadoraCo2Ok(String v) => 'CO₂ pedido: $v %';
  static const valvulaAbierta = 'Válvula abierta';
  static const valvulaCerrada = 'Válvula cerrada';

  static const timelapse = 'Timelapse';
  static const timelapseEnCurso = 'Timelapse en curso';
  static const timelapseEnPausa = 'Timelapse en pausa';
  static const timelapseNinguno = 'No hay un timelapse en curso';
  static const timelapseNingunoDetalle = 'Toma fotos automáticas cada cierto tiempo, aunque cierres la app.';
  static const timelapseNuevo = 'Nuevo timelapse';
  static const timelapsePausar = 'Pausar';
  static const timelapseSeguir = 'Seguir tomando fotos';
  static const timelapseAnotar = 'Anotar';
  static const timelapseDetener = 'Detener';
  static const timelapseDetenerPregunta = '¿Detener el timelapse?';
  static const timelapseDetenerDetalle =
      'Deja de tomar fotos y cierra el experimento. Las fotos ya tomadas quedan guardadas. No se puede deshacer.';
  static const timelapseDetenido = 'Timelapse detenido';
  static const timelapseDeteniendo = 'Deteniendo: el microscopio termina la foto en curso…';
  static const timelapsePausado = 'En pausa: puedes abrir la incubadora, mirar y enfocar';
  static const timelapseContinuado = 'Sigue el timelapse: toma una foto ahora';
  static const timelapseNotaTitulo = 'Nota en el experimento';
  static const timelapseNotaEjemplo = 'Ej.: agregué el fármaco';
  static String timelapseNotaOk(String hora) => 'Nota guardada a las $hora';
  static String timelapseFoto(int n) => 'Foto $n';
  static String timelapseCada(String cada) => 'una cada $cada';
  static String timelapseProxima(String cuando) => 'la próxima $cuando';
  static String timelapseTermina(String cuando) => 'termina $cuando';
  static const timelapsePuedesMirar = 'Puedes abrir la incubadora, mirar y enfocar.';
  static String timelapseReanudado(String cuando) => 'Se cortó la luz y siguió solo al volver ($cuando).';
  static const timelapseSinFotoAun = 'Todavía no hay fotos de esta cámara';
  static const timelapseVerExperimento = 'Ver el experimento';

  static const accesoVivo = 'Vivo';
  static const accesoVivoDetalle = 'Cámara, luz, foco y fotos';
  static const accesoExperimentos = 'Experimentos';
  static const accesoExperimentosDetalle = 'Fotos guardadas y timelapses';

  // ---------------------------------------------------------------- vivo
  static const vivoEnVivo = 'EN VIVO';
  static const vivoUltimoCiclo = 'ÚLTIMO CICLO';
  static const vivoConectando = 'Conectando la cámara…';
  static const vivoCortado = 'Se cortó el video. Volviendo a conectar…';
  static const vivoTimelapse = 'Hay un timelapse tomando fotos: se muestra la última foto de cada ciclo.';
  static const vivoPantallaCompleta = 'Pantalla completa';
  static const panelLuz = 'Luz';
  static const panelFoco = 'Foco';
  static const panelFoto = 'Foto';

  static const luzEncendida = 'Luz encendida';
  static const luzApagada = 'Luz apagada';
  static const luzBrillo = 'Brillo';
  static const luzLasDos = 'Misma luz en las dos cámaras';
  static const luzTipo = 'Tipo de luz';
  static const luzCampoClaro = 'Campo claro';
  static const luzRelieveIzq = 'Relieve DPC ←';
  static const luzRelieveDer = 'Relieve DPC →';
  static const luzRelieveArr = 'Relieve DPC ↑';
  static const luzRelieveAbj = 'Relieve DPC ↓';
  static const luzFondoNegro = 'Fondo negro';
  static const luzColores = 'De colores';
  static const luzColorCampo = 'Color';
  static const luzColorRelieve = 'Color del relieve';
  static const luzColorRelieveNota =
      'El verde es el recomendado: la fase depende del color y el objetivo está mejor corregido en verde.';
  static const luzColorCentro = 'Centro';
  static const luzColorAnillo = 'Anillo';
  static const colorBlanco = 'Blanco';
  static const colorVerde = 'Verde';
  static const colorRojo = 'Rojo';
  static const colorAzul = 'Azul';
  static const colorNaranja = 'Naranja';
  static const colorAmarillo = 'Amarillo';
  static const colorLima = 'Lima';
  static const colorVerdeAgua = 'Verde agua';
  static const colorCian = 'Cian';
  static const colorCeleste = 'Celeste';
  static const colorMorado = 'Morado';
  static const colorMagenta = 'Magenta';
  static const colorRosa = 'Rosa';
  static const colorSalmon = 'Salmón';
  static const colorVerdeClaro = 'Verde claro';
  static const colorLavanda = 'Lavanda';
  static const colorOtro = 'Otro color';
  static const colorOtroTitulo = 'Otro color';
  static const colorRecomendado = '(recomendado)';
  static const bloqueadoTimelapse = 'Bloqueado mientras el timelapse toma fotos. Ponlo en pausa para usarlo.';

  static const focoAltura = 'Altura';
  static const focoSubir = 'Subir';
  static const focoBajar = 'Bajar';
  static const focoPaso = 'Paso';
  static const focoAyuda = 'Toca: un paso · Mantén apretado: sigue moviendo';
  static const focoFino = 'Fino';
  static const focoMedio = 'Medio';
  static const focoGrueso = 'Grueso';
  static const focoAutofoco = 'Autofoco';
  static const focoEnfocando = 'Enfocando…';
  static const focoEnfocandoDetalle = 'Tarda de 15 a 30 segundos. No muevas la muestra.';
  static String focoEnfocado(String mov) => 'Enfocado: $mov';
  static const focoNoHizoFalta = 'no hizo falta moverse';
  static String focoSubio(String um) => 'subió $um µm';
  static String focoBajo(String um) => 'bajó $um µm';
  static String focoNoEncontrado(String um) =>
      'No encontré el foco (busqué $um µm hacia cada lado). Me quedé donde estaba: acércate un poco a mano y vuelve a intentar.';
  static const focoSinMotor = 'Esta cámara no tiene motor de enfoque';
  static const focoProblemaMotor = 'El motor avisa un problema (temperatura o conexión). Revísalo en la web.';

  static const fotoNombre = 'Nombre del experimento';
  static const fotoNombreEjemplo = 'Opcional: ej. Muestra B';
  static const fotoNombreAyuda = 'Sin nombre, la foto va a «Fotos sueltas» de hoy.';
  static const fotoTipo = 'Tipo de foto';
  static const fotoNormal = 'Campo claro';
  static const fotoRelieve = 'Relieve DPC';
  static const fotoFondoNegro = 'Fondo negro';
  static const fotoColores = 'De colores';
  static const fotoColoresNota = 'Luz Rheinberg con los colores elegidos en el panel Luz.';
  static const fotoDeQueCamara = '¿De qué cámara?';
  static const fotoEsta = 'Esta';
  static const fotoTomar = 'Tomar foto';
  static const fotoTomando = 'Tomando la foto…';
  static String fotoGuardada(int n, String exp) => n > 1 ? '$n fotos guardadas en «$exp»' : 'Foto guardada en «$exp»';
  static const fotoBloqueada = 'No se pueden tomar fotos sueltas mientras hay un timelapse (aunque esté en pausa).';

  // ---------------------------------------------------------------- nuevo timelapse
  static const nuevoTitulo = 'Nuevo timelapse';
  static const nuevoNombre = 'Nombre del experimento';
  static const nuevoNombreEjemplo = 'Ej.: Células día 1';
  static const nuevoTipo = 'Tipo de foto';
  static const nuevoTipoNormal = 'Campo claro (1 foto)';
  static const nuevoTipoRelieve = 'Relieve DPC (4 fotos)';
  static const nuevoTipoFondo = 'Fondo negro (1 foto)';
  static const nuevoTipoColores = 'De colores (1 foto)';
  static const relieveTitulo = 'Relieve DPC';
  static const relieveProcesar = 'Calcular el relieve al terminar cada ciclo';
  static const relieveBorrar = 'Borrar las 4 fotos originales (ahorra espacio)';
  static const relieveSuma = 'Guardar también la foto normal, más chica (~3 MB)';
  static const relieveFase = 'Guardar también la fase (en prueba)';
  static const relieveJpg = 'Vista previa en color (JPG)';
  static const relieveAyuda = 'Las 4 fotos se borran solo después de comprobar que el relieve quedó bien.';
  static const relieveSinProcesar = 'Se guardan las 4 fotos sin procesar (64 MB por cámara y por ciclo).';
  static const nuevoCada = 'Una foto cada';
  static const nuevoHasta = '¿Hasta cuándo?';
  static const nuevoDurante = 'Durante';
  static const nuevoHastaFecha = 'Hasta';
  static const nuevoHoras = 'Horas';
  static const nuevoTermina = 'Termina';
  static const nuevoCamaras = 'Cámaras';
  static const nuevoAutofoco = 'Reenfocar antes de cada foto';
  static const nuevoAutofocoAyuda =
      'Enfoca tú antes de empezar. En cada ciclo busca el foco alrededor de donde quedó el anterior.';
  static const nuevoIniciar = 'Iniciar timelapse';
  static const nuevoIniciado = 'Timelapse iniciado';
  static const nuevoConfirmar = '¿Iniciar el timelapse?';
  static const nuevoSinCamaras = 'Elige al menos una cámara.';
  static const nuevoFinPasado = 'Elige un día y hora de término que sea después de ahora.';
  static String nuevoResumen(int n, String cada, String durante) =>
      'Se tomarán $n ${n == 1 ? 'foto' : 'fotos'} por cámara, una cada $cada durante $durante.';
  static String nuevoEspacio(String ocupa, String libre) => 'Ocupará unos $ocupa de $libre libres.';
  static String nuevoNoAlcanza(String ocupa, String libre) =>
      'Ocupará unos $ocupa y NO ALCANZA el espacio ($libre libres). Toma fotos menos seguido o borra experimentos viejos desde la web.';
  static const nuevoSigueSolo = 'Puedes cerrar la app: el microscopio sigue solo.';
  static const nuevoBloqueado = 'Ya hay un timelapse en curso.';
  static const nuevoMasOpciones = 'Guardar en USB, contar células, enviar a la PC o al NAS: desde la web.';

  // ---------------------------------------------------------------- experimentos
  static const expTitulo = 'Experimentos';
  static const expNinguno = 'Todavía no hay fotos guardadas.';
  static const expEnCurso = 'EN CURSO';
  static String expFotos(int n) => n == 1 ? '1 foto' : '$n fotos';
  static String expLibre(String libre, int fotos) => 'Quedan $libre libres en el microscopio (≈ $fotos fotos).';
  static const expTimelapse = 'Timelapse';
  static const expFotosSueltas = 'Fotos';
  static const expNotas = 'Notas';
  static const expSinNotas = 'Sin notas.';
  static const expSinFotos = 'Este experimento no tiene fotos.';
  static const expCompartir = 'Compartir';
  static const expPreparando = 'Preparando la copia…';
  static const expMasEnLaWeb = 'Renombrar, borrar, descargar originales o exportar video: desde la web.';
  static String expCadaDurante(String cada, String durante) => 'Una foto cada $cada durante $durante';
  static String expDeN(int i, int n) => '$i de $n';

  // ---------------------------------------------------------------- ajustes
  static const ajustesTitulo = 'Ajustes';
  static const ajustesMicroscopio = 'Microscopio';
  static const ajustesCambiar = 'Cambiar de microscopio';
  static const ajustesCerrarSesion = 'Cerrar sesión de internet';
  static const ajustesCerrarSesionDetalle = 'La próxima vez la app te pedirá el código del correo.';
  static const ajustesApariencia = 'Apariencia';
  static const ajustesOscuro = 'Oscuro';
  static const ajustesClaro = 'Claro';
  static const ajustesSistema = 'Automático';
  static const ajustesControl = 'Control';
  static const ajustesEnLaWeb = 'Solo en la web';
  static const ajustesEnLaWebDetalle =
      'Estas funciones todavía no están en la app. Se abren en el navegador del teléfono.';
  static const ajustesFuncionesWeb = [
    'Respaldo en NAS, memorias USB y envío a la PC',
    'Marca de agua, óptica y perfiles',
    'Alertas por correo o Telegram',
    'Calibrar la cámara y el autofoco',
    'Exportar video y OME-TIFF',
    'Renombrar, borrar o recuperar experimentos',
    'Reservas y bitácora',
    'Actualizar el programa',
  ];
  static const ajustesAcercaDe = 'Acerca de';
  static const ajustesVersionApp = 'Versión de la app';
  static const ajustesVersionMicroscopio = 'Versión del microscopio';
  static const ajustesSinVersion = 'Sin datos';
}
