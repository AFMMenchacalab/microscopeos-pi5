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
import 'coordinador_vivo.dart';

/// Tomar una foto (o las 4 de un relieve DPC) de esta cámara o de las dos,
/// en el experimento que se nombre (o en «Fotos sueltas» de hoy).
class PanelFoto extends ConsumerStatefulWidget {
  const PanelFoto({super.key, required this.camara});
  final int camara;

  @override
  ConsumerState<PanelFoto> createState() => _PanelFotoState();
}

class _PanelFotoState extends ConsumerState<PanelFoto> {
  late final TextEditingController _nombre;
  late ModoFoto _modo;
  late bool _lasDos;
  bool _tomando = false;

  @override
  void initState() {
    super.initState();
    final r = ref.read(recordarProvider);
    _nombre = TextEditingController(text: r.texto('foto_nombre'));
    _modo = ModoFoto.desdeId(r.texto('foto_modo', 'blanco')) ?? ModoFoto.blanco;
    _lasDos = r.logico('foto_las_dos', false);
  }

  @override
  void dispose() {
    _nombre.dispose();
    super.dispose();
  }

  Future<void> _tomar() async {
    final r = ref.read(recordarProvider);
    r.guardarTexto('foto_nombre', _nombre.text.trim());
    r.guardarTexto('foto_modo', _modo.id);
    r.guardarLogico('foto_las_dos', _lasDos);
    HapticFeedback.heavyImpact();
    setState(() => _tomando = true);
    // La cámara no puede estar en vivo mientras saca las fotos.
    ref.read(pausaVivoProvider.notifier).fijar(true);
    try {
      await ejecutar(
        context,
        ref,
        (c) => c.capturar(cam: _lasDos ? null : widget.camara, modo: _modo, nombre: _nombre.text),
        exitoCon: (res) => Textos.fotoGuardada(res.guardadas.length, res.nombre),
      );
    } finally {
      ref.read(pausaVivoProvider.notifier).fijar(false);
      ref.invalidate(experimentosProvider);
      if (mounted) setState(() => _tomando = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final bloqueado = ref.watch(estadoProvider.select((e) => e.value?.bloqueaFotos ?? false));
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (bloqueado) ...[
          const Franja(texto: Textos.fotoBloqueada, icono: CupertinoIcons.lock_fill),
          const SizedBox(height: Medidas.espacio),
        ],
        CupertinoListSection.insetGrouped(
          margin: EdgeInsets.zero,
          footer: const Text(Textos.fotoNombreAyuda),
          children: [
            CupertinoTextField.borderless(
              controller: _nombre,
              placeholder: Textos.fotoNombreEjemplo,
              prefix: Padding(
                padding: const EdgeInsets.only(left: 16),
                child: Text(Textos.fotoNombre, style: Estilos.cuerpo(context)),
              ),
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 18),
              textCapitalization: TextCapitalization.sentences,
              maxLength: 60,
              textInputAction: TextInputAction.done,
            ),
          ],
        ),
        const SizedBox(height: Medidas.espacio),
        Text(Textos.fotoTipo, style: Estilos.encabezado(context)),
        const SizedBox(height: 6),
        Segmentos<ModoFoto>(
          habilitado: !_tomando,
          valor: _modo,
          opciones: const {
            ModoFoto.blanco: Textos.fotoNormal,
            ModoFoto.dpc: Textos.fotoRelieve,
            ModoFoto.oscuro: Textos.fotoFondoNegro,
          },
          alCambiar: (m) => setState(() => _modo = m),
        ),
        const SizedBox(height: Medidas.espacio),
        Text(Textos.fotoDeQueCamara, style: Estilos.encabezado(context)),
        const SizedBox(height: 6),
        Segmentos<bool>(
          habilitado: !_tomando,
          valor: _lasDos,
          opciones: {false: '${Textos.fotoEsta} (${Textos.camaraN(widget.camara)})', true: Textos.lasDos},
          alCambiar: (v) => setState(() => _lasDos = v),
        ),
        const SizedBox(height: 18),
        Center(
          child: _Disparador(
            key: const ValueKey('disparador'),
            tomando: _tomando,
            alTocar: bloqueado || _tomando ? null : _tomar,
          ),
        ),
        const SizedBox(height: 8),
        Text(
          _tomando ? Textos.fotoTomando : Textos.fotoTomar,
          textAlign: TextAlign.center,
          style: Estilos.secundario(context),
        ),
      ],
    );
  }
}

/// El botón de disparo de la app Cámara: un círculo blanco con anillo.
class _Disparador extends StatelessWidget {
  const _Disparador({super.key, required this.tomando, required this.alTocar});
  final bool tomando;
  final VoidCallback? alTocar;

  @override
  Widget build(BuildContext context) {
    final anillo = CupertinoDynamicColor.resolve(Colores.texto, context);
    return Semantics(
      button: true,
      enabled: alTocar != null,
      label: Textos.fotoTomar,
      child: GestureDetector(
        onTap: alTocar,
        child: Opacity(
          opacity: alTocar == null && !tomando ? 0.35 : 1,
          child: Container(
            width: 84,
            height: 84,
            padding: const EdgeInsets.all(6),
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              border: Border.all(color: anillo, width: 4),
            ),
            child: AnimatedContainer(
              duration: const Duration(milliseconds: 150),
              decoration: BoxDecoration(shape: BoxShape.circle, color: tomando ? CupertinoColors.systemGrey : anillo),
              child: tomando ? const CupertinoActivityIndicator(color: CupertinoColors.white) : null,
            ),
          ),
        ),
      ),
    );
  }
}
