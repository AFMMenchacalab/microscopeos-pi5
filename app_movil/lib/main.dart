import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'app.dart';
import 'estado/preferencias.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final prefs = await SharedPreferences.getInstance();
  runApp(
    ProviderScope(
      overrides: [preferenciasProvider.overrideWithValue(prefs)],
      // Los reintentos los decide la app (solo lecturas, ver cliente_api.dart).
      retry: (_, _) => null,
      child: const MicroscopeOSApp(),
    ),
  );
}
