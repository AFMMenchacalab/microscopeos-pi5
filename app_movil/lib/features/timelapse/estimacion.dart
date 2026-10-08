import 'dart:math' as math;

import '../../api/modelos.dart';

/// TIFF de 16 bits sin comprimir del sensor IMX219
/// (BYTES_POR_FOTO en core/experimentos.py).
const int bytesPorFoto = 3280 * 2464 * 2;

/// Lo que queda en disco por cámara y por ciclo, igual que
/// `_bytes_por_ciclo` en server/api.py (y `dpc.bytes_por_ciclo` en
/// core/dpc.py) y `tlBytesPorCiclo` en la web.
int bytesPorCiclo(ModoFoto modo, {OpcionesRelieve relieve = const OpcionesRelieve()}) {
  if (modo != ModoFoto.dpc) return modo.fotosPorToma * bytesPorFoto;
  // Sin calcular el relieve quedan las 4 crudas.
  if (!relieve.procesar) return 4 * bytesPorFoto;
  const comprimido = 0.75; // dpcLR/dpcTB/suma en TIFF comprimido
  const sumaLado = 1640 / 3280; // la suma se guarda a la mitad de ancho
  var total = 2 * bytesPorFoto * comprimido;
  if (relieve.suma) total += bytesPorFoto * sumaLado * sumaLado * comprimido;
  if (relieve.fase) total += bytesPorFoto;
  if (relieve.jpg) total += 1.5e6;
  if (!relieve.borrarCrudas) total += 4 * bytesPorFoto;
  return total.toInt();
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
  OpcionesRelieve relieve = const OpcionesRelieve(),
}) {
  final ciclos = math.max(1, duracionS ~/ math.max(1, intervaloS));
  return Estimacion(
    fotosPorCamara: ciclos,
    bytes: ciclos * camaras * bytesPorCiclo(modo, relieve: relieve),
    libreBytes: libreBytes,
  );
}
