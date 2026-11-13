"""EcoFlow Open API (EU) client.

Signature: HMAC-SHA256 over accessKey, nonce, timestamp and request
parameters, sent as request headers. Same scheme as the official
device list and quota endpoints on https://api-e.ecoflow.com.
"""

import hashlib
import hmac
import os
import random
import time

import requests

BASE_URL = "https://api-e.ecoflow.com"
DEFAULT_SN = "DCEBZ5SGC081956"


class EcoflowError(Exception):
    def __init__(self, message, payload=None):
        super().__init__(message)
        self.payload = payload


def flatten(data, prefix=""):
    out = {}
    for key, value in data.items():
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(flatten(value, full))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                item_key = f"{full}[{index}]"
                if isinstance(item, dict):
                    out.update(flatten(item, item_key))
                else:
                    out[item_key] = item
        else:
            out[full] = value
    return out


def load_env(path=".env"):
    """Load KEY=VALUE lines. Does not override variables already set."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.split(" #", 1)[0].strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


class EcoflowClient:
    def __init__(self, access_key, secret_key, sn, base_url=BASE_URL, timeout=30):
        self.access_key = access_key
        self.secret_key = secret_key
        self.sn = sn
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @classmethod
    def from_env(cls):
        load_env()
        sn = os.environ.get("ECOFLOW_SN", DEFAULT_SN).split()[0]
        return cls(
            os.environ["ECOFLOW_ACCESS_KEY"],
            os.environ["ECOFLOW_SECRET_KEY"],
            sn,
        )

    def headers_for(self, params=None):
        nonce = str(random.randint(100000, 999999))
        timestamp = str(int(time.time() * 1000))
        flat = flatten(params) if params else {}
        signed = "&".join(f"{key}={flat[key]}" for key in sorted(flat))
        signed = (signed + "&" if signed else "") + (
            f"accessKey={self.access_key}&nonce={nonce}&timestamp={timestamp}"
        )
        sign = hmac.new(
            self.secret_key.encode(), signed.encode(), hashlib.sha256
        ).hexdigest()
        return {
            "accessKey": self.access_key,
            "nonce": nonce,
            "timestamp": timestamp,
            "sign": sign,
        }

    def _check(self, payload):
        if payload.get("code") != "0":
            raise EcoflowError(payload.get("message") or "EcoFlow API error", payload)
        return payload

    def list_devices(self):
        response = requests.get(
            f"{self.base_url}/iot-open/sign/device/list",
            headers=self.headers_for(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return self._check(response.json())

    def get_quota(self):
        params = {"sn": self.sn}
        response = requests.get(
            f"{self.base_url}/iot-open/sign/device/quota/all",
            params=params,
            headers=self.headers_for(params),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return self._check(response.json())

    def set_quota(self, params):
        body = {
            "sn": self.sn,
            "moduleType": 0,
            "operateType": "TCP",
            "params": params,
        }
        response = requests.put(
            f"{self.base_url}/iot-open/sign/device/quota",
            json=body,
            headers=self.headers_for(body),
            timeout=self.timeout,
        )
        return response.status_code, response.text

    def device_record(self):
        payload = self.list_devices()
        for device in payload.get("data") or []:
            if device.get("sn") == self.sn:
                return device
        return None
