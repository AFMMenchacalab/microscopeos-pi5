import 'dart:async';
import 'dart:convert';
import 'dart:developer' as developer;
import 'dart:typed_data';

import 'package:dio/dio.dart';

import '../textos.dart';
import 'errores.dart';
import 'lector_mjpeg.dart';
import 'lector_sse.dart';
import 'modelos.dart';

/// Cómo interpretar el campo `error` de una respuesta 200.
enum _Errores {
  /// `{"error": "..."}` no vacío = falló. Lo normal en MicroscopeOS.
  campoError,

  /// `{"ok": false, "error": ...}` = falló; con `ok: true` se ignora el
  /// `error` (temperatura, CO2: el controlador puede dejar uno viejo).
  campoOk,

  /// `error` es un dato, no un fallo (estado de la incubadora, foco sin
  /// motores, versión sin git).
  ninguno,
}

/// El ÚNICO lugar de la app que habla HTTP con el microscopio.
///
/// Todas las rutas, sus cuerpos y la forma de las respuestas están aquí.
/// Las pantallas llaman a estos métodos y reciben modelos o un [ErrorApi].
/// Cuando el backend pase a `/api/v1/`, solo cambia este archivo.
class ClienteMicroscopio {
  ClienteMicroscopio(
    this.base, {
    Dio? dio,
    this.sesionAcceso,
    this.reintentosLectura = 2,
    this.esperaReintento = const Duration(milliseconds: 700),
  }) : _dio =
           dio ??
           Dio(
             BaseOptions(
               connectTimeout: const Duration(seconds: 5),
               receiveTimeout: tiempoNormal,
               sendTimeout: tiempoNormal,
             ),
           ) {
    _dio.options
      ..baseUrl = base.toString()
      // Los códigos HTTP se interpretan a mano (ver _interpretar).
      ..validateStatus = ((_) => true)
      // Cloudflare Access contesta con una redirección a su página de
      // login: hay que verla, no seguirla.
      ..followRedirects = false;
    final s = sesionAcceso;
    if (s != null && s.isNotEmpty) {
      // La misma cookie que guarda el navegador después de entrar con el
      // correo. Access también acepta el token en este encabezado.
      _dio.options.headers['Cookie'] = '$cookieAcceso=$s';
      _dio.options.headers['cf-access-token'] = s;
    }
  }

  /// Nombre de la cookie de sesión de Cloudflare Access.
  static const cookieAcceso = 'CF_Authorization';

  final Uri base;

  /// Token de Cloudflare Access (acceso desde internet); null en la red local.
  final String? sesionAcceso;
  final Dio _dio;
  final int reintentosLectura;
  final Duration esperaReintento;

  static const tiempoNormal = Duration(seconds: 10);
  static const tiempoProbar = Duration(seconds: 4);

  /// Una foto DPC de las dos cámaras son 8 capturas con cambios de luz.
  static const tiempoCaptura = Duration(seconds: 90);

  /// El autofoco responde recién cuando termina (15-30 s, más si amplía
  /// la búsqueda).
  static const tiempoAutofoco = Duration(seconds: 120);

  /// Entre dos cuadros del vivo o dos eventos de la incubadora.
  static const tiempoSinDatos = Duration(seconds: 6);

  void cerrar() => _dio.close(force: true);

  // ================================================================ básico
  /// GET /api/version. Sirve para saber si en esa dirección hay un
  /// microscopio: cualquier JSON con forma de versión vale.
  Future<VersionMicroscopio> version({bool probar = false}) async {
    final j = await _json(
      'GET',
      '/api/version',
      errores: _Errores.ninguno,
      tiempo: probar ? tiempoProbar : null,
      reintentos: probar ? 0 : null,
    );
    if (!j.containsKey('git')) throw const ErrorInesperado(Textos.errorNoEsMicroscopio);
    return VersionMicroscopio.desdeJson(j);
  }

  /// GET /status
  Future<EstadoGeneral> estado() async => EstadoGeneral.desdeJson(await _json('GET', '/status'));

  // ================================================================ control
  Future<EstadoControl> control() async => EstadoControl.desdeJson(await _json('GET', '/api/control'));

  Future<EstadoControl> tomarControl() async => EstadoControl.desdeJson(await _json('POST', '/api/control/tomar'));

