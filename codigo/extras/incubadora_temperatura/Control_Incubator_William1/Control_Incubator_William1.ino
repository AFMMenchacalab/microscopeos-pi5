/*
 * ==========================================================
 * CONTROL PID DOBLE
 * - Temperatura (DS18B20 + PWM calefactor)
 * - CO2 (SCD30 + válvula solenoide)
 * ==========================================================
 */

#include <Wire.h>
#include <Adafruit_SCD30.h>
#include <PID_v1.h>

#include <OneWire.h>
#include <DallasTemperature.h>

// ==========================================================
// SCD30 (CO2)
// ==========================================================

Adafruit_SCD30 scd30;

const int VALVULA_PIN = 9;

double CO2_Setpoint = 5000.0;
double CO2_Input;
double CO2_Output;

double CO2_Kp = 0.08;
double CO2_Ki = 0.02;
double CO2_Kd = 0.00;

PID CO2_PID(
  &CO2_Input,
  &CO2_Output,
  &CO2_Setpoint,
  CO2_Kp,
  CO2_Ki,
  CO2_Kd,
  DIRECT
);

const unsigned long WindowSize = 10000;
unsigned long windowStartTime;

bool valveState = false;
bool previousValveState = false;

// ==========================================================
// DS18B20 (Temperatura)
// ==========================================================

const int oneWirePin = 2;
const int PWM_pin = 3;

OneWire oneWireBus(oneWirePin);
DallasTemperature sensors(&oneWireBus);

float temperature_read = 0.0;
float set_temperature = 37.0;

// PID temperatura

float PID_error = 0;
float previous_error = 0;

float elapsedTime;
float Time;
float timePrev;

float kp = 8.0;
float ki = 0.05;
float kd = 3.0;

float PID_p = 0;
float PID_i = 0;
float PID_d = 0;

int PID_value = 0;

// ==========================================================
// Variables ambientales SCD30
// ==========================================================

float ambientTemp = 0;
float ambientHum  = 0;
float co2ppm      = 0;

// ==========================================================
// Temporizadores
// ==========================================================

unsigned long lastTempRead = 0;
unsigned long lastTelemetry = 0;

const unsigned long tempInterval = 300;
const unsigned long telemetryInterval = 1000;

// ==========================================================

void setup()
{
  Serial.begin(115200);

  // ---------- DS18B20 ----------
  sensors.begin();

  pinMode(PWM_pin, OUTPUT);

  // PWM ~980 Hz
  TCCR2B = TCCR2B & B11111000 | 0x03;

  Time = millis();

  // ---------- SCD30 ----------

  if (!scd30.begin())
  {
    Serial.println("ERROR: SCD30 no encontrado");
    while (1);
  }

  pinMode(VALVULA_PIN, OUTPUT);
  digitalWrite(VALVULA_PIN, LOW);

  CO2_PID.SetOutputLimits(0, WindowSize);
  CO2_PID.SetMode(AUTOMATIC);

  windowStartTime = millis();

  Serial.println("READY");
}

// ==========================================================

void loop()
{
  recibirComandos();

  controlarTemperatura();

  controlarCO2();

  enviarTelemetria();
}

// ==========================================================
// RECEPCION DE COMANDOS
// ==========================================================

void recibirComandos()
{
  if (Serial.available())
  {
    String msg = Serial.readStringUntil('\n');
    msg.trim();

    // SET_TEMP:40.5

    if (msg.startsWith("SET_TEMP:"))
    {
      float new_sp = msg.substring(9).toFloat();

      if (new_sp >= 20.0 && new_sp <= 80.0)
      {
        set_temperature = new_sp;
      }
    }

    // SET_CO2:5000

    if (msg.startsWith("SET_CO2:"))
    {
      float new_sp = msg.substring(8).toFloat();

      if (new_sp >= 400 && new_sp <= 20000)
      {
        CO2_Setpoint = new_sp;
      }
    }
  }
}

// ==========================================================
// PID TEMPERATURA
// ==========================================================

void controlarTemperatura()
{
  if (millis() - lastTempRead < tempInterval)
    return;

  lastTempRead = millis();

  sensors.requestTemperatures();

  temperature_read =
    sensors.getTempCByIndex(0);

  if (temperature_read == DEVICE_DISCONNECTED_C)
    return;

  PID_error = set_temperature - temperature_read;

  timePrev = Time;
  Time = millis();

  elapsedTime =
    (Time - timePrev) / 1000.0;

  PID_p = kp * PID_error;

  PID_i += ki * PID_error * elapsedTime;

  if (PID_i > 255) PID_i = 255;
  if (PID_i < 0) PID_i = 0;

  if (elapsedTime > 0)
  {
    PID_d =
      kd *
      ((PID_error - previous_error)
      / elapsedTime);
  }

  PID_value =
    PID_p + PID_i + PID_d;

  if (PID_value < 0)
    PID_value = 0;

  if (PID_value > 255)
    PID_value = 255;

  analogWrite(PWM_pin, PID_value);

  previous_error = PID_error;
}

// ==========================================================
// PID CO2
// ==========================================================

void controlarCO2()
{
  if (!scd30.dataReady())
    return;

  if (!scd30.read())
    return;

  co2ppm      = scd30.CO2;
  ambientTemp = scd30.temperature;
  ambientHum  = scd30.relative_humidity;

  CO2_Input = co2ppm;

  CO2_PID.Compute();

  unsigned long now = millis();

  if ((now - windowStartTime) > WindowSize)
  {
    windowStartTime += WindowSize;
  }

  valveState =
    ((now - windowStartTime)
     < CO2_Output);

  digitalWrite(
    VALVULA_PIN,
    valveState
  );

  if (valveState != previousValveState)
  {
    if (valveState)
      Serial.println("VALVE_OPEN");
    else
      Serial.println("VALVE_CLOSED");

    previousValveState = valveState;
  }
}

// ==========================================================
// TELEMETRIA JSON
// ==========================================================

void enviarTelemetria()
{
  if (millis() - lastTelemetry
      < telemetryInterval)
    return;

  lastTelemetry = millis();

  float dutyCycle =
    (CO2_Output / WindowSize) * 100.0;

  Serial.print("{");

  Serial.print("\"temp\":");
  Serial.print(temperature_read, 2);

  Serial.print(",\"temp_set\":");
  Serial.print(set_temperature, 1);

  Serial.print(",\"heater_pwm\":");
  Serial.print(PID_value);

  Serial.print(",\"co2\":");
  Serial.print(co2ppm);

  Serial.print(",\"co2_set\":");
  Serial.print(CO2_Setpoint);

  Serial.print(",\"co2_pid\":");
  Serial.print(CO2_Output);

  Serial.print(",\"co2_duty\":");
  Serial.print(dutyCycle);

  Serial.print(",\"valve\":");
  Serial.print(valveState ? 1 : 0);

  Serial.print(",\"ambient_temp\":");
  Serial.print(ambientTemp, 2);

  Serial.print(",\"humidity\":");
  Serial.print(ambientHum, 2);

  Serial.println("}");
}