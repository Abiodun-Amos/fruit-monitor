"""
inference.py
Lightweight TFLite inference for fruit freshness classification.
Loads the INT8 model once at startup, classifies images on demand.
"""
import json
import logging
from pathlib import Path

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────
_BASE       = Path(__file__).resolve().parent.parent  # project root
_MODEL_PATH = _BASE / "ml_models" / "fruit_model_v2_int8.tflite"
_CLASS_MAP  = _BASE / "ml_models" / "class_map_v2.json"

IMG_SIZE = (224, 224)   # matches training

# ── Lazy-loaded globals ───────────────────────────────────────
_interpreter = None
_input_details = None
_output_details = None
_class_map = None


def _load():
    """Load interpreter + class map once. Called on first inference."""
    global _interpreter, _input_details, _output_details, _class_map

    if _interpreter is not None:
        return

    # Import here so the app can still boot if tflite isn't installed
    try:
        from tflite_runtime.interpreter import Interpreter
    except ImportError:
        from tensorflow.lite import Interpreter  # fallback if full TF present

    logger.info("Loading TFLite model from %s", _MODEL_PATH)
    _interpreter = Interpreter(model_path=str(_MODEL_PATH))
    _interpreter.allocate_tensors()
    _input_details  = _interpreter.get_input_details()
    _output_details = _interpreter.get_output_details()

    with open(_CLASS_MAP) as f:
        _class_map = json.load(f)

    logger.info("Model loaded. Input shape: %s, classes: %d",
                _input_details[0]["shape"], len(_class_map))


def classify_image(image_bytes: bytes) -> dict:
    """
    Run inference on raw image bytes (JPEG/PNG).
    Returns: {"ml_label": str, "confidence": float, "all_scores": {...}}
    """
    _load()

    # ── Preprocess ────────────────────────────────────────────
    img = Image.open(__import__("io").BytesIO(image_bytes)).convert("RGB")
    img = img.resize(IMG_SIZE)
    arr = np.asarray(img)

    # Match the model's expected input dtype (INT8 quantized)
    in_dtype = _input_details[0]["dtype"]
    if in_dtype == np.uint8 or in_dtype == np.int8:
        # Quantized model expects raw 0-255 (or int8) pixels
        scale, zero_point = _input_details[0]["quantization"]
        if scale == 0:
            data = arr.astype(in_dtype)
        else:
            data = (arr / scale + zero_point).astype(in_dtype)
    else:
        # Float model expects normalised 0-1
        data = (arr.astype(np.float32) / 255.0)

    data = np.expand_dims(data, axis=0)

    # ── Inference ─────────────────────────────────────────────
    _interpreter.set_tensor(_input_details[0]["index"], data)
    _interpreter.invoke()
    output = _interpreter.get_tensor(_output_details[0]["index"])[0]

    # Dequantize output if needed
    out_dtype = _output_details[0]["dtype"]
    if out_dtype in (np.uint8, np.int8):
        o_scale, o_zero = _output_details[0]["quantization"]
        output = (output.astype(np.float32) - o_zero) * o_scale

    # ── Resolve label ─────────────────────────────────────────
    top_idx    = int(np.argmax(output))
    confidence = float(output[top_idx])
    ml_label   = _class_map[str(top_idx)]

    all_scores = {_class_map[str(i)]: float(output[i]) for i in range(len(output))}

    return {
        "ml_label":   ml_label,
        "confidence": round(confidence, 4),
        "all_scores": all_scores,
    }