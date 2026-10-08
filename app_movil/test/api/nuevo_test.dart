import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:microscopeos/api/cliente_api.dart';
import 'package:microscopeos/api/direccion.dart';
import 'package:microscopeos/api/errores.dart';
import 'package:microscopeos/api/modelos.dart';
import 'package:microscopeos/features/conexion/login_acceso_page.dart';
import 'package:microscopeos/features/timelapse/estimacion.dart';

import '../ayuda/adaptador_falso.dart';

/// Lo que contesta Cloudflare Access sin sesión (medido el 2026-10-07).
ResponseBody redireccionAcceso() => ResponseBody.fromString(
  '',
  302,
  headers: {
    'location': [
      'https://small-lake-1bcd.cloudflareaccess.com/cdn-cgi/access/login/microscopio.lmimenchacalab.com?kid=x',
    ],
    'www-authenticate': ['Cloudflare-Access resource_metadata="x"'],
  },
);

void main() {
  group('acceso desde internet (Cloudflare Access)', () {
    test('sin sesión: la redirección a la página de login es ErrorNecesitaLogin', () async {
      final (c, _) = clienteFalso((_) => redireccionAcceso());
      await expectLater(
        c.version(probar: true),
        throwsA(isA<ErrorNecesitaLogin>().having((e) => e.vencida, 'vencida', isFalse)),
      );
    });

    test('con sesión: manda la cookie y el token en cada pedido', () async {
      final adaptador = AdaptadorFalso((_) => json({'running': false}));
      final c = ClienteMicroscopio(
        Uri.parse('https://microscopio.lmimenchacalab.com'),
        dio: Dio()..httpClientAdapter = adaptador,
        sesionAcceso: 'jwt.de.prueba',
      );
      await c.estado();
      final h = adaptador.pedidos.single.opciones.headers;
      expect(h['Cookie'], 'CF_Authorization=jwt.de.prueba');
      expect(h['cf-access-token'], 'jwt.de.prueba');
      expect(adaptador.pedidos.single.opciones.followRedirects, isFalse);
    });

    test('con sesión vencida: ErrorNecesitaLogin(vencida)', () async {
      final adaptador = AdaptadorFalso((_) => redireccionAcceso());
      final c = ClienteMicroscopio(
        Uri.parse('https://microscopio.lmimenchacalab.com'),
        dio: Dio()..httpClientAdapter = adaptador,
        sesionAcceso: 'viejo',
        reintentosLectura: 0,
      );
      await expectLater(c.estado(), throwsA(isA<ErrorNecesitaLogin>().having((e) => e.vencida, 'vencida', isTrue)));
    });

    test('también en los flujos (SSE de la incubadora, MJPEG)', () async {
      final (c, _) = clienteFalso((_) => redireccionAcceso());
      await expectLater(c.flujoIncubadora().first, throwsA(isA<ErrorNecesitaLogin>()));
    });

    test('403 con WWW-Authenticate de Access', () {
      expect(ClienteMicroscopio.esPantallaDeAcceso(403, null, 'Cloudflare-Access x'), isTrue);
      expect(ClienteMicroscopio.esPantallaDeAcceso(302, '/cdn-cgi/access/login/x', null), isTrue);
      expect(ClienteMicroscopio.esPantallaDeAcceso(302, '/otra/cosa', null), isFalse);
      expect(ClienteMicroscopio.esPantallaDeAcceso(500, null, null), isFalse);
    });

    test('el login nunca abre la interfaz web (manda órdenes al cargar)', () {
      final base = Uri.parse('https://microscopio.lmimenchacalab.com');
      expect(paginaInicial(base).toString(), 'https://microscopio.lmimenchacalab.com/api/version');
      expect(permitida(Uri.parse('https://small-lake-1bcd.cloudflareaccess.com/cdn-cgi/access/login/x'), base), isTrue);
      expect(
        permitida(Uri.parse('https://microscopio.lmimenchacalab.com/cdn-cgi/access/authorized?x=1'), base),
        isTrue,
      );
      expect(permitida(Uri.parse('https://microscopio.lmimenchacalab.com/api/version'), base), isTrue);
      expect(permitida(Uri.parse('https://microscopio.lmimenchacalab.com/'), base), isFalse);
      expect(permitida(Uri.parse('https://microscopio.lmimenchacalab.com/ui'), base), isFalse);
      final local = Uri.parse('http://10.0.2.2:8001');
      expect(permitida(Uri.parse('http://10.0.2.2:8001/'), local), isFalse);
      expect(permitida(Uri.parse('http://10.0.2.2:8001/cdn-cgi/access/login/x'), local), isTrue);
    });

    test('el dominio sin https:// se completa con https', () {
      expect(
        normalizarDireccion('microscopio.lmimenchacalab.com').toString(),
        'https://microscopio.lmimenchacalab.com',
      );
      expect(normalizarDireccion('192.168.1.50').toString(), 'http://192.168.1.50:8000');
    });
  });

  group('colores de la luz', () {
    test('/light/estado trae los colores de cada matriz', () async {
      final (c, _) = clienteFalso(
        (_) => json({
          'matrices': {
            '0': {
              'encendida': true,
              'modo': 'rheinberg',
              'percent': 50,
              'color_campo': 'ffffff',
              'color_dpc': '00FF00',
              'rheinberg': ['00ff00', 'FF0000'],
            },
          },
        }),
      );
      final m = (await c.estadoLuz())[0]!;
      expect(m.colorRelieve, '00FF00');
      expect(m.rheinberg, ('00FF00', 'FF0000'));
    });

    test('cambiar el color: POST /light/colores', () async {
      final (c, a) = clienteFalso((_) => json({'matrices': {}}));
      await c.fijarColores([0, 1], relieve: '00FF00');
      expect(a.pedidos.single.ruta, '/light/colores');
      expect(a.pedidos.single.json, {
        'camaras': [0, 1],
        'dpc': '00FF00',
      });
      await c.fijarColores([1], campo: 'FF0000');
      expect(a.pedidos.last.json, {
        'camaras': [1],
        'campo': 'FF0000',
      });
    });

    test('Rheinberg manda los dos colores con el modo', () async {
      final (c, a) = clienteFalso((_) => json({'status': 'ok'}));
      await c.fijarLuz(ModoLuz.rheinberg, 70, [0], rheinberg: ('0000FF', 'FF6A00'));
      expect(a.pedidos.single.json, {
        'modo': 'rheinberg',
        'percent': 70,
        'camaras': [0],
        'color_centro': '0000FF',
        'color_anillo': 'FF6A00',
      });
      await c.fijarLuz(ModoLuz.full, 70, [0], rheinberg: ('0000FF', 'FF6A00'));
      expect(a.pedidos.last.json.containsKey('color_centro'), isFalse);
    });
  });

  group('opciones del relieve en el timelapse', () {
    test('van en el pedido solo en modo relieve', () {
      final dpc = PedidoTimelapse(
        modo: ModoFoto.dpc,
        intervaloS: 600,
        duracionS: 3600,
        relieve: const OpcionesRelieve(borrarCrudas: false, fase: true),
      ).aJson();
      expect(dpc['dpc_procesar'], isTrue);
      expect(dpc['dpc_borrar_crudas'], isFalse);
      expect(dpc['dpc_suma'], isTrue);
      expect(dpc['dpc_fase'], isTrue);
      expect(dpc['dpc_jpg'], isTrue);
      final normal = const PedidoTimelapse(modo: ModoFoto.blanco, intervaloS: 600, duracionS: 3600).aJson();
      expect(normal.keys.where((k) => k.startsWith('dpc_')), isEmpty);
    });

    test('el espacio por ciclo es igual a dpc.bytes_por_ciclo del servidor', () {
      // Valores calculados con codigo/MicroscopeOS/core/dpc.py.
      int b(OpcionesRelieve o) => bytesPorCiclo(ModoFoto.dpc, relieve: o);
      expect(b(const OpcionesRelieve()), 28776480);
      expect(b(const OpcionesRelieve(suma: false)), 25745760);
      expect(b(const OpcionesRelieve(fase: true)), 44940320);
      expect(b(const OpcionesRelieve(jpg: false)), 27276480);
      expect(b(const OpcionesRelieve(borrarCrudas: false)), 93431840);
      expect(b(const OpcionesRelieve(suma: false, jpg: false, fase: true, borrarCrudas: false)), 105064960);
      expect(b(const OpcionesRelieve(procesar: false)), 4 * bytesPorFoto);
    });
  });
}
