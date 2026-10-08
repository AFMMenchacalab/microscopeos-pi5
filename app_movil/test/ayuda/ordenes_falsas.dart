import 'package:microscopeos/api/errores.dart';
import 'package:microscopeos/features/vivo/control_jog.dart';

/// Registra las órdenes en orden. Cada jog puede tardar [demora] o fallar.
class OrdenesFalsas implements OrdenesJog {
  final List<String> registro = [];
  Duration demora = Duration.zero;
  Object? fallarJog;
  int fallosStop = 0;

  int get jogs => registro.where((r) => r.startsWith('jog')).length;
  int get stops => registro.where((r) => r.startsWith('stop')).length;

  @override
  Future<void> jog(int motor, int direccion) async {
    registro.add('jog $motor $direccion');
    if (demora > Duration.zero) await Future<void>.delayed(demora);
    if (fallarJog != null) throw fallarJog!;
  }

  @override
  Future<void> pararJog(int motor) async {
    registro.add('stop $motor');
    if (fallosStop > 0) {
      fallosStop--;
      throw const ErrorDeRed();
    }
  }
}
