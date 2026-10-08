import 'package:flutter/cupertino.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/cliente_api.dart';
import '../api/errores.dart';
import '../api/modelos.dart';
import '../estado/avisos.dart';
import '../estado/conexion.dart';
import '../estado/datos.dart';
import '../textos.dart';

/// Ejecuta una acción sobre el microscopio y se encarga de lo común:
///
/// - si otra persona tiene el control (423), pregunta «¿Tomar el
///   control?» y, si dice que sí, lo toma y repite la acción una vez;
/// - cualquier otro error se muestra tal cual (los mensajes del
///   microscopio ya están pensados para personas);
/// - si sale bien y hay [exito], lo avisa.
///
/// Devuelve el resultado, o null si no se pudo.
Future<T?> ejecutar<T>(
  BuildContext context,
  WidgetRef ref,
  Future<T> Function(ClienteMicroscopio c) accion, {
  String? exito,
  String Function(T resultado)? exitoCon,
}) async {
  final c = ref.read(clienteProvider);
  if (c == null) return null;
  final avisos = ref.read(avisosProvider.notifier);
  for (var intento = 0; intento < 2; intento++) {
    try {
      final r = await accion(c);
      final texto = exitoCon != null ? exitoCon(r) : exito;
      if (texto != null) avisos.exito(texto);
      return r;
    } on ErrorSinControl catch (e) {
      ref.invalidate(controlProvider);
      if (intento > 0 || !context.mounted) {
        avisos.error(e.mensaje);
        return null;
      }
      final tomar = await preguntarTomarControl(context, nombreCorto(e.quien));
      if (!tomar) return null;
      try {
        await c.tomarControl();
        ref.invalidate(controlProvider);
        avisos.mostrar(Textos.controlAhoraTuyo);
      } on ErrorApi catch (e2) {
        avisos.error(e2.mensaje);
        return null;
      }
    } on ErrorApi catch (e) {
      avisos.error(e.mensaje);
      return null;
    }
  }
  return null;
}

Future<bool> preguntarTomarControl(BuildContext context, String quien) async {
  final r = await showCupertinoDialog<bool>(
    context: context,
    builder: (c) => CupertinoAlertDialog(
      title: Text(Textos.controlTomarPregunta(quien)),
      content: const Text(Textos.controlTomarDetalle),
      actions: [
        CupertinoDialogAction(onPressed: () => Navigator.pop(c, false), child: const Text(Textos.cancelar)),
        CupertinoDialogAction(
          isDefaultAction: true,
          onPressed: () => Navigator.pop(c, true),
          child: const Text(Textos.controlTomar),
        ),
      ],
    ),
  );
  return r ?? false;
}

/// Pregunta antes de algo que no se puede deshacer.
Future<bool> confirmar(
  BuildContext context, {
  required String titulo,
  String? detalle,
  required String accion,
  bool destructiva = false,
}) async {
  final r = await showCupertinoDialog<bool>(
    context: context,
    builder: (c) => CupertinoAlertDialog(
      title: Text(titulo),
      content: detalle == null ? null : Text(detalle),
      actions: [
        CupertinoDialogAction(
          isDefaultAction: destructiva,
          onPressed: () => Navigator.pop(c, false),
          child: const Text(Textos.cancelar),
        ),
        CupertinoDialogAction(
          isDestructiveAction: destructiva,
          isDefaultAction: !destructiva,
          onPressed: () => Navigator.pop(c, true),
          child: Text(accion),
        ),
      ],
    ),
  );
  return r ?? false;
}

/// Pide un texto corto (una nota).
Future<String?> pedirTexto(BuildContext context, {required String titulo, String? ejemplo}) {
  final ctl = TextEditingController();
  return showCupertinoDialog<String>(
    context: context,
    builder: (c) => CupertinoAlertDialog(
      title: Text(titulo),
      content: Padding(
        padding: const EdgeInsets.only(top: 12),
        child: CupertinoTextField(
          controller: ctl,
          placeholder: ejemplo,
          autofocus: true,
          maxLength: 500,
          maxLines: 3,
          minLines: 1,
          textCapitalization: TextCapitalization.sentences,
          onSubmitted: (v) => Navigator.pop(c, v),
        ),
      ),
      actions: [
        CupertinoDialogAction(onPressed: () => Navigator.pop(c), child: const Text(Textos.cancelar)),
        CupertinoDialogAction(
          isDefaultAction: true,
          onPressed: () => Navigator.pop(c, ctl.text),
          child: const Text(Textos.guardar),
        ),
      ],
    ),
  );
}
