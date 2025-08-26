# 🔑 One-Time Password (Tray)

A lightweight cross-platform system tray app for securely storing and copying a single password.
Designed for convenience with **auto-clearing clipboard** support, **optional secure storage** (via `keyring`), and a simple UI.

---

## ✨ Features

* 🖥️ **Tray icon app** – runs quietly in the background.
* 🔑 **Set / change password** – store one password at a time.
* 📋 **Copy to clipboard** – one click from UI or tray menu.
* 🧹 **Auto-clear clipboard** – configurable timeout (default 20 seconds).
* 💾 **Secure storage** – integrates with the system keyring (Windows Credential Manager, macOS Keychain, GNOME Keyring, etc.).
* 🔐 **In-memory fallback** – if no keyring is available, password is only stored until app is closed.
* ⚡ **Windows special support** – tries to force-clear the system clipboard buffer.

---

## 📦 Requirements

* **Python 3.8+**
* [PySide6](https://pypi.org/project/PySide6/)
* [keyring](https://pypi.org/project/keyring/) *(optional but recommended)*

Install dependencies:

```bash
pip install PySide6 keyring
```

---

## 🚀 Usage

Run the app:

```bash
python app.py
```

When started:

1. A **tray icon 🔑** will appear.
2. Use **right-click menu** or **main window** to:

   * Copy password to clipboard
   * Set / change stored password
   * Clear saved password on device
   * Configure auto-clear clipboard timeout

### Tray Menu

* **Copy password** – copy stored password into clipboard
* **Set / Change password** – prompt for new password
* **Clear saved password** – remove stored password from keyring/device
* **Show / Hide** – toggle main window visibility
* **Quit** – exit the app

---

## ⚙️ Settings

Settings are stored in:

* **Windows**: `%LOCALAPPDATA%\NESearchTool_PasswordOnly\settings.json`
* **Linux/macOS**: `~/NESearchTool_PasswordOnly/settings.json`

Configurable options:

* `auto_clear`: whether clipboard clears automatically (default: `true`)
* `auto_clear_secs`: timeout in seconds (default: `20`)

---

## 🔒 Security Notes

* Auto-clear removes the password from **active clipboard buffer**, but **Windows Clipboard History (Win+V)** is separate and not cleared.
* If `keyring` is not available, the password is stored **only in memory** and lost when the app exits.
* Clipboard contents are inherently insecure – use auto-clear to reduce exposure.

---

## 📜 License

MIT License – free to use, modify, and distribute.
