import 'package:flutter/cupertino.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_riverpod/misc.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:microscopeos/api/modelos.dart';
import 'package:microscopeos/estado/preferencias.dart';
import 'package:microscopeos/ui/capsula_aviso.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Monta una pantalla con la app de verdad alrededor (tema, idioma,
/// avisos) y los datos del microscopio reemplazados por [overrides].
Future<void> montar(WidgetTester tester, Widget pantalla, {List<Override> overrides = const []}) async {
  SharedPreferences.setMockInitialValues({});
  final prefs = await SharedPreferences.getInstance();
  await tester.binding.setSurfaceSize(const Size(430, 1400));
  addTearDown(() => tester.binding.setSurfaceSize(null));
  await tester.pumpWidget(
    ProviderScope(
      overrides: [preferenciasProvider.overrideWithValue(prefs), ...overrides],
      retry: (_, _) => null,
      child: CupertinoApp(
        locale: const Locale('es', 'MX'),
        supportedLocales: const [Locale('es', 'MX')],
        localizationsDelegates: GlobalMaterialLocalizations.delegates,
        builder: (context, hijo) => Stack(children: [hijo!, const CapsulaAviso()]),
        home: pantalla,
      ),
    ),
  );
}

/// Quita la pantalla (dispose de todo) y deja correr los timers pendientes.
Future<void> desmontar(WidgetTester tester) async {
  await tester.pumpWidget(const SizedBox());
  await tester.pump(const Duration(seconds: 10));
}

const estadoLibre = EstadoGeneral();

EstadoGeneral estadoConTimelapse({bool pausado = false}) => EstadoGeneral.desdeJson({
  'running': true,
  'ciclo': 12,
  'timelapse': {
    'nombre': 'Células HeLa día 2',
    'modo': 'blanco',
    'intervalo_s': 300,
    'duracion_s': 86400,
    'camaras': [0, 1],
    'inicio': DateTime.now().subtract(const Duration(hours: 1)).toIso8601String(),
    'fin_previsto': DateTime.now().add(const Duration(hours: 23)).toIso8601String(),
    'proxima': DateTime.now().add(const Duration(minutes: 3)).toIso8601String(),
    'ciclo': 12,
    'pausado': pausado,
    'carpeta': '2026-10-07_1000_Celulas_HeLa_dia_2',
    'ultimas': {
      '0': {'ciclo': 12, 'hora': DateTime.now().toIso8601String(), 'dpc': false},
    },
  },
});

EstadoControl controlDe(String? quien, {bool yo = false}) => EstadoControl.desdeJson({
  'yo': {'id': 'local:10.0.2.16', 'nombre': 'Red local (10.0.2.16)', 'remoto': false},
  'control': yo
      ? {'id': 'local:10.0.2.16', 'nombre': 'Red local (10.0.2.16)'}
      : (quien == null ? null : {'id': quien, 'nombre': quien}),
  'tengo_control': yo,
  'conectados': [
    {'id': 'local:10.0.2.16', 'nombre': 'Red local (10.0.2.16)', 'remoto': false, 'hace_s': 0},
    if (quien != null) {'id': quien, 'nombre': quien, 'remoto': true, 'hace_s': 3},
  ],
});
