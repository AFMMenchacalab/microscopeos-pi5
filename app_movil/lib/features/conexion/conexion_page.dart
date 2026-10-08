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

enum _Prueba { nada, probando, ok, error }

class _ConexionPageState extends ConsumerState<ConexionPage> {
  final _nombre = TextEditingController();
  final _direccion = TextEditingController();
  _Prueba _prueba = _Prueba.nada;
  String? _resultado;

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
    final c = ClienteMicroscopio(uri, reintentosLectura: 0);
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

  Future<void> _conectar() async {
    if (_prueba != _Prueba.ok && !await _probar()) return;
    final uri = normalizarDireccion(_direccion.text)!;
    final nombre = _nombre.text.trim().isEmpty ? uri.host : _nombre.text.trim();
    await ref
        .read(microscopiosProvider.notifier)
        .guardarYUsar(MicroscopioGuardado(nombre: nombre, url: uri.toString()));
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
                  onChanged: (_) => setState(() => _prueba = _Prueba.nada),
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
                      icono: _prueba == _Prueba.ok
                          ? CupertinoIcons.checkmark_circle_fill
                          : CupertinoIcons.exclamationmark_triangle_fill,
                      color: _prueba == _Prueba.ok ? Colores.bien : Colores.mal,
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
              children: const [
                CupertinoListTile(
                  padding: EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                  leading: Icon(CupertinoIcons.globe, color: Colores.textoSecundario),
                  title: Text(Textos.conexionRemotaTexto, maxLines: 4),
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
