import 'package:flutter/cupertino.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../api/modelos.dart';
import '../../estado/datos.dart';
import '../../textos.dart';
import '../../ui/acciones.dart';
import '../../ui/componentes.dart';
import '../../ui/formato.dart';
import '../../ui/tema.dart';
import 'estimacion.dart';

/// Formulario para empezar un timelapse: lo mínimo de TimelapseReq. Lo
/// demás (USB, conteo, PC, NAS, opciones del relieve) queda como viene
/// por defecto en el servidor, o desde la web.
class NuevoTimelapsePage extends ConsumerStatefulWidget {
  const NuevoTimelapsePage({super.key});

  static Future<void> abrir(BuildContext context) => Navigator.of(
    context,
    rootNavigator: true,
  ).push(CupertinoPageRoute<void>(fullscreenDialog: true, builder: (_) => const NuevoTimelapsePage()));

  @override
  ConsumerState<NuevoTimelapsePage> createState() => _NuevoTimelapsePageState();
}

/// Intervalos que se ofrecen, en segundos (la web acepta desde 30 s).
const intervalosTimelapse = [30, 60, 120, 300, 600, 900, 1800, 3600];

class _NuevoTimelapsePageState extends ConsumerState<NuevoTimelapsePage> {
  final _nombre = TextEditingController();
  ModoFoto _modo = ModoFoto.blanco;
  int _intervalo = 300; // la web empieza en 5 min
  bool _hastaFecha = false;
  double _horas = 1;
  late DateTime _fin = _finPorDefecto();
  final Set<int> _camaras = {0, 1};
  bool _autofoco = true;
  bool _enviando = false;

  /// Como la web: dentro de 48 h, en punto.
  static DateTime _finPorDefecto() {
    final d = DateTime.now().add(const Duration(hours: 48));
    return DateTime(d.year, d.month, d.day, d.hour);
  }

  @override
  void dispose() {
    _nombre.dispose();
    super.dispose();
  }

  int get _duracionS => _hastaFecha ? _fin.difference(DateTime.now()).inSeconds : (_horas * 3600).round();

  Future<void> _iniciar(Estimacion est) async {
    if (_camaras.isEmpty) {
      await confirmar(context, titulo: Textos.nuevoSinCamaras, accion: Textos.aceptar);
      return;
    }
    if (_duracionS < _intervalo || _duracionS < 60) {
      await confirmar(context, titulo: Textos.nuevoFinPasado, accion: Textos.aceptar);
      return;
    }
    final si = await confirmar(
      context,
      titulo: Textos.nuevoConfirmar,
      detalle: _resumen(est),
      accion: Textos.nuevoIniciar,
    );
    if (!si || !mounted) return;
    setState(() => _enviando = true);
    final pedido = PedidoTimelapse(
      modo: _modo,
      intervaloS: _intervalo,
      duracionS: _hastaFecha ? null : _duracionS,
      fin: _hastaFecha ? _fin : null,
      nombre: _nombre.text.trim(),
      camaras: _camaras.toList()..sort(),
      autofoco: _autofoco,
    );
    final ok = await ejecutar(context, ref, (c) async {
      await c.iniciarTimelapse(pedido);
      return true;
    }, exito: Textos.nuevoIniciado);
    if (!mounted) return;
    setState(() => _enviando = false);
    if (ok == true) {
      ref.invalidate(estadoProvider);
      Navigator.of(context).pop();
    }
  }

  String _resumen(Estimacion est) {
    final partes = [
      Textos.nuevoResumen(est.fotosPorCamara, cadaTexto(_intervalo), duracionTexto(_duracionS)),
      if (est.libreBytes != null)
        est.alcanza
            ? Textos.nuevoEspacio(bytesTexto(est.bytes), bytesTexto(est.libreBytes!))
            : Textos.nuevoNoAlcanza(bytesTexto(est.bytes), bytesTexto(est.libreBytes!)),
      Textos.nuevoSigueSolo,
    ];
    return partes.join(' ');
  }

