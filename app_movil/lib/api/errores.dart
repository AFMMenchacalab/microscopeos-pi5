import '../textos.dart';

/// Todo lo que puede salir mal al hablar con el microscopio, ya traducido
/// a un mensaje que se le puede mostrar tal cual a la persona.
///
/// Las pantallas solo ven estos tipos: nunca un DioException ni un
/// código HTTP. Así, cuando el backend cambie (p. ej. a `/api/v1` con
/// códigos de error de verdad), solo cambia `cliente_api.dart`.
sealed class ErrorApi implements Exception {
  const ErrorApi(this.mensaje);

  /// Listo para mostrar. Los del microscopio ya vienen en español.
  final String mensaje;

  @override
  String toString() => mensaje;
}

/// El microscopio contestó que no pudo hacerlo: `200 + {"error": ...}` o
/// `{"ok": false, "error": ...}`. Ej.: «Timelapse en curso».
class ErrorDelMicroscopio extends ErrorApi {
  const ErrorDelMicroscopio(super.mensaje);
}

/// 423: otra persona tiene el control. [quien] es su nombre tal como lo
/// muestra el microscopio («ana@lab.mx», «Red local (192.168.1.23)»).
class ErrorSinControl extends ErrorApi {
  const ErrorSinControl(super.mensaje, {required this.quien});
  final String quien;
}

/// 409: la cámara está ocupada por un timelapse.
class ErrorOcupadoPorTimelapse extends ErrorApi {
  const ErrorOcupadoPorTimelapse([super.mensaje = Textos.errorOcupadoTimelapse]);
}

/// 404: la ruta no existe en esa versión del microscopio, o la imagen ya
/// no está.
class ErrorNoDisponible extends ErrorApi {
  const ErrorNoDisponible([super.mensaje = Textos.errorNoDisponible]);
}

/// 422: el pedido no pasó la validación del servidor. El detalle de
/// FastAPI viene en inglés: se guarda para el registro, no se muestra.
class ErrorDatosInvalidos extends ErrorApi {
  const ErrorDatosInvalidos(this.detalle) : super(Textos.errorDatosInvalidos);
  final Object? detalle;
}

/// El microscopio está detrás de Cloudflare Access (acceso desde
/// internet) y hay que entrar con el correo: nunca se entró ([vencida]
/// false) o la sesión guardada ya no sirve ([vencida] true).
class ErrorNecesitaLogin extends ErrorApi {
  const ErrorNecesitaLogin({this.vencida = false})
    : super(vencida ? Textos.errorSesionVencida : Textos.errorNecesitaLogin);
  final bool vencida;
}

/// Sin red, el microscopio apagado o tardó demasiado en contestar.
class ErrorDeRed extends ErrorApi {
  const ErrorDeRed({this.tiempoAgotado = false})
    : super(tiempoAgotado ? Textos.errorTiempoAgotado : Textos.errorSinRed);
  final bool tiempoAgotado;
}

/// Cualquier otra cosa (un 500, una respuesta que no se entiende).
class ErrorInesperado extends ErrorApi {
  const ErrorInesperado(super.mensaje, {this.codigo});
  final int? codigo;
}
