import os
import sys
import json
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QPainter, QPixmap, QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSystemTrayIcon, QMenu, QMessageBox,
    QDialog, QLineEdit, QCheckBox, QSpinBox
)

# keyring is optional; if missing, we store only in memory
try:
    import keyring  # pip install keyring
except Exception:
    keyring = None

# Optional Windows API fallback to force‑clear clipboard buffer
IS_WINDOWS = sys.platform.startswith("win")
if IS_WINDOWS:
    try:
        from ctypes import windll
        def win_clear_clipboard() -> bool:
            try:
                user32 = windll.user32
                if user32.OpenClipboard(0):
                    user32.EmptyClipboard()
                    user32.CloseClipboard()
                    return True
            except Exception:
                pass
            return False
    except Exception:
        def win_clear_clipboard() -> bool:
            return False
else:
    def win_clear_clipboard() -> bool:
        return False

APP_NAME = "One-Time Password (Tray)"
SERVICE_NAME = "NESearchTool_PasswordOnly"
USERNAME_LABEL = "default"  # logical account label for the saved password (not an OS user)

# ---------- Paths / settings ----------
def user_data_dir():
    root = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(root, "NESearchTool_PasswordOnly")

def ensure_dir(path):
    os.makedirs(path, exist_ok=True)

SETTINGS_PATH = os.path.join(user_data_dir(), "settings.json")

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
            ensure_dir(user_data_dir())
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump({
                    "auto_clear": self.auto_clear,
                    "auto_clear_secs": self.auto_clear_secs
                }, f, indent=2)
        except Exception:
            pass

# ---------- Emoji icon builder ----------
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

# ---------- Password storage ----------
class PasswordStore:
    def __init__(self, service: str, account: str):
        self.service = service
        self.account = account
        self._in_memory: Optional[str] = None
    def get(self) -> Optional[str]:
        if self._in_memory:
            return self._in_memory
        if keyring is not None:
            try:
                pw = keyring.get_password(self.service, self.account)
                if pw:
                    self._in_memory = pw
                    return pw
            except Exception:
                pass
        return None
    def set(self, password: str, remember_device: bool):
        self._in_memory = password
        if remember_device and keyring is not None:
            try:
                keyring.set_password(self.service, self.account, password)
            except Exception:
                # keep in memory even if secure store fails
                pass
    def clear_device_store(self):
        if keyring is not None:
            try:
                keyring.delete_password(self.service, self.account)
            except Exception:
                pass
    def clear_memory(self):
        self._in_memory = None

# ---------- Password dialog ----------
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

        self.chk_remember = QCheckBox("Remember on this device (secure store)")
        if keyring is None:
            self.chk_remember.setText("Remember for this session (secure store not available)")
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

# ---------- Main window ----------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(420, 240)

        self.settings = Settings()
        self.settings.load()

        self.store = PasswordStore(SERVICE_NAME, USERNAME_LABEL)

        # Clipboard clear timer
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
        lay.addWidget(QLabel("Note: Windows Clipboard History (Win+V) is separate. Auto‑clear empties only the active clipboard buffer."))
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

    # --- UI helpers ---
    def refresh_status(self):
        has = self.store.get() is not None
        if has:
            if keyring is not None:
                self.status_lbl.setText("Password is stored. You can copy it any time from here or the tray menu.")
            else:
                self.status_lbl.setText("Password is stored for this session (secure store not available).")
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

    # --- Actions ---
    def change_password(self):
        dlg = PasswordDialog(self)
        if dlg.exec() == QDialog.Accepted:
            pw, remember = dlg.get_values()
            if not pw:
                QMessageBox.warning(self, "Password required", "Password cannot be empty.")
                return
            if not remember and keyring is not None:
                self.store.clear_device_store()
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
            if not remember and keyring is not None:
                self.store.clear_device_store()
            self.store.set(pw, remember)
            self.refresh_status()

        cb = QApplication.clipboard()
        cb.setText(pw)  # No mode arg; default is the system clipboard
        self._last_copied_value = pw
        self.tray.showMessage("Copied", "Password copied to clipboard.", QSystemTrayIcon.Information, 1200)

        # Use LIVE UI values (no need to press Save for effect)
        if self.chk_auto.isChecked():
            secs = max(1, int(self.spin_secs.value()))
            self._clear_timer.start(secs * 1000)

    def _maybe_clear_clipboard(self):
        try:
            cb = QApplication.clipboard()
            if self._last_copied_value is None:
                return
            # Only clear if clipboard still holds the same password we copied
            if cb.text() == self._last_copied_value:
                # Layered clearing attempts
                cb.clear()
                cb.setText("")
                cb.clear()
                # Windows fallback to force‑clear system buffer
                win_clear_clipboard()
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

# ---------- Entry ----------
def main():
    ensure_dir(user_data_dir())
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(emoji_icon("🔑"))

    w = MainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
