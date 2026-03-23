import subprocess
import sys


def get_current_ssid() -> str | None:
    """Detect the Wi-Fi SSID the PC is currently connected to."""
    try:
        if sys.platform == "win32":
            out = subprocess.check_output(
                ["netsh", "wlan", "show", "interfaces"],
                text=True, timeout=5
            )
            for line in out.splitlines():
                if "SSID" in line and "BSSID" not in line:
                    return line.split(":", 1)[1].strip()

        else:  # Linux / macOS
            # Try nmcli first (most Linux distros)
            try:
                out = subprocess.check_output(
                    ["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"],
                    text=True, timeout=5
                )
                for line in out.splitlines():
                    if line.startswith("yes:"):
                        return line.split(":", 1)[1].strip()
            except FileNotFoundError:
                pass

            # Fallback: iwgetid
            try:
                out = subprocess.check_output(
                    ["iwgetid", "-r"],
                    text=True, timeout=5
                )
                return out.strip() or None
            except FileNotFoundError:
                pass

    except Exception:
        pass

    return None
