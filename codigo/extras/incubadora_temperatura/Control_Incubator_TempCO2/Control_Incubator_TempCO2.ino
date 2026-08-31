/*
 * =====================================================================
 *  Controlador PID Doble — MicroscopeOS
 * =====================================================================
 *  - Temperatura: DS18B20 + PWM calefactor (D3)
 *  - CO2: SCD30 + válvula solenoide (D9)
 *
 *  Protocolo serial (9600 baud, compatible con temperature_controller.py):
 *    Comandos recibidos:
 *      SET:xx.x\n          → setpoint de temperatura (°C)
 *      SET_CO2:xxxxx\n     → setpoint de CO2 (ppm)
 *    Telemetría enviada (JSON, cada ~1s):
 *      {"temp":..,"setpoint":..,"pwm":..,
 *       "co2":..,"co2_set":..,"co2_duty":..,"valve":..,
 *       "ambient_temp":..,"humidity":..}
 *
 *  Conexiones:
 *    DS18B20   → D2 (pull-up 4.7kΩ entre datos y VCC)
 *    Calefactor→ D3 (PWM → MOSFET)
 *    SCD30     → I2C (SDA/SCL)
 *    Válvula   → D9 (a través de driver/relevador correspondiente)
 * =====================================================================
 */

#include <Wire.h>
#include <Adafruit_SCD30.h>
#include <PID_v1.h>
#include <OneWire.h>
#include <DallasTemperature.h>

// ==========================================================
// DS18B20 (Temperatura) — igual al sketch ya afinado
// ==========================================================

#define ONEWIRE_PIN 2
int PWM_pin = 3;

OneWire oneWireBus(ONEWIRE_PIN);
DallasTemperature sensors(&oneWireBus);

float temperature_read = 0.0;
float set_temperature  = 37.0;

float PID_error      = 0;
float previous_error = 0;
float elapsedTime, Time, timePrev;
int   PID_value      = 0;

// Ganancias ya afinadas para evitar overshoot
float kp = 5.0;
float ki = 0.02;
float kd = 3.0;

float PID_p = 0;
float PID_i = 0;
float PID_d = 0;

// Límite de rampa del PWM (evita subida/bajada de golpe)
int   PID_value_prev  = 0;
const int MAX_PWM_STEP = 4;   // pasos de PWM por ciclo (~300ms)

unsigned long lastTempRead = 0;
const unsigned long tempInterval = 300;

// ==========================================================
// SCD30 (CO2) + válvula solenoide
// ==========================================================

Adafruit_SCD30 scd30;
bool scd30_ok = false;   // si no se encuentra el sensor, no se bloquea todo el sistema

const int VALVULA_PIN = 9;

double CO2_Setpoint = 40000.0;   // 4% CO2 — target biológico real
double CO2_Input    = 0;
double CO2_Output   = 0;

double CO2_Kp = 0.08;
double CO2_Ki = 0.02;
double CO2_Kd = 0.00;

PID CO2_PID(&CO2_Input, &CO2_Output, &CO2_Setpoint, CO2_Kp, CO2_Ki, CO2_Kd, DIRECT);

const unsigned long WindowSize = 10000;   // ventana de PWM lento para la válvula (10s)
unsigned long windowStartTime;

bool valveState         = false;
bool previousValveState = false;

float ambientTemp = 0;
float ambientHum  = 0;
float co2ppm      = 0;

// ==========================================================
// Telemetría
// ==========================================================

unsigned long lastTelemetry = 0;
const unsigned long telemetryInterval = 1000;


void setup() {
  Serial.begin(9600);

  // ---------- DS18B20 ----------
  sensors.begin();
  pinMode(PWM_pin, OUTPUT);
  TCCR2B = TCCR2B & B11111000 | 0x03;   // PWM ~980 Hz
  Time = millis();

  // ---------- SCD30 ----------
  Wire.begin();
  if (scd30.begin()) {
    scd30_ok = true;
  } else {
    Serial.println("WARN:SCD30_NOT_FOUND");
    // No se cuelga el programa: la temperatura sigue funcionando
    // aunque el sensor de CO2 no responda.
  }

  pinMode(VALVULA_PIN, OUTPUT);
  digitalWrite(VALVULA_PIN, LOW);

  CO2_PID.SetOutputLimits(0, WindowSize);
  CO2_PID.SetMode(AUTOMATIC);
  windowStartTime = millis();

  Serial.println("READY");
}


