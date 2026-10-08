// Forma de las respuestas de MicroscopeOS, tal como las arma
// codigo/MicroscopeOS/server/api.py (y core/usuarios.py, core/timelapse.py,
// core/experimentos.py, temperature_controller.py).
//
// Los constructores `desdeJson` son tolerantes: un campo que falta o viene
// con otro tipo queda en null en vez de romper la app. Un microscopio con
// una versión un poco más vieja o más nueva tiene que seguir viéndose.

double? _num(Object? v) => v is num ? v.toDouble() : (v is String ? double.tryParse(v) : null);
int? _int(Object? v) => v is num ? v.toInt() : (v is String ? int.tryParse(v) : null);
String? _str(Object? v) => v?.toString();
bool _bool(Object? v) => v == true;
Map<String, dynamic> _mapa(Object? v) => v is Map ? v.cast<String, dynamic>() : const {};
List<dynamic> _lista(Object? v) => v is List ? v : const [];
DateTime? _fecha(Object? v) => v is String ? DateTime.tryParse(v) : null;

// ---------------------------------------------------------------- versión
/// GET /api/version
class VersionMicroscopio {
  const VersionMicroscopio({this.git = false, this.rama, this.commit, this.fecha, this.titulo});

  factory VersionMicroscopio.desdeJson(Map<String, dynamic> j) => VersionMicroscopio(
    git: _bool(j['git']),
    rama: _str(j['rama']),
    commit: _str(j['commit']),
    fecha: _fecha(j['fecha']),
    titulo: _str(j['titulo']),
  );

  final bool git;
  final String? rama;
  final String? commit;
  final DateTime? fecha;
  final String? titulo;
}

// ---------------------------------------------------------------- /status
/// GET /status
class EstadoGeneral {
  const EstadoGeneral({this.corriendo = false, this.camaraActiva = 0, this.ciclo = 0, this.timelapse, this.reanudando});

  factory EstadoGeneral.desdeJson(Map<String, dynamic> j) => EstadoGeneral(
    corriendo: _bool(j['running']),
    camaraActiva: _int(j['camara_activa']) ?? 0,
    ciclo: _int(j['ciclo']) ?? 0,
    timelapse: j['timelapse'] is Map ? ResumenTimelapse.desdeJson(_mapa(j['timelapse'])) : null,
    reanudando: _str(j['reanudando']),
  );

  /// Hay un timelapse (tomando fotos o en pausa).
  final bool corriendo;
  final int camaraActiva;
  final int ciclo;
  final ResumenTimelapse? timelapse;

  /// Texto mientras se reanuda un timelapse que cortó la luz.
  final String? reanudando;

  bool get enPausa => corriendo && (timelapse?.pausado ?? false);

  /// El timelapse está tomando fotos: el vivo, la luz, el foco y el
  /// autofoco devuelven «Timelapse en curso». En pausa NO: para eso es.
  bool get bloqueaControles => corriendo && !enPausa;

  /// Las fotos sueltas se bloquean aunque esté en pausa
  /// (/capture mira timelapse.is_running(), no la pausa).
  bool get bloqueaFotos => corriendo;

  /// No se puede iniciar otro mientras hay uno o se reanuda el anterior.
  bool get bloqueaNuevoTimelapse => corriendo || reanudando != null;
}

/// TimelapseManager.resumen()
class ResumenTimelapse {
  const ResumenTimelapse({
    this.nombre,
    this.modo,
    this.intervaloS,
    this.duracionS,
    this.camaras = const [],
    this.inicio,
    this.finPrevisto,
    this.proxima,
    this.reanudaciones = const [],
    this.ciclo = 0,
    this.pausado = false,
    this.carpeta,
    this.ultimas = const {},
  });

