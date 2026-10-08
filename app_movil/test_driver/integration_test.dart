import 'dart:io';

import 'package:integration_test/integration_test_driver_extended.dart';

/// Recibe las capturas de pantalla de integration_test/app_test.dart y las
/// guarda en docs/capturas/.
Future<void> main() => integrationDriver(
  onScreenshot: (nombre, bytes, [args]) async {
    final f = File('docs/capturas/$nombre.png');
    await f.parent.create(recursive: true);
    await f.writeAsBytes(bytes);
    return true;
  },
);