  Future<EstadoControl> soltarControl() async => EstadoControl.desdeJson(await _json('POST', '/api/control/soltar'));

  /// Ya se mostró «X tomó el control»: el servidor deja de avisarlo.
  Future<void> avisoVisto() => _json('POST', '/api/control/visto_aviso');

  // ================================================================ incubadora
  Future<LecturaIncubadora> incubadora() async =>
      LecturaIncubadora.desdeJson(await _json('GET', '/api/temperature/status', errores: _Errores.ninguno));

  /// SSE de /api/temperature/stream: un evento por segundo. El flujo
  /// termina (o da error) si se corta la conexión; reconectar es cosa de
  /// quien escucha (ver estado/incubadora.dart).
  Stream<LecturaIncubadora> flujoIncubadora() {
    final sse = SeparadorSse();
    return _flujo(
      '/api/temperature/stream',
      (bytes) => bytes.cast<List<int>>().transform(utf8.decoder),
    ).expand(sse.agregar).map((datos) {
      final j = jsonDecode(datos);
      return LecturaIncubadora.desdeJson(j is Map ? j.cast<String, dynamic>() : const {});
    });
  }

  Future<void> fijarTemperatura(double celsius) => _json(
    'POST',
    '/api/temperature/setpoint',
    cuerpo: {'value': celsius},
    errores: _Errores.campoOk,
    siFallaSinMensaje: Textos.errorTemperaturaRango,
  );

  /// CO2 en ppm (40000 = 4 %). Antes del PR #4 la ruta no existía: 404.
  Future<void> fijarCo2(double ppm) => _json(
    'POST',
    '/api/temperature/co2_setpoint',
    cuerpo: {'value': ppm},
    errores: _Errores.campoOk,
    siFallaSinMensaje: Textos.errorCo2Rango,
    si404: Textos.errorCo2NoSoportado,
  );

  // ================================================================ vivo
  Future<void> iniciarVivo(int cam) => _json('POST', '/live/start/$cam');

  Future<void> detenerVivo(int cam) => _json('POST', '/live/stop/$cam');

  /// Cuadros JPEG de /live/stream/{cam} (MJPEG, ~15 por segundo). Hay que
  /// llamar antes a [iniciarVivo]. Cancelar la suscripción corta la
  /// conexión; el servidor también la corta al detener el vivo o al
  /// tomar una foto con esa cámara.
  Stream<Uint8List> flujoVivo(int cam) {
    final mjpeg = SeparadorMjpeg();
    return _flujo('/live/stream/$cam', (bytes) => bytes).expand(mjpeg.agregar);
  }

  /// JPEG del último ciclo del timelapse (se actualiza en cada ciclo).
  /// 404 si todavía no hay foto de esa cámara.
  Future<Uint8List> vistaTimelapse(int cam, {int tamano = 800, int? ciclo}) =>
      bytes('/timelapse/vista/$cam', query: {'size': tamano, 'v': ?ciclo});

  /// Ruta de la vista del último ciclo para [ImagenMicroscopio]. [version]
  /// cambia con cada ciclo, así la caché de imágenes no muestra una vieja.
  String rutaVistaTimelapse(int cam, {int tamano = 800, String? version}) =>
      '/timelapse/vista/$cam?size=$tamano${version == null ? '' : '&v=$version'}';

  /// Saturación de cada cámara en el vivo (opcional).
  Future<Map<int, double>> saturacionVivo() async {
    final j = await _json('GET', '/api/vivo/estadisticas');
    final out = <int, double>{};
    final camaras = j['camaras'];
    if (camaras is Map) {
      for (final e in camaras.entries) {
        final cam = int.tryParse('${e.key}');
        final v = e.value is Map ? (e.value as Map)['saturados_pct'] : null;
        if (cam != null && v is num) out[cam] = v.toDouble();
      }
    }
    return out;
  }

  // ================================================================ luz
  Future<Map<int, MatrizLuz>> estadoLuz() async => _matrices(await _json('GET', '/light/estado'));

  static Map<int, MatrizLuz> _matrices(Map<String, dynamic> j) {
    final out = <int, MatrizLuz>{};
    final m = j['matrices'];
    if (m is Map) {
      for (final e in m.entries) {
        final cam = int.tryParse('${e.key}');
        if (cam != null && e.value is Map) {
          out[cam] = MatrizLuz.desdeJson((e.value as Map).cast<String, dynamic>());
        }
      }
    }
    return out;
  }

