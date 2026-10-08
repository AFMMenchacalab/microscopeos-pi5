import 'package:flutter/cupertino.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../api/imagen_api.dart';
import '../../api/modelos.dart';
import '../../estado/conexion.dart';
import '../../estado/datos.dart';
import '../../textos.dart';
import '../../ui/componentes.dart';
import '../../ui/formato.dart';
import '../../ui/tema.dart';
import 'detalle_page.dart';

/// Los experimentos guardados en el microscopio, del más nuevo al más viejo.
class ExperimentosPage extends ConsumerWidget {
  const ExperimentosPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final lista = ref.watch(experimentosProvider);
    final c = ref.watch(clienteProvider);
    final datos = lista.value;

    return CupertinoPageScaffold(
      child: CustomScrollView(
        slivers: [
          const CupertinoSliverNavigationBar(largeTitle: Text(Textos.expTitulo)),
          CupertinoSliverRefreshControl(onRefresh: () => ref.refresh(experimentosProvider.future)),
          if (datos == null && lista.hasError)
            SliverFillRemaining(
              hasScrollBody: false,
              child: Mensaje(
                icono: CupertinoIcons.wifi_slash,
                titulo: Textos.sinConexion,
                detalle: lista.error.toString(),
                accion: Textos.reintentar,
                alTocar: () => ref.invalidate(experimentosProvider),
              ),
            )
          else if (datos == null)
            const SliverFillRemaining(hasScrollBody: false, child: Center(child: CupertinoActivityIndicator()))
          else if (datos.experimentos.isEmpty)
            const SliverFillRemaining(
              hasScrollBody: false,
              child: Mensaje(icono: CupertinoIcons.photo_on_rectangle, titulo: Textos.expNinguno),
            )
          else
            SliverToBoxAdapter(
              child: CupertinoListSection.insetGrouped(
                footer: datos.libreBytes == null
                    ? null
                    : Text(Textos.expLibre(bytesTexto(datos.libreBytes!), datos.fotosQueCaben ?? 0)),
                children: [
                  for (final e in datos.experimentos)
                    _FilaExperimento(
                      experimento: e,
                      portada: c == null || e.portada == null
                          ? null
                          : ImagenMicroscopio(c, c.rutaMiniatura(e.id, e.portada!, tamano: 160)),
                    ),
                ],
              ),
            ),
          const SliverToBoxAdapter(child: SizedBox(height: 40)),
        ],
      ),
    );
  }
}

class _FilaExperimento extends StatelessWidget {
  const _FilaExperimento({required this.experimento, required this.portada});
  final Experimento experimento;
  final ImageProvider? portada;

  @override
  Widget build(BuildContext context) {
    final e = experimento;
    final detalle = [
      if (e.inicio != null) fechaAmigable(e.inicio!),
      Textos.expFotos(e.nFotos),
      if (e.bytes > 0) bytesTexto(e.bytes),
    ].join(' · ');
    return CupertinoListTile.notched(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      leadingSize: 64,
      leading: ClipRRect(
        borderRadius: BorderRadius.circular(10),
        child: SizedBox(
          width: 64,
          height: 64,
          child: ColoredBox(
            color: CupertinoDynamicColor.resolve(Colores.tarjetaInterna, context),
            child: portada == null
                ? Icon(e.esTimelapse ? CupertinoIcons.timer : CupertinoIcons.photo, color: Colores.textoTerciario)
                : Image(
                    image: portada!,
                    fit: BoxFit.cover,
                    errorBuilder: (_, _, _) => const Icon(CupertinoIcons.photo, color: Colores.textoTerciario),
                  ),
          ),
        ),
      ),
      title: Row(
        children: [
          Flexible(child: Text(e.nombre, overflow: TextOverflow.ellipsis)),
          if (e.enCurso) ...[
            const SizedBox(width: 8),
            const Insignia(Textos.expEnCurso, color: Colores.bien, punto: true),
          ],
        ],
      ),
      subtitle: Text(detalle),
      additionalInfo: Icon(
        e.esTimelapse ? CupertinoIcons.timer : CupertinoIcons.camera,
        size: 18,
        color: Colores.textoTerciario,
      ),
      trailing: const CupertinoListTileChevron(),
      onTap: () => DetalleExperimentoPage.abrir(context, e.id, e.nombre),
    );
  }
}
