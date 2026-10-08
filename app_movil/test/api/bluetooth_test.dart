import 'dart:async';

import 'package:flutter/cupertino.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:microscopeos/api/bluetooth/protocolo.dart';
import 'package:microscopeos/estado/preferencias.dart';
import 'package:microscopeos/features/configuracion/buscar_bluetooth_page.dart';
import 'package:microscopeos/textos.dart';

import '../ayuda/equipo_falso.dart';
import '../ayuda/montar.dart';

void main() {
  group('protocolo Bluetooth', () {
    test('estado: nombre, red e IP para guardar', () async {
      final eq = EquipoFalso(conectado: true)
        ..red = 'Lab'
        ..ip = '192.168.1.50';
      final e = await SesionEquipo(eq).estado();
      expect(e.nombre, 'MicroscopeOS-3F2A');
      expect(e.direccion.toString(), 'http://192.168.1.50:8000');
      expect(EstadoEquipo.desdeJson({'nombre': 'x', 'conectado': false, 'ip': null}).direccion, isNull);
    });

    test('código y Wi-Fi: cada comando lleva un id y espera su respuesta', () async {
      final eq = EquipoFalso();
      final s = SesionEquipo(eq);
      await s.pedirCodigo();
      await s.enviarCodigo(['rojo', 'verde', 'azul']);
      final e = await s.configurarWifi('Laboratorio', 'clave-del-lab');
      expect(e.conectado, isTrue);
      expect(e.direccion.toString(), 'http://192.168.1.77:8000');
      expect(eq.escritos.map((p) => p['cmd']), ['pedir_codigo', 'codigo', 'wifi']);
      expect(eq.escritos.map((p) => p['id']).toSet(), hasLength(3), reason: 'ids distintos');
      expect(eq.escritos.last, containsPair('clave', 'clave-del-lab'));
    });

    test('los errores del microscopio llegan con su mensaje', () async {
      final s = SesionEquipo(EquipoFalso());
      await s.pedirCodigo();
      await expectLater(
        s.enviarCodigo(['azul', 'azul', 'azul']),
        throwsA(isA<ErrorEquipo>().having((e) => e.mensaje, 'mensaje', contains('no coinciden'))),
      );
      await expectLater(
        s.configurarWifi('Lab', 'clave-del-lab'),
        throwsA(isA<ErrorEquipo>().having((e) => e.mensaje, 'mensaje', 'Primero confirma los colores de la luz')),
      );
    });

    test('si se pierde la notificación, relee el estado', () async {
      final eq = EquipoFalso(notificar: false);
      final s = SesionEquipo(eq, cadaCuantoLeer: const Duration(milliseconds: 20));
      final e = await s.pedirCodigo();
      expect(e.paso, 'codigo');
    });

    test('si el «¿Vincular?» no se acepta, la escritura no queda colgada', () async {
      final s = SesionEquipo(_Colgado(), esperaEscritura: const Duration(milliseconds: 100));
      await expectLater(
        s.pedirCodigo(),
        throwsA(isA<ErrorEquipo>().having((e) => e.mensaje, 'mensaje', contains('Vincular'))),
      );
    });

    test('si no contesta, avisa en vez de quedarse esperando', () async {
      final eq = _Mudo();
      final s = SesionEquipo(
        eq,
        esperaComando: const Duration(milliseconds: 200),
        cadaCuantoLeer: const Duration(milliseconds: 50),
      );
      await expectLater(s.pedirCodigo(), throwsA(isA<ErrorEquipo>()));
    });
  });

  group('pantalla «Microscopios cerca»', () {
    testWidgets('microscopio nuevo: colores, red, contraseña y guardar', (tester) async {
      final eq = EquipoFalso();
      await montar(
        tester,
        const BuscarBluetoothPage(),
        overrides: [bluetoothProvider.overrideWithValue(BluetoothFalso(eq))],
      );
      Future<void> esperar() => tester.pump(const Duration(milliseconds: 300));
      await esperar();
      expect(find.text('MOS-3F2A'), findsOneWidget);
      await tester.tap(find.text('MOS-3F2A'));
      await esperar();
      expect(find.text(Textos.btSinRed), findsOneWidget);
      await tester.tap(find.text(Textos.btConfigurarWifi));
      await esperar();
      expect(find.text(Textos.btCodigoTitulo), findsOneWidget);
      // Primero mal…
      for (final c in ['azul', 'azul', 'azul']) {
        await tester.tap(find.bySemanticsLabel(c));
        await tester.pump();
      }
      await esperar();
      expect(find.textContaining('no coinciden'), findsOneWidget);
      // …y después bien.
      for (final c in ['rojo', 'verde', 'azul']) {
        await tester.tap(find.bySemanticsLabel(c));
        await tester.pump();
      }
      await esperar();
      expect(find.text(Textos.btElegirRed), findsOneWidget);
      await tester.tap(find.text('Laboratorio'));
      await esperar();
      await tester.enterText(find.byType(CupertinoTextField).first, 'clave-del-lab');
      await tester.tap(find.text(Textos.btConectarRed));
      await esperar();
      expect(find.text(Textos.btListo), findsOneWidget);
      expect(find.text(Textos.btConectadoA('Laboratorio', '192.168.1.77')), findsOneWidget);
      await tester.tap(find.text(Textos.btGuardarYUsar));
      await esperar();
      final ctx = tester.element(find.byType(CupertinoApp));
      final guardado = ProviderScope.containerOf(ctx).read(microscopiosProvider).actual;
      expect(guardado?.url, 'http://192.168.1.77:8000');
      expect(guardado?.nombre, 'MicroscopeOS-3F2A');
      await desmontar(tester);
    });

    testWidgets('resincronizar: ya tiene red, solo se guarda su IP nueva', (tester) async {
      final eq = EquipoFalso(conectado: true, configurable: false)
        ..red = 'Lab'
        ..ip = '192.168.1.99';
      await montar(
        tester,
        const BuscarBluetoothPage(),
        overrides: [bluetoothProvider.overrideWithValue(BluetoothFalso(eq))],
      );
      await tester.pump(const Duration(milliseconds: 300));
      await tester.tap(find.text('MOS-3F2A'));
      await tester.pump(const Duration(milliseconds: 300));
      expect(find.text(Textos.btConectadoA('Lab', '192.168.1.99')), findsOneWidget);
      expect(find.text(Textos.btConfigurarWifi), findsNothing, reason: 'con red y fuera de la ventana no se configura');
      expect(find.textContaining('reinícialo'), findsOneWidget);
      await tester.tap(find.text(Textos.btGuardarYUsar));
      await tester.pump(const Duration(milliseconds: 300));
      expect(eq.escritos, isEmpty, reason: 'resincronizar solo lee');
      await desmontar(tester);
    });
  });
}

class _Colgado extends EquipoFalso {
  @override
  Future<void> escribirComando(List<int> datos) => Completer<void>().future;
}

class _Mudo extends EquipoFalso {
  @override
  Future<void> escribirComando(List<int> datos) async {}
}
