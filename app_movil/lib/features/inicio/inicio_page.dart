import 'package:flutter/cupertino.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../estado/datos.dart';
import '../../estado/preferencias.dart';
import '../../textos.dart';
import '../../ui/barra_control.dart';
import '../../ui/componentes.dart';
import '../../ui/tema.dart';
import 'tarjeta_incubadora.dart';
import 'tarjeta_timelapse.dart';

/// Lo primero que se ve: quién controla, la incubadora, el timelapse en
/// curso y accesos a Vivo y Experimentos.
class InicioPage extends ConsumerWidget {
  const InicioPage({super.key, required this.irAVivo, required this.irAExperimentos});

  final VoidCallback irAVivo;
  final VoidCallback irAExperimentos;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final microscopio = ref.watch(microscopiosProvider.select((e) => e.actual));
    final estado = ref.watch(estadoProvider);
    final e = estado.value;
    final sinConexion = estado.hasError;

    return CupertinoPageScaffold(
      child: CustomScrollView(
        slivers: [
          CupertinoSliverNavigationBar(largeTitle: Text(microscopio?.nombre ?? Textos.app)),
          CupertinoSliverRefreshControl(
            onRefresh: () async {
              ref.invalidate(estadoProvider);
              ref.invalidate(controlProvider);
              ref.invalidate(incubadoraProvider);
              await ref.read(estadoProvider.future).catchError((_) => e!);
            },
          ),
          SliverPadding(
            padding: const EdgeInsets.fromLTRB(Medidas.margen, 4, Medidas.margen, 32),
            sliver: SliverList.list(
              children: [
                if (sinConexion) ...[
                  Franja(
                    texto: '${Textos.sinConexion}. ${estado.error}',
                    icono: CupertinoIcons.wifi_slash,
                    color: Colores.mal,
                    accion: Textos.reintentar,
                    alTocar: () => ref.invalidate(estadoProvider),
                  ),
                  const SizedBox(height: Medidas.espacio),
                ],
                const BarraControl(),
                const SizedBox(height: 22),
                const TarjetaIncubadora(),
                const SizedBox(height: 22),
                if (e != null)
                  TarjetaTimelapse(estado: e)
                else if (!sinConexion)
                  const Padding(padding: EdgeInsets.all(24), child: CupertinoActivityIndicator()),
                const SizedBox(height: 22),
                _Acceso(
                  icono: CupertinoIcons.videocam_fill,
                  color: Colores.vivo,
                  titulo: Textos.accesoVivo,
                  detalle: Textos.accesoVivoDetalle,
                  alTocar: irAVivo,
                ),
                const SizedBox(height: 10),
                _Acceso(
                  icono: CupertinoIcons.photo_on_rectangle,
                  color: CupertinoColors.systemIndigo,
                  titulo: Textos.accesoExperimentos,
                  detalle: Textos.accesoExperimentosDetalle,
                  alTocar: irAExperimentos,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _Acceso extends StatelessWidget {
  const _Acceso({
    required this.icono,
    required this.color,
    required this.titulo,
    required this.detalle,
    required this.alTocar,
  });

  final IconData icono;
  final Color color;
  final String titulo, detalle;
  final VoidCallback alTocar;

  @override
  Widget build(BuildContext context) {
    return Tarjeta(
      alTocar: alTocar,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      child: Row(
        children: [
          Container(
            width: 44,
            height: 44,
            decoration: BoxDecoration(
              color: CupertinoDynamicColor.resolve(color, context),
              borderRadius: BorderRadius.circular(12),
            ),
            child: Icon(icono, color: CupertinoColors.white, size: 24),
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(titulo, style: Estilos.titular(context)),
                Text(detalle, style: Estilos.nota(context)),
              ],
            ),
          ),
          const Icon(CupertinoIcons.chevron_forward, color: Colores.textoTerciario),
        ],
      ),
    );
  }
}
