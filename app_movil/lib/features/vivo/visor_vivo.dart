import 'dart:async';
import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:flutter/cupertino.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../api/errores.dart';
import '../../api/imagen_api.dart';
import '../../estado/conexion.dart';
import '../../estado/datos.dart';
import '../../textos.dart';
import '../../ui/componentes.dart';
import '../../ui/tema.dart';
import 'coordinador_vivo.dart';

/// Video en vivo de una cámara (MJPEG de /live/stream/{cam}).
///
/// - Mira el vivo solo mientras se ve: con la app en primer plano, la
///   pestaña visible y sin una foto en curso. Si no, suelta la cámara
///   (ver [CoordinadorVivo]).
/// - Si el servidor corta el flujo (una foto, un reinicio), vuelve a
///   conectar solo.
/// - Con un timelapse tomando fotos no hay vivo: muestra la última foto
///   de cada ciclo (/timelapse/vista/{cam}), que se actualiza sola.
/// - Cada cuadro se decodifica a mano y se pinta con RawImage: con
///   Image.memory, cada cuadro (15 por segundo) quedaría en la caché de
///   imágenes. Si llega un cuadro mientras se decodifica el anterior, se
///   salta.
class VisorVivo extends ConsumerStatefulWidget {
  const VisorVivo({super.key, required this.camara, this.alPantallaCompleta, this.redondeado = true});

  final int camara;
  final VoidCallback? alPantallaCompleta;
  final bool redondeado;

  @override
  ConsumerState<VisorVivo> createState() => _VisorVivoState();
}

enum _Estado { conectando, vivo, cortado }

class _VisorVivoState extends ConsumerState<VisorVivo> {
  ui.Image? _imagen;
  bool _decodificando = false;
  _Estado _estado = _Estado.conectando;
  String? _error;
  StreamSubscription<Uint8List>? _sub;
  Timer? _reintento;
  int _gen = 0;

  // Uso registrado en el coordinador (para soltarlo exactamente una vez).
  CoordinadorVivo? _coord;
  int? _camUsada;

  late final AppLifecycleListener _ciclo;
  bool _appActiva = true;
  bool _visible = true;

  @override
  void initState() {
    super.initState();
    _ciclo = AppLifecycleListener(
      onStateChange: (s) {
        final activa = s == AppLifecycleState.resumed;
        if (activa == _appActiva) return;
        _appActiva = activa;
        // A segundo plano: soltar la cámara YA, sin la espera habitual.
        _actualizar(inmediato: !activa);
      },
    );
    WidgetsBinding.instance.addPostFrameCallback((_) => _actualizar());
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    // false si la pestaña no es la visible o hay otra pantalla encima.
    final v = TickerMode.valuesOf(context).enabled;
    if (v != _visible) {
      _visible = v;
      _actualizar();
    }
  }

