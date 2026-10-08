import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/cliente_api.dart';
import 'preferencias.dart';

/// El cliente del microscopio al que está conectada la app (null = ninguno).
final clienteProvider = Provider<ClienteMicroscopio?>((ref) {
  final actual = ref.watch(microscopiosProvider.select((e) => e.actual));
  if (actual == null) return null;
  final c = ClienteMicroscopio(actual.uri);
  ref.onDispose(c.cerrar);
  return c;
});

/// true mientras la app está en primer plano. Lo actualiza la raíz de la
/// app (AppLifecycleListener). Todas las consultas periódicas lo miran:
/// en segundo plano no se consulta nada, y al volver se recarga todo.
class PrimerPlano extends Notifier<bool> {
  @override
  bool build() => true;

  void fijar(bool v) {
    if (v != state) state = v;
  }
}

final primerPlanoProvider = NotifierProvider<PrimerPlano, bool>(PrimerPlano.new);

/// Consulta [leer] cada [cada] mientras la app esté en primer plano.
///
/// La próxima consulta se programa cuando termina la anterior, así nunca
/// hay dos en vuelo aunque la red esté lenta. Al pasar a segundo plano se
/// corta (el provider se reconstruye) y al volver arranca con una
/// consulta inmediata.
Stream<T> sondear<T>(Ref ref, Duration cada, Future<T> Function() leer) {
  if (!ref.watch(primerPlanoProvider)) return const Stream.empty();
  Timer? timer;
  var cerrado = false;
  late final StreamController<T> ctl;

  Future<void> consultar() async {
    try {
      final v = await leer();
      if (!cerrado) ctl.add(v);
    } catch (e, st) {
      if (!cerrado) ctl.addError(e, st);
    } finally {
      if (!cerrado) timer = Timer(cada, consultar);
    }
  }

  ctl = StreamController<T>(
    onListen: consultar,
    onCancel: () {
      cerrado = true;
      timer?.cancel();
    },
  );
  ref.onDispose(() {
    cerrado = true;
    timer?.cancel();
    ctl.close();
  });
  return ctl.stream;
}
