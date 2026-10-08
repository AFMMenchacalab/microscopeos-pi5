import 'dart:convert';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Se reemplaza en main() con la instancia ya cargada.
final preferenciasProvider = Provider<SharedPreferences>(
  (ref) => throw UnimplementedError('preferenciasProvider sin inicializar'),
);

/// Un microscopio guardado: nombre que eligió la persona y su dirección.
class MicroscopioGuardado {
  const MicroscopioGuardado({required this.nombre, required this.url, this.sesion});

  factory MicroscopioGuardado.desdeJson(Map<String, dynamic> j) => MicroscopioGuardado(
    nombre: '${j['nombre'] ?? ''}',
    url: '${j['url'] ?? ''}',
    sesion: j['sesion'] is String ? j['sesion'] as String : null,
  );

  final String nombre;
  final String url;

  /// Token de Cloudflare Access (cookie CF_Authorization) si se entra
  /// desde internet con el correo; null en la red del laboratorio.
  final String? sesion;

  Uri get uri => Uri.parse(url);

  bool get esRemoto => uri.scheme == 'https' || sesion != null;

  MicroscopioGuardado conSesion(String? s) => MicroscopioGuardado(nombre: nombre, url: url, sesion: s);

  Map<String, dynamic> aJson() => {'nombre': nombre, 'url': url, 'sesion': ?sesion};
}

class EstadoMicroscopios {
  const EstadoMicroscopios({this.lista = const [], this.actual});

  final List<MicroscopioGuardado> lista;

  /// Al que está conectada la app (null = mostrar la pantalla de conexión).
  final MicroscopioGuardado? actual;
}

class Microscopios extends Notifier<EstadoMicroscopios> {
  static const _claveLista = 'microscopios';
  static const _claveActual = 'microscopio_actual';

  SharedPreferences get _prefs => ref.read(preferenciasProvider);

  @override
  EstadoMicroscopios build() {
    final prefs = ref.watch(preferenciasProvider);
    var lista = <MicroscopioGuardado>[];
    try {
      final v = jsonDecode(prefs.getString(_claveLista) ?? '[]');
      if (v is List) {
        lista = v.whereType<Map>().map((m) => MicroscopioGuardado.desdeJson(m.cast())).toList();
      }
    } on FormatException {
      lista = [];
    }
    final url = prefs.getString(_claveActual);
    final actual = lista.where((m) => m.url == url).firstOrNull;
    return EstadoMicroscopios(lista: lista, actual: actual);
  }

  /// Agrega (o renombra, si la dirección ya estaba) y lo deja como actual.
  Future<void> guardarYUsar(MicroscopioGuardado m) async {
    final lista = [...state.lista.where((x) => x.url != m.url), m];
    await _guardar(lista, m);
  }

  Future<void> usar(MicroscopioGuardado m) => _guardar(state.lista, m);

  /// Encontrado por Bluetooth: si ya estaba guardado con ese nombre (y
  /// otra IP), se reemplaza en vez de quedar dos veces.
  Future<void> guardarDeBluetooth(MicroscopioGuardado m) async {
    final lista = [...state.lista.where((x) => x.url != m.url && x.nombre != m.nombre), m];
    await _guardar(lista, m);
  }

  /// Después de volver a entrar con el correo: la sesión nueva reemplaza
  /// a la vencida, sin cambiar de pantalla.
  Future<void> guardarSesion(String url, String? sesion) async {
    final lista = [for (final m in state.lista) m.url == url ? m.conSesion(sesion) : m];
    final actual = state.actual?.url == url ? state.actual!.conSesion(sesion) : state.actual;
    await _guardar(lista, actual);
  }

  /// Vuelve a la pantalla de conexión sin borrar nada.
  Future<void> desconectar() => _guardar(state.lista, null);

  Future<void> borrar(MicroscopioGuardado m) async {
    final lista = state.lista.where((x) => x.url != m.url).toList();
    await _guardar(lista, state.actual?.url == m.url ? null : state.actual);
  }

  Future<void> _guardar(List<MicroscopioGuardado> lista, MicroscopioGuardado? actual) async {
    state = EstadoMicroscopios(lista: lista, actual: actual);
    await _prefs.setString(_claveLista, jsonEncode(lista.map((m) => m.aJson()).toList()));
    if (actual == null) {
      await _prefs.remove(_claveActual);
    } else {
      await _prefs.setString(_claveActual, actual.url);
    }
  }
}

final microscopiosProvider = NotifierProvider<Microscopios, EstadoMicroscopios>(Microscopios.new);

// ---------------------------------------------------------------- tema
enum ModoTema { oscuro, claro, sistema }

class Tema extends Notifier<ModoTema> {
  @override
  ModoTema build() {
    final v = ref.watch(preferenciasProvider).getString('tema');
    // Oscuro por defecto: el microscopio se usa con poca luz.
    return ModoTema.values.where((m) => m.name == v).firstOrNull ?? ModoTema.oscuro;
  }

  Future<void> cambiar(ModoTema m) async {
    state = m;
    await ref.read(preferenciasProvider).setString('tema', m.name);
  }
}

final temaProvider = NotifierProvider<Tema, ModoTema>(Tema.new);

// ---------------------------------------------------------------- recordados
/// Pequeñas cosas que la app recuerda entre usos (último nombre de
/// experimento, cámara elegida, tamaño del paso del foco).
class Recordar {
  Recordar(this._prefs);
  final SharedPreferences _prefs;

  int entero(String clave, int porDefecto) => _prefs.getInt(clave) ?? porDefecto;
  Future<void> guardarEntero(String clave, int v) => _prefs.setInt(clave, v);
  String texto(String clave, [String porDefecto = '']) => _prefs.getString(clave) ?? porDefecto;
  Future<void> guardarTexto(String clave, String v) => _prefs.setString(clave, v);
  bool logico(String clave, bool porDefecto) => _prefs.getBool(clave) ?? porDefecto;
  Future<void> guardarLogico(String clave, bool v) => _prefs.setBool(clave, v);
}

final recordarProvider = Provider<Recordar>((ref) => Recordar(ref.watch(preferenciasProvider)));
