import 'dart:async';

import 'package:flutter/cupertino.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../api/modelos.dart';
import '../../estado/datos.dart';
import '../../estado/preferencias.dart';
import '../../textos.dart';
import '../../ui/acciones.dart';
import '../../ui/componentes.dart';
import '../../ui/tema.dart';

const nombresLuz = {
  ModoLuz.full: Textos.luzCampoClaro,
  ModoLuz.left: Textos.luzRelieveIzq,
  ModoLuz.right: Textos.luzRelieveDer,
  ModoLuz.top: Textos.luzRelieveArr,
  ModoLuz.bottom: Textos.luzRelieveAbj,
  ModoLuz.ring: Textos.luzFondoNegro,
  ModoLuz.rheinberg: Textos.luzColores,
};

/// Encender/apagar, tipo de luz y brillo.
///
/// Lo que la persona mueve se ve al instante (estado local) y se manda al
/// microscopio: como mucho un pedido cada 300 ms mientras se arrastra el
/// brillo, uno al soltar, y nunca dos a la vez (si hay uno en vuelo, se
/// manda el último valor apenas termina, como enviarGrupoLuz() en la web).
/// Lo que viene de /light/estado (otra persona, una foto que tocó la luz)
/// se respeta, salvo en los 3 s después de un cambio propio.
class PanelLuz extends ConsumerStatefulWidget {
  const PanelLuz({super.key, required this.camara, this.intervaloMinimo = const Duration(milliseconds: 300)});

  final int camara;
  final Duration intervaloMinimo;

  @override
  ConsumerState<PanelLuz> createState() => _PanelLuzState();
}

class _PanelLuzState extends ConsumerState<PanelLuz> {
  MatrizLuz? _local;
  DateTime _ultimaEdicion = DateTime.fromMillisecondsSinceEpoch(0);
  DateTime _ultimoEnvio = DateTime.fromMillisecondsSinceEpoch(0);
  bool _enVuelo = false;
  bool _pendiente = false;
  Timer? _programado;

  bool get _lasDos => ref.read(recordarProvider).logico('luz_las_dos', true);

