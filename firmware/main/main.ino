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
#define MQTT_TOPIC_PUB    "fruit/sensor/data"
#define MQTT_TOPIC_SUB    "fruit/prediction/result"   // backend echoes the resolved label here
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

// ── Autonomous fruit label ──────────────────────────────────────
// No camera on this board, so it cannot know the fruit type itself.
// Instead it listens on MQTT_TOPIC_SUB for the backend's own resolved
// classification (sourced from the ESP32-CAM) and republishes THAT
// on its next reading — no manual editing or re-flashing required.
//
// knownFruitLabel/knownConfidence below are ONLY ever used for the
// handful of seconds between boot and the first echo received; once
// even one prediction has come back, they always reflect real data.
String fruitLabel      = "freshapple";   // bootstrap-only placeholder
float  fruitConfidence = 0.50f;          // bootstrap-only placeholder
bool   haveRealLabel    = false;         // true once an echo has arrived

// ── Objects ───────────────────────────────────────────────────
DHT              dht(DHT_PIN, DHT_TYPE);
WiFiClientSecure wifiClient;
PubSubClient     mqtt(wifiClient);
unsigned long    lastPublish = 0;

// ── Forward declarations ───────────────────────────────────────
void publishStatus(const char* status);
void onMqttMessage(char* topic, byte* payload, unsigned int length);

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
            mqtt.subscribe(MQTT_TOPIC_SUB);
            Serial.printf("Subscribed to %s\n", MQTT_TOPIC_SUB);
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

// ── Receives the backend's resolved prediction and adopts its label ──
void onMqttMessage(char* topic, byte* payload, unsigned int length) {
    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, payload, length);
    if (err) {
        Serial.printf("Prediction echo: JSON parse failed (%s)\n", err.c_str());
        return;
    }

    // Only adopt echoes that came from THIS device's own readings
    const char* echoedDevice = doc["device_id"] | "";
    if (strcmp(echoedDevice, DEVICE_ID) != 0) return;

    const char* mlLabel = doc["ml_label"] | "";
    if (strlen(mlLabel) == 0) return;

    fruitLabel      = String(mlLabel);
    fruitConfidence = doc["confidence"] | fruitConfidence;
    haveRealLabel   = true;

    Serial.printf("Adopted label from backend -> %s\n", fruitLabel.c_str());
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
    if (mqtt.publish(MQTT_TOPIC_PUB, buf)) {
        Serial.printf("Sent: %s (%s)  temp=%.1fC  hum=%.1f%%  gas=%d\n",
                      fruitLabel.c_str(),
                      haveRealLabel ? "confirmed" : "bootstrap",
                      temp, hum, gasRaw);
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
    mqtt.setCallback(onMqttMessage);
    mqtt.setBufferSize(1024);   // prediction echoes carry many fields — 256 was too small to receive them

    connectMQTT();
    publishStatus("resumed");   // system starts active

    // Publish immediately on boot - don't wait 30s for first reading
    readAndPublish();
    lastPublish = millis();

    Serial.printf("\nReading every %lu seconds. Monitoring: autonomous (backend-fed)\n\n",
                  INTERVAL_MS / 1000);
}

void publishStatus(const char* status) {
    JsonDocument doc;
    doc["device_id"] = DEVICE_ID;
    doc["status"]     = status;   // "paused" or "resumed"
    doc["ts"]         = (long)time(nullptr);

    char buf[128];
    serializeJson(doc, buf);
    mqtt.publish("fruit/device/status", buf, true);   // retained=true
}

void checkButton() {
    bool state = digitalRead(BUTTON_PIN);

    if (state == LOW && lastButtonState == HIGH &&
        (millis() - lastButtonPress) > DEBOUNCE_MS) {

        monitoringActive = !monitoringActive;
        lastButtonPress  = millis();

        if (monitoringActive) {
            Serial.println("\n>>> MONITORING RESUMED <<<\n");
            publishStatus("resumed");
        } else {
            Serial.println("\n>>> MONITORING PAUSED <<<\n");
            publishStatus("paused");
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