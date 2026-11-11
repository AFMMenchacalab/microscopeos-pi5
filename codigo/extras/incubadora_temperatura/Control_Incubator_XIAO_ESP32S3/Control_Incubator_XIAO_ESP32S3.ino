/*
 * ==========================================================
 * CONTROL PID DOBLE  —  XIAO ESP32S3
 * - Temperatura (DS18B20 + PWM calefactor)
 * - CO2 (SCD30 + válvula solenoide)
 *
 * Adaptado de Control_Incubator_William.ino (Arduino UNO).
 * Cambios (oct-2026): PID del calefactor reajustado con antiwindup y
 * tope de potencia al 80 %; pulso minimo de valvula, un solo pulso por
 * ventana, valvula cerrada si el SCD30 se satura (WARN:CO2_SATURADO,
 * "co2_sat" en el JSON).
 * El protocolo serial es el mismo:
 *   Entrada:  SET_TEMP:37.0   SET_CO2:40000   ID?
 *   Salida:   JSON cada 1 s, VALVE_OPEN / VALVE_CLOSED, READY
 *
 * Compilar: placa "XIAO_ESP32S3", USB CDC On Boot: Enabled
 *   arduino-cli: esp32:esp32:XIAO_ESP32S3
 *   OJO: en esta placa la opción "CDCOnBoot=cdc" significa DESACTIVADO
 *   (al revés que en la matriz). Déjala por defecto.
 * ==========================================================
 */

#include <Wire.h>
#include <Adafruit_SCD30.h>
#include <PID_v1.h>

#include <OneWire.h>
#include <DallasTemperature.h>

// ==========================================================
// PINES (número de GPIO; entre paréntesis, la serigrafía)
// ==========================================================

const int HEATER_PIN  = 1;   // (D0) compuerta MOSFET calefactor
const int ONEWIRE_PIN = 2;   // (D1) dato DS18B20, 4.7k a 3V3
const int VALVULA_PIN = 4;   // (D3) compuerta MOSFET solenoide
const int SDA_PIN     = 5;   // (D4) SDA SCD30
const int SCL_PIN     = 6;   // (D5) SCL SCD30

// ==========================================================
// PWM calefactor: 1 kHz y 8 bits, igual que el UNO (980 Hz, 0-255),
// así las constantes kp/ki/kd no cambian.
// ==========================================================

const int PWM_FREQ = 1000;
const int PWM_RES  = 8;
const int PWM_CH   = 0;      // solo se usa con el core 2.x

// ==========================================================
// SEGURIDAD
// ==========================================================

const float TEMP_CORTE    = 45.0;  // arriba de esto se apaga el calefactor
const float SET_TEMP_MIN  = 20.0;
const float SET_TEMP_MAX  = 42.0;  // antes 80: la caja es impresa
const double SET_CO2_MIN  = 400;
const double SET_CO2_MAX  = 40000; // tope de medición del SCD30

// El SCD30 mide hasta 40000 ppm: cerca de ahí la lectura se satura y ya no
// se sabe si hay 4 % o 8 %. Con el setpoint en 40000 el PID veía error 0 y
// seguía abriendo la válvula a ciegas. Arriba de esto: válvula cerrada.
const double CO2_SATURADO = 39500;

// Pulso mínimo de válvula. Con duty de 0.77 % la válvula se abría 77 ms
// cada 10 s: casi no mete gas y gasta la solenoide.
const unsigned long VALVULA_MIN_MS = 300;

const unsigned long CO2_TIMEOUT    = 10000; // sin datos del SCD30 -> válvula cerrada
const unsigned long SCD30_REINTENTO = 5000; // buscar el SCD30 cada 5 s si no está

// ==========================================================
// SCD30 (CO2)
// ==========================================================

Adafruit_SCD30 scd30;

double CO2_Setpoint = 40000.0;     // 4 %, el setpoint real (antes 5000)
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

// Cuánto se abre la válvula en la ventana actual. Se fija al empezar cada
// ventana: si se comparaba contra CO2_Output, que cambia a media ventana
// (llega dato cada ~2 s), la válvula podía cerrar y volver a abrir varias
// veces en la misma ventana.
unsigned long valvulaAbiertaMs = 0;
bool co2Saturado = false;

