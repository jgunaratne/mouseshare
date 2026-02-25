# MouseShare

A macOS menu bar app that shares your Mac's keyboard and mouse with an Ubuntu Linux machine over a direct USB-C cable.

## How It Works

1. **Push your mouse to any screen edge** → MouseShare captures all keyboard and mouse input and forwards it to the Linux machine over TCP
2. **Press Escape** → control returns to the Mac

The app lives entirely in the menu bar — no Dock icon, no main window.

## Network Setup (Required Before Use)

### On the Mac

1. Plug in the USB-C cable between the two machines
2. Open **System Settings → Network**
3. Find the USB-C / Thunderbolt network interface
4. Set **Configure IPv4** to **Manually**
5. Set **IP Address** to `192.168.100.1`
6. Set **Subnet Mask** to `255.255.255.0`
7. Click **Apply**

### On the Linux Machine

```bash
# Identify the USB interface (usually something like enx* or usb0)
ip link show

# Assign the static IP
sudo ip addr add 192.168.100.2/24 dev <interface-name>
sudo ip link set <interface-name> up
```

### Verify Connectivity

From the Mac: `ping 192.168.100.2`
From Linux: `ping 192.168.100.1`

## Building

Open `MouseShare.xcodeproj` in Xcode and build (⌘B). The project requires:

- **macOS 12.0+** deployment target
- **App Sandbox: Disabled** (already configured)
- No external dependencies — uses only system frameworks (AppKit, CoreGraphics, Network)

## First Launch

1. **Build and Run** from Xcode (⌘R)
2. **Grant Accessibility permission** when the system dialog appears
   - If you miss it: System Settings → Privacy & Security → Accessibility → add MouseShare
3. The menu bar icon appears — click it to see connection status
4. Run the Linux companion script on the Ubuntu machine
5. Once connected, push your mouse to any screen edge to start controlling Linux

## Connection Details

- **Protocol**: TCP with 4-byte big-endian length-prefix framing + JSON payload
- **Mac (server)**: `192.168.100.1:9876`
- **Linux (client)**: connects to the Mac's IP on port 9876

## Menu Bar Icons

| Icon | Meaning |
|------|---------|
| 🔌 Cable connector | USB-C cable not detected |
| 🔗 Horizontal cable | Waiting for Linux to connect |
| 🖱 Cursor with motion | Connected / Controlling Linux |

## Troubleshooting

- **"Failed to create event tap"** → Accessibility permission is not granted. Check System Settings.
- **No menu bar icon** → Make sure `LSUIElement` is set to `true` in Info.plist (it is by default).
- **Can't connect** → Verify both machines have their static IPs set and can ping each other.
- **Input not forwarding** → Make sure you push the cursor to the screen edge while the Linux client is connected.
