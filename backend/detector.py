"""
detector.py
YOLOv8-nano fruit detection — finds and localises multiple fruits per
frame. Runs alongside inference.py (the freshness classifier), never
replacing it: this file only answers "what and where," inference.py
still answers "fresh or rotten" for each box this file finds.
"""
import json
import logging
from pathlib import Path

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

_BASE            = Path(__file__).resolve().parent.parent
_DETECTOR_PATH   = _BASE / "ml_models" / "fruit_detector_best.tflite"
_DETECTOR_CLASSES = _BASE / "ml_models" / "detector_class_map.json"

DET_IMG_SIZE     = (640, 640)   # matches YOLO training imgsz
CONF_THRESHOLD   = 0.60         # per-box detection confidence floor
IOU_THRESHOLD    = 0.45         # non-max suppression overlap threshold

_interpreter = None
_input_details = None
_output_details = None
_class_map = None


def _load():
    global _interpreter, _input_details, _output_details, _class_map
    if _interpreter is not None:
        return

    try:
        from ai_edge_litert.interpreter import Interpreter
    except ImportError:
        try:
            from tflite_runtime.interpreter import Interpreter
        except ImportError:
            from tensorflow.lite import Interpreter

    logger.info("Loading fruit detector from %s", _DETECTOR_PATH)
    _interpreter = Interpreter(model_path=str(_DETECTOR_PATH))
    _interpreter.allocate_tensors()
    _input_details  = _interpreter.get_input_details()
    _output_details = _interpreter.get_output_details()

    with open(_DETECTOR_CLASSES) as f:
        _class_map = json.load(f)

    logger.info("Detector loaded. Classes: %d", len(_class_map))


def detect_fruits(image_bytes: bytes) -> list:
    """
    Runs YOLO detection on raw image bytes.
    Returns a list of dicts, one per detected box:
        {"fruit_type": str, "confidence": float,
         "box": (x1, y1, x2, y2)}   # pixel coords in the ORIGINAL image
    """
    _load()

    img = Image.open(__import__("io").BytesIO(image_bytes)).convert("RGB")
    orig_w, orig_h = img.size
    resized = img.resize(DET_IMG_SIZE)
    arr = np.asarray(resized).astype(np.float32) / 255.0
    arr = np.expand_dims(arr, axis=0)

    in_dtype = _input_details[0]["dtype"]
    if in_dtype in (np.uint8, np.int8):
        scale, zero_point = _input_details[0]["quantization"]
        arr = (arr / scale + zero_point).astype(in_dtype) if scale else arr.astype(in_dtype)

    _interpreter.set_tensor(_input_details[0]["index"], arr)
    _interpreter.invoke()
    output = _interpreter.get_tensor(_output_details[0]["index"])[0]  # (84, 8400)-ish, YOLOv8 export layout

    out_dtype = _output_details[0]["dtype"]
    if out_dtype in (np.uint8, np.int8):
        o_scale, o_zero = _output_details[0]["quantization"]
        output = (output.astype(np.float32) - o_zero) * o_scale

    boxes = output[:4, :].T          # cx, cy, w, h  (normalised 0-1)
    scores = output[4:, :].T         # per-class confidence, one row per box

    detections = []
    class_ids = np.argmax(scores, axis=1)
    confidences = np.max(scores, axis=1)

    keep = confidences >= CONF_THRESHOLD
    boxes, class_ids, confidences = boxes[keep], class_ids[keep], confidences[keep]

    if len(boxes) == 0:
        return []

    # Convert cx,cy,w,h (normalised) -> x1,y1,x2,y2 (pixel, original image)
    x1 = (boxes[:, 0] - boxes[:, 2] / 2) * orig_w
    y1 = (boxes[:, 1] - boxes[:, 3] / 2) * orig_h
    x2 = (boxes[:, 0] + boxes[:, 2] / 2) * orig_w
    y2 = (boxes[:, 1] + boxes[:, 3] / 2) * orig_h
    px_boxes = np.stack([x1, y1, x2, y2], axis=1)

    keep_idx = _nms(px_boxes, confidences, IOU_THRESHOLD)

    for i in keep_idx:
        detections.append({
            "fruit_type": _class_map[str(int(class_ids[i]))],
            "confidence": round(float(confidences[i]), 4),
            "box": tuple(px_boxes[i].tolist()),
        })
    return detections


def crop_box(image_bytes: bytes, box: tuple) -> bytes:
    """Crops one detected region out of the original image, returns JPEG bytes."""
    img = Image.open(__import__("io").BytesIO(image_bytes)).convert("RGB")
    x1, y1, x2, y2 = [max(0, int(v)) for v in box]
    crop = img.crop((x1, y1, x2, y2))
    buf = __import__("io").BytesIO()
    crop.save(buf, format="JPEG")
    return buf.getvalue()


def _nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list:
    """Standard non-max suppression — same box for the same fruit doesn't get kept twice."""
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w, h = np.maximum(0, xx2 - xx1), np.maximum(0, yy2 - yy1)
        inter = w * h
        iou = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[1:][iou <= iou_threshold]
    return keep