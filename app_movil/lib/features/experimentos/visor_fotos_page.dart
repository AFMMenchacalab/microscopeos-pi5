import 'dart:io';

import 'package:flutter/cupertino.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:path_provider/path_provider.dart';
import 'package:share_plus/share_plus.dart';

import '../../api/errores.dart';
import '../../api/imagen_api.dart';
import '../../api/modelos.dart';
import '../../estado/avisos.dart';
import '../../estado/conexion.dart';
import '../../textos.dart';
import '../../ui/formato.dart';

/// Las fotos de una cámara a pantalla completa: deslizar para pasar,
/// pellizcar para acercar, y «Compartir» la copia con marca de agua.
///
/// Se muestra la miniatura grande (1600 px, JPEG): los originales son
/// TIFF de 16 bits de decenas de MB y no se bajan al teléfono.
class VisorFotosPage extends ConsumerStatefulWidget {
  const VisorFotosPage({super.key, required this.experimento, required this.fotos, required this.inicial});

  final Experimento experimento;
  final List<String> fotos;
  final int inicial;

  static Future<void> abrir(BuildContext context, Experimento e, List<String> fotos, int i) =>
      Navigator.of(context, rootNavigator: true).push(
        CupertinoPageRoute<void>(
          fullscreenDialog: true,
          builder: (_) => VisorFotosPage(experimento: e, fotos: fotos, inicial: i),
        ),
      );

  @override
  ConsumerState<VisorFotosPage> createState() => _VisorFotosPageState();
}

class _VisorFotosPageState extends ConsumerState<VisorFotosPage> {
  late final PageController _paginas = PageController(initialPage: widget.inicial);
  late int _actual = widget.inicial;
  bool _compartiendo = false;
  final _botonCompartir = GlobalKey();

  @override
  void dispose() {
    _paginas.dispose();
    super.dispose();
  }

  /// Baja la copia JPEG con marca de agua, la guarda en la carpeta
  /// temporal y abre el menú de compartir del sistema.
  Future<void> _compartir() async {
    final c = ref.read(clienteProvider);
    if (c == null) return;
    setState(() => _compartiendo = true);
    try {
      final archivo = await c.paraCompartir(widget.experimento.id, widget.fotos[_actual]);
      final dir = await getTemporaryDirectory();
      final ruta = '${dir.path}/${archivo.nombre}';
      await File(ruta).writeAsBytes(archivo.bytes, flush: true);
      final caja = _botonCompartir.currentContext?.findRenderObject() as RenderBox?;
      await SharePlus.instance.share(
        ShareParams(
          files: [XFile(ruta, mimeType: archivo.tipo, name: archivo.nombre)],
          subject: widget.experimento.nombre,
          sharePositionOrigin: caja == null ? null : caja.localToGlobal(Offset.zero) & caja.size,
        ),
      );
    } on ErrorApi catch (e) {
      ref.read(avisosProvider.notifier).error(e.mensaje);
    } finally {
      if (mounted) setState(() => _compartiendo = false);
    }
  }

  String _pie(String rel) {
    final nombre = rel.split('/').last.replaceAll('.tif', '');
    final m = RegExp(r'(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})').firstMatch(nombre);
    if (m == null) return nombre;
    final g = [for (var i = 1; i <= 6; i++) int.parse(m.group(i)!)];
    final fecha = fechaAmigable(DateTime(g[0], g[1], g[2], g[3], g[4], g[5]));
    final ciclo = RegExp(r'^(\d{4})_').firstMatch(nombre)?.group(1);
    final canal = RegExp(r'_(L|R|T|B)$').firstMatch(nombre)?.group(1);
    return [if (ciclo != null) Textos.timelapseFoto(int.parse(ciclo)), fecha, ?canal].join(' · ');
  }

  @override
  Widget build(BuildContext context) {
    final c = ref.watch(clienteProvider);
    final rel = widget.fotos[_actual];
    return CupertinoTheme(
      data: const CupertinoThemeData(brightness: Brightness.dark, primaryColor: CupertinoColors.systemBlue),
      child: CupertinoPageScaffold(
        backgroundColor: CupertinoColors.black,
        navigationBar: CupertinoNavigationBar(
          backgroundColor: const Color(0xCC000000),
          leading: CupertinoButton(
            padding: EdgeInsets.zero,
            onPressed: () => Navigator.of(context).pop(),
            child: const Text(Textos.listo, style: TextStyle(fontWeight: FontWeight.w600)),
          ),
          middle: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(_pie(rel), style: const TextStyle(color: CupertinoColors.white, fontSize: 15)),
              Text(
                Textos.expDeN(_actual + 1, widget.fotos.length),
                style: const TextStyle(color: CupertinoColors.systemGrey, fontSize: 12),
              ),
            ],
          ),
          trailing: CupertinoButton(
            key: _botonCompartir,
            padding: EdgeInsets.zero,
            minimumSize: const Size(44, 44),
            onPressed: _compartiendo ? null : _compartir,
            child: _compartiendo
                ? const CupertinoActivityIndicator()
                : const Icon(CupertinoIcons.share, semanticLabel: Textos.expCompartir),
          ),
        ),
        child: c == null
            ? const SizedBox()
            : PageView.builder(
                controller: _paginas,
                itemCount: widget.fotos.length,
                onPageChanged: (i) => setState(() => _actual = i),
                itemBuilder: (context, i) => InteractiveViewer(
                  maxScale: 8,
                  child: Center(
                    child: Image(
                      image: ImagenMicroscopio(
                        c,
                        c.rutaMiniatura(widget.experimento.id, widget.fotos[i], tamano: 1600),
                      ),
                      fit: BoxFit.contain,
                      gaplessPlayback: true,
                      frameBuilder: (c, hijo, cuadro, _) =>
                          cuadro == null ? const CupertinoActivityIndicator(radius: 14) : hijo,
                      errorBuilder: (_, _, _) => const Icon(
                        CupertinoIcons.exclamationmark_triangle,
                        color: CupertinoColors.systemGrey,
                        size: 40,
                      ),
                    ),
                  ),
                ),
              ),
      ),
    );
  }
}