  factory ResumenTimelapse.desdeJson(Map<String, dynamic> j) => ResumenTimelapse(
    nombre: _str(j['nombre']),
    modo: _str(j['modo']),
    intervaloS: _int(j['intervalo_s']),
    duracionS: _int(j['duracion_s']),
    camaras: _lista(j['camaras']).map(_int).whereType<int>().toList(),
    inicio: _fecha(j['inicio']),
    finPrevisto: _fecha(j['fin_previsto']),
    proxima: _fecha(j['proxima']),
    reanudaciones: _lista(j['reanudaciones']).map(_fecha).whereType<DateTime>().toList(),
    ciclo: _int(j['ciclo']) ?? 0,
    pausado: _bool(j['pausado']),
    carpeta: _str(j['carpeta']),
    ultimas: {
      for (final e in _mapa(j['ultimas']).entries)
        if (int.tryParse(e.key) != null) int.parse(e.key): UltimaFoto.desdeJson(_mapa(e.value)),
    },
  );

  final String? nombre;
  final String? modo;
  final int? intervaloS;
  final int? duracionS;
  final List<int> camaras;
  final DateTime? inicio;
  final DateTime? finPrevisto;
  final DateTime? proxima;
  final List<DateTime> reanudaciones;
  final int ciclo;
  final bool pausado;

  /// Id del experimento (para abrirlo en la galería).
  final String? carpeta;
  final Map<int, UltimaFoto> ultimas;

  /// 0-1 según la hora (no según los ciclos: con pausas no coinciden).
  double progreso(DateTime ahora) {
    final i = inicio, f = finPrevisto;
    if (i == null || f == null || !f.isAfter(i)) return 0;
    return (ahora.difference(i).inSeconds / f.difference(i).inSeconds).clamp(0.0, 1.0);
  }
}

class UltimaFoto {
  const UltimaFoto({required this.ciclo, this.hora, this.dpc = false});

  factory UltimaFoto.desdeJson(Map<String, dynamic> j) =>
      UltimaFoto(ciclo: _int(j['ciclo']) ?? 0, hora: _fecha(j['hora']), dpc: _bool(j['dpc']));

  final int ciclo;
  final DateTime? hora;
  final bool dpc;
}

// ---------------------------------------------------------------- control
/// GET /api/control (Usuarios.estado)
class EstadoControl {
  const EstadoControl({
    required this.yo,
    this.controlador,
    this.tengoControl = false,
    this.meLoQuito,
    this.conectados = const [],
    this.reservaDeOtro = false,
    this.reservaDe,
  });

  factory EstadoControl.desdeJson(Map<String, dynamic> j) {
    final reserva = _mapa(j['reserva_actual']);
    return EstadoControl(
      yo: Persona.desdeJson(_mapa(j['yo'])),
      controlador: j['control'] is Map ? Persona.desdeJson(_mapa(j['control'])) : null,
      tengoControl: _bool(j['tengo_control']),
      meLoQuito: _str(j['me_lo_quitaron']),
      conectados: _lista(j['conectados']).map((c) => Persona.desdeJson(_mapa(c))).toList(),
      reservaDeOtro: _bool(j['reserva_de_otro']),
      reservaDe: _str(reserva['usuario']),
    );
  }

  final Persona yo;

  /// Quién tiene el control ahora (null = nadie).
  final Persona? controlador;
  final bool tengoControl;

  /// Si alguien me quitó el control: su nombre (hasta avisar con
  /// /api/control/visto_aviso).
  final String? meLoQuito;
  final List<Persona> conectados;
  final bool reservaDeOtro;
  final String? reservaDe;

  bool get controlaOtro => controlador != null && !tengoControl;
}

class Persona {
  const Persona({required this.id, required this.nombre, this.remoto = false});

  factory Persona.desdeJson(Map<String, dynamic> j) =>
      Persona(id: _str(j['id']) ?? '', nombre: _str(j['nombre']) ?? '', remoto: _bool(j['remoto']));

  final String id;
  final String nombre;
  final bool remoto;

  /// «Red local (192.168.1.23)» -> «192.168.1.23»; «ana@lab.mx» -> «ana».
  /// Igual que corto() en index_uiux.html.
  String get corto => nombreCorto(nombre);
}

String nombreCorto(String nombre) {
  final local = RegExp(r'^Red local \((.*)\)$').firstMatch(nombre);
  return (local != null ? local.group(1)! : nombre).split('@').first;
}

