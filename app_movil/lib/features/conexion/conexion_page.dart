import 'package:flutter/cupertino.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../api/cliente_api.dart';
import '../../api/direccion.dart';
import '../../api/errores.dart';
import '../../estado/preferencias.dart';
import '../../textos.dart';
import '../../ui/acciones.dart';
import '../../ui/componentes.dart';
import '../../ui/formato.dart';
import '../../ui/tema.dart';
import 'login_acceso_page.dart';

/// Lista de microscopios guardados y alta de uno nuevo por IP.
///
/// No hay descubrimiento automático: se escribe la IP a mano y se prueba
/// con GET /api/version.
class ConexionPage extends ConsumerStatefulWidget {
  const ConexionPage({super.key, this.desdeAjustes = false});

  /// Abierta desde Ajustes (con botón para volver) o como primera pantalla.
  final bool desdeAjustes;

  @override
  ConsumerState<ConexionPage> createState() => _ConexionPageState();
}

enum _Prueba { nada, probando, ok, login, error }

class _ConexionPageState extends ConsumerState<ConexionPage> {
  final _nombre = TextEditingController();
  final _direccion = TextEditingController();
  _Prueba _prueba = _Prueba.nada;
  String? _resultado;

  /// Token de Cloudflare Access, si este microscopio pide entrar con el correo.
  String? _sesion;

  @override
  void dispose() {
    _nombre.dispose();
    _direccion.dispose();
    super.dispose();
  }

  Future<bool> _probar() async {
    final uri = normalizarDireccion(_direccion.text);
    if (uri == null) {
      setState(() {
        _prueba = _Prueba.error;
        _resultado = Textos.conexionDireccionInvalida;
      });
      return false;
    }
    setState(() {
      _prueba = _Prueba.probando;
      _resultado = null;
    });
    final c = ClienteMicroscopio(uri, sesionAcceso: _sesion, reintentosLectura: 0);
    try {
      final v = await c.version(probar: true);
      final texto = [
        if (v.commit != null) v.commit!,
        if (v.fecha != null) fechaAmigable(v.fecha!.toLocal()),
      ].join(' · ');
      if (!mounted) return false;
      HapticFeedback.mediumImpact();
      setState(() {
        _prueba = _Prueba.ok;
        _resultado = Textos.conexionOk(texto.isEmpty ? uri.toString() : texto);
      });
      return true;
    } on ErrorNecesitaLogin {
      // Detrás de Cloudflare Access: hay que entrar con el correo.
      if (!mounted) return false;
      setState(() {
        _prueba = _Prueba.login;
        _resultado = Textos.errorNecesitaLogin;
      });
      return false;
    } on ErrorApi catch (e) {
      if (!mounted) return false;
      setState(() {
        _prueba = _Prueba.error;
        _resultado = e.mensaje;
      });
      return false;
    } finally {
      c.cerrar();
    }
  }

  Future<void> _entrarConCorreo() async {
    final uri = normalizarDireccion(_direccion.text);
    if (uri == null) return;
    final token = await entrarConCorreo(context, uri);
    if (token == null || !mounted) return;
    _sesion = token;
    if (await _probar()) await _conectar();
  }

  void _usarInternet() {
    HapticFeedback.selectionClick();
    _direccion.text = Textos.conexionDominio;
    if (_nombre.text.trim().isEmpty) _nombre.text = Textos.conexionNombreInternet;
    _sesion = null;
    _probar();
  }

  Future<void> _conectar() async {
    if (_prueba != _Prueba.ok && !await _probar()) return;
    final uri = normalizarDireccion(_direccion.text)!;
    final nombre = _nombre.text.trim().isEmpty ? uri.host : _nombre.text.trim();
    await ref
        .read(microscopiosProvider.notifier)
        .guardarYUsar(MicroscopioGuardado(nombre: nombre, url: uri.toString(), sesion: _sesion));
    if (mounted && widget.desdeAjustes) Navigator.of(context).pop();
  }

  Future<void> _usar(MicroscopioGuardado m) async {
    HapticFeedback.selectionClick();
    await ref.read(microscopiosProvider.notifier).usar(m);
    if (mounted && widget.desdeAjustes) Navigator.of(context).pop();
  }

  Future<void> _borrar(MicroscopioGuardado m) async {
    final si = await confirmar(
      context,
      titulo: Textos.conexionBorrarPregunta,
      detalle: '${m.nombre}\n${m.url}',
      accion: Textos.borrar,
      destructiva: true,
    );
    if (si) await ref.read(microscopiosProvider.notifier).borrar(m);
  }

