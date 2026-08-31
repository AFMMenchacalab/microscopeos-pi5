# Firmware de incubadora — PID de temperatura y CO2

Sketches Arduino (Uno/Nano) que corren el lazo PID de temperatura
(DS18B20 + calefactor PWM) y el control de CO2 (SCD30 + válvula
solenoide), reportando telemetría JSON por serial. `Control_Incubator_TempCO2`
es el que usa `temperature_controller.py` en producción; los demás son
iteraciones anteriores, se conservan como referencia.

## Librerías

No están vendorizadas en el repo (se sacaron 2026-08-31: 19 MB de código de
terceros inflando el historial y las estadísticas de lenguaje del repo sin
necesidad — `arduino-cli`/Arduino IDE las resuelven solas). Instalar con:

```bash
arduino-cli lib install \
  "Adafruit SCD30@1.0.11" \
  "DallasTemperature@4.0.6" \
  "OneWire@2.3.8" \
  "PID@1.2.0"
```

Instala también, como dependencias transitivas de `Adafruit SCD30`:
`Adafruit BusIO` (1.17.4), `Adafruit Unified Sensor` (1.1.15),
`Adafruit GFX Library` (1.12.6) y `Adafruit SSD1306` (2.5.17) — no hace
falta pedirlas a mano, el Library Manager las trae solas.

Versiones fijadas al momento de la migración a Pi 5; si compilás con una
más nueva y algo no anda, primero probá con estas exactas antes de asumir
que el código está mal.
