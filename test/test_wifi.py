#!/usr/bin/env python3
"""
ESP32 Wi-Fi Connection Tester
------------------------------
Run: python test_wifi.py
Tests serial communication and Wi-Fi credential sending to ESP32.
"""

import sys
import re
import time
import json
import subprocess
import serial
import serial.tools.list_ports


ESP32_VIDS = {0x10C4, 0x1A86, 0x0403, 0x2341, 0x303A}


# ── 1. Detect port ────────────────────────────────────────────────────────────

def find_port():
    print("\n[1] Scanning for ESP32...")
    ports = list(serial.tools.list_ports.comports())
    for p in ports:
        print(f"    {p.device}  |  {p.description}  |  VID:{p.vid}")
        if p.vid in ESP32_VIDS:
            print(f"    ✓ ESP32 found on {p.device}")
            return p.device

    fallback = [p for p in ports if any(
        k in (p.description or "").lower()
        for k in ["cp210", "ch340", "ftdi", "uart", "usb serial"]
    )]
    if fallback:
        print(f"    ✓ Likely ESP32 on {fallback[0].device}")
        return fallback[0].device

    print("    ✗ No ESP32 found.")
    return None


# ── 2. Detect PC Wi-Fi SSID ───────────────────────────────────────────────────

def get_ssid():
    print("\n[2] Detecting PC Wi-Fi SSID...")
    try:
        if sys.platform == "win32":
            out = subprocess.check_output(
                ["netsh", "wlan", "show", "interfaces"], text=True, timeout=5
            )
            for line in out.splitlines():
                if "SSID" in line and "BSSID" not in line:
                    ssid = line.split(":", 1)[1].strip()
                    print(f"    ✓ Connected to: {ssid}")
                    return ssid
        else:
            try:
                out = subprocess.check_output(
                    ["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"],
                    text=True, timeout=5
                )
                for line in out.splitlines():
                    if line.startswith("yes:"):
                        ssid = line.split(":", 1)[1].strip()
                        print(f"    ✓ Connected to: {ssid}")
                        return ssid
            except FileNotFoundError:
                pass
            try:
                out = subprocess.check_output(["iwgetid", "-r"], text=True, timeout=5)
                ssid = out.strip()
                if ssid:
                    print(f"    ✓ Connected to: {ssid}")
                    return ssid
            except FileNotFoundError:
                pass
    except Exception as e:
        print(f"    ✗ Error: {e}")

    print("    ✗ Could not detect SSID")
    return None


# ── 3. Serial read test ───────────────────────────────────────────────────────

def test_serial_read(port):
    print(f"\n[3] Listening on {port} for 5 seconds...")
    print("    Press RESET on ESP32 now.")
    try:
        s = serial.Serial(port, 115200, timeout=1)
        time.sleep(0.5)
        s.reset_input_buffer()
        got_data = False
        deadline = time.time() + 5
        while time.time() < deadline:
            line = s.readline().decode("utf-8", errors="replace").strip()
            if line:
                print(f"    ← {line}")
                got_data = True
        s.close()
        if got_data:
            print("    ✓ ESP32 is sending serial data")
        else:
            print("    ⚠ No serial data received (ESP32 may be idle, continuing anyway)")
        return True
    except Exception as e:
        print(f"    ✗ Serial error: {e}")
        return False


# ── 4. Send Wi-Fi credentials ─────────────────────────────────────────────────

def send_wifi(port, ssid, password):
    print(f"\n[4] Sending Wi-Fi credentials...")
    print(f"    SSID:     {ssid}")
    print(f"    Password: {'*' * len(password)}")

    try:
        s = serial.Serial(port, 115200, timeout=1)
        time.sleep(1)
        cmd = json.dumps({"cmd": "setwifi", "ssid": ssid, "pass": password})
        print(f"    → {cmd}")
        s.write((cmd + "\n").encode())
        time.sleep(0.5)
        s.close()
        print("    ✓ Credentials sent. Closing port.")
        return True
    except Exception as e:
        print(f"    ✗ Failed to send: {e}")
        return False


# ── 5. Wait for IP ────────────────────────────────────────────────────────────

def wait_for_ip(port, timeout=40):
    print(f"\n[5] Waiting for ESP32 to connect to Wi-Fi (up to {timeout}s)...")
    print("    ESP32 will restart — port will disappear briefly, that is normal.")

    ip_pattern = re.compile(r'READY ip=([\d.]+)')
    deadline = time.time() + timeout
    s = None

    while time.time() < deadline:
        remaining = int(deadline - time.time())
        try:
            if s is None or not s.is_open:
                s = serial.Serial(port, 115200, timeout=1)
                print(f"    Serial reconnected ({remaining}s remaining)")

            line = s.readline().decode("utf-8", errors="replace").strip()
            if line:
                print(f"    ← {line}")

            if "READY ip=" in line:
                m = ip_pattern.search(line)
                if m:
                    ip = m.group(1)
                    if s: s.close()
                    print(f"\n    ✓ ESP32 connected!")
                    print(f"    ✓ IP address: {ip}")
                    return ip

            if "wrong password" in line.lower():
                if s: s.close()
                print("    ✗ Wrong Wi-Fi password.")
                return None

            if "no ap found" in line.lower():
                if s: s.close()
                print("    ✗ Wi-Fi network not found. Check SSID.")
                return None

        except serial.SerialException:
            if s:
                try: s.close()
                except: pass
                s = None
            time.sleep(1)
        except Exception as e:
            print(f"    ✗ Error: {e}")
            break

    if s:
        try: s.close()
        except: pass

    print("    ✗ Timed out.")
    return None


# ── 6. HTTP test ──────────────────────────────────────────────────────────────

def test_http(ip):
    print(f"\n[6] Testing HTTP server at {ip}...")
    try:
        import urllib.request
        url = f"http://{ip}/esphost-health"
        with urllib.request.urlopen(url, timeout=5) as r:
            body = r.read().decode()
            print(f"    ✓ HTTP response: {body[:100]}")
            return True
    except Exception as e:
        print(f"    ✗ HTTP failed: {e}")
        print(f"    → This is OK if SPIFFS files are not flashed yet.")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 54)
    print("  ESP32 WI-FI CONNECTION TESTER")
    print("=" * 54)

    port = find_port()
    if not port:
        print("\nFix: plug in ESP32 and retry.")
        return

    ssid = get_ssid()
    if not ssid:
        ssid = input("\n    Enter Wi-Fi SSID manually: ").strip()

    password = input(f"\n    Enter password for '{ssid}': ")

    serial_ok = test_serial_read(port)
    if not serial_ok:
        print("\nFix: check USB cable and port permissions.")
        return

    sent = send_wifi(port, ssid, password)
    if not sent:
        return

    ip = wait_for_ip(port)
    if not ip:
        print("\nDIAGNOSIS:")
        print("  1. Wrong password       → re-run with correct password")
        print("  2. Wrong SSID           → check exact Wi-Fi name (case-sensitive)")
        print("  3. ESP32 not restarting → press RESET manually after credentials sent")
        print("  4. Firmware not flashed → flash via esphost first")
        return

    test_http(ip)

    print("\n" + "=" * 54)
    print("  RESULT")
    print("=" * 54)
    print(f"  ESP32 IP:   {ip}")
    print(f"  Access at:  http://{ip}")
    print(f"  Health:     http://{ip}/esphost-health")
    print("=" * 54)


if __name__ == "__main__":
    main()
