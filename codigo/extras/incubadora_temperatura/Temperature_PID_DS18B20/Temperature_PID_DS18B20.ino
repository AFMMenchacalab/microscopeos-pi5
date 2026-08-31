/*
 * =====================================================================
 *  Controlador PID de Temperatura — MicroscopeOS
 * =====================================================================
 *  Lee temperatura con sensor DS18B20 (OneWire).
 *  Controla un calefactor mediante PWM en el pin D3 a través de un
 *  MOSFET de potencia.
 *  Se comunica con la Raspberry Pi por USB/Serial:
 *    - Envía telemetría JSON cada ~300 ms
 *    - Recibe comandos "SET:xx.x\n" para cambiar el setpoint
 *
 *  Conexiones DS18B20:
 *    Datos → D2 (con resistencia pull-up de 4.7kΩ entre datos y VCC)
 *    VCC → 5V | GND → GND
 *  Salida PWM: D3 → compuerta del MOSFET → carga a 12V
 * =====================================================================
 */

#include <OneWire.h>
#include <DallasTemperature.h>

// --- Pin de datos del DS18B20 ---
#define ONEWIRE_PIN 2

// --- Pin PWM para el calefactor ---
int PWM_pin = 3;

OneWire oneWireBus(ONEWIRE_PIN);
DallasTemperature sensors(&oneWireBus);

// --- Variables de temperatura ---
float temperature_read = 0.0;   // Temperatura medida (°C)
float set_temperature  = 37.0;  // Setpoint por defecto (°C)

// --- Variables PID ---
float PID_error      = 0;
float previous_error = 0;
float elapsedTime, Time, timePrev;
int   PID_value      = 0;   // Salida del PID (0–255, va al PWM)

// --- Ganancias del PID ---
float kp = 5.0;    // Proporcional (antes 8.0 — bajado para reducir el empuje inicial)
float ki = 0.02;   // Integral (antes 0.05 — bajado para reducir overshoot por acumulación)
float kd = 3.0;    // Derivativo

float PID_p = 0;
float PID_i = 0;
float PID_d = 0;

// --- Límite de rampa para el PWM ---
// Evita que la salida suba/baje de golpe: por ciclo (300ms), el PWM
// solo puede cambiar como máximo esta cantidad de pasos.
int   PID_value_prev  = 0;
const int MAX_PWM_STEP = 4;   // pasos de PWM permitidos por ciclo (~300ms)


void setup() {
  pinMode(PWM_pin, OUTPUT);

  // Cambia la frecuencia del Timer2 a ~980 Hz para suavizar el PWM
  // en el calefactor (evita ruido audible y mejora respuesta del PID)
  TCCR2B = TCCR2B & B11111000 | 0x03;

  sensors.begin();

  Time = millis();

  Serial.begin(9600);
  Serial.println("READY");   // Señal para que la Pi sepa que el Arduino está listo
}


void loop() {

  // --- Recibir nuevo setpoint desde la Raspberry Pi ---
  // Protocolo: la Pi manda "SET:40.5\n"
  if (Serial.available() > 0) {
    String msg = Serial.readStringUntil('\n');
    msg.trim();
    if (msg.startsWith("SET:")) {
      float new_sp = msg.substring(4).toFloat();
      // Rango seguro: 20–80 °C
      if (new_sp >= 20.0 && new_sp <= 80.0) {
        set_temperature = new_sp;
      }
    }
  }

  // --- Leer temperatura del DS18B20 ---
  sensors.requestTemperatures();
  float t = sensors.getTempCByIndex(0);

  // Si el sensor está desconectado o hay error de lectura, se mantiene
  // el último valor válido y se saltan los cálculos de este ciclo
  if (t == DEVICE_DISCONNECTED_C) {
    delay(300);
    return;
  }
  temperature_read = t;

  // --- Cálculo PID ---
  PID_error = set_temperature - temperature_read;

  // Tiempo transcurrido desde el ciclo anterior (en segundos)
  timePrev    = Time;
  Time        = millis();
  elapsedTime = (Time - timePrev) / 1000.0;

  PID_p = kp * PID_error;

  // Acumulador integral con anti-windup (limita entre 0 y 255)
  PID_i += ki * PID_error * elapsedTime;
  if (PID_i > 255) PID_i = 255;
  if (PID_i < 0)   PID_i = 0;

  if (elapsedTime > 0) {
    PID_d = kd * ((PID_error - previous_error) / elapsedTime);
  }

  // Suma de términos, saturada a rango PWM válido
  PID_value = PID_p + PID_i + PID_d;
  if (PID_value < 0)   PID_value = 0;
  if (PID_value > 255) PID_value = 255;

  // --- Límite de rampa (rate limit) ---
  // Aunque el PID pida un salto grande, el PWM real solo se mueve
  // como máximo MAX_PWM_STEP por ciclo. Esto suaviza la subida inicial
  // y evita el "golpe" de temperatura que luego se pasa y baja.
  if (PID_value > PID_value_prev + MAX_PWM_STEP) {
    PID_value = PID_value_prev + MAX_PWM_STEP;
  } else if (PID_value < PID_value_prev - MAX_PWM_STEP) {
    PID_value = PID_value_prev - MAX_PWM_STEP;
  }
  PID_value_prev = PID_value;

  // Aplicar salida al MOSFET vía PWM
  analogWrite(PWM_pin, (int)PID_value);

  previous_error = PID_error;

  delay(300);   // Periodo de muestreo ~300 ms

  // --- Enviar telemetría JSON a la Raspberry Pi ---
  // Formato: {"temp":36.75,"setpoint":37.0,"pwm":128}
  Serial.print("{\"temp\":");
  Serial.print(temperature_read, 2);
  Serial.print(",\"setpoint\":");
  Serial.print(set_temperature, 1);
  Serial.print(",\"pwm\":");
  Serial.print(PID_value);
  Serial.println("}");
}
