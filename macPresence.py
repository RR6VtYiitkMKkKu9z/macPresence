#!/usr/bin/env python3
"""
MacPresence — expose your Mac's sleep/wake state as a HomeKit Occupancy Sensor.
https://github.com/RR6VtYiitkMKkKu9z/MacPresence

The sensor reports "Occupancy Detected" when the Mac is awake,
and "Occupancy Not Detected" when the Mac goes to sleep.

Only advertises on trusted home networks — never on VPN or unknown Wi-Fi.

Dependencies:
    pip3 install HAP-python pyobjc-framework-Cocoa
"""

import logging
import signal
import os
import socket
import subprocess
import threading
import time

import objc
from Cocoa import (
    NSObject,
    NSWorkspace,
    NSWorkspaceWillSleepNotification,
    NSWorkspaceDidWakeNotification,
)
from PyObjCTools import AppHelper

from pyhap.accessory import Accessory
from pyhap.accessory_driver import AccessoryDriver
from typing import Optional
from pyhap.const import CATEGORY_SENSOR

# ── Configuration ─────────────────────────────────────────────────────────────

HOMEKIT_PORT = 51827              # Change if this conflicts with another service
PERSIST_DIR  = "~/.macpresence"  # HomeKit pairing state is stored here

# Only advertise on these physical interfaces (Wi-Fi and ethernet).
# VPN tunnels on macOS always use utun*, ipsec*, or ppp* — never en0/en1.
ALLOWED_INTERFACES = {"en0", "en1"}

# Only advertise when connected to this Wi-Fi SSID.
# Ethernet connections bypass the SSID check (no SSID exists on ethernet).
HOME_SSID = "YourNetworkName"

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MacPresence] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Network trust checks ──────────────────────────────────────────────────────

def get_active_interface() -> Optional[str]:
    """
    Return the name of the interface carrying the default route (e.g. 'en0'),
    or None if no default route exists.
    """
    try:
        result = subprocess.run(
            ["route", "-n", "get", "default"],
            capture_output=True, text=True, timeout=3
        )
        for line in result.stdout.splitlines():
            if "interface:" in line:
                return line.split(":")[-1].strip()
    except Exception as e:
        log.warning("Could not determine active interface: %s", e)
    return None


def get_wifi_ssid() -> Optional[str]:
    """
    Return the SSID of the currently connected Wi-Fi network, or None.

    Uses a macOS Shortcut named "WifiSSID" as the primary method — the only
    reliable way to get the SSID on macOS 14.4+ without Location Services
    permission. Falls back to CoreWLAN and networksetup for older macOS.
    """
    # Method 1: macOS Shortcut (works on Sonoma 14.4+ where airport is removed)
    try:
        result = subprocess.run(
            ["shortcuts", "run", "WifiSSID", "--output-path", "-"],
            capture_output=True, timeout=5
        )
        ssid = result.stdout.decode("utf-8", errors="ignore").strip()
        if ssid:
            return ssid
    except Exception:
        pass

    # Method 2: CoreWLAN via PyObjC (works on older macOS)
    try:
        from CoreWLAN import CWWiFiClient
        client = CWWiFiClient.sharedWiFiClient()
        interface = client.interface()
        if interface is not None:
            ssid = interface.ssid()
            if ssid and ssid != "Wi-Fi":
                return ssid
    except Exception:
        pass

    # Method 3: networksetup (works on macOS 13 and earlier)
    try:
        result = subprocess.run(
            ["networksetup", "-getairportnetwork", "en0"],
            capture_output=True, text=True, timeout=3
        )
        out = result.stdout.strip()
        if "Current Wi-Fi Network:" in out:
            return out.split("Current Wi-Fi Network:")[-1].strip()
    except Exception:
        pass

    return None


def is_on_ethernet(interface: str) -> bool:
    """Return True if the interface looks like a wired ethernet port."""
    # en0 is Wi-Fi on most Macs, en1+ can be ethernet adapters or Thunderbolt bridges.
    # We check for an SSID: if there is none on an allowed interface, it's ethernet.
    return interface in ALLOWED_INTERFACES and get_wifi_ssid() is None


def is_trusted_network():
    """
    Return (trusted: bool, reason: str).
    Trusted when:
      - Active interface is en0 or en1 (not a VPN tunnel), AND
      - Either connected to HOME_SSID (Wi-Fi), or no SSID present (ethernet)
    """
    interface = get_active_interface()

    if interface is None:
        return False, "no default route / no network"

    if interface not in ALLOWED_INTERFACES:
        return False, f"interface {interface!r} is not in allowed list (VPN or unknown)"

    ssid = get_wifi_ssid()

    if ssid is None:
        # No Wi-Fi SSID → assume ethernet on an allowed interface → trusted
        return True, f"ethernet on {interface}"

    if ssid == HOME_SSID:
        return True, f"Wi-Fi '{ssid}' on {interface}"

    return False, f"Wi-Fi '{ssid}' is not the home network"



def wait_for_network(timeout: int = 5) -> bool:
    """
    After wake, poll until the network is ready (trusted network detected)
    or timeout is reached. Returns True if network came up, False if timed out.
    """
    log.info("Waiting for network to be ready after wake…")
    for _ in range(timeout):
        trusted, reason = is_trusted_network()
        if trusted:
            log.info("Network ready (%s)", reason)
            return True
        time.sleep(1)
    log.warning("Network did not become ready within %ds after wake", timeout)
    return False


# ── Sleep/Wake listener (runs on the macOS main thread via NSRunLoop) ─────────

