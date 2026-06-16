# MacPresence

Expose your Mac's **sleep/wake state** as a native HomeKit Occupancy Sensor — no Homebridge, no hub, no extra hardware.

When your Mac wakes up, the sensor reports **Occupied**. When it goes to sleep, it reports **Unoccupied**. Use it to trigger lights, scenes, or any HomeKit automation.

MacPresence only advertises on your trusted home network — it stays silent on VPN connections, public Wi-Fi, or any other network.

---

## How it works

MacPresence is a lightweight Python script that:
1. Listens for macOS `NSWorkspaceWillSleepNotification` and `NSWorkspaceDidWakeNotification` — native system events fired the moment you sleep or wake your Mac
2. Checks whether you're on a trusted network (correct interface + correct SSID) before advertising
3. Exposes itself on your local network as a native HomeKit accessory via the HomeKit Accessory Protocol (HAP)
4. Pushes state changes directly to HomeKit — instant and reliable
5. Restarts the HAP driver automatically when you switch networks, so it always re-announces on the new connection

RAM usage: ~25 MB. CPU: effectively 0%.

---

## Requirements

- macOS 11 or later (macOS 14 Sonoma or later recommended)
- Python 3.9+
- Python libraries: `HAP-python`, `pyobjc-framework-Cocoa`, `pyobjc-framework-CoreWLAN`
- A macOS Shortcut named **WifiSSID** (see installation step 2)

---

## Installation

### 1. Install dependencies

```bash
pip3 install HAP-python pyobjc-framework-Cocoa pyobjc-framework-CoreWLAN
```

> `pyobjc-framework-CoreWLAN` is kept as a fallback but on macOS 14.4+ it cannot read the SSID due to Apple's location privacy restrictions. The Shortcut method is the primary approach.

### 2. Create the WifiSSID Shortcut

This is required for SSID detection on macOS 14.4+, where Apple removed the `airport` command and restricted direct SSID access.

1. Open the **Shortcuts** app on your Mac
2. Click **+** to create a new shortcut
3. Search for and add **"Get Network Details"** — set it to **Wi-Fi** and **Network Name**
4. Search for and add **"Stop and Output"** — connect it to the result of step 3
5. Name the shortcut exactly: **`WifiSSID`**

Test it works:
```bash
shortcuts run "WifiSSID" --output-path /tmp/ssid.txt && cat /tmp/ssid.txt
```
You should see your network name printed. macOS will ask for permission the first time — allow it.

### 3. Configure your home network

Open `mac_presence.py` and set your home Wi-Fi name near the top:

```python
HOME_SSID = "YourNetworkName"
```

### 4. Place the script

```bash
mkdir ~/MacPresence
# copy mac_presence.py into ~/MacPresence/
```

### 5. Run it

```bash
python3 ~/MacPresence/mac_presence.py
```

You should see:

```
[MacPresence] INFO: MacPresence starting — allowed interfaces: {'en1', 'en0'}, home SSID: 'YourNetworkName'
[MacPresence] INFO: Listening for sleep/wake events
[MacPresence] INFO: Trusted network detected (Wi-Fi 'YourNetworkName' on en0) — starting HAP driver
[MacPresence] INFO: Pair in the Home app → Add Accessory → More Options → PIN: 021-82-017
```

If you're on VPN or a different network, you'll see:

```
[MacPresence] INFO: Not on trusted network (interface 'utun8' is not in allowed list) — HAP driver paused
```

This is expected — connect to your home Wi-Fi and it will start automatically.

### 6. Pair with the Home app

1. Make sure your iPhone/Mac and the MacPresence machine are on the same home network
2. Open **Home** on your iPhone or Mac
3. Tap **+** → **Add Accessory**
4. Choose **More Options** — you should see **MacPresence** appear
5. Enter the PIN: **021-82-017**
6. Assign it to a room and tap Done

The sensor is now live in HomeKit. Put your Mac to sleep — the sensor will flip to Unoccupied within a second.

---

## Auto-start at login (recommended)

### 1. Edit the plist

Open `com.user.macpresence.plist` and set the full absolute path to your script (no `~`):

```xml
<string>/Users/yourname/MacPresence/mac_presence.py</string>
```

### 2. Install the Launch Agent

```bash
cp com.user.macpresence.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.macpresence.plist
```

