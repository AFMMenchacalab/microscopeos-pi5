import 'dart:async';
import 'dart:convert';

/// Protocolo Bluetooth (BLE) de MicroscopeOS para configurar la red.
///
/// El otro lado está en codigo/MicroscopeOS/core/servicio_bluetooth.py
/// (rama feature/configuracion-bluetooth). Tres características:
///
/// - ESTADO  (leer, notificar): JSON con nombre, red, IP, si se puede
///   configurar y el resultado del último comando ("ultimo", "error").
/// - REDES   (leer): JSON [[ssid, señal, segura], ...].
/// - COMANDO (escribir, cifrado): JSON {"id": n, "cmd": ...}.
///
/// Este archivo no depende del paquete de Bluetooth: [EnlaceEquipo] es lo
/// único que hace falta para hablar con el microscopio, y en las pruebas
/// se reemplaza por uno falso.
const uuidServicio = '6d6f732d-0001-4c4d-8000-6d6963726f73';
const uuidEstado = '6d6f732d-0002-4c4d-8000-6d6963726f73';
const uuidRedes = '6d6f732d-0003-4c4d-8000-6d6963726f73';
const uuidComando = '6d6f732d-0004-4c4d-8000-6d6963726f73';

/// Los colores del código, en el mismo orden y con los mismos nombres que
/// COLORES en core/configuracion_red.py.
const coloresCodigo = <String, int>{
  'rojo': 0xFFFF0000,
  'verde': 0xFF00FF00,
  'azul': 0xFF0000FF,
  'amarillo': 0xFFFFFF00,
  'magenta': 0xFFFF00FF,
  'cian': 0xFF00FFFF,
};

/// Lo que el microscopio cuenta de sí mismo (característica ESTADO).
class EstadoEquipo {
  const EstadoEquipo({
    required this.nombre,
    this.conectado = false,
    this.red,
    this.ip,
    this.puerto = 8000,
    this.configurable = false,
    this.motivo = '',
    this.paso = 'listo',
    this.error,
    this.intentos = 3,
    this.bloqueadoS = 0,
    this.autorizado = false,
    this.ultimo = 0,
  });

  factory EstadoEquipo.desdeJson(Map<String, dynamic> j) => EstadoEquipo(
    nombre: '${j['nombre'] ?? 'MicroscopeOS'}',
    conectado: j['conectado'] == true,
    red: j['red'] as String?,
    ip: j['ip'] as String?,
    puerto: (j['puerto'] as num?)?.toInt() ?? 8000,
    configurable: j['configurable'] == true,
    motivo: '${j['motivo'] ?? ''}',
    paso: '${j['paso'] ?? 'listo'}',
    error: j['error'] as String?,
    intentos: (j['intentos'] as num?)?.toInt() ?? 3,
    bloqueadoS: (j['bloqueado_s'] as num?)?.toInt() ?? 0,
    autorizado: j['autorizado'] == true,
    ultimo: (j['ultimo'] as num?)?.toInt() ?? 0,
  );

  final String nombre;
  final bool conectado;
  final String? red;
  final String? ip;
  final int puerto;

  /// ¿Se puede configurar el Wi-Fi ahora? (sin red o recién encendido, y
  /// sin timelapse). Si no, [motivo] dice por qué.
  final bool configurable;
  final String motivo;

  /// listo | codigo | conectando | conectado | error
  final String paso;
  final String? error;
  final int intentos;
  final int bloqueadoS;
  final bool autorizado;

  /// Id del último comando que atendió.
  final int ultimo;

  /// La dirección para guardar en la app (http://IP:8000).
  Uri? get direccion => conectado && ip != null ? Uri(scheme: 'http', host: ip, port: puerto) : null;
}

/// Una red Wi-Fi que ve el microscopio.
class RedWifi {
  const RedWifi(this.ssid, this.senal, this.segura);
  final String ssid;

  /// 0-100
  final int senal;
  final bool segura;
}

/// Un microscopio encontrado por Bluetooth (todavía sin conectar).
class EquipoCercano {
  const EquipoCercano({required this.id, required this.nombre, required this.senal});
  final String id;

  /// El nombre corto del anuncio («MOS-3F2A»).
  final String nombre;