// ---------------------------------------------------------------- incubadora
/// GET /api/temperature/status y cada evento de /api/temperature/stream
/// (TemperatureController.status). Ojo: aquí `error` es un DATO (estado
/// del Arduino), no un fallo del pedido.
class LecturaIncubadora {
  const LecturaIncubadora({
    this.conectada = false,
    this.temperatura,
    this.temperaturaPedida,
    this.co2Ppm,
    this.co2PedidoPpm,
    this.humedad,
    this.valvulaAbierta,
    this.error,
  });

  factory LecturaIncubadora.desdeJson(Map<String, dynamic> j) => LecturaIncubadora(
    conectada: _bool(j['connected']),
    temperatura: _num(j['temperature']),
    temperaturaPedida: _num(j['setpoint']),
    co2Ppm: _num(j['co2']),
    co2PedidoPpm: _num(j['co2_setpoint']),
    humedad: _num(j['humidity']),
    valvulaAbierta: j['valve_open'] is bool ? j['valve_open'] as bool : null,
    error: _str(j['error']),
  );

  final bool conectada;
  final double? temperatura;
  final double? temperaturaPedida;
  final double? co2Ppm;
  final double? co2PedidoPpm;
  final double? humedad;
  final bool? valvulaAbierta;
  final String? error;

  /// La interfaz muestra el CO2 en %: ppm / 10000.
  double? get co2Pct => co2Ppm == null ? null : co2Ppm! / 10000;
  double? get co2PedidoPct => co2PedidoPpm == null ? null : co2PedidoPpm! / 10000;

  bool get hayCo2 => co2Ppm != null && !co2Ppm!.isNaN;

  /// Mismas tolerancias que pintarIncubadora() en la web: menos de 0.5 °C
  /// bien, menos de 2 °C aviso, más es un problema.
  Semaforo get semaforoTemperatura {
    final t = temperatura, p = temperaturaPedida;
    if (t == null || p == null) return Semaforo.sinDato;
    final d = (t - p).abs();
    return d < 0.5 ? Semaforo.bien : (d < 2 ? Semaforo.aviso : Semaforo.mal);
  }

  /// CO2: diferencia relativa al pedido, < 10 % bien, < 25 % aviso.
  Semaforo get semaforoCo2 {
    final c = co2Ppm, p = co2PedidoPpm;
    if (c == null || p == null || p == 0) return Semaforo.sinDato;
    final d = (c - p).abs() / p;
    return d < 0.10 ? Semaforo.bien : (d < 0.25 ? Semaforo.aviso : Semaforo.mal);
  }
}

enum Semaforo { bien, aviso, mal, sinDato }

// ---------------------------------------------------------------- luz
/// Modos de /light/set (ver _METODOS_LUZ en api.py, más «rheinberg»).
enum ModoLuz {
  full('full'),
  left('left'),
  right('right'),
  top('top'),
  bottom('bottom'),
  ring('ring'),
  rheinberg('rheinberg');

  const ModoLuz(this.id);
  final String id;

  bool get esRelieve => this == left || this == right || this == top || this == bottom;

  static ModoLuz? desdeId(String? id) {
    for (final m in values) {
      if (m.id == id) return m;
    }
    return null;
  }
}

/// Cada matriz de GET /light/estado.
class MatrizLuz {
  const MatrizLuz({
    this.encendida = false,
    this.modo = ModoLuz.full,
    this.porcentaje,
    this.colorCampo = 'FFFFFF',
    this.colorRelieve = 'FFFFFF',
    this.rheinberg = const ('0000FF', 'FF6A00'),
  });

  factory MatrizLuz.desdeJson(Map<String, dynamic> j) {
    final rh = _lista(j['rheinberg']).map((e) => '$e'.toUpperCase()).toList();
    return MatrizLuz(
      encendida: _bool(j['encendida']),
      modo: ModoLuz.desdeId(_str(j['modo'])) ?? ModoLuz.full,
      porcentaje: _num(j['percent'])?.round(),
      colorCampo: (_str(j['color_campo']) ?? 'FFFFFF').toUpperCase(),
      colorRelieve: (_str(j['color_dpc']) ?? 'FFFFFF').toUpperCase(),
      rheinberg: rh.length == 2 ? (rh[0], rh[1]) : const ('0000FF', 'FF6A00'),
    );
  }