  /// [rheinberg] (centro, anillo) solo se usa en modo Rheinberg.
  Future<void> fijarLuz(ModoLuz modo, int porcentaje, List<int> camaras, {(String, String)? rheinberg}) => _json(
    'POST',
    '/light/set',
    cuerpo: {
      'modo': modo.id,
      'percent': porcentaje.clamp(0, 100),
      'camaras': camaras,
      if (modo == ModoLuz.rheinberg && rheinberg != null) ...{
        'color_centro': rheinberg.$1,
        'color_anillo': rheinberg.$2,
      },
    },
  );

  /// Color del campo claro y/o del relieve DPC (RRGGBB; "FFFFFF" =
  /// blanco) de esas cámaras. Si la matriz está encendida en ese modo,
  /// cambia al momento. Devuelve cómo quedaron las matrices.
  Future<Map<int, MatrizLuz>> fijarColores(List<int> camaras, {String? campo, String? relieve}) async =>
      _matrices(await _json('POST', '/light/colores', cuerpo: {'camaras': camaras, 'campo': ?campo, 'dpc': ?relieve}));

  Future<void> apagarLuz(List<int> camaras) => _json('POST', '/light/off', cuerpo: {'camaras': camaras});

  // ================================================================ foco
  Future<Map<int, MotorFoco>> estadoFoco() async {
    final j = await _json('GET', '/api/focus/status', errores: _Errores.ninguno);
    final out = <int, MotorFoco>{};
    final m = j['motores'];
    if (m is Map) {
      for (final e in m.entries) {
        final cam = int.tryParse('${e.key}');
        if (cam != null && e.value is Map) {
          out[cam] = MotorFoco.desdeJson(cam, (e.value as Map).cast<String, dynamic>());
        }
      }
    }
    return out;
  }

  /// Movimiento continuo: hay que repetirlo cada 250 ms mientras el dedo
  /// esté apretado. Si deja de llegar, el servidor para el motor solo a
  /// los [watchdog] segundos. `direccion` 1 = baja, -1 = sube.
  Future<double?> jog(int motor, int direccion, {double velocidad = 0.003, double watchdog = 1.5}) async {
    final j = await _json(
      'POST',
      '/api/focus/jog',
      cuerpo: {'motor': motor, 'direction': direccion, 'velocidad': velocidad, 'watchdog': watchdog},
      tiempo: const Duration(seconds: 3),
      reintentos: 0,
    );
    return (j['posicion_um'] as num?)?.toDouble();
  }

  /// Para el movimiento continuo YA.
  Future<void> pararJog(int motor) =>
      _json('POST', '/api/focus/jog/stop', cuerpo: {'motor': motor}, tiempo: const Duration(seconds: 4), reintentos: 0);

  /// Paso fijo en micras. `direccion` 1 = baja, -1 = sube. Devuelve la
  /// posición nueva del motor.
  Future<double?> moverFoco(int motor, int direccion, double um) async {
    final j = await _json(
      'POST',
      '/api/focus/move',
      cuerpo: {'motor': motor, 'direction': direccion, 'um': um},
      tiempo: const Duration(seconds: 30),
    );
    return (j['posicion_um'] as num?)?.toDouble();
  }

  /// Resolución de micropasos para mover a mano: también fija la
  /// velocidad del movimiento continuo (más fino = más lento).
  Future<void> configurarFoco(int motor, int microsteps) =>
      _json('POST', '/api/focus/config', cuerpo: {'motor': motor, 'microsteps': microsteps});

  Future<ResultadoAutofoco> autofoco(int cam, {double rangoUm = 40, double rangoMaxUm = 200}) async =>
      ResultadoAutofoco.desdeJson(
        await _json(
          'POST',
          '/api/focus/auto',
          cuerpo: {'camera': cam, 'metodo': 'auto', 'rango_um': rangoUm, 'rango_max_um': rangoMaxUm},
          tiempo: tiempoAutofoco,
        ),
      );

