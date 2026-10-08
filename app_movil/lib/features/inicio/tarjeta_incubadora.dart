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

/// Temperatura y CO2 en vivo (SSE). Verde/naranja/rojo con las mismas
/// tolerancias que la web. Al tocarla se abre la hoja para cambiarlos.
class TarjetaIncubadora extends ConsumerWidget {
  const TarjetaIncubadora({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final lectura = ref.watch(incubadoraProvider);
    final l = lectura.value;

    Widget cuerpo;
    if (l == null && lectura.hasError) {
      cuerpo = _sinDatos(context, Textos.incubadoraSinDatos, lectura.error.toString());
    } else if (l == null) {
      cuerpo = const Padding(
        padding: EdgeInsets.all(24),
        child: Center(child: CupertinoActivityIndicator()),
      );
    } else if (!l.conectada || l.temperatura == null) {
      cuerpo = _sinDatos(context, Textos.incubadoraSinConexion, l.error);
    } else {
      cuerpo = Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Metrica(
                  titulo: Textos.temperatura,
                  valor: l.temperatura!.toStringAsFixed(1),
                  unidad: '°C',
                  semaforo: l.semaforoTemperatura,
                  detalle: l.temperaturaPedida == null
                      ? null
                      : Textos.pedido('${l.temperaturaPedida!.toStringAsFixed(1)} °C'),
                ),
              ),
              if (l.hayCo2)
                Expanded(
                  child: Metrica(
                    titulo: Textos.co2,
                    valor: l.co2Pct!.toStringAsFixed(1),
                    unidad: '%',
                    semaforo: l.semaforoCo2,
                    detalle: l.co2PedidoPct == null ? null : Textos.pedido('${l.co2PedidoPct!.toStringAsFixed(1)} %'),
                  ),
                ),
            ],
          ),
          if (l.humedad != null || l.valvulaAbierta != null) ...[
            const SizedBox(height: 12),
            Text(
              [
                if (l.humedad != null) '${Textos.humedad} ${l.humedad!.round()} %',
                if (l.valvulaAbierta != null) l.valvulaAbierta! ? Textos.valvulaAbierta : Textos.valvulaCerrada,
              ].join(' · '),
              style: Estilos.nota(context),
            ),
          ],
        ],
      );
    }

    final puedeCambiar = l != null && l.conectada;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        TituloSeccion(
          Textos.incubadora,
          icono: CupertinoIcons.thermometer,
          extremo: puedeCambiar
              ? CupertinoButton(
                  padding: EdgeInsets.zero,
                  minimumSize: const Size(44, 30),
                  onPressed: () => abrirHojaIncubadora(context, l),
                  child: const Text(Textos.incubadoraCambiar, style: TextStyle(fontSize: 15)),
                )
              : null,
        ),
        Tarjeta(alTocar: puedeCambiar ? () => abrirHojaIncubadora(context, l) : null, child: cuerpo),
      ],
    );
  }

  Widget _sinDatos(BuildContext context, String titulo, String? detalle) => Row(
    children: [
      const PuntoSemaforo(Semaforo.sinDato, tamano: 12),
      const SizedBox(width: 10),
      Expanded(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(titulo, style: Estilos.titular(context)),
            if (detalle != null && detalle.isNotEmpty) Text(detalle, style: Estilos.nota(context)),
          ],
        ),
      ),
    ],
  );
}

Future<void> abrirHojaIncubadora(BuildContext context, LecturaIncubadora lectura) => showCupertinoModalPopup<void>(
  context: context,
  builder: (_) => HojaIncubadora(lectura: lectura),
);

/// Hoja de abajo para pedir otra temperatura o CO2. Cada valor se aplica
/// con su botón (es un cambio que importa: no se manda al deslizar).
class HojaIncubadora extends ConsumerStatefulWidget {
  const HojaIncubadora({super.key, required this.lectura});
  final LecturaIncubadora lectura;

  @override
  ConsumerState<HojaIncubadora> createState() => _HojaIncubadoraState();
}

class _HojaIncubadoraState extends ConsumerState<HojaIncubadora> {
  late double _temp = (widget.lectura.temperaturaPedida ?? 37).clamp(20, 80);
  // En % (la interfaz), se manda en ppm.
  late double _co2 = ((widget.lectura.co2PedidoPpm ?? 40000) / 10000).clamp(0.1, 10);
  bool _enviandoT = false, _enviandoC = false;

