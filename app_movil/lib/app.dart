import 'package:flutter/cupertino.dart';
import 'package:flutter/services.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'estado/avisos.dart';
import 'estado/conexion.dart';
import 'estado/datos.dart';
import 'estado/preferencias.dart';
import 'features/ajustes/ajustes_page.dart';
import 'features/conexion/conexion_page.dart';
import 'features/experimentos/experimentos_page.dart';
import 'features/inicio/inicio_page.dart';
import 'features/vivo/vivo_page.dart';
import 'textos.dart';
import 'ui/capsula_aviso.dart';
import 'ui/tema.dart';

class MicroscopeOSApp extends ConsumerStatefulWidget {
  const MicroscopeOSApp({super.key});

  @override
  ConsumerState<MicroscopeOSApp> createState() => _MicroscopeOSAppState();
}

class _MicroscopeOSAppState extends ConsumerState<MicroscopeOSApp> {
  late final AppLifecycleListener _ciclo;

  @override
  void initState() {
    super.initState();
    // Solo se consulta al microscopio con la app en primer plano.
    _ciclo = AppLifecycleListener(
      onStateChange: (s) => ref.read(primerPlanoProvider.notifier).fijar(s == AppLifecycleState.resumed),
    );
  }

  @override
  void dispose() {
    _ciclo.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final tema = ref.watch(temaProvider);
    final actual = ref.watch(microscopiosProvider.select((e) => e.actual));
    return CupertinoApp(
      title: Textos.app,
      debugShowCheckedModeBanner: false,
      theme: temaApp(switch (tema) {
        ModoTema.oscuro => Brightness.dark,
        ModoTema.claro => Brightness.light,
        ModoTema.sistema => null,
      }),
      locale: const Locale('es', 'MX'),
      supportedLocales: const [Locale('es', 'MX'), Locale('es')],
      localizationsDelegates: GlobalMaterialLocalizations.delegates,
      builder: (context, hijo) => Stack(children: [hijo ?? const SizedBox(), const CapsulaAviso()]),
      home: actual == null ? const ConexionPage() : Principal(key: ValueKey(actual.url)),
    );
  }
}

/// Las cuatro pestañas: Inicio, Vivo, Experimentos y Ajustes.
class Principal extends ConsumerStatefulWidget {
  const Principal({super.key});

  @override
  ConsumerState<Principal> createState() => _PrincipalState();
}

class _PrincipalState extends ConsumerState<Principal> {
  final _pestanas = CupertinoTabController();

  @override
  void dispose() {
    _pestanas.dispose();
    super.dispose();
  }

  void irA(int i) => _pestanas.index = i;

  @override
  Widget build(BuildContext context) {
    // Si alguien me quitó el control, se avisa una vez (como la web).
    ref.listen(controlProvider, (_, siguiente) {
      final quien = siguiente.value?.meLoQuito;
      if (quien == null) return;
      HapticFeedback.heavyImpact();
      ref.read(avisosProvider.notifier).mostrar(Textos.controlTeLoQuitaron(quien.split('@').first));
      ref.read(clienteProvider)?.avisoVisto().ignore();
    });

    return CupertinoTabScaffold(
      controller: _pestanas,
      tabBar: CupertinoTabBar(
        height: 58,
        iconSize: 28,
        onTap: (_) => HapticFeedback.selectionClick(),
        items: const [
          BottomNavigationBarItem(icon: Icon(CupertinoIcons.house_fill), label: Textos.tabInicio),
          BottomNavigationBarItem(icon: Icon(CupertinoIcons.videocam_fill), label: Textos.tabVivo),
          BottomNavigationBarItem(icon: Icon(CupertinoIcons.photo_on_rectangle), label: Textos.tabExperimentos),
          BottomNavigationBarItem(icon: Icon(CupertinoIcons.gear_alt_fill), label: Textos.tabAjustes),
        ],
      ),
      tabBuilder: (context, i) => CupertinoTabView(
        builder: (context) => switch (i) {
          0 => InicioPage(irAVivo: () => irA(1), irAExperimentos: () => irA(2)),
          1 => const VivoPage(),
          2 => const ExperimentosPage(),
          _ => const AjustesPage(),
        },
      ),
    );
  }
}
