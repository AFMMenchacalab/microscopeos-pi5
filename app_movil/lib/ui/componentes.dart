import 'package:flutter/cupertino.dart';
import 'package:flutter/services.dart';

import '../api/modelos.dart';
import 'tema.dart';

/// Superficie redondeada sobre el fondo agrupado (como las tarjetas de
/// Salud o Fitness).
class Tarjeta extends StatelessWidget {
  const Tarjeta({super.key, required this.child, this.padding = const EdgeInsets.all(Medidas.margen), this.alTocar});

  final Widget child;
  final EdgeInsetsGeometry padding;
  final VoidCallback? alTocar;

  @override
  Widget build(BuildContext context) {
    final contenido = DecoratedBox(
      decoration: BoxDecoration(
        color: CupertinoDynamicColor.resolve(Colores.tarjeta, context),
        borderRadius: BorderRadius.circular(Medidas.radioTarjeta),
      ),
      child: Padding(padding: padding, child: child),
    );
    if (alTocar == null) return contenido;
    return GestureDetector(behavior: HitTestBehavior.opaque, onTap: alTocar, child: contenido);
  }
}

/// Título chico arriba de una tarjeta o sección.
class TituloSeccion extends StatelessWidget {
  const TituloSeccion(this.texto, {super.key, this.icono, this.extremo});
  final String texto;
  final IconData? icono;
  final Widget? extremo;

  @override
  Widget build(BuildContext context) {
    final estilo = Estilos.encabezado(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(4, 0, 4, 8),
      child: Row(
        children: [
          if (icono != null) ...[Icon(icono, size: 15, color: estilo.color), const SizedBox(width: 6)],
          Expanded(child: Text(texto, style: estilo)),
          ?extremo,
        ],
      ),
    );
  }
}

enum EstiloBoton { primario, secundario, destructivo, suave }

/// Botón grande y fácil de tocar con guantes. Con [cargando] muestra un
/// indicador y no se puede tocar.
class BotonGrande extends StatelessWidget {
  const BotonGrande({
    super.key,
    required this.texto,
    this.icono,
    this.alTocar,
    this.estilo = EstiloBoton.primario,
    this.cargando = false,
    this.alto = Medidas.toque,
    this.expandir = true,
  });

  final String texto;
  final IconData? icono;
  final VoidCallback? alTocar;
  final EstiloBoton estilo;
  final bool cargando;
  final double alto;
  final bool expandir;

  @override
  Widget build(BuildContext context) {
    final activo = alTocar != null && !cargando;
    final (Color fondo, Color frente) = switch (estilo) {
      EstiloBoton.primario => (Colores.acento, CupertinoColors.white),
      EstiloBoton.destructivo => (Colores.mal.withValues(alpha: 0.16), Colores.mal),
      EstiloBoton.secundario => (Colores.acento.withValues(alpha: 0.16), Colores.acento),
      EstiloBoton.suave => (Colores.relleno, Colores.texto),
    };
    final f = CupertinoDynamicColor.resolve(fondo, context);
    final t = CupertinoDynamicColor.resolve(frente, context);
    final hijo = Row(
      mainAxisSize: expandir ? MainAxisSize.max : MainAxisSize.min,
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        if (cargando)
          Padding(
            padding: const EdgeInsets.only(right: 10),
            child: CupertinoActivityIndicator(color: t),
          )
        else if (icono != null)
          Padding(
            padding: const EdgeInsets.only(right: 8),
            child: Icon(icono, color: t, size: 22),
          ),
        Flexible(
          child: Text(
            texto,
            maxLines: 2,
            textAlign: TextAlign.center,
            overflow: TextOverflow.ellipsis,
            style: Estilos.titular(context).copyWith(color: t),
          ),
        ),
      ],
    );
    return Semantics(
      button: true,
      enabled: activo,
      label: texto,
      excludeSemantics: true,
      child: Opacity(
        opacity: activo || cargando ? 1 : 0.4,
        child: CupertinoButton(
          padding: const EdgeInsets.symmetric(horizontal: 18),
          minimumSize: Size(alto, alto),
          color: f,
          borderRadius: BorderRadius.circular(Medidas.radioBoton),
          onPressed: activo
              ? () {
                  HapticFeedback.lightImpact();
                  alTocar!();
                }
              : null,
          disabledColor: f,
          child: SizedBox(height: alto, child: hijo),
        ),
      ),
    );
  }
}

