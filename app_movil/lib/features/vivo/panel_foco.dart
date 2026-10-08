import 'dart:async';

import 'package:flutter/cupertino.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../api/cliente_api.dart';
import '../../api/errores.dart';
import '../../api/modelos.dart';
import '../../estado/avisos.dart';
import '../../estado/conexion.dart';
import '../../estado/datos.dart';
import '../../textos.dart';
import '../../ui/acciones.dart';
import '../../ui/componentes.dart';
import '../../ui/formato.dart';
import '../../ui/tema.dart';
import 'control_jog.dart';

/// Tamaños de paso, igual que PASOS_Z de la web. Cada uno trae la
/// resolución de micropasos con la que se mueve a mano, que fija también
/// la velocidad al mantener apretado: más fino = más lento.
class PasoFoco {
  const PasoFoco(this.um, this.microsteps, this.nombre);
  final double um;
  final int microsteps;
  final String nombre;
}

const pasosFoco = [
  PasoFoco(1, 64, Textos.focoFino),
  PasoFoco(5, 16, Textos.focoMedio),
  PasoFoco(25, 8, Textos.focoGrueso),
];

/// El paso que corresponde a la resolución que tiene el motor ahora.
int pasoDeMicrosteps(int? ms) {
  if (ms == null) return 1;
  final i = pasosFoco.indexWhere((p) => p.microsteps == ms);
  if (i >= 0) return i;
  return ms > 16 ? 0 : (ms < 16 ? 2 : 1);
}

/// Manda las órdenes del jog con el cliente y avisa la posición nueva.
class _OrdenesCliente implements OrdenesJog {
  _OrdenesCliente(this._cliente, this._alMoverse);
  final ClienteMicroscopio? Function() _cliente;
  final void Function(double posicionUm) _alMoverse;

  @override
  Future<void> jog(int motor, int direccion) async {
    final c = _cliente();
    if (c == null) throw const ErrorDeRed();
    final p = await c.jog(motor, direccion);
    if (p != null) _alMoverse(p);
  }

  @override
  Future<void> pararJog(int motor) async {
    final c = _cliente();
    if (c == null) return;
    await c.pararJog(motor);
  }
}

class PanelFoco extends ConsumerStatefulWidget {
  const PanelFoco({super.key, required this.camara, this.ordenes});

  final int camara;

  /// Para las pruebas: reemplaza al cliente en el jog.
  final OrdenesJog? ordenes;

  @override
  ConsumerState<PanelFoco> createState() => PanelFocoState();
}

class PanelFocoState extends ConsumerState<PanelFoco> {
  late final ControlJog jog;
  late final AppLifecycleListener _ciclo;
  double? _posicionLocal;
  int? _paso;
  bool _autofoco = false;
  bool _pasoEnCurso = false;

  @override
  void initState() {
    super.initState();
    jog = ControlJog(
      widget.ordenes ?? _OrdenesCliente(() => ref.read(clienteProvider), _alMoverse),
      alFallar: (e) {
        if (!mounted) return;
        ref.read(avisosProvider.notifier).error(e is ErrorApi ? e.mensaje : '$e');
        if (e is ErrorSinControl) ref.invalidate(controlProvider);
      },
    );
    // A segundo plano (o cualquier cosa que no sea primer plano): stop.
    _ciclo = AppLifecycleListener(
      onStateChange: (s) {
        if (s != AppLifecycleState.resumed) jog.detener();
      },
    );
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    // Otra pestaña o pantalla encima: stop.
    if (!TickerMode.valuesOf(context).enabled) jog.detener();
  }

