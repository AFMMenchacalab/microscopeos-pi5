// Acceso desde internet, de punta a punta: la app en el emulador contra
// dev_server/servidor_falso.py --cloudflare, que se porta como Cloudflare
// Access (login, cookie CF_Authorization HttpOnly, sesión que vence).
//
//     .venv/bin/python servidor_falso.py --puerto 8001 --cloudflare
//     flutter drive -d emulator-5554 --driver=test_driver/integration_test.dart \
//         --target=integration_test/acceso_remoto_test.dart
//
// NUNCA contra microscopio.lmimenchacalab.com: ahí el login pide el código
// del correo de una persona.

import 'package:dio/dio.dart';
import 'package:flutter/cupertino.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:microscopeos/features/conexion/login_acceso_page.dart';
import 'package:microscopeos/main.dart' as app;
import 'package:microscopeos/textos.dart';

const servidor = String.fromEnvironment('SERVIDOR_CF', defaultValue: '10.0.2.2:8001');

void main() {
  final binding = IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  final dev = Dio(BaseOptions(baseUrl: 'http://$servidor', followRedirects: false, validateStatus: (_) => true));

  testWidgets('entrar con el correo, usar la app, sesión vencida y volver a entrar', (tester) async {
    final r = await dev.get<dynamic>('/dev/motores');
    expect(r.statusCode, 200, reason: 'Esto corre contra servidor_falso.py --cloudflare');

    Future<void> esperarReal(int ms) async {
      await Future<void>.delayed(Duration(milliseconds: ms));
      await tester.pump();
    }

    var convertido = false;
    Future<void> esperarA(bool Function() cond, String que, {int segundos = 20}) async {
      for (var i = 0; i < segundos * 10; i++) {
        await esperarReal(100);
        if (cond()) return;
      }
      if (!convertido) {
        await binding.convertFlutterSurfaceToImage();
        convertido = true;
      }
      await binding.takeScreenshot('error_remoto');
      fail('No pasó: $que');
    }

    Future<void> captura(String nombre) async {
      if (!convertido) {
        await binding.convertFlutterSurfaceToImage();
        convertido = true;
      }
      await esperarReal(400);
      await binding.takeScreenshot(nombre);
    }

    Future<void> tocar(Finder f) async {
      await tester.ensureVisible(f);
      await esperarReal(300);
      await tester.tap(f);
      await esperarReal(500);
    }

    /// «Escribe» en la página de login falsa: la envía con el correo puesto.
    Future<void> entrarEnLaPagina() async {
      await esperarA(() => LoginAccesoPage.controladorParaPruebas.value != null, 'abre el login');
      final web = LoginAccesoPage.controladorParaPruebas.value!;
      await esperarA(() => find.byType(CupertinoActivityIndicator).evaluate().isEmpty, 'carga la página');
      await esperarReal(1500);
      await web.runJavaScript("document.querySelector('form').submit()");
    }

    app.main();
    await esperarA(() => find.text(Textos.conexionTitulo).evaluate().isNotEmpty, 'pantalla de conexión');

    // El microscopio pide entrar con el correo.
    await tester.enterText(find.byType(CupertinoTextField).at(0), 'Microscopio (internet, falso)');
    await tester.enterText(find.byType(CupertinoTextField).at(1), servidor);
    FocusManager.instance.primaryFocus?.unfocus();
    await tocar(find.text(Textos.conexionProbar));
    await esperarA(() => find.text(Textos.conexionEntrarCorreo).evaluate().isNotEmpty, 'ofrece entrar con el correo');
    await captura('21_conexion_pide_correo');

    // Login (la página de Cloudflare falsa) y de vuelta en Inicio.
    await tocar(find.text(Textos.conexionEntrarCorreo));
    await entrarEnLaPagina();
    await esperarA(() => find.text('°C').evaluate().isNotEmpty, 'entra a Inicio', segundos: 30);
    await esperarReal(2000);
    await captura('22_inicio_desde_internet');

    // El microscopio sabe quién es (por el correo, como en la web).
    await tocar(find.descendant(of: find.byType(CupertinoTabBar), matching: find.text(Textos.tabAjustes)));
    await esperarA(
      () => find.text('tu.correo@lmimenchacalab.com').evaluate().isNotEmpty,
      'el microscopio la reconoce por su correo',
      segundos: 15,
    );
    await captura('23_ajustes_sesion_remota');

    // El video también pasa con la sesión.
    await tocar(find.descendant(of: find.byType(CupertinoTabBar), matching: find.text(Textos.tabVivo)));
    await esperarA(() => find.text(Textos.vivoEnVivo).evaluate().isNotEmpty, 'video en vivo con la sesión');

    // La sesión vence: Inicio lo dice y deja volver a entrar.
    await dev.post<dynamic>('/dev/vencer_sesion');
    await tocar(find.descendant(of: find.byType(CupertinoTabBar), matching: find.text(Textos.tabInicio)));
    await esperarA(() => find.text(Textos.errorSesionVencida).evaluate().isNotEmpty, 'avisa que venció', segundos: 15);
    await captura('24_sesion_vencida');
    await tocar(find.text(Textos.loginEntrar));
    await entrarEnLaPagina();
    await esperarA(
      () => find.text(Textos.errorSesionVencida).evaluate().isEmpty && find.text('°C').evaluate().isNotEmpty,
      'vuelve a funcionar',
      segundos: 30,
    );
    await captura('25_de_nuevo_adentro');
  });
}
