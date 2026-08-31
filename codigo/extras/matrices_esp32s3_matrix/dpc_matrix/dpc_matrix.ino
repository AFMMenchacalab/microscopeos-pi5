/*
  MicroscopeOS - firmware de iluminacion para Waveshare ESP32-S3-Matrix
  (8x8 WS2812B), control por linea serie sobre USB-CDC nativo.

  Reescrito desde cero: el .ino original de 2026-08-29 (validado 16/16 casos
  de protocolo y los 4 patrones DPC en el microscopio) no quedo guardado en
  el repo. Este reemplazo mantiene el protocolo documentado en README.md de
  esta carpeta para FULL/LEFT/RIGHT/TOP/BOTTOM/OFF/ID, y lo extiende con:

    - RING       campo oscuro: solo el borde exterior de la matriz (8x8),
                 dejando el centro apagado (iluminacion oblicua pura).
    - RHEINBERG  centro + anillo con DOS colores simultaneos (contraste de
                 color falso). Unico patron que no es blanco por defecto.
    - color opcional RRGGBB en cualquier patron simple (PATRON:brillo:color),
      con blanco FFFFFF implicito si se omite - asi los comandos viejos
      (FULL:40, LEFT:128, etc, sin color) siguen dando el mismo eco de
      siempre y el driver Python actual no necesita cambios para lo basico.

  Board: esp32:esp32:esp32s3, USBMode=hwcdc, CDCOnBoot=cdc, PSRAM=enabled.
  Orientacion por placa via -DMATRIX_ROTATION -DMATRIX_FLIP_X -DMATRIX_FLIP_Y
  (ver README, "Compilar y subir"). ESTOS VALORES NO SE DEDUCEN SOBRE EL
  PAPEL: recalibrar SIEMPRE encendiendo LEFT y TOP y mirando la matriz fisica
  montada en el microscopio (el README explica por que: el camino optico
  refleja la imagen, y las dos placas de este set NO comparten calibracion).

  Cableado de los 64 pixeles: confirmado row-major (indice = fila*8 + col,
  sin zigzag/serpentina) contra un ejemplo de terceros para esta misma
  placa (github.com/jaylikesbunda/Jays-Matrix32) - Waveshare no publica esto
  en su wiki. Si alguna vez un patron sale con filas/columnas mezcladas en
  vez de solo rotado/reflejado, ES ESTO lo que hay que revisar primero.
*/

#include <Adafruit_NeoPixel.h>
#include <esp_mac.h>
#include <stdlib.h>

#ifndef MATRIX_ROTATION
#define MATRIX_ROTATION 0
#endif
#ifndef MATRIX_FLIP_X
#define MATRIX_FLIP_X 0
#endif
#ifndef MATRIX_FLIP_Y
#define MATRIX_FLIP_Y 0
#endif

#define DATA_PIN    14
#define MATRIX_SIDE 8
#define NUM_PIXELS  (MATRIX_SIDE * MATRIX_SIDE)
#define MAX_LINE    47

// NEO_GRB (el orden habitual de WS2812B) daba R/G invertidos en esta
// placa -- confirmado a ojo el 2026-08-31 con RHEINBERG (rojo salia verde
// y viceversa). Esta placa concreta es RGB, no GRB.
Adafruit_NeoPixel strip(NUM_PIXELS, DATA_PIN, NEO_RGB + NEO_KHZ800);

// =====================================================
// GEOMETRIA: coordenada logica (r,c), r,c en [0,7] -> indice fisico
// =====================================================
int physicalIndex(int r, int c) {
  int rr = r, cc = c;
  int pasos = (MATRIX_ROTATION / 90) % 4;
  if (pasos < 0) pasos += 4;
  for (int i = 0; i < pasos; i++) {
    int nr = cc;
    int nc = (MATRIX_SIDE - 1) - rr;
    rr = nr;
    cc = nc;
  }
  if (MATRIX_FLIP_X) cc = (MATRIX_SIDE - 1) - cc;
  if (MATRIX_FLIP_Y) rr = (MATRIX_SIDE - 1) - rr;
  return rr * MATRIX_SIDE + cc;
}

enum Zona { Z_TODO, Z_IZQ, Z_DER, Z_ARRIBA, Z_ABAJO, Z_ANILLO, Z_CENTRO };

