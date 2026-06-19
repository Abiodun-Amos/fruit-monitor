# ml_models/

Place your trained model files here after exporting from Google Colab.

## Expected files

| File | Description |
|---|---|
| `fruit_model_v2_int8.tflite` | INT8 quantized model → deploy on ESP32-S3 |
| `fruit_model_v2_float32.tflite` | Float32 model → backend inference fallback |
| `fruit_freshness_v2_model.keras` | Full Keras model → for fine-tuning later |
| `class_map_v2.json` | Maps output index (0–13) to class name |

## class_map_v2.json format
```json
{
  "0": "freshapple",
  "1": "freshbanana",
  "2": "freshgrape",
  "3": "freshguava",
  "4": "freshorange",
  "5": "freshstrawberry",
  "6": "freshtomato",
  "7": "rottenapple",
  "8": "rottenbanana",
  "9": "rottengrape",
  "10": "rottenguava",
  "11": "rottenorange",
  "12": "rottenstrawberry",
  "13": "rottentomato"
}
```