  final bool encendida;

  /// El modo puesto o, si está apagada, el último que tuvo encendido.
  final ModoLuz modo;
  final int? porcentaje;

  /// Colores RRGGBB de cada matriz ("FFFFFF" = blanco). Cada cámara
  /// tiene los suyos y el servidor los guarda (profiles/iluminacion.json).
  final String colorCampo;
  final String colorRelieve;

  /// Rheinberg: (centro, anillo).
  final (String, String) rheinberg;

  MatrizLuz copiar({
    bool? encendida,
    ModoLuz? modo,
    int? porcentaje,
    String? colorCampo,
    String? colorRelieve,
    (String, String)? rheinberg,
  }) => MatrizLuz(
    encendida: encendida ?? this.encendida,
    modo: modo ?? this.modo,
    porcentaje: porcentaje ?? this.porcentaje,
    colorCampo: colorCampo ?? this.colorCampo,
    colorRelieve: colorRelieve ?? this.colorRelieve,
    rheinberg: rheinberg ?? this.rheinberg,
  );
}

// ---------------------------------------------------------------- foco
/// Cada motor de GET /api/focus/status (FocusMotorController.estado_completo).
class MotorFoco {
  const MotorFoco({
    required this.camara,
    this.posicionUm = 0,
    this.microsteps,
    this.jogActivo = false,
    this.uartOk = true,
    this.caliente = false,
    this.corteTermico = false,
    this.corto = false,
  });

  factory MotorFoco.desdeJson(int camara, Map<String, dynamic> j) => MotorFoco(
    camara: camara,
    posicionUm: _num(j['posicion_um']) ?? 0,
    microsteps: _int(j['microsteps']),
    jogActivo: _bool(j['jog_activo']),
    uartOk: j['uart'] == null || j['uart'] == 'ok',
    caliente: _bool(j['sobretemp_aviso']),
    corteTermico: _bool(j['sobretemp_corte']),
    corto: _bool(j['corto_fase_a']) || _bool(j['corto_fase_b']),
  );

  final int camara;

  /// Posición del motor: positivo = la plataforma bajó.
  final double posicionUm;
  final int? microsteps;
  final bool jogActivo;
  final bool uartOk;
  final bool caliente;
  final bool corteTermico;
  final bool corto;

  /// Lo que se muestra: altura (subir = número más grande), como la web.
  double get alturaUm => -posicionUm;

  bool get hayProblema => caliente || corteTermico || corto || !uartOk;
}

/// POST /api/focus/auto (Autofocus.enfocar_auto)
class ResultadoAutofoco {
  const ResultadoAutofoco({this.encontrado = true, this.desplazamientoUm, this.segundos, this.rangoUm, this.aviso});

  factory ResultadoAutofoco.desdeJson(Map<String, dynamic> j) => ResultadoAutofoco(
    encontrado: j['encontrado'] != false,
    desplazamientoUm: _num(j['desplazamiento_um']),
    segundos: _num(j['segundos']),
    rangoUm: _num(j['rango_um']),
    aviso: _str(j['aviso']),
  );

  final bool encontrado;

  /// Positivo = bajó (convención del motor).
  final double? desplazamientoUm;
  final double? segundos;
  final double? rangoUm;
  final String? aviso;
}

// ---------------------------------------------------------------- fotos
/// Tipos de foto (MODOS en core/timelapse.py).
enum ModoFoto {
  blanco('blanco', 1),
  dpc('dpc', 4),
  oscuro('oscuro', 1),
  rheinberg('rheinberg', 1);

  const ModoFoto(this.id, this.fotosPorToma);
  final String id;
  final int fotosPorToma;

  static ModoFoto? desdeId(String? id) {
    for (final m in values) {
      if (m.id == id) return m;
    }
    return null;
  }
}

/// POST /capture/... -> {"saved": [...], "experimento": id, "nombre": ...}
class ResultadoCaptura {
  const ResultadoCaptura({required this.guardadas, required this.experimento, required this.nombre});