  /// RSSI en dBm (más cerca de 0 = más cerca).
  final int senal;
}

/// Un error que el microscopio explica (mensaje para mostrar tal cual).
class ErrorEquipo implements Exception {
  const ErrorEquipo(this.mensaje);
  final String mensaje;
  @override
  String toString() => mensaje;
}

/// Lo mínimo para hablar con el microscopio por Bluetooth.
abstract interface class EnlaceEquipo {
  Future<List<int>> leerEstado();
  Future<List<int>> leerRedes();

  /// Escritura con respuesta en COMANDO (cifrada: la primera vez el
  /// teléfono pide emparejar).
  Future<void> escribirComando(List<int> datos);

  /// Notificaciones de ESTADO.
  Stream<List<int>> get cambiosDeEstado;

  Future<void> cerrar();
}

/// Una conexión con un microscopio: leer su estado y mandarle comandos.
class SesionEquipo {
  SesionEquipo(
    this._enlace, {
    this.esperaComando = const Duration(seconds: 15),
    this.esperaWifi = const Duration(seconds: 75),
    this.cadaCuantoLeer = const Duration(seconds: 1),
  });

  final EnlaceEquipo _enlace;
  final Duration esperaComando;
  final Duration esperaWifi;

  /// Por si una notificación se pierde: se relee el estado cada tanto.
  final Duration cadaCuantoLeer;
  int _id = DateTime.now().millisecondsSinceEpoch % 100000;

  static EstadoEquipo _estadoDe(List<int> datos) {
    final j = jsonDecode(utf8.decode(datos));
    if (j is! Map) throw const ErrorEquipo('El microscopio respondió algo que la app no entiende');
    return EstadoEquipo.desdeJson(j.cast<String, dynamic>());
  }

  Future<EstadoEquipo> estado() async => _estadoDe(await _enlace.leerEstado());

  Future<List<RedWifi>> redes() async {
    final j = jsonDecode(utf8.decode(await _enlace.leerRedes()));
    if (j is! List) return [];
    return [
      for (final r in j)
        if (r is List && r.length >= 3) RedWifi('${r[0]}', (r[1] as num).toInt(), r[2] == true),
    ];
  }

  /// Muestra el código de colores en la luz del microscopio.
  Future<EstadoEquipo> pedirCodigo() => _comando({'cmd': 'pedir_codigo'});

  /// Los colores que la persona vio, en orden.
  Future<EstadoEquipo> enviarCodigo(List<String> colores) => _comando({'cmd': 'codigo', 'colores': colores});

  /// Conecta el microscopio a esa red y devuelve su estado (con la IP).
  Future<EstadoEquipo> configurarWifi(String ssid, String clave) =>
      _comando({'cmd': 'wifi', 'ssid': ssid, 'clave': clave}, espera: esperaWifi);

  /// Escribe el comando y espera el estado que dice que lo atendió.
  Future<EstadoEquipo> _comando(Map<String, dynamic> datos, {Duration? espera}) async {
    final id = ++_id;
    final resultado = Completer<EstadoEquipo>();
    void revisar(EstadoEquipo e) {
      if (e.ultimo == id && !resultado.isCompleted) resultado.complete(e);
    }

    final sub = _enlace.cambiosDeEstado.listen((d) {
      try {
        revisar(_estadoDe(d));
      } catch (_) {}
    });
    final lector = Timer.periodic(cadaCuantoLeer, (_) async {
      try {
        revisar(await estado());
      } catch (_) {}
    });
    try {
      await _enlace.escribirComando(utf8.encode(jsonEncode({'id': id, ...datos})));
      final e = await resultado.future.timeout(
        espera ?? esperaComando,
        onTimeout: () => throw const ErrorEquipo('El microscopio no respondió a tiempo'),
      );
      if (e.error != null && e.error!.isNotEmpty) throw ErrorEquipo(e.error!);
      return e;
    } finally {
      lector.cancel();
      // Sin esperar: si el microscopio se desconectó, cancelar la
      // suscripción a sus notificaciones puede no terminar nunca.
      unawaited(sub.cancel());
    }
  }

  Future<void> cerrar() => _enlace.cerrar();
}
