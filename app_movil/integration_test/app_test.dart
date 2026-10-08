// Prueba de punta a punta: la app de verdad, en el emulador de Android,
// contra dev_server/servidor_falso.py. Recorre todas las pantallas y guarda
// una captura de cada una en docs/capturas/.
//
//     # en una terminal
//     cd dev_server && .venv/bin/python servidor_falso.py
//     # en otra, con el emulador encendido
//     flutter drive --driver=test_driver/integration_test.dart \
//         --target=integration_test/app_test.dart
//
// NUNCA correr esto contra el microscopio real: mueve el motor, toma
// fotos e inicia y detiene un timelapse.

import 'package:dio/dio.dart';
import 'package:flutter/cupertino.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:microscopeos/main.dart' as app;
import 'package:microscopeos/textos.dart';

const servidor = String.fromEnvironment('SERVIDOR', defaultValue: '10.0.2.2:8000');

void main() {
  final binding = IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  final dev = Dio(BaseOptions(baseUrl: 'http://$servidor'));

  testWidgets('recorrido completo contra el servidor falso', (tester) async {
    final semantica = tester.ensureSemantics();
    // Por si acaso: esto solo corre contra el servidor falso, que tiene
    // rutas /dev/ que la Raspberry no tiene.
    final r = await dev.get<dynamic>('/dev/motores');
    expect(r.statusCode, 200, reason: 'Esto tiene que correr contra servidor_falso.py, no contra la Pi');

    Future<void> esperarReal(int ms) async {
      await Future<void>.delayed(Duration(milliseconds: ms));
      await tester.pump();
    }

    Future<void> esperarA(Finder f, {int segundos = 20}) async {
      for (var i = 0; i < segundos * 10; i++) {
        await esperarReal(100);
        if (f.evaluate().isNotEmpty) return;
      }
      await binding.takeScreenshot('error_no_aparecio');
      fail('No apareció: $f');
    }

    var convertido = false;
    Future<void> captura(String nombre) async {
      if (!convertido) {
        await binding.convertFlutterSurfaceToImage();
        convertido = true;
      }
      await esperarReal(400);
      await binding.takeScreenshot(nombre);
    }

    Finder pestana(String t) => find.descendant(of: find.byType(CupertinoTabBar), matching: find.text(t));

    Future<void> tocar(Finder f) async {
      await tester.ensureVisible(f);
      await esperarReal(300);
      await tester.tap(f);
      await esperarReal(500);
    }

    app.main();
    await esperarA(find.text(Textos.conexionTitulo));

    // ------------------------------------------------------------ Conexión
    await tester.enterText(find.byType(CupertinoTextField).at(0), 'Microscopio de prueba');
    await tester.enterText(find.byType(CupertinoTextField).at(1), servidor);
    await tocar(find.text(Textos.conexionProbar));
    await esperarA(find.textContaining('Hay un MicroscopeOS'));
    FocusManager.instance.primaryFocus?.unfocus();
    await captura('01_conexion');
    await tocar(find.text(Textos.conexionConectar));

    // ------------------------------------------------------------ Inicio
    await esperarA(find.text('°C'));
    await esperarA(find.text(Textos.timelapseNuevo));
    await captura('02_inicio');

    // ------------------------------------------------------------ Vivo: foco
    await tocar(pestana(Textos.tabVivo));
    await esperarA(find.text(Textos.vivoEnVivo));
    // Prender la luz (campo claro) para que se vea la muestra.
    await tocar(find.text(Textos.panelLuz));
    await tocar(find.text(Textos.luzCampoClaro));
    await tocar(find.text(Textos.panelFoco));
    await esperarReal(1500);
    await captura('03_vivo_foco');

    // Mantener «Bajar» 1.2 s: tiene que moverse y PARAR al soltar.
    final antes = (await dev.get<Map<String, dynamic>>('/dev/motores')).data!['0']['posicion_um'] as num;
    final g = await tester.startGesture(tester.getCenter(find.byKey(const ValueKey('foco_1'))));
    await esperarReal(1200);
    final durante = (await dev.get<Map<String, dynamic>>('/dev/motores')).data!['0'];
    expect(durante['jog_activo'], isTrue);
    await g.up();
    // La app manda el stop al soltar (lo verifican test/widgets/vivo_test.dart
    // y el log del servidor). Cuánto tarda el MOTOR en parar depende del
    // backend: stop_jog() de core/motor_focus.py espera el mismo candado que
    // el hilo del jog y a veces tarda hasta el watchdog (1.5 s). Ver
    // «Pendientes del backend» en el README. Aquí se mide y se exige < 2 s.
    final soltado = DateTime.now();
    late Map<String, dynamic> despues;
    do {
      await esperarReal(50);
      despues = (await dev.get<Map<String, dynamic>>('/dev/motores')).data!['0'];
    } while (despues['jog_activo'] == true && DateTime.now().difference(soltado).inMilliseconds < 2500);
    final ms = DateTime.now().difference(soltado).inMilliseconds;
    debugPrint('El motor paró $ms ms después de soltar');
    expect(despues['jog_activo'], isFalse, reason: 'el motor tiene que haber parado');
    expect(ms, lessThan(2000));
    expect(despues['posicion_um'] as num, greaterThan(antes), reason: 'bajó');

    // Autofoco: tarda (en el falso, 8 s) y deja la imagen nítida.
    await tocar(find.text(Textos.focoAutofoco));
    await esperarA(find.text(Textos.focoEnfocando));
    await captura('04_vivo_autofoco');
    await esperarA(find.textContaining('Enfocado'), segundos: 40);
    await esperarReal(1200);
    await captura('05_vivo_enfocado');

    // ------------------------------------------------------------ Vivo: luz
    await tocar(find.text(Textos.panelLuz));
    await esperarA(find.text(Textos.luzBrillo));
    await tocar(find.text(Textos.luzRelieveIzq));
    // Color del relieve: verde (el recomendado).
    await tocar(find.bySemanticsLabel(Textos.colorVerde).first);
    await esperarReal(1500);
    await captura('06_vivo_luz');
    // Rheinberg: centro y anillo.
    await tocar(find.text(Textos.luzColores));
    await tocar(find.bySemanticsLabel(Textos.colorVerde).last);
    await esperarReal(1500);
    await tester.scrollUntilVisible(find.text(Textos.luzColorAnillo), 200);
    await captura('06b_vivo_luz_rheinberg');
    await tocar(find.text(Textos.luzCampoClaro));

    // ------------------------------------------------------------ Vivo: foto
    await tocar(find.text(Textos.panelFoto));
    await tester.enterText(find.byType(CupertinoTextField).first, 'Prueba desde la app');
    FocusManager.instance.primaryFocus?.unfocus();
    await esperarReal(1200);
    await tocar(find.byKey(const ValueKey('disparador')));
    await esperarA(find.textContaining('guardada en «Prueba desde la app»'));
    await captura('07_vivo_foto');
    await esperarA(find.text(Textos.vivoEnVivo), segundos: 10);

    // ------------------------------------------------------------ control
    await dev.post<dynamic>('/dev/otra_persona');
    await esperarA(find.text(Textos.controlDeOtro('ana')), segundos: 10);
    await captura('08_control_otra_persona');
    // Intentar cambiar la luz: 423 -> «¿Tomar el control?»
    await tocar(find.text(Textos.panelLuz));
    await tocar(find.text(Textos.luzFondoNegro));
    await esperarA(find.text(Textos.controlTomarDetalle));
    await captura('09_tomar_el_control');
    await tocar(find.descendant(of: find.byType(CupertinoAlertDialog), matching: find.text(Textos.controlTomar)));
    await esperarA(find.text(Textos.controlTuyo), segundos: 10);
    await tocar(find.text(Textos.luzCampoClaro));

    // ------------------------------------------------------------ nuevo timelapse
    await tocar(pestana(Textos.tabInicio));
    await tocar(find.text(Textos.timelapseNuevo));
    await esperarA(find.text(Textos.nuevoTitulo));
    await tester.enterText(find.byType(CupertinoTextField).first, 'Timelapse desde la app');
    FocusManager.instance.primaryFocus?.unfocus();
    // 5 min -> 2 min -> 1 min -> 30 s
    // Opciones del relieve: aparecen al elegir relieve DPC.
    await esperarReal(1200); // que termine de cerrarse el teclado
    await tocar(find.text(Textos.nuevoTipoRelieve));
    await esperarA(find.text(Textos.relieveProcesar));
    await tester.scrollUntilVisible(find.text(Textos.relieveJpg), 200);
    await captura('10b_nuevo_timelapse_relieve');
    await tester.scrollUntilVisible(find.text(Textos.nuevoTipoNormal), -200);
    await tocar(find.text(Textos.nuevoTipoNormal));
    for (var i = 0; i < 3; i++) {
      await tocar(find.byIcon(CupertinoIcons.minus_circle_fill).first);
    }
    await captura('10_nuevo_timelapse');
    await tester.scrollUntilVisible(find.text(Textos.nuevoIniciar), 300);
    await tocar(find.text(Textos.nuevoIniciar));
    await esperarA(find.text(Textos.nuevoConfirmar));
    await tocar(find.descendant(of: find.byType(CupertinoAlertDialog), matching: find.text(Textos.nuevoIniciar)));
    await esperarA(find.text('EN CURSO'), segundos: 15);
    // La primera foto llega después del autofoco de cada cámara.
    await esperarA(find.textContaining('${Textos.camaraN(1)} · ${Textos.timelapseFoto(1)}'), segundos: 60);
    await esperarReal(2500);
    await tester.scrollUntilVisible(find.text(Textos.timelapsePausar), 300);
    await captura('11_inicio_timelapse');

    // Pausa y nota
    await tester.scrollUntilVisible(find.text(Textos.timelapsePausar), 300);
    await tocar(find.text(Textos.timelapsePausar));
    await esperarA(find.text(Textos.timelapseSeguir), segundos: 10);
    await tocar(find.text(Textos.timelapseAnotar));
    await tester.enterText(find.byType(CupertinoTextField).last, 'Agregué el medio nuevo');
    await tocar(find.text(Textos.guardar));
    await esperarA(find.textContaining('Nota guardada'));
    await captura('12_timelapse_en_pausa');
    await tocar(find.text(Textos.timelapseSeguir));
    await esperarA(find.text(Textos.timelapsePausar), segundos: 10);

    // Vivo con el timelapse tomando fotos: la última foto del ciclo
    await tocar(pestana(Textos.tabVivo));
    await esperarA(find.text(Textos.vivoUltimoCiclo), segundos: 10);
    await tester.fling(find.byType(CustomScrollView).hitTestable().first, const Offset(0, 1500), 3000);
    await esperarReal(2000);
    await captura('13_vivo_con_timelapse');

    // ------------------------------------------------------------ experimentos
    await tocar(pestana(Textos.tabExperimentos));
    await esperarA(find.text('Células HeLa día 1'));
    await esperarReal(2000);
    await captura('14_experimentos');
    await tocar(find.text('Células HeLa día 1'));
    await esperarA(find.text(Textos.expNotas));
    await esperarReal(2500);
    await captura('15_experimento_detalle');
    await tocar(find.bySemanticsLabel(RegExp(r'^0012_')).first);
    await esperarReal(2500);
    await captura('16_visor_foto');
    await tocar(find.text(Textos.listo));
    await tester.pageBack();
    await esperarReal(600);

    // ------------------------------------------------------------ ajustes
    await tocar(pestana(Textos.tabAjustes));
    await esperarA(find.text(Textos.ajustesApariencia));
    await esperarReal(1000);
    await captura('17_ajustes');
    await tester.scrollUntilVisible(find.text(Textos.ajustesVersionMicroscopio), 300);
    // La versión del microscopio: un commit (7 caracteres) y su fecha.
    await esperarA(find.textContaining(RegExp(r'^[0-9a-f]{7} · ')).hitTestable(), segundos: 10);
    await captura('18_ajustes_version');

    // ------------------------------------------------------------ detener
    await tocar(pestana(Textos.tabInicio));
    await tester.scrollUntilVisible(find.text(Textos.timelapseDetener), 300);
    await tocar(find.text(Textos.timelapseDetener));
    await esperarA(find.text(Textos.timelapseDetenerPregunta));
    await captura('19_detener_confirmacion');
    await tocar(find.descendant(of: find.byType(CupertinoAlertDialog), matching: find.text(Textos.timelapseDetener)));
    await esperarA(find.text(Textos.timelapseDetenido), segundos: 90);
    await esperarA(find.text(Textos.timelapseNinguno), segundos: 10);

    // Nada quedó moviéndose.
    final fin = (await dev.get<Map<String, dynamic>>('/dev/motores')).data!;
    expect(fin['0']['jog_activo'], isFalse);
    expect(fin['1']['jog_activo'], isFalse);
    semantica.dispose();
  });
}
