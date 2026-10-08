import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

enum TipoAviso { info, exito, error }

class Aviso {
  const Aviso(this.texto, this.tipo, this.id);
  final String texto;
  final TipoAviso tipo;
  final int id;
}

/// Mensajes cortos que aparecen arriba y se van solos (como el toast de
/// la web, con aspecto de cápsula de iOS).
class Avisos extends Notifier<Aviso?> {
  Timer? _timer;
  int _id = 0;

  @override
  Aviso? build() {
    ref.onDispose(() => _timer?.cancel());
    return null;
  }

  void mostrar(String texto, {TipoAviso tipo = TipoAviso.info, Duration? duracion}) {
    // Una acción que termina después de cerrar la app no tiene a quién avisar.
    if (!ref.mounted) return;
    _timer?.cancel();
    state = Aviso(texto, tipo, ++_id);
    final d = duracion ?? Duration(milliseconds: (2500 + texto.length * 35).clamp(2500, 7000));
    _timer = Timer(d, ocultar);
  }

  void error(String texto) => mostrar(texto, tipo: TipoAviso.error);
  void exito(String texto) => mostrar(texto, tipo: TipoAviso.exito);

  void ocultar() {
    _timer?.cancel();
    if (ref.mounted) state = null;
  }
}

final avisosProvider = NotifierProvider<Avisos, Aviso?>(Avisos.new);
