import 'package:flutter/cupertino.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:microscopeos/api/modelos.dart';
import 'package:microscopeos/estado/conexion.dart';
import 'package:microscopeos/estado/datos.dart';
import 'package:microscopeos/features/timelapse/nuevo_timelapse_page.dart';
import 'package:microscopeos/features/vivo/panel_luz.dart';
import 'package:microscopeos/textos.dart';

import '../ayuda/adaptador_falso.dart';
import '../ayuda/montar.dart';

Future<AdaptadorFalso> montarLuz(WidgetTester tester, MatrizLuz m) async {
  final (c, a) = clienteFalso((p) => json({'status': 'ok', 'matrices': {}}));
  await montar(
    tester,
    const CupertinoPageScaffold(child: SingleChildScrollView(child: PanelLuz(camara: 0))),
    overrides: [
      clienteProvider.overrideWithValue(c),
      estadoProvider.overrideWithValue(const AsyncValue.data(estadoLibre)),
      luzProvider.overrideWithValue(AsyncValue.data({0: m, 1: m})),
    ],
  );
  await tester.pump();
  return a;
}

void main() {
  group('colores de la luz', () {
    testWidgets('campo claro: tocar un color lo manda a /light/colores', (tester) async {
      final a = await montarLuz(tester, const MatrizLuz(encendida: true, porcentaje: 60));
      expect(find.text(Textos.luzColorCampo), findsOneWidget);
      await tester.tap(find.bySemanticsLabel(Textos.colorVerde));
      await tester.pump(const Duration(milliseconds: 100));
      expect(a.a('/light/colores').single.json, {
        'camaras': [0, 1],
        'campo': '00FF00',
      });
      await desmontar(tester);
    });

    testWidgets('relieve: dice cuál es el recomendado', (tester) async {
      await montarLuz(tester, const MatrizLuz(encendida: true, modo: ModoLuz.left, colorRelieve: '00FF00'));
      expect(find.text(Textos.luzColorRelieve), findsOneWidget);
      expect(find.text('${Textos.colorVerde} ${Textos.colorRecomendado}'), findsOneWidget);
      await desmontar(tester);
    });

    testWidgets('Rheinberg: centro y anillo van con el modo', (tester) async {
      final a = await montarLuz(tester, const MatrizLuz(encendida: true, modo: ModoLuz.rheinberg, porcentaje: 40));
      expect(find.text(Textos.luzColorCentro), findsOneWidget);
      expect(find.text(Textos.luzColorAnillo), findsOneWidget);
      // El primer «Rojo» es el del centro.
      await tester.tap(find.bySemanticsLabel(Textos.colorRojo).first);
      await tester.pump(const Duration(milliseconds: 100));
      final j = a.a('/light/set').single.json;
      expect(j['color_centro'], 'FF0000');
      expect(j['color_anillo'], 'FF6A00');
      await desmontar(tester);
    });

    testWidgets('«Otro color» abre más colores', (tester) async {
      final a = await montarLuz(tester, const MatrizLuz(encendida: true, porcentaje: 60));
      await tester.tap(find.bySemanticsLabel(Textos.colorOtro));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500)); // la hoja sube
      expect(find.text(Textos.colorOtroTitulo), findsOneWidget);
      await tester.tap(find.bySemanticsLabel(Textos.colorMagenta));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      expect(a.a('/light/colores').single.json['campo'], 'FF00FF');
      await desmontar(tester);
    });
  });

  testWidgets('nuevo timelapse: las opciones del relieve aparecen solo en relieve DPC', (tester) async {
    final (c, a) = clienteFalso((p) => json({'status': 'started'}));
    await montar(
      tester,
      const NuevoTimelapsePage(),
      overrides: [
        clienteProvider.overrideWithValue(c),
        estadoProvider.overrideWithValue(const AsyncValue.data(estadoLibre)),
        experimentosProvider.overrideWithValue(
          AsyncValue.data(
            ListaExperimentos.desdeJson({
              'experimentos': [],
              'espacio': {'libre_bytes': 100000000000},
            }),
          ),
        ),
      ],
    );
    await tester.pump();
    expect(find.text(Textos.relieveProcesar), findsNothing);
    await tester.tap(find.text(Textos.nuevoTipoRelieve));
    await tester.pump();
    expect(find.text(Textos.relieveProcesar), findsOneWidget);
    expect(find.text(Textos.relieveBorrar), findsOneWidget);
    // Sin calcular el relieve, las demás opciones no aplican.
    await tester.tap(find.byType(CupertinoSwitch).first);
    await tester.pump();
    expect(find.text(Textos.relieveBorrar), findsNothing);
    expect(find.text(Textos.relieveSinProcesar), findsOneWidget);
    expect(a.pedidos, isEmpty);
    await desmontar(tester);
  });
}
