/*
 * ==========================================================
 * CONTROL PID DOBLE  —  XIAO ESP32S3
 * - Temperatura (DS18B20 + PWM calefactor)
 * - CO2 (STC31-C + válvula solenoide)
 * - Humedad/temperatura del gas (SHT4x o SHTC3 de la flex del SEK-STC31-C)
 *
 * Adaptado de Control_Incubator_William.ino (Arduino UNO).
 * Cambios (oct-2026): PID del calefactor reajustado con antiwindup y
 * tope de potencia al 80 %; pulso minimo de valvula, un solo pulso por
 * ventana, valvula cerrada si el sensor se satura (WARN:CO2_SATURADO,
 * "co2_sat" en el JSON).
 * Cambios (nov-2026): el SCD30 (tope 4 %) se cambió por un STC31-C en
 * modo "CO2 en aire, 0-40 vol%", compensado en cada medición con la
 * humedad y temperatura del SHT de la flex. El STC31-C da vol%; aquí se
 * pasa a ppm (1 vol% = 10000 ppm) para no cambiar el backend.
 *   Librerías: "Sensirion I2C STC3x" (+ "Sensirion Core") y "arduino-sht".
 *   CO2 con cero corregido (offset en NVS) y promedio de 10 lecturas.
 * El protocolo serial es el mismo:
 *   Entrada:  SET_TEMP:37.0   SET_CO2:40000   ID?
 *             CAL_CO2_AIRE   CAL_CO2?   CAL_CO2_RESET   (cero del STC31)
 *   Salida:   JSON cada 1 s, VALVE_OPEN / VALVE_CLOSED, READY
 *
 * Compilar: placa "XIAO_ESP32S3", USB CDC On Boot: Enabled
 *   arduino-cli: esp32:esp32:XIAO_ESP32S3
 *   OJO: en esta placa la opción "CDCOnBoot=cdc" significa DESACTIVADO
 *   (al revés que en la matriz). Déjala por defecto.
 * ==========================================================
 */

#include <Wire.h>
#include <SensirionI2cStc3x.h>
#include <SHTSensor.h>
#include <Preferences.h>

// Igual que el ejemplo de Sensirion: asegura que NO_ERROR sea 0
#ifdef NO_ERROR
#undef NO_ERROR
#endif
#define NO_ERROR 0

#include <OneWire.h>
#include <DallasTemperature.h>

// ==========================================================
// PINES (número de GPIO; entre paréntesis, la serigrafía)
// ==========================================================

const int HEATER_PIN  = 1;   // (D0) compuerta MOSFET calefactor
const int ONEWIRE_PIN = 2;   // (D1) dato DS18B20, 4.7k a 3V3
const int VALVULA_PIN = 4;   // (D3) compuerta MOSFET solenoide
const int SDA_PIN     = 5;   // (D4) SDA STC31-C + SHT
const int SCL_PIN     = 6;   // (D5) SCL STC31-C + SHT

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
const double SET_CO2_MAX  = 50000; // setpoint hasta 5 % (el STC31-C lee hasta 40 %)

// El SCD30 se saturaba en 40000 ppm y el PID abría la válvula a ciegas.
// El STC31-C en el modo 0-40 vol% llega hasta 400000 ppm; se deja el mismo
// resguardo cerca de su tope. Arriba de esto: válvula cerrada.
const double CO2_SATURADO = 395000;

// Lecturas fuera de esto no son físicas (sensor sin modo de gas, p. ej.
// tras un corte de alimentación): se descartan, no entran al PID.
const float CO2_VOL_MIN = -5.0;
const float CO2_VOL_MAX = 45.0;

// Pulso mínimo de válvula. Con duty de 0.77 % la válvula se abría 77 ms
// cada 10 s: casi no mete gas y gasta la solenoide.
const unsigned long VALVULA_MIN_MS = 300;

const unsigned long CO2_TIMEOUT    = 10000; // sin datos del STC31 -> válvula cerrada
const unsigned long STC31_REINTENTO = 5000; // buscar el STC31 cada 5 s si no está

// ==========================================================
// STC31-C (CO2) + SHT (humedad y temperatura para compensar)
// ==========================================================

// Modo del STC31-C: 0x0013 = CO2 en aire, 0-40 vol% (el de mejor
// exactitud en bajo rango; el STC31-C no tiene modo 0-25 %, ese es del
// STC31 sin -C). Lista en SensirionI2cStc3x.h, setBinaryGas().
const uint16_t STC31_GAS_CO2_AIRE_40 = 0x0013;

// Presión para la compensación por densidad, en mbar (600-1200). El
// sensor supone 1013 (nivel del mar). Monterrey está a ~540 m: atmósfera
// estándar ~950 mbar, 6 % menos; sin esto la lectura sale sesgada.
const uint16_t PRESION_MBAR = 950;

