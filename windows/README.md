# MouseShare — Windows Client

Receives keyboard and mouse events from a Mac running MouseShare and injects
them into Windows via the SendInput API.

## Requirements

- Python 3.10+
- Windows 10/11

## Setup

```powershell
# Install dependencies (for tray icon support)
pip install -r requirements.txt

# Run with tray icon (no console window)
pythonw mouseshare.py

# Run with console (for debugging)
python mouseshare.py
```

## Auto-Start on Boot

```powershell
# Enable auto-start
python mouseshare.py --install

# Disable auto-start
python mouseshare.py --uninstall
```

## System Tray

When running, MouseShare shows a colored icon in the system tray:

- 🟢 **Green** — Connected to Mac
- 🟡 **Yellow** — Connecting…
- ⚪ **Gray** — Disconnected

Right-click the tray icon to see connection status or quit.

## Configuration

Edit the top of `mouseshare.py` to change:

- `MAC_IP` — Mac's USB-C IP address (default: `192.168.100.1`)
- `PORT` — TCP port (default: `9876`)