MacPresence will now start at every login and restart automatically if it ever crashes.

### Useful commands

```bash
# Check it's running (a PID in the first column means it's alive)
launchctl list | grep macpresence

# Stream live logs
tail -f /tmp/macpresence.log

# Restart after updating the script
launchctl kickstart -k gui/$(id -u)/com.user.macpresence

# Stop
launchctl unload ~/Library/LaunchAgents/com.user.macpresence.plist

# Start
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.macpresence.plist
```

---

## Configuration

Open `mac_presence.py` and adjust near the top:

| Setting | Default | Description |
|---|---|---|
| `HOME_SSID` | `"[Your Network Name]"` | Your home Wi-Fi name — must match exactly |
| `ALLOWED_INTERFACES` | `{"en0", "en1"}` | Physical interfaces to advertise on; VPN tunnels (`utun*`) are always excluded |
| `HOMEKIT_PORT` | `51827` | Network port (change if conflicting with Homebridge) |
| `PERSIST_DIR` | `~/.macpresence` | Where HomeKit pairing data is stored |
| `RESTART_DELAY_SECONDS` | `5` | How long to wait before restarting the HAP driver after a network change |

---

## Network trust logic

MacPresence uses two independent checks before advertising:

**Interface check** — the active interface must be `en0` or `en1`. macOS always assigns VPN tunnels to `utun*`, `ipsec*`, or `ppp*` interfaces, so this automatically blocks all VPN connections without any configuration.

**SSID check** — when on Wi-Fi, the network name must match `HOME_SSID` exactly. MacPresence reads the SSID via a macOS Shortcut (`WifiSSID`), which is the only method that works on macOS 14.4+ without Location Services permission. When on ethernet (no SSID present), this check is skipped — ethernet on an allowed interface is always trusted.

If either check fails, the HAP driver pauses silently. It restarts automatically once you're back on a trusted network.

---

## Cross-VLAN setup (IoT network + regular network)

If your HomePod is on a separate IoT VLAN and your Mac is on a regular VLAN, HomeKit discovery will fail by default because mDNS (Bonjour) doesn't cross subnets.

On **MikroTik RouterOS 7**, enable the built-in mDNS repeater:

```
/tool mdns-repeater set enabled=yes interfaces=[Select Bridges or VLANs]
```

Also open the HomeKit port between VLANs in your firewall:

```
/ip firewall filter
add chain=forward src-address=<regular-subnet> dst-address=<iot-subnet> protocol=tcp dst-port=51827 action=accept
add chain=forward src-address=<iot-subnet> dst-address=<regular-subnet> protocol=tcp dst-port=51827 action=accept
```

---

## Creating an automation

In the Home app:

1. Go to **Automations** → **+**
2. **An Accessory is Controlled** → select **MacPresence**
3. Trigger: **Occupancy Detected** → turn lights on
4. Add a second automation: **No Occupancy Detected** → turn lights off (optionally with a delay)

---

## Troubleshooting

**MacPresence doesn't appear in the Home app**
Make sure your iPhone and Mac are on the same network. Check the log — if it says "Not on trusted network", the SSID check may be failing. Verify `HOME_SSID` matches your network name exactly (case-sensitive).

**SSID reads as `None` or the wrong value**
On macOS 14.4+, Apple removed the `airport` utility and restricts SSID access for privacy reasons. MacPresence relies on the `WifiSSID` Shortcut — make sure it exists in the Shortcuts app and that you've run it at least once manually to grant permission. Test with: `shortcuts run "WifiSSID" --output-path /tmp/ssid.txt && cat /tmp/ssid.txt`

**Log says "Not on trusted network" but you are home**
Check the SSID is being read correctly by running the shortcut test above. Also verify `HOME_SSID` in the script matches your network name exactly — it is case-sensitive.

**Port conflict on restart**
Kill any lingering process before restarting: `lsof -ti :51827 | xargs kill -9 2>/dev/null; true`

**Want to re-pair?**
Delete `~/.macpresence/accessory.state`, restart the script, and pair again in the Home app.

**Sleep event fires but HomeKit doesn't update**
This is usually a network timing issue — Wi-Fi disconnects just before sleep completes. HomeKit will reconcile on the next wake.

---

## License

MIT