  Future<void> _aplicarTemp() async {
    setState(() => _enviandoT = true);
    final v = (_temp * 2).round() / 2;
    await ejecutar(
      context,
      ref,
      (c) => c.fijarTemperatura(v),
      exito: Textos.incubadoraTemperaturaOk(v.toStringAsFixed(1)),
    );
    if (mounted) setState(() => _enviandoT = false);
  }

  Future<void> _aplicarCo2() async {
    setState(() => _enviandoC = true);
    final pct = (_co2 * 10).round() / 10;
    await ejecutar(context, ref, (c) => c.fijarCo2(pct * 10000), exito: Textos.incubadoraCo2Ok(numeroCorto(pct)));
    if (mounted) setState(() => _enviandoC = false);
  }

  @override
  Widget build(BuildContext context) {
    final fondo = CupertinoDynamicColor.resolve(CupertinoColors.systemGroupedBackground, context);
    return Container(
      decoration: BoxDecoration(
        color: fondo,
        borderRadius: const BorderRadius.vertical(top: Radius.circular(24)),
      ),
      padding: EdgeInsets.fromLTRB(20, 10, 20, 20 + MediaQuery.paddingOf(context).bottom),
      child: SafeArea(
        top: false,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Center(
              child: Container(
                width: 40,
                height: 5,
                decoration: BoxDecoration(
                  color: CupertinoDynamicColor.resolve(Colores.textoTerciario, context),
                  borderRadius: BorderRadius.circular(3),
                ),
              ),
            ),
            const SizedBox(height: 14),
            Text(Textos.incubadora, style: Estilos.titulo(context), textAlign: TextAlign.center),
            const SizedBox(height: 18),
            _Ajuste(
              titulo: Textos.incubadoraTemperaturaPedida,
              valor: '${_temp.toStringAsFixed(1)} °C',
              min: 20,
              max: 45,
              paso: 0.5,
              actual: _temp,
              alCambiar: (v) => setState(() => _temp = v),
              enviando: _enviandoT,
              alAplicar: _aplicarTemp,
            ),
            const SizedBox(height: 16),
            if (widget.lectura.hayCo2 || widget.lectura.co2PedidoPpm != null)
              _Ajuste(
                titulo: Textos.incubadoraCo2Pedido,
                valor: '${_co2.toStringAsFixed(1)} %',
                min: 0.1,
                max: 10,
                paso: 0.1,
                actual: _co2,
                alCambiar: (v) => setState(() => _co2 = v),
                enviando: _enviandoC,
                alAplicar: _aplicarCo2,
              ),
          ],
        ),
      ),
    );
  }
}

class _Ajuste extends StatelessWidget {
  const _Ajuste({
    required this.titulo,
    required this.valor,
    required this.min,
    required this.max,
    required this.paso,
    required this.actual,
    required this.alCambiar,
    required this.enviando,
    required this.alAplicar,
  });

  final String titulo, valor;
  final double min, max, paso, actual;
  final ValueChanged<double> alCambiar;
  final bool enviando;
  final VoidCallback alAplicar;

  void _sumar(double d) {
    HapticFeedback.selectionClick();
    alCambiar((actual + d).clamp(min, max));
  }

  @override
  Widget build(BuildContext context) {
    return Tarjeta(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(titulo, style: Estilos.encabezado(context)),
          const SizedBox(height: 8),
          Row(
            children: [
              _BotonRedondo(icono: CupertinoIcons.minus, alTocar: () => _sumar(-paso)),
              Expanded(
                child: Text(valor, textAlign: TextAlign.center, style: Estilos.metrica(context, tamano: 36)),
              ),
              _BotonRedondo(icono: CupertinoIcons.plus, alTocar: () => _sumar(paso)),
            ],
          ),
          CupertinoSlider(
            value: actual.clamp(min, max),
            min: min,
            max: max,
            divisions: ((max - min) / paso).round(),
            onChanged: alCambiar,
          ),
          const SizedBox(height: 8),
          BotonGrande(texto: Textos.incubadoraAplicar, cargando: enviando, alTocar: alAplicar),
        ],
      ),
    );
  }
}

class _BotonRedondo extends StatelessWidget {
  const _BotonRedondo({required this.icono, required this.alTocar});
  final IconData icono;
  final VoidCallback alTocar;

  @override
  Widget build(BuildContext context) => CupertinoButton(
    padding: EdgeInsets.zero,
    minimumSize: const Size(Medidas.toque, Medidas.toque),
    color: CupertinoDynamicColor.resolve(Colores.relleno, context),
    borderRadius: BorderRadius.circular(Medidas.toque),
    onPressed: alTocar,
    child: Icon(icono, size: 26, color: CupertinoDynamicColor.resolve(Colores.texto, context)),
  );
}
