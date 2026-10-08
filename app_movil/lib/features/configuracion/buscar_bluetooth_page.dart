import 'dart:async';

import 'package:flutter/cupertino.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../api/bluetooth/bluetooth.dart';
import '../../api/bluetooth/protocolo.dart';
import '../../api/modelos.dart';
import '../../estado/preferencias.dart';
import '../../textos.dart';
import '../../ui/componentes.dart';
import '../../ui/tema.dart';

/// Lo que hace falta para hablar con los microscopios por Bluetooth (las
/// pruebas lo reemplazan).
final bluetoothProvider = Provider<BluetoothMicroscopios>((ref) => BluetoothMicroscopios());

/// Microscopio nuevo (configurarle el Wi-Fi) o que cambió de IP (volver a
/// encontrarlo): todo por Bluetooth, de cerca.
///
/// Pasos: buscar → conectar y leer su estado → (si se puede configurar)
/// confirmar los colores de la luz → elegir la red → conectar → guardar.
class BuscarBluetoothPage extends ConsumerStatefulWidget {
  const BuscarBluetoothPage({super.key});

  static Future<void> abrir(BuildContext context) => Navigator.of(
    context,
    rootNavigator: true,
  ).push(CupertinoPageRoute<void>(fullscreenDialog: true, builder: (_) => const BuscarBluetoothPage()));

  @override
  ConsumerState<BuscarBluetoothPage> createState() => _BuscarBluetoothPageState();
}

enum _Paso { buscando, conectando, equipo, codigo, redes, clave, configurando, listo }

class _BuscarBluetoothPageState extends ConsumerState<BuscarBluetoothPage> {
  _Paso _paso = _Paso.buscando;
  EstadoBluetooth _bt = EstadoBluetooth.desconocido;
  final _encontrados = <String, EquipoCercano>{};
  StreamSubscription<EquipoCercano>? _busqueda;
  StreamSubscription<EstadoBluetooth>? _estadoBt;
  SesionEquipo? _sesion;
  EstadoEquipo? _estado;
  List<RedWifi> _redes = [];
  RedWifi? _red;
  final _colores = <String>[];
  final _clave = TextEditingController();
  final _otraRed = TextEditingController();
  String? _error;
  bool _ocupado = false;

  BluetoothMicroscopios get _ble => ref.read(bluetoothProvider);

  @override
  void initState() {
    super.initState();
    _empezar();
  }

  @override
  void dispose() {
    _busqueda?.cancel();
    _estadoBt?.cancel();
    _sesion?.cerrar();
    _clave.dispose();
    _otraRed.dispose();
    super.dispose();
  }

  Future<void> _empezar() async {
    final permiso = await _ble.pedirPermiso();
    if (!mounted) return;
    if (!permiso) {
      setState(() => _bt = EstadoBluetooth.sinPermiso);
      return;
    }
    _estadoBt = _ble.estado.listen((e) {
      if (!mounted) return;
      setState(() => _bt = e);
      if (e == EstadoBluetooth.listo && _busqueda == null && _paso == _Paso.buscando) _buscar();
    });
  }

  void _buscar() {
    _busqueda?.cancel();
    _busqueda = _ble.buscar().listen(
      (d) {
        if (mounted) setState(() => _encontrados[d.id] = d);
      },
      onError: (Object e) {
        if (mounted) setState(() => _error = Textos.btErrorBuscar);
      },
    );
  }

