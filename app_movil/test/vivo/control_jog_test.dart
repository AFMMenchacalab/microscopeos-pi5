import 'package:fake_async/fake_async.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:microscopeos/api/errores.dart';
import 'package:microscopeos/features/vivo/control_jog.dart';

import '../ayuda/ordenes_falsas.dart';

void main() {
  test('manda jog enseguida y después cada 250 ms', () {
    fakeAsync((t) {
      final o = OrdenesFalsas();
      final j = ControlJog(o);
      j.iniciar(0, -1);
      t.flushMicrotasks();
      expect(o.registro, ['jog 0 -1']);
      t.elapse(const Duration(milliseconds: 1000));
      expect(o.jogs, 5, reason: '0, 250, 500, 750 y 1000 ms');
      j.detener();
    });
  });

  test('al soltar manda el stop ENSEGUIDA y no más jog', () {
    fakeAsync((t) {
      final o = OrdenesFalsas();
      final j = ControlJog(o);
      j.iniciar(1, 1);
      t.elapse(const Duration(milliseconds: 600));
      j.detener();
      t.flushMicrotasks();
      expect(o.registro.last, 'stop 1');
      final antes = o.registro.length;
      t.elapse(const Duration(seconds: 2));
      expect(o.registro.length, antes);
      expect(j.activo, isFalse);
    });
  });

  test('detener sin movimiento no manda nada', () {
    fakeAsync((t) {
      final o = OrdenesFalsas();
      ControlJog(o).detener();
      t.flushMicrotasks();
      expect(o.registro, isEmpty);
    });
  });

  test('si un jog devuelve error, deja de reenviar, manda el stop y avisa', () {
    fakeAsync((t) {
      final o = OrdenesFalsas();
      Object? avisado;
      final j = ControlJog(o, alFallar: (e) => avisado = e);
      j.iniciar(0, 1);
      t.elapse(const Duration(milliseconds: 300));
      o.fallarJog = const ErrorDelMicroscopio('Timelapse en curso');
      t.elapse(const Duration(milliseconds: 250));
      expect(o.registro.last, 'stop 0');
      expect(avisado, isA<ErrorDelMicroscopio>());
      expect(j.activo, isFalse);
      final antes = o.jogs;
      t.elapse(const Duration(seconds: 2));
      expect(o.jogs, antes, reason: 'no sigue mandando jog');
    });
  });

  test('si se pierde la conexión en medio del jog, también para', () {
    fakeAsync((t) {
      final o = OrdenesFalsas();
      final j = ControlJog(o);
      j.iniciar(0, -1);
      t.elapse(const Duration(milliseconds: 260));
      o.fallarJog = const ErrorDeRed(tiempoAgotado: true);
      t.elapse(const Duration(milliseconds: 250));
      expect(j.activo, isFalse);
      expect(o.stops, greaterThanOrEqualTo(1));
    });
  });

  test('un jog que contesta DESPUÉS del stop provoca otro stop', () {
    fakeAsync((t) {
      final o = OrdenesFalsas()..demora = const Duration(milliseconds: 200);
      final j = ControlJog(o);
      j.iniciar(0, 1);
      t.elapse(const Duration(milliseconds: 50)); // el primer jog sigue en vuelo
      j.detener();
      t.flushMicrotasks();
      expect(o.registro, ['jog 0 1', 'stop 0']);
      t.elapse(const Duration(milliseconds: 300)); // ahora contesta el jog
      expect(o.registro, ['jog 0 1', 'stop 0', 'stop 0']);
    });
  });

  test('con la red lenta no se apilan jogs (nunca dos en vuelo)', () {
    fakeAsync((t) {
      final o = OrdenesFalsas()..demora = const Duration(milliseconds: 600);
      final j = ControlJog(o);
      j.iniciar(0, 1);
      t.elapse(const Duration(milliseconds: 1300));
      // A 0 ms sale uno (contesta a 600), a 750 el siguiente (contesta a 1350).
      expect(o.jogs, 2);
      j.detener();
      t.elapse(const Duration(seconds: 1));
    });
  });

  test('si el stop falla, lo reintenta una vez', () {
    fakeAsync((t) {
      final o = OrdenesFalsas()..fallosStop = 1;
      final j = ControlJog(o);
      j.iniciar(0, 1);
      t.elapse(const Duration(milliseconds: 100));
      j.detener();
      t.elapse(const Duration(milliseconds: 500));
      expect(o.stops, 2);
    });
  });

  test('iniciar otro movimiento para el anterior primero', () {
    fakeAsync((t) {
      final o = OrdenesFalsas();
      final j = ControlJog(o);
      j.iniciar(0, 1);
      t.flushMicrotasks();
      j.iniciar(1, -1);
      t.flushMicrotasks();
      expect(o.registro, ['jog 0 1', 'stop 0', 'jog 1 -1']);
      j.detener();
      t.flushMicrotasks();
    });
  });
}
