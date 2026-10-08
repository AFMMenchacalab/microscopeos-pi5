// Cómo se escriben fechas, tamaños e intervalos (igual que la web:
// fechaAmigable, horaTl, cadaTl, proximaTl, fmtBytes en index_uiux.html).

const _meses = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];

String _dos(int n) => n.toString().padLeft(2, '0');

String horaMinutos(DateTime d) => '${_dos(d.hour)}:${_dos(d.minute)}';

bool _mismoDia(DateTime a, DateTime b) => a.year == b.year && a.month == b.month && a.day == b.day;

/// «hoy, 14:30», «ayer, 09:05» o «5 oct 2026, 14:30».
String fechaAmigable(DateTime d, {DateTime? ahora}) {
  final hoy = ahora ?? DateTime.now();
  if (_mismoDia(d, hoy)) return 'hoy, ${horaMinutos(d)}';
  if (_mismoDia(d, hoy.subtract(const Duration(days: 1)))) return 'ayer, ${horaMinutos(d)}';
  if (_mismoDia(d, hoy.add(const Duration(days: 1)))) return 'mañana, ${horaMinutos(d)}';
  return '${d.day} ${_meses[d.month - 1]} ${d.year}, ${horaMinutos(d)}';
}

/// «5 min», «30 s», «1.5 h».
String cadaTexto(int segundos) {
  if (segundos < 60) return '$segundos s';
  if (segundos < 3600) return '${_corto(segundos / 60)} min';
  return '${_corto(segundos / 3600)} h';
}

/// «en unos segundos», «en 45 s», «en 3 min» o la hora.
String proximaTexto(DateTime cuando, {DateTime? ahora}) {
  final s = cuando.difference(ahora ?? DateTime.now()).inSeconds;
  if (s <= 5) return 'en unos segundos';
  if (s < 90) return 'en $s s';
  if (s < 5400) return 'en ${(s / 60).round()} min';
  return fechaAmigable(cuando, ahora: ahora);
}

/// «2 h 30 min», «45 min», «3 días 4 h».
String duracionTexto(int segundos) {
  final s = segundos < 0 ? 0 : segundos;
  final d = s ~/ 86400, h = (s % 86400) ~/ 3600, m = (s % 3600) ~/ 60;
  if (d > 0) return h > 0 ? '$d ${d == 1 ? 'día' : 'días'} $h h' : '$d ${d == 1 ? 'día' : 'días'}';
  if (h > 0) return m > 0 ? '$h h $m min' : '$h h';
  return '$m min';
}

/// «16.4 GB», «820 MB», «12 KB».
String bytesTexto(num n) {
  if (n >= 1e9) return '${(n / 1e9).toStringAsFixed(1)} GB';
  if (n >= 1e6) return '${(n / 1e6).toStringAsFixed(1)} MB';
  if (n >= 1e3) return '${(n / 1e3).toStringAsFixed(0)} KB';
  return '$n B';
}

/// 1.0 -> «1», 1.5 -> «1.5», 0.25 -> «0.25».
String _corto(double v) {
  final t = v.toStringAsFixed(2);
  return t.replaceFirst(RegExp(r'\.?0+$'), '');
}

String numeroCorto(double v) => _corto(v);
