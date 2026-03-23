import os
import serial
import serial.tools.list_ports
import subprocess
import json
import re


ESP32_VIDS = {0x10C4, 0x1A86, 0x0403, 0x2341, 0x303A}

HEAVY_FRAMEWORKS = [
    "react", "vue", "angular", "webpack", "next",
    "bootstrap.min.js", "jquery"
]

BLOCKED_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".zip", ".tar", ".gz"}

MAX_FILE_SIZE_MB = 1.0  # per file
RAM_PER_CONN_KB  = 20   # estimated per TCP connection


class ESPScanner:

    def detect_esp(self) -> dict:
        """Scan serial ports, find ESP32, interrogate hardware."""
        ports = list(serial.tools.list_ports.comports())
        esp_port = None

        for p in ports:
            if p.vid in ESP32_VIDS:
                esp_port = p.device
                break

        if not esp_port:
            # fallback: try common names
            candidates = [p for p in ports if any(
                k in (p.description or "").lower()
                for k in ["esp", "cp210", "ch340", "ftdi", "uart", "usb serial"]
            )]
            if candidates:
                esp_port = candidates[0].device

        if not esp_port:
            return {"found": False}

        return self._interrogate(esp_port)

    def _interrogate(self, port: str) -> dict:
        """Use esptool to read flash size and chip info."""
        info = {
            "found": True,
            "port":  port,
            "flash_size":   "Unknown",
            "spiffs_free":  "Unknown",
            "free_ram":     "Unknown",
            "cpu_freq":     "Unknown",
            "ip":           "esp32.local",
            # raw KB values for scanner
            "flash_size_kb":  4096,
            "spiffs_free_kb": 1800,
            "free_ram_kb":    200,
        }

        try:
            result = subprocess.run(
                ["python", "-m", "esptool", "--port", port, "flash_id"],
                capture_output=True, text=True, timeout=15
            )
            output = result.stdout + result.stderr

            # Parse flash size
            m = re.search(r"Detected flash size: (\S+)", output)
            if m:
                info["flash_size"] = m.group(1)
                size_str = m.group(1).upper()
                if "4MB" in size_str:
                    info["flash_size_kb"]  = 4096
                    info["spiffs_free_kb"] = 1800
                elif "8MB" in size_str:
                    info["flash_size_kb"]  = 8192
                    info["spiffs_free_kb"] = 5000
                elif "2MB" in size_str:
                    info["flash_size_kb"]  = 2048
                    info["spiffs_free_kb"] = 800
                elif "1MB" in size_str:
                    info["flash_size_kb"]  = 1024
                    info["spiffs_free_kb"] = 300

            # Parse chip
            m2 = re.search(r"Chip is (.+)", output)
            if m2:
                chip = m2.group(1).strip()
                info["chip"] = chip
                if "240MHz" in chip or "ESP32" in chip:
                    info["cpu_freq"]  = "240MHz"
                    info["free_ram"]  = "~214KB"
                    info["free_ram_kb"] = 214
                elif "160MHz" in chip:
                    info["cpu_freq"]  = "160MHz"

            info["spiffs_free"] = f"{info['spiffs_free_kb']}KB"
            info["free_ram"]    = f"{info['free_ram_kb']}KB"

        except Exception as e:
            info["esptool_error"] = str(e)

        return info

    def scan_files(self, file_paths: list, esp_info: dict) -> dict:
        """Evaluate files against real ESP32 hardware limits."""
        reasons   = []
        warnings  = []
        total_kb  = 0
        file_data = []

        spiffs_free_kb = esp_info.get("spiffs_free_kb", 1800)
        free_ram_kb    = esp_info.get("free_ram_kb",    200)

        for path in file_paths:
            if not os.path.isfile(path):
                continue

            name    = os.path.basename(path)
            ext     = os.path.splitext(name)[1].lower()
            size_kb = os.path.getsize(path) / 1024
            total_kb += size_kb

            entry = {"name": name, "size_kb": round(size_kb, 1), "ok": True, "notes": []}

            # Blocked file types
            if ext in BLOCKED_EXTENSIONS:
                entry["ok"] = False
                entry["notes"].append(f"Blocked type ({ext})")
                reasons.append(f"{name}: blocked file type {ext}")

            # Single file too large (>1MB)
            elif size_kb > MAX_FILE_SIZE_MB * 1024:
                entry["ok"] = False
                entry["notes"].append(f"File too large ({size_kb:.0f}KB > 1MB)")
                reasons.append(f"{name}: exceeds 1MB limit")

            # Heavy JS frameworks
            if ext == ".js":
                for fw in HEAVY_FRAMEWORKS:
                    if fw in name.lower():
                        entry["notes"].append(f"Heavy framework: {fw}")
                        warnings.append(f"{name}: heavy framework may cause RAM issues")

            file_data.append(entry)

        # Total size vs SPIFFS
        if total_kb > spiffs_free_kb:
            reasons.append(
                f"Total size {total_kb:.0f}KB exceeds available SPIFFS {spiffs_free_kb}KB"
            )

        # RAM check: at least 1 user must fit
        if RAM_PER_CONN_KB + 64 > free_ram_kb:
            reasons.append(
                f"Insufficient RAM: need {RAM_PER_CONN_KB + 64}KB minimum, have {free_ram_kb}KB"
            )

        # Calculate max concurrent users from actual free RAM
        # Reserve 64KB for system/stack, divide remainder by per-conn cost
        usable_ram_kb   = max(0, free_ram_kb - 64)
        max_users       = max(1, usable_ram_kb // RAM_PER_CONN_KB)

        # Re-check RAM with calculated max_users
        ram_needed = RAM_PER_CONN_KB * max_users
        if ram_needed > free_ram_kb:
            max_users = max(1, max_users - 1)

        hostable = len(reasons) == 0

        return {
            "hostable":        hostable,
            "reasons":         reasons,
            "warnings":        warnings,
            "files":           file_data,
            "total_size_kb":   round(total_kb, 1),
            "spiffs_free_kb":  spiffs_free_kb,
            "ram_per_user_kb": RAM_PER_CONN_KB,
            "max_users":       max_users,
            "notes":           "; ".join(warnings) if warnings else "Clean",
        }
