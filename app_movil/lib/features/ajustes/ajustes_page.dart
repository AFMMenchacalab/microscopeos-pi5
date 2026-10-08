import 'package:flutter/cupertino.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../estado/avisos.dart';
import '../../estado/datos.dart';
import '../../estado/preferencias.dart';
import '../../textos.dart';
import '../../ui/acciones.dart';
import '../../ui/formato.dart';
import '../../ui/tema.dart';
import '../conexion/conexion_page.dart';
import '../conexion/login_acceso_page.dart';

/// Abre la interfaz web del microscopio en el navegador del teléfono,
/// para todo lo que la app todavía no hace.
Future<void> abrirEnLaWeb(WidgetRef ref) async {
  final m = ref.read(microscopiosProvider).actual;
  if (m == null) return;
  final ok = await launchUrl(m.uri, mode: LaunchMode.externalApplication);
  if (!ok) ref.read(avisosProvider.notifier).error(m.url);
}

class AjustesPage extends ConsumerWidget {
  const AjustesPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final actual = ref.watch(microscopiosProvider.select((e) => e.actual));
    final tema = ref.watch(temaProvider);
    final controlAsync = ref.watch(controlProvider);
    final control = controlAsync.value;
    final version = ref.watch(versionProvider);

    return CupertinoPageScaffold(
      child: CustomScrollView(
        slivers: [
          const CupertinoSliverNavigationBar(largeTitle: Text(Textos.ajustesTitulo)),
          SliverList.list(
            children: [
              CupertinoListSection.insetGrouped(
                header: const Text(Textos.ajustesMicroscopio),
                children: [
                  CupertinoListTile(
                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                    leading: const Icon(CupertinoIcons.circle_grid_hex_fill, color: Colores.acento),
                    title: Text(actual?.nombre ?? ''),
                    subtitle: Text(actual?.url ?? ''),
                  ),
                  CupertinoListTile(
                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                    title: const Text(Textos.ajustesCambiar),
                    trailing: const CupertinoListTileChevron(),
                    onTap: () =>
                        Navigator.of(context)
                            .push(CupertinoPageRoute<void>(builder: (_) => const ConexionPage(desdeAjustes: true))),
                  ),
                  if (actual?.sesion != null)
                    CupertinoListTile(
                      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                      title: const Text(Textos.ajustesCerrarSesion, style: TextStyle(color: Colores.mal)),
                      subtitle: const Text(Textos.ajustesCerrarSesionDetalle),
                      onTap: () async {
                        final si = await confirmar(
                          context,
                          titulo: Textos.ajustesCerrarSesion,
                          detalle: Textos.ajustesCerrarSesionDetalle,
                          accion: Textos.ajustesCerrarSesion,
                          destructiva: true,
                        );
                        if (!si) return;
                        await cerrarSesionRemota(ref);
                      },
                    ),
                ],
              ),
              CupertinoListSection.insetGrouped(
                header: const Text(Textos.ajustesApariencia),
                children: [
                  Padding(
                    padding: const EdgeInsets.all(12),
                    child: SizedBox(
                      width: double.infinity,
                      child: CupertinoSlidingSegmentedControl<ModoTema>(
                        groupValue: tema,
                        onValueChanged: (v) {
                          if (v != null) ref.read(temaProvider.notifier).cambiar(v);
                        },
                        children: const {
                          ModoTema.oscuro: Padding(
                            padding: EdgeInsets.symmetric(vertical: 10),
                            child: Text(Textos.ajustesOscuro),
                          ),
                          ModoTema.claro: Padding(
                            padding: EdgeInsets.symmetric(vertical: 10),
                            child: Text(Textos.ajustesClaro),
                          ),
                          ModoTema.sistema: Padding(
                            padding: EdgeInsets.symmetric(vertical: 10),
                            child: Text(Textos.ajustesSistema),
                          ),
                        },
                      ),
                    ),
                  ),
                ],
              ),
              CupertinoListSection.insetGrouped(
                header: const Text(Textos.ajustesControl),
                footer: const Text(Textos.controlExplicacion),
                children: [
                  CupertinoListTile(
                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                    title: Text(
                      control == null
                          ? (controlAsync.hasError ? '${controlAsync.error}' : Textos.cargando)
                          : control.tengoControl
                          ? Textos.controlTuyo
                          : control.controlador != null
                          ? Textos.controlDeOtro(control.controlador!.corto)
                          : Textos.controlNadie,
                    ),
                    subtitle: control == null ? null : Text(control.yo.nombre),
                  ),
                  if (control != null && control.tengoControl)
                    CupertinoListTile(
                      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                      title: const Text(Textos.controlSoltar, style: TextStyle(color: Colores.acento)),
                      onTap: () async {
                        await ejecutar(context, ref, (c) => c.soltarControl(), exito: Textos.controlSoltado);
                        ref.invalidate(controlProvider);
                      },
                    ),
                  if (control != null && control.controlaOtro)
                    CupertinoListTile(
                      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                      title: const Text(Textos.controlTomar, style: TextStyle(color: Colores.acento)),
                      onTap: () async {
                        if (!await preguntarTomarControl(context, control.controlador!.corto)) return;
                        if (!context.mounted) return;
                        await ejecutar(context, ref, (c) => c.tomarControl(), exito: Textos.controlAhoraTuyo);
                        ref.invalidate(controlProvider);
                      },
                    ),
                ],
              ),
              CupertinoListSection.insetGrouped(
                header: const Text(Textos.ajustesEnLaWeb),
                footer: const Text(Textos.ajustesEnLaWebDetalle),
                children: [
                  CupertinoListTile(
                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                    leading: const Icon(CupertinoIcons.globe, color: Colores.acento),
                    title: const Text(Textos.abrirEnLaWeb, style: TextStyle(color: Colores.acento)),
                    trailing: const Icon(CupertinoIcons.arrow_up_right_square, color: Colores.textoTerciario),
                    onTap: () => abrirEnLaWeb(ref),
                  ),
                  for (final f in Textos.ajustesFuncionesWeb)
                    CupertinoListTile(
                      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                      leading: const Icon(CupertinoIcons.circle_fill, size: 6, color: Colores.textoTerciario),
                      leadingSize: 12,
                      title: Text(f, style: Estilos.secundario(context)),
                    ),
                ],
              ),
              CupertinoListSection.insetGrouped(
                header: const Text(Textos.ajustesAcercaDe),
                children: [
                  const CupertinoListTile(
                    padding: EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                    title: Text(Textos.ajustesVersionApp),
                    additionalInfo: Text(Textos.versionApp),
                  ),
                  CupertinoListTile(
                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                    title: const Text(Textos.ajustesVersionMicroscopio),
                    additionalInfo: Text(switch (version) {
                      AsyncData(:final value) => [
                        if (value.commit != null) value.commit!,
                        if (value.fecha != null) fechaAmigable(value.fecha!.toLocal()),
                      ].join(' · '),
                      AsyncError() => Textos.ajustesSinVersion,
                      _ => Textos.cargando,
                    }),
                    subtitle: version.value?.titulo == null ? null : Text(version.value!.titulo!, maxLines: 2),
                  ),
                  const CupertinoListTile(
                    padding: EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                    title: Text(Textos.laboratorio),
                    additionalInfo: Text(Textos.app),
                  ),
                ],
              ),
              const SizedBox(height: 40),
            ],
          ),
        ],
      ),
    );
  }
}