  @override
  Widget build(BuildContext context) {
    final estado = ref.watch(microscopiosProvider);
    return CupertinoPageScaffold(
      child: CustomScrollView(
        slivers: [
          CupertinoSliverNavigationBar(
            largeTitle: const Text(Textos.conexionTitulo),
            automaticallyImplyLeading: widget.desdeAjustes,
          ),
          if (estado.lista.isNotEmpty)
            SliverToBoxAdapter(
              child: CupertinoListSection.insetGrouped(
                header: const Text(Textos.conexionGuardados),
                children: [
                  for (final m in estado.lista)
                    CupertinoListTile.notched(
                      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                      leading: const Icon(CupertinoIcons.circle_grid_hex_fill, color: Colores.acento),
                      title: Text(m.nombre),
                      subtitle: Text(m.url),
                      trailing: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          if (estado.actual?.url == m.url)
                            const Icon(CupertinoIcons.checkmark_alt, color: Colores.acento),
                          CupertinoButton(
                            padding: EdgeInsets.zero,
                            minimumSize: const Size(44, 44),
                            onPressed: () => _borrar(m),
                            child: const Icon(CupertinoIcons.trash, color: Colores.textoSecundario, size: 22),
                          ),
                        ],
                      ),
                      onTap: () => _usar(m),
                    ),
                ],
              ),
            ),
          SliverToBoxAdapter(
            child: CupertinoListSection.insetGrouped(
              header: const Text(Textos.conexionAgregar),
              footer: Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text('${Textos.conexionAyuda}\n${Textos.conexionAyudaEmulador}'),
              ),
              children: [
                CupertinoTextFormFieldRow(
                  controller: _nombre,
                  prefix: const SizedBox(width: 110, child: Text(Textos.conexionNombre)),
                  placeholder: Textos.conexionNombreEjemplo,
                  textCapitalization: TextCapitalization.sentences,
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
                ),
                CupertinoTextFormFieldRow(
                  controller: _direccion,
                  prefix: const SizedBox(width: 110, child: Text(Textos.conexionDireccion)),
                  placeholder: Textos.conexionDireccionEjemplo,
                  keyboardType: TextInputType.url,
                  autocorrect: false,
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
                  onChanged: (_) => setState(() {
                    _prueba = _Prueba.nada;
                    _sesion = null;
                  }),
                  onFieldSubmitted: (_) => _probar(),
                ),
              ],
            ),
          ),
          SliverPadding(
            padding: const EdgeInsets.symmetric(horizontal: 20),
            sliver: SliverToBoxAdapter(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  if (_resultado != null) ...[
                    Franja(
                      texto: _resultado!,
                      icono: switch (_prueba) {
                        _Prueba.ok => CupertinoIcons.checkmark_circle_fill,
                        _Prueba.login => CupertinoIcons.lock_fill,
                        _ => CupertinoIcons.exclamationmark_triangle_fill,
                      },
                      color: switch (_prueba) {
                        _Prueba.ok => Colores.bien,
                        _Prueba.login => Colores.acento,
                        _ => Colores.mal,
                      },
                    ),
                    const SizedBox(height: Medidas.espacio),
                  ],
                  if (_prueba == _Prueba.login) ...[
                    BotonGrande(
                      texto: Textos.conexionEntrarCorreo,
                      icono: CupertinoIcons.envelope_fill,
                      alto: Medidas.toqueGrande,
                      alTocar: _entrarConCorreo,
                    ),
                    const SizedBox(height: Medidas.espacio),
                  ],
                  Row(
                    children: [
                      Expanded(
                        child: BotonGrande(
                          texto: _prueba == _Prueba.probando ? Textos.conexionProbando : Textos.conexionProbar,
                          estilo: EstiloBoton.secundario,
                          cargando: _prueba == _Prueba.probando,
                          alTocar: _probar,
                        ),
                      ),
                      const SizedBox(width: Medidas.espacio),
                      Expanded(
                        child: BotonGrande(
                          texto: Textos.conexionConectar,
                          icono: CupertinoIcons.link,
                          alTocar: _prueba == _Prueba.probando ? null : _conectar,
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
          SliverToBoxAdapter(
            child: CupertinoListSection.insetGrouped(
              header: const Text(Textos.conexionRemotaTitulo),
              footer: const Text(Textos.conexionRemotaTexto),
              children: [
                CupertinoListTile(
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                  leading: const Icon(CupertinoIcons.globe, color: Colores.acento),
                  title: const Text(Textos.conexionDominio),
                  subtitle: const Text(Textos.conexionRemotaSub),
                  trailing: const CupertinoListTileChevron(),
                  onTap: _usarInternet,
                ),
              ],
            ),
          ),
          const SliverToBoxAdapter(child: SizedBox(height: 40)),
        ],
      ),
    );
  }
}
