/*
 * Fruit Monitor — ESP32-CAM Firmware
 * Captures image every 5 min → uploads to ImgBB → publishes URL via MQTT.
 * Backend fetches the URL, runs TFLite inference, feeds spoilage engine.
 *
 * Board: AI Thinker ESP32-CAM
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include "esp_camera.h"
#include "base64.h"

// ── WiFi ──────────────────────────────────────────────────────
#define WIFI_SSID    "ABIODUN AMOS"
#define WIFI_PASS    "SMARTHOME"

// ── ImgBB ─────────────────────────────────────────────────────
#define IMGBB_API_KEY "94baf54e6b7d5a403712b5ee71bf5cc1"

// ── HiveMQ Cloud ──────────────────────────────────────────────
#define MQTT_HOST    "7b54e7cfbe324ae095f2f083bd32ee47.s1.eu.hivemq.cloud"
#define MQTT_PORT    8883
#define MQTT_USER    "camera_user"
#define MQTT_PASS    "Camera2026"
#define MQTT_TOPIC   "fruit/camera/image"
#define DEVICE_ID    "esp32-cam-01"

// ── Capture interval ──────────────────────────────────────────
#define INTERVAL_MS  30000UL

// ── AI Thinker ESP32-CAM pin map ──────────────────────────────
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

// ── Objects ───────────────────────────────────────────────────
WiFiClientSecure wifiClient;
PubSubClient     mqtt(wifiClient);
unsigned long    lastCapture = 0;

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

bool initCamera() {
    camera_config_t config;
    config.ledc_channel = LEDC_CHANNEL_0;
    config.ledc_timer   = LEDC_TIMER_0;
    config.pin_d0       = Y2_GPIO_NUM;
    config.pin_d1       = Y3_GPIO_NUM;
    config.pin_d2       = Y4_GPIO_NUM;
    config.pin_d3       = Y5_GPIO_NUM;
    config.pin_d4       = Y6_GPIO_NUM;
    config.pin_d5       = Y7_GPIO_NUM;
    config.pin_d6       = Y8_GPIO_NUM;
    config.pin_d7       = Y9_GPIO_NUM;
    config.pin_xclk     = XCLK_GPIO_NUM;
    config.pin_pclk     = PCLK_GPIO_NUM;
    config.pin_vsync    = VSYNC_GPIO_NUM;
    config.pin_href     = HREF_GPIO_NUM;
    config.pin_sccb_sda = SIOD_GPIO_NUM;
    config.pin_sccb_scl = SIOC_GPIO_NUM;
    config.pin_pwdn     = PWDN_GPIO_NUM;
    config.pin_reset    = RESET_GPIO_NUM;
    config.xclk_freq_hz = 20000000;
    config.pixel_format = PIXFORMAT_JPEG;

    // Use PSRAM — we confirmed 4MB is present
    if (psramFound()) {
        config.frame_size   = FRAMESIZE_VGA;   // 640x480 — good balance
        config.jpeg_quality = 12;              // lower = better quality
        config.fb_count     = 2;
    } else {
        config.frame_size   = FRAMESIZE_CIF;
        config.jpeg_quality = 15;
        config.fb_count     = 1;
    }

    esp_err_t err = esp_camera_init(&config);
    if (err != ESP_OK) {
        Serial.printf("Camera init failed: 0x%x\n", err);
        return false;
    }
    Serial.println("Camera initialised");
    return true;
}

String uploadToImgBB(camera_fb_t *fb) {
    // Base64-encode the JPEG
    String encoded = base64::encode(fb->buf, fb->len);

    // URL-encode the base64 (+ and / and = need encoding for form POST)
    encoded.replace("+", "%2B");
    encoded.replace("/", "%2F");
    encoded.replace("=", "%3D");

    HTTPClient http;
    WiFiClientSecure client;
    client.setInsecure();

    String url = "https://api.imgbb.com/1/upload?key=" + String(IMGBB_API_KEY);
    http.begin(client, url);
    http.addHeader("Content-Type", "application/x-www-form-urlencoded");
    http.setTimeout(20000);

    String body = "image=" + encoded;
    int code = http.POST(body);

    String imageUrl = "";
    if (code == 200) {
        String resp = http.getString();
        // Parse JSON response for data.url
        JsonDocument doc;
        DeserializationError err = deserializeJson(doc, resp);
        if (!err) {
            imageUrl = doc["data"]["url"].as<String>();
            Serial.printf("Uploaded: %s\n", imageUrl.c_str());
        } else {
            Serial.println("Failed to parse ImgBB response");
        }
    } else {
        Serial.printf("ImgBB upload failed, HTTP %d\n", code);
    }

    http.end();
    return imageUrl;
}

void captureAndPublish() {
    Serial.println("\nCapturing image...");

    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) {
        Serial.println("Capture failed");
        return;
    }
    Serial.printf("Captured %d bytes\n", fb->len);

    String imageUrl = uploadToImgBB(fb);
    esp_camera_fb_return(fb);   // free the buffer ASAP

    if (imageUrl.length() == 0) {
        Serial.println("No URL — skipping publish");
        return;
    }

    // Publish URL via MQTT
    JsonDocument doc;
    doc["device_id"] = DEVICE_ID;
    doc["image_url"] = imageUrl;

    char buf[512];
    serializeJson(doc, buf);

    if (mqtt.publish(MQTT_TOPIC, buf)) {
        Serial.println("Published image URL to MQTT");
    } else {
        Serial.println("MQTT publish failed");
    }
}

// ─────────────────────────────────────────────────────────────

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("\n====== Fruit Monitor — ESP32-CAM Firmware ======");

    if (!initCamera()) {
        Serial.println("Halting — camera init failed");
        while (true) delay(1000);
    }

    connectWiFi();

    wifiClient.setInsecure();
    mqtt.setServer(MQTT_HOST, MQTT_PORT);
    mqtt.setBufferSize(1024);
    connectMQTT();

    // First capture immediately
    captureAndPublish();
    lastCapture = millis();

    Serial.printf("\nCapturing every %lu minutes\n\n", INTERVAL_MS / 60000);
}

void loop() {
    if (WiFi.status() != WL_CONNECTED) connectWiFi();
    if (!mqtt.connected())              connectMQTT();
    mqtt.loop();

    if (millis() - lastCapture >= INTERVAL_MS) {
        lastCapture = millis();
        captureAndPublish();
    }
}