bool scdOK = false;
unsigned long lastSCDData = 0;
unsigned long lastSCDRetry = 0;
unsigned long lastSCDPoll = 0;
const unsigned long scdPollInterval = 250;

// ==========================================================
// DS18B20 (Temperatura)
// ==========================================================

OneWire oneWireBus(ONEWIRE_PIN);
DallasTemperature sensors(&oneWireBus);

float temperature_read = 0.0;
float set_temperature = 37.0;

bool tempOK = false;
bool convPendiente = false;
unsigned long lastTempRequest = 0;

// El UNO esperaba ~750 ms en cada lectura (bloqueante) + 300 ms,
// o sea ~1 s por ciclo. Aquí se pide sin bloquear y se lee después.
const unsigned long tempInterval = 1000;
const unsigned long convTime     = 800;   // 12 bits = 750 ms

// PID temperatura (mismas constantes que el original)

float PID_error = 0;
float previous_error = 0;

float elapsedTime;
float Time;
float timePrev;

// Ajuste oct-2026 con un registro de la caja (calienta ~0.35 °C/min a
// 255, constante de tiempo ~40 min, necesita ~60-70 % para sostener 37).
// Antes kp=8 ki=0.05 kd=3: la integral mandaba, se llenaba hasta 255 al
// calentar desde frío, se pasaba hasta ~3 °C y tardaba más de una hora en
// vaciarse (la caja solo se enfría sola). El experimento de la ferrita
// osciló entre 35.6 y 38.7 °C tres días.
// kd=0: con la lectura cada 1 s y la resolución del DS18B20 (0.0625 °C)
// la derivada era casi puro ruido.
float kp = 40.0;
float ki = 0.03;
float kd = 0.0;

// Tope de potencia de la tira: 80 %. Tarda más en llegar desde frío, pero
// la tira no pasa largos ratos al 100 % (la caja es impresa). Si algún día
// no llega a 37 °C con la caja cerrada, el tope quedó por debajo de lo que
// necesita para sostener: subirlo un poco.
const int PWM_MAX = 204;

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
// Telemetría y comandos
// ==========================================================

unsigned long lastTelemetry = 0;
const unsigned long telemetryInterval = 1000;

char cmdBuf[64];
size_t cmdLen = 0;

// ==========================================================
// PROTOTIPOS
// ==========================================================

void recibirComandos();
void controlarTemperatura();
void controlarCO2();
void enviarTelemetria();

// ==========================================================
// UTILIDADES
// ==========================================================

// Escribe una línea completa de un jalón. Si la Pi no está conectada
// no escribe, para que el control no se frene esperando el USB.
void enviar(const char *linea)
{
  if (Serial)
    Serial.println(linea);
}

void calefactor(int valor)
{
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(HEATER_PIN, valor);
#else
  ledcWrite(PWM_CH, valor);
#endif
}

void apagarCalefactor()
{
  PID_value = 0;
  PID_i = 0;
  calefactor(0);
}

bool iniciarSCD30()
{
  return scd30.begin(SCD30_I2CADDR_DEFAULT, &Wire);
}

// ==========================================================