  factory ResultadoCaptura.desdeJson(Map<String, dynamic> j) => ResultadoCaptura(
    guardadas: _lista(j['saved']).map((e) => e.toString()).toList(),
    experimento: _str(j['experimento']) ?? '',
    nombre: _str(j['nombre']) ?? '',
  );

  final List<String> guardadas;
  final String experimento;
  final String nombre;
}

// ---------------------------------------------------------------- timelapse
/// Qué hacer con las 4 fotos de un relieve DPC al terminar cada ciclo
/// (dpc_* de TimelapseReq; ver core/dpc.py). Los valores por defecto son
/// los del servidor y los de la web.
class OpcionesRelieve {
  const OpcionesRelieve({
    this.procesar = true,
    this.borrarCrudas = true,
    this.suma = true,
    this.fase = false,
    this.jpg = true,
  });

  /// Calcular el relieve al terminar cada ciclo. Sin esto, solo se
  /// guardan las 4 fotos crudas (y las demás opciones no aplican).
  final bool procesar;

  /// Borrar las 4 fotos originales después de comprobar el resultado.
  final bool borrarCrudas;

  /// Guardar también la foto normal (campo claro), más chica.
  final bool suma;

  /// Guardar también la fase (en prueba).
  final bool fase;

  /// Vista previa en color (JPG).
  final bool jpg;

  OpcionesRelieve copiar({bool? procesar, bool? borrarCrudas, bool? suma, bool? fase, bool? jpg}) => OpcionesRelieve(
    procesar: procesar ?? this.procesar,
    borrarCrudas: borrarCrudas ?? this.borrarCrudas,
    suma: suma ?? this.suma,
    fase: fase ?? this.fase,
    jpg: jpg ?? this.jpg,
  );

  Map<String, dynamic> aJson() => {
    'dpc_procesar': procesar,
    'dpc_borrar_crudas': borrarCrudas,
    'dpc_suma': suma,
    'dpc_fase': fase,
    'dpc_jpg': jpg,
  };
}

/// Lo mínimo de TimelapseReq; el resto queda con los valores por defecto
/// del servidor.
class PedidoTimelapse {
  const PedidoTimelapse({
    required this.modo,
    required this.intervaloS,
    this.duracionS,
    this.fin,
    this.nombre = '',
    this.camaras = const [0, 1],
    this.autofoco = true,
    this.relieve = const OpcionesRelieve(),
  }) : assert(duracionS != null || fin != null);

  final ModoFoto modo;
  final int intervaloS;
  final int? duracionS;

  /// Hora local de término. Si viene, el servidor la usa en vez de la duración.
  final DateTime? fin;
  final String nombre;
  final List<int> camaras;
  final bool autofoco;

  /// Solo se manda en modo relieve DPC.
  final OpcionesRelieve relieve;

  Map<String, dynamic> aJson() => {
    'modo': modo.id,
    'interval': intervaloS,
    'duration': duracionS ?? (fin!.difference(DateTime.now()).inSeconds),
    if (fin != null) 'fin': _isoLocalMinutos(fin!),
    'nombre': nombre,
    'camaras': camaras,
    'autofocus': autofoco,
    if (modo == ModoFoto.dpc) ...relieve.aJson(),
  };
}

/// «2026-10-06T09:00»: hora local sin zona, como el datetime-local de la web.
String _isoLocalMinutos(DateTime d) {
  String dos(int n) => n.toString().padLeft(2, '0');
  return '${d.year}-${dos(d.month)}-${dos(d.day)}T${dos(d.hour)}:${dos(d.minute)}';
}

// ---------------------------------------------------------------- experimentos
/// Cada elemento de GET /api/experimentos (Experimentos.info).
class Experimento {
  const Experimento({
    required this.id,
    required this.nombre,
    this.tipo = 'fotos',
    this.inicio,
    this.inicioLegible,
    this.estado,
    this.intervaloS,
    this.duracionS,
    this.modo,
    this.camaras = const [],
    this.nFotos = 0,
    this.bytes = 0,
    this.portada,
    this.enCurso = false,
  });