// int y no Zona: el generador de prototipos de arduino-cli inserta las
// declaraciones adelantadas ANTES de este enum, y "Zona" ahi no existiria.
bool enZona(int r, int c, int z) {
  switch (z) {
    case Z_TODO:   return true;
    case Z_IZQ:    return c < MATRIX_SIDE / 2;
    case Z_DER:    return c >= MATRIX_SIDE / 2;
    case Z_ARRIBA: return r < MATRIX_SIDE / 2;
    case Z_ABAJO:  return r >= MATRIX_SIDE / 2;
    // Borde exterior de 1 pixel: aproxima iluminacion oblicua fuera del
    // cono de apertura numerica del objetivo (campo oscuro).
    case Z_ANILLO: return r == 0 || r == MATRIX_SIDE - 1 ||
                          c == 0 || c == MATRIX_SIDE - 1;
    // Bloque central 2x2: disco de luz directa para Rheinberg.
    case Z_CENTRO: return r >= 3 && r <= 4 && c >= 3 && c <= 4;
  }
  return false;
}

void pintarZona(int z, uint8_t brillo, uint8_t r8, uint8_t g8, uint8_t b8) {
  uint8_t rr = (uint16_t)r8 * brillo / 255;
  uint8_t gg = (uint16_t)g8 * brillo / 255;
  uint8_t bb = (uint16_t)b8 * brillo / 255;
  uint32_t color = strip.Color(rr, gg, bb);
  for (int r = 0; r < MATRIX_SIDE; r++) {
    for (int c = 0; c < MATRIX_SIDE; c++) {
      if (enZona(r, c, z)) {
        strip.setPixelColor(physicalIndex(r, c), color);
      }
    }
  }
}

// =====================================================
// PROTOCOLO SERIE
// =====================================================
char lineBuf[MAX_LINE + 2];
uint8_t lineLen = 0;
bool overflow = false;