  @override
  void didUpdateWidget(PanelFoco oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.camara != widget.camara) {
      jog.detener();
      _posicionLocal = null;
      _paso = null;
    }
  }

  @override
  void dispose() {
    // Al salir de la pantalla: stop.
    jog.detener();
    _ciclo.dispose();
    super.dispose();
  }

  void _alMoverse(double posicionUm) {
    if (mounted) setState(() => _posicionLocal = posicionUm);
  }

  Future<void> _darPaso(int direccion) async {
    if (_pasoEnCurso) return; // no apilar pasos con la red lenta
    _pasoEnCurso = true;
    HapticFeedback.selectionClick();
    final paso = pasosFoco[_pasoActual(ref.read(focoProvider).value?[widget.camara])];
    final p = await ejecutar(context, ref, (c) => c.moverFoco(widget.camara, direccion, paso.um));
    _pasoEnCurso = false;
    if (p != null) _alMoverse(p);
  }

  void _iniciarJog(int direccion) {
    HapticFeedback.mediumImpact();
    jog.iniciar(widget.camara, direccion);
    setState(() {});
  }

  void _detenerJog() {
    if (!jog.activo) return;
    jog.detener();
    HapticFeedback.lightImpact();
    if (mounted) setState(() {});
    ref.invalidate(focoProvider);
  }

  int _pasoActual(MotorFoco? motor) => _paso ?? pasoDeMicrosteps(motor?.microsteps);

  Future<void> _elegirPaso(int i) async {
    setState(() => _paso = i);
    await ejecutar(context, ref, (c) => c.configurarFoco(widget.camara, pasosFoco[i].microsteps));
  }

  Future<void> _autoenfocar() async {
    jog.detener();
    setState(() => _autofoco = true);
    final r = await ejecutar(context, ref, (c) => c.autofoco(widget.camara));
    if (!mounted) return;
    setState(() => _autofoco = false);
    ref.invalidate(focoProvider);
    if (r == null) return;
    final avisos = ref.read(avisosProvider.notifier);
    if (!r.encontrado) {
      avisos.error(Textos.focoNoEncontrado(((r.rangoUm ?? 40) / 2).toStringAsFixed(0)));
      return;
    }
    final um = r.desplazamientoUm ?? 0;
    final mov = um.abs() < 0.05
        ? Textos.focoNoHizoFalta
        : (um < 0 ? Textos.focoSubio(um.abs().toStringAsFixed(1)) : Textos.focoBajo(um.abs().toStringAsFixed(1)));
    avisos.exito(Textos.focoEnfocado(mov) + (r.aviso != null ? ' · ${r.aviso}' : ''));
  }

  @override
  Widget build(BuildContext context) {
    // Timelapse que arranca, o se pierde la conexión: stop.
    ref.listen(estadoProvider, (_, e) {
      if (e.hasError || (e.value?.bloqueaControles ?? false)) jog.detener();
    });
    ref.listen(focoProvider, (_, _) {
      if (!jog.activo && _posicionLocal != null) setState(() => _posicionLocal = null);
    });
    final bloqueado = ref.watch(estadoProvider.select((e) => e.value?.bloqueaControles ?? false));
    final motores = ref.watch(focoProvider);
    final motor = motores.value?[widget.camara];
    final sinMotor = motores.hasValue && motor == null;
    final altura = -(_posicionLocal ?? motor?.posicionUm ?? 0);
    final habilitado = !bloqueado && !sinMotor && !_autofoco;
    final paso = _pasoActual(motor);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (bloqueado) ...[
          const Franja(texto: Textos.bloqueadoTimelapse, icono: CupertinoIcons.lock_fill),
          const SizedBox(height: Medidas.espacio),
        ] else if (sinMotor) ...[
          const Franja(texto: Textos.focoSinMotor, icono: CupertinoIcons.exclamationmark_triangle_fill),
          const SizedBox(height: Medidas.espacio),
        ] else if (motor != null && motor.hayProblema) ...[
          const Franja(texto: Textos.focoProblemaMotor, icono: CupertinoIcons.flame_fill, color: Colores.mal),
          const SizedBox(height: Medidas.espacio),
        ],
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SizedBox(
              width: 132,
              child: PadFoco(
                habilitado: habilitado,
                moviendo: jog.activo,
                alTocar: _darPaso,
                alMantener: _iniciarJog,
                alSoltar: _detenerJog,
              ),
            ),
            const SizedBox(width: Medidas.espacio),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Tarjeta(
                    padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                    child: Metrica(
                      titulo: Textos.focoAltura,
                      valor: motor == null && _posicionLocal == null
                          ? '—'
                          : '${altura > 0.05 ? '+' : ''}${(altura.abs() < 0.05 ? 0 : altura).toStringAsFixed(1)}',
                      unidad: 'µm',
                      tamano: 34,
                    ),
                  ),
                  const SizedBox(height: 10),
                  Text(Textos.focoPaso, style: Estilos.encabezado(context)),
                  const SizedBox(height: 6),
                  Segmentos<int>(
                    habilitado: habilitado,
                    valor: paso,
                    opciones: {for (final (i, p) in pasosFoco.indexed) i: '${numeroCorto(p.um)} µm'},
                    alCambiar: _elegirPaso,
                  ),
                  const SizedBox(height: 4),
                  Text(pasosFoco[paso].nombre, textAlign: TextAlign.center, style: Estilos.nota(context)),
                ],
              ),
            ),
          ],
        ),
        const SizedBox(height: 8),
        Text(Textos.focoAyuda, textAlign: TextAlign.center, style: Estilos.nota(context)),
        const SizedBox(height: Medidas.espacio),
        BotonGrande(
          texto: _autofoco ? Textos.focoEnfocando : Textos.focoAutofoco,
          icono: CupertinoIcons.scope,
          estilo: EstiloBoton.secundario,
          cargando: _autofoco,
          alTocar: bloqueado || sinMotor || jog.activo ? null : _autoenfocar,
        ),
        if (_autofoco) ...[
          const SizedBox(height: 6),
          Text(Textos.focoEnfocandoDetalle, textAlign: TextAlign.center, style: Estilos.nota(context)),
        ],
      ],
    );
  }
}

