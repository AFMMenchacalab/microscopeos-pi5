import 'dart:math' as math;

import '../../api/modelos.dart';

/// TIFF de 16 bits sin comprimir del sensor IMX219
/// (BYTES_POR_FOTO en core/experimentos.py).
const int bytesPorFoto = 3280 * 2464 * 2;

/// Lo que queda en disco por cámara y por ciclo, igual que
/// `_bytes_por_ciclo` en server/api.py y `tlBytesPorCiclo` en la web,
/// con las opciones de relieve que la app deja por defecto (calcular el
/// relieve, borrar las 4 crudas, guardar la suma chica y la vista JPG).
int bytesPorCiclo(ModoFoto modo) {
  if (modo != ModoFoto.dpc) return modo.fotosPorToma * bytesPorFoto;
  const comprimido = 0.75; // dpcLR/dpcTB/suma en TIFF comprimido
  const sumaLado = 1640 / 3280; // la suma se guarda a la mitad de ancho
  return (2 * bytesPorFoto * comprimido + bytesPorFoto * sumaLado * sumaLado * comprimido + 1.5e6).toInt();
}

class Estimacion {
  const Estimacion({required this.fotosPorCamara, required this.bytes, required this.libreBytes});

  final int fotosPorCamara;
  final int bytes;
  final int? libreBytes;

  /// El servidor rechaza el timelapse si ocupa más del 95 % de lo libre.
  bool get alcanza => libreBytes == null || bytes <= libreBytes! * 0.95;
}

Estimacion estimarTimelapse({
  required ModoFoto modo,
  required int intervaloS,
  required int duracionS,
  required int camaras,
  int? libreBytes,
}) {
  final ciclos = math.max(1, duracionS ~/ math.max(1, intervaloS));
  return Estimacion(fotosPorCamara: ciclos, bytes: ciclos * camaras * bytesPorCiclo(modo), libreBytes: libreBytes);
}
