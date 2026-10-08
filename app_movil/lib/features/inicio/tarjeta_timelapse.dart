import 'package:flutter/cupertino.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../api/cliente_api.dart';
import '../../api/imagen_api.dart';
import '../../api/modelos.dart';
import '../../estado/avisos.dart';
import '../../estado/conexion.dart';
import '../../estado/datos.dart';
import '../../textos.dart';
import '../../ui/acciones.dart';
import '../../ui/componentes.dart';
import '../../ui/formato.dart';
import '../../ui/tema.dart';
import '../../ui/visor_imagen.dart';
import '../experimentos/detalle_page.dart';
import '../timelapse/nuevo_timelapse_page.dart';

/// El timelapse en curso: ciclo, progreso, pausar/seguir, anotar,
/// detener y la última foto de cada cámara. Sin timelapse, el botón para
/// empezar uno.
class TarjetaTimelapse extends ConsumerWidget {
  const TarjetaTimelapse({super.key, required this.estado});
  final EstadoGeneral estado;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = estado.timelapse;
    if (t == null) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const TituloSeccion(Textos.timelapse, icono: CupertinoIcons.timer),
          Tarjeta(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(estado.reanudando ?? Textos.timelapseNinguno, style: Estilos.titular(context)),
                const SizedBox(height: 4),
                Text(Textos.timelapseNingunoDetalle, style: Estilos.secundario(context)),
                const SizedBox(height: 14),
                BotonGrande(
                  texto: Textos.timelapseNuevo,
                  icono: CupertinoIcons.add,
                  alTocar: estado.bloqueaNuevoTimelapse ? null : () => NuevoTimelapsePage.abrir(context),
                ),
              ],
            ),
          ),
        ],
      );
    }

    final ahora = DateTime.now();
    final partes = <String>[
      Textos.timelapseFoto(estado.ciclo),
      if (t.intervaloS != null) Textos.timelapseCada(cadaTexto(t.intervaloS!)),
      if (!t.pausado && t.proxima != null) Textos.timelapseProxima(proximaTexto(t.proxima!, ahora: ahora)),
    ];
    final color = t.pausado ? Colores.aviso : Colores.bien;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        TituloSeccion(
          t.pausado ? Textos.timelapseEnPausa : Textos.timelapseEnCurso,
          icono: t.pausado ? CupertinoIcons.pause_circle : CupertinoIcons.timer,
        ),
        Tarjeta(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  Expanded(child: Text(t.nombre ?? Textos.timelapse, style: Estilos.titulo(context))),
                  Insignia(t.pausado ? 'EN PAUSA' : 'EN CURSO', color: color, punto: true),
                ],
              ),
              const SizedBox(height: 6),
              Text(partes.join(' · '), style: Estilos.secundario(context)),
              if (t.pausado) ...[
                const SizedBox(height: 4),
                Text(Textos.timelapsePuedesMirar, style: Estilos.secundario(context)),
              ],
              const SizedBox(height: 12),
              BarraProgreso(t.progreso(ahora), color: color),
              const SizedBox(height: 6),
              Wrap(
                alignment: WrapAlignment.spaceBetween,
                spacing: 12,
                children: [
                  if (t.inicio != null) Text(fechaAmigable(t.inicio!), style: Estilos.nota(context)),
                  if (t.finPrevisto != null)
                    Text(Textos.timelapseTermina(fechaAmigable(t.finPrevisto!)), style: Estilos.nota(context)),
                ],
              ),
              if (t.reanudaciones.isNotEmpty) ...[
                const SizedBox(height: 10),
                Franja(
                  texto: Textos.timelapseReanudado(fechaAmigable(t.reanudaciones.last)),
                  icono: CupertinoIcons.bolt_fill,
                ),
              ],
              const SizedBox(height: 14),
              _UltimasFotos(resumen: t),
              const SizedBox(height: 14),
              BotonGrande(
                texto: t.pausado ? Textos.timelapseSeguir : Textos.timelapsePausar,
                icono: t.pausado ? CupertinoIcons.play_fill : CupertinoIcons.pause_fill,
                estilo: t.pausado ? EstiloBoton.primario : EstiloBoton.secundario,
                alTocar: () => _pausarOSeguir(context, ref, t.pausado),
              ),
              const SizedBox(height: 10),
              Row(
                children: [
                  Expanded(
                    child: BotonGrande(
                      texto: Textos.timelapseAnotar,
                      icono: CupertinoIcons.pencil,
                      estilo: EstiloBoton.suave,
                      alTocar: () => _anotar(context, ref),
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: BotonGrande(
                      texto: Textos.timelapseDetener,
                      icono: CupertinoIcons.stop_fill,
                      estilo: EstiloBoton.destructivo,
                      alTocar: () => _detener(context, ref),
                    ),
                  ),
                ],
              ),
              if (t.carpeta != null)
                CupertinoButton(
                  onPressed: () => DetalleExperimentoPage.abrir(context, t.carpeta!, t.nombre ?? ''),
                  child: const Text(Textos.timelapseVerExperimento),
                ),
            ],
          ),
        ),
      ],
    );
  }

  Future<void> _pausarOSeguir(BuildContext context, WidgetRef ref, bool enPausa) async {
    await ejecutar(
      context,
      ref,
      (c) => enPausa ? c.continuarTimelapse() : c.pausarTimelapse(),
      exito: enPausa ? Textos.timelapseContinuado : Textos.timelapsePausado,
    );
    ref.invalidate(estadoProvider);
  }

  Future<void> _anotar(BuildContext context, WidgetRef ref) async {
    final texto = await pedirTexto(context, titulo: Textos.timelapseNotaTitulo, ejemplo: Textos.timelapseNotaEjemplo);
    if (texto == null || texto.trim().isEmpty || !context.mounted) return;
    await ejecutar(
      context,
      ref,
      (c) => c.anotarTimelapse(texto.trim()),
      exitoCon: (n) => Textos.timelapseNotaOk(horaMinutos(n.hora ?? DateTime.now())),
    );
  }

  Future<void> _detener(BuildContext context, WidgetRef ref) async {
    final si = await confirmar(
      context,
      titulo: Textos.timelapseDetenerPregunta,
      detalle: Textos.timelapseDetenerDetalle,
      accion: Textos.timelapseDetener,
      destructiva: true,
    );
    if (!si || !context.mounted) return;
    // El servidor termina el ciclo en curso antes de contestar (con
    // autofoco, puede tardar más de medio minuto).
    ref.read(avisosProvider.notifier).mostrar(Textos.timelapseDeteniendo, duracion: const Duration(minutes: 2));
    await ejecutar(context, ref, (c) => c.detenerTimelapse(), exito: Textos.timelapseDetenido);
    ref.invalidate(estadoProvider);
  }
}

