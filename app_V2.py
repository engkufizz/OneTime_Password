import os
import sys
import json
import base64
import ctypes
from ctypes import Structure, c_void_p, c_uint, c_wchar_p, POINTER, byref
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QPainter, QPixmap, QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSystemTrayIcon, QMenu, QMessageBox,
    QDialog, QLineEdit, QCheckBox, QSpinBox
)

APP_NAME = "One-Time Password (Tray)"
SERVICE_DIR = "NESearchTool_PasswordOnly"  # folder under LocalAppData
CRED_FILENAME = "cred.json"                # DPAPI-protected storage
SETTINGS_FILENAME = "settings.json"        # non-secret settings
USERNAME_LABEL = "default"                 # logical credential label

IS_WINDOWS = sys.platform.startswith("win")

# ---------------- Paths ----------------
def user_data_dir() -> str:
    root = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(root, SERVICE_DIR)

def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

DATA_DIR = user_data_dir()
CRED_PATH = os.path.join(DATA_DIR, CRED_FILENAME)
SETTINGS_PATH = os.path.join(DATA_DIR, SETTINGS_FILENAME)

# ---------------- Windows DPAPI helpers ----------------
# Secure, user-bound encryption without external deps (Windows only).
if IS_WINDOWS:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    class DATA_BLOB(Structure):
        _fields_ = [("cbData", c_uint),
                    ("pbData", c_void_p)]

    CRYPTPROTECT_UI_FORBIDDEN = 0x01

    def _bytes_to_blob(data: bytes):
        buf = ctypes.create_string_buffer(data, len(data))
        blob = DATA_BLOB(len(data), ctypes.addressof(buf))
        # Keep a reference to prevent GC
        return blob, buf

    def _blob_to_bytes(blob: DATA_BLOB) -> bytes:
        if not blob.cbData or not blob.pbData:
            return b""
        return ctypes.string_at(blob.pbData, blob.cbData)

    def dpapi_encrypt(plaintext: str) -> str:
        raw = plaintext.encode("utf-8")
        in_blob, in_buf = _bytes_to_blob(raw)
        out_blob = DATA_BLOB()
        # BOOL CryptProtectData(DATA_BLOB*, LPCWSTR, DATA_BLOB*, PVOID, PVOID, DWORD, DATA_BLOB*);
        ok = crypt32.CryptProtectData(byref(in_blob), c_wchar_p(None),
                                      None, None, None,
                                      CRYPTPROTECT_UI_FORBIDDEN,
                                      byref(out_blob))
        if not ok:
            raise OSError("CryptProtectData failed")
        try:
            enc = _blob_to_bytes(out_blob)
        finally:
            if out_blob.pbData:
                kernel32.LocalFree(out_blob.pbData)
        return base64.b64encode(enc).decode("ascii")

    def dpapi_decrypt(b64: str) -> Optional[str]:
        try:
            enc = base64.b64decode(b64)
        except Exception:
            return None
        in_blob, in_buf = _bytes_to_blob(enc)
        out_blob = DATA_BLOB()
        # BOOL CryptUnprotectData(DATA_BLOB*, LPWSTR*, DATA_BLOB*, PVOID, PVOID, DWORD, DATA_BLOB*);
        ok = crypt32.CryptUnprotectData(byref(in_blob), None,
                                        None, None, None, 0,
                                        byref(out_blob))
        if not ok:
            return None
        try:
            dec = _blob_to_bytes(out_blob)
        finally:
            if out_blob.pbData:
                kernel32.LocalFree(out_blob.pbData)
        try:
            return dec.decode("utf-8")
        except Exception:
            return None
else:
    # Non-Windows fallback (not secure; for completeness only)
    def dpapi_encrypt(plaintext: str) -> str:
        return base64.b64encode(plaintext.encode("utf-8")).decode("ascii")
    def dpapi_decrypt(b64: str) -> Optional[str]:
        try:
            return base64.b64decode(b64).decode("utf-8")
        except Exception:
            return None

# Optional Windows API to aggressively clear clipboard buffer
def win_clear_clipboard() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        user32 = ctypes.windll.user32
        if user32.OpenClipboard(0):
            user32.EmptyClipboard()
            user32.CloseClipboard()
            return True
    except Exception:
        pass
    return False

# ---------------- Settings (non-secret) ----------------
class Settings:
    def __init__(self):
        self.auto_clear = True
        self.auto_clear_secs = 20

    def load(self):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.auto_clear = bool(data.get("auto_clear", True))
            self.auto_clear_secs = int(data.get("auto_clear_secs", 20))
        except Exception:
            pass

    def save(self):
        try:
            ensure_dir(DATA_DIR)
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump({
                    "auto_clear": self.auto_clear,
                    "auto_clear_secs": self.auto_clear_secs
                }, f, indent=2)
        except Exception:
            pass