// Cero del STC31: en aire ambiente marcaba -0.413 vol% (media de 106
// lecturas, nov-2026) en vez de ~0.045. A la lectura se le resta el
// offset. Se guarda en la NVS del ESP32 y se rehace con CAL_CO2_AIRE.
const float CO2_OFFSET_DEFAULT = -0.458;   // vol%
const float CO2_AIRE_VOL       = 0.045;    // CO2 de un cuarto, ~450 ppm
const int   CAL_LECTURAS       = 60;       // ~1 min en aire quieto

// Promedio móvil: una lectura sola varía ±0.16 vol% (±1600 ppm); el
// promedio de 10 (~10 s), ±0.03 vol%. El CO2 de la caja cambia en
// minutos, así que el retraso no importa al PID.
const int CO2_PROMEDIO_N = 10;

SensirionI2cStc3x stc31;
SHTSensor sht;        // AUTO_DETECT: SHT4x (0x44) o SHTC3 (0x70)
Preferences prefs;

bool shtOK = false;
bool stcReconfigurar = false;   // tras un error de I2C, volver a fijar el modo

float co2Offset = CO2_OFFSET_DEFAULT;
float co2Buf[CO2_PROMEDIO_N];
int co2BufN = 0;      // lecturas válidas en el búfer (hasta CO2_PROMEDIO_N)
int co2BufI = 0;      // siguiente posición a escribir

// Calibración en curso (CAL_CO2_AIRE): válvula cerrada mientras dura
int calRestantes = 0;
double calSuma = 0;

double CO2_Setpoint = 50000.0;     // 5 % (antes 4 %, tope del SCD30)
double CO2_Output;                 // ms de válvula abierta por ventana

// PID del CO2 (nov-2026; antes PID_v1). Con PID_v1 la integral se llenaba
// mientras el CO2 tardaba en subir (llegó a 68 % de apertura con el
// sensor viendo aire) y al llegar al setpoint seguía metiendo gas: se
// pasaba. Ahora:
//  - Se controla con el CO2 PREVISTO dentro de CO2_ANTICIPO_S, según la
//    pendiente: si viene subiendo rápido, cierra antes de llegar.
//  - Si el previsto ya alcanza el setpoint, la válvula se cierra en ese
//    momento, aunque la ventana tuviera tiempo de apertura.
//  - La integral solo trabaja cerca del setpoint (CO2_BANDA_I) y con tope.
// Kp igual que antes. Ki es el que de verdad aplicaba PID_v1 (0.02 por su
// SampleTime de 0.1 s, llamado una vez por segundo).
const float CO2_Kp         = 0.08;    // ms de válvula por ppm de error
const float CO2_Ki         = 0.002;   // ms por ppm·s
const float CO2_ANTICIPO_S = 30.0;    // s hacia adelante
const float CO2_BANDA_I    = 15000;   // ppm: integral solo a <1.5 % del setpoint
const float CO2_I_MAX      = 3000;    // ms: tope de la integral (30 %)
const int   CO2_PENDIENTE_N = 20;     // pendiente sobre 20 s del promedio

float co2Hist[CO2_PENDIENTE_N];
int co2HistN = 0;
int co2HistI = 0;
float co2Pendiente = 0;   // ppm/s
float co2Previsto  = 0;   // ppm
float co2I = 0;           // integral, ms
bool co2Frenar = false;   // el previsto alcanza el setpoint: válvula cerrada

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

bool scdOK = false;            // (nombre de antes) el STC31 responde
unsigned long lastSCDData = 0;
unsigned long lastSCDRetry = 0;
unsigned long lastSCDPoll = 0;
// El STC31 mide por disparo (bloquea ~110 ms en la librería): una medición
// por segundo, en vez de revisar cada 250 ms como con el SCD30.
const unsigned long scdPollInterval = 1000;

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
// Variables ambientales (SHT de la flex del STC31-C)
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

// Busca el SHT de la flex. Sin él se sigue midiendo CO2, pero el STC31
// usa la última compensación que se le dio (o 0 %RH y su temperatura
// interna si nunca se le dio).
void iniciarSHT()
{
  shtOK = sht.init(Wire);
  if (shtOK)
    enviar(sht.getSensorType() == SHTSensor::SHT4X
           ? "HUM_SENSOR_OK:SHT4x" : "HUM_SENSOR_OK:SHTC3");
}