  Future<void> _elegirFin() async {
    var elegido = _fin;
    await showCupertinoModalPopup<void>(
      context: context,
      builder: (c) => Container(
        height: 320,
        color: CupertinoDynamicColor.resolve(Colores.tarjeta, c),
        child: SafeArea(
          top: false,
          child: Column(
            children: [
              Align(
                alignment: Alignment.centerRight,
                child: CupertinoButton(
                  onPressed: () => Navigator.pop(c),
                  child: const Text(Textos.listo, style: TextStyle(fontWeight: FontWeight.w600)),
                ),
              ),
              Expanded(
                child: CupertinoDatePicker(
                  initialDateTime: _fin,
                  minimumDate: DateTime.now().add(const Duration(minutes: 5)),
                  maximumDate: DateTime.now().add(const Duration(days: 60)),
                  use24hFormat: true,
                  minuteInterval: 5,
                  onDateTimeChanged: (d) => elegido = d,
                ),
              ),
            ],
          ),
        ),
      ),
    );
    setState(() => _fin = elegido);
  }

  @override
  Widget build(BuildContext context) {
    final bloqueado = ref.watch(estadoProvider.select((e) => e.value?.bloqueaNuevoTimelapse ?? false));
    final libre = ref.watch(experimentosProvider).value?.libreBytes;
    final est = estimarTimelapse(
      modo: _modo,
      intervaloS: _intervalo,
      duracionS: _duracionS,
      camaras: _camaras.length,
      libreBytes: libre,
    );

    return CupertinoPageScaffold(
      navigationBar: CupertinoNavigationBar(
        middle: const Text(Textos.nuevoTitulo),
        leading: CupertinoButton(
          padding: EdgeInsets.zero,
          onPressed: () => Navigator.of(context).pop(),
          child: const Text(Textos.cancelar),
        ),
      ),
      child: SafeArea(
        child: ListView(
          padding: const EdgeInsets.only(bottom: 40),
          children: [
            if (bloqueado)
              const Padding(
                padding: EdgeInsets.fromLTRB(16, 16, 16, 0),
                child: Franja(texto: Textos.nuevoBloqueado, icono: CupertinoIcons.lock_fill),
              ),
            CupertinoListSection.insetGrouped(
              header: const Text(Textos.nuevoNombre),
              children: [
                CupertinoTextField.borderless(
                  controller: _nombre,
                  placeholder: Textos.nuevoNombreEjemplo,
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 18),
                  textCapitalization: TextCapitalization.sentences,
                  maxLength: 60,
                ),
              ],
            ),
            CupertinoListSection.insetGrouped(
              header: const Text(Textos.nuevoTipo),
              children: [
                for (final (m, t) in const [
                  (ModoFoto.blanco, Textos.nuevoTipoNormal),
                  (ModoFoto.dpc, Textos.nuevoTipoRelieve),
                  (ModoFoto.oscuro, Textos.nuevoTipoFondo),
                  (ModoFoto.rheinberg, Textos.nuevoTipoColores),
                ])
                  CupertinoListTile(
                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
                    title: Text(t),
                    trailing: _modo == m ? const Icon(CupertinoIcons.checkmark_alt, color: Colores.acento) : null,
                    onTap: () {
                      HapticFeedback.selectionClick();
                      setState(() => _modo = m);
                    },
                  ),
              ],
            ),
            CupertinoListSection.insetGrouped(
              header: const Text(Textos.nuevoCada),
              children: [
                _FilaPasos(
                  valor: cadaTexto(_intervalo),
                  alRestar: _intervalo > intervalosTimelapse.first
                      ? () => setState(() => _intervalo = intervalosTimelapse.lastWhere((v) => v < _intervalo))
                      : null,
                  alSumar: _intervalo < intervalosTimelapse.last
                      ? () => setState(() => _intervalo = intervalosTimelapse.firstWhere((v) => v > _intervalo))
                      : null,
                ),
              ],
            ),
            CupertinoListSection.insetGrouped(
              header: const Text(Textos.nuevoHasta),
              children: [
                Padding(
                  padding: const EdgeInsets.all(12),
                  child: Segmentos<bool>(
                    valor: _hastaFecha,
                    opciones: const {false: Textos.nuevoDurante, true: Textos.nuevoHastaFecha},
                    alCambiar: (v) => setState(() => _hastaFecha = v),
                  ),
                ),
                if (!_hastaFecha)
                  _FilaPasos(
                    valor: '${numeroCorto(_horas)} h',
                    alRestar: _horas > 0.5 ? () => setState(() => _horas -= _horas > 12 ? 6 : 0.5) : null,
                    alSumar: _horas < 24 * 14 ? () => setState(() => _horas += _horas >= 12 ? 6 : 0.5) : null,
                  )
                else
                  CupertinoListTile(
                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
                    title: const Text(Textos.nuevoTermina),
                    additionalInfo: Text(fechaAmigable(_fin)),
                    trailing: const CupertinoListTileChevron(),
                    onTap: _elegirFin,
                  ),
              ],
            ),
            CupertinoListSection.insetGrouped(
              header: const Text(Textos.nuevoCamaras),
              children: [
                for (final cam in const [0, 1])
                  CupertinoListTile(
                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                    title: Text(Textos.camaraN(cam)),
                    trailing: CupertinoSwitch(
                      value: _camaras.contains(cam),
                      onChanged: (v) => setState(() => v ? _camaras.add(cam) : _camaras.remove(cam)),
                    ),
                  ),
              ],
            ),
            CupertinoListSection.insetGrouped(
              footer: const Text(Textos.nuevoAutofocoAyuda),
              children: [
                CupertinoListTile(
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                  title: const Text(Textos.nuevoAutofoco),
                  trailing: CupertinoSwitch(value: _autofoco, onChanged: (v) => setState(() => _autofoco = v)),
                ),
              ],
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 4, 20, 0),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Franja(
                    texto: _resumen(est),
                    icono: est.alcanza ? CupertinoIcons.info_circle_fill : CupertinoIcons.exclamationmark_triangle_fill,
                    color: est.alcanza ? Colores.acento : Colores.mal,
                  ),
                  const SizedBox(height: 16),
                  BotonGrande(
                    texto: Textos.nuevoIniciar,
                    icono: CupertinoIcons.play_fill,
                    alto: Medidas.toqueGrande,
                    cargando: _enviando,
                    alTocar: bloqueado || !est.alcanza ? null : () => _iniciar(est),
                  ),
                  const SizedBox(height: 12),
                  Text(Textos.nuevoMasOpciones, textAlign: TextAlign.center, style: Estilos.nota(context)),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// «− valor +» para elegir con botones grandes en vez de teclear.
class _FilaPasos extends StatelessWidget {
  const _FilaPasos({required this.valor, this.alRestar, this.alSumar});
  final String valor;
  final VoidCallback? alRestar;
  final VoidCallback? alSumar;

  Widget _boton(BuildContext context, IconData icono, VoidCallback? f) => CupertinoButton(
    padding: EdgeInsets.zero,
    minimumSize: const Size(Medidas.toque, Medidas.toque),
    onPressed: f == null
        ? null
        : () {
            HapticFeedback.selectionClick();
            f();
          },
    child: Icon(icono, size: 34),
  );

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
    child: Row(
      children: [
        _boton(context, CupertinoIcons.minus_circle_fill, alRestar),
        Expanded(
          child: Text(valor, textAlign: TextAlign.center, style: Estilos.metrica(context, tamano: 26)),
        ),
        _boton(context, CupertinoIcons.plus_circle_fill, alSumar),
      ],
    ),
  );
}
