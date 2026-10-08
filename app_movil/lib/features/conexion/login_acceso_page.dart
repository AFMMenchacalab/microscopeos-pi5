import 'dart:async';

import 'package:flutter/cupertino.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:webview_flutter/webview_flutter.dart';

import '../../api/cliente_api.dart';
import '../../api/errores.dart';
import '../../estado/datos.dart';
import '../../estado/preferencias.dart';
import '../../textos.dart';
import '../../ui/tema.dart';

/// Entrar al microscopio desde internet (Cloudflare Access).
///
/// Abre la misma página de Cloudflare que ve el navegador: la persona
/// escribe su correo del laboratorio y el código que le llega. Al
/// terminar, Cloudflare deja la cookie de sesión `CF_Authorization` en el
/// dominio del microscopio; la app la lee (es HttpOnly: la página no
/// puede, el navegador integrado sí) y la manda en cada pedido.
///
/// Devuelve el token, o null si la persona canceló.
Future<String?> entrarConCorreo(BuildContext context, Uri base) => Navigator.of(
  context,
  rootNavigator: true,
).push(CupertinoPageRoute<String>(fullscreenDialog: true, builder: (_) => LoginAccesoPage(base: base)));

/// La sesión venció (o nunca hubo): entra de nuevo y la guarda para el
/// microscopio actual. Devuelve true si quedó adentro.
Future<bool> volverAEntrar(BuildContext context, WidgetRef ref) async {
  final actual = ref.read(microscopiosProvider).actual;
  if (actual == null) return false;
  final token = await entrarConCorreo(context, actual.uri);
  if (token == null) return false;
  await ref.read(microscopiosProvider.notifier).guardarSesion(actual.url, token);
  ref
    ..invalidate(estadoProvider)
    ..invalidate(controlProvider)
    ..invalidate(incubadoraProvider);
  return true;
}

/// Olvida la sesión de internet: la de la app y la del navegador
/// integrado (la próxima vez pide el código del correo otra vez).
Future<void> cerrarSesionRemota(WidgetRef ref) async {
  final actual = ref.read(microscopiosProvider).actual;
  if (actual == null) return;
  await WebViewCookieManager().clearCookies().catchError((_) => false);
  await ref.read(microscopiosProvider.notifier).guardarSesion(actual.url, null);
}

/// Dónde empieza el login: una ruta del microscopio que solo LEE. Cloudflare
/// devuelve a la persona ahí después de entrar. Si fuera la raíz, se
/// cargaría la interfaz web, que al abrirse manda órdenes al microscopio
/// (p. ej. POST /api/focus/config, que además toma el control).
Uri paginaInicial(Uri base) => base.replace(path: '/api/version');

/// Qué puede abrir el navegador del login: las páginas de Cloudflare y,
/// del microscopio, solo /cdn-cgi/ (donde Access deja la cookie) y la
/// página inicial. Nunca la interfaz web.
bool permitida(Uri? url, Uri base) {
  if (url == null) return false;
  final mismoSitio = url.host == base.host && (url.hasPort ? url.port : null) == (base.hasPort ? base.port : null);
  if (!mismoSitio) return true; // la página de Cloudflare (otro dominio)
  return url.path.startsWith('/cdn-cgi/') || url.path == paginaInicial(base).path;
}

class LoginAccesoPage extends StatefulWidget {
  const LoginAccesoPage({super.key, required this.base});

  final Uri base;

  /// Para la prueba de punta a punta: deja tocar la página desde la prueba.
  @visibleForTesting
  static final controladorParaPruebas = ValueNotifier<WebViewController?>(null);

  @override
  State<LoginAccesoPage> createState() => _LoginAccesoPageState();
}

class _LoginAccesoPageState extends State<LoginAccesoPage> {
  late final WebViewController _web;
  final _cookies = WebViewCookieManager();
  bool _cargando = true;
  bool _listo = false;
  bool _revisando = false;
  String? _error;

  /// Tokens que ya se probaron y no sirven (una cookie vieja).
  final _descartados = <String>{};

  @override
  void initState() {
    super.initState();
    _web = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..setNavigationDelegate(
        NavigationDelegate(
          onNavigationRequest: (pedido) {
            if (permitida(Uri.tryParse(pedido.url), widget.base)) return NavigationDecision.navigate;
            // Una página del microscopio (la interfaz web): no se abre.
            _revisar();
            return NavigationDecision.prevent;
          },
          onPageStarted: (_) {
            if (mounted) setState(() => _cargando = true);
          },
          onPageFinished: (_) {
            if (mounted) setState(() => _cargando = false);
            _revisar();
          },
          onUrlChange: (_) => _revisar(),
          onWebResourceError: (e) {
            if (e.isForMainFrame != true || !mounted) return;
            setState(() {
              _cargando = false;
              _error = Textos.loginSinRed;
            });
          },
        ),
      )
      ..loadRequest(paginaInicial(widget.base));
    LoginAccesoPage.controladorParaPruebas.value = _web;
  }

  @override
  void dispose() {
    LoginAccesoPage.controladorParaPruebas.value = null;
    super.dispose();
  }

  /// ¿Ya hay una cookie de sesión que el microscopio acepte?
  Future<void> _revisar() async {
    if (_listo || _revisando) return;
    _revisando = true;
    try {
      final cookies = await _cookies.getCookies(domain: widget.base);
      final token = cookies
          .where((c) => c.name == ClienteMicroscopio.cookieAcceso)
          .map((c) => c.value)
          .where((v) => v.isNotEmpty && !_descartados.contains(v))
          .firstOrNull;
      if (token == null) return;
      // Una cookie vieja (vencida) también estaría aquí: se prueba antes
      // de darla por buena.
      final c = ClienteMicroscopio(widget.base, sesionAcceso: token, reintentosLectura: 0);
      try {
        await c.version(probar: true);
      } on ErrorNecesitaLogin {
        _descartados.add(token);
        return;
      } on ErrorApi {
        // Sin red u otra cosa: igual sirve como sesión; lo dirá la prueba
        // de conexión.
      } finally {
        c.cerrar();
      }
      if (!mounted || _listo) return;
      _listo = true;
      Navigator.of(context).pop(token);
    } finally {
      _revisando = false;
    }
  }

  @override
  Widget build(BuildContext context) {
    return CupertinoPageScaffold(
      navigationBar: CupertinoNavigationBar(
        middle: const Text(Textos.loginTitulo),
        leading: CupertinoButton(
          padding: EdgeInsets.zero,
          onPressed: () => Navigator.of(context).pop(),
          child: const Text(Textos.cancelar),
        ),
        trailing: _cargando ? const CupertinoActivityIndicator() : null,
      ),
      child: SafeArea(
        child: Column(
          children: [
            Container(
              width: double.infinity,
              padding: const EdgeInsets.fromLTRB(16, 10, 16, 10),
              color: CupertinoDynamicColor.resolve(Colores.tarjeta, context),
              child: Text(
                _error ?? Textos.loginAyuda(widget.base.host),
                style: Estilos.nota(context)
                    .copyWith(color: _error == null ? null : CupertinoDynamicColor.resolve(Colores.mal, context)),
              ),
            ),
            Expanded(child: WebViewWidget(controller: _web)),
          ],
        ),
      ),
    );
  }
}
