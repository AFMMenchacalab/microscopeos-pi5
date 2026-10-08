import 'dart:async';
import 'dart:io';

import 'package:flutter/services.dart';
import 'package:flutter_reactive_ble/flutter_reactive_ble.dart';

import 'protocolo.dart';

/// Estado del Bluetooth del teléfono, en palabras de la app.
enum EstadoBluetooth { listo, apagado, sinPermiso, noDisponible, desconocido }

/// Buscar microscopios cerca y conectarse a uno (flutter_reactive_ble).
class BluetoothMicroscopios {
  BluetoothMicroscopios({FlutterReactiveBle? ble}) : _ble = ble ?? FlutterReactiveBle();

  final FlutterReactiveBle _ble;
  static const _permisos = MethodChannel('microscopeos/permisos');

  Stream<EstadoBluetooth> get estado => _ble.statusStream.map(
    (s) => switch (s) {
      BleStatus.ready => EstadoBluetooth.listo,
      BleStatus.poweredOff => EstadoBluetooth.apagado,
      BleStatus.unauthorized || BleStatus.locationServicesDisabled => EstadoBluetooth.sinPermiso,
      BleStatus.unsupported => EstadoBluetooth.noDisponible,
      BleStatus.unknown => EstadoBluetooth.desconocido,
    },
  );

  /// Android 12+ pide permiso para buscar y conectarse por Bluetooth (el
  /// paquete no lo pide solo). En iPhone el sistema pregunta la primera
  /// vez, sin hacer nada.
  Future<bool> pedirPermiso() async {
    if (!Platform.isAndroid) return true;
    try {
      return await _permisos.invokeMethod<bool>('bluetooth') ?? false;
    } on PlatformException {
      return false;
    }
  }

  /// Microscopios que se anuncian cerca (solo los de MicroscopeOS).
  Stream<EquipoCercano> buscar() => _ble
      .scanForDevices(withServices: [Uuid.parse(uuidServicio)], scanMode: ScanMode.lowLatency)
      .map((d) => EquipoCercano(id: d.id, nombre: d.name.isEmpty ? 'MicroscopeOS' : d.name, senal: d.rssi));

  /// Se conecta y deja lista la sesión.
  Future<SesionEquipo> conectar(String id) async {
    final conectado = Completer<void>();
    final sub = _ble
        .connectToDevice(
          id: id,
          servicesWithCharacteristicsToDiscover: {
            Uuid.parse(uuidServicio): [Uuid.parse(uuidEstado), Uuid.parse(uuidRedes), Uuid.parse(uuidComando)],
          },
          connectionTimeout: const Duration(seconds: 15),
        )
        .listen(
          (u) {
            if (conectado.isCompleted) return;
            if (u.connectionState == DeviceConnectionState.connected) {
              conectado.complete();
            } else if (u.connectionState == DeviceConnectionState.disconnected) {
              conectado.completeError(const ErrorEquipo('No se pudo conectar con el microscopio'));
            }
          },
          onError: (Object e) {
            if (!conectado.isCompleted) {
              conectado.completeError(const ErrorEquipo('No se pudo conectar con el microscopio'));
            }
          },
        );
    try {
      await conectado.future.timeout(const Duration(seconds: 20));
    } catch (_) {
      await sub.cancel();
      rethrow;
    }
    // Paquetes más grandes: el estado y los comandos entran de una vez.
    try {
      await _ble.requestMtu(deviceId: id, mtu: 247);
    } catch (_) {}
    return SesionEquipo(_EnlaceReactive(_ble, id, sub));
  }
}

class _EnlaceReactive implements EnlaceEquipo {
  _EnlaceReactive(this._ble, this._id, this._conexion);

  final FlutterReactiveBle _ble;
  final String _id;
  final StreamSubscription<ConnectionStateUpdate> _conexion;

  QualifiedCharacteristic _car(String uuid) =>
      QualifiedCharacteristic(serviceId: Uuid.parse(uuidServicio), characteristicId: Uuid.parse(uuid), deviceId: _id);

  @override
  Future<List<int>> leerEstado() => _ble.readCharacteristic(_car(uuidEstado));

  @override
  Future<List<int>> leerRedes() => _ble.readCharacteristic(_car(uuidRedes));

  @override
  Future<void> escribirComando(List<int> datos) =>
      _ble.writeCharacteristicWithResponse(_car(uuidComando), value: datos);

  @override
  Stream<List<int>> get cambiosDeEstado => _ble.subscribeToCharacteristic(_car(uuidEstado));

  /// Cancelar la suscripción de la conexión la cierra.
  @override
  Future<void> cerrar() => _conexion.cancel();
}
