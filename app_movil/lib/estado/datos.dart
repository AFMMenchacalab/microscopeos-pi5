import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/cliente_api.dart';
import '../api/errores.dart';
import '../api/modelos.dart';
import 'conexion.dart';

// Lo que la app lee del microscopio. Los intervalos son los de la web
// (index_uiux.html): /status cada 2 s, foco cada 3 s, luz cada 4 s.

ClienteMicroscopio? _cliente(Ref ref) => ref.watch(clienteProvider);

/// GET /status cada 2 s.
final estadoProvider = StreamProvider.autoDispose<EstadoGeneral>((ref) {
  final c = _cliente(ref);
  if (c == null) return const Stream.empty();
  return sondear(ref, const Duration(seconds: 2), c.estado);
});

/// GET /api/control cada 4 s (la web lo hace cada 5).
final controlProvider = StreamProvider.autoDispose<EstadoControl>((ref) {
  final c = _cliente(ref);
  if (c == null) return const Stream.empty();
  return sondear(ref, const Duration(seconds: 4), c.control);
});

/// Matrices de luz, cada 4 s (solo mientras la pantalla de Vivo está abierta).
final luzProvider = StreamProvider.autoDispose<Map<int, MatrizLuz>>((ref) {
  final c = _cliente(ref);
  if (c == null) return const Stream.empty();
  return sondear(ref, const Duration(seconds: 4), c.estadoLuz);
});

/// Motores de enfoque, cada 3 s (solo mientras la pantalla de Vivo está abierta).
final focoProvider = StreamProvider.autoDispose<Map<int, MotorFoco>>((ref) {
  final c = _cliente(ref);
  if (c == null) return const Stream.empty();
  return sondear(ref, const Duration(seconds: 3), c.estadoFoco);
});

/// Temperatura y CO2 en vivo por SSE, con reconexión.
final incubadoraProvider = StreamProvider.autoDispose<LecturaIncubadora>((ref) {
  final c = _cliente(ref);
  if (c == null || !ref.watch(primerPlanoProvider)) return const Stream.empty();
  return vigilarIncubadora(c);
});

final versionProvider = FutureProvider.autoDispose<VersionMicroscopio>((ref) async {
  final c = _cliente(ref);
  if (c == null) throw const ErrorDeRed();
  return c.version();
});

final experimentosProvider = FutureProvider.autoDispose<ListaExperimentos>((ref) async {
  final c = _cliente(ref);
  if (c == null) throw const ErrorDeRed();
  return c.experimentos();
});

final detalleExperimentoProvider = FutureProvider.autoDispose.family<DetalleExperimento, String>((ref, id) async {
  final c = _cliente(ref);
  if (c == null) throw const ErrorDeRed();
  return c.experimento(id);
});

final notasProvider = FutureProvider.autoDispose.family<List<Nota>, String>((ref, id) async {
  final c = _cliente(ref);
  if (c == null) throw const ErrorDeRed();
  return c.notas(id);
});

/// Espera antes de reconectar el SSE: 1, 2, 4, 8, 16 y 30 s como máximo.
Duration esperaReconexion(int fallos) => Duration(seconds: fallos <= 0 ? 1 : (1 << (fallos - 1)).clamp(1, 30));

/// Escucha /api/temperature/stream y, si se corta, reconecta con espera
/// creciente. Mientras el SSE no funciona usa GET /api/temperature/status
/// para no dejar la tarjeta vacía. Si ni eso responde, emite el error
/// (la tarjeta lo muestra) y sigue intentando.
Stream<LecturaIncubadora> vigilarIncubadora(
  ClienteMicroscopio c, {
  Duration Function(int fallos) espera = esperaReconexion,
}) async* {
  var fallos = 0;
  while (true) {
    var recibio = false;
    try {
      await for (final lectura in c.flujoIncubadora()) {
        recibio = true;
        fallos = 0;
        yield lectura;
      }
    } catch (_) {
      // Cualquier corte (sin red, JSON roto, el servidor se reinició):
      // se reconecta abajo. Nunca se deja de vigilar.
    }
    if (!recibio) {
      try {
        yield await c.incubadora();
      } on ErrorApi catch (e, st) {
        yield* Stream<LecturaIncubadora>.error(e, st);
      }
    }
    fallos++;
    await Future<void>.delayed(espera(fallos));
  }
}
