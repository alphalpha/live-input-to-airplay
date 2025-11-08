#!/usr/bin/env python3
import os
import time
import httpx
from pyhap.accessory import Accessory, Bridge
from pyhap.accessory_driver import AccessoryDriver
from pyhap.const import CATEGORY_SWITCH

# --- Environment variables configuration (added, non-invasive) ---
BACKEND_ENDPOINT = os.getenv("BACKEND_ENDPOINT", "http://127.0.0.1:8080/api")
HAP_ADDRESS = os.getenv("HAP_ADDRESS", "0.0.0.0")
HOMEKIT_DATA_DIR = os.getenv("HOMEKIT_DATA_DIR", "./data")
os.makedirs(HOMEKIT_DATA_DIR, exist_ok=True)
persist_file = os.path.join(HOMEKIT_DATA_DIR, "record_player.state")

print(f"[homekit] BACKEND_ENDPOINT={BACKEND_ENDPOINT}")
print(f"[homekit] HAP_ADDRESS={HAP_ADDRESS}")
print(f"[homekit] HOMEKIT_DATA_DIR={HOMEKIT_DATA_DIR}")
print(f"[homekit] persist_file={persist_file}")
# ---------------------------------------------------------------

POLL_INTERVAL = 5.0
HTTP_TIMEOUT = 10.0


class RecordPlayerMain(Accessory):
    """Main HomeKit switch controlling the record player web backend."""
    category = CATEGORY_SWITCH

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        serv_switch = self.add_preload_service("Switch")
        # setter must be sync for HAP-python; no coroutine here
        self.char_on = serv_switch.configure_char("On", setter_callback=self.set_on)
        # sync client to avoid event loop requirements
        self._client = httpx.Client(timeout=HTTP_TIMEOUT)

    # Toggle via backend
    def set_on(self, value):
        try:
            if value:
                self._client.post(f"{BACKEND_ENDPOINT}/start")
            else:
                self._client.post(f"{BACKEND_ENDPOINT}/stop")
        except Exception as e:
            print(f"[ERROR] Backend call failed: {e}")

    # Poll backend and reflect state in the characteristic
    # NOTE: HAP-python runs this in an accessory thread; keep it sync
    def run(self):
        while not self.driver.stop_event.is_set():
            try:
                r = self._client.get(f"{BACKEND_ENDPOINT}/status")
                if r.is_success:
                    data = r.json()
                    # Use the field names from your backend
                    state = bool(data.get("both_active")) or (
                        bool(data.get("owntone_active")) and bool(data.get("pipe_active"))
                    )
                    if state != self.char_on.value:
                        self.char_on.set_value(state)
            except Exception as e:
                print(f"[WARN] Status poll failed: {e}")
            time.sleep(POLL_INTERVAL)


class RecordPlayerBridge(Bridge):
    """HomeKit bridge that exposes the record player accessory."""
    def __init__(self, driver, display_name="Record Player"):
        super().__init__(driver, display_name)
        # PASS THE DRIVER to the Accessory subclass (required by HAP-python)
        self.add_accessory(RecordPlayerMain(self.driver, "Record Player Switch"))


def main():
    print(f"[INFO] Starting Record Player HomeKit bridge...")
    print(f"[INFO] Backend endpoint: {BACKEND_ENDPOINT}")
    print(f"[INFO] Binding address: {HAP_ADDRESS}")
    print(f"[INFO] Persist directory: {HOMEKIT_DATA_DIR}")

    driver = AccessoryDriver(
        port=51826,
        persist_file=persist_file,
        address=HAP_ADDRESS,
    )
    bridge = RecordPlayerBridge(driver, display_name="Record Player")
    driver.add_accessory(accessory=bridge)

    setup_code = "123-45-678"  # keep your preferred code
    print(f"[INFO] Use setup code: {setup_code}")
    driver.start()


if __name__ == "__main__":
    main()