String macID() {
  uint8_t mac[6];
  esp_read_mac(mac, ESP_MAC_WIFI_STA);
  char buf[18];
  snprintf(buf, sizeof(buf), "%02X:%02X:%02X:%02X:%02X:%02X",
           mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  return String(buf);
}

void enviarID() {
  Serial.print("ID:MATRIZ:");
  Serial.print(macID());
  Serial.print(":ROT");
  Serial.print(MATRIX_ROTATION);
  Serial.print(":FX");
  Serial.print(MATRIX_FLIP_X);
  Serial.print(":FY");
  Serial.println(MATRIX_FLIP_Y);
}

bool esNumero(const String &s, long &valor, long maxVal) {
  if (s.length() == 0) return false;
  for (unsigned i = 0; i < s.length(); i++) {
    if (!isDigit(s[i])) return false;
  }
  long v = s.toInt();
  if (v < 0 || v > maxVal) return false;
  valor = v;
  return true;
}

bool esColorHex(const String &s, uint8_t &r, uint8_t &g, uint8_t &b) {
  if (s.length() != 6) return false;
  for (unsigned i = 0; i < 6; i++) {
    if (!isHexadecimalDigit(s[i])) return false;
  }
  long v = strtol(s.c_str(), NULL, 16);
  r = (v >> 16) & 0xFF;
  g = (v >> 8) & 0xFF;
  b = v & 0xFF;
  return true;
}

String subir(String s) {
  s.trim();
  s.toUpperCase();
  return s;
}

void procesarLinea(String linea) {
  linea.trim();
  if (linea.length() == 0) return;

  String campos[4];
  int n = 0;
  int inicio = 0;
  for (int i = 0; i <= (int)linea.length() && n < 4; i++) {
    if (i == (int)linea.length() || linea[i] == ':') {
      campos[n++] = linea.substring(inicio, i);
      inicio = i + 1;
      if (i == (int)linea.length()) break;
    }
  }
  bool camposDeMas = false;
  {
    int cuenta = 1;
    for (unsigned i = 0; i < linea.length(); i++) if (linea[i] == ':') cuenta++;
    if (cuenta > 4) camposDeMas = true;
  }

  String patron = subir(campos[0]);

  if (patron == "ID" && n == 1) {
    enviarID();
    return;
  }

  const bool esSimple = (patron == "FULL" || patron == "LEFT" || patron == "RIGHT" ||
                         patron == "TOP" || patron == "BOTTOM" || patron == "OFF" ||
                         patron == "RING");
  const bool esRheinberg = (patron == "RHEINBERG");

  if (!esSimple && !esRheinberg) {
    Serial.print("ERR:UNKNOWN_PATTERN:");
    Serial.println(patron);
    return;
  }

  if (esSimple) {
    if (camposDeMas || !(n == 2 || n == 3)) {
      Serial.print("ERR:BAD_FORMAT:");
      Serial.println(linea);
      return;
    }
    long brillo;
    String tokBrillo = campos[1];
    tokBrillo.trim();
    if (!esNumero(tokBrillo, brillo, 255)) {
      Serial.print("ERR:BAD_BRIGHTNESS:");
      Serial.println(tokBrillo);
      return;
    }
    uint8_t r = 255, g = 255, b = 255;
    if (n == 3) {
      String tokColor = campos[2];
      tokColor.trim();
      if (!esColorHex(tokColor, r, g, b)) {
        Serial.print("ERR:BAD_COLOR:");
        Serial.println(tokColor);
        return;
      }
    }

    if (patron == "OFF") {
      strip.clear();
    } else {
      strip.clear();
      Zona z = Z_TODO;
      if (patron == "LEFT") z = Z_IZQ;
      else if (patron == "RIGHT") z = Z_DER;
      else if (patron == "TOP") z = Z_ARRIBA;
      else if (patron == "BOTTOM") z = Z_ABAJO;
      else if (patron == "RING") z = Z_ANILLO;
      pintarZona(z, (uint8_t)brillo, r, g, b);
    }
    strip.show();

    Serial.print("OK:");
    Serial.print(patron);
    Serial.print(":");
    Serial.print(brillo);
    if (n == 3) {
      char hexbuf[7];
      snprintf(hexbuf, sizeof(hexbuf), "%02X%02X%02X", r, g, b);
      Serial.print(":");
      Serial.print(hexbuf);
    }
    Serial.println();
    return;
  }

  // RHEINBERG:brillo:RRGGBB_centro:RRGGBB_anillo
  if (camposDeMas || n != 4) {
    Serial.print("ERR:BAD_FORMAT:");
    Serial.println(linea);
    return;
  }
  long brillo;
  String tokBrillo = campos[1];
  tokBrillo.trim();
  if (!esNumero(tokBrillo, brillo, 255)) {
    Serial.print("ERR:BAD_BRIGHTNESS:");
    Serial.println(tokBrillo);
    return;
  }
  String tokC = campos[2]; tokC.trim();
  String tokA = campos[3]; tokA.trim();
  uint8_t cr, cg, cb, ar, ag, ab;
  if (!esColorHex(tokC, cr, cg, cb)) {
    Serial.print("ERR:BAD_COLOR:");
    Serial.println(tokC);
    return;
  }
  if (!esColorHex(tokA, ar, ag, ab)) {
    Serial.print("ERR:BAD_COLOR:");
    Serial.println(tokA);
    return;
  }

  strip.clear();
  pintarZona(Z_CENTRO, (uint8_t)brillo, cr, cg, cb);
  pintarZona(Z_ANILLO, (uint8_t)brillo, ar, ag, ab);
  strip.show();

  char hexC[7], hexA[7];
  snprintf(hexC, sizeof(hexC), "%02X%02X%02X", cr, cg, cb);
  snprintf(hexA, sizeof(hexA), "%02X%02X%02X", ar, ag, ab);
  Serial.print("OK:RHEINBERG:");
  Serial.print(brillo);
  Serial.print(":");
  Serial.print(hexC);
  Serial.print(":");
  Serial.println(hexA);
}

void setup() {
  Serial.begin(115200);
  strip.begin();
  strip.clear();
  strip.show();  // arranca a oscuras: un reinicio no debe meter un destello
}

void loop() {
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n') {
      lineBuf[lineLen] = '\0';
      if (overflow) {
        Serial.println("ERR:BAD_FORMAT:LINE_TOO_LONG");
      } else {
        procesarLinea(String(lineBuf));
      }
      lineLen = 0;
      overflow = false;
    } else if (ch != '\r') {
      if (lineLen < MAX_LINE) {
        lineBuf[lineLen++] = ch;
      } else {
        overflow = true;
      }
    }
  }
}
