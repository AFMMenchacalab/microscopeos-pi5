import 'package:flutter/cupertino.dart';

import '../textos.dart';

/// Una imagen a pantalla completa, sobre negro, con zoom de dos dedos
/// (como Fotos). Se cierra con «Listo».
class VisorImagen extends StatelessWidget {
  const VisorImagen({super.key, required this.imagen, required this.titulo, this.subtitulo});

  final ImageProvider imagen;
  final String titulo;
  final String? subtitulo;

  static Future<void> abrir(BuildContext context, ImageProvider imagen, String titulo, {String? subtitulo}) =>
      Navigator.of(context, rootNavigator: true).push(
        CupertinoPageRoute(
          fullscreenDialog: true,
          builder: (_) => VisorImagen(imagen: imagen, titulo: titulo, subtitulo: subtitulo),
        ),
      );

  @override
  Widget build(BuildContext context) {
    return CupertinoTheme(
      data: const CupertinoThemeData(brightness: Brightness.dark),
      child: CupertinoPageScaffold(
        backgroundColor: CupertinoColors.black,
        navigationBar: CupertinoNavigationBar(
          backgroundColor: const Color(0xCC000000),
          middle: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(titulo, style: const TextStyle(color: CupertinoColors.white)),
              if (subtitulo != null)
                Text(subtitulo!, style: const TextStyle(fontSize: 12, color: CupertinoColors.systemGrey)),
            ],
          ),
          trailing: CupertinoButton(
            padding: EdgeInsets.zero,
            onPressed: () => Navigator.of(context).pop(),
            child: const Text(Textos.listo, style: TextStyle(fontWeight: FontWeight.w600)),
          ),
        ),
        child: InteractiveViewer(
          minScale: 1,
          maxScale: 8,
          child: Center(
            child: Image(
              image: imagen,
              fit: BoxFit.contain,
              gaplessPlayback: true,
              frameBuilder: (c, hijo, cuadro, _) =>
                  cuadro == null ? const CupertinoActivityIndicator(radius: 14) : hijo,
              errorBuilder: (c, e, s) =>
                  const Icon(CupertinoIcons.exclamationmark_triangle, color: CupertinoColors.systemGrey, size: 40),
            ),
          ),
        ),
      ),
    );
  }
}
