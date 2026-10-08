import 'package:flutter/cupertino.dart';

import '../api/modelos.dart';

/// Aspecto de la app: el de una app de Apple (Human Interface
/// Guidelines). Colores del sistema que cambian solos entre claro y
/// oscuro, listas agrupadas, tipografía del sistema y controles grandes
/// (el microscopio se usa con guantes).
abstract final class Medidas {
  /// Alto mínimo de cualquier cosa que se toque. Apple pide 44; con
  /// guantes, más.
  static const toque = 56.0;
  static const toqueGrande = 64.0;
  static const radioTarjeta = 22.0;
  static const radioBoton = 16.0;
  static const margen = 16.0;
  static const espacio = 12.0;
}

abstract final class Colores {
  static const acento = CupertinoColors.systemBlue;
  static const bien = CupertinoColors.systemGreen;
  static const aviso = CupertinoColors.systemOrange;
  static const mal = CupertinoColors.systemRed;
  static const vivo = CupertinoColors.systemRed;

  static const fondo = CupertinoColors.systemGroupedBackground;
  static const tarjeta = CupertinoColors.secondarySystemGroupedBackground;
  static const tarjetaInterna = CupertinoColors.tertiarySystemGroupedBackground;
  static const relleno = CupertinoColors.tertiarySystemFill;
  static const texto = CupertinoColors.label;
  static const textoSecundario = CupertinoColors.secondaryLabel;
  static const textoTerciario = CupertinoColors.tertiaryLabel;
  static const separador = CupertinoColors.separator;

  static Color deSemaforo(Semaforo s) => switch (s) {
    Semaforo.bien => bien,
    Semaforo.aviso => aviso,
    Semaforo.mal => mal,
    Semaforo.sinDato => textoTerciario,
  };
}

CupertinoThemeData temaApp(Brightness? brillo) => CupertinoThemeData(
  brightness: brillo,
  primaryColor: Colores.acento,
  scaffoldBackgroundColor: Colores.fondo,
  applyThemeToAll: true,
);

/// Estilos de texto sobre la escala tipográfica de iOS.
abstract final class Estilos {
  static const _numeros = [FontFeature.tabularFigures()];

  static TextStyle base(BuildContext c) => CupertinoTheme.of(c).textTheme.textStyle;

  static TextStyle tituloGrande(BuildContext c) =>
      base(c).copyWith(fontSize: 28, fontWeight: FontWeight.w700, letterSpacing: 0.2);

  static TextStyle titulo(BuildContext c) =>
      base(c).copyWith(fontSize: 20, fontWeight: FontWeight.w600, letterSpacing: 0.3);

  static TextStyle titular(BuildContext c) => base(c).copyWith(fontSize: 17, fontWeight: FontWeight.w600);

  static TextStyle cuerpo(BuildContext c) => base(c).copyWith(fontSize: 17);

  static TextStyle secundario(BuildContext c) =>
      base(c).copyWith(fontSize: 15, color: CupertinoDynamicColor.resolve(Colores.textoSecundario, c));

  static TextStyle nota(BuildContext c) =>
      base(c).copyWith(fontSize: 13, color: CupertinoDynamicColor.resolve(Colores.textoSecundario, c));

  static TextStyle encabezado(BuildContext c) => base(c).copyWith(
    fontSize: 13,
    fontWeight: FontWeight.w600,
    letterSpacing: 0.4,
    color: CupertinoDynamicColor.resolve(Colores.textoSecundario, c),
  );

  /// Números grandes de la incubadora y la altura del foco.
  static TextStyle metrica(BuildContext c, {double tamano = 40}) => base(
    c,
  ).copyWith(fontSize: tamano, fontWeight: FontWeight.w600, letterSpacing: -0.5, height: 1.05, fontFeatures: _numeros);

  static TextStyle unidad(BuildContext c) => base(c).copyWith(
    fontSize: 17,
    fontWeight: FontWeight.w500,
    color: CupertinoDynamicColor.resolve(Colores.textoSecundario, c),
  );
}
