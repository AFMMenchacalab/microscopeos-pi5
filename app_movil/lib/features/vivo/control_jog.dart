import 'dart:async';

/// Las dos órdenes que necesita el movimiento continuo del foco.
/// En la app las cumple ClienteMicroscopio; en las pruebas, un falso.
abstract interface class OrdenesJog {
  /// POST /api/focus/jog. `direccion` 1 = baja, -1 = sube.
  Future<void> jog(int motor, int direccion);

  /// POST /api/focus/jog/stop
  Future<void> pararJog(int motor);
}

/// Movimiento continuo del foco mientras el dedo está apretado.
///
/// REGLAS DE SEGURIDAD (un motor que sigue girando puede romper el
/// portaobjetos o el objetivo):
///
/// - Mientras está activo, manda /api/focus/jog cada [cada] (250 ms). El
///   servidor para el motor solo si deja de recibirlos durante 1.5 s.
/// - [detener] manda /api/focus/jog/stop ENSEGUIDA. La pantalla lo llama
///   al soltar, al cancelarse el gesto, al salir de la pantalla y cuando
///   la app pasa a segundo plano.
/// - Si un jog falla (error del microscopio o sin conexión), deja de
///   reenviar y manda el stop.
/// - Nunca hay dos jog en vuelo a la vez: si el anterior no contestó, se
///   salta el turno (no se apilan pedidos con la red lenta).
/// - Si un jog que salió ANTES de soltar contesta DESPUÉS del stop, pudo
///   llegar al servidor después del stop y volver a arrancar el motor:
///   se manda otro stop.
class ControlJog {
  ControlJog(this._ordenes, {this.cada = const Duration(milliseconds: 250), this.alFallar});

  final OrdenesJog _ordenes;
  final Duration cada;

  /// Se llama con el error cuando un jog falla y el movimiento se corta.
  final void Function(Object error)? alFallar;

  Timer? _timer;
  int? _motor;
  int _direccion = 1;
  bool _enVuelo = false;

  /// Cuenta los movimientos: un jog que contesta tarde sabe si el suyo
  /// ya terminó.
  int _sesion = 0;

  bool get activo => _motor != null;
  int? get motor => _motor;

  void iniciar(int motor, int direccion) {
    if (activo) detener();
    _sesion++;
    _motor = motor;
    _direccion = direccion > 0 ? 1 : -1;
    _enviar();
    _timer = Timer.periodic(cada, (_) => _enviar());
  }

  /// Para el motor. Se puede llamar de más: si no hay movimiento, no
  /// hace nada.
  void detener() {
    final m = _motor;
    _timer?.cancel();
    _timer = null;
    _motor = null;
    if (m != null) _mandarStop(m);
  }

  Future<void> _enviar() async {
    final m = _motor;
    if (m == null || _enVuelo) return;
    final sesion = _sesion;
    _enVuelo = true;
    try {
      await _ordenes.jog(m, _direccion);
      if (_motor != m || _sesion != sesion) {
        // Este jog salió antes de soltar y contestó después del stop.
        _mandarStop(m);
      }
    } catch (e) {
      if (_motor == m && _sesion == sesion) {
        detener();
        alFallar?.call(e);
      } else {
        _mandarStop(m);
      }
    } finally {
      _enVuelo = false;
    }
  }

  Future<void> _mandarStop(int motor) async {
    try {
      await _ordenes.pararJog(motor);
    } catch (_) {
      // Un reintento: si tampoco llega, el watchdog del servidor lo para
      // a los 1.5 s.
      try {
        await Future<void>.delayed(const Duration(milliseconds: 300));
        await _ordenes.pararJog(motor);
      } catch (_) {}
    }
  }
}
