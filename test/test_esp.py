#!/usr/bin/env python3
"""
ESP32 Flash Tester
------------------
Run: python test_esp.py
Diagnoses why ESP32 is not flashable.
"""

import sys
import time
import subprocess
import serial
import serial.tools.list_ports

PORT    = None   # auto-detect
BAUDS   = [115200, 921600, 460800, 230400, 74880]
TIMEOUT = 5


# ── 1. Port detection ─────────────────────────────────────────────────────────

def find_port():
    print("\n[1] Scanning serial ports...")
    ports = list(serial.tools.list_ports.comports())

    if not ports:
        print("    ✗ No serial ports found at all.")
        print("      → Check USB cable (must be data cable, not charge-only)")
        print("      → Try different USB port on your PC")
        return None

    for p in ports:
        print(f"    Found: {p.device}  |  {p.description}  |  VID:PID {p.vid}:{p.pid}")

    esp_vids = {0x10C4, 0x1A86, 0x0403, 0x2341, 0x303A}
    for p in ports:
        if p.vid in esp_vids:
            print(f"    ✓ ESP32 likely on {p.device}")
            return p.device

    # fallback: return first port
    fallback = ports[0].device
    print(f"    ⚠ No known ESP VID matched. Using first port: {fallback}")
    return fallback


# ── 2. Port open test ─────────────────────────────────────────────────────────

def test_port_open(port):
    print(f"\n[2] Testing port open: {port}")
    try:
        s = serial.Serial(port, 115200, timeout=1)
        s.close()
        print(f"    ✓ Port {port} opened successfully")
        return True
    except PermissionError:
        print(f"    ✗ Permission denied: {port}")
        print(f"      → Run: sudo chmod 666 {port}")
        print(f"      → Or:  sudo usermod -aG dialout $USER  (then relogin)")
        return False
    except serial.SerialException as e:
        print(f"    ✗ Could not open port: {e}")
        return False


# ── 3. Serial data test ───────────────────────────────────────────────────────

def test_serial_output(port):
    print(f"\n[3] Listening for serial output at multiple baud rates...")
    print(f"    Press RESET on your ESP32 now if you haven't already.")
    time.sleep(1)

    for baud in BAUDS:
        print(f"    Trying {baud} baud...", end=" ", flush=True)
        try:
            s = serial.Serial(port, baud, timeout=2)
            s.reset_input_buffer()
            data = s.read(64)
            s.close()
            if data:
                print(f"✓ Got {len(data)} bytes")
                try:
                    print(f"    Data: {data.decode('utf-8', errors='replace')[:80]}")
                except Exception:
                    print(f"    Raw: {data.hex()}")
                return True
            else:
                print("no data")
        except Exception as e:
            print(f"error: {e}")

    print("    ✗ No serial output at any baud rate.")
    print("      → ESP32 may not be powered properly")
    print("      → Try pressing RESET while this script runs")
    return False


# ── 4. esptool chip_id test ───────────────────────────────────────────────────

def test_esptool(port):
    print(f"\n[4] Testing esptool connection at multiple baud rates...")
    print(f"    If your board has a BOOT/GPIO0 pin, hold it LOW now.")
    time.sleep(1)

    for baud in [115200, 230400, 460800]:
        print(f"    Trying esptool at {baud} baud...", end=" ", flush=True)
        try:
            result = subprocess.run(
                [
                    sys.executable, "-m", "esptool",
                    "--port", port,
                    "--baud", str(baud),
                    "--connect-attempts", "3",
                    "chip_id"
                ],
                capture_output=True, text=True, timeout=15
            )
            output = result.stdout + result.stderr

            if "Chip is" in output or "chip id" in output.lower():
                print("✓ Connected!")
                # Extract chip info
                for line in output.splitlines():
                    if any(k in line for k in ["Chip is", "Crystal", "MAC", "flash size"]):
                        print(f"    {line.strip()}")
                return True
            elif "No serial data received" in output:
                print("no serial data")
            elif "Permission" in output:
                print(f"permission denied → run: sudo chmod 666 {port}")
                return False
            else:
                print(f"failed")
        except subprocess.TimeoutExpired:
            print("timeout")
        except Exception as e:
            print(f"error: {e}")

    print("\n    ✗ esptool could not connect.")
    return False


# ── 5. Diagnosis ──────────────────────────────────────────────────────────────

def diagnose(port_found, port_open, serial_ok, esptool_ok):
    print("\n" + "─" * 50)
    print("DIAGNOSIS")
    print("─" * 50)

    if not port_found:
        print("""
PROBLEM: ESP32 not detected on any port.

FIXES:
  1. Try a different USB cable (data cable, not charge-only)
     Test: plug in and run `lsusb` — if no new device appears, cable is dead
  2. Try a different USB port on your computer
  3. Install CH340 driver (if your board uses CH340 USB chip):
       sudo apt install ch341  (Ubuntu/Debian)
  4. Check if ESP32 is actually powered (LED on?)
""")
        return

    if not port_open:
        print(f"""
PROBLEM: Port found but cannot be opened ({port_found}).

FIXES:
  1. Quick fix (this session):
       sudo chmod 666 {port_found}
  2. Permanent fix (need relogin):
       sudo usermod -aG dialout $USER
  3. Check if another app is using the port (Arduino IDE, minicom, etc.)
       lsof {port_found}
""")
        return

    if not serial_ok:
        print("""
PROBLEM: Port opens but ESP32 sends no data.

FIXES:
  1. Press RESET button on ESP32
  2. Check USB cable — try a known-good data cable
  3. ESP32 may be bricked — try:
       python -m esptool --port PORT erase_flash
  4. Power issue — try powering ESP32 from 3.3V external supply
""")

    if not esptool_ok:
        print("""
PROBLEM: esptool cannot enter flash mode.

FIXES:
  1. Your board has no auto-reset circuit.
     Manual boot mode:
       a. Connect GPIO0 to GND with a wire
       b. Press RESET
       c. Remove wire
       d. Run flash immediately

  2. Try erasing first:
       python -m esptool --port PORT --baud 115200 erase_flash

  3. Some boards need --before no_reset:
       python -m esptool --port PORT --before no_reset chip_id
""")
        return

    print("✓ ESP32 is flashable. No issues detected.")
    print(f"  Run esphost and flash on port: {port_found}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("  ESP32 FLASH TESTER")
    print("=" * 50)

    port = find_port()
    if not port:
        diagnose(False, False, False, False)
        return

    port_open  = test_port_open(port)
    if not port_open:
        diagnose(port, False, False, False)
        return

    serial_ok  = test_serial_output(port)
    esptool_ok = test_esptool(port)

    diagnose(port, port_open, serial_ok, esptool_ok)


if __name__ == "__main__":
    main()