void loop() {
  recibirComandos();
  controlarTemperatura();
  if (scd30_ok) {
    controlarCO2();
  }
  enviarTelemetria();
}


// ==========================================================
// RECEPCION DE COMANDOS
// ==========================================================
void recibirComandos() {
  if (Serial.available() > 0) {
    String msg = Serial.readStringUntil('\n');
    msg.trim();

    // Temperatura — protocolo original: "SET:37.5"
    if (msg.startsWith("SET:")) {
      float new_sp = msg.substring(4).toFloat();
      if (new_sp >= 20.0 && new_sp <= 80.0) {
        set_temperature = new_sp;
      }
    }

    // CO2 — nuevo: "SET_CO2:40000"
    else if (msg.startsWith("SET_CO2:")) {
      float new_sp = msg.substring(8).toFloat();
      if (new_sp >= 400 && new_sp <= 100000) {
        CO2_Setpoint = new_sp;
      }
    }
  }
}


// ==========================================================
// PID TEMPERATURA (con rate limiter)
// ==========================================================
void controlarTemperatura() {
  if (millis() - lastTempRead < tempInterval) return;
  lastTempRead = millis();

  sensors.requestTemperatures();
  float t = sensors.getTempCByIndex(0);
  if (t == DEVICE_DISCONNECTED_C) return;
  temperature_read = t;

  PID_error = set_temperature - temperature_read;

  timePrev    = Time;
  Time        = millis();
  elapsedTime = (Time - timePrev) / 1000.0;

  PID_p = kp * PID_error;

  PID_i += ki * PID_error * elapsedTime;
  if (PID_i > 255) PID_i = 255;
  if (PID_i < 0)   PID_i = 0;

  if (elapsedTime > 0) {
    PID_d = kd * ((PID_error - previous_error) / elapsedTime);
  }

  PID_value = PID_p + PID_i + PID_d;
  if (PID_value < 0)   PID_value = 0;
  if (PID_value > 255) PID_value = 255;

  // Límite de rampa: evita saltos bruscos de PWM
  if (PID_value > PID_value_prev + MAX_PWM_STEP) {
    PID_value = PID_value_prev + MAX_PWM_STEP;
  } else if (PID_value < PID_value_prev - MAX_PWM_STEP) {
    PID_value = PID_value_prev - MAX_PWM_STEP;
  }
  PID_value_prev = PID_value;

  analogWrite(PWM_pin, PID_value);
  previous_error = PID_error;
}


// ==========================================================
// PID CO2 + VÁLVULA SOLENOIDE
// ==========================================================
void controlarCO2() {
  if (!scd30.dataReady()) return;
  if (!scd30.read())      return;

  co2ppm      = scd30.CO2;
  ambientTemp = scd30.temperature;
  ambientHum  = scd30.relative_humidity;

  CO2_Input = co2ppm;
  CO2_PID.Compute();

  unsigned long now = millis();
  if ((now - windowStartTime) > WindowSize) {
    windowStartTime += WindowSize;
  }

  valveState = ((now - windowStartTime) < CO2_Output);
  digitalWrite(VALVULA_PIN, valveState);

  if (valveState != previousValveState) {
    Serial.println(valveState ? "VALVE_OPEN" : "VALVE_CLOSED");
    previousValveState = valveState;
  }
}


// ==========================================================
// TELEMETRIA JSON
// ==========================================================
void enviarTelemetria() {
  if (millis() - lastTelemetry < telemetryInterval) return;
  lastTelemetry = millis();

  float dutyCycle = (CO2_Output / WindowSize) * 100.0;

  Serial.print("{\"temp\":");
  Serial.print(temperature_read, 2);

  Serial.print(",\"setpoint\":");
  Serial.print(set_temperature, 1);

  Serial.print(",\"pwm\":");
  Serial.print(PID_value);

  Serial.print(",\"co2\":");
  Serial.print(co2ppm, 1);

  Serial.print(",\"co2_set\":");
  Serial.print(CO2_Setpoint, 0);

  Serial.print(",\"co2_duty\":");
  Serial.print(dutyCycle, 1);

  Serial.print(",\"valve\":");
  Serial.print(valveState ? 1 : 0);

  Serial.print(",\"ambient_temp\":");
  Serial.print(ambientTemp, 2);

  Serial.print(",\"humidity\":");
  Serial.print(ambientHum, 2);

  Serial.println("}");
}