  // ================================================================ fotos
  /// `cam` null = las dos cámaras. Ojo: `nombre` va en la query aunque
  /// sea POST (así lo declara api.py).
  Future<ResultadoCaptura> capturar({int? cam, required ModoFoto modo, String nombre = ''}) async {
    final ruta = cam == null ? '/capture/both/${modo.id}' : '/capture/$cam/${modo.id}';
    return ResultadoCaptura.desdeJson(
      await _json('POST', ruta, query: {'nombre': nombre.trim()}, tiempo: tiempoCaptura),
    );
  }

  // ================================================================ timelapse
  Future<void> iniciarTimelapse(PedidoTimelapse p) => _json('POST', '/timelapse/start', cuerpo: p.aJson());

  Future<void> detenerTimelapse() => _json('POST', '/timelapse/stop', tiempo: const Duration(seconds: 90));

  Future<void> pausarTimelapse() => _json('POST', '/timelapse/pausar');

  Future<void> continuarTimelapse() => _json('POST', '/timelapse/continuar');

  Future<Nota> anotarTimelapse(String texto) async {
    final j = await _json('POST', '/timelapse/nota', cuerpo: {'texto': texto});
    return Nota.desdeJson(j['nota'] is Map ? (j['nota'] as Map).cast<String, dynamic>() : const {});
  }

  // ================================================================ experimentos
  /// GET /api/experimentos. OJO: como efecto secundario el servidor vacía
  /// la papelera (lo de más de 7 días). Ver «Pendientes del backend».
  Future<ListaExperimentos> experimentos() async =>
      ListaExperimentos.desdeJson(await _json('GET', '/api/experimentos'));

  Future<DetalleExperimento> experimento(String id) async =>
      DetalleExperimento.desdeJson(await _json('GET', '/api/exp/${Uri.encodeComponent(id)}'));

  Future<List<Nota>> notas(String id) async {
    final j = await _json('GET', '/api/exp/${Uri.encodeComponent(id)}/notas');
    final l = j['notas'];
    return l is List ? l.whereType<Map>().map((n) => Nota.desdeJson(n.cast<String, dynamic>())).toList() : [];
  }

  /// Ruta (relativa a [base]) de la miniatura JPEG de una foto. Nunca se
  /// bajan los TIFF originales: pesan decenas de MB.
  String rutaMiniatura(String id, String rel, {int tamano = 320}) =>
      '/api/exp/${Uri.encodeComponent(id)}/mini/${_rel(rel)}?size=$tamano';

  /// La copia JPEG con la marca de agua del laboratorio, para compartir.
  Future<ArchivoDescargado> paraCompartir(String id, String rel) async {
    final r = await _pedir(
      'GET',
      '/api/exp/${Uri.encodeComponent(id)}/compartir/${_rel(rel)}',
      query: {'formato': 'jpg'},
      tipo: ResponseType.bytes,
      tiempo: const Duration(seconds: 60),
    );
    final datos = _bytesDe(r);
    final disp = r.headers.value('content-disposition') ?? '';
    final nombre =
        RegExp(r'filename="([^"]+)"').firstMatch(disp)?.group(1) ??
        '${id}_${rel.split('/').last.replaceAll('.tif', '')}.jpg';
    return ArchivoDescargado(nombre: nombre, bytes: datos, tipo: 'image/jpeg');
  }

  /// Bytes de una imagen (miniatura, vista del timelapse).
  Future<Uint8List> bytes(String ruta, {Map<String, dynamic>? query}) async {
    final r = await _pedir('GET', ruta, query: query, tipo: ResponseType.bytes);
    return _bytesDe(r);
  }

  String _rel(String rel) => rel.split('/').map(Uri.encodeComponent).join('/');

  // ================================================================ interno
  Future<Map<String, dynamic>> _json(
    String metodo,
    String ruta, {
    Object? cuerpo,
    Map<String, dynamic>? query,
    Duration? tiempo,
    int? reintentos,
    _Errores errores = _Errores.campoError,
    String? siFallaSinMensaje,
    String? si404,
  }) async {
    final r = await _pedir(
      metodo,
      ruta,
      cuerpo: cuerpo,
      query: query,
      tiempo: tiempo,
      reintentos: reintentos,
      si404: si404,
    );
    final datos = r.data;
    final j = datos is Map
        ? datos.cast<String, dynamic>()
        : (datos is String && datos.trim().startsWith('{') ? _decodificar(datos) : null);
    if (j == null) throw const ErrorInesperado(Textos.errorRespuestaRara);
    _revisarError(j, errores, siFallaSinMensaje);
    return j;
  }

