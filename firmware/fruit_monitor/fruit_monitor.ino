/*
 * Serial bridge — turns NodeMCU into a USB-TTL programmer
 * Uses UART2 (GPIO16/17) to talk to ESP32-CAM
 * Upload this to NodeMCU BEFORE connecting ESP32-CAM
 */
#include <HardwareSerial.h>

HardwareSerial CamSerial(2);   // UART2 on NodeMCU

void setup() {
    Serial.begin(115200);                          // USB ↔ PC
    CamSerial.begin(115200, SERIAL_8N1, 16, 17);  // GPIO16=RX, GPIO17=TX
}

void loop() {
    if (Serial.available())    CamSerial.write(Serial.read());
    if (CamSerial.available()) Serial.write(CamSerial.read());
}