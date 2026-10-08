import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:microscopeos/api/errores.dart';
import 'package:microscopeos/api/lector_mjpeg.dart';
import 'package:microscopeos/api/lector_sse.dart';
import 'package:microscopeos/estado/datos.dart';

import '../ayuda/adaptador_falso.dart';

/// Un "JPEG" con sus marcadores de inicio y fin y datos en el medio
/// (incluye FF 00, que es como aparece un FF dentro de los datos).
Uint8List jpegFalso(int n) => Uint8List.fromList([
  0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, //
  for (var i = 0; i < 50 + n * 7; i++) (i * 31 + n) % 0xFE,
  0xFF, 0x00, n, 0xFF, 0x00,
  0xFF, 0xD9,
]);

/// Como lo arma live_stream() en api.py.
List<int> parteMjpeg(Uint8List jpeg) => [
  ...ascii.encode('--frame\r\nContent-Type: image/jpeg\r\n\r\n'),
  ...jpeg,
  ...ascii.encode('\r\n'),
];

/// Corta los bytes en trozos de tamaño al azar (pero reproducible).
List<List<int>> trocear(List<int> datos, Random r, {int maximo = 40}) {
  final out = <List<int>>[];
  var i = 0;
  while (i < datos.length) {
    final n = 1 + r.nextInt(maximo);
    out.add(datos.sublist(i, min(i + n, datos.length)));
    i += n;
  }
  return out;
}