  factory Experimento.desdeJson(Map<String, dynamic> j) => Experimento(
    id: _str(j['id']) ?? '',
    nombre: _str(j['nombre']) ?? _str(j['id']) ?? '',
    tipo: _str(j['tipo']) ?? 'fotos',
    inicio: _fecha(j['inicio']),
    inicioLegible: _str(j['inicio_legible']),
    estado: _str(j['estado']),
    intervaloS: _int(j['intervalo_s']),
    duracionS: _int(j['duracion_s']),
    modo: _str(j['modo']),
    camaras: _lista(j['camaras']).map(_int).whereType<int>().toList(),
    nFotos: _int(j['n_fotos']) ?? 0,
    bytes: _int(j['bytes']) ?? 0,
    portada: _str(j['portada']),
    enCurso: _bool(j['en_curso']),
  );

  final String id;
  final String nombre;

  /// "timelapse" o "fotos".
  final String tipo;
  final DateTime? inicio;
  final String? inicioLegible;
  final String? estado;
  final int? intervaloS;
  final int? duracionS;
  final String? modo;
  final List<int> camaras;
  final int nFotos;
  final int bytes;

  /// Ruta relativa de la foto de portada (para /mini/).
  final String? portada;
  final bool enCurso;

  bool get esTimelapse => tipo == 'timelapse';
}

/// GET /api/experimentos
class ListaExperimentos {
  const ListaExperimentos({required this.experimentos, this.libreBytes, this.fotosQueCaben});

  factory ListaExperimentos.desdeJson(Map<String, dynamic> j) {
    final espacio = _mapa(j['espacio']);
    return ListaExperimentos(
      experimentos: _lista(j['experimentos']).map((e) => Experimento.desdeJson(_mapa(e))).toList(),
      libreBytes: _int(espacio['libre_bytes']),
      fotosQueCaben: _int(espacio['fotos_que_caben']),
    );
  }

  final List<Experimento> experimentos;
  final int? libreBytes;
  final int? fotosQueCaben;
}

/// GET /api/exp/{id}: lo mismo que la lista más las rutas de las fotos.
class DetalleExperimento {
  const DetalleExperimento({required this.info, required this.imagenes});

  factory DetalleExperimento.desdeJson(Map<String, dynamic> j) => DetalleExperimento(
    info: Experimento.desdeJson(j),
    imagenes: _lista(j['imagenes']).map((e) => e.toString()).toList(),
  );

  /// Sin datos (para cuando falla una recarga).
  factory DetalleExperimento.vacio(String id) => DetalleExperimento(
    info: Experimento(id: id, nombre: id),
    imagenes: const [],
  );

  final Experimento info;

  /// Rutas relativas («cam0/0001_2026-10-02_10-30-00.tif»), por cámara y
  /// en orden de toma.
  final List<String> imagenes;

  /// Las fotos de una cámara («cam0/...»).
  List<String> deCamara(int cam) => imagenes.where((r) => r.startsWith('cam$cam/')).toList();

  /// Cámaras que tienen fotos, en orden.
  List<int> get camarasConFotos {
    final s = <int>{};
    for (final r in imagenes) {
      final m = RegExp(r'^cam(\d+)/').firstMatch(r);
      if (m != null) s.add(int.parse(m.group(1)!));
    }
    return s.toList()..sort();
  }
}

/// Cada nota de GET /api/exp/{id}/notas (notas.csv).
class Nota {
  const Nota({this.hora, this.ciclo, this.tipo = 'nota', this.autor = '', this.texto = ''});

  factory Nota.desdeJson(Map<String, dynamic> j) => Nota(
    hora: _fecha(j['hora']),
    ciclo: _int(j['ciclo']),
    tipo: _str(j['tipo']) ?? 'nota',
    autor: _str(j['autor']) ?? '',
    texto: _str(j['texto']) ?? '',
  );

  final DateTime? hora;
  final int? ciclo;

  /// "nota" (escrita por alguien), "pausa" o "reanudar" (automáticas).
  final String tipo;
  final String autor;
  final String texto;
}

/// Un archivo para compartir (la copia JPEG con marca de agua).
class ArchivoDescargado {
  const ArchivoDescargado({required this.nombre, required this.bytes, required this.tipo});
  final String nombre;
  final List<int> bytes;
  final String tipo;
}