  @override
  void didUpdateWidget(VisorVivo oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.camara != widget.camara) {
      _desconectar();
      _limpiarImagen();
      _estado = _Estado.conectando;
      _actualizar();
    }
  }

  @override
  void dispose() {
    _ciclo.dispose();
    _desconectar();
    _limpiarImagen();
    super.dispose();
  }

  bool get _debeMirar =>
      mounted &&
      _appActiva &&
      _visible &&
      !ref.read(pausaVivoProvider) &&
      !(ref.read(estadoProvider).value?.bloqueaControles ?? false);

  void _actualizar({bool inmediato = false}) {
    if (_debeMirar) {
      if (_sub == null && _reintento == null) _conectar();
    } else {
      _desconectar(inmediato: inmediato);
    }
  }

  Future<void> _conectar() async {
    final coord = ref.read(coordinadorVivoProvider);
    final c = ref.read(clienteProvider);
    if (coord == null || c == null) return;
    final gen = ++_gen;
    final cam = widget.camara;
    try {
      if (_camUsada == cam && identical(_coord, coord)) {
        await coord.reactivar(cam);
      } else {
        _soltarUso();
        _coord = coord;
        _camUsada = cam;
        await coord.usar(cam);
      }
    } on ErrorApi catch (e) {
      if (gen != _gen || !mounted) return;
      setState(() {
        _estado = _Estado.cortado;
        _error = e.mensaje;
      });
      _programarReintento();
      return;
    }
    if (gen != _gen || !mounted) return;
    _sub = c
        .flujoVivo(cam)
        .listen(
          (cuadro) => _mostrar(cuadro, gen),
          onError: (_) => _cortado(gen),
          onDone: () => _cortado(gen),
          cancelOnError: true,
        );
  }

  void _cortado(int gen) {
    if (gen != _gen || !mounted) return;
    _sub = null;
    setState(() => _estado = _Estado.cortado);
    _programarReintento();
  }

  void _programarReintento() {
    _reintento?.cancel();
    _reintento = Timer(const Duration(milliseconds: 1500), () {
      _reintento = null;
      if (_debeMirar) _conectar();
    });
  }

  void _desconectar({bool inmediato = false}) {
    _gen++;
    _reintento?.cancel();
    _reintento = null;
    _sub?.cancel();
    _sub = null;
    _soltarUso(inmediato: inmediato);
  }

  void _soltarUso({bool inmediato = false}) {
    final cam = _camUsada;
    if (cam != null) _coord?.soltar(cam, inmediato: inmediato);
    _camUsada = null;
    _coord = null;
  }

  Future<void> _mostrar(Uint8List bytes, int gen) async {
    if (_decodificando) return;
    _decodificando = true;
    try {
      final codec = await ui.instantiateImageCodec(bytes);
      final cuadro = await codec.getNextFrame();
      codec.dispose();
      if (!mounted || gen != _gen) {
        cuadro.image.dispose();
        return;
      }
      final viejo = _imagen;
      setState(() {
        _imagen = cuadro.image;
        _estado = _Estado.vivo;
        _error = null;
      });
      // RawImage no es dueña de la imagen: se libera la anterior cuando
      // ya se pintó la nueva.
      if (viejo != null) WidgetsBinding.instance.addPostFrameCallback((_) => viejo.dispose());
    } catch (_) {
      // un cuadro roto: se espera el siguiente
    } finally {
      _decodificando = false;
    }
  }

  void _limpiarImagen() {
    final viejo = _imagen;
    _imagen = null;
    if (viejo != null) WidgetsBinding.instance.addPostFrameCallback((_) => viejo.dispose());
  }

  @override
  Widget build(BuildContext context) {
    ref.listen(pausaVivoProvider, (_, _) => _actualizar(inmediato: true));
    ref.listen(estadoProvider, (antes, ahora) {
      if (antes?.value?.bloqueaControles != ahora.value?.bloqueaControles) _actualizar(inmediato: true);
    });
    final estado = ref.watch(estadoProvider).value;
    final conTimelapse = estado?.bloqueaControles ?? false;

    Widget contenido;
    Widget? insignia;
    if (conTimelapse) {
      final u = estado!.timelapse?.ultimas[widget.camara];
      final c = ref.watch(clienteProvider);
      insignia = const Insignia(Textos.vivoUltimoCiclo, color: Colores.aviso);
      contenido = u == null || c == null
          ? _texto(Textos.timelapseSinFotoAun)
          : InteractiveViewer(
              maxScale: 6,
              child: Center(
                child: Image(
                  image: ImagenMicroscopio(
                    c,
                    c.rutaVistaTimelapse(widget.camara, version: '${u.ciclo}${u.dpc ? 'd' : ''}'),
                  ),
                  fit: BoxFit.contain,
                  gaplessPlayback: true,
                  errorBuilder: (_, _, _) => _texto(Textos.timelapseSinFotoAun),
                ),
              ),
            );
    } else if (_imagen != null) {
      insignia = _estado == _Estado.vivo
          ? const Insignia(Textos.vivoEnVivo, color: Colores.vivo, punto: true)
          : const Insignia(Textos.reconectando, color: Colores.aviso);
      contenido = InteractiveViewer(
        maxScale: 6,
        child: Center(
          child: RawImage(image: _imagen, fit: BoxFit.contain),
        ),
      );
    } else {
      contenido = Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const CupertinoActivityIndicator(color: CupertinoColors.white, radius: 13),
            const SizedBox(height: 10),
            Text(
              _estado == _Estado.cortado ? (_error ?? Textos.vivoCortado) : Textos.vivoConectando,
              textAlign: TextAlign.center,
              style: const TextStyle(color: CupertinoColors.systemGrey, fontSize: 14),
            ),
          ],
        ),
      );
    }

    final radio = widget.redondeado ? BorderRadius.circular(18) : BorderRadius.zero;
    return Semantics(
      label: '${Textos.camaraN(widget.camara)}. ${conTimelapse ? Textos.vivoTimelapse : Textos.vivoEnVivo}',
      child: ClipRRect(
        borderRadius: radio,
        child: ColoredBox(
          color: CupertinoColors.black,
          child: Stack(
            fit: StackFit.expand,
            children: [
              contenido,
              if (insignia != null) Positioned(top: 10, left: 10, child: _fondoInsignia(insignia)),
              if (widget.alPantallaCompleta != null)
                Positioned(
                  top: 4,
                  right: 4,
                  child: CupertinoButton(
                    padding: EdgeInsets.zero,
                    minimumSize: const Size(48, 48),
                    onPressed: widget.alPantallaCompleta,
                    child: Container(
                      width: 38,
                      height: 38,
                      decoration: const BoxDecoration(color: Color(0x99000000), shape: BoxShape.circle),
                      child: const Icon(
                        CupertinoIcons.arrow_up_left_arrow_down_right,
                        color: CupertinoColors.white,
                        size: 18,
                        semanticLabel: Textos.vivoPantallaCompleta,
                      ),
                    ),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _fondoInsignia(Widget hijo) => DecoratedBox(
    decoration: BoxDecoration(color: const Color(0xB3000000), borderRadius: BorderRadius.circular(7)),
    child: hijo,
  );

  Widget _texto(String t) => Center(
    child: Padding(
      padding: const EdgeInsets.all(16),
      child: Text(
        t,
        textAlign: TextAlign.center,
        style: const TextStyle(color: CupertinoColors.systemGrey),
      ),
    ),
  );
}
