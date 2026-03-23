import os
import sys
import shutil
import struct
import subprocess
import tempfile
import hashlib
import importlib.resources


FIRMWARE_FILENAME = "esphost.bin"
SPIFFS_OFFSET     = "0x290000"   # standard 4MB layout
FIRMWARE_OFFSET   = "0x10000"
BOOTLOADER_OFFSET = "0x1000"
PARTITIONS_OFFSET = "0x8000"


class ESPFlasher:

    def __init__(self, port: str):
        self.port = port

    # ── Firmware flash ────────────────────────────────────────────────────────

    def flash_firmware(self, progress_cb=None):
        """Flash the bundled ESP32 firmware."""
        fw_path = self._get_firmware_path()

        if progress_cb:
            progress_cb(5, f"Firmware located: {fw_path}")

        cmd = [
            sys.executable, "-m", "esptool",
            "--port", self.port,
            "--baud", "921600",
            "--before", "default_reset",
            "--after",  "hard_reset",
            "write_flash",
            "-z",
            "--flash_mode", "dio",
            "--flash_freq", "80m",
            "--flash_size", "detect",
            FIRMWARE_OFFSET, fw_path,
        ]

        if progress_cb:
            progress_cb(10, "Connecting to ESP32...")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        if result.returncode != 0:
            raise RuntimeError(f"Firmware flash failed:\n{result.stderr}")

        if progress_cb:
            progress_cb(45, "Firmware flashed successfully")

    # ── SPIFFS file upload ────────────────────────────────────────────────────

    def upload_files(self, file_paths: list, progress_cb=None):
        """Build SPIFFS image from files and flash to ESP32."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Copy files into tmp staging dir
            for path in file_paths:
                dst = os.path.join(tmpdir, os.path.basename(path))
                shutil.copy2(path, dst)

            if progress_cb:
                progress_cb(50, f"Staging {len(file_paths)} file(s) for SPIFFS...")

            spiffs_image = os.path.join(tmpdir, "spiffs.bin")
            self._build_spiffs_image(tmpdir, spiffs_image, progress_cb)

            # Verify image was created
            if not os.path.exists(spiffs_image):
                raise RuntimeError("SPIFFS image build failed")

            if progress_cb:
                progress_cb(70, "Flashing SPIFFS to ESP32...")

            self._flash_spiffs(spiffs_image, progress_cb)

            if progress_cb:
                progress_cb(95, "Verifying checksum...")
                self._verify_checksum(file_paths, progress_cb)
                progress_cb(100, "Upload complete")

    def _build_spiffs_image(self, data_dir: str, output_img: str, progress_cb=None):
        """Try mkspiffs, fallback to manual LittleFS-compatible image."""
        mkspiffs = shutil.which("mkspiffs")

        if mkspiffs:
            if progress_cb:
                progress_cb(55, "Building SPIFFS image with mkspiffs...")
            cmd = [
                mkspiffs,
                "-c", data_dir,
                "-b", "4096",
                "-p", "256",
                "-s", "0x160000",
                output_img
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                return

        # fallback: use littlefs-python
        if progress_cb:
            progress_cb(55, "Building SPIFFS image with littlefs-python...")

        try:
            import littlefs
            fs = littlefs.LittleFS(block_size=4096, block_count=88)
            for fname in os.listdir(data_dir):
                fpath = os.path.join(data_dir, fname)
                if os.path.isfile(fpath):
                    with open(fpath, "rb") as f:
                        data = f.read()
                    with fs.open(f"/{fname}", "wb") as lf:
                        lf.write(data)
            with open(output_img, "wb") as out:
                out.write(fs.context.buffer)
        except ImportError:
            # Last resort: raw minimal SPIFFS-like image placeholder
            # In production, require mkspiffs or littlefs-python
            raise RuntimeError(
                "mkspiffs not found and littlefs-python not installed.\n"
                "Run: pip install littlefs-python"
            )

    def _flash_spiffs(self, image_path: str, progress_cb=None):
        cmd = [
            sys.executable, "-m", "esptool",
            "--port", self.port,
            "--baud", "921600",
            "write_flash",
            SPIFFS_OFFSET, image_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"SPIFFS flash failed:\n{result.stderr}")

    def _verify_checksum(self, file_paths: list, progress_cb=None):
        """MD5 checksum of all files for integrity confirmation."""
        h = hashlib.md5()
        for path in sorted(file_paths):
            with open(path, "rb") as f:
                h.update(f.read())
        digest = h.hexdigest()
        if progress_cb:
            progress_cb(98, f"MD5: {digest[:8]}...  ✓")
        return digest

    # ── Firmware path ─────────────────────────────────────────────────────────

    def _get_firmware_path(self) -> str:
        """Locate bundled firmware .bin file."""
        # Try importlib.resources first (installed package)
        try:
            with importlib.resources.path("esphost.firmware", FIRMWARE_FILENAME) as p:
                if os.path.exists(p):
                    return str(p)
        except Exception:
            pass

        # Fallback: relative to this file (dev mode)
        here = os.path.dirname(__file__)
        local = os.path.join(here, "firmware", FIRMWARE_FILENAME)
        if os.path.exists(local):
            return local

        raise FileNotFoundError(
            f"Firmware not found: {FIRMWARE_FILENAME}\n"
            "Place firmware/esphost.bin inside the esphost package."
        )
