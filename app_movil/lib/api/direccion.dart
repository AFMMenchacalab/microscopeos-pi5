/// Dirección de un microscopio a partir de lo que escribe la persona.
///
/// «192.168.1.50» -> http://192.168.1.50:8000
/// «http://10.0.2.2:8000/» -> http://10.0.2.2:8000
/// «https://microscopio.lmimenchacalab.com» -> la misma (acceso remoto,
///   que en esta versión todavía no funciona: ver README).
///
/// «microscopio.lmimenchacalab.com» -> https://microscopio.lmimenchacalab.com
///
/// Devuelve null si no se entiende o si es HTTP sin cifrar fuera de la
/// red local.
Uri? normalizarDireccion(String entrada) {
  var t = entrada.trim();
  if (t.isEmpty) return null;
  if (!t.contains('://')) {
    // Sin esquema: HTTP en la red local, HTTPS para todo lo demás
    // (microscopio.lmimenchacalab.com).
    final host = Uri.tryParse('http://$t')?.host ?? '';
    t = '${esRedLocal(host) ? 'http' : 'https'}://$t';
  }
  final u = Uri.tryParse(t);
  if (u == null || u.host.isEmpty || !(u.scheme == 'http' || u.scheme == 'https')) return null;
  if (u.scheme == 'http' && !esRedLocal(u.host)) return null;
  final puerto = u.hasPort ? u.port : (u.scheme == 'http' ? 8000 : 443);
  return Uri(scheme: u.scheme, host: u.host, port: puerto);
}

/// Direcciones de la red del laboratorio, a las que se permite HTTP sin
/// cifrar. Android no deja expresar rangos de IP en
/// network_security_config.xml, así que la regla se aplica aquí.
bool esRedLocal(String host) {
  final h = host.toLowerCase();
  if (h == 'localhost' || h.endsWith('.local') || h.endsWith('.lan') || h.endsWith('.localhost')) {
    return true;
  }
  final p = h.split('.').map(int.tryParse).toList();
  if (p.length != 4 || p.any((x) => x == null || x < 0 || x > 255)) return false;
  final a = p[0]!, b = p[1]!;
  return a == 10 || a == 127 || (a == 192 && b == 168) || (a == 172 && b >= 16 && b <= 31) || (a == 169 && b == 254);
}
