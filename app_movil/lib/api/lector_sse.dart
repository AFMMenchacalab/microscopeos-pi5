/// Separa un flujo Server-Sent Events (text/event-stream) en eventos.
///
/// MicroscopeOS manda en /api/temperature/stream un evento por segundo:
///
///     data: {"temperature": 36.9, ...}
///     <línea vacía>
///
/// Se sigue el formato estándar: varias líneas `data:` de un mismo evento
/// se juntan con saltos de línea, las que empiezan con `:` son
/// comentarios, y `event:`/`id:`/`retry:` se ignoran (el servidor no los
/// usa). El texto puede llegar cortado en cualquier punto.
class SeparadorSse {
  final StringBuffer _resto = StringBuffer();
  final List<String> _datos = [];

  /// Agrega texto y devuelve los `data` de los eventos completos.
  List<String> agregar(String texto) {
    _resto.write(texto);
    final todo = _resto.toString();
    final lineas = todo.split('\n');
    _resto
      ..clear()
      ..write(lineas.removeLast()); // la última puede estar incompleta
    final eventos = <String>[];
    for (var linea in lineas) {
      if (linea.endsWith('\r')) linea = linea.substring(0, linea.length - 1);
      if (linea.isEmpty) {
        if (_datos.isNotEmpty) {
          eventos.add(_datos.join('\n'));
          _datos.clear();
        }
      } else if (linea.startsWith(':')) {
        continue;
      } else if (linea.startsWith('data:')) {
        var v = linea.substring(5);
        if (v.startsWith(' ')) v = v.substring(1);
        _datos.add(v);
      }
    }
    return eventos;
  }
}
