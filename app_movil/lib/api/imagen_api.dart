import 'dart:ui' as ui;

import 'package:flutter/foundation.dart';
import 'package:flutter/painting.dart';

import 'cliente_api.dart';

/// Una imagen del microscopio (miniatura, vista del timelapse) como
/// ImageProvider de Flutter, pero bajada por [ClienteMicroscopio]: así
/// las pantallas no hacen HTTP por su cuenta, y los errores y el acceso
/// remoto futuro (cookie o tokens de Cloudflare) quedan en un solo lugar.
///
/// Flutter la guarda en su caché de imágenes con la dirección completa
/// como clave.
@immutable
class ImagenMicroscopio extends ImageProvider<ImagenMicroscopio> {
  const ImagenMicroscopio(this.cliente, this.ruta);

  final ClienteMicroscopio cliente;

  /// Ruta con su query, relativa a la base del microscopio
  /// (ver [ClienteMicroscopio.rutaMiniatura]).
  final String ruta;

  String get clave => '${cliente.base}$ruta';

  @override
  Future<ImagenMicroscopio> obtainKey(ImageConfiguration configuration) => SynchronousFuture<ImagenMicroscopio>(this);

  @override
  ImageStreamCompleter loadImage(ImagenMicroscopio key, ImageDecoderCallback decode) =>
      MultiFrameImageStreamCompleter(codec: _cargar(decode), scale: 1.0, debugLabel: clave);

  Future<ui.Codec> _cargar(ImageDecoderCallback decode) async {
    final datos = await cliente.bytes(ruta);
    return decode(await ui.ImmutableBuffer.fromUint8List(datos));
  }

  @override
  bool operator ==(Object other) => other is ImagenMicroscopio && other.clave == clave;

  @override
  int get hashCode => clave.hashCode;
}