/// Punto de color: verde bien, naranja aviso, rojo problema.
class PuntoSemaforo extends StatelessWidget {
  const PuntoSemaforo(this.semaforo, {super.key, this.tamano = 10});
  final Semaforo semaforo;
  final double tamano;

  @override
  Widget build(BuildContext context) => Container(
    width: tamano,
    height: tamano,
    decoration: BoxDecoration(
      color: CupertinoDynamicColor.resolve(Colores.deSemaforo(semaforo), context),
      shape: BoxShape.circle,
    ),
  );
}

/// Etiqueta chica de color («EN CURSO», «EN VIVO»).
class Insignia extends StatelessWidget {
  const Insignia(this.texto, {super.key, this.color = Colores.acento, this.punto = false});
  final String texto;
  final Color color;
  final bool punto;

  @override
  Widget build(BuildContext context) {
    final c = CupertinoDynamicColor.resolve(color, context);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(color: c.withValues(alpha: 0.18), borderRadius: BorderRadius.circular(6)),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (punto) ...[
            Container(
              width: 7,
              height: 7,
              decoration: BoxDecoration(color: c, shape: BoxShape.circle),
            ),
            const SizedBox(width: 5),
          ],
          Text(
            texto,
            style: Estilos.base(context)
                .copyWith(fontSize: 11, fontWeight: FontWeight.w700, letterSpacing: 0.6, color: c),
          ),
        ],
      ),
    );
  }
}

/// Mensaje a lo ancho: vacío, error o aviso, con un botón opcional.
class Mensaje extends StatelessWidget {
  const Mensaje({
    super.key,
    required this.icono,
    required this.titulo,
    this.detalle,
    this.accion,
    this.alTocar,
    this.color,
  });

  final IconData icono;
  final String titulo;
  final String? detalle;
  final String? accion;
  final VoidCallback? alTocar;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    final c = CupertinoDynamicColor.resolve(color ?? Colores.textoSecundario, context);
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 32),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icono, size: 44, color: c),
          const SizedBox(height: 12),
          Text(titulo, textAlign: TextAlign.center, style: Estilos.titular(context)),
          if (detalle != null) ...[
            const SizedBox(height: 6),
            Text(detalle!, textAlign: TextAlign.center, style: Estilos.secundario(context)),
          ],
          if (accion != null && alTocar != null) ...[
            const SizedBox(height: 16),
            BotonGrande(texto: accion!, alTocar: alTocar, estilo: EstiloBoton.secundario, expandir: false),
          ],
        ],
      ),
    );
  }
}

/// Franja de aviso dentro de una pantalla (sin conexión, bloqueado...).
class Franja extends StatelessWidget {
  const Franja({
    super.key,
    required this.texto,
    this.icono = CupertinoIcons.info_circle_fill,
    this.color = Colores.aviso,
    this.accion,
    this.alTocar,
  });

  final String texto;
  final IconData icono;
  final Color color;
  final String? accion;
  final VoidCallback? alTocar;

  @override
  Widget build(BuildContext context) {
    final c = CupertinoDynamicColor.resolve(color, context);
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 12, 8, 12),
      decoration: BoxDecoration(color: c.withValues(alpha: 0.14), borderRadius: BorderRadius.circular(14)),
      child: Row(
        children: [
          Icon(icono, color: c, size: 22),
          const SizedBox(width: 10),
          Expanded(child: Text(texto, style: Estilos.base(context).copyWith(fontSize: 15))),
          if (accion != null)
            CupertinoButton(
              padding: const EdgeInsets.symmetric(horizontal: 10),
              minimumSize: const Size(44, 44),
              onPressed: alTocar,
              child: Text(
                accion!,
                style: Estilos.base(context).copyWith(fontSize: 15, fontWeight: FontWeight.w600, color: c),
              ),
            ),
        ],
      ),
    );
  }
}

