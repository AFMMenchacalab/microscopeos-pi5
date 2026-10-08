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
import 'visor_fotos_page.dart';

/// Un experimento: datos, cuadrícula de miniaturas por cámara y notas.
class DetalleExperimentoPage extends ConsumerStatefulWidget {
  const DetalleExperimentoPage({super.key, required this.id, required this.titulo});

  final String id;
  final String titulo;

  static Future<void> abrir(BuildContext context, String id, String titulo) => Navigator.of(context).push(
    CupertinoPageRoute<void>(
      title: titulo,
      builder: (_) => DetalleExperimentoPage(id: id, titulo: titulo),
    ),
  );

  @override
  ConsumerState<DetalleExperimentoPage> createState() => _DetalleExperimentoPageState();
}

class _DetalleExperimentoPageState extends ConsumerState<DetalleExperimentoPage> {
  int? _camara;

  @override
  Widget build(BuildContext context) {
    final detalle = ref.watch(detalleExperimentoProvider(widget.id));
    final notas = ref.watch(notasProvider(widget.id));
    final c = ref.watch(clienteProvider);
    final d = detalle.value;

    final slivers = <Widget>[
      CupertinoSliverNavigationBar(
        largeTitle: Text(d?.info.nombre ?? widget.titulo),
        previousPageTitle: Textos.expTitulo,
      ),
      CupertinoSliverRefreshControl(
        onRefresh: () async {
          ref.invalidate(notasProvider(widget.id));
          await ref
              .refresh(detalleExperimentoProvider(widget.id).future)
              .catchError((_) => DetalleExperimento.vacio(widget.id));
        },
      ),
    ];

    if (d == null) {
      slivers.add(
        SliverFillRemaining(
          hasScrollBody: false,
          child: detalle.hasError
              ? Mensaje(
                  icono: CupertinoIcons.exclamationmark_triangle,
                  titulo: detalle.error.toString(),
                  accion: Textos.reintentar,
                  alTocar: () => ref.invalidate(detalleExperimentoProvider(widget.id)),
                )
              : const Center(child: CupertinoActivityIndicator()),
        ),
      );
    } else {
      final camaras = d.camarasConFotos;
      final cam = _camara != null && camaras.contains(_camara) ? _camara! : (camaras.isEmpty ? 0 : camaras.first);
      final fotos = d.deCamara(cam);
      slivers.addAll([
        SliverPadding(
          padding: const EdgeInsets.fromLTRB(Medidas.margen, 0, Medidas.margen, 12),
          sliver: SliverToBoxAdapter(child: _Resumen(info: d.info)),
        ),
        if (camaras.length > 1)
          SliverPadding(
            padding: const EdgeInsets.fromLTRB(Medidas.margen, 0, Medidas.margen, 12),
            sliver: SliverToBoxAdapter(
              child: Segmentos<int>(
                valor: cam,
                opciones: {for (final x in camaras) x: Textos.camaraN(x)},
                alCambiar: (v) => setState(() => _camara = v),
              ),
            ),
          ),
        if (fotos.isEmpty)
          const SliverToBoxAdapter(
            child: Mensaje(icono: CupertinoIcons.photo, titulo: Textos.expSinFotos),
          )
        else
          SliverPadding(
            padding: const EdgeInsets.symmetric(horizontal: Medidas.margen),
            sliver: SliverGrid.builder(
              gridDelegate: const SliverGridDelegateWithMaxCrossAxisExtent(
                maxCrossAxisExtent: 160,
                mainAxisSpacing: 4,
                crossAxisSpacing: 4,
                childAspectRatio: 4 / 3,
              ),
              itemCount: fotos.length,
              itemBuilder: (context, i) => _Miniatura(
                imagen: c == null ? null : ImagenMicroscopio(c, c.rutaMiniatura(d.info.id, fotos[i], tamano: 320)),
                rel: fotos[i],
                alTocar: () => VisorFotosPage.abrir(context, d.info, fotos, i),
              ),
            ),
          ),
        SliverToBoxAdapter(
          child: CupertinoListSection.insetGrouped(
            header: const Text(Textos.expNotas),
            children: [
              ...switch (notas) {
                AsyncData(:final value) when value.isEmpty => [
                  const CupertinoListTile(title: Text(Textos.expSinNotas)),
                ],
                AsyncData(:final value) => [for (final n in value) _FilaNota(nota: n)],
                AsyncError(:final error) => [CupertinoListTile(title: Text('$error'))],
                _ => [const CupertinoListTile(title: CupertinoActivityIndicator())],
              },
            ],
          ),
        ),
        SliverPadding(
          padding: const EdgeInsets.fromLTRB(32, 0, 32, 40),
          sliver: SliverToBoxAdapter(
            child: Text(Textos.expMasEnLaWeb, textAlign: TextAlign.center, style: Estilos.nota(context)),
          ),
        ),
      ]);
    }
    return CupertinoPageScaffold(child: CustomScrollView(slivers: slivers));
  }
}

