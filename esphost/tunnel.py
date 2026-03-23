import os
import sys
import stat
import platform
import subprocess
import threading
import re
import time
import requests


CLOUDFLARED_DIR  = os.path.join(os.path.expanduser("~"), ".esphost")
CLOUDFLARED_BIN  = os.path.join(CLOUDFLARED_DIR, "cloudflared" + (".exe" if sys.platform == "win32" else ""))

DOWNLOAD_URLS = {
    ("windows", "amd64"):  "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe",
    ("linux",   "amd64"):  "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
    ("linux",   "arm64"):  "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64",
    ("linux",   "armv6"):  "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm",
    ("darwin",  "amd64"):  "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-amd64.tgz",
    ("darwin",  "arm64"):  "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-arm64.tgz",
}


class TunnelManager:

    def __init__(self):
        self._process = None
        self._url     = None
        os.makedirs(CLOUDFLARED_DIR, exist_ok=True)

    def start(self, target_host: str, port: int = 80) -> str:
        """Download cloudflared if needed, start tunnel, return public URL."""
        self._ensure_binary()

        target = f"http://{target_host}:{port}"
        self._process = subprocess.Popen(
            [CLOUDFLARED_BIN, "tunnel", "--url", target],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        url = self._wait_for_url(timeout=30)
        if not url:
            raise RuntimeError("Cloudflare Tunnel did not return a URL within 30s")

        self._url = url

        # Keep tunnel alive in background thread
        threading.Thread(target=self._monitor, daemon=True).start()

        return url

    def stop(self):
        if self._process:
            self._process.terminate()
            self._process = None

    def _wait_for_url(self, timeout=30) -> str | None:
        deadline = time.time() + timeout
        url_pattern = re.compile(r"https://[a-z0-9\-]+\.trycloudflare\.com")

        while time.time() < deadline:
            line = self._process.stdout.readline()
            if not line:
                break
            m = url_pattern.search(line)
            if m:
                return m.group(0)

        return None

    def _monitor(self):
        """Read remaining stdout to keep buffer clear."""
        for line in self._process.stdout:
            pass  # just drain

    # ── Binary management ─────────────────────────────────────────────────────

    def _ensure_binary(self):
        if os.path.exists(CLOUDFLARED_BIN):
            return

        key = self._platform_key()
        url = DOWNLOAD_URLS.get(key)
        if not url:
            raise RuntimeError(f"No cloudflared binary for platform: {key}")

        self._download(url)

    def _platform_key(self):
        system = sys.platform
        machine = platform.machine().lower()

        if system == "win32":
            os_key = "windows"
        elif system == "darwin":
            os_key = "darwin"
        else:
            os_key = "linux"

        if machine in ("x86_64", "amd64"):
            arch = "amd64"
        elif machine in ("arm64", "aarch64"):
            arch = "arm64"
        elif machine.startswith("arm"):
            arch = "armv6"
        else:
            arch = "amd64"

        return (os_key, arch)

    def _download(self, url: str):
        print(f"Downloading cloudflared from {url} ...")

        r = requests.get(url, stream=True, timeout=60)
        r.raise_for_status()

        tmp_path = CLOUDFLARED_BIN + ".tmp"
        with open(tmp_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)

        # Handle .tgz on macOS
        if url.endswith(".tgz"):
            import tarfile
            with tarfile.open(tmp_path) as tar:
                for member in tar.getmembers():
                    if "cloudflared" in member.name and not member.name.endswith("/"):
                        member.name = os.path.basename(member.name)
                        tar.extract(member, CLOUDFLARED_DIR)
            os.remove(tmp_path)
        else:
            os.replace(tmp_path, CLOUDFLARED_BIN)

        # Make executable on Unix
        if sys.platform != "win32":
            st = os.stat(CLOUDFLARED_BIN)
            os.chmod(CLOUDFLARED_BIN, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        print("cloudflared ready.")