void setup()
{
  // Salidas en bajo antes que nada (los 10k a GND ya las sostienen al arrancar)
  pinMode(VALVULA_PIN, OUTPUT);
  digitalWrite(VALVULA_PIN, LOW);

#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcAttach(HEATER_PIN, PWM_FREQ, PWM_RES);
#else
  ledcSetup(PWM_CH, PWM_FREQ, PWM_RES);
  ledcAttachPin(HEATER_PIN, PWM_CH);
#endif
  calefactor(0);

  Serial.begin(115200);

  // ---------- DS18B20 ----------
  sensors.begin();
  sensors.setWaitForConversion(false);   // no bloquear 750 ms

  Time = millis();

  // ---------- SCD30 ----------
  // Clock stretching del SCD30: timeout largo. 50 kHz por el cable al GX16.
  Wire.setTimeOut(1000);
  Wire.begin(SDA_PIN, SCL_PIN, 50000);

  scdOK = iniciarSCD30();
  if (!scdOK)
    enviar("ERROR: SCD30 no encontrado");   // ya no se traba: reintenta solo

  lastSCDData = millis();
  lastSCDRetry = millis();

  CO2_PID.SetOutputLimits(0, WindowSize);
  CO2_PID.SetMode(scdOK ? AUTOMATIC : MANUAL);
  CO2_Output = 0;

  windowStartTime = millis();

  enviar("READY");
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
// RECEPCION DE COMANDOS (sin bloquear)
// ==========================================================

void procesarComando(String msg)
{
  msg.trim();

  // SET_TEMP:37.0
  if (msg.startsWith("SET_TEMP:"))
  {
    float new_sp = msg.substring(9).toFloat();

    if (new_sp >= SET_TEMP_MIN && new_sp <= SET_TEMP_MAX)
      set_temperature = new_sp;
  }

  // SET_CO2:40000
  else if (msg.startsWith("SET_CO2:"))
  {
    float new_sp = msg.substring(8).toFloat();

    if (new_sp >= SET_CO2_MIN && new_sp <= SET_CO2_MAX)
      CO2_Setpoint = new_sp;
  }

  // ID?  -> para que el backend reconozca el dispositivo
  else if (msg == "ID?")
  {
    enviar("ID:INCUBADORA");
  }
}

void recibirComandos()
{
  while (Serial.available())
  {
    char c = Serial.read();

    if (c == '\n')
    {
      cmdBuf[cmdLen] = '\0';
      procesarComando(String(cmdBuf));
      cmdLen = 0;
    }
    else if (cmdLen < sizeof(cmdBuf) - 1)
    {
      cmdBuf[cmdLen++] = c;
    }
  }
}

// ==========================================================
// PID TEMPERATURA
// ==========================================================

void controlarTemperatura()
{
  unsigned long now = millis();

  // 1) Pedir conversión (no bloquea)
  if (!convPendiente)
  {
    if (now - lastTempRequest >= tempInterval)
    {
      sensors.requestTemperatures();
      lastTempRequest = now;
      convPendiente = true;
    }
    return;
  }

  // 2) Esperar a que termine
  if (now - lastTempRequest < convTime)
    return;

  convPendiente = false;

  float t = sensors.getTempCByIndex(0);

  // Sensor desconectado (o lectura de arranque, 85 °C): calefactor apagado.
  // En el original el PWM se quedaba en el último valor.
  if (t == DEVICE_DISCONNECTED_C || t == 85.0)
  {
    if (tempOK)
      enviar("ERROR: DS18B20 desconectado");
    tempOK = false;
    apagarCalefactor();
    return;
  }

  temperature_read = t;

  // Recién (re)conectado: arrancar el PID limpio
  if (!tempOK)
  {
    tempOK = true;
    PID_i = 0;
    Time = now;
    previous_error = set_temperature - temperature_read;
    enviar("TEMP_SENSOR_OK");
  }

  // Corte por sobretemperatura
  if (temperature_read > TEMP_CORTE)
  {
    apagarCalefactor();
    previous_error = set_temperature - temperature_read;
    Time = now;
    return;
  }

  PID_error = set_temperature - temperature_read;

  timePrev = Time;
  Time = now;

  elapsedTime = (Time - timePrev) / 1000.0;

  PID_p = kp * PID_error;

  // Antiwindup: solo integra si la salida no está topada, o si integrar
  // la saca del tope. Así la integral no se llena mientras calienta desde
  // frío y no empuja de más al llegar.
  float u_sin_tope = PID_p + PID_i;
  if ((u_sin_tope < PWM_MAX || PID_error < 0) &&
      (u_sin_tope > 0 || PID_error > 0))
  {
    PID_i += ki * PID_error * elapsedTime;
  }

  if (PID_i > PWM_MAX) PID_i = PWM_MAX;
  if (PID_i < 0) PID_i = 0;

  if (elapsedTime > 0)
  {
    PID_d = kd * ((PID_error - previous_error) / elapsedTime);
  }

  PID_value = PID_p + PID_i + PID_d;

  if (PID_value < 0)       PID_value = 0;
  if (PID_value > PWM_MAX) PID_value = PWM_MAX;

  calefactor(PID_value);

  previous_error = PID_error;
}

// ==========================================================
// PID CO2
// ==========================================================

void controlarCO2()
{
  unsigned long now = millis();

  // Sensor ausente: reintentar cada 5 s
  if (!scdOK)
  {
    if (now - lastSCDRetry >= SCD30_REINTENTO)
    {
      lastSCDRetry = now;

      if (iniciarSCD30())
      {
        scdOK = true;
        lastSCDData = now;
        CO2_PID.SetMode(AUTOMATIC);
        enviar("CO2_SENSOR_OK");
      }
    }
  }
  // Sensor presente: revisar si hay dato nuevo (cada 250 ms, no en cada vuelta)
  else if (now - lastSCDPoll >= scdPollInterval)
  {
    lastSCDPoll = now;

    if (scd30.dataReady() && scd30.read())
    {
      co2ppm      = scd30.CO2;
      ambientTemp = scd30.temperature;
      ambientHum  = scd30.relative_humidity;

      CO2_Input = co2ppm;

      if (co2ppm >= CO2_SATURADO)
      {
        // Lectura saturada: no se puede controlar. Válvula cerrada y el
        // PID en pausa para que la integral no siga creciendo.
        if (!co2Saturado)
        {
          co2Saturado = true;
          CO2_PID.SetMode(MANUAL);
          enviar("WARN:CO2_SATURADO");
        }
        CO2_Output = 0;
      }
      else
      {
        if (co2Saturado)
        {
          // Volver a AUTOMATIC reinicia la integral desde CO2_Output = 0
          co2Saturado = false;
          CO2_PID.SetMode(AUTOMATIC);
        }
        CO2_PID.Compute();
      }

      lastSCDData = now;
    }

    // Sin datos por mucho tiempo: se considera desconectado
    if (now - lastSCDData > CO2_TIMEOUT)
    {
      scdOK = false;
      co2Saturado = false;   // al reconectar se vuelve a evaluar desde cero
      lastSCDRetry = now;
      CO2_PID.SetMode(MANUAL);
      CO2_Output = 0;
      enviar("ERROR: SCD30 sin datos");
    }
  }

  // Ventana de la válvula: se evalúa en cada vuelta
  // (en el original solo cuando llegaba dato, cada ~2 s)
  while (now - windowStartTime > WindowSize)
  {
    windowStartTime += WindowSize;
    // Un solo pulso por ventana, fijado aquí; si es muy corto, no se abre.
    valvulaAbiertaMs = (CO2_Output >= VALVULA_MIN_MS)
                       ? (unsigned long)CO2_Output : 0;
  }

  // Se cierra de inmediato si el sensor se pierde o se satura, aunque la
  // ventana todavía tuviera tiempo de apertura.
  valveState = scdOK && !co2Saturado &&
               ((now - windowStartTime) < valvulaAbiertaMs);

  digitalWrite(VALVULA_PIN, valveState);

  if (valveState != previousValveState)
  {
    enviar(valveState ? "VALVE_OPEN" : "VALVE_CLOSED");
    previousValveState = valveState;
  }
}

// ==========================================================
// TELEMETRIA JSON (mismas llaves + temp_ok y co2_ok)
// ==========================================================

void enviarTelemetria()
{
  if (millis() - lastTelemetry < telemetryInterval)
    return;

  lastTelemetry = millis();

  float dutyCycle = (CO2_Output / WindowSize) * 100.0;

  char buf[320];

  snprintf(buf, sizeof(buf),
    "{\"temp\":%.2f,\"temp_set\":%.1f,\"heater_pwm\":%d,"
    "\"co2\":%.2f,\"co2_set\":%.2f,\"co2_pid\":%.2f,\"co2_duty\":%.2f,"
    "\"valve\":%d,\"ambient_temp\":%.2f,\"humidity\":%.2f,"
    "\"temp_ok\":%d,\"co2_ok\":%d,\"co2_sat\":%d}",
    temperature_read, set_temperature, PID_value,
    co2ppm, CO2_Setpoint, CO2_Output, dutyCycle,
    valveState ? 1 : 0, ambientTemp, ambientHum,
    tempOK ? 1 : 0, scdOK ? 1 : 0, co2Saturado ? 1 : 0);

  enviar(buf);
}