  Map<String, dynamic>? _decodificar(String s) {
    try {
      final v = jsonDecode(s);
      return v is Map ? v.cast<String, dynamic>() : null;
    } on FormatException {
      return null;
    }
  }

  /// Casi todas las rutas responden 200 aunque fallen: el fallo viene en
  /// el cuerpo.
  static void _revisarError(Map<String, dynamic> j, _Errores modo, String? siFallaSinMensaje) {
    if (modo == _Errores.ninguno) return;
    final e = j['error'];
    final mensaje = e is String && e.trim().isNotEmpty ? e.trim() : null;
    // {"ok": true, "error": "viejo"} es un éxito: el controlador de la
    // incubadora no limpia su último error.
    if (j['ok'] == true) return;
    if (j['ok'] == false || mensaje != null) {
      throw ErrorDelMicroscopio(mensaje ?? siFallaSinMensaje ?? Textos.errorSinDetalle);
    }
  }

  /// Hace el pedido, reintenta las lecturas si se cae la red, y convierte
  /// los códigos HTTP en errores de la app.
  Future<Response<dynamic>> _pedir(
    String metodo,
    String ruta, {
    Object? cuerpo,
    Map<String, dynamic>? query,
    Duration? tiempo,
    int? reintentos,
    ResponseType tipo = ResponseType.json,
    String? si404,
  }) async {
    final esLectura = metodo == 'GET';
    final maxIntentos = 1 + (esLectura ? (reintentos ?? reintentosLectura) : 0);
    for (var intento = 1; ; intento++) {
      try {
        final r = await _dio.request<dynamic>(
          ruta,
          data: cuerpo,
          queryParameters: query,
          options: Options(
            method: metodo,
            responseType: tipo,
            receiveTimeout: tiempo,
            sendTimeout: cuerpo == null ? null : tiempo,
            contentType: cuerpo == null ? null : Headers.jsonContentType,
          ),
        );
        _interpretar(r, si404: si404, conSesion: sesionAcceso != null);
        return r;
      } on DioException catch (e) {
        final error = _deDio(e);
        // Solo se reintentan las lecturas: repetir un POST (una foto, un
        // movimiento del motor) podría hacerlo dos veces.
        if (error is ErrorDeRed && intento < maxIntentos) {
          await Future<void>.delayed(esperaReintento * intento);
          continue;
        }
        throw error;
      }
    }
  }

  /// Cloudflare Access pide entrar: redirige a su página de login
  /// (`<equipo>.cloudflareaccess.com/cdn-cgi/access/login/...`) o, a
  /// veces, contesta 401/403 con `WWW-Authenticate: Cloudflare-Access`.
  static bool esPantallaDeAcceso(int codigo, String? location, String? wwwAuthenticate) {
    if (wwwAuthenticate != null && wwwAuthenticate.toLowerCase().startsWith('cloudflare-access')) return true;
    if (codigo >= 300 && codigo < 400 && location != null) {
      final l = location.toLowerCase();
      return l.contains('/cdn-cgi/access/') || (Uri.tryParse(l)?.host.endsWith('cloudflareaccess.com') ?? false);
    }
    return false;
  }

  /// Códigos HTTP que usa MicroscopeOS (ver api.py y core/usuarios.py).
  static void _interpretar(Response<dynamic> r, {String? si404, bool conSesion = false}) {
    final codigo = r.statusCode ?? 0;
    if (codigo >= 200 && codigo < 300) return;
    if (esPantallaDeAcceso(codigo, r.headers.value('location'), r.headers.value('www-authenticate'))) {
      throw ErrorNecesitaLogin(vencida: conSesion);
    }
    final cuerpo = _cuerpoComoMapa(r.data);
    switch (codigo) {
      case 423:
        final quien = cuerpo?['control']?.toString() ?? '';
        throw ErrorSinControl(
          (cuerpo?['error'] as String?) ?? Textos.errorSinControl(nombreCorto(quien)),
          quien: quien,
        );
      case 409:
        throw const ErrorOcupadoPorTimelapse();
      case 404:
        throw ErrorNoDisponible(si404 ?? Textos.errorNoDisponible);
      case 422:
        developer.log(
          '422 en ${r.requestOptions.method} ${r.requestOptions.path}: ${cuerpo?['detail'] ?? r.data}',
          name: 'microscopeos.api',
        );
        throw ErrorDatosInvalidos(cuerpo?['detail'] ?? r.data);
      default:
        // Algunas rutas de imágenes devuelven 500 con texto plano.
        final texto = _comoTexto(r.data);
        developer.log('HTTP $codigo en ${r.requestOptions.path}: $texto', name: 'microscopeos.api');
        throw ErrorInesperado(Textos.errorServidor(codigo), codigo: codigo);
    }
  }