# ---------------- Credential storage (DPAPI on Windows) ----------------
class PasswordStore:
    def __init__(self, label: str):
        self.label = label
        self._in_memory: Optional[str] = None

    def get(self) -> Optional[str]:
        if self._in_memory:
            return self._in_memory
        # Try loading from DPAPI-protected file
        try:
            with open(CRED_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            enc = data.get("dpapi") or ""
            if not enc:
                return None
            pw = dpapi_decrypt(enc)
            if pw:
                self._in_memory = pw
                return pw
        except Exception:
            pass
        return None

    def set(self, password: str, remember_device: bool):
        self._in_memory = password
        if remember_device:
            try:
                ensure_dir(DATA_DIR)
                enc = dpapi_encrypt(password)
                with open(CRED_PATH, "w", encoding="utf-8") as f:
                    json.dump({"label": self.label, "dpapi": enc}, f)
            except Exception:
                # If writing fails, we still keep it in memory for this session
                pass

    def clear_device_store(self):
        try:
            if os.path.exists(CRED_PATH):
                os.remove(CRED_PATH)
        except Exception:
            pass

    def clear_memory(self):
        self._in_memory = None

# ---------------- Emoji icon builder ----------------
def emoji_icon(emoji: str, size: int = 128,
               bg=QColor(32, 48, 79), fg=QColor(220, 230, 255)) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setBrush(bg)
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(0, 0, size, size)
    painter.setPen(fg)
    painter.drawText(pm.rect(), Qt.AlignCenter, emoji)
    painter.end()
    return QIcon(pm)

# ---------------- Password dialog ----------------
class PasswordDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Enter password")
        self.setModal(True)
        self.setMinimumWidth(360)

        self.lbl = QLabel("Enter password to store:")
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.Password)

        self.chk_show = QCheckBox("Show password")
        self.chk_show.toggled.connect(self.on_toggle_show)

        self.chk_remember = QCheckBox(
            "Remember on this device (Windows-protected store)" if IS_WINDOWS
            else "Remember for this session only (device store unavailable)"
        )
        if not IS_WINDOWS:
            self.chk_remember.setEnabled(False)

        self.btn_ok = QPushButton("OK")
        self.btn_cancel = QPushButton("Cancel")
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.btn_cancel)
        buttons.addWidget(self.btn_ok)

        layout = QVBoxLayout(self)
        layout.addWidget(self.lbl)
        layout.addWidget(self.edit)
        layout.addWidget(self.chk_show)
        layout.addSpacing(6)
        layout.addWidget(self.chk_remember)
        layout.addSpacing(8)
        layout.addLayout(buttons)

    def on_toggle_show(self, checked: bool):
        self.edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)

    def get_values(self):
        return self.edit.text(), self.chk_remember.isChecked()

# ---------------- Main window ----------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(420, 240)

        self.settings = Settings()
        self.settings.load()

        self.store = PasswordStore(USERNAME_LABEL)

        # Clipboard clear timer and marker
        self._last_copied_value: Optional[str] = None
        self._clear_timer = QTimer(self)
        self._clear_timer.setSingleShot(True)
        self._clear_timer.timeout.connect(self._maybe_clear_clipboard)

        # UI
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("color:#D6E2FF; font-weight:600;")

        self.btn_copy = QPushButton("Copy password")
        self.btn_copy.clicked.connect(self.copy_password)

        self.btn_set = QPushButton("Set / Change password")
        self.btn_set.clicked.connect(self.change_password)

        self.btn_clear_device = QPushButton("Clear saved password on device")
        self.btn_clear_device.clicked.connect(self.clear_saved_password)

        self.chk_auto = QCheckBox("Auto-clear clipboard after")
        self.chk_auto.setChecked(self.settings.auto_clear)

        self.spin_secs = QSpinBox()
        self.spin_secs.setRange(3, 300)
        self.spin_secs.setValue(self.settings.auto_clear_secs)
        self.spin_secs.setSuffix(" s")

        self.btn_save_settings = QPushButton("Save settings")
        self.btn_save_settings.clicked.connect(self.save_settings)

        # Layouts
        row_buttons = QHBoxLayout()
        row_buttons.addWidget(self.btn_copy)
        row_buttons.addWidget(self.btn_set)

        row_clear = QHBoxLayout()
        row_clear.addWidget(self.btn_clear_device)
        row_clear.addStretch(1)

        row_opts = QHBoxLayout()
        row_opts.addWidget(self.chk_auto)
        row_opts.addWidget(self.spin_secs)
        row_opts.addStretch(1)
        row_opts.addWidget(self.btn_save_settings)

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)
        lay.addWidget(self.status_lbl)
        lay.addSpacing(6)
        lay.addLayout(row_buttons)
        lay.addLayout(row_clear)
        lay.addSpacing(10)
        lay.addWidget(QLabel("Note: Windows Clipboard History (Win+V) is separate and not cleared by apps."))
        lay.addSpacing(8)
        lay.addLayout(row_opts)
        lay.addStretch(1)
        self.setCentralWidget(central)

        # Tray
        self.tray = QSystemTrayIcon(emoji_icon("🔑"), self)
        self.tray.setToolTip("One-Time Password")
        menu = QMenu()
        act_copy = QAction("Copy password", self)
        act_copy.triggered.connect(self.copy_password)
        act_change = QAction("Set / Change password…", self)
        act_change.triggered.connect(self.change_password)
        act_clear = QAction("Clear saved password on device", self)
        act_clear.triggered.connect(self.clear_saved_password)
        act_show = QAction("Show / Hide", self)
        act_show.triggered.connect(self.toggle_visible)
        act_quit = QAction("Quit", self)
        act_quit.triggered.connect(QApplication.instance().quit)
        menu.addAction(act_copy)
        menu.addAction(act_change)
        menu.addAction(act_clear)
        menu.addSeparator()
        menu.addAction(act_show)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.show()

        self.refresh_status()

    # ---- Helpers ----
    def refresh_status(self):
        has = self.store.get() is not None
        if has:
            self.status_lbl.setText("Password is stored. You can copy it any time from here or the tray menu.")
        else:
            self.status_lbl.setText("No password saved yet. Click “Set / Change password” to add one.")

    def toggle_visible(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.activateWindow()
            self.raise_()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.toggle_visible()

    def closeEvent(self, event):
        # Hide to tray instead of quitting
        self.hide()
        self.tray.showMessage("Still running", "The app is hidden in the system tray.",
                              QSystemTrayIcon.Information, 2000)
        event.ignore()

    # ---- Actions ----
    def change_password(self):
        dlg = PasswordDialog(self)
        if dlg.exec() == QDialog.Accepted:
            pw, remember = dlg.get_values()
            if not pw:
                QMessageBox.warning(self, "Password required", "Password cannot be empty.")
                return
            if not IS_WINDOWS and remember:
                QMessageBox.information(self, "Note",
                                        "Device store is only available on Windows. The password will be kept for this session.")
                remember = False
            self.store.set(pw, remember)
            self.refresh_status()
            self.tray.showMessage("Saved", "Password has been updated.", QSystemTrayIcon.Information, 1500)

    def clear_saved_password(self):
        self.store.clear_device_store()
        self.store.clear_memory()
        self.refresh_status()
        self.tray.showMessage("Cleared", "Saved password removed from this device.", QSystemTrayIcon.Information, 1500)

    def copy_password(self):
        pw = self.store.get()
        if not pw:
            dlg = PasswordDialog(self)
            if dlg.exec() != QDialog.Accepted:
                return
            pw, remember = dlg.get_values()
            if not pw:
                QMessageBox.warning(self, "Password required", "Password cannot be empty.")
                return
            if not IS_WINDOWS and remember:
                QMessageBox.information(self, "Note",
                                        "Device store is only available on Windows. The password will be kept for this session.")
                remember = False
            self.store.set(pw, remember)
            self.refresh_status()

        cb = QApplication.clipboard()
        cb.setText(pw)  # System clipboard
        self._last_copied_value = pw
        self.tray.showMessage("Copied", "Password copied to clipboard.", QSystemTrayIcon.Information, 1200)

        # Live settings (no need to press Save for effect)
        if self.chk_auto.isChecked():
            secs = max(1, int(self.spin_secs.value()))
            self._clear_timer.start(secs * 1000)

    def _maybe_clear_clipboard(self):
        try:
            cb = QApplication.clipboard()
            if self._last_copied_value is None:
                return
            if cb.text() == self._last_copied_value:
                cb.clear()
                cb.setText("")
                cb.clear()
                win_clear_clipboard()  # Windows buffer clear
                self.tray.showMessage("Clipboard cleared", "Password removed from clipboard buffer.",
                                      QSystemTrayIcon.Information, 1200)
        except Exception:
            pass
        finally:
            self._last_copied_value = None

    def save_settings(self):
        self.settings.auto_clear = self.chk_auto.isChecked()
        self.settings.auto_clear_secs = int(self.spin_secs.value())
        self.settings.save()
        QMessageBox.information(self, "Saved", "Settings updated.")

# ---------------- Entry ----------------
def main():
    ensure_dir(DATA_DIR)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(emoji_icon("🔑"))

    w = MainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