// El modo de gas se pierde al cortar la alimentación del STC31: se fija
// cada vez que se (re)conecta.
bool iniciarSTC31()
{
  stc31.begin(Wire, STC31_C_I2C_ADDR_29);

  uint32_t productId = 0;
  uint64_t serie = 0;
  if (stc31.getProductId(productId, serie) != NO_ERROR)
    return false;

  if (stc31.setBinaryGas(STC31_GAS_CO2_AIRE_40) != NO_ERROR)
    return false;

  stc31.setPressure(PRESION_MBAR);
  stcReconfigurar = false;
  co2BufN = 0;   // promedio desde cero al (re)conectar
  co2BufI = 0;

  char buf[48];
  snprintf(buf, sizeof(buf), "STC31_ID:0x%08lX", (unsigned long)productId);
  enviar(buf);

  if (!shtOK)
    iniciarSHT();

  return true;
}

// Una medición: compensa con el SHT y lee el STC31. Devuelve false si el
// STC31 no respondió o dio un valor que no es físico.
bool medirCO2()
{
  if (stcReconfigurar)
  {
    if (stc31.setBinaryGas(STC31_GAS_CO2_AIRE_40) != NO_ERROR)
      return false;
    stc31.setPressure(PRESION_MBAR);
    stcReconfigurar = false;
  }

  // Compensación en cada ciclo con el SHT
  if (shtOK)
  {
    if (sht.readSample())
    {
      ambientTemp = sht.getTemperature();
      ambientHum  = sht.getHumidity();

      stc31.setRelativeHumidity(ambientHum);
      if (ambientTemp > 0)            // la librería lo pasa a uint16
        stc31.setTemperature(ambientTemp);
    }
    else
    {
      shtOK = false;
      enviar("WARN:HUM_SENSOR_SIN_DATOS");
    }
  }

  float co2Vol = 0;
  float tSTC = 0;
  if (stc31.measureGasConcentration(co2Vol, tSTC) != NO_ERROR)
  {
    stcReconfigurar = true;
    return false;
  }

  if (co2Vol < CO2_VOL_MIN || co2Vol > CO2_VOL_MAX)
  {
    stcReconfigurar = true;
    return false;
  }

  // Calibración en curso: se promedia la lectura sin corregir
  if (calRestantes > 0)
  {
    calSuma += co2Vol;
    if (--calRestantes == 0)
    {
      co2Offset = calSuma / CAL_LECTURAS - CO2_AIRE_VOL;
      prefs.putFloat("co2_offset", co2Offset);
      co2BufN = 0;   // el promedio viejo traía el offset anterior
      co2BufI = 0;
      reiniciarPIDCO2();
      char buf[48];
      snprintf(buf, sizeof(buf), "CAL_CO2_OK:offset=%.3f", co2Offset);
      enviar(buf);
    }
  }

  // Cero corregido y promedio móvil
  co2Buf[co2BufI] = co2Vol - co2Offset;
  co2BufI = (co2BufI + 1) % CO2_PROMEDIO_N;
  if (co2BufN < CO2_PROMEDIO_N)
    co2BufN++;

  float suma = 0;
  for (int i = 0; i < co2BufN; i++)
    suma += co2Buf[i];

  co2ppm = (suma / co2BufN) * 10000.0;   // 1 vol% = 10000 ppm
  return true;
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

  // ---------- STC31-C + SHT ----------
  // 50 kHz por el cable al GX16. Sin Wire.setTimeOut largo: era por el
  // clock stretching del SCD30; el STC31 no lo usa (la librería espera
  // con delay), así un bus colgado no frena el loop 1 s.
  Wire.begin(SDA_PIN, SCL_PIN, 50000);
  delay(14);   // arranque del sensor (ejemplo de Sensirion)

  prefs.begin("incubadora", false);
  co2Offset = prefs.getFloat("co2_offset", CO2_OFFSET_DEFAULT);
  {
    char buf[40];
    snprintf(buf, sizeof(buf), "CO2_OFFSET:%.3f", co2Offset);
    enviar(buf);
  }

  iniciarSHT();
  if (!shtOK)
    enviar("WARN:HUM_SENSOR_AUSENTE");

  scdOK = iniciarSTC31();
  if (!scdOK)
    enviar("ERROR: STC31 no encontrado");   // no se traba: reintenta solo

  lastSCDData = millis();
  lastSCDRetry = millis();

  reiniciarPIDCO2();

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

  // CAL_CO2_AIRE -> cero del STC31 con el sensor en aire de cuarto,
  // ~1 min sin soplarle. Válvula cerrada mientras dura.
  else if (msg == "CAL_CO2_AIRE")
  {
    calRestantes = CAL_LECTURAS;
    calSuma = 0;
    reiniciarPIDCO2();
    enviar("CAL_CO2_INICIO");
  }

  // CAL_CO2? -> offset en uso;  CAL_CO2_RESET -> volver al de fábrica
  else if (msg == "CAL_CO2?" || msg == "CAL_CO2_RESET")
  {
    if (msg == "CAL_CO2_RESET")
    {
      co2Offset = CO2_OFFSET_DEFAULT;
      prefs.remove("co2_offset");
      co2BufN = 0;
      co2BufI = 0;
    }
    char buf[40];
    snprintf(buf, sizeof(buf), "CO2_OFFSET:%.3f", co2Offset);
    enviar(buf);
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

void reiniciarPIDCO2()
{
  co2I = 0;
  co2HistN = 0;
  co2HistI = 0;
  co2Pendiente = 0;
  co2Frenar = false;
  CO2_Output = 0;
}

// Se llama con cada lectura nueva (una por segundo), con co2ppm ya
// corregido y promediado.
void calcularPIDCO2()
{
  const float dt = scdPollInterval / 1000.0;

  // Pendiente: lectura de ahora contra la de hace CO2_PENDIENTE_N s.
  // Hasta juntar esas lecturas se toma 0.
  if (co2HistN == CO2_PENDIENTE_N)
    co2Pendiente = (co2ppm - co2Hist[co2HistI]) / (CO2_PENDIENTE_N * dt);
  else
    co2Pendiente = 0;

  co2Hist[co2HistI] = co2ppm;
  co2HistI = (co2HistI + 1) % CO2_PENDIENTE_N;
  if (co2HistN < CO2_PENDIENTE_N)
    co2HistN++;

  co2Previsto = co2ppm + co2Pendiente * CO2_ANTICIPO_S;

  float error = CO2_Setpoint - co2Previsto;
  float p = CO2_Kp * error;

  // Integral solo cerca del setpoint (lejos manda la proporcional), y con
  // el mismo antiwindup que el calefactor: no integra si la salida ya
  // está topada en esa dirección.
  float u = p + co2I;
  if (fabs(CO2_Setpoint - co2ppm) < CO2_BANDA_I &&
      (u < WindowSize || error < 0) && (u > 0 || error > 0))
  {
    co2I += CO2_Ki * error * dt;
  }
  if (co2I > CO2_I_MAX) co2I = CO2_I_MAX;
  if (co2I < 0) co2I = 0;

  co2Frenar = (co2Previsto >= CO2_Setpoint);

  float out = co2Frenar ? 0 : p + co2I;
  if (out < 0) out = 0;
  if (out > WindowSize) out = WindowSize;
  CO2_Output = out;
}

void controlarCO2()
{
  unsigned long now = millis();

  // Sensor ausente: reintentar cada 5 s
  if (!scdOK)
  {
    if (now - lastSCDRetry >= STC31_REINTENTO)
    {
      lastSCDRetry = now;

      if (iniciarSTC31())
      {
        scdOK = true;
        lastSCDData = now;
        reiniciarPIDCO2();
        enviar("CO2_SENSOR_OK");
      }
    }
  }
  // Sensor presente: una medición por segundo
  else if (now - lastSCDPoll >= scdPollInterval)
  {
    lastSCDPoll = now;

    // El SHT perdido se vuelve a buscar junto con la medición
    if (!shtOK && now - lastSCDRetry >= STC31_REINTENTO)
    {
      lastSCDRetry = now;
      iniciarSHT();
    }

    if (medirCO2())
    {
      if (co2ppm >= CO2_SATURADO)
      {
        // Lectura saturada: no se puede controlar. Válvula cerrada y el
        // PID en pausa para que la integral no siga creciendo.
        if (!co2Saturado)
        {
          co2Saturado = true;
          reiniciarPIDCO2();
          enviar("WARN:CO2_SATURADO");
        }
        CO2_Output = 0;
      }
      else if (calRestantes > 0)
      {
        // Calibrando (CAL_CO2_AIRE): PID en pausa y válvula cerrada
        CO2_Output = 0;
      }
      else
      {
        if (co2Saturado)
        {
          co2Saturado = false;
          reiniciarPIDCO2();
        }
        calcularPIDCO2();
      }

      lastSCDData = now;
    }

    // Sin datos por mucho tiempo: se considera desconectado
    if (now - lastSCDData > CO2_TIMEOUT)
    {
      scdOK = false;
      co2Saturado = false;   // al reconectar se vuelve a evaluar desde cero
      lastSCDRetry = now;
      reiniciarPIDCO2();
      enviar("ERROR: STC31 sin datos");
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

  // Se cierra de inmediato si el sensor se pierde o se satura, o si el CO2
  // previsto ya llega al setpoint, aunque la ventana tuviera tiempo de
  // apertura.
  valveState = scdOK && !co2Saturado && calRestantes == 0 && !co2Frenar &&
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