/// La última foto de cada cámara (GET /timelapse/vista/{cam}). La
/// dirección cambia con el ciclo, así que se recarga sola cuando hay una
/// foto nueva y no antes.
class _UltimasFotos extends ConsumerWidget {
  const _UltimasFotos({required this.resumen});
  final ResumenTimelapse resumen;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final c = ref.watch(clienteProvider);
    if (c == null || resumen.camaras.isEmpty) return const SizedBox.shrink();
    return Row(
      children: [
        for (final (i, cam) in resumen.camaras.indexed) ...[
          if (i > 0) const SizedBox(width: 10),
          Expanded(child: _foto(context, c, cam, resumen.ultimas[cam])),
        ],
      ],
    );
  }

  Widget _foto(BuildContext context, ClienteMicroscopio cliente, int cam, UltimaFoto? u) {
    final pie = u == null
        ? Textos.camaraN(cam)
        : '${Textos.camaraN(cam)} · ${Textos.timelapseFoto(u.ciclo)}${u.hora != null ? ', ${horaMinutos(u.hora!)}' : ''}';
    Widget imagen;
    if (u == null) {
      imagen = Center(
        child: Padding(
          padding: const EdgeInsets.all(8),
          child: Text(Textos.timelapseSinFotoAun, textAlign: TextAlign.center, style: Estilos.nota(context)),
        ),
      );
    } else {
      final version = '${u.ciclo}${u.dpc ? 'd' : ''}';
      final ruta = cliente.rutaVistaTimelapse(cam, version: version);
      final grande = cliente.rutaVistaTimelapse(cam, tamano: 1600, version: version);
      imagen = GestureDetector(
        onTap: () => VisorImagen.abrir(context, ImagenMicroscopio(cliente, grande), pie),
        child: Image(
          image: ImagenMicroscopio(cliente, ruta),
          fit: BoxFit.cover,
          gaplessPlayback: true,
          errorBuilder: (_, _, _) => const Center(child: Icon(CupertinoIcons.photo, color: Colores.textoTerciario)),
        ),
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        ClipRRect(
          borderRadius: BorderRadius.circular(14),
          child: AspectRatio(
            aspectRatio: 4 / 3,
            child: ColoredBox(color: CupertinoDynamicColor.resolve(Colores.tarjetaInterna, context), child: imagen),
          ),
        ),
        const SizedBox(height: 4),
        Text(pie, style: Estilos.nota(context), maxLines: 1, overflow: TextOverflow.ellipsis),
      ],
    );
  }
}
