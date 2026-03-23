import os
import sys
import subprocess
import shutil
from setuptools import setup
from setuptools.command.install import install
from setuptools.command.develop import develop


FIRMWARE_SRC = os.path.join(os.path.dirname(__file__), "src")
FIRMWARE_DST = os.path.join(os.path.dirname(__file__), "esphost", "firmware", "esphost.bin")
PIO_BUILD    = os.path.join(os.path.dirname(__file__), ".pio", "build", "esp32dev", "firmware.bin")


def ensure_platformio():
    """Install platformio if not present."""
    if shutil.which("pio") is None:
        print("[esphost] Installing platformio...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "platformio"])


def build_firmware():
    """Run pio build and copy binary into the package."""
    project_dir = os.path.dirname(os.path.abspath(__file__))

    # Already built
    if os.path.exists(FIRMWARE_DST):
        print("[esphost] Firmware already built, skipping.")
        return

    ensure_platformio()

    print("[esphost] Building ESP32 firmware via PlatformIO...")
    result = subprocess.run(
        ["pio", "run", "--environment", "esp32dev"],
        cwd=project_dir,
    )

    if result.returncode != 0:
        print("[esphost] WARNING: Firmware build failed.")
        print("[esphost] You can build it manually later:")
        print("          cd <esphost project dir> && pio run")
        print("          cp .pio/build/esp32dev/firmware.bin esphost/firmware/esphost.bin")
        return

    os.makedirs(os.path.dirname(FIRMWARE_DST), exist_ok=True)
    shutil.copy2(PIO_BUILD, FIRMWARE_DST)
    print(f"[esphost] Firmware built and saved to {FIRMWARE_DST}")


class PostInstall(install):
    def run(self):
        install.run(self)
        build_firmware()


class PostDevelop(develop):
    def run(self):
        develop.run(self)
        build_firmware()


setup(
    cmdclass={
        "install": PostInstall,
        "develop": PostDevelop,
    }
)
