import 'package:flutter/cupertino.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/errores.dart';
import '../api/modelos.dart';
import '../estado/avisos.dart';
import '../estado/conexion.dart';
import '../estado/datos.dart';
import '../textos.dart';
import 'acciones.dart';
import 'tema.dart';

/// Quién controla el microscopio, arriba de Inicio y de Vivo.
///
/// Verde: lo controlas tú. Naranja: otra persona, con «Tomar el control».
/// Gris: nadie (el primero que cambie algo lo toma solo).
class BarraControl extends ConsumerWidget {
  const BarraControl({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final estado = ref.watch(controlProvider).value;
    if (estado == null) return const SizedBox.shrink();
    final otros = estado.conectados.where((p) => p.id != estado.yo.id).length;

    final (Color color, IconData icono, String texto, String? accion) = estado.tengoControl
        ? (Colores.bien, CupertinoIcons.hand_raised_fill, Textos.controlTuyo, null)
        : estado.controlador != null
        ? (
            Colores.aviso,
            CupertinoIcons.person_fill,
            Textos.controlDeOtro(estado.controlador!.corto),
            Textos.controlTomar,
          )
        : (Colores.textoSecundario, CupertinoIcons.hand_raised, Textos.controlNadie, null);
    final c = CupertinoDynamicColor.resolve(color, context);
    final sub = [
      if (otros > 0) Textos.controlConectados(otros + 1),
      if (estado.reservaDeOtro && estado.reservaDe != null) Textos.controlTurnoDe(nombreCorto(estado.reservaDe!)),
    ].join(' · ');

    return Semantics(
      container: true,
      label: texto,
      child: Container(
        constraints: const BoxConstraints(minHeight: Medidas.toque),
        padding: const EdgeInsets.fromLTRB(14, 6, 6, 6),
        decoration: BoxDecoration(color: c.withValues(alpha: 0.13), borderRadius: BorderRadius.circular(16)),
        child: Row(
          children: [
            Icon(icono, color: c, size: 20),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(texto, style: Estilos.base(context).copyWith(fontSize: 15, fontWeight: FontWeight.w600)),
                  if (sub.isNotEmpty) Text(sub, style: Estilos.nota(context)),
                ],
              ),
            ),
            if (accion != null)
              CupertinoButton(
                padding: const EdgeInsets.symmetric(horizontal: 14),
                minimumSize: const Size(Medidas.toque, 44),
                color: c,
                borderRadius: BorderRadius.circular(12),
                onPressed: () => _tomar(context, ref, estado.controlador!.corto),
                child: Text(
                  accion,
                  style: Estilos.base(context)
                      .copyWith(fontSize: 15, fontWeight: FontWeight.w600, color: CupertinoColors.white),
                ),
              ),
          ],
        ),
      ),
    );
  }

  Future<void> _tomar(BuildContext context, WidgetRef ref, String quien) async {
    final si = await preguntarTomarControl(context, quien);
    if (!si) return;
    final c = ref.read(clienteProvider);
    if (c == null) return;
    try {
      await c.tomarControl();
      HapticFeedback.mediumImpact();
      ref.read(avisosProvider.notifier).exito(Textos.controlAhoraTuyo);
    } on ErrorApi catch (e) {
      ref.read(avisosProvider.notifier).error(e.mensaje);
    }
    ref.invalidate(controlProvider);
  }
}
