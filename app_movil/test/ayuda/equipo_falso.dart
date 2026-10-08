import 'dart:async';
import 'dart:convert';

import 'package:microscopeos/api/bluetooth/bluetooth.dart';
import 'package:microscopeos/api/bluetooth/protocolo.dart';

/// Un microscopio por Bluetooth, falso: se porta como
/// core/configuracion_red.py + core/servicio_bluetooth.py.
class EquipoFalso implements EnlaceEquipo {
  EquipoFalso({this.conectado = false, this.configurable = true, this.notificar = true});

  bool conectado;
  bool configurable;

  /// false = las notificaciones se pierden (la app tiene que releer).
  bool notificar;
  String? red;
  String? ip;
  int ultimo = 0;
  String? error;
  String paso = 'listo';
  List<String>? codigo;
  bool autorizado = false;
  final escritos = <Map<String, dynamic>>[];
  final _cambios = StreamController<List<int>>.broadcast();
  bool cerrado = false;

  Map<String, dynamic> get _estado => {
    'v': 1,
    'nombre': 'MicroscopeOS-3F2A',
    'conectado': conectado,
    'red': red,
    'ip': ip,
    'puerto': 8000,
    'configurable': configurable,
    'motivo': configurable ? 'Sin red' : 'Ya tiene red. Para cambiarla, reinícialo',
    'paso': paso,
    'error': error,
    'intentos': 3,
    'bloqueado_s': 0,
    'autorizado': autorizado,
    'ultimo': ultimo,
  };

  @override
  Future<List<int>> leerEstado() async => utf8.encode(jsonEncode(_estado));

  @override
  Future<List<int>> leerRedes() async => utf8.encode(
    jsonEncode([
      ['Laboratorio', 82, true],
      ['Invitados', 41, false],
    ]),
  );

  @override
  Stream<List<int>> get cambiosDeEstado => _cambios.stream;

  @override
  Future<void> escribirComando(List<int> datos) async {
    final p = (jsonDecode(utf8.decode(datos)) as Map).cast<String, dynamic>();
    escritos.add(p);
    // Se atiende «en otro hilo», como el servicio real.
    scheduleMicrotask(() => _atender(p));
  }

  void _atender(Map<String, dynamic> p) {
    error = null;
    switch (p['cmd']) {
      case 'pedir_codigo':
        codigo = ['rojo', 'verde', 'azul'];
        paso = 'codigo';
      case 'codigo':
        if ((p['colores'] as List).join(',') == codigo?.join(',')) {
          autorizado = true;
          paso = 'listo';
        } else {
          error = 'Los colores no coinciden. Quedan 2 intentos';
        }
      case 'wifi':
        if (!autorizado) {
          error = 'Primero confirma los colores de la luz';
        } else if (p['clave'] == 'mala1234') {
          error = 'La contraseña del Wi-Fi no es correcta';
          paso = 'error';
        } else {
          conectado = true;
          red = p['ssid'] as String;
          ip = '192.168.1.77';
          paso = 'conectado';
          autorizado = false;
        }
    }
    ultimo = p['id'] as int;
    if (notificar) _cambios.add(utf8.encode(jsonEncode(_estado)));
  }

  @override
  Future<void> cerrar() async => cerrado = true;
}

/// El Bluetooth del teléfono, falso: encuentra un microscopio.
class BluetoothFalso implements BluetoothMicroscopios {
  BluetoothFalso(this.equipo);
  final EquipoFalso equipo;

  @override
  Stream<EstadoBluetooth> get estado => Stream.value(EstadoBluetooth.listo);

  @override
  Future<bool> pedirPermiso() async => true;

  @override
  Stream<EquipoCercano> buscar() => Stream.value(const EquipoCercano(id: 'AA:BB', nombre: 'MOS-3F2A', senal: -50));

  @override
  Future<SesionEquipo> conectar(String id) async =>
      SesionEquipo(equipo, cadaCuantoLeer: const Duration(milliseconds: 50));
}
