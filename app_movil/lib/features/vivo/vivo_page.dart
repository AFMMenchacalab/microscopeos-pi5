import 'package:flutter/cupertino.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../estado/datos.dart';
import '../../estado/preferencias.dart';
import '../../textos.dart';
import '../../ui/barra_control.dart';
import '../../ui/componentes.dart';
import '../../ui/tema.dart';
import 'panel_foco.dart';
import 'panel_foto.dart';
import 'panel_luz.dart';
import 'visor_vivo.dart';

enum _Panel { foco, luz, foto }

/// Video en vivo con los controles de luz, foco y foto debajo.
class VivoPage extends ConsumerStatefulWidget {
  const VivoPage({super.key});

  @override
  ConsumerState<VivoPage> createState() => _VivoPageState();
}

class _VivoPageState extends ConsumerState<VivoPage> {
  late int _camara;
  _Panel _panel = _Panel.foco;

  @override
  void initState() {
    super.initState();
    _camara = ref.read(recordarProvider).entero('vivo_camara', 0).clamp(0, 1);
  }

  void _elegirCamara(int c) {
    setState(() => _camara = c);
    ref.read(recordarProvider).guardarEntero('vivo_camara', c);
  }

  Future<void> _pantallaCompleta() async {
    await SystemChrome.setEnabledSystemUIMode(SystemUiMode.immersiveSticky);
    if (!mounted) return;
    await Navigator.of(
      context,
      rootNavigator: true,
    ).push(CupertinoPageRoute<void>(fullscreenDialog: true, builder: (_) => _PantallaCompleta(camara: _camara)));
    await SystemChrome.setEnabledSystemUIMode(SystemUiMode.edgeToEdge);
  }

  @override
  Widget build(BuildContext context) {
    final sinConexion = ref.watch(estadoProvider.select((e) => e.hasError));
    return CupertinoPageScaffold(
      navigationBar: CupertinoNavigationBar(
        middle: SizedBox(
          width: 260,
          child: CupertinoSlidingSegmentedControl<int>(
            groupValue: _camara,
            onValueChanged: (v) {
              if (v == null) return;
              HapticFeedback.selectionClick();
              _elegirCamara(v);
            },
            children: {
              0: Padding(padding: const EdgeInsets.symmetric(vertical: 6), child: Text(Textos.camaraN(0))),
              1: Padding(padding: const EdgeInsets.symmetric(vertical: 6), child: Text(Textos.camaraN(1))),
            },
          ),
        ),
      ),
      child: SafeArea(
        bottom: false,
        child: CustomScrollView(
          slivers: [
            SliverPadding(
              padding: const EdgeInsets.fromLTRB(Medidas.margen, 10, Medidas.margen, 0),
              sliver: SliverList.list(
                children: [
                  if (sinConexion) ...[
                    const Franja(texto: Textos.sinConexion, icono: CupertinoIcons.wifi_slash, color: Colores.mal),
                    const SizedBox(height: 10),
                  ],
                  AspectRatio(
                    aspectRatio: 4 / 3,
                    child: VisorVivo(camara: _camara, alPantallaCompleta: _pantallaCompleta),
                  ),
                  const SizedBox(height: 12),
                  const BarraControl(),
                  const SizedBox(height: 12),
                  Segmentos<_Panel>(
                    valor: _panel,
                    opciones: const {
                      _Panel.foco: Textos.panelFoco,
                      _Panel.luz: Textos.panelLuz,
                      _Panel.foto: Textos.panelFoto,
                    },
                    alCambiar: (p) => setState(() => _panel = p),
                  ),
                  const SizedBox(height: 14),
                  switch (_panel) {
                    _Panel.foco => PanelFoco(camara: _camara),
                    _Panel.luz => PanelLuz(camara: _camara),
                    _Panel.foto => PanelFoto(camara: _camara),
                  },
                  SizedBox(height: 40 + MediaQuery.paddingOf(context).bottom),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Solo el video, sobre negro, con zoom. Gira con el teléfono.
class _PantallaCompleta extends StatelessWidget {
  const _PantallaCompleta({required this.camara});
  final int camara;

  @override
  Widget build(BuildContext context) {
    return CupertinoPageScaffold(
      backgroundColor: CupertinoColors.black,
      child: Stack(
        children: [
          Positioned.fill(child: VisorVivo(camara: camara, redondeado: false)),
          SafeArea(
            child: Align(
              alignment: Alignment.topRight,
              child: Padding(
                padding: const EdgeInsets.all(8),
                child: CupertinoButton(
                  padding: const EdgeInsets.symmetric(horizontal: 16),
                  minimumSize: const Size(Medidas.toque, 44),
                  color: const Color(0x99000000),
                  borderRadius: BorderRadius.circular(22),
                  onPressed: () => Navigator.of(context).pop(),
                  child: const Text(
                    Textos.listo,
                    style: TextStyle(color: CupertinoColors.white, fontWeight: FontWeight.w600),
                  ),
                ),
              ),
            ),
          ),
          SafeArea(
            child: Align(
              alignment: Alignment.topLeft,
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 52, 16, 16),
                child: Text(
                  Textos.camaraN(camara),
                  style: const TextStyle(color: CupertinoColors.white, fontSize: 15, fontWeight: FontWeight.w600),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