  static Map<String, dynamic>? _cuerpoComoMapa(Object? datos) {
    if (datos is Map) return datos.cast<String, dynamic>();
    final t = _comoTexto(datos);
    if (t == null || !t.trimLeft().startsWith('{')) return null;
    try {
      final v = jsonDecode(t);
      return v is Map ? v.cast<String, dynamic>() : null;
    } on FormatException {
      return null;
    }
  }

  static String? _comoTexto(Object? datos) {
    if (datos is String) return datos;
    if (datos is List<int>) {
      try {
        return utf8.decode(datos);
      } on FormatException {
        return null;
      }
    }
    return datos?.toString();
  }

  static ErrorApi _deDio(DioException e) {
    switch (e.type) {
      case DioExceptionType.connectionTimeout:
      case DioExceptionType.sendTimeout:
      case DioExceptionType.receiveTimeout:
      case DioExceptionType.transformTimeout:
        return const ErrorDeRed(tiempoAgotado: true);
      case DioExceptionType.connectionError:
      case DioExceptionType.unknown:
        return const ErrorDeRed();
      case DioExceptionType.cancel:
        return const ErrorDeRed();
      case DioExceptionType.badCertificate:
        return const ErrorInesperado(Textos.errorCertificado);
      case DioExceptionType.badResponse:
        final r = e.response;
        if (r != null) {
          try {
            _interpretar(r);
          } on ErrorApi catch (x) {
            return x;
          }
        }
        return const ErrorInesperado(Textos.errorRespuestaRara);
    }
  }

  static Uint8List _bytesDe(Response<dynamic> r) {
    final d = r.data;
    if (d is Uint8List) return d;
    if (d is List<int>) return Uint8List.fromList(d);
    throw const ErrorInesperado(Textos.errorRespuestaRara);
  }

  /// Respuesta en streaming (SSE, MJPEG). La conexión se corta al
  /// cancelar la suscripción.
  Stream<T> _flujo<T>(String ruta, Stream<T> Function(Stream<Uint8List> bytes) transformar) {
    final cancelar = CancelToken();
    late final StreamController<T> salida;
    StreamSubscription<T>? sub;
    salida = StreamController<T>(
      onListen: () async {
        try {
          final r = await _dio.get<ResponseBody>(
            ruta,
            cancelToken: cancelar,
            options: Options(responseType: ResponseType.stream, receiveTimeout: tiempoSinDatos),
          );
          final codigo = r.statusCode ?? 0;
          if (codigo < 200 || codigo >= 300) {
            await r.data?.stream.drain<void>().catchError((_) {});
            _interpretar(
              Response<dynamic>(requestOptions: r.requestOptions, statusCode: codigo, headers: r.headers),
              conSesion: sesionAcceso != null,
            );
          }
          final cuerpo = r.data;
          if (cuerpo == null) throw const ErrorInesperado(Textos.errorRespuestaRara);
          if (salida.isClosed) {
            cancelar.cancel();
            return;
          }
          sub = transformar(cuerpo.stream).listen(
            salida.add,
            // Un corte a mitad del flujo llega como HttpException o
            // SocketException, no como DioException: todo es «sin red».
            onError: (Object e, StackTrace st) {
              if (salida.isClosed) return;
              salida.addError(
                e is DioException ? _deDio(e) : (e is ErrorApi || e is FormatException ? e : const ErrorDeRed()),
                st,
              );
            },
            onDone: salida.close,
          );
        } on DioException catch (e, st) {
          if (!salida.isClosed) {
            salida.addError(_deDio(e), st);
            await salida.close();
          }
        } on ErrorApi catch (e, st) {
          if (!salida.isClosed) {
            salida.addError(e, st);
            await salida.close();
          }
        }
      },
      onCancel: () async {
        cancelar.cancel();
        await sub?.cancel();
      },
    );
    return salida.stream;
  }
}