/// Número grande con unidad y una línea abajo (temperatura, CO2, altura).
class Metrica extends StatelessWidget {
  const Metrica({
    super.key,
    required this.valor,
    required this.unidad,
    this.titulo,
    this.detalle,
    this.semaforo,
    this.tamano = 40,
  });

  final String valor;
  final String unidad;
  final String? titulo;
  final String? detalle;
  final Semaforo? semaforo;
  final double tamano;

  @override
  Widget build(BuildContext context) {
    final color = semaforo == null || semaforo == Semaforo.bien || semaforo == Semaforo.sinDato
        ? null
        : CupertinoDynamicColor.resolve(Colores.deSemaforo(semaforo!), context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        if (titulo != null)
          Row(
            children: [
              if (semaforo != null) ...[PuntoSemaforo(semaforo!), const SizedBox(width: 6)],
              Flexible(
                child: Text(titulo!, style: Estilos.encabezado(context), overflow: TextOverflow.ellipsis),
              ),
            ],
          ),
        const SizedBox(height: 4),
        // Con letra grande (accesibilidad) o pantallas angostas, el número
        // se achica en vez de salirse de la tarjeta.
        FittedBox(
          fit: BoxFit.scaleDown,
          alignment: Alignment.centerLeft,
          child: Row(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.baseline,
            textBaseline: TextBaseline.alphabetic,
            children: [
              Text(
                valor,
                style: Estilos.metrica(context, tamano: tamano).copyWith(color: color),
              ),
              const SizedBox(width: 4),
              Text(unidad, style: Estilos.unidad(context)),
            ],
          ),
        ),
        if (detalle != null) ...[const SizedBox(height: 2), Text(detalle!, style: Estilos.nota(context))],
      ],
    );
  }
}

/// Barra de progreso fina y redondeada.
class BarraProgreso extends StatelessWidget {
  const BarraProgreso(this.valor, {super.key, this.color = Colores.acento, this.alto = 6});
  final double valor;
  final Color color;
  final double alto;

  @override
  Widget build(BuildContext context) => ClipRRect(
    borderRadius: BorderRadius.circular(alto),
    child: SizedBox(
      height: alto,
      child: Stack(
        children: [
          Positioned.fill(child: ColoredBox(color: CupertinoDynamicColor.resolve(Colores.relleno, context))),
          FractionallySizedBox(
            widthFactor: valor.clamp(0.0, 1.0),
            child: ColoredBox(color: CupertinoDynamicColor.resolve(color, context)),
          ),
        ],
      ),
    ),
  );
}

/// Control segmentado de iOS con opciones grandes.
class Segmentos<T extends Object> extends StatelessWidget {
  const Segmentos({
    super.key,
    required this.opciones,
    required this.valor,
    required this.alCambiar,
    this.habilitado = true,
  });

  final Map<T, String> opciones;
  final T valor;
  final ValueChanged<T> alCambiar;
  final bool habilitado;

  @override
  Widget build(BuildContext context) {
    return Opacity(
      opacity: habilitado ? 1 : 0.45,
      child: IgnorePointer(
        ignoring: !habilitado,
        child: SizedBox(
          width: double.infinity,
          child: CupertinoSlidingSegmentedControl<T>(
            groupValue: valor,
            padding: const EdgeInsets.all(3),
            onValueChanged: (v) {
              if (v == null) return;
              HapticFeedback.selectionClick();
              alCambiar(v);
            },
            children: {
              for (final e in opciones.entries)
                e.key: Padding(
                  padding: const EdgeInsets.symmetric(vertical: 11, horizontal: 4),
                  child: Text(
                    e.value,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: Estilos.base(context).copyWith(fontSize: 15, fontWeight: FontWeight.w600),
                  ),
                ),
            },
          ),
        ),
      ),
    );
  }
}