  @override
  void didUpdateWidget(PanelLuz oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.camara != widget.camara) _local = null;
  }

  @override
  void dispose() {
    _programado?.cancel();
    super.dispose();
  }

  void _cambiar(MatrizLuz nueva, {bool yaMismo = false}) {
    setState(() => _local = nueva);
    _ultimaEdicion = DateTime.now();
    if (yaMismo) {
      _programado?.cancel();
      _programado = null;
      _enviar();
      return;
    }
    // Arrastrando: espaciar los pedidos.
    final falta = widget.intervaloMinimo - DateTime.now().difference(_ultimoEnvio);
    if (falta <= Duration.zero) {
      _enviar();
    } else {
      _programado ??= Timer(falta, () {
        _programado = null;
        _enviar();
      });
    }
  }

  Future<void> _enviar() async {
    final m = _local;
    if (m == null) return;
    if (_enVuelo) {
      _pendiente = true;
      return;
    }
    _enVuelo = true;
    _ultimoEnvio = DateTime.now();
    final cams = _lasDos ? const [0, 1] : [widget.camara];
    await ejecutar(context, ref, (c) => m.encendida ? c.fijarLuz(m.modo, m.porcentaje ?? 60, cams) : c.apagarLuz(cams));
    _enVuelo = false;
    _ultimaEdicion = DateTime.now();
    if (_pendiente && mounted) {
      _pendiente = false;
      _enviar();
    }
  }

  @override
  Widget build(BuildContext context) {
    final bloqueado = ref.watch(estadoProvider.select((e) => e.value?.bloqueaControles ?? false));
    final remoto = ref.watch(luzProvider).value?[widget.camara];
    // Lo del microscopio manda, salvo justo después de un cambio propio.
    final editando = _enVuelo || DateTime.now().difference(_ultimaEdicion) < const Duration(seconds: 3);
    if (remoto != null && (!editando || _local == null)) _local = remoto;
    final m = _local;
    final lasDos = ref.watch(recordarProvider).logico('luz_las_dos', true);

    if (m == null) {
      return const Padding(
        padding: EdgeInsets.all(24),
        child: Center(child: CupertinoActivityIndicator()),
      );
    }
    final habilitado = !bloqueado;
    final pct = (m.porcentaje ?? 60).clamp(0, 100);

    return IgnorePointer(
      ignoring: !habilitado,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (bloqueado) ...[
            const Franja(texto: Textos.bloqueadoTimelapse, icono: CupertinoIcons.lock_fill),
            const SizedBox(height: Medidas.espacio),
          ],
          Opacity(
            opacity: habilitado ? 1 : 0.45,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Tarjeta(
                  padding: const EdgeInsets.fromLTRB(16, 6, 12, 6),
                  child: Row(
                    children: [
                      Icon(
                        m.encendida ? CupertinoIcons.lightbulb_fill : CupertinoIcons.lightbulb,
                        color: m.encendida ? CupertinoColors.systemYellow : Colores.textoSecundario,
                        size: 26,
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Text(
                          m.encendida ? Textos.luzEncendida : Textos.luzApagada,
                          style: Estilos.titular(context),
                        ),
                      ),
                      Transform.scale(
                        scale: 1.15,
                        child: CupertinoSwitch(
                          value: m.encendida,
                          onChanged: (v) {
                            HapticFeedback.mediumImpact();
                            _cambiar(m.copiar(encendida: v), yaMismo: true);
                          },
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: Medidas.espacio),
                Text(Textos.luzTipo, style: Estilos.encabezado(context)),
                const SizedBox(height: 8),
                _RejillaModos(
                  actual: m.modo,
                  alElegir: (modo) => _cambiar(m.copiar(modo: modo, encendida: true), yaMismo: true),
                ),
                const SizedBox(height: Medidas.espacio),
                Tarjeta(
                  padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Row(
                        children: [
                          Text(Textos.luzBrillo, style: Estilos.titular(context)),
                          const Spacer(),
                          Text('$pct %', style: Estilos.metrica(context, tamano: 22)),
                        ],
                      ),
                      Row(
                        children: [
                          const Icon(CupertinoIcons.sun_min, color: Colores.textoSecundario),
                          Expanded(
                            child: CupertinoSlider(
                              value: pct.toDouble(),
                              min: 0,
                              max: 100,
                              divisions: 100,
                              onChanged: (v) => _cambiar(m.copiar(porcentaje: v.round(), encendida: true)),
                              onChangeEnd: (v) =>
                                  _cambiar(m.copiar(porcentaje: v.round(), encendida: true), yaMismo: true),
                            ),
                          ),
                          const Icon(CupertinoIcons.sun_max_fill, color: Colores.textoSecundario),
                        ],
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: Medidas.espacio),
                Tarjeta(
                  padding: const EdgeInsets.fromLTRB(16, 6, 12, 6),
                  child: Row(
                    children: [
                      Expanded(child: Text(Textos.luzLasDos, style: Estilos.cuerpo(context))),
                      CupertinoSwitch(
                        value: lasDos,
                        onChanged: (v) async {
                          await ref.read(recordarProvider).guardarLogico('luz_las_dos', v);
                          ref.invalidate(recordarProvider);
                          if (v) _cambiar(m, yaMismo: true);
                        },
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _RejillaModos extends StatelessWidget {
  const _RejillaModos({required this.actual, required this.alElegir});
  final ModoLuz actual;
  final ValueChanged<ModoLuz> alElegir;

  @override
  Widget build(BuildContext context) {
    final modos = ModoLuz.values;
    return LayoutBuilder(
      builder: (context, c) {
        final columnas = c.maxWidth > 520 ? 4 : 2;
        final ancho = (c.maxWidth - 8 * (columnas - 1)) / columnas;
        return Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            for (final m in modos)
              SizedBox(
                width: ancho,
                child: _Opcion(
                  texto: nombresLuz[m]!,
                  elegida: m == actual,
                  alTocar: () {
                    HapticFeedback.selectionClick();
                    alElegir(m);
                  },
                ),
              ),
          ],
        );
      },
    );
  }
}

class _Opcion extends StatelessWidget {
  const _Opcion({required this.texto, required this.elegida, required this.alTocar});
  final String texto;
  final bool elegida;
  final VoidCallback alTocar;

  @override
  Widget build(BuildContext context) {
    final acento = CupertinoDynamicColor.resolve(Colores.acento, context);
    return Semantics(
      selected: elegida,
      button: true,
      child: GestureDetector(
        onTap: alTocar,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 150),
          height: Medidas.toque,
          alignment: Alignment.center,
          padding: const EdgeInsets.symmetric(horizontal: 8),
          decoration: BoxDecoration(
            color: elegida ? acento.withValues(alpha: 0.18) : CupertinoDynamicColor.resolve(Colores.tarjeta, context),
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: elegida ? acento : const Color(0x00000000), width: 2),
          ),
          child: Text(
            texto,
            textAlign: TextAlign.center,
            maxLines: 2,
            style: Estilos.base(context).copyWith(
              fontSize: 15,
              fontWeight: elegida ? FontWeight.w600 : FontWeight.w500,
              color: elegida ? acento : null,
            ),
          ),
        ),
      ),
    );
  }
}