void main() {
  group('lector MJPEG', () {
    test('separa los cuadros aunque lleguen cortados por cualquier lado', () {
      final cuadros = [for (var i = 0; i < 20; i++) jpegFalso(i)];
      final flujo = [for (final c in cuadros) ...parteMjpeg(c)];
      for (var semilla = 0; semilla < 30; semilla++) {
        final s = SeparadorMjpeg();
        final salida = <Uint8List>[];
        for (final t in trocear(flujo, Random(semilla), maximo: 1 + semilla * 5)) {
          salida.addAll(s.agregar(t));
        }
        expect(salida, cuadros, reason: 'semilla $semilla');
        expect(s.pendientes, lessThan(80), reason: 'no acumula lo que ya entregó');
      }
    });

    test('un marcador partido justo entre dos trozos', () {
      final j = jpegFalso(1);
      final s = SeparadorMjpeg();
      expect(s.agregar([...ascii.encode('--frame\r\n\r\n'), 0xFF]), isEmpty);
      expect(s.agregar(j.sublist(1, j.length - 1)), isEmpty);
      expect(s.agregar([0xD9, 13, 10]), [j]);
    });

    test('un cuadro cortado a la mitad se descarta y sigue con el próximo', () {
      final a = jpegFalso(1), b = jpegFalso(2);
      final s = SeparadorMjpeg();
      final salida = s.agregar([...a.sublist(0, 30), ...parteMjpeg(b)]);
      expect(salida, [b]);
    });

    test('si se acumula demasiado sin cerrar el cuadro, se reinicia', () {
      final s = SeparadorMjpeg(maximoBytes: 1000);
      s.agregar([0xFF, 0xD8, ...List.filled(2000, 1)]);
      expect(s.pendientes, 0);
      expect(s.agregar(parteMjpeg(jpegFalso(3))), [jpegFalso(3)]);
    });

    test('el cliente entrega los cuadros de /live/stream', () async {
      final cuadros = [for (var i = 0; i < 5; i++) jpegFalso(i)];
      final datos = [for (final c in cuadros) ...parteMjpeg(c)];
      final (c, a) = clienteFalso(
        (_) => flujo(Stream.fromIterable(trocear(datos, Random(7))), tipo: 'multipart/x-mixed-replace; boundary=frame'),
      );
      expect(await c.flujoVivo(1).toList(), cuadros);
      expect(a.pedidos.single.ruta, '/live/stream/1');
    });

    test('cancelar la suscripción corta la conexión', () async {
      final fuente = StreamController<List<int>>();
      var cancelada = false;
      fuente.onCancel = () => cancelada = true;
      final (c, _) = clienteFalso((_) => flujo(fuente.stream, tipo: 'multipart/x-mixed-replace'));
      final recibidos = <Uint8List>[];
      final sub = c.flujoVivo(0).listen(recibidos.add);
      await pumpEventQueue();
      fuente.add(parteMjpeg(jpegFalso(1)));
      await pumpEventQueue();
      expect(recibidos, hasLength(1));
      await sub.cancel();
      await pumpEventQueue();
      expect(cancelada, isTrue);
    });
  });

  group('lector SSE', () {
    test('un evento por bloque «data:», aunque llegue cortado', () {
      const texto = 'data: {"temperature": 36.9}\n\ndata: {"temperature": 37.0}\n\n';
      for (var corte = 1; corte < texto.length; corte++) {
        final s = SeparadorSse();
        final ev = [...s.agregar(texto.substring(0, corte)), ...s.agregar(texto.substring(corte))];
        expect(ev, ['{"temperature": 36.9}', '{"temperature": 37.0}'], reason: 'corte en $corte');
      }
    });

    test('CRLF, comentarios, varias líneas data y campos que no se usan', () {
      final s = SeparadorSse();
      final ev = s.agregar(': hola\r\nevent: x\r\nid: 3\r\ndata: a\r\ndata:b\r\n\r\nretry: 10\n\n');
      expect(ev, ['a\nb']);
    });

    test('el cliente convierte cada evento en una lectura de la incubadora', () async {
      final eventos = [
        'data: {"connected": true, "temperature": 36.9, "setpoint": 37.0, "co2": 41000, "co2_setpoint": 40000, "humidity": 88.5, "valve_open": false, "error": null}\n\n',
        'data: {"connected": true, "temperature": 37.1, "setpoint": 37.0, "co2": null, "error": null}\n\n',
      ];
      final bytes = utf8.encode(eventos.join());
      final (c, a) = clienteFalso((_) => flujo(Stream.fromIterable(trocear(bytes, Random(3)))));
      final l = await c.flujoIncubadora().toList();
      expect(a.pedidos.single.ruta, '/api/temperature/stream');
      expect(l, hasLength(2));
      expect(l[0].temperatura, 36.9);
      expect(l[0].co2Pct, 4.1);
      expect(l[0].valvulaAbierta, isFalse);
      expect(l[1].hayCo2, isFalse);
    });

    test('UTF-8 partido entre trozos (°, ₂)', () async {
      final bytes = utf8.encode('data: {"error": "CO₂ y °C"}\n\n');
      final (c, _) = clienteFalso(
        (_) => flujo(
          Stream.fromIterable([
            for (final b in bytes) [b],
          ]),
        ),
      );
      final l = await c.flujoIncubadora().single;
      expect(l.error, 'CO₂ y °C');
    });

    test('si se corta, reconecta; si el SSE falla, usa GET /api/temperature/status', () async {
      var conexiones = 0;
      final (c, a) = clienteFalso((p) {
        if (p.ruta == '/api/temperature/status') {
          return json({'connected': true, 'temperature': 30.0, 'setpoint': 37.0});
        }
        conexiones++;
        if (conexiones == 2) return sinRed(p); // la segunda vez no conecta
        return flujo(Stream.value(utf8.encode('data: {"connected": true, "temperature": $conexiones.5}\n\n')));
      });
      final esperas = <int>[];
      final lecturas = await vigilarIncubadora(
        c,
        espera: (f) {
          esperas.add(f);
          return Duration.zero;
        },
      ).take(3).toList();
      expect(lecturas.map((l) => l.temperatura), [1.5, 30.0, 3.5]);
      expect(a.a('/api/temperature/status'), hasLength(1));
      // 1 s tras el corte; 2 s tras el fallo (la lectura simple no cuenta
      // como que el SSE volvió).
      expect(esperas, [1, 2]);
    });

    test('un corte a mitad del flujo (HttpException) no detiene la vigilancia', () async {
      var conexiones = 0;
      final (c, _) = clienteFalso((p) {
        conexiones++;
        final ctl = StreamController<List<int>>();
        ctl.add(utf8.encode('data: {"connected": true, "temperature": $conexiones.0}\n\n'));
        ctl.addError(const HttpException('Connection closed while receiving data'));
        ctl.close();
        return flujo(ctl.stream);
      });
      final lecturas = await vigilarIncubadora(c, espera: (_) => Duration.zero).take(3).toList();
      expect(lecturas.map((l) => l.temperatura), [1.0, 2.0, 3.0]);
    });

    test('si no responde nada, avisa el error y sigue intentando con espera creciente', () async {
      final (c, _) = clienteFalso(sinRed, reintentos: 0);
      final esperas = <int>[];
      final errores = <Object>[];
      final sub = vigilarIncubadora(
        c,
        espera: (f) {
          esperas.add(f);
          return Duration.zero;
        },
      ).listen((_) {}, onError: errores.add);
      await pumpEventQueue(times: 100);
      await sub.cancel();
      expect(errores, isNotEmpty);
      expect(errores.first, isA<ErrorDeRed>());
      expect(esperas.take(3), [1, 2, 3]);
      expect(esperaReconexion(1), const Duration(seconds: 1));
      expect(esperaReconexion(4), const Duration(seconds: 8));
      expect(esperaReconexion(20), const Duration(seconds: 30));
    });
  });
}
