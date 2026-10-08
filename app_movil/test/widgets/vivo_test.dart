import 'dart:async';

import 'package:flutter/cupertino.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:microscopeos/api/modelos.dart';
import 'package:microscopeos/estado/conexion.dart';
import 'package:microscopeos/estado/datos.dart';
import 'package:microscopeos/features/vivo/panel_foco.dart';
import 'package:microscopeos/features/vivo/visor_vivo.dart';
import 'package:microscopeos/textos.dart';

import '../ayuda/adaptador_falso.dart';
import '../ayuda/montar.dart';
import '../ayuda/ordenes_falsas.dart';

/// Monta el panel del foco con un "estado" que la prueba puede cambiar.
Future<(OrdenesFalsas, StreamController<EstadoGeneral>, AdaptadorFalso)> montarFoco(
  WidgetTester tester, {
  EstadoGeneral estado = estadoLibre,
}) async {
  final ordenes = OrdenesFalsas();
  final estados = StreamController<EstadoGeneral>();
  final (c, a) = clienteFalso((p) => json({'status': 'ok', 'posicion_um': -10.0}));
  await montar(
    tester,
    CupertinoPageScaffold(
      child: ListView(children: [PanelFoco(camara: 0, ordenes: ordenes)]),
    ),
    overrides: [
      clienteProvider.overrideWithValue(c),
      estadoProvider.overrideWith((ref) => estados.stream),
      focoProvider.overrideWithValue(
        const AsyncValue.data({0: MotorFoco(camara: 0, posicionUm: -12.5, microsteps: 16)}),
      ),
      controlProvider.overrideWithValue(AsyncValue.data(controlDe(null, yo: true))),
    ],
  );
  estados.add(estado);
  await tester.pump();
  return (ordenes, estados, a);
}

Finder subir() => find.byKey(const ValueKey('foco_-1'));
Finder bajar() => find.byKey(const ValueKey('foco_1'));

