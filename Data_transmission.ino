#include <Wire.h>
#include <U8g2lib.h>
#include "MAX30105.h"
#include "LSM6DS3.h"
#include <bluefruit.h>

MAX30105 particleSensor;
BLEUart bleuart;
LSM6DS3 myIMU(I2C_MODE, 0x6A); 

// RESET PIN IS D1 (Physical pin 1)
U8G2_SSD1309_128X64_NONAME0_F_HW_I2C u8g2(U8G2_R0, /* reset=*/ 1);

bool imuReady = false;
int displayBPM = 0;
int displaySpO2 = 0;

void setup() {
  Serial.begin(115200);
  unsigned long startTime = millis();
  while (!Serial && (millis() - startTime < 3000)) delay(10);

  Serial.println("--- BOOTING UP ---");

  // 1. START I2C FIRST
  Wire.begin();
  Wire.setClock(100000); // 100kHz

  // 2. OLED SETUP
  Serial.println("Initializing OLED...");
  u8g2.setI2CAddress(0x3C << 1);
  u8g2.begin(); 
  
  u8g2.clearBuffer();
  u8g2.setFont(u8g2_font_ncenB10_tr);
  u8g2.drawStr(0, 15, "Booting...");
  u8g2.sendBuffer();
  Serial.println("OLED OK.");

  // 3. BLUETOOTH
  Serial.println("Initializing Bluetooth...");
  Bluefruit.begin();
  Bluefruit.setName("XIAO Pulse Oximeter");
  bleuart.begin();
  Bluefruit.Advertising.addService(bleuart);
  Bluefruit.Advertising.start(0);
  Serial.println("Bluetooth OK.");

  // 4. SENSORS
  Serial.println("Initializing MAX30102...");
  if (!particleSensor.begin(Wire, I2C_SPEED_STANDARD)) {
    Serial.println("MAX30102 FAILED");
    while (1);
  }
  particleSensor.setup(60, 4, 2, 100, 411, 4096);
  Serial.println("MAX30102 OK.");

  Serial.println("Initializing IMU...");
  if (myIMU.begin() == 0) {
    imuReady = true;
    Serial.println("IMU OK.");
  } else {
    Serial.println("IMU FAILED.");
  }

  Serial.println("Setup Complete!");
}

void loop() {
  uint32_t rawIR = particleSensor.getIR();
  uint32_t rawRed = particleSensor.getRed();
  
  int16_t ax_mg = 0, ay_mg = 0, az_mg = 0;
  if (imuReady) {
    ax_mg = (int16_t)(myIMU.readFloatAccelX() * 1000.0f);
    ay_mg = (int16_t)(myIMU.readFloatAccelY() * 1000.0f);
    az_mg = (int16_t)(myIMU.readFloatAccelZ() * 1000.0f);
  }

  if (Bluefruit.connected()) {
    uint8_t packet[14];
    packet[0] = (rawIR >> 0) & 0xFF;  packet[1] = (rawIR >> 8) & 0xFF;
    packet[2] = (rawIR >> 16) & 0xFF; packet[3] = (rawIR >> 24) & 0xFF;
    packet[4] = (rawRed >> 0) & 0xFF; packet[5] = (rawRed >> 8) & 0xFF;
    packet[6] = (rawRed >> 16) & 0xFF; packet[7] = (rawRed >> 24) & 0xFF;
    packet[8] = (ax_mg >> 0) & 0xFF;  packet[9] = (ax_mg >> 8) & 0xFF;
    packet[10] = (ay_mg >> 0) & 0xFF; packet[11] = (ay_mg >> 8) & 0xFF;
    packet[12] = (az_mg >> 0) & 0xFF; packet[13] = (az_mg >> 8) & 0xFF;
    bleuart.write(packet, 14);
  }

  if (Bluefruit.connected() && bleuart.available()) {
    String incoming = bleuart.readStringUntil('\n');
    incoming.trim();
    int commaIndex = incoming.indexOf(',');
    if (commaIndex > 0) {
      displayBPM = incoming.substring(0, commaIndex).toInt();
      displaySpO2 = incoming.substring(commaIndex + 1).toInt();
    }
  }

  static unsigned long lastDisplay = 0;
  if (millis() - lastDisplay > 500) {
    lastDisplay = millis();
    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_ncenB10_tr);

    if (displayBPM > 0) {
      u8g2.drawStr(0, 15, "Status: GOOD");
      char bpmStr[20];
      sprintf(bpmStr, "HR: %d BPM", displayBPM);
      u8g2.drawStr(0, 35, bpmStr);
      char spo2Str[20];
      sprintf(spo2Str, "SpO2: %d%%", displaySpO2);
      u8g2.drawStr(0, 55, spo2Str);
    } else {
      u8g2.drawStr(0, 15, "Status: WAIT");
      u8g2.drawStr(0, 35, "HR: --");
      u8g2.drawStr(0, 55, "SpO2: --");
    }
    u8g2.sendBuffer();
  }
}