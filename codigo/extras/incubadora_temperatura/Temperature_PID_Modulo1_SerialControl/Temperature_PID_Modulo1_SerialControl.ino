#include <SPI.h>
#define MAX6675_CS   10
#define MAX6675_SO   12
#define MAX6675_SCK  13

int PWM_pin = 3;

float temperature_read = 0.0;
float set_temperature = 37.0;
float PID_error = 0;
float previous_error = 0;
float elapsedTime, Time, timePrev;
int PID_value = 0;

float kp = 8.0;
float ki = 0.05;
float kd = 3.0;
float PID_p = 0;
float PID_i = 0;
float PID_d = 0;

void setup() {
  pinMode(PWM_pin, OUTPUT);
  TCCR2B = TCCR2B & B11111000 | 0x03;
  Time = millis();
  Serial.begin(9600);
  Serial.println("READY");
}

void loop() {
  if (Serial.available() > 0) {
    String msg = Serial.readStringUntil('\n');
    msg.trim();
    if (msg.startsWith("SET:")) {
      float new_sp = msg.substring(4).toFloat();
      if (new_sp >= 20.0 && new_sp <= 80.0) {
        set_temperature = new_sp;
      }
    }
  }

  temperature_read = readThermocouple();

  PID_error = set_temperature - temperature_read;
  timePrev = Time;
  Time = millis();
  elapsedTime = (Time - timePrev) / 1000.0;

  PID_p = kp * PID_error;
  PID_i += ki * PID_error * elapsedTime;
  if (PID_i > 255) PID_i = 255;
  if (PID_i < 0)   PID_i = 0;
  PID_d = kd * ((PID_error - previous_error) / elapsedTime);

  PID_value = PID_p + PID_i + PID_d;
  if (PID_value < 0)   PID_value = 0;
  if (PID_value > 255) PID_value = 255;

  analogWrite(PWM_pin, (int)PID_value);
  previous_error = PID_error;

  delay(300);

  Serial.print("{\"temp\":");
  Serial.print(temperature_read, 2);
  Serial.print(",\"setpoint\":");
  Serial.print(set_temperature, 1);
  Serial.print(",\"pwm\":");
  Serial.print(PID_value);
  Serial.println("}");
}

double readThermocouple() {
  uint16_t v;
  pinMode(MAX6675_CS, OUTPUT);
  pinMode(MAX6675_SO, INPUT);
  pinMode(MAX6675_SCK, OUTPUT);

  digitalWrite(MAX6675_CS, LOW);
  delayMicroseconds(10);
  v = shiftIn(MAX6675_SO, MAX6675_SCK, MSBFIRST);
  v <<= 8;
  v |= shiftIn(MAX6675_SO, MAX6675_SCK, MSBFIRST);
  digitalWrite(MAX6675_CS, HIGH);

  if (v & 0x4) return NAN;
  v >>= 3;
  return v * 0.25 - 1.0;
}
