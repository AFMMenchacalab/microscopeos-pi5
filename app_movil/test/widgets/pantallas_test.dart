import 'package:flutter/cupertino.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:microscopeos/api/modelos.dart';
import 'package:microscopeos/estado/conexion.dart';
import 'package:microscopeos/estado/datos.dart';
import 'package:microscopeos/features/conexion/conexion_page.dart';
import 'package:microscopeos/features/experimentos/experimentos_page.dart';
import 'package:microscopeos/features/inicio/inicio_page.dart';
import 'package:microscopeos/features/timelapse/estimacion.dart';
import 'package:microscopeos/features/vivo/panel_luz.dart';
import 'package:microscopeos/textos.dart';

import '../ayuda/adaptador_falso.dart';
import '../ayuda/montar.dart';

void main() {
  group('Inicio', () {
    testWidgets('muestra quién controla, la incubadora y el timelapse en curso', (tester) async {
      final (c, _) = clienteFalso((p) => texto('', codigo: 404));
      await montar(
        tester,
        InicioPage(irAVivo: () {}, irAExperimentos: () {}),
        overrides: [
          clienteProvider.overrideWithValue(c),
          estadoProvider.overrideWithValue(AsyncValue.data(estadoConTimelapse())),
          controlProvider.overrideWithValue(AsyncValue.data(controlDe('ana@lab.mx'))),
          incubadoraProvider.overrideWithValue(
            const AsyncValue.data(
              LecturaIncubadora(
                conectada: true,
                temperatura: 36.94,
                temperaturaPedida: 37,
                co2Ppm: 41000,
                co2PedidoPpm: 40000,
                humedad: 88.4,
              ),
            ),
          ),
        ],
      );
      await tester.pump();
      expect(find.text(Textos.controlDeOtro('ana')), findsOneWidget);
      expect(find.text(Textos.controlTomar), findsOneWidget);
      expect(find.text('36.9'), findsOneWidget);
      expect(find.text('4.1'), findsOneWidget);
      expect(find.text('Células HeLa día 2'), findsOneWidget);
      expect(find.text(Textos.timelapsePausar), findsOneWidget);
      expect(find.text(Textos.timelapseDetener), findsOneWidget);
      await desmontar(tester);
    });

    testWidgets('detener el timelapse pide confirmación antes', (tester) async {
      final (c, a) = clienteFalso(
        (p) => p.ruta == '/timelapse/stop' ? json({'status': 'stopped'}) : texto('', codigo: 404),
      );
      await montar(
        tester,
        InicioPage(irAVivo: () {}, irAExperimentos: () {}),
        overrides: [
          clienteProvider.overrideWithValue(c),
          estadoProvider.overrideWithValue(AsyncValue.data(estadoConTimelapse())),
          controlProvider.overrideWithValue(AsyncValue.data(controlDe(null, yo: true))),
          incubadoraProvider.overrideWithValue(const AsyncValue.data(LecturaIncubadora())),
        ],
      );
      Future<void> esperar() => tester.pump(const Duration(milliseconds: 400));
      await esperar();
      await tester.scrollUntilVisible(find.text(Textos.timelapseDetener), 200);
      await tester.tap(find.text(Textos.timelapseDetener));
      await esperar();
      expect(find.text(Textos.timelapseDetenerPregunta), findsOneWidget);
      await tester.tap(find.text(Textos.cancelar));
      await esperar();
      expect(a.a('/timelapse/stop'), isEmpty, reason: 'cancelar no detiene nada');

      await tester.tap(find.text(Textos.timelapseDetener));
      await esperar();
      await tester.tap(
        find.descendant(of: find.byType(CupertinoAlertDialog), matching: find.text(Textos.timelapseDetener)),
      );
      await esperar();
      expect(a.a('/timelapse/stop'), hasLength(1));
      expect(find.text(Textos.timelapseDetenido), findsOneWidget);
      await desmontar(tester);
    });

    testWidgets('sin timelapse ofrece empezar uno', (tester) async {
      await montar(
        tester,
        InicioPage(irAVivo: () {}, irAExperimentos: () {}),
        overrides: [
          estadoProvider.overrideWithValue(const AsyncValue.data(estadoLibre)),
          controlProvider.overrideWithValue(AsyncValue.data(controlDe(null))),
          incubadoraProvider.overrideWithValue(
            const AsyncValue.data(LecturaIncubadora(error: 'Arduino no encontrado')),
          ),
        ],
      );
      await tester.pump();
      expect(find.text(Textos.timelapseNinguno), findsOneWidget);
      expect(find.text(Textos.timelapseNuevo), findsOneWidget);
      expect(find.text(Textos.incubadoraSinConexion), findsOneWidget);
      expect(find.text('Arduino no encontrado'), findsOneWidget);
      await desmontar(tester);
    });

    testWidgets('si se pierde la conexión lo dice', (tester) async {
      await montar(
        tester,
        InicioPage(irAVivo: () {}, irAExperimentos: () {}),
        overrides: [
          estadoProvider.overrideWithValue(AsyncValue.error(Exception('x'), StackTrace.empty)),
          controlProvider.overrideWithValue(const AsyncValue.loading()),
          incubadoraProvider.overrideWithValue(const AsyncValue.loading()),
        ],
      );
      await tester.pump();
      expect(find.textContaining(Textos.sinConexion), findsOneWidget);
      expect(find.text(Textos.reintentar), findsOneWidget);
      await desmontar(tester);
    });
  });

  group('Conexión', () {
    testWidgets('rechaza HTTP sin cifrar fuera de la red local', (tester) async {
      await montar(tester, const ConexionPage());
      await tester.enterText(find.byType(CupertinoTextField).at(1), 'http://8.8.8.8');
      await tester.tap(find.text(Textos.conexionProbar));
      await tester.pump();
      expect(find.text(Textos.conexionDireccionInvalida), findsOneWidget);
      await desmontar(tester);
    });
  });

  group('Experimentos', () {
    testWidgets('lista con nombre, fotos, «en curso» y espacio libre', (tester) async {
      final (c, _) = clienteFalso((p) => texto('', codigo: 404));
      await montar(
        tester,
        const ExperimentosPage(),
        overrides: [
          clienteProvider.overrideWithValue(c),
          experimentosProvider.overrideWithValue(
            AsyncValue.data(
              ListaExperimentos.desdeJson({
                'experimentos': [
                  {
                    'id': '2026-10-07_2012_TL_app',
                    'nombre': 'TL app',
                    'tipo': 'timelapse',
                    'inicio': '2026-10-07T20:12:00',
                    'n_fotos': 8,
                    'bytes': 12000000,
                    'portada': 'cam0/0001.tif',
                    'en_curso': true,
                  },
                  {
                    'id': '2026-10-06_Muestra_B',
                    'nombre': 'Muestra B',
                    'tipo': 'fotos',
                    'n_fotos': 3,
                    'bytes': 4500000,
                  },
                ],
                'espacio': {'libre_bytes': 16400000000, 'fotos_que_caben': 1015},
              }),
            ),
          ),
        ],
      );
      await tester.pump();
      expect(find.text('TL app'), findsOneWidget);
      expect(find.text('Muestra B'), findsOneWidget);
      expect(find.text(Textos.expEnCurso), findsOneWidget);
      expect(find.textContaining('8 fotos'), findsOneWidget);
      expect(find.text(Textos.expLibre('16.4 GB', 1015)), findsOneWidget);
      await desmontar(tester);
    });
  });

  group('Luz', () {
    testWidgets('elegir un tipo de luz lo manda a las dos cámaras', (tester) async {
      final (c, a) = clienteFalso((p) => json({'status': 'ok'}));
      await montar(
        tester,
        const CupertinoPageScaffold(child: SingleChildScrollView(child: PanelLuz(camara: 0))),
        overrides: [
          clienteProvider.overrideWithValue(c),
          estadoProvider.overrideWithValue(const AsyncValue.data(estadoLibre)),
          luzProvider.overrideWithValue(
            const AsyncValue.data({
              0: MatrizLuz(encendida: false, modo: ModoLuz.full, porcentaje: 70),
              1: MatrizLuz(encendida: false, modo: ModoLuz.full, porcentaje: 70),
            }),
          ),
        ],
      );
      await tester.pump();
      await tester.tap(find.text(Textos.luzFondoNegro));
      await tester.pump(const Duration(milliseconds: 100));
      expect(a.a('/light/set').single.json, {
        'modo': 'ring',
        'percent': 70,
        'camaras': [0, 1],
      });
      await desmontar(tester);
    });

    testWidgets('con un timelapse tomando fotos está bloqueada', (tester) async {
      final (c, a) = clienteFalso((p) => json({'status': 'ok'}));
      await montar(
        tester,
        const CupertinoPageScaffold(child: SingleChildScrollView(child: PanelLuz(camara: 0))),
        overrides: [
          clienteProvider.overrideWithValue(c),
          estadoProvider.overrideWithValue(AsyncValue.data(estadoConTimelapse())),
          luzProvider.overrideWithValue(const AsyncValue.data({0: MatrizLuz(porcentaje: 70)})),
        ],
      );
      await tester.pump();
      expect(find.text(Textos.bloqueadoTimelapse), findsOneWidget);
      await tester.tap(find.text(Textos.luzFondoNegro), warnIfMissed: false);
      await tester.pump();
      expect(a.pedidos, isEmpty);
      await desmontar(tester);
    });
  });

  group('Estimación del timelapse (igual que la web y api.py)', () {
    test('campo claro: un TIFF de 16 MB por cámara y ciclo', () {
      final e = estimarTimelapse(modo: ModoFoto.blanco, intervaloS: 300, duracionS: 3600, camaras: 2);
      expect(e.fotosPorCamara, 12);
      expect(e.bytes, 12 * 2 * 3280 * 2464 * 2);
    });

    test('relieve DPC con las opciones por defecto (dpc.bytes_por_ciclo)', () {
      // 2*crudo*0.75 + crudo*0.25*0.75 + 1.5 MB, con crudo = 16 163 840
      expect(bytesPorCiclo(ModoFoto.dpc), 28776480);
    });

    test('no alcanza si pasa del 95 % de lo libre', () {
      final e = estimarTimelapse(
        modo: ModoFoto.blanco,
        intervaloS: 60,
        duracionS: 3600,
        camaras: 1,
        libreBytes: 60 * bytesPorFoto,
      );
      expect(e.alcanza, isFalse);
    });
  });
}