class SleepWakeListener(NSObject):
    """
    Objective-C object that receives NSWorkspace sleep/wake notifications.
    Must live on the main thread's run loop — PyObjC handles this automatically.
    """

    def initWithCallback_(self, callback):
        self = objc.super(SleepWakeListener, self).init()
        if self is not None:
            self._callback = callback
            nc = NSWorkspace.sharedWorkspace().notificationCenter()
            nc.addObserver_selector_name_object_(
                self, "handleSleep:", NSWorkspaceWillSleepNotification, None
            )
            nc.addObserver_selector_name_object_(
                self, "handleWake:", NSWorkspaceDidWakeNotification, None
            )
            log.info("Listening for sleep/wake events")
        return self

    def handleSleep_(self, notification):
        log.info("System going to sleep → Unoccupied")
        self._callback(occupied=False)

def handleWake_(self, notification):
    log.info("System woke up — waiting for network before updating HomeKit")
    def deferred_wake():
        if wait_for_network(timeout=5):
            self._callback(occupied=True)
        else:
            log.info("Woke up but not on trusted network — skipping HomeKit update")
    threading.Thread(target=deferred_wake, daemon=True).start()


# ── HomeKit Accessory ─────────────────────────────────────────────────────────

class MacPresence(Accessory):
    """A HomeKit Occupancy Sensor driven by macOS sleep/wake events."""

    category = CATEGORY_SENSOR

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        svc = self.add_preload_service("OccupancySensor")
        self.occupancy = svc.get_characteristic("OccupancyDetected")

        # Mac is awake right now (we're running), so start as Occupied
        self.occupancy.set_value(1)
        log.info("MacPresence accessory ready — currently: Occupied (awake)")

    def set_occupied(self, occupied: bool):
        """Called from the sleep/wake listener to push state into HomeKit."""
        value = 1 if occupied else 0
        self.occupancy.set_value(value)
        log.info("HomeKit updated → %s", "Occupied" if occupied else "Unoccupied")


# ── HAP driver loop ───────────────────────────────────────────────────────────

RESTART_DELAY_SECONDS = 8

def run_hap_driver(persist_path: str, get_accessory, restart_event: threading.Event):
    """
    Runs the HAP driver in a loop on a background thread.
    Only starts when on a trusted network. Stops and waits when not trusted.
    Restarts on network change or crash.
    """
    while True:
        trusted, reason = is_trusted_network()

        if not trusted:
            log.info("Not on trusted network (%s) — HAP driver paused", reason)
            # Wait for a restart_event signal (triggered by network watcher)
            restart_event.wait()
            restart_event.clear()
            continue

        log.info("Trusted network detected (%s) — starting HAP driver", reason)
        driver = None
        try:
            driver = AccessoryDriver(
                port=HOMEKIT_PORT,
                persist_file=os.path.join(persist_path, "accessory.state"),
                zeroconf_server="0.0.0.0",
            )
            accessory = get_accessory(driver)
            driver.add_accessory(accessory=accessory)
            run_hap_driver.current_accessory = accessory

            log.info("HAP driver started on port %d", HOMEKIT_PORT)
            restart_event.clear()

            # Run driver in its own thread so we can watch for network changes
            t = threading.Thread(target=driver.start, daemon=True)
            t.start()

            while t.is_alive():
                if restart_event.wait(timeout=1):
                    log.info("Network change detected — stopping HAP driver")
                    driver.stop()
                    break

        except Exception as e:
            log.warning("HAP driver error (%s: %s) — restarting in %ds",
                        type(e).__name__, e, RESTART_DELAY_SECONDS)
        finally:
            run_hap_driver.current_accessory = None
            if driver:
                try:
                    driver.stop()
                except Exception:
                    pass

        time.sleep(RESTART_DELAY_SECONDS)

run_hap_driver.current_accessory = None


# ── Network watcher ───────────────────────────────────────────────────────────

def watch_network(restart_event: threading.Event):
    """
    Polls the active interface and SSID every 10 seconds.
    Signals the HAP driver to re-evaluate whenever anything changes.
    """
    last_interface = get_active_interface()
    last_ssid      = get_wifi_ssid()
    log.info("Network watcher started — interface: %s, SSID: %s",
             last_interface, last_ssid or "ethernet/none")

    while True:
        time.sleep(10)
        interface = get_active_interface()
        ssid      = get_wifi_ssid()

        if interface != last_interface or ssid != last_ssid:
            log.info(
                "Network changed — interface: %s→%s, SSID: %s→%s",
                last_interface, interface,
                last_ssid or "none", ssid or "none",
            )
            last_interface = interface
            last_ssid      = ssid
            restart_event.set()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    persist_path = os.path.expanduser(PERSIST_DIR)
    os.makedirs(persist_path, exist_ok=True)

    def make_accessory(driver):
        return MacPresence(driver, "MacPresence")

    restart_event = threading.Event()

    threading.Thread(
        target=run_hap_driver,
        args=(persist_path, make_accessory, restart_event),
        daemon=True,
    ).start()

    threading.Thread(
        target=watch_network,
        args=(restart_event,),
        daemon=True,
    ).start()

    log.info("MacPresence starting — allowed interfaces: %s, home SSID: '%s'",
             ALLOWED_INTERFACES, HOME_SSID)
    log.info("Pair in the Home app → Add Accessory → More Options → PIN: 021-82-017")

    def on_sleep_wake(occupied: bool):
        acc = run_hap_driver.current_accessory
        if acc is not None:
            acc.set_occupied(occupied)
        else:
            log.info("Sleep/wake event ignored — not on trusted network")

    signal.signal(signal.SIGTERM, lambda *_: AppHelper.stopEventLoop())
    listener = SleepWakeListener.alloc().initWithCallback_(on_sleep_wake)  # noqa: F841

    try:
        AppHelper.runConsoleEventLoop()
    except KeyboardInterrupt:
        log.info("Shutting down…")


if __name__ == "__main__":
    main()
