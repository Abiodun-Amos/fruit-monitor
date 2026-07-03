#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <DHT.h>
#include <ArduinoJson.h>
#include <time.h>

// ── WiFi ──────────────────────────────────────────────────────
#define WIFI_SSID    "ABIODUN AMOS"
#define WIFI_PASS    "SMARTHOME"

// ── HiveMQ Cloud ──────────────────────────────────────────────
#define MQTT_HOST    "7b54e7cfbe324ae095f2f083bd32ee47.s1.eu.hivemq.cloud"
#define MQTT_PORT    8883
#define MQTT_USER    "fruitmonitor"
#define MQTT_PASS    "Fruitmonitor01"
#define MQTT_TOPIC   "fruit/sensor/data"
#define DEVICE_ID    "esp32-devkit-01"

// ── Sensors ───────────────────────────────────────────────────
#define DHT_PIN      4       // GPIO4  — DHT22 data
#define DHT_TYPE     DHT22
#define MQ135_PIN    34      // GPIO34 — MQ-135 AO (via voltage divider)
// ── Button / pause-resume state ───────────────────────────────
#define BUTTON_PIN   13      // GPIO13 — pause/resume push button
#define DEBOUNCE_MS  300

bool          monitoringActive = true;   // starts ON
bool          lastButtonState  = HIGH;
unsigned long lastButtonPress  = 0;

// ── Publish interval ──────────────────────────────────────────
#define INTERVAL_MS  30000UL   // 30 seconds

// ── Fruit label ───────────────────────────────────────────────
// Change this to the fruit you are physically testing with.
// Options: freshapple, freshbanana, freshorange,
//          freshguava, freshtomato, freshgrape, freshstrawberry
// (prefix with "rotten" for spoiled fruit)
String fruitLabel      = "freshbanana";
float  fruitConfidence = 0.95f;

// ── Objects ───────────────────────────────────────────────────
DHT              dht(DHT_PIN, DHT_TYPE);
WiFiClientSecure wifiClient;
PubSubClient     mqtt(wifiClient);
unsigned long    lastPublish = 0;

// ─────────────────────────────────────────────────────────────

void connectWiFi() {
    if (WiFi.status() == WL_CONNECTED) return;
    Serial.printf("\nConnecting to WiFi: %s", WIFI_SSID);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
    }
    Serial.printf("\nWiFi connected  IP: %s\n", WiFi.localIP().toString().c_str());
}

void connectMQTT() {
    while (!mqtt.connected()) {
        Serial.print("Connecting to MQTT...");
        if (mqtt.connect(DEVICE_ID, MQTT_USER, MQTT_PASS)) {
            Serial.println(" connected");
        } else {
            Serial.printf(" rc=%d  retry in 5s\n", mqtt.state());
            delay(5000);
        }
    }
}

void syncTime() {
    configTime(0, 0, "pool.ntp.org", "time.nist.gov");
    Serial.print("Syncing time");
    int tries = 0;
    while (time(nullptr) < 1000000000UL && tries < 20) {
        Serial.print(".");
        delay(500);
        tries++;
    }
    Serial.println(" done");
}

void readAndPublish() {
    // ── Read DHT22 ────────────────────────────────────────────
    float temp = dht.readTemperature();
    float hum  = dht.readHumidity();

    if (isnan(temp) || isnan(hum)) {
        Serial.println("DHT22 read failed - check wiring on GPIO4");
        return;
    }

    // ── Read MQ-135 ───────────────────────────────────────────
    int gasRaw = analogRead(MQ135_PIN);   // 0-4095

    // ── Build JSON ────────────────────────────────────────────
    JsonDocument doc;
    doc["device_id"]   = DEVICE_ID;
    doc["ml_label"]    = fruitLabel;
    doc["confidence"]  = fruitConfidence;
    doc["temperature"] = roundf(temp * 10) / 10.0f;
    doc["humidity"]    = roundf(hum  * 10) / 10.0f;
    doc["gas_raw"]     = gasRaw;
    doc["ts"]          = (long)time(nullptr);

    char buf[256];
    serializeJson(doc, buf);

    // ── Publish ───────────────────────────────────────────────
    if (mqtt.publish(MQTT_TOPIC, buf)) {
        Serial.printf("Sent: %s  temp=%.1fC  hum=%.1f%%  gas=%d\n",
                      fruitLabel.c_str(), temp, hum, gasRaw);
    } else {
        Serial.println("Publish failed - will retry next cycle");
    }
}

// ─────────────────────────────────────────────────────────────

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("\n====== Fruit Monitor - ESP32 DevKitC Firmware ======");

    dht.begin();
    pinMode(BUTTON_PIN, INPUT_PULLUP);   // button connects pin to GND when pressed
    analogReadResolution(12);   // 12-bit ADC -> values 0-4095

    connectWiFi();
    syncTime();

    wifiClient.setInsecure();   // skip TLS cert check (fine for project)
    mqtt.setServer(MQTT_HOST, MQTT_PORT);
    mqtt.setBufferSize(512);

    connectMQTT();

    // Publish immediately on boot - don't wait 30s for first reading
    readAndPublish();
    lastPublish = millis();

    bool          monitoringActive = true;   // starts ON
    bool          lastButtonState  = HIGH;
    unsigned long lastButtonPress  = 0;
    #define DEBOUNCE_MS  300

    Serial.printf("\nReading every %lu seconds. Monitoring: %s\n\n",
                  INTERVAL_MS / 1000, fruitLabel.c_str());
}

void checkButton() {
    bool state = digitalRead(BUTTON_PIN);

    // Detect a press (HIGH → LOW transition) with debounce
    if (state == LOW && lastButtonState == HIGH &&
        (millis() - lastButtonPress) > DEBOUNCE_MS) {

        monitoringActive = !monitoringActive;     // toggle
        lastButtonPress  = millis();

        if (monitoringActive) {
            Serial.println("\n>>> MONITORING RESUMED <<<\n");
        } else {
            Serial.println("\n>>> MONITORING PAUSED <<<\n");
        }
    }
    lastButtonState = state;
}

void loop() {
    if (WiFi.status() != WL_CONNECTED) connectWiFi();
    if (!mqtt.connected())              connectMQTT();
    mqtt.loop();

    checkButton();   // always check the button, even when paused

    if (monitoringActive && (millis() - lastPublish >= INTERVAL_MS)) {
        lastPublish = millis();
        readAndPublish();
    }
}