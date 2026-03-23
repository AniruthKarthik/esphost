import sys
import os
import threading
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QProgressBar, QTextEdit,
    QFrame, QStackedWidget, QGraphicsDropShadowEffect
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QPropertyAnimation, QEasingCurve, QTimer, QMimeData
from PyQt6.QtGui import QFont, QColor, QPalette, QDragEnterEvent, QDropEvent, QFontDatabase, QPainter, QLinearGradient

from esphost.scanner import ESPScanner
from esphost.flasher import ESPFlasher
from esphost.tunnel import TunnelManager
from esphost.queue_proxy import QueueProxy


# ── Worker threads ────────────────────────────────────────────────────────────

class DetectWorker(QThread):
    result = pyqtSignal(dict)
    error  = pyqtSignal(str)

    def run(self):
        try:
            scanner = ESPScanner()
            info = scanner.detect_esp()
            self.result.emit(info)
        except Exception as e:
            self.error.emit(str(e))


class ScanWorker(QThread):
    result = pyqtSignal(dict)
    error  = pyqtSignal(str)

    def __init__(self, files, esp_info):
        super().__init__()
        self.files = files
        self.esp_info = esp_info

    def run(self):
        try:
            scanner = ESPScanner()
            verdict = scanner.scan_files(self.files, self.esp_info)
            self.result.emit(verdict)
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
            flasher = ESPFlasher(self.port)
            flasher.flash_firmware(lambda p, m: self.progress.emit(p, m))
            flasher.upload_files(self.files, lambda p, m: self.progress.emit(p, m))
            self.done.emit()
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
            tm = TunnelManager()
            public_url = tm.start(self.esp_ip)
            self.url.emit(public_url)
        except Exception as e:
            self.error.emit(str(e))


# ── Drop zone widget ──────────────────────────────────────────────────────────

class DropZone(QFrame):
    files_dropped = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumHeight(160)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hovered = False
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.icon_label = QLabel("⬆")
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("font-size: 36px; color: #00ff9d;")

        self.text_label = QLabel("Drop site files here\nor click to browse")
        self.text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text_label.setStyleSheet("font-size: 13px; color: #888; letter-spacing: 1px;")

        self.file_list_label = QLabel("")
        self.file_list_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.file_list_label.setStyleSheet("font-size: 11px; color: #00ff9d;")
        self.file_list_label.setWordWrap(True)

        layout.addWidget(self.icon_label)
        layout.addWidget(self.text_label)
        layout.addWidget(self.file_list_label)

        self._apply_style(False)

    def _apply_style(self, hovered):
        border_color = "#00ff9d" if hovered else "#2a2a2a"
        bg = "#0d1f17" if hovered else "#111111"
        self.setStyleSheet(f"""
            DropZone {{
                border: 2px dashed {border_color};
                border-radius: 12px;
                background: {bg};
            }}
        """)

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._apply_style(True)

    def dragLeaveEvent(self, e):
        self._apply_style(False)

    def dropEvent(self, e: QDropEvent):
        self._apply_style(False)
        paths = [u.toLocalFile() for u in e.mimeData().urls()]
        self._set_files(paths)
        self.files_dropped.emit(paths)

    def mousePressEvent(self, e):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select site files", "",
            "Web files (*.html *.css *.js *.png *.jpg *.ico *.json *.svg *.woff *.woff2 *.ttf)"
        )
        if paths:
            self._set_files(paths)
            self.files_dropped.emit(paths)

    def _set_files(self, paths):
        names = [os.path.basename(p) for p in paths]
        self.file_list_label.setText("  ".join(names))
        self.text_label.setText(f"{len(paths)} file(s) selected")


# ── Log widget ────────────────────────────────────────────────────────────────

class LogWidget(QTextEdit):
    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setMaximumHeight(150)
        self.setStyleSheet("""
            QTextEdit {
                background: #0a0a0a;
                border: 1px solid #1e1e1e;
                border-radius: 8px;
                color: #555;
                font-family: 'Courier New', monospace;
                font-size: 11px;
                padding: 8px;
            }
        """)

    def log(self, msg, color="#555"):
        self.append(f'<span style="color:{color};">{msg}</span>')
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def ok(self, msg):   self.log(f"✓  {msg}", "#00ff9d")
    def err(self, msg):  self.log(f"✗  {msg}", "#ff4444")
    def info(self, msg): self.log(f"→  {msg}", "#888")


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ESPHost")
        self.setMinimumSize(680, 780)
        self.setStyleSheet("""
            QMainWindow { background: #0d0d0d; }
            QWidget     { background: #0d0d0d; color: #e0e0e0; }
        """)

        self.esp_info   = {}
        self.files      = []
        self.scan_result = {}

        self._build_ui()

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(20)

        # ── Header
        header = QLabel("ESPHOST")
        header.setStyleSheet("""
            font-size: 28px;
            font-weight: 800;
            letter-spacing: 8px;
            color: #00ff9d;
            font-family: 'Courier New', monospace;
        """)
        sub = QLabel("flash · host · serve")
        sub.setStyleSheet("font-size: 11px; color: #444; letter-spacing: 4px;")

        layout.addWidget(header)
        layout.addWidget(sub)
        layout.addSpacing(8)

        # ── ESP detect row
        esp_row = QHBoxLayout()
        self.esp_status = QLabel("No ESP detected")
        self.esp_status.setStyleSheet("font-size: 12px; color: #555; font-family: 'Courier New';")

        self.detect_btn = self._make_btn("Detect ESP", "#1a1a1a", "#00ff9d")
        self.detect_btn.clicked.connect(self._detect_esp)

        esp_row.addWidget(self.esp_status)
        esp_row.addStretch()
        esp_row.addWidget(self.detect_btn)
        layout.addLayout(esp_row)

        # ── ESP info card
        self.esp_card = QFrame()
        self.esp_card.setStyleSheet("""
            QFrame {
                background: #111;
                border: 1px solid #1e1e1e;
                border-radius: 10px;
                padding: 4px;
            }
        """)
        self.esp_card.setVisible(False)
        card_layout = QHBoxLayout(self.esp_card)
        card_layout.setSpacing(24)

        self.lbl_port  = self._stat_label("PORT",  "—")
        self.lbl_flash = self._stat_label("FLASH", "—")
        self.lbl_spiffs = self._stat_label("SPIFFS FREE", "—")
        self.lbl_ram   = self._stat_label("FREE RAM", "—")
        self.lbl_freq  = self._stat_label("CPU", "—")

        for w in [self.lbl_port, self.lbl_flash, self.lbl_spiffs, self.lbl_ram, self.lbl_freq]:
            card_layout.addWidget(w)

        layout.addWidget(self.esp_card)

        # ── Drop zone
        self.drop_zone = DropZone()
        self.drop_zone.files_dropped.connect(self._on_files)
        layout.addWidget(self.drop_zone)

        # ── Scan result
        self.scan_label = QLabel("")
        self.scan_label.setStyleSheet("font-size: 12px; color: #555; font-family: 'Courier New';")
        self.scan_label.setWordWrap(True)
        layout.addWidget(self.scan_label)

        # ── Progress bar
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setStyleSheet("""
            QProgressBar {
                background: #1a1a1a;
                border: none;
                border-radius: 4px;
                height: 6px;
                text-align: center;
            }
            QProgressBar::chunk {
                background: #00ff9d;
                border-radius: 4px;
            }
        """)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)

        # ── Log
        self.log = LogWidget()
        layout.addWidget(self.log)

        # ── URL display
        self.url_frame = QFrame()
        self.url_frame.setVisible(False)
        self.url_frame.setStyleSheet("""
            QFrame {
                background: #0d1f17;
                border: 1px solid #00ff9d;
                border-radius: 10px;
                padding: 4px;
            }
        """)
        url_layout = QVBoxLayout(self.url_frame)
        url_lbl = QLabel("LIVE URL")
        url_lbl.setStyleSheet("font-size: 10px; color: #00ff9d; letter-spacing: 3px;")
        self.url_value = QLabel("—")
        self.url_value.setStyleSheet("""
            font-size: 15px;
            color: #ffffff;
            font-family: 'Courier New';
            font-weight: bold;
        """)
        self.url_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        url_layout.addWidget(url_lbl)
        url_layout.addWidget(self.url_value)
        layout.addWidget(self.url_frame)

        # ── Action button
        self.action_btn = self._make_btn("SCAN FILES", "#00ff9d", "#0d0d0d")
        self.action_btn.setMinimumHeight(48)
        self.action_btn.setEnabled(False)
        self.action_btn.clicked.connect(self._action)
        layout.addWidget(self.action_btn)

        self._state = "scan"

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _make_btn(self, text, bg, fg):
        btn = QPushButton(text)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {bg};
                color: {fg};
                border: 1px solid #2a2a2a;
                border-radius: 8px;
                padding: 8px 20px;
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 2px;
                font-family: 'Courier New', monospace;
            }}
            QPushButton:hover  {{ background: #1e1e1e; color: #00ff9d; border-color: #00ff9d; }}
            QPushButton:disabled {{ opacity: 0.3; }}
        """)
        return btn

    def _stat_label(self, title, value):
        frame = QFrame()
        layout = QVBoxLayout(frame)
        layout.setSpacing(2)
        layout.setContentsMargins(8, 8, 8, 8)
        t = QLabel(title)
        t.setStyleSheet("font-size: 9px; color: #444; letter-spacing: 2px;")
        v = QLabel(value)
        v.setStyleSheet("font-size: 13px; color: #00ff9d; font-family: 'Courier New'; font-weight: bold;")
        v.setObjectName(f"val_{title.replace(' ', '_')}")
        layout.addWidget(t)
        layout.addWidget(v)
        frame._val = v
        return frame

    def _update_stat(self, frame, value):
        frame._val.setText(value)

    # ── ESP detect ────────────────────────────────────────────────────────────

    def _detect_esp(self):
        self.detect_btn.setEnabled(False)
        self.esp_status.setText("Scanning USB ports...")
        self.log.info("Scanning for ESP32...")

        self._detect_worker = DetectWorker()
        self._detect_worker.result.connect(self._on_esp_detected)
        self._detect_worker.error.connect(self._on_esp_error)
        self._detect_worker.start()

    def _on_esp_detected(self, info):
        self.esp_info = info
        self.detect_btn.setEnabled(True)

        if not info.get("found"):
            self.esp_status.setText("No ESP32 found")
            self.log.err("No ESP32 detected on any port")
            return

        self.esp_status.setText(f"ESP32 detected  ✓")
        self.esp_status.setStyleSheet("font-size: 12px; color: #00ff9d; font-family: 'Courier New';")
        self.esp_card.setVisible(True)

        self._update_stat(self.lbl_port,   info.get("port", "—"))
        self._update_stat(self.lbl_flash,  info.get("flash_size", "—"))
        self._update_stat(self.lbl_spiffs, info.get("spiffs_free", "—"))
        self._update_stat(self.lbl_ram,    info.get("free_ram", "—"))
        self._update_stat(self.lbl_freq,   info.get("cpu_freq", "—"))

        self.log.ok(f"ESP32 on {info.get('port')}  |  Flash: {info.get('flash_size')}  |  SPIFFS free: {info.get('spiffs_free')}")
        self._check_ready()

    def _on_esp_error(self, err):
        self.detect_btn.setEnabled(True)
        self.esp_status.setText("Detection failed")
        self.log.err(err)

    # ── Files ─────────────────────────────────────────────────────────────────

    def _on_files(self, paths):
        self.files = paths
        self.log.info(f"{len(paths)} file(s) loaded")
        self._check_ready()

    def _check_ready(self):
        if self.esp_info.get("found") and self.files:
            self.action_btn.setEnabled(True)

    # ── State machine ─────────────────────────────────────────────────────────

    def _action(self):
        if self._state == "scan":
            self._run_scan()
        elif self._state == "flash":
            self._run_flash()
        elif self._state == "tunnel":
            self._run_tunnel()

    def _run_scan(self):
        self.action_btn.setEnabled(False)
        self.log.info("Scanning files against ESP hardware...")

        self._scan_worker = ScanWorker(self.files, self.esp_info)
        self._scan_worker.result.connect(self._on_scan_done)
        self._scan_worker.error.connect(lambda e: self.log.err(e))
        self._scan_worker.start()

    def _on_scan_done(self, result):
        self.scan_result = result
        hostable = result.get("hostable", False)

        if hostable:
            max_users = result.get("max_users", 1)
            self.scan_label.setText(
                f"✓  HOSTABLE   |  {result.get('total_size_kb')}KB / {result.get('spiffs_free_kb')}KB  "
                f"|  RAM/user: {result.get('ram_per_user_kb')}KB  "
                f"|  Max concurrent users: {max_users}  |  {result.get('notes', '')}"
            )
            self.scan_label.setStyleSheet("font-size: 12px; color: #00ff9d; font-family: 'Courier New';")
            self.log.ok(f"Verdict: HOSTABLE  |  Max concurrent users from hardware: {max_users}")
            self.action_btn.setText("FLASH ESP32")
            self._state = "flash"
        else:
            reasons = "  |  ".join(result.get("reasons", ["Unknown"]))
            self.scan_label.setText(f"✗  NOT HOSTABLE   |  {reasons}")
            self.scan_label.setStyleSheet("font-size: 12px; color: #ff4444; font-family: 'Courier New';")
            self.log.err(f"Verdict: NOT HOSTABLE  —  {reasons}")

        self.action_btn.setEnabled(True)

    def _run_flash(self):
        self.action_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.log.info("Flashing firmware to ESP32...")

        port = self.esp_info.get("port")
        self._flash_worker = FlashWorker(port, self.files)
        self._flash_worker.progress.connect(self._on_flash_progress)
        self._flash_worker.done.connect(self._on_flash_done)
        self._flash_worker.error.connect(self._on_flash_error)
        self._flash_worker.start()

    def _on_flash_progress(self, pct, msg):
        self.progress.setValue(pct)
        self.log.info(msg)

    def _on_flash_done(self):
        self.progress.setValue(100)
        self.log.ok("Flash complete. Files persisted to SPIFFS.")
        self.log.ok("ESP32 will serve site on every boot.")
        self.action_btn.setText("START TUNNEL")
        self.action_btn.setEnabled(True)
        self._state = "tunnel"

    def _on_flash_error(self, err):
        self.log.err(f"Flash failed: {err}")
        self.action_btn.setEnabled(True)
        self.progress.setVisible(False)

    def _run_tunnel(self):
        self.action_btn.setEnabled(False)
        self.log.info("Starting Cloudflare Tunnel...")

        esp_ip = self.esp_info.get("ip", "esp32.local")
        self._tunnel_worker = TunnelWorker(esp_ip)
        self._tunnel_worker.url.connect(self._on_tunnel_up)
        self._tunnel_worker.error.connect(lambda e: self.log.err(f"Tunnel error: {e}"))
        self._tunnel_worker.start()

    def _on_tunnel_up(self, url):
        self.log.ok(f"Tunnel live → {url}")
        self.log.ok("Proxy active  |  max 3 concurrent users  |  queue enabled")

        proxy = QueueProxy(
            target_url=f"http://{self.esp_info.get('ip', 'esp32.local')}:80",
            max_slots=self.scan_result.get("max_users", 1)
        )
        threading.Thread(target=proxy.run, daemon=True).start()

        self.url_value.setText(url)
        self.url_frame.setVisible(True)
        self.action_btn.setText("DEPLOYED ✓")
        self.action_btn.setStyleSheet(self.action_btn.styleSheet().replace("#00ff9d", "#1a1a1a"))


# ── Launch ────────────────────────────────────────────────────────────────────

def launch():
    app = QApplication(sys.argv)
    app.setApplicationName("ESPHost")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
