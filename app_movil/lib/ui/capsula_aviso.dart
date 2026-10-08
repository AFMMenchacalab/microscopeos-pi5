import 'package:flutter/cupertino.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../estado/avisos.dart';
import 'tema.dart';

/// Los avisos cortos de [avisosProvider]: una cápsula arriba, encima de
/// todo, que entra y sale con una animación suave. Se toca para cerrarla.
class CapsulaAviso extends ConsumerWidget {
  const CapsulaAviso({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final aviso = ref.watch(avisosProvider);
    final arriba = MediaQuery.paddingOf(context).top + 8;
    return Positioned(
      top: arriba,
      left: 12,
      right: 12,
      child: AnimatedSwitcher(
        duration: const Duration(milliseconds: 260),
        switchInCurve: Curves.easeOutCubic,
        switchOutCurve: Curves.easeInCubic,
        transitionBuilder: (hijo, anim) => FadeTransition(
          opacity: anim,
          child: SlideTransition(
            position: Tween(begin: const Offset(0, -0.6), end: Offset.zero).animate(anim),
            child: hijo,
          ),
        ),
        child: aviso == null
            ? const SizedBox.shrink(key: ValueKey('nada'))
            : _Capsula(key: ValueKey(aviso.id), aviso: aviso),
      ),
    );
  }
}

class _Capsula extends ConsumerWidget {
  const _Capsula({super.key, required this.aviso});
  final Aviso aviso;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final (IconData icono, Color color) = switch (aviso.tipo) {
      TipoAviso.exito => (CupertinoIcons.checkmark_circle_fill, Colores.bien),
      TipoAviso.error => (CupertinoIcons.exclamationmark_triangle_fill, Colores.mal),
      TipoAviso.info => (CupertinoIcons.info_circle_fill, Colores.acento),
    };
    final oscuro = CupertinoTheme.brightnessOf(context) == Brightness.dark;
    return Center(
      child: GestureDetector(
        onTap: () => ref.read(avisosProvider.notifier).ocultar(),
        child: Semantics(
          liveRegion: true,
          label: aviso.texto,
          child: Container(
            constraints: const BoxConstraints(maxWidth: 520, minHeight: 52),
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
            decoration: BoxDecoration(
              color: oscuro ? const Color(0xF22C2C2E) : const Color(0xF7FFFFFF),
              borderRadius: BorderRadius.circular(26),
              boxShadow: const [BoxShadow(color: Color(0x33000000), blurRadius: 24, offset: Offset(0, 8))],
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(icono, color: CupertinoDynamicColor.resolve(color, context), size: 22),
                const SizedBox(width: 10),
                Flexible(
                  child: Text(
                    aviso.texto,
                    style: Estilos.base(context).copyWith(fontSize: 15, fontWeight: FontWeight.w500),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
