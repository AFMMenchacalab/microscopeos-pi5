import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:microscopeos/api/cliente_api.dart';
import 'package:microscopeos/api/direccion.dart';
import 'package:microscopeos/api/errores.dart';
import 'package:microscopeos/api/modelos.dart';
import 'package:microscopeos/textos.dart';

import '../ayuda/adaptador_falso.dart';

void main() {
  group('errores en el cuerpo con 200 OK', () {
    test('{"error": ...} se convierte en un error con el mensaje tal cual', () async {
      final (c, _) = clienteFalso((_) => json({'error': 'Timelapse en curso'}));
      await expectLater(
        c.iniciarVivo(0),
        throwsA(isA<ErrorDelMicroscopio>().having((e) => e.mensaje, 'mensaje', 'Timelapse en curso')),
      );
    });

    test('{"error": ""} o null no es un error', () async {
      final (c, _) = clienteFalso((_) => json({'status': 'live', 'error': null}));
      await c.iniciarVivo(0);
      final (c2, _) = clienteFalso((_) => json({'status': 'live', 'error': ''}));
      await c2.iniciarVivo(0);
    });

    test('{"ok": false, "error": ...} es un error', () async {
      final (c, _) = clienteFalso((_) => json({'ok': false, 'error': 'El CO₂ tiene que estar entre 400 y 100000 ppm'}));
      await expectLater(
        c.fijarCo2(10),
        throwsA(isA<ErrorDelMicroscopio>().having((e) => e.mensaje, 'mensaje', contains('400 y 100000'))),
      );
    });

    test('{"ok": false, "error": null} usa un mensaje propio', () async {
      final (c, _) = clienteFalso((_) => json({'ok': false, 'error': null}));
      await expectLater(
        c.fijarTemperatura(90),
        throwsA(isA<ErrorDelMicroscopio>().having((e) => e.mensaje, 'mensaje', Textos.errorTemperaturaRango)),
      );
    });

    test('{"ok": true, "error": "viejo"} es un éxito', () async {
      final (c, _) = clienteFalso((_) => json({'ok': true, 'error': 'Arduino no conectado'}));
      await c.fijarTemperatura(37);
    });

    test('en el estado de la incubadora, "error" es un dato y no un fallo', () async {
      final (c, _) = clienteFalso(
        (_) => json({
          'connected': false,
          'temperature': null,
          'setpoint': 37.0,
          'error': 'Arduino no encontrado',
          'co2': null,
        }),
      );
      final l = await c.incubadora();
      expect(l.conectada, isFalse);
      expect(l.error, 'Arduino no encontrado');
    });

    test('foco sin motores: {"error": ..., "motores": {}} devuelve vacío', () async {
      final (c, _) = clienteFalso((_) => json({'error': 'Sin motores de enfoque', 'motores': {}}));
      expect(await c.estadoFoco(), isEmpty);
    });

    test('/api/version con {"git": false, "error": ...} sigue siendo un microscopio', () async {
      final (c, _) = clienteFalso((_) => json({'git': false, 'error': 'no es un repo'}));
      expect((await c.version()).git, isFalse);
    });

    test('un JSON que no es de MicroscopeOS no se acepta como microscopio', () async {
      final (c, _) = clienteFalso((_) => json({'hola': 'mundo'}));
      await expectLater(c.version(probar: true), throwsA(isA<ErrorInesperado>()));
    });
  });

  group('códigos HTTP', () {
    test('423: otra persona tiene el control', () async {
      final (c, _) = clienteFalso(
        (_) => json({
          'error': 'Ahora controla el microscopio ana@lab.mx. Toca «Tomar el control» si lo necesitas.',
          'control': 'ana@lab.mx',
        }, codigo: 423),
      );
      await expectLater(
        c.apagarLuz([0]),
        throwsA(
          isA<ErrorSinControl>()
              .having((e) => e.quien, 'quien', 'ana@lab.mx')
              .having((e) => e.mensaje, 'mensaje', contains('Tomar el control')),
        ),
      );
    });

    test('409: la cámara está ocupada por un timelapse', () async {
      final (c, _) = clienteFalso((_) => texto('', codigo: 409), reintentos: 0);
      await expectLater(c.bytes('/preview/0'), throwsA(isA<ErrorOcupadoPorTimelapse>()));
    });

    test('404 en el CO₂: esta versión del microscopio no lo permite', () async {
      final (c, _) = clienteFalso((_) => json({'detail': 'Not Found'}, codigo: 404));
      await expectLater(
        c.fijarCo2(40000),
        throwsA(isA<ErrorNoDisponible>().having((e) => e.mensaje, 'mensaje', Textos.errorCo2NoSoportado)),
      );
    });

    test('404 en una imagen', () async {
      final (c, _) = clienteFalso((_) => texto('', codigo: 404));
      await expectLater(c.vistaTimelapse(0), throwsA(isA<ErrorNoDisponible>()));
    });

    test('422: datos inválidos, con el detalle de FastAPI guardado', () async {
      final detalle = [
        {
          'type': 'int_parsing',
          'loc': ['body', 'motor'],
          'msg': 'Input should be a valid integer',
        },
      ];
      final (c, _) = clienteFalso((_) => json({'detail': detalle}, codigo: 422));
      await expectLater(
        c.moverFoco(0, 1, 5),
        throwsA(
          isA<ErrorDatosInvalidos>()
              .having((e) => e.mensaje, 'mensaje', Textos.errorDatosInvalidos)
              .having((e) => e.detalle, 'detalle', detalle),
        ),
      );
    });

    test('500 con texto plano (rutas de imágenes)', () async {
      final (c, _) = clienteFalso((_) => texto('no se pudo armar la vista: boom', codigo: 500));
      await expectLater(
        c.paraCompartir('exp', 'cam0/a.tif'),
        throwsA(isA<ErrorInesperado>().having((e) => e.codigo, 'codigo', 500)),
      );
    });
  });

  group('sin red', () {
    test('lectura: reintenta y al final da ErrorDeRed', () async {
      final (c, a) = clienteFalso(sinRed, reintentos: 2);
      await expectLater(c.estado(), throwsA(isA<ErrorDeRed>()));
      expect(a.pedidos, hasLength(3), reason: '1 intento + 2 reintentos');
    });

    test('lectura: si vuelve la red en un reintento, funciona', () async {
      var n = 0;
      final (c, a) = clienteFalso((p) => ++n < 2 ? sinRed(p) : json({'running': false, 'ciclo': 0}));
      expect((await c.estado()).corriendo, isFalse);
      expect(a.pedidos, hasLength(2));
    });

    test('escritura: NUNCA se reintenta (una foto o un paso no se repite)', () async {
      final (c, a) = clienteFalso(sinRed, reintentos: 5);
      await expectLater(c.capturar(cam: 0, modo: ModoFoto.blanco), throwsA(isA<ErrorDeRed>()));
      await expectLater(c.moverFoco(0, 1, 5), throwsA(isA<ErrorDeRed>()));
      expect(a.pedidos, hasLength(2));
    });

    test('tiempo agotado', () async {
      final (c, _) = clienteFalso(tiempoAgotado, reintentos: 0);
      await expectLater(c.estado(), throwsA(isA<ErrorDeRed>().having((e) => e.tiempoAgotado, 'tiempoAgotado', true)));
    });
  });

  group('forma de los pedidos (como api.py)', () {
    test('foto: el nombre va en la query aunque sea POST', () async {
      final (c, a) = clienteFalso(
        (_) => json({
          'saved': ['cam0/2026-10-07_20-11-34.tif'],
          'experimento': '2026-10-07_Muestra_B',
          'nombre': 'Muestra B',
        }),
      );
      final r = await c.capturar(cam: 0, modo: ModoFoto.dpc, nombre: ' Muestra B ');
      expect(a.pedidos.single.metodo, 'POST');
      expect(a.pedidos.single.ruta, '/capture/0/dpc');
      expect(a.pedidos.single.query, {'nombre': 'Muestra B'});
      expect(r.nombre, 'Muestra B');
      await c.capturar(modo: ModoFoto.blanco);
      expect(a.pedidos.last.ruta, '/capture/both/blanco');
    });

    test('jog, stop y paso fijo', () async {
      final (c, a) = clienteFalso((_) => json({'status': 'ok', 'posicion_um': -2.5}));
      expect(await c.jog(1, -1), -2.5);
      expect(a.pedidos.last.ruta, '/api/focus/jog');
      expect(a.pedidos.last.json, {'motor': 1, 'direction': -1, 'velocidad': 0.003, 'watchdog': 1.5});
      await c.pararJog(1);
      expect(a.pedidos.last.ruta, '/api/focus/jog/stop');
      expect(a.pedidos.last.json, {'motor': 1});
      await c.moverFoco(0, 1, 5);
      expect(a.pedidos.last.json, {'motor': 0, 'direction': 1, 'um': 5.0});
    });

    test('autofoco: cuerpo y espera de al menos 90 s', () async {
      final (c, a) = clienteFalso((_) => json({'encontrado': true, 'desplazamiento_um': -3.2, 'segundos': 21.5}));
      final r = await c.autofoco(0);
      expect(a.pedidos.single.json, {'camera': 0, 'metodo': 'auto', 'rango_um': 40.0, 'rango_max_um': 200.0});
      expect(a.pedidos.single.opciones.receiveTimeout, greaterThanOrEqualTo(const Duration(seconds: 90)));
      expect(r.desplazamientoUm, -3.2);
    });

    test('luz', () async {
      final (c, a) = clienteFalso((_) => json({'status': 'ok'}));
      await c.fijarLuz(ModoLuz.left, 130, [0, 1]);
      expect(a.pedidos.last.json, {
        'modo': 'left',
        'percent': 100,
        'camaras': [0, 1],
      });
      await c.apagarLuz([1]);
      expect(a.pedidos.last.json, {
        'camaras': [1],
      });
    });

    test('timelapse con hora de término', () async {
      final (c, a) = clienteFalso((_) => json({'status': 'started'}));
      final fin = DateTime.now().add(const Duration(hours: 5));
      await c.iniciarTimelapse(PedidoTimelapse(modo: ModoFoto.dpc, intervaloS: 600, fin: fin, nombre: 'X'));
      final j = a.pedidos.single.json;
      expect(j['fin'], matches(RegExp(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$')));
      expect(j['modo'], 'dpc');
      expect(j['interval'], 600);
      expect(j['camaras'], [0, 1]);
      expect(j['autofocus'], isTrue);
    });

    test('las rutas de las fotos se codifican por partes', () {
      final (c, _) = clienteFalso((_) => json({}));
      expect(
        c.rutaMiniatura('2026-10-05_1710_Celulas', 'cam0/0001_2026 x.tif', tamano: 160),
        '/api/exp/2026-10-05_1710_Celulas/mini/cam0/0001_2026%20x.tif?size=160',
      );
    });
  });

  group('modelos', () {
    test('/status en pausa: se puede mirar y enfocar, pero no sacar fotos', () {
      final e = EstadoGeneral.desdeJson({
        'running': true,
        'ciclo': 3,
        'timelapse': {
          'nombre': 'TL',
          'pausado': true,
          'camaras': [0, 1],
          'ultimas': {
            '0': {'ciclo': 3, 'hora': '2026-10-07T20:12:31', 'dpc': false},
          },
        },
      });
      expect(e.enPausa, isTrue);
      expect(e.bloqueaControles, isFalse);
      expect(e.bloqueaFotos, isTrue);
      expect(e.timelapse!.ultimas[0]!.ciclo, 3);
    });

    test('incubadora: mismas tolerancias que la web', () {
      LecturaIncubadora l(double t, double co2) =>
          LecturaIncubadora(temperatura: t, temperaturaPedida: 37, co2Ppm: co2, co2PedidoPpm: 40000);
      expect(l(37.4, 40000).semaforoTemperatura, Semaforo.bien);
      expect(l(38.5, 40000).semaforoTemperatura, Semaforo.aviso);
      expect(l(39.5, 40000).semaforoTemperatura, Semaforo.mal);
      expect(l(37, 43000).semaforoCo2, Semaforo.bien);
      expect(l(37, 47000).semaforoCo2, Semaforo.aviso);
      expect(l(37, 52000).semaforoCo2, Semaforo.mal);
      expect(l(37, 40000).co2Pct, 4.0);
    });

    test('nombre corto de quien controla', () {
      expect(nombreCorto('Red local (192.168.1.23)'), '192.168.1.23');
      expect(nombreCorto('ana@lab.mx'), 'ana');
    });
  });

  group('dirección del microscopio', () {
    test('IP sola: http y puerto 8000', () {
      expect(normalizarDireccion('192.168.1.50').toString(), 'http://192.168.1.50:8000');
      expect(normalizarDireccion(' http://10.0.2.2:8000/ ').toString(), 'http://10.0.2.2:8000');
    });

    test('HTTP sin cifrar solo en la red local', () {
      expect(normalizarDireccion('http://8.8.8.8'), isNull);
      expect(normalizarDireccion('8.8.8.8').toString(), 'https://8.8.8.8', reason: 'fuera de la red local, HTTPS');
      expect(normalizarDireccion('http://microscopio.lmimenchacalab.com'), isNull);
      expect(
        normalizarDireccion('https://microscopio.lmimenchacalab.com').toString(),
        'https://microscopio.lmimenchacalab.com',
      );
      expect(esRedLocal('172.20.0.4'), isTrue);
      expect(esRedLocal('172.32.0.4'), isFalse);
      expect(esRedLocal('microscopio.local'), isTrue);
    });

    test('basura', () {
      expect(normalizarDireccion(''), isNull);
      expect(normalizarDireccion('ftp://192.168.1.2'), isNull);
    });
  });

  test('ClienteMicroscopio interpreta códigos aunque dio los marque como error', () async {
    // Por si alguien cambia validateStatus: el 423 tiene que seguir siendo ErrorSinControl.
    final adaptador = AdaptadorFalso((_) => json({'error': 'x', 'control': 'beto'}, codigo: 423));
    final dio = Dio()..httpClientAdapter = adaptador;
    final c = ClienteMicroscopio(Uri.parse('http://10.0.0.2:8000'), dio: dio);
    await expectLater(c.apagarLuz([0]), throwsA(isA<ErrorSinControl>()));
  });
}
