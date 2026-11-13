"""
EcoFlow Open API (EU) - DELTA Pro helper.

Usage (keys come from environment variables):
  python ecoflow_delta_pro.py devices              # list devices bound to the account
  python ecoflow_delta_pro.py status               # read-only: dump quota
  python ecoflow_delta_pro.py charge-pause on      # pause AC charging  (unverified on Delta Pro)
  python ecoflow_delta_pro.py charge-pause off     # resume AC charging
  python ecoflow_delta_pro.py charge-power 200     # AC charging power from grid, watts
  python ecoflow_delta_pro.py charge-limit 80      # max charge level, percent (unverified)
  python ecoflow_delta_pro.py usb on|off           # USB outputs (unverified)
"""
import json
import sys

from ecoflow_client import EcoflowClient


def status(client):
    data = client.get_quota()
    values = data.get("data", {})
    print(f"Total fields: {len(values)}")
    for key in sorted(values):
        lowered = key.lower()
        if any(token in lowered for token in ("chg", "usb", "soc", "wattsin", "wattsout", "acin", "acout")):
            print(f"{key} = {values[key]}")
    with open("quota_all.json", "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    print("Full response saved to quota_all.json")


def send_command(client, params):
    """Delta Pro commands use moduleType 0, operateType TCP, params with an 'id'."""
    status_code, text = client.set_quota(params)
    print("SET:", status_code, text)


def set_charge_pause(client, paused):
    # Seen working on DELTA Max (id 69, chgPause 0/1); NOT confirmed on Delta Pro.
    send_command(client, {"id": 69, "chgPause": 1 if paused else 0})


def set_charge_power(client, watts):
    # AC charging power from grid ("slowChgPower"), id 69. Seen used with Delta Pro.
    send_command(client, {"id": 69, "slowChgPower": int(watts)})


def set_charge_limit(client, percent):
    # Max charge level, id 49 "maxChgSoc" (from memory of community projects, unverified).
    send_command(client, {"id": 49, "maxChgSoc": int(percent)})


def set_usb(client, on):
    # Unverified for Delta Pro.
    send_command(client, {"id": 34, "enabled": 1 if on else 0})


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return
    cmd = argv[1]
    arg = argv[2] if len(argv) > 2 else None
    client = EcoflowClient.from_env()
    if cmd == "devices":
        print(json.dumps(client.list_devices(), indent=2))
    elif cmd == "status":
        status(client)
    elif cmd == "charge-pause" and arg in ("on", "off"):
        set_charge_pause(client, arg == "on")
    elif cmd == "charge-power" and arg:
        set_charge_power(client, arg)
    elif cmd == "charge-limit" and arg:
        set_charge_limit(client, arg)
    elif cmd == "usb" and arg in ("on", "off"):
        set_usb(client, arg == "on")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
