import sys
import os
import re
import threading
import serial
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QProgressBar, QTextEdit,
    QFrame, QLineEdit
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent

from esphost.scanner import ESPScanner
from esphost.flasher import ESPFlasher
from esphost.tunnel import TunnelManager
from esphost.queue_proxy import QueueProxy


class DetectWorker(QThread):
    result = pyqtSignal(dict)
    error  = pyqtSignal(str)
    def run(self):
        try:
            self.result.emit(ESPScanner().detect_esp())
        except Exception as e:
            self.error.emit(str(e))


class ScanWorker(QThread):
    result = pyqtSignal(dict)
    error  = pyqtSignal(str)
    def __init__(self, files, esp_info):
        super().__init__()
        self.files    = files
        self.esp_info = esp_info
    def run(self):
        try:
            self.result.emit(ESPScanner().scan_files(self.files, self.esp_info))
        except Exception as e:
            self.error.emit(str(e))


class FlashWorker(QThread):
    progress = pyqtSignal(int, str)
    done     = pyqtSignal()
    error    = pyqtSignal(str)
    def __init__(self, port, files):
        super().__init__()
        self.port  = port
        self.files = files
    def run(self):
        try:
            f = ESPFlasher(self.port)
            f.flash_firmware(lambda p, m: self.progress.emit(p, m))
            f.upload_files(self.files, lambda p, m: self.progress.emit(p, m))
            self.done.emit()
        except Exception as e:
            self.error.emit(str(e))


class WiFiWorker(QThread):
    ip_found = pyqtSignal(str)
    log_msg  = pyqtSignal(str)
    error    = pyqtSignal(str)
    def __init__(self, port, ssid, password):
        super().__init__()
        self.port     = port
        self.ssid     = ssid
        self.password = password
    def run(self):
        import json, time
        try:
            self.log_msg.emit("Opening serial to ESP32...")
            s = serial.Serial(self.port, 115200, timeout=1)
            time.sleep(2)
            cmd = json.dumps({"cmd": "setwifi", "ssid": self.ssid, "pass": self.password})
            self.log_msg.emit("Sending Wi-Fi credentials...")
            s.write((cmd + "\n").encode())
            deadline = time.time() + 30
            ip_pattern = re.compile(r'READY ip=([\d.]+)')
            while time.time() < deadline:
                line = s.readline().decode("utf-8", errors="replace").strip()
                if line:
                    self.log_msg.emit(line)
                if "READY ip=" in line:
                    m = ip_pattern.search(line)
                    if m:
                        s.close()
                        self.ip_found.emit(m.group(1))
                        return
                if "failed" in line.lower():
                    s.close()
                    self.error.emit("Wi-Fi connection failed. Check SSID and password.")
                    return
            s.close()
            self.error.emit("Timed out. Check Wi-Fi credentials and try again.")
        except Exception as e:
            self.error.emit(str(e))


class TunnelWorker(QThread):
    url   = pyqtSignal(str)
    error = pyqtSignal(str)
    def __init__(self, esp_ip):
        super().__init__()
        self.esp_ip = esp_ip
    def run(self):
        try:
            self.url.emit(TunnelManager().start(self.esp_ip))
        except Exception as e:
            self.error.emit(str(e))


class DropZone(QFrame):
    files_dropped = pyqtSignal(list)
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumHeight(140)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon = QLabel("⬆")
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon.setStyleSheet("font-size:32px;color:#00ff9d;")
        self.text = QLabel("Drop site files here  ·  or click to browse")
        self.text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text.setStyleSheet("font-size:12px;color:#666;letter-spacing:1px;")
        self.files_lbl = QLabel("")
        self.files_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.files_lbl.setStyleSheet("font-size:11px;color:#00ff9d;")
        self.files_lbl.setWordWrap(True)
        layout.addWidget(self.icon)
        layout.addWidget(self.text)
        layout.addWidget(self.files_lbl)
        self._style(False)

    def _style(self, on):
        self.setStyleSheet(f"DropZone{{border:2px dashed {'#00ff9d' if on else '#2a2a2a'};border-radius:12px;background:{'#0d1f17' if on else '#111'};}}")

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction(); self._style(True)
    def dragLeaveEvent(self, e): self._style(False)
    def dropEvent(self, e):
        self._style(False)
        paths = [u.toLocalFile() for u in e.mimeData().urls()]
        self._set(paths); self.files_dropped.emit(paths)
    def mousePressEvent(self, e):
        paths, _ = QFileDialog.getOpenFileNames(self, "Select site files", "",
            "Web files (*.html *.css *.js *.png *.jpg *.ico *.json *.svg *.woff *.woff2 *.ttf)")
        if paths:
            self._set(paths); self.files_dropped.emit(paths)
    def _set(self, paths):
        self.files_lbl.setText("  ".join(os.path.basename(p) for p in paths))
        self.text.setText(f"{len(paths)} file(s) selected")