/// Dos botones grandes en una sola pieza: Subir arriba, Bajar abajo.
/// Un toque da un paso; mantener más de 400 ms mueve hasta soltar.
///
/// Se usan eventos de puntero crudos (Listener): un gesto que la lista
/// de abajo interpreta como scroll NO cancela el movimiento sin avisar;
/// todo termina en [alSoltar], también si el sistema cancela el gesto.
class PadFoco extends StatefulWidget {
  const PadFoco({
    super.key,
    required this.habilitado,
    required this.moviendo,
    required this.alTocar,
    required this.alMantener,
    required this.alSoltar,
    this.demoraMantener = const Duration(milliseconds: 400),
  });

  final bool habilitado;
  final bool moviendo;
  final ValueChanged<int> alTocar;
  final ValueChanged<int> alMantener;
  final VoidCallback alSoltar;
  final Duration demoraMantener;

  @override
  State<PadFoco> createState() => _PadFocoState();
}

class _PadFocoState extends State<PadFoco> {
  Timer? _timer;
  int? _apretado; // dirección del botón apretado
  bool _mantenido = false;

  void _abajo(int direccion) {
    if (!widget.habilitado || _apretado != null) return;
    setState(() => _apretado = direccion);
    _mantenido = false;
    _timer = Timer(widget.demoraMantener, () {
      _mantenido = true;
      widget.alMantener(direccion);
    });
  }

  // Los eventos de un dedo que se apoyó antes pueden seguir llegando
  // después de que el botón desapareció: entonces no hay nada que pintar
  // (el stop ya lo mandó PanelFoco al cerrarse).
  void _arriba() {
    final d = _apretado;
    if (d == null || !mounted) return;
    final eraToque = _timer?.isActive ?? false;
    _timer?.cancel();
    _timer = null;
    setState(() => _apretado = null);
    if (eraToque) {
      widget.alTocar(d);
    } else if (_mantenido) {
      widget.alSoltar();
    }
    _mantenido = false;
  }

  void _cancelado() {
    if (!mounted) return;
    _timer?.cancel();
    _timer = null;
    final mantenido = _mantenido;
    _mantenido = false;
    if (_apretado != null) setState(() => _apretado = null);
    if (mantenido) widget.alSoltar();
  }

  @override
  void didUpdateWidget(PadFoco oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!widget.habilitado && _apretado != null) _cancelado();
  }

  @override
  void dispose() {
    // El stop lo manda PanelFoco al cerrarse (no se puede avisar al
    // padre mientras el árbol se desarma).
    _timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Opacity(
      opacity: widget.habilitado ? 1 : 0.4,
      child: Container(
        decoration: BoxDecoration(
          color: CupertinoDynamicColor.resolve(Colores.tarjeta, context),
          borderRadius: BorderRadius.circular(28),
        ),
        clipBehavior: Clip.antiAlias,
        // El GestureDetector de arrastre vertical gana la pelea con el
        // scroll de la pantalla: así mantener apretado no mueve la página.
        child: GestureDetector(
          onVerticalDragStart: (_) {},
          onVerticalDragUpdate: (_) {},
          onVerticalDragEnd: (_) {},
          child: Column(
            children: [
              _boton(context, -1, CupertinoIcons.chevron_up, Textos.focoSubir),
              Container(height: 1, color: CupertinoDynamicColor.resolve(Colores.separador, context)),
              _boton(context, 1, CupertinoIcons.chevron_down, Textos.focoBajar),
            ],
          ),
        ),
      ),
    );
  }

  Widget _boton(BuildContext context, int direccion, IconData icono, String texto) {
    final apretado = _apretado == direccion;
    final moviendo = apretado && widget.moviendo;
    final fondo = moviendo
        ? CupertinoDynamicColor.resolve(Colores.acento, context)
        : apretado
        ? CupertinoDynamicColor.resolve(Colores.relleno, context)
        : const Color(0x00000000);
    final frente = moviendo ? CupertinoColors.white : CupertinoDynamicColor.resolve(Colores.acento, context);
    return Semantics(
      button: true,
      label: texto,
      onTap: widget.habilitado ? () => widget.alTocar(direccion) : null,
      child: Listener(
        key: ValueKey('foco_$direccion'),
        behavior: HitTestBehavior.opaque,
        onPointerDown: (_) => _abajo(direccion),
        onPointerUp: (_) => _arriba(),
        onPointerCancel: (_) => _cancelado(),
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 120),
          height: 112,
          color: fondo,
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(icono, size: 36, color: frente),
              const SizedBox(height: 4),
              Text(texto, style: Estilos.titular(context).copyWith(color: frente)),
            ],
          ),
        ),
      ),
    );
  }
}
