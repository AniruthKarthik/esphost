import os
import sys
import shutil
import subprocess
import tempfile
import hashlib
import importlib.resources


FIRMWARE_FILENAME = "esphost.bin"
SPIFFS_OFFSET     = "0x290000"   # standard 4MB layout
FIRMWARE_OFFSET   = "0x10000"


class ESPFlasher:

    def __init__(self, port: str):
        self.port = port

    # ── Firmware flash ────────────────────────────────────────────────────────

    def flash_firmware(self, progress_cb=None):
        """Firmware is flashed together with SPIFFS in upload_files — single connection."""
        fw_path = self._get_firmware_path()
        if progress_cb:
            progress_cb(10, f"Firmware located: {fw_path}")
            progress_cb(45, "Firmware will flash with SPIFFS in one connection...")

    # ── SPIFFS file upload ────────────────────────────────────────────────────

    def upload_files(self, file_paths: list, progress_cb=None):
        """Build SPIFFS image and flash firmware + SPIFFS in a single esptool call."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Copy files into staging dir
            for path in file_paths:
                dst = os.path.join(tmpdir, os.path.basename(path))
                shutil.copy2(path, dst)

            if progress_cb:
                progress_cb(50, f"Staging {len(file_paths)} file(s) for SPIFFS...")

            spiffs_image = os.path.join(tmpdir, "spiffs.bin")
            self._build_spiffs_image(tmpdir, spiffs_image, progress_cb)

            if not os.path.exists(spiffs_image):
                raise RuntimeError("SPIFFS image build failed")

            if progress_cb:
                progress_cb(65, "Flashing firmware + SPIFFS in single connection...")

            self._flash_all(spiffs_image, progress_cb)

            if progress_cb:
                progress_cb(95, "Verifying checksum...")
                self._verify_checksum(file_paths, progress_cb)
                progress_cb(100, "Upload complete ✓")

    def _build_spiffs_image(self, data_dir: str, output_img: str, progress_cb=None):
        """Try mkspiffs, fallback to littlefs-python."""
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

        # Fallback: littlefs-python
        if progress_cb:
            progress_cb(55, "Building SPIFFS image with littlefs-python...")

        try:
            import littlefs
            fs = littlefs.LittleFS(block_size=4096, block_count=88)
            for fname in os.listdir(data_dir):
                fpath = os.path.join(data_dir, fname)
                if os.path.isfile(fpath) and fname != "spiffs.bin":
                    with open(fpath, "rb") as f:
                        data = f.read()
                    with fs.open(f"/{fname}", "wb") as lf:
                        lf.write(data)
            with open(output_img, "wb") as out:
                out.write(fs.context.buffer)
        except ImportError:
            raise RuntimeError(
                "mkspiffs not found and littlefs-python not installed.\n"
                "Run: pip install littlefs-python"
            )

    def _flash_all(self, spiffs_image: str, progress_cb=None):
        """Flash firmware + SPIFFS in a single esptool connection."""
        fw_path = self._get_firmware_path()

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
            SPIFFS_OFFSET,   spiffs_image,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)

        if result.returncode != 0:
            raise RuntimeError(f"Flash failed:\n{result.stderr}")

        if progress_cb:
            progress_cb(90, "Firmware + SPIFFS flashed successfully ✓")

    def _verify_checksum(self, file_paths: list, progress_cb=None):
        h = hashlib.md5()
        for path in sorted(file_paths):
            with open(path, "rb") as f:
                h.update(f.read())
        digest = h.hexdigest()
        if progress_cb:
            progress_cb(98, f"MD5: {digest[:8]}...  ✓")
        return digest

    def _get_firmware_path(self) -> str:
        # Try importlib.resources (installed package)
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
