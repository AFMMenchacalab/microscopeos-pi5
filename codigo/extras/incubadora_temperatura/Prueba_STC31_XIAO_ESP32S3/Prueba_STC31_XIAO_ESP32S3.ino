/*
 * ==========================================================
 * PRUEBA STC31-C + SHT  —  XIAO ESP32S3 de la incubadora
 * - Calefactor y válvula APAGADOS todo el tiempo.
 * - Escáner I2C al arrancar y cada 30 s.
 * - Cada 1 s: SHT (T, RH), STC31-C crudo y compensado (vol% y ppm).
 *
 * Compilar: esp32:esp32:XIAO_ESP32S3 (opciones por defecto)
 * Librerías: "Sensirion I2C STC3x" (+ "Sensirion Core"), "arduino-sht"
 * ==========================================================
 */

#include <Wire.h>
#include <SensirionI2cStc3x.h>
#include <SHTSensor.h>

#ifdef NO_ERROR
#undef NO_ERROR
#endif
#define NO_ERROR 0

const int HEATER_PIN  = 1;   // (D0)
const int VALVULA_PIN = 4;   // (D3)
const int SDA_PIN     = 5;   // (D4)
const int SCL_PIN     = 6;   // (D5)

const uint16_t STC31_GAS_CO2_AIRE_40 = 0x0013;
const uint16_t PRESION_MBAR = 950;   // Monterrey, ~540 m

SensirionI2cStc3x stc31;
SHTSensor sht;

bool stcOK = false;
bool shtOK = false;
unsigned long lastScan = 0;
char errMsg[64];

void escanearI2C()
{
  Serial.print("I2C_SCAN:");
  int n = 0;
  for (uint8_t a = 1; a < 127; a++)
  {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0)
    {
      Serial.printf(" 0x%02X", a);
      n++;
    }
  }
  if (n == 0) Serial.print(" (nada)");
  Serial.println();
}

void iniciarSensores()
{
  shtOK = sht.init(Wire);
  if (shtOK)
    Serial.println(sht.getSensorType() == SHTSensor::SHT4X
                   ? "SHT: SHT4x detectado" : "SHT: SHTC1/SHTC3 detectado");
  else
    Serial.println("SHT: no detectado");

  stc31.begin(Wire, STC31_C_I2C_ADDR_29);
  uint32_t productId = 0;
  uint64_t serie = 0;
  int16_t e = stc31.getProductId(productId, serie);
  if (e != NO_ERROR)
  {
    errorToString(e, errMsg, sizeof errMsg);
    Serial.printf("STC31: getProductId error: %s\n", errMsg);
    stcOK = false;
    return;
  }
  Serial.printf("STC31: product_id=0x%08lX serie=0x%08lX%08lX\n",
                (unsigned long)productId,
                (unsigned long)(serie >> 32), (unsigned long)(serie & 0xFFFFFFFF));

  e = stc31.setBinaryGas(STC31_GAS_CO2_AIRE_40);
  if (e != NO_ERROR)
  {
    errorToString(e, errMsg, sizeof errMsg);
    Serial.printf("STC31: setBinaryGas error: %s\n", errMsg);
    stcOK = false;
    return;
  }
  e = stc31.setPressure(PRESION_MBAR);
  Serial.printf("STC31: modo 0x%04X (CO2 en aire 0-40%%), presion %u mbar %s\n",
                STC31_GAS_CO2_AIRE_40, PRESION_MBAR, e == NO_ERROR ? "OK" : "ERROR");
  stcOK = true;
}

void setup()
{
  pinMode(VALVULA_PIN, OUTPUT);
  digitalWrite(VALVULA_PIN, LOW);
  pinMode(HEATER_PIN, OUTPUT);
  digitalWrite(HEATER_PIN, LOW);

  Serial.begin(115200);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 3000) delay(10);

  Wire.begin(SDA_PIN, SCL_PIN, 50000);
  delay(14);

  Serial.println("PRUEBA_STC31 READY (calefactor y valvula apagados)");
  escanearI2C();
  lastScan = millis();
  iniciarSensores();
}

void loop()
{
  delay(1000);

  if (millis() - lastScan > 30000)
  {
    lastScan = millis();
    escanearI2C();
  }

  if (!stcOK || !shtOK)
  {
    iniciarSensores();
    if (!stcOK) return;
  }

  float t = NAN, rh = NAN;
  if (shtOK && sht.readSample())
  {
    t = sht.getTemperature();
    rh = sht.getHumidity();
    stc31.setRelativeHumidity(rh);
    if (t > 0) stc31.setTemperature(t);
  }
  else
  {
    shtOK = false;
  }

  uint16_t gasTicks = 0, tempTicks = 0;
  int16_t e = stc31.measureGasConcentrationRaw(gasTicks, tempTicks);
  if (e != NO_ERROR)
  {
    errorToString(e, errMsg, sizeof errMsg);
    Serial.printf("STC31: medicion error: %s\n", errMsg);
    stcOK = false;
    return;
  }

  float vol = SensirionI2cStc3x::signalGasConcentration(gasTicks);
  float tStc = SensirionI2cStc3x::signalTemperature(tempTicks);

  Serial.printf("co2_raw=%u co2_vol=%.3f co2_ppm=%.0f t_stc=%.2f | sht_t=%.2f sht_rh=%.2f\n",
                gasTicks, vol, vol * 10000.0, tStc, t, rh);
}
