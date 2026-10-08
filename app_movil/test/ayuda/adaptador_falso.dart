import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:microscopeos/api/cliente_api.dart';

/// Un pedido que llegó al "microscopio" falso.
class Pedido {
  Pedido(this.metodo, this.ruta, this.query, this.cuerpo, this.opciones);
  final String metodo;
  final String ruta;
  final Map<String, dynamic> query;
  final Object? cuerpo;
  final RequestOptions opciones;

  Map<String, dynamic> get json => cuerpo is Map ? (cuerpo as Map).cast<String, dynamic>() : const {};

  @override
  String toString() => '$metodo $ruta';
}

typedef Respuesta = FutureOr<ResponseBody> Function(Pedido p);

/// Reemplaza la red de dio: cada pedido se contesta con [responder].
class AdaptadorFalso implements HttpClientAdapter {
  AdaptadorFalso(this.responder);

  Respuesta responder;
  final List<Pedido> pedidos = [];

  List<Pedido> a(String ruta) => pedidos.where((p) => p.ruta == ruta).toList();

  @override
  Future<ResponseBody> fetch(RequestOptions o, Stream<Uint8List>? cuerpo, Future<void>? cancelar) async {
    final p = Pedido(o.method, o.uri.path, o.uri.queryParameters, o.data, o);
    pedidos.add(p);
    return responder(p);
  }

  @override
  void close({bool force = false}) {}
}

ResponseBody json(Object cuerpo, {int codigo = 200}) => ResponseBody.fromString(
  jsonEncode(cuerpo),
  codigo,
  headers: {
    Headers.contentTypeHeader: ['application/json'],
  },
);

ResponseBody texto(String cuerpo, {int codigo = 200}) => ResponseBody.fromString(
  cuerpo,
  codigo,
  headers: {
    Headers.contentTypeHeader: ['text/plain'],
  },
);

ResponseBody bytes(List<int> datos, {int codigo = 200, Map<String, List<String>>? encabezados}) =>
    ResponseBody.fromBytes(
      datos,
      codigo,
      headers: {
        Headers.contentTypeHeader: ['image/jpeg'],
        ...?encabezados,
      },
    );

/// Respuesta en streaming: los trozos llegan uno por uno.
ResponseBody flujo(Stream<List<int>> trozos, {String tipo = 'text/event-stream'}) => ResponseBody(
  trozos.map(Uint8List.fromList),
  200,
  headers: {
    Headers.contentTypeHeader: [tipo],
  },
);

Never sinRed(Pedido p) => throw DioException(requestOptions: p.opciones, type: DioExceptionType.connectionError);

Never tiempoAgotado(Pedido p) => throw DioException(requestOptions: p.opciones, type: DioExceptionType.receiveTimeout);

/// Un cliente que habla con el adaptador falso, sin esperas entre reintentos.
(ClienteMicroscopio, AdaptadorFalso) clienteFalso(Respuesta responder, {int reintentos = 2}) {
  final adaptador = AdaptadorFalso(responder);
  final dio = Dio()..httpClientAdapter = adaptador;
  final c = ClienteMicroscopio(
    Uri.parse('http://192.168.1.50:8000'),
    dio: dio,
    reintentosLectura: reintentos,
    esperaReintento: Duration.zero,
  );
  return (c, adaptador);
}