  Future<void> _paraTarea(Future<void> Function() f) async {
    setState(() {
      _ocupado = true;
      _error = null;
    });
    try {
      await f();
    } on ErrorEquipo catch (e) {
      if (mounted) setState(() => _error = e.mensaje);
    } catch (e) {
      if (mounted) setState(() => _error = Textos.btErrorConexion);
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  Future<void> _conectar(EquipoCercano d) => _paraTarea(() async {
    HapticFeedback.selectionClick();
    // Dejar de buscar (no hace falta esperar a que termine de cancelarse).
    unawaited(_busqueda?.cancel());
    _busqueda = null;
    setState(() => _paso = _Paso.conectando);
    try {
      _sesion = await _ble.conectar(d.id);
      _estado = await _sesion!.estado();
      if (mounted) setState(() => _paso = _Paso.equipo);
    } catch (_) {
      if (mounted) setState(() => _paso = _Paso.buscando);
      _buscar();
      rethrow;
    }
  });

  Future<void> _pedirCodigo() => _paraTarea(() async {
    _estado = await _sesion!.pedirCodigo();
    _colores.clear();
    if (mounted) setState(() => _paso = _Paso.codigo);
  });

  void _tocarColor(String c) {
    if (_colores.length >= 3 || _ocupado) return;
    HapticFeedback.selectionClick();
    setState(() => _colores.add(c));
    if (_colores.length == 3) _enviarCodigo();
  }

  Future<void> _enviarCodigo() => _paraTarea(() async {
    try {
      _estado = await _sesion!.enviarCodigo(List.of(_colores));
    } on ErrorEquipo {
      if (mounted) setState(_colores.clear);
      rethrow;
    }
    HapticFeedback.mediumImpact();
    _redes = await _sesion!.redes();
    if (mounted) setState(() => _paso = _Paso.redes);
  });

  void _elegirRed(RedWifi r) {
    HapticFeedback.selectionClick();
    setState(() {
      _red = r;
      _clave.clear();
      _error = null;
      _paso = _Paso.clave;
    });
  }

  Future<void> _configurar() => _paraTarea(() async {
    final red = _red!;
    setState(() => _paso = _Paso.configurando);
    try {
      _estado = await _sesion!.configurarWifi(red.ssid, _clave.text);
    } on ErrorEquipo {
      if (mounted) setState(() => _paso = _Paso.clave);
      rethrow;
    }
    HapticFeedback.heavyImpact();
    if (mounted) setState(() => _paso = _Paso.listo);
  });

  /// Guarda (o actualiza, si ya estaba con otra IP) el microscopio y lo usa.
  Future<void> _guardar() async {
    final e = _estado!;
    final dir = e.direccion;
    if (dir == null) return;
    await ref
        .read(microscopiosProvider.notifier)
        .guardarDeBluetooth(MicroscopioGuardado(nombre: e.nombre, url: dir.toString()));
    if (mounted) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    return CupertinoPageScaffold(
      navigationBar: CupertinoNavigationBar(
        middle: const Text(Textos.btTitulo),
        leading: CupertinoButton(
          padding: EdgeInsets.zero,
          onPressed: () => Navigator.of(context).pop(),
          child: const Text(Textos.cancelar),
        ),
      ),
      child: SafeArea(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(Medidas.margen, 16, Medidas.margen, 40),
          children: [
            if (_error != null) ...[
              Franja(texto: _error!, icono: CupertinoIcons.exclamationmark_triangle_fill, color: Colores.mal),
              const SizedBox(height: Medidas.espacio),
            ],
            ..._contenido(context),
          ],
        ),
      ),
    );
  }

  List<Widget> _contenido(BuildContext context) => switch (_paso) {
    _Paso.buscando => _buscando(context),
    _Paso.conectando => [_esperando(context, Textos.btConectando)],
    _Paso.equipo => _equipo(context),
    _Paso.codigo => _codigo(context),
    _Paso.redes => _listaRedes(context),
    _Paso.clave => _pedirClave(context),
    _Paso.configurando => [_esperando(context, Textos.btConfigurando(_red?.ssid ?? ''))],
    _Paso.listo => _listo(context),
  };

  Widget _esperando(BuildContext context, String texto) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 60),
    child: Column(
      children: [
        const CupertinoActivityIndicator(radius: 16),
        const SizedBox(height: 16),
        Text(texto, textAlign: TextAlign.center, style: Estilos.secundario(context)),
      ],
    ),
  );

  List<Widget> _buscando(BuildContext context) {
    if (_bt == EstadoBluetooth.apagado) {
      return const [
        Mensaje(icono: CupertinoIcons.bluetooth, titulo: Textos.btApagado, detalle: Textos.btApagadoDetalle),
      ];
    }
    if (_bt == EstadoBluetooth.sinPermiso) {
      return [
        Mensaje(
          icono: CupertinoIcons.lock,
          titulo: Textos.btSinPermiso,
          detalle: Textos.btSinPermisoDetalle,
          accion: Textos.reintentar,
          alTocar: _empezar,
        ),
      ];
    }
    if (_bt == EstadoBluetooth.noDisponible) {
      return const [Mensaje(icono: CupertinoIcons.bluetooth, titulo: Textos.btNoDisponible)];
    }
    final lista = _encontrados.values.toList()..sort((a, b) => b.senal.compareTo(a.senal));
    return [
      Text(Textos.btBuscandoDetalle, style: Estilos.secundario(context)),
      const SizedBox(height: 16),
      Row(
        children: [
          const CupertinoActivityIndicator(),
          const SizedBox(width: 10),
          Text(Textos.btBuscando, style: Estilos.encabezado(context)),
        ],
      ),
      const SizedBox(height: 10),
      for (final d in lista) ...[
        Tarjeta(
          alTocar: _ocupado ? null : () => _conectar(d),
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
          child: Row(
            children: [
              const Icon(CupertinoIcons.circle_grid_hex_fill, color: Colores.acento, size: 30),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(d.nombre, style: Estilos.titular(context)),
                    Text(Textos.btSenal(d.senal), style: Estilos.nota(context)),
                  ],
                ),
              ),
              const Icon(CupertinoIcons.chevron_forward, color: Colores.textoTerciario),
            ],
          ),
        ),
        const SizedBox(height: 8),
      ],
    ];
  }

  List<Widget> _equipo(BuildContext context) {
    final e = _estado!;
    return [
      Tarjeta(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(e.nombre, style: Estilos.titulo(context)),
            const SizedBox(height: 8),
            Row(
              children: [
                PuntoSemaforo(e.conectado ? Semaforo.bien : Semaforo.aviso),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    e.conectado ? Textos.btConectadoA(e.red ?? '?', e.ip ?? '?') : Textos.btSinRed,
                    style: Estilos.secundario(context),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
      const SizedBox(height: 16),
      if (e.direccion != null) ...[
        BotonGrande(texto: Textos.btGuardarYUsar, icono: CupertinoIcons.link, alTocar: _guardar),
        const SizedBox(height: 6),
        Text(Textos.btGuardarDetalle, textAlign: TextAlign.center, style: Estilos.nota(context)),
        const SizedBox(height: 18),
      ],
      if (e.configurable) ...[
        BotonGrande(
          texto: e.conectado ? Textos.btCambiarRed : Textos.btConfigurarWifi,
          icono: CupertinoIcons.wifi,
          estilo: e.direccion != null ? EstiloBoton.secundario : EstiloBoton.primario,
          cargando: _ocupado,
          alTocar: _pedirCodigo,
        ),
        const SizedBox(height: 6),
        Text(Textos.btVincularAviso, textAlign: TextAlign.center, style: Estilos.nota(context)),
      ] else
        Franja(texto: e.motivo, icono: CupertinoIcons.lock_fill, color: Colores.acento),
    ];
  }

  List<Widget> _codigo(BuildContext context) => [
    Text(Textos.btCodigoTitulo, style: Estilos.titulo(context)),
    const SizedBox(height: 6),
    Text(Textos.btCodigoDetalle, style: Estilos.secundario(context)),
    const SizedBox(height: 20),
    // Los 3 lugares
    Row(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        for (var i = 0; i < 3; i++)
          Container(
            margin: const EdgeInsets.symmetric(horizontal: 8),
            width: 64,
            height: 64,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: i < _colores.length
                  ? Color(coloresCodigo[_colores[i]]!)
                  : CupertinoDynamicColor.resolve(Colores.relleno, context),
              border: Border.all(color: CupertinoDynamicColor.resolve(Colores.separador, context)),
            ),
            child: i < _colores.length ? null : Center(child: Text('${i + 1}', style: Estilos.titular(context))),
          ),
      ],
    ),
    const SizedBox(height: 24),
    Wrap(
      alignment: WrapAlignment.center,
      spacing: 16,
      runSpacing: 16,
      children: [
        for (final e in coloresCodigo.entries)
          Semantics(
            button: true,
            label: e.key,
            excludeSemantics: true,
            child: GestureDetector(
              onTap: () => _tocarColor(e.key),
              child: Column(
                children: [
                  Container(
                    width: 76,
                    height: 76,
                    decoration: BoxDecoration(shape: BoxShape.circle, color: Color(e.value)),
                  ),
                  const SizedBox(height: 4),
                  Text(e.key, style: Estilos.nota(context)),
                ],
              ),
            ),
          ),
      ],
    ),
    const SizedBox(height: 20),
    if (_ocupado)
      const Center(child: CupertinoActivityIndicator())
    else
      Row(
        children: [
          Expanded(
            child: BotonGrande(
              texto: Textos.btBorrar,
              estilo: EstiloBoton.suave,
              alTocar: _colores.isEmpty ? null : () => setState(_colores.clear),
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: BotonGrande(texto: Textos.btOtroCodigo, estilo: EstiloBoton.secundario, alTocar: _pedirCodigo),
          ),
        ],
      ),
  ];

  List<Widget> _listaRedes(BuildContext context) => [
    Text(Textos.btElegirRed, style: Estilos.titulo(context)),
    const SizedBox(height: 12),
    CupertinoListSection.insetGrouped(
      margin: EdgeInsets.zero,
      children: [
        for (final r in _redes)
          CupertinoListTile(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
            leading: Icon(
              r.senal > 66
                  ? CupertinoIcons.wifi
                  : (r.senal > 33 ? CupertinoIcons.wifi : CupertinoIcons.wifi_exclamationmark),
              color: Colores.acento,
            ),
            title: Text(r.ssid),
            additionalInfo: r.segura ? const Icon(CupertinoIcons.lock_fill, size: 16) : null,
            trailing: const CupertinoListTileChevron(),
            onTap: () => _elegirRed(r),
          ),
        CupertinoTextField.borderless(
          controller: _otraRed,
          placeholder: Textos.btOtraRed,
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 18),
          onSubmitted: (v) {
            if (v.trim().isNotEmpty) _elegirRed(RedWifi(v.trim(), 0, true));
          },
        ),
      ],
    ),
  ];

  List<Widget> _pedirClave(BuildContext context) => [
    Text(Textos.btClaveTitulo(_red!.ssid), style: Estilos.titulo(context)),
    const SizedBox(height: 12),
    if (_red!.segura)
      CupertinoTextField(
        controller: _clave,
        placeholder: Textos.btClave,
        obscureText: true,
        autofocus: true,
        padding: const EdgeInsets.all(16),
        onSubmitted: (_) => _configurar(),
      )
    else
      Text(Textos.btRedAbierta, style: Estilos.secundario(context)),
    const SizedBox(height: 16),
    BotonGrande(texto: Textos.btConectarRed, icono: CupertinoIcons.wifi, cargando: _ocupado, alTocar: _configurar),
    CupertinoButton(onPressed: () => setState(() => _paso = _Paso.redes), child: const Text(Textos.btOtraRedVolver)),
  ];

  List<Widget> _listo(BuildContext context) {
    final e = _estado!;
    return [
      const SizedBox(height: 20),
      const Icon(CupertinoIcons.checkmark_circle_fill, size: 64, color: Colores.bien),
      const SizedBox(height: 12),
      Text(Textos.btListo, textAlign: TextAlign.center, style: Estilos.titulo(context)),
      const SizedBox(height: 6),
      Text(
        Textos.btConectadoA(e.red ?? '', e.ip ?? '?'),
        textAlign: TextAlign.center,
        style: Estilos.secundario(context),
      ),
      const SizedBox(height: 24),
      BotonGrande(
        texto: Textos.btGuardarYUsar,
        icono: CupertinoIcons.link,
        alTocar: e.direccion == null ? null : _guardar,
      ),
    ];
  }
}
