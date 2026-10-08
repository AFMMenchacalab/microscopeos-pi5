import 'dart:typed_data';

/// Separa un flujo MJPEG (`multipart/x-mixed-replace; boundary=frame`) en
/// cuadros JPEG sueltos.
///
/// No interpreta los encabezados de cada parte: busca los marcadores de
/// inicio (FF D8) y fin (FF D9) de cada JPEG. Dentro de los datos
/// comprimidos de un JPEG un FF siempre va seguido de 00 o de un marcador
/// de reinicio, así que FF D8 / FF D9 solo aparecen en los bordes. Así
/// funciona igual aunque cambie el boundary o falte Content-Length (el
/// servidor de MicroscopeOS no lo manda).
///
/// Los trozos que llegan de la red pueden cortar un cuadro (o un
/// marcador) por cualquier lado: lo que sobra se guarda para el próximo.
class SeparadorMjpeg {
  SeparadorMjpeg({this.maximoBytes = 8 * 1024 * 1024});

  /// Si se acumula más que esto sin encontrar el final del cuadro, el
  /// flujo está roto: se descarta y se espera el próximo inicio.
  final int maximoBytes;

  Uint8List _buf = Uint8List(256 * 1024);
  int _largo = 0;
  int _inicio = -1; // donde empieza el cuadro en curso (FF D8), o -1
  int _pos = 0; // hasta donde ya se miro

  /// Agrega un trozo y devuelve los cuadros completos que aparecieron.
  List<Uint8List> agregar(List<int> trozo) {
    _anexar(trozo);
    final cuadros = <Uint8List>[];
    var i = _pos;
    while (i + 1 < _largo) {
      if (_buf[i] == 0xFF) {
        final m = _buf[i + 1];
        if (m == 0xD8) {
          // Un inicio nuevo sin haber cerrado el anterior: el anterior
          // llegó cortado; se descarta.
          _inicio = i;
          i += 2;
          continue;
        }
        if (m == 0xD9 && _inicio >= 0) {
          cuadros.add(Uint8List.fromList(Uint8List.sublistView(_buf, _inicio, i + 2)));
          _inicio = -1;
          i += 2;
          continue;
        }
      }
      i++;
    }
    // Lo que ya no hace falta se tira: todo si no hay cuadro abierto
    // (salvo el ultimo byte, que puede ser la mitad de un marcador).
    final corte = _inicio >= 0 ? _inicio : i;
    _descartar(corte);
    if (_inicio >= 0) _inicio -= corte;
    _pos = i - corte;
    if (_largo > maximoBytes) reiniciar();
    return cuadros;
  }

  void reiniciar() {
    _largo = 0;
    _inicio = -1;
    _pos = 0;
  }

  /// Bytes guardados esperando el resto del cuadro (para las pruebas).
  int get pendientes => _largo;

  void _anexar(List<int> trozo) {
    final necesario = _largo + trozo.length;
    if (necesario > _buf.length) {
      var nuevo = _buf.length * 2;
      while (nuevo < necesario) {
        nuevo *= 2;
      }
      _buf = Uint8List(nuevo)..setRange(0, _largo, _buf);
    }
    _buf.setRange(_largo, necesario, trozo);
    _largo = necesario;
  }

  void _descartar(int n) {
    if (n <= 0) return;
    _buf.setRange(0, _largo - n, _buf, n);
    _largo -= n;
  }
}
