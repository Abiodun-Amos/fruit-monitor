# 🍎 Fruit Monitor — IoT Freshness Detection System

An end-to-end IoT system for real-time fruit freshness classification,
spoilage prediction, and alerting using ESP32-S3, ML, and MQTT.

## Project Structure

```
fruit_monitor/
├── ml_models/          # Trained TFLite models + class maps
├── spoilage_engine/    # Module 2 — Spoilage prediction logic
├── backend/            # Module 3 — FastAPI server + MQTT broker
├── dashboard/          # Module 4 — Live web dashboard
├── alerts/             # Module 5 — Telegram + email alerts
└── firmware/           # Module 6 — ESP32-S3 Arduino firmware
```

## Hardware
- ESP32-S3 (main controller + TFLite inference)
- ESP32-CAM (fruit image capture)
- DHT22 (temperature + humidity)
- Gas sensor MQ-135 (ethylene / CO₂)
- MicroSD card (local logging)
- LiPo battery

## Software Stack
- **ML:** TensorFlow / TFLite (MobileNetV2, INT8 quantized)
- **Backend:** FastAPI + Paho MQTT + SQLite
- **Dashboard:** HTML / JS / MQTT over WebSocket
- **Alerts:** Telegram Bot API
- **Firmware:** Arduino C++ / ESP-IDF

## Modules Status
- [x] Module 1 — Freshness classification model (trained, exported)
- [ ] Module 2 — Spoilage prediction engine
- [ ] Module 3 — Backend server + MQTT
- [ ] Module 4 — Live dashboard
- [ ] Module 5 — Alert system
- [ ] Module 6 — ESP32 firmware (pending hardware)