class LogWidget(QTextEdit):
    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setMaximumHeight(140)
        self.setStyleSheet("QTextEdit{background:#0a0a0a;border:1px solid #1e1e1e;border-radius:8px;color:#555;font-family:'Courier New',monospace;font-size:11px;padding:8px;}")
    def _a(self, msg, color):
        self.append(f'<span style="color:{color};">{msg}</span>')
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
    def ok(self, m):   self._a(f"✓  {m}", "#00ff9d")
    def err(self, m):  self._a(f"✗  {m}", "#ff4444")
    def info(self, m): self._a(f"→  {m}", "#888")


class WiFiCard(QFrame):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("QFrame{background:#111;border:1px solid #2a2a2a;border-radius:12px;}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        title = QLabel("📶  Connect ESP32 to Wi-Fi")
        title.setStyleSheet("font-size:13px;font-weight:bold;color:#e0e0e0;background:transparent;")

        hint = QLabel("Enter your Wi-Fi name and password below.\nESP32 will connect automatically and get a public URL.")
        hint.setStyleSheet("font-size:11px;color:#555;background:transparent;")
        hint.setWordWrap(True)

        self.ssid_input = self._field("Wi-Fi Name (e.g. MyHomeNetwork)", False)
        self.pass_input = self._field("Wi-Fi Password", True)

        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(self.ssid_input)
        layout.addWidget(self.pass_input)

    def _field(self, placeholder, secret):
        f = QLineEdit()
        f.setPlaceholderText(placeholder)
        if secret:
            f.setEchoMode(QLineEdit.EchoMode.Password)
        f.setStyleSheet("""
            QLineEdit{background:#0d0d0d;border:1px solid #2a2a2a;border-radius:8px;
                      padding:10px 14px;font-size:13px;color:#e0e0e0;font-family:'Courier New',monospace;}
            QLineEdit:focus{border-color:#00ff9d;}
        """)
        return f

    def get_credentials(self):
        return self.ssid_input.text().strip(), self.pass_input.text()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ESPHost")
        self.setMinimumSize(680, 820)
        self.setStyleSheet("QMainWindow{background:#0d0d0d;} QWidget{background:#0d0d0d;color:#e0e0e0;}")
        self.esp_info    = {}
        self.files       = []
        self.scan_result = {}
        self.esp_ip      = None
        self._state      = "scan"
        self._build_ui()

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(32, 32, 32, 32)
        lay.setSpacing(18)

        hdr = QLabel("ESPHOST")
        hdr.setStyleSheet("font-size:28px;font-weight:800;letter-spacing:8px;color:#00ff9d;font-family:'Courier New';")
        sub = QLabel("flash · host · serve")
        sub.setStyleSheet("font-size:11px;color:#444;letter-spacing:4px;")
        lay.addWidget(hdr)
        lay.addWidget(sub)
        lay.addSpacing(4)

        row = QHBoxLayout()
        self.esp_status = QLabel("No ESP detected")
        self.esp_status.setStyleSheet("font-size:12px;color:#555;font-family:'Courier New';")
        self.detect_btn = self._btn("Detect ESP", "#1a1a1a", "#00ff9d")
        self.detect_btn.clicked.connect(self._detect_esp)
        row.addWidget(self.esp_status); row.addStretch(); row.addWidget(self.detect_btn)
        lay.addLayout(row)

        self.esp_card = QFrame()
        self.esp_card.setStyleSheet("QFrame{background:#111;border:1px solid #1e1e1e;border-radius:10px;}")
        self.esp_card.setVisible(False)
        cl = QHBoxLayout(self.esp_card); cl.setSpacing(20)
        self.lbl_port   = self._stat("PORT",        "—")
        self.lbl_flash  = self._stat("FLASH",       "—")
        self.lbl_spiffs = self._stat("SPIFFS FREE", "—")
        self.lbl_ram    = self._stat("FREE RAM",    "—")
        self.lbl_freq   = self._stat("CPU",         "—")
        for w in [self.lbl_port, self.lbl_flash, self.lbl_spiffs, self.lbl_ram, self.lbl_freq]:
            cl.addWidget(w)
        lay.addWidget(self.esp_card)

        self.drop_zone = DropZone()
        self.drop_zone.files_dropped.connect(self._on_files)
        lay.addWidget(self.drop_zone)

        self.scan_label = QLabel("")
        self.scan_label.setStyleSheet("font-size:12px;color:#555;font-family:'Courier New';")
        self.scan_label.setWordWrap(True)
        lay.addWidget(self.scan_label)

        self.wifi_card = WiFiCard()
        self.wifi_card.setVisible(False)
        lay.addWidget(self.wifi_card)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet("QProgressBar{background:#1a1a1a;border:none;border-radius:4px;height:6px;} QProgressBar::chunk{background:#00ff9d;border-radius:4px;}")
        lay.addWidget(self.progress)

        self.log = LogWidget()
        lay.addWidget(self.log)

        self.url_frame = QFrame()
        self.url_frame.setVisible(False)
        self.url_frame.setStyleSheet("QFrame{background:#0d1f17;border:1px solid #00ff9d;border-radius:10px;}")
        ul = QVBoxLayout(self.url_frame); ul.setContentsMargins(16, 12, 16, 12)
        url_lbl = QLabel("YOUR SITE IS LIVE AT")
        url_lbl.setStyleSheet("font-size:9px;color:#00ff9d;letter-spacing:3px;background:transparent;")
        self.url_value = QLabel("—")
        self.url_value.setStyleSheet("font-size:15px;color:#fff;font-family:'Courier New';font-weight:bold;background:transparent;")
        self.url_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        ul.addWidget(url_lbl); ul.addWidget(self.url_value)
        lay.addWidget(self.url_frame)

        self.action_btn = self._btn("SCAN FILES", "#00ff9d", "#0d0d0d")
        self.action_btn.setMinimumHeight(48)
        self.action_btn.setEnabled(False)
        self.action_btn.clicked.connect(self._action)
        lay.addWidget(self.action_btn)

    def _btn(self, text, bg, fg):
        b = QPushButton(text)
        b.setStyleSheet(f"""
            QPushButton{{background:{bg};color:{fg};border:1px solid #2a2a2a;border-radius:8px;
                         padding:8px 20px;font-size:12px;font-weight:700;letter-spacing:2px;font-family:'Courier New',monospace;}}
            QPushButton:hover{{background:#1e1e1e;color:#00ff9d;border-color:#00ff9d;}}
            QPushButton:disabled{{opacity:0.3;}}
        """)
        return b

    def _stat(self, title, value):
        f = QFrame()
        l = QVBoxLayout(f); l.setSpacing(2); l.setContentsMargins(8, 8, 8, 8)
        t = QLabel(title); t.setStyleSheet("font-size:9px;color:#444;letter-spacing:2px;background:transparent;")
        v = QLabel(value); v.setStyleSheet("font-size:13px;color:#00ff9d;font-family:'Courier New';font-weight:bold;background:transparent;")
        l.addWidget(t); l.addWidget(v)
        f._val = v
        return f

    def _detect_esp(self):
        self.detect_btn.setEnabled(False)
        self.esp_status.setText("Scanning USB ports...")
        self.log.info("Scanning for ESP32...")
        self._dw = DetectWorker()
        self._dw.result.connect(self._on_esp_detected)
        self._dw.error.connect(lambda e: (self.log.err(e), self.detect_btn.setEnabled(True)))
        self._dw.start()

    def _on_esp_detected(self, info):
        self.esp_info = info
        self.detect_btn.setEnabled(True)
        if not info.get("found"):
            self.esp_status.setText("No ESP32 found")
            self.log.err("No ESP32 detected"); return
        self.esp_status.setText("ESP32 detected  ✓")
        self.esp_status.setStyleSheet("font-size:12px;color:#00ff9d;font-family:'Courier New';")
        self.esp_card.setVisible(True)
        self.lbl_port._val.setText(info.get("port","—"))
        self.lbl_flash._val.setText(info.get("flash_size","—"))
        self.lbl_spiffs._val.setText(info.get("spiffs_free","—"))
        self.lbl_ram._val.setText(info.get("free_ram","—"))
        self.lbl_freq._val.setText(info.get("cpu_freq","—"))
        self.log.ok(f"ESP32 on {info.get('port')}  |  Flash: {info.get('flash_size')}  |  SPIFFS: {info.get('spiffs_free')}")
        self._check_ready()

    def _on_files(self, paths):
        self.files = paths
        self.log.info(f"{len(paths)} file(s) loaded")
        self._check_ready()

    def _check_ready(self):
        if self.esp_info.get("found") and self.files:
            self.action_btn.setEnabled(True)

    def _action(self):
        if   self._state == "scan":   self._run_scan()
        elif self._state == "flash":  self._run_flash()
        elif self._state == "wifi":   self._run_wifi()
        elif self._state == "tunnel": self._run_tunnel()

    def _run_scan(self):
        self.action_btn.setEnabled(False)
        self.log.info("Scanning files against ESP hardware...")
        self._sw = ScanWorker(self.files, self.esp_info)
        self._sw.result.connect(self._on_scan_done)
        self._sw.error.connect(lambda e: (self.log.err(e), self.action_btn.setEnabled(True)))
        self._sw.start()

    def _on_scan_done(self, result):
        self.scan_result = result
        if result.get("hostable"):
            mu = result.get("max_users", 1)
            self.scan_label.setText(f"✓  HOSTABLE  |  {result.get('total_size_kb')}KB / {result.get('spiffs_free_kb')}KB  |  Max users: {mu}")
            self.scan_label.setStyleSheet("font-size:12px;color:#00ff9d;font-family:'Courier New';")
            self.log.ok(f"HOSTABLE  |  Max concurrent users: {mu}")
            self.action_btn.setText("FLASH ESP32"); self._state = "flash"
        else:
            reasons = "  |  ".join(result.get("reasons", ["Unknown"]))
            self.scan_label.setText(f"✗  NOT HOSTABLE  |  {reasons}")
            self.scan_label.setStyleSheet("font-size:12px;color:#ff4444;font-family:'Courier New';")
            self.log.err(f"NOT HOSTABLE — {reasons}")
        self.action_btn.setEnabled(True)

    def _run_flash(self):
        self.action_btn.setEnabled(False)
        self.progress.setVisible(True); self.progress.setValue(0)
        self.log.info("Flashing firmware + files to ESP32...")
        self._fw = FlashWorker(self.esp_info.get("port"), self.files)
        self._fw.progress.connect(lambda p, m: (self.progress.setValue(p), self.log.info(m)))
        self._fw.done.connect(self._on_flash_done)
        self._fw.error.connect(lambda e: (self.log.err(f"Flash failed: {e}"), self.action_btn.setEnabled(True), self.progress.setVisible(False)))
        self._fw.start()

    def _on_flash_done(self):
        self.progress.setValue(100)
        self.log.ok("Flash complete. Files saved permanently to ESP32.")
        self.log.info("Enter your Wi-Fi details so ESP32 can go online.")
        self.wifi_card.setVisible(True)
        self.action_btn.setText("CONNECT TO WI-FI")
        self.action_btn.setEnabled(True)
        self._state = "wifi"

    def _run_wifi(self):
        ssid, password = self.wifi_card.get_credentials()
        if not ssid:
            self.log.err("Wi-Fi name cannot be empty."); return
        if not password:
            self.log.err("Wi-Fi password cannot be empty."); return
        self.action_btn.setEnabled(False)
        self.log.info(f"Sending Wi-Fi credentials to ESP32...")
        self.log.info("ESP32 will restart and connect. This takes 15-30 seconds.")
        self._ww = WiFiWorker(self.esp_info.get("port"), ssid, password)
        self._ww.log_msg.connect(self.log.info)
        self._ww.ip_found.connect(self._on_ip_found)
        self._ww.error.connect(lambda e: (self.log.err(e), self.action_btn.setEnabled(True)))
        self._ww.start()

    def _on_ip_found(self, ip):
        self.esp_ip = ip
        self.log.ok(f"ESP32 online  |  IP: {ip}")
        self.wifi_card.setVisible(False)
        self.action_btn.setText("GO LIVE  →  START TUNNEL")
        self.action_btn.setEnabled(True)
        self._state = "tunnel"

    def _run_tunnel(self):
        self.action_btn.setEnabled(False)
        self.log.info("Starting Cloudflare Tunnel...")
        self._tw = TunnelWorker(self.esp_ip)
        self._tw.url.connect(self._on_tunnel_up)
        self._tw.error.connect(lambda e: (self.log.err(f"Tunnel: {e}"), self.action_btn.setEnabled(True)))
        self._tw.start()

    def _on_tunnel_up(self, url):
        self.log.ok(f"Tunnel live → {url}")
        max_slots = self.scan_result.get("max_users", 1)
        self.log.ok(f"Proxy active  |  max {max_slots} users  |  queue enabled")
        proxy = QueueProxy(target_url=f"http://{self.esp_ip}:80", max_slots=max_slots)
        threading.Thread(target=proxy.run, daemon=True).start()
        self.url_value.setText(url)
        self.url_frame.setVisible(True)
        self.action_btn.setText("LIVE ✓")
        self.action_btn.setEnabled(False)


def launch():
    app = QApplication(sys.argv)
    app.setApplicationName("ESPHost")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