class _Resumen extends StatelessWidget {
  const _Resumen({required this.info});
  final Experimento info;

  @override
  Widget build(BuildContext context) {
    final lineas = [
      if (info.inicioLegible != null && info.inicioLegible!.isNotEmpty) info.inicioLegible!,
      if (info.esTimelapse && info.intervaloS != null && info.duracionS != null)
        Textos.expCadaDurante(cadaTexto(info.intervaloS!), duracionTexto(info.duracionS!)),
      [Textos.expFotos(info.nFotos), if (info.bytes > 0) bytesTexto(info.bytes)].join(' · '),
    ];
    return Tarjeta(
      child: Row(
        children: [
          Icon(info.esTimelapse ? CupertinoIcons.timer : CupertinoIcons.camera, color: Colores.acento, size: 30),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Text(
                      info.esTimelapse ? Textos.expTimelapse : Textos.expFotosSueltas,
                      style: Estilos.titular(context),
                    ),
                    if (info.enCurso) ...[
                      const SizedBox(width: 8),
                      const Insignia(Textos.expEnCurso, color: Colores.bien, punto: true),
                    ],
                  ],
                ),
                for (final l in lineas) Text(l, style: Estilos.secundario(context)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _Miniatura extends StatelessWidget {
  const _Miniatura({required this.imagen, required this.rel, required this.alTocar});
  final ImageProvider? imagen;
  final String rel;
  final VoidCallback alTocar;

  @override
  Widget build(BuildContext context) {
    final sufijo = RegExp(r'_(L|R|T|B)\.tif$').firstMatch(rel)?.group(1);
    return Semantics(
      button: true,
      label: rel.split('/').last,
      child: GestureDetector(
        onTap: alTocar,
        child: ClipRRect(
          borderRadius: BorderRadius.circular(6),
          child: ColoredBox(
            color: CupertinoDynamicColor.resolve(Colores.tarjetaInterna, context),
            child: Stack(
              fit: StackFit.expand,
              children: [
                if (imagen != null)
                  Image(
                    image: imagen!,
                    fit: BoxFit.cover,
                    frameBuilder: (c, hijo, frame, sinc) => AnimatedOpacity(
                      opacity: frame == null ? 0 : 1,
                      duration: const Duration(milliseconds: 200),
                      child: hijo,
                    ),
                    errorBuilder: (_, _, _) => const Icon(CupertinoIcons.photo, color: Colores.textoTerciario),
                  ),
                if (sufijo != null)
                  Positioned(
                    right: 4,
                    bottom: 4,
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                      decoration: BoxDecoration(color: const Color(0x99000000), borderRadius: BorderRadius.circular(4)),
                      child: Text(sufijo, style: const TextStyle(color: CupertinoColors.white, fontSize: 11)),
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

class _FilaNota extends StatelessWidget {
  const _FilaNota({required this.nota});
  final Nota nota;

  @override
  Widget build(BuildContext context) {
    final icono = switch (nota.tipo) {
      'pausa' => CupertinoIcons.pause_circle,
      'reanudar' => CupertinoIcons.play_circle,
      _ => CupertinoIcons.text_bubble,
    };
    final pie = [
      if (nota.hora != null) fechaAmigable(nota.hora!),
      if (nota.ciclo != null) Textos.timelapseFoto(nota.ciclo!),
      if (nota.autor.isNotEmpty) nombreCorto(nota.autor),
    ].join(' · ');
    return CupertinoListTile(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      leading: Icon(icono, color: Colores.acento),
      title: Text(nota.texto, maxLines: 4),
      subtitle: Text(pie),
    );
  }
}