void main() {
  group('foco: reglas de seguridad del jog (sección 3.5)', () {
    testWidgets('mantener apretado manda jog; soltar manda stop enseguida', (tester) async {
      final (o, _, _) = await montarFoco(tester);
      final g = await tester.startGesture(tester.getCenter(subir()));
      await tester.pump(const Duration(milliseconds: 450));
      expect(o.registro, ['jog 0 -1'], reason: 'después de 400 ms empieza el movimiento continuo');
      await tester.pump(const Duration(milliseconds: 500));
      expect(o.jogs, 3, reason: 'cada 250 ms mientras siga apretado');
      await g.up();
      await tester.pump();
      expect(o.registro.last, 'stop 0');
      final n = o.registro.length;
      await tester.pump(const Duration(seconds: 2));
      expect(o.registro.length, n, reason: 'nada más después del stop');
      await desmontar(tester);
    });

    testWidgets('un toque corto da un paso fijo, no un jog', (tester) async {
      final (o, _, a) = await montarFoco(tester);
      await tester.tap(bajar());
      await tester.pump(const Duration(milliseconds: 100));
      expect(o.registro, isEmpty);
      expect(a.a('/api/focus/move').single.json, {'motor': 0, 'direction': 1, 'um': 5.0});
      await desmontar(tester);
    });

    testWidgets('si el sistema cancela el gesto, stop', (tester) async {
      final (o, _, _) = await montarFoco(tester);
      final g = await tester.startGesture(tester.getCenter(bajar()));
      await tester.pump(const Duration(milliseconds: 700));
      expect(o.jogs, greaterThan(0));
      await g.cancel();
      await tester.pump();
      expect(o.registro.last, 'stop 0');
      await desmontar(tester);
    });

    testWidgets('si la app pasa a segundo plano, stop', (tester) async {
      final (o, _, _) = await montarFoco(tester);
      final g = await tester.startGesture(tester.getCenter(subir()));
      await tester.pump(const Duration(milliseconds: 700));
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
      await tester.pump();
      expect(o.registro.last, 'stop 0');
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
      await g.up();
      await desmontar(tester);
    });

    testWidgets('si se sale de la pantalla con el dedo apretado, stop', (tester) async {
      final (o, _, _) = await montarFoco(tester);
      final g = await tester.startGesture(tester.getCenter(subir()));
      await tester.pump(const Duration(milliseconds: 700));
      await tester.pumpWidget(const SizedBox()); // la pantalla desaparece
      await tester.pump();
      expect(o.registro.last, 'stop 0');
      await g.up();
      await tester.pump(const Duration(seconds: 2));
      expect(o.registro.where((r) => r.startsWith('jog')).length, lessThan(5));
    });

    testWidgets('si se pierde la conexión con el microscopio, stop', (tester) async {
      final (o, estados, _) = await montarFoco(tester);
      final g = await tester.startGesture(tester.getCenter(subir()));
      await tester.pump(const Duration(milliseconds: 700));
      estados.addError(Exception('sin red'));
      await tester.pump();
      expect(o.registro.last, 'stop 0');
      await g.up();
      await desmontar(tester);
    });

    testWidgets('si un jog devuelve error, deja de mandar y avisa', (tester) async {
      final (o, _, _) = await montarFoco(tester);
      final g = await tester.startGesture(tester.getCenter(subir()));
      await tester.pump(const Duration(milliseconds: 450));
      o.fallarJog = Exception('Autofoco en curso, espera a que termine');
      await tester.pump(const Duration(milliseconds: 260));
      expect(o.registro.last, 'stop 0');
      final n = o.jogs;
      await tester.pump(const Duration(seconds: 1));
      expect(o.jogs, n);
      await g.up();
      await desmontar(tester);
    });

    testWidgets('con un timelapse tomando fotos, los botones no hacen nada', (tester) async {
      final (o, _, a) = await montarFoco(tester, estado: estadoConTimelapse());
      expect(find.text(Textos.bloqueadoTimelapse), findsOneWidget);
      final g = await tester.startGesture(tester.getCenter(subir()));
      await tester.pump(const Duration(milliseconds: 800));
      await g.up();
      await tester.tap(bajar());
      await tester.pump();
      expect(o.registro, isEmpty);
      expect(a.a('/api/focus/move'), isEmpty);
      await desmontar(tester);
    });

    testWidgets('si arranca un timelapse en medio del jog, stop', (tester) async {
      final (o, estados, _) = await montarFoco(tester);
      final g = await tester.startGesture(tester.getCenter(bajar()));
      await tester.pump(const Duration(milliseconds: 700));
      estados.add(estadoConTimelapse());
      await tester.pump();
      expect(o.registro.last, 'stop 0');
      await g.up();
      await desmontar(tester);
    });

    testWidgets('muestra la altura como la web (subir = número más grande)', (tester) async {
      await montarFoco(tester);
      expect(find.text('+12.5'), findsOneWidget);
      await desmontar(tester);
    });
  });

  group('video en vivo', () {
    testWidgets('pide /live/start al abrirse y /live/stop al salir', (tester) async {
      final cuadros = StreamController<List<int>>();
      final (c, a) = clienteFalso(
        (p) => p.ruta.startsWith('/live/stream')
            ? flujo(cuadros.stream, tipo: 'multipart/x-mixed-replace')
            : json({'status': 'live'}),
      );
      await montar(
        tester,
        const VisorVivo(camara: 1),
        overrides: [
          clienteProvider.overrideWithValue(c),
          estadoProvider.overrideWithValue(const AsyncValue.data(estadoLibre)),
        ],
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 100));
      expect(a.a('/live/start/1'), hasLength(1));
      expect(a.a('/live/stream/1'), hasLength(1));
      await desmontar(tester);
      expect(a.a('/live/stop/1'), hasLength(1));
      unawaited(cuadros.close());
    });

    testWidgets('al pasar a segundo plano suelta la cámara enseguida', (tester) async {
      final (c, a) = clienteFalso(
        (p) => p.ruta.startsWith('/live/stream')
            ? flujo(const Stream.empty(), tipo: 'multipart/x-mixed-replace')
            : json({'status': 'live'}),
      );
      await montar(
        tester,
        const VisorVivo(camara: 0),
        overrides: [
          clienteProvider.overrideWithValue(c),
          estadoProvider.overrideWithValue(const AsyncValue.data(estadoLibre)),
        ],
      );
      await tester.pump(const Duration(milliseconds: 100));
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
      await tester.pump(const Duration(milliseconds: 100));
      expect(a.a('/live/stop/0'), hasLength(1));
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
      await tester.pump(const Duration(milliseconds: 100));
      expect(a.a('/live/start/0').length, greaterThanOrEqualTo(2), reason: 'al volver, vuelve a pedir el vivo');
      await desmontar(tester);
    });

    testWidgets('con un timelapse tomando fotos no pide el vivo y muestra el último ciclo', (tester) async {
      final (c, a) = clienteFalso((p) => texto('', codigo: 404));
      await montar(
        tester,
        const VisorVivo(camara: 0),
        overrides: [
          clienteProvider.overrideWithValue(c),
          estadoProvider.overrideWithValue(AsyncValue.data(estadoConTimelapse())),
        ],
      );
      await tester.pump(const Duration(milliseconds: 100));
      expect(a.a('/live/start/0'), isEmpty);
      expect(find.text(Textos.vivoUltimoCiclo), findsOneWidget);
      await desmontar(tester);
    });
  });
}
