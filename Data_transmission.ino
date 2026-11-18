#include <Wire.h>
#include "MAX30105.h"
#include <bluefruit.h>

MAX30105 particleSensor;
BLEUart bleuart;

void setup() {
  Serial.begin(115200);
  
  // 1. WAIT FOR SERIAL MONITOR (Optional, for debugging)
  unsigned long startTime = millis();
  while (!Serial && (millis() - startTime < 3000)) {
    delay(10); 
  }

  Serial.println("--- BOOTING UP ---");

  // 2. BLUETOOTH SETUP
  Bluefruit.begin();
  Bluefruit.setName("XIAO Pulse Oximeter");
  bleuart.begin();
  Bluefruit.Advertising.addService(bleuart);
  Bluefruit.Advertising.start(0);
  Serial.println("Bluetooth is advertising!");

  // 3. SENSOR SETUP
  Wire.begin(); 
  Wire.setClock(400000);

  if (!particleSensor.begin(Wire, I2C_SPEED_FAST)) {
    Serial.println("MAX30102 NOT FOUND. Check wiring!");
    while (1); 
  }

  // Setup for RAW data
  byte ledBrightness = 0x1F; 
  byte sampleAverage = 1; // 1 = NO averaging. We want every single raw sample.
  byte ledMode = 2;       // 2 = Red and IR
  int sampleRate = 100;   // 100Hz
  int pulseWidth = 411; 
  int adcRange = 4096; 

  particleSensor.setup(ledBrightness, sampleAverage, ledMode, sampleRate, pulseWidth, adcRange);
  
  Serial.println("Setup Complete. Transmitting raw data...");
}

void loop() {
  // 1. Read the raw values from the sensor
  long rawIR = particleSensor.getIR();
  long rawRed = particleSensor.getRed();

  // 2. Format as a simple string: "rawIR,rawRed"
  char bleBuffer[32];
  snprintf(bleBuffer, sizeof(bleBuffer), "%ld,%ld\n", rawIR, rawRed);
  
  // 3. Send to USB Serial (for debugging)
  Serial.print(bleBuffer);
  
  // 4. Send over Bluetooth
  if (Bluefruit.connected()) {
    bleuart.print(bleBuffer); 
  }

  // The MAX30102 library handles the 100Hz timing internally via FIFO, 
  // but a tiny delay prevents the Bluetooth stack from getting overwhelmed.
  delay(10);
}