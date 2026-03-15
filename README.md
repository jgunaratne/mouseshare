# MouseShare

<p align="center">
  <img src="icon.png" width="128" alt="MouseShare icon" />
</p>

**Share your Mac's keyboard and mouse with another computer over a direct USB-C cable.**

Push your cursor to a screen edge and it jumps to the other machine — just like a multi-monitor setup, but across two separate computers. Press Escape (or push back to the return edge) to regain control on the Mac.

---

## Supported Platforms

| Role | Platform | Language |
|------|----------|----------|
| **Host** (captures input) | macOS 12+ | Swift / AppKit |
| **Client** (receives input) | Windows 10/11 | Python 3.10+ |
| **Client** (receives input) | Ubuntu Linux (X11 / Wayland) | Python 3.10+ |

The **Mac** always acts as the host — it captures keyboard and mouse events and sends them over TCP to a connected client machine.

---

## How It Works

1. Connect both machines with a **USB-C cable** (Thunderbolt or USB-C to USB-C)
2. Assign static IPs on the USB network interface (see [Network Setup](#network-setup))
3. Start MouseShare on the Mac (host) and the companion script on the client
4. **Push your cursor to a screen edge** → input is forwarded to the client
5. **Press Escape** or push the cursor back to the opposite edge → control returns to the Mac

---

## Network Setup

Both machines need static IPs on the USB-C network interface.

### Mac (Host)

1. Plug in the USB-C cable between the two machines
2. Open **System Settings → Network**
3. Find the USB-C / Thunderbolt network interface
4. Set **Configure IPv4** to **Manually**
5. Set **IP Address** to `192.168.100.1`
6. Set **Subnet Mask** to `255.255.255.0`
7. Click **Apply**

### Client Machine (Windows or Linux)

**Windows** — Open *Settings → Network & Internet → Ethernet* and set:
- IP: `192.168.100.2`
- Subnet: `255.255.255.0`

**Linux** — Run:

```bash
# Find the USB interface (usually enx* or usb0)
ip link show

# Assign the static IP
sudo ip addr add 192.168.100.2/24 dev <interface-name>
sudo ip link set <interface-name> up
```

### Verify Connectivity

```
# From Mac
ping 192.168.100.2

# From client
ping 192.168.100.1
```

---

## macOS (Host)

The Mac app is a **menu bar application** — no Dock icon, no main window.

### Building

```bash
cd mac
open MouseShare.xcodeproj
# Build with ⌘B, Run with ⌘R
```

**Requirements:**
- macOS 12.0+
- Xcode
- No external dependencies (uses AppKit, CoreGraphics, Network)

### First Launch

1. **Build and Run** from Xcode (⌘R)
2. **Grant Accessibility permission** when prompted
   - If missed: *System Settings → Privacy & Security → Accessibility → add MouseShare*
3. A menu bar icon appears — click it to see connection status
4. Start the companion script on the client machine
5. Push your cursor to any screen edge to start controlling the client

### Menu Bar Icons

| Icon | Meaning |
|------|---------|
| 🔌 Cable connector | USB-C cable not detected |
| 🔗 Horizontal cable | Waiting for client to connect |
| 🖱 Cursor with motion | Connected / Controlling client |

### Troubleshooting (Mac)

- **"Failed to create event tap"** → Accessibility permission not granted
- **No menu bar icon** → Verify `LSUIElement` is `true` in Info.plist
- **Can't connect** → Check static IPs and ping between machines
- **Input not forwarding** → Push the cursor to the screen edge while a client is connected

---

## Windows (Client)

### Requirements

- Python 3.10+
- Windows 10 or 11

### Setup

```powershell
cd windows

# Install dependencies (tray icon support)
pip install -r requirements.txt

# Run with tray icon (recommended, no console window)
pythonw mouseshare.py

# Run with console (for debugging)
python mouseshare.py
```

### Auto-Start on Boot

```powershell
# Enable
python mouseshare.py --install

# Disable
python mouseshare.py --uninstall
```

### System Tray

| Color | Meaning |
|-------|---------|
| 🟢 Green | Connected to Mac |
| 🟡 Yellow | Connecting… |
| ⚪ Gray | Disconnected |

Right-click the tray icon for status info or to quit.

### Configuration

Edit the top of `mouseshare.py` to change:

- `MAC_IP` — Mac's USB-C IP address (default: `192.168.100.1`)
- `PORT` — TCP port (default: `9876`)

---

## Linux (Client)

### Requirements

- Python 3.10+
- Ubuntu (or any distro with uinput support)
- **X11**: `xdotool`
- **Wayland**: `ydotool`

### Setup

```bash
cd linux

# Install Python dependency
pip3 install evdev

# Install cursor positioning tool
sudo apt install xdotool        # X11
sudo apt install ydotool        # Wayland (start daemon: sudo ydotoold &)

# Enable the uinput kernel module
sudo modprobe uinput

# To persist across reboots:
echo 'uinput' | sudo tee -a /etc/modules
```

> **Note:** If you get a `/dev/uinput` permission error, create a udev rule:
> ```bash
> echo 'KERNEL=="uinput", MODE="0660", GROUP="input"' | sudo tee /etc/udev/rules.d/99-uinput.rules
> sudo udevadm control --reload-rules && sudo udevadm trigger
> ```

### Running

```bash
# Run (may require sudo for uinput access)
python3 mouseshare.py
```

The script auto-detects X11 vs. Wayland and the primary screen resolution.

### Auto-Start on Boot

Create a systemd service to start MouseShare automatically:

```bash
# Create the service file
sudo tee /etc/systemd/system/mouseshare.service > /dev/null << 'EOF'
[Unit]
Description=MouseShare Linux Companion
After=network.target graphical-session.target

[Service]
Type=simple
User=YOUR_USERNAME
Environment=DISPLAY=:0
ExecStart=/usr/bin/python3 /path/to/linux/mouseshare.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Update the User and ExecStart path, then enable
sudo systemctl daemon-reload
sudo systemctl enable mouseshare.service
sudo systemctl start mouseshare.service
```

> **Tip:** Replace `YOUR_USERNAME` with your Linux username and update the `ExecStart` path to where `mouseshare.py` lives. If using Wayland, also add `Environment=WAYLAND_DISPLAY=wayland-0`.

```bash
# Check status
sudo systemctl status mouseshare.service

# View logs
journalctl -u mouseshare.service -f

# Disable auto-start
sudo systemctl disable mouseshare.service
```

### Configuration

Edit the top of `mouseshare.py` to change:

- `MAC_IP` — Mac's USB-C IP address (default: `192.168.100.1`)
- `PORT` — TCP port (default: `9876`)

### Troubleshooting (Linux)

- **Mouse position is offset with display scaling** — The script auto-detects the scale factor from `xdotool`, `GDK_SCALE`, `QT_SCALE_FACTOR`, or GNOME gsettings. If it still gets it wrong, you can force the scale factor by setting `GDK_SCALE` before running:
  ```bash
  GDK_SCALE=2 python3 mouseshare.py
  ```

---

## Protocol

MouseShare uses a simple TCP protocol with **4-byte big-endian length-prefix framing** and **JSON payloads**.

- **Server**: Mac at `192.168.100.1:9876`
- **Client**: connects to the Mac's IP on port `9876`

Events include mouse movements (normalized coordinates), clicks, key presses, scroll wheel, edge configuration, heartbeats, and return-control signals.

---

## License

This project is for personal use.
