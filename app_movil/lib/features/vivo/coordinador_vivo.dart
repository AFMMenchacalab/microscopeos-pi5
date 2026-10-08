import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../api/cliente_api.dart';
import '../../estado/conexion.dart';

/// Lleva la cuenta de quién está mirando el vivo de cada cámara, para
/// que /live/start y /live/stop no se pisen.
///
/// Al abrir la pantalla completa, el visor chico deja de mirar y el
/// grande empieza: si cada uno mandara su propio stop/start, el stop del
/// chico podría llegar después del start del grande y cortar el video.
/// Con la cuenta, el stop se manda recién cuando NADIE mira esa cámara
/// (con una pequeña espera, por si otro visor la pide enseguida), o al
/// momento si la app pasa a segundo plano.
class CoordinadorVivo {
  CoordinadorVivo(this._cliente, {this.esperaParada = const Duration(milliseconds: 1500)});

  final ClienteMicroscopio _cliente;
  final Duration esperaParada;
  final Map<int, int> _usos = {};
  final Map<int, Timer> _paradas = {};

  int usos(int cam) => _usos[cam] ?? 0;

  /// Empieza a mirar una cámara: POST /live/start/{cam}. Puede fallar
  /// (p. ej. «Timelapse en curso»); igual cuenta como uso, así que hay
  /// que llamar a [soltar] después.
  Future<void> usar(int cam) {
    _paradas.remove(cam)?.cancel();
    _usos[cam] = usos(cam) + 1;
    return _cliente.iniciarVivo(cam);
  }

  /// Vuelve a pedir el vivo sin sumar un uso (después de que el
  /// servidor cortó el flujo, p. ej. por una foto).
  Future<void> reactivar(int cam) => _cliente.iniciarVivo(cam);

  void soltar(int cam, {bool inmediato = false}) {
    final n = usos(cam) - 1;
    _usos[cam] = n < 0 ? 0 : n;
    if (usos(cam) > 0) return;
    _paradas.remove(cam)?.cancel();
    if (inmediato) {
      _parar(cam);
    } else {
      _paradas[cam] = Timer(esperaParada, () => _parar(cam));
    }
  }

  void _parar(int cam) {
    _paradas.remove(cam);
    if (usos(cam) == 0) _cliente.detenerVivo(cam).ignore();
  }

  /// Al cambiar de microscopio o cerrar: se detiene ya todo lo que
  /// estaba en uso o esperando su stop.
  void cerrar() {
    final camaras = {..._paradas.keys, ..._usos.entries.where((e) => e.value > 0).map((e) => e.key)};
    for (final t in _paradas.values) {
      t.cancel();
    }
    _paradas.clear();
    _usos.clear();
    for (final cam in camaras) {
      _cliente.detenerVivo(cam).ignore();
    }
  }
}

final coordinadorVivoProvider = Provider<CoordinadorVivo?>((ref) {
  final c = ref.watch(clienteProvider);
  if (c == null) return null;
  final coord = CoordinadorVivo(c);
  ref.onDispose(coord.cerrar);
  return coord;
});

/// true mientras se toma una foto: los visores sueltan el vivo para que
/// la cámara no cambie de modo entre las 4 fotos de un relieve DPC.
class PausaVivo extends Notifier<bool> {
  @override
  bool build() => false;

  void fijar(bool v) => state = v;
}

final pausaVivoProvider = NotifierProvider<PausaVivo, bool>(PausaVivo.new);
