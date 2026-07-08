"""
device_state.py
Tracks the latest paused/resumed status per device, published over MQTT.
"""
import threading
from datetime import datetime


class DeviceState:
    def __init__(self):
        self._lock  = threading.Lock()
        self._status = {}   # device_id -> {"status": "...", "updated_at": ...}

    def update(self, device_id: str, status: str):
        with self._lock:
            self._status[device_id] = {
                "status": status,
                "updated_at": datetime.utcnow().isoformat(),
            }

    def get_all(self):
        with self._lock:
            return dict(self._status)


device_state = DeviceState()