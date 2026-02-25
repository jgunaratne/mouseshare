#!/usr/bin/env python3
"""
MouseShare — Linux Companion Daemon

Receives keyboard and mouse events from the macOS MouseShare app over a
USB-C TCP connection and injects them into the Linux input system via
uinput so they control the machine as if real hardware were attached.

When the Mac sends a returnControl event the daemon watches the local
cursor position and sends control back to the Mac when the cursor
reaches any screen edge.

Dependencies:
    pip3 install evdev
    sudo apt install xdotool        # X11
    sudo apt install ydotool        # Wayland (optional)
    sudo modprobe uinput            # kernel module
"""

import asyncio
import json
import logging
import os
import shutil
import struct
import subprocess
import sys
import re

from evdev import UInput, ecodes

# ── Configuration ──────────────────────────────────────────────────────

MAC_IP = "192.168.100.1"
PORT = 9876

# Detected at startup — see detect_screen_resolution().
SCREEN_WIDTH = 1920
SCREEN_HEIGHT = 1080

# Pixels from any edge before we hand control back to the Mac.
EDGE_THRESHOLD = 5

# Seconds to wait before retrying after a dropped connection.
RECONNECT_DELAY = 2.0

# Edge-detection polling interval in seconds.
EDGE_POLL_INTERVAL = 0.1

# ── Logging ────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mouseshare")

# ── Mac HID → Linux evdev Keycode Mapping ──────────────────────────────
#
# Mac CGEvent virtual keycodes (decimal) → Linux evdev KEY_* constants.
# Reference: Events.h in macOS IOKit / Carbon HIToolbox.

MAC_TO_LINUX_KEYCODE = {
    # ── Letters ──
    0:   ecodes.KEY_A,
    11:  ecodes.KEY_B,
    8:   ecodes.KEY_C,
    2:   ecodes.KEY_D,
    14:  ecodes.KEY_E,
    3:   ecodes.KEY_F,
    5:   ecodes.KEY_G,
    4:   ecodes.KEY_H,
    34:  ecodes.KEY_I,
    38:  ecodes.KEY_J,
    40:  ecodes.KEY_K,
    37:  ecodes.KEY_L,
    46:  ecodes.KEY_M,
    45:  ecodes.KEY_N,
    31:  ecodes.KEY_O,
    35:  ecodes.KEY_P,
    12:  ecodes.KEY_Q,
    15:  ecodes.KEY_R,
    1:   ecodes.KEY_S,
    17:  ecodes.KEY_T,
    32:  ecodes.KEY_U,
    9:   ecodes.KEY_V,
    13:  ecodes.KEY_W,
    7:   ecodes.KEY_X,
    16:  ecodes.KEY_Y,
    6:   ecodes.KEY_Z,

    # ── Number row ──
    29:  ecodes.KEY_0,
    18:  ecodes.KEY_1,
    19:  ecodes.KEY_2,
    20:  ecodes.KEY_3,
    21:  ecodes.KEY_4,
    23:  ecodes.KEY_5,
    22:  ecodes.KEY_6,
    26:  ecodes.KEY_7,
    28:  ecodes.KEY_8,
    25:  ecodes.KEY_9,

    # ── Punctuation & symbols ──
    49:  ecodes.KEY_SPACE,
    36:  ecodes.KEY_ENTER,
    51:  ecodes.KEY_BACKSPACE,
    48:  ecodes.KEY_TAB,
    53:  ecodes.KEY_ESC,
    43:  ecodes.KEY_COMMA,
    47:  ecodes.KEY_DOT,
    44:  ecodes.KEY_SLASH,
    41:  ecodes.KEY_SEMICOLON,
    39:  ecodes.KEY_APOSTROPHE,
    33:  ecodes.KEY_LEFTBRACE,
    30:  ecodes.KEY_RIGHTBRACE,
    42:  ecodes.KEY_BACKSLASH,
    50:  ecodes.KEY_GRAVE,
    27:  ecodes.KEY_MINUS,
    24:  ecodes.KEY_EQUAL,

    # ── Modifier keys ──
    56:  ecodes.KEY_LEFTSHIFT,
    60:  ecodes.KEY_RIGHTSHIFT,
    59:  ecodes.KEY_LEFTCTRL,
    62:  ecodes.KEY_RIGHTCTRL,
    58:  ecodes.KEY_LEFTALT,      # Mac Option → Linux Alt
    61:  ecodes.KEY_RIGHTALT,
    55:  ecodes.KEY_LEFTMETA,     # Mac Command → Linux Super
    54:  ecodes.KEY_RIGHTMETA,
    57:  ecodes.KEY_CAPSLOCK,

    # ── Function keys ──
    122: ecodes.KEY_F1,
    120: ecodes.KEY_F2,
    99:  ecodes.KEY_F3,
    118: ecodes.KEY_F4,
    96:  ecodes.KEY_F5,
    97:  ecodes.KEY_F6,
    98:  ecodes.KEY_F7,
    100: ecodes.KEY_F8,
    101: ecodes.KEY_F9,
    109: ecodes.KEY_F10,
    103: ecodes.KEY_F11,
    111: ecodes.KEY_F12,

    # ── Navigation ──
    123: ecodes.KEY_LEFT,
    124: ecodes.KEY_RIGHT,
    125: ecodes.KEY_DOWN,
    126: ecodes.KEY_UP,
    115: ecodes.KEY_HOME,
    119: ecodes.KEY_END,
    116: ecodes.KEY_PAGEUP,
    121: ecodes.KEY_PAGEDOWN,
    117: ecodes.KEY_DELETE,       # Mac Forward Delete
    114: ecodes.KEY_INSERT,

    # ── Numpad ──
    71:  ecodes.KEY_NUMLOCK,
    82:  ecodes.KEY_KP0,
    83:  ecodes.KEY_KP1,
    84:  ecodes.KEY_KP2,
    85:  ecodes.KEY_KP3,
    86:  ecodes.KEY_KP4,
    87:  ecodes.KEY_KP5,
    88:  ecodes.KEY_KP6,
    89:  ecodes.KEY_KP7,
    91:  ecodes.KEY_KP8,
    92:  ecodes.KEY_KP9,
    65:  ecodes.KEY_KPDOT,
    69:  ecodes.KEY_KPPLUS,
    78:  ecodes.KEY_KPMINUS,
    67:  ecodes.KEY_KPASTERISK,
    75:  ecodes.KEY_KPSLASH,
    76:  ecodes.KEY_KPENTER,

    # ── Media ──
    # Mac doesn't send these via CGEvent keycodes in the same way,
    # but if they arrive from a future extension they'll map here.
    # 1000+: reserved for custom media key mapping.
}

# ── Display Server Detection ──────────────────────────────────────────

USE_WAYLAND = bool(os.environ.get("WAYLAND_DISPLAY"))

def detect_display_server():
    """Log which display server is active."""
    if USE_WAYLAND:
        log.info("Display server: Wayland (will use ydotool)")
    else:
        log.info("Display server: X11 (will use xdotool)")


def detect_screen_resolution():
    """Detect the primary screen resolution and update the global constants.

    Tries xrandr first (works on X11 and most XWayland setups), then
    falls back to xdpyinfo.  If neither works, keeps the defaults.
    """
    global SCREEN_WIDTH, SCREEN_HEIGHT

    # ── Try xrandr ──
    if shutil.which("xrandr"):
        try:
            result = subprocess.run(
                ["xrandr"], capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                # Match the currently-active mode, e.g.  "1920x1080+0+0"
                m = re.search(r"(\d+)x(\d+)\+\d+\+\d+", line)
                if m:
                    SCREEN_WIDTH = int(m.group(1))
                    SCREEN_HEIGHT = int(m.group(2))
                    log.info("Screen resolution (xrandr): %dx%d", SCREEN_WIDTH, SCREEN_HEIGHT)
                    return
        except Exception as exc:
            log.debug("xrandr failed: %s", exc)

    # ── Try xdpyinfo ──
    if shutil.which("xdpyinfo"):
        try:
            result = subprocess.run(
                ["xdpyinfo"], capture_output=True, text=True, timeout=5
            )
            m = re.search(r"dimensions:\s+(\d+)x(\d+)\s+pixels", result.stdout)
            if m:
                SCREEN_WIDTH = int(m.group(1))
                SCREEN_HEIGHT = int(m.group(2))
                log.info("Screen resolution (xdpyinfo): %dx%d", SCREEN_WIDTH, SCREEN_HEIGHT)
                return
        except Exception as exc:
            log.debug("xdpyinfo failed: %s", exc)

    log.warning(
        "Could not detect screen resolution — using default %dx%d",
        SCREEN_WIDTH, SCREEN_HEIGHT,
    )

# ── Virtual Input Device ──────────────────────────────────────────────

def create_virtual_device():
    """Create a uinput virtual keyboard + mouse device."""

    # All keyboard keys + mouse buttons we might inject.
    key_caps = set(MAC_TO_LINUX_KEYCODE.values()) | {
        ecodes.BTN_LEFT,
        ecodes.BTN_RIGHT,
        ecodes.BTN_MIDDLE,
    }

    capabilities = {
        ecodes.EV_KEY: list(key_caps),
        ecodes.EV_REL: [
            ecodes.REL_X,
            ecodes.REL_Y,
            ecodes.REL_WHEEL,
            ecodes.REL_HWHEEL,
        ],
    }

    device = UInput(capabilities, name="MouseShare Virtual Input")
    log.info("Virtual input device created: %s", device.device.path)
    return device

# ── Event Injection ───────────────────────────────────────────────────

def inject_event(event: dict, device: UInput):
    """Decode a SharedEvent dict and inject the appropriate input.

    Returns (True, normalised_y) if the cursor has hit the left edge
    and control should be returned to the Mac; otherwise (False, 0).
    """

    event_type = event.get("type")

    if event_type == "mouseMove":
        x, y = _inject_mouse_move(event)
        if x <= EDGE_THRESHOLD:
            norm_y = y / SCREEN_HEIGHT if SCREEN_HEIGHT else 0
            return (True, norm_y)

    elif event_type == "leftMouseDown":
        device.write(ecodes.EV_KEY, ecodes.BTN_LEFT, 1)
        device.syn()

    elif event_type == "leftMouseUp":
        device.write(ecodes.EV_KEY, ecodes.BTN_LEFT, 0)
        device.syn()

    elif event_type == "rightMouseDown":
        device.write(ecodes.EV_KEY, ecodes.BTN_RIGHT, 1)
        device.syn()

    elif event_type == "rightMouseUp":
        device.write(ecodes.EV_KEY, ecodes.BTN_RIGHT, 0)
        device.syn()

    elif event_type in ("keyDown", "keyUp"):
        mac_code = event.get("keyCode")
        if mac_code is None:
            return (False, 0)
        linux_code = MAC_TO_LINUX_KEYCODE.get(mac_code)
        if linux_code is None:
            log.warning("Unknown Mac keycode %d — add it to the mapping table", mac_code)
            return (False, 0)
        value = 1 if event_type == "keyDown" else 0
        device.write(ecodes.EV_KEY, linux_code, value)
        device.syn()

    elif event_type == "scrollWheel":
        dx = event.get("scrollDeltaX", 0)
        dy = event.get("scrollDeltaY", 0)
        if dy:
            device.write(ecodes.EV_REL, ecodes.REL_WHEEL, int(dy))
        if dx:
            device.write(ecodes.EV_REL, ecodes.REL_HWHEEL, int(dx))
        if dy or dx:
            device.syn()

    elif event_type == "returnControl":
        # Mac pressed Escape — it already stopped capturing on its side.
        log.info("Mac sent returnControl (Escape) — acknowledged")

    return (False, 0)


def _inject_mouse_move(event: dict):
    """Move the cursor to the absolute screen position derived from
    the normalised coordinates.  Returns the computed (x, y).

    The Mac's normalizedY is already top-down (CoreGraphics origin is
    top-left), which matches Linux X11 coordinates, so no Y-flip is
    needed.
    """
    nx = event.get("normalizedX", 0)
    ny = event.get("normalizedY", 0)

    x = int(nx * SCREEN_WIDTH)
    y = int(ny * SCREEN_HEIGHT)

    # Clamp to screen bounds.
    x = max(0, min(x, SCREEN_WIDTH - 1))
    y = max(0, min(y, SCREEN_HEIGHT - 1))

    if USE_WAYLAND:
        subprocess.run(
            ["ydotool", "mousemove", "--absolute", "-x", str(x), "-y", str(y)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        subprocess.run(
            ["xdotool", "mousemove", str(x), str(y)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    return (x, y)

# ── Edge Detection ────────────────────────────────────────────────────

def _get_cursor_position():
    """Return (x, y) of the current cursor, or None on failure."""
    try:
        if USE_WAYLAND:
            # ydotool doesn't have a direct getmouselocation equivalent
            # on all versions; fall back to xdotool under XWayland if
            # available, otherwise return centre to avoid false triggers.
            result = subprocess.run(
                ["xdotool", "getmouselocation"],
                capture_output=True, text=True, timeout=1,
            )
        else:
            result = subprocess.run(
                ["xdotool", "getmouselocation"],
                capture_output=True, text=True, timeout=1,
            )
        # Output looks like: x:1234 y:567 screen:0 window:12345678
        match = re.search(r"x:(\d+)\s+y:(\d+)", result.stdout)
        if match:
            return int(match.group(1)), int(match.group(2))
    except Exception:
        pass
    return None


async def _edge_detection_loop(writer: asyncio.StreamWriter, stop_event: asyncio.Event):
    """Poll cursor position and send returnControl when the left edge is reached.

    Only the left edge triggers return-control because the Linux monitor
    sits to the right of the Mac monitor.
    """
    log.info("Edge detection active — push cursor to left screen edge to return control to Mac")
    while not stop_event.is_set():
        pos = _get_cursor_position()
        if pos:
            x, y = pos
            if x <= EDGE_THRESHOLD:
                log.info("Left edge reached at (%d, %d) — returning control to Mac", x, y)
                # Normalise Y so the Mac can place its cursor at the
                # matching vertical position on its own screen.
                norm_y = y / SCREEN_HEIGHT if SCREEN_HEIGHT else 0
                return_event = {
                    "type": "returnControl",
                    "normalizedX": 0,
                    "normalizedY": norm_y,
                    "edge": "left",
                }
                payload = json.dumps(return_event).encode("utf-8")
                header = struct.pack("!I", len(payload))
                try:
                    writer.write(header + payload)
                    await writer.drain()
                except Exception as exc:
                    log.warning("Failed to send returnControl: %s", exc)
                stop_event.set()
                return
        await asyncio.sleep(EDGE_POLL_INTERVAL)

# ── TCP Client ────────────────────────────────────────────────────────

async def _read_exact(reader: asyncio.StreamReader, n: int) -> bytes:
    """Read exactly n bytes from the stream, raising on EOF."""
    data = b""
    while len(data) < n:
        chunk = await reader.read(n - len(data))
        if not chunk:
            raise ConnectionError("Connection closed by remote")
        data += chunk
    return data


async def tcp_client(device: UInput):
    """Connect to the Mac and process events in a loop.
    Reconnects automatically on failure."""

    while True:
        try:
            log.info("Connecting to Mac at %s:%d …", MAC_IP, PORT)
            reader, writer = await asyncio.open_connection(MAC_IP, PORT)
            log.info("Connected to Mac")

            while True:
                # Read 4-byte length header.
                header = await _read_exact(reader, 4)
                length = struct.unpack("!I", header)[0]

                if length == 0 or length > 1_000_000:
                    log.warning("Invalid message length: %d — dropping connection", length)
                    break

                # Read the JSON payload.
                payload = await _read_exact(reader, length)
                event = json.loads(payload.decode("utf-8"))

                # Inject the event and check if the cursor hit the left edge.
                should_return, norm_y = inject_event(event, device)

                if should_return:
                    log.info("Left edge hit — sending returnControl to Mac")
                    return_event = {
                        "type": "returnControl",
                        "normalizedX": 0,
                        "normalizedY": norm_y,
                        "edge": "left",
                    }
                    ret_payload = json.dumps(return_event).encode("utf-8")
                    ret_header = struct.pack("!I", len(ret_payload))
                    writer.write(ret_header + ret_payload)
                    await writer.drain()

        except (ConnectionError, OSError, asyncio.IncompleteReadError) as exc:
            log.warning("Connection lost: %s", exc)
        except Exception as exc:
            log.error("Unexpected error: %s", exc, exc_info=True)

        log.info("Reconnecting in %.0f seconds …", RECONNECT_DELAY)
        await asyncio.sleep(RECONNECT_DELAY)

# ── Startup Checks ────────────────────────────────────────────────────

def check_uinput():
    """Verify /dev/uinput is accessible."""
    if not os.path.exists("/dev/uinput"):
        print("❌ /dev/uinput not found.")
        print("   Run: sudo modprobe uinput")
        print("   To persist: echo 'uinput' | sudo tee -a /etc/modules")
        sys.exit(1)
    try:
        with open("/dev/uinput", "rb"):
            pass
    except PermissionError:
        print("❌ Cannot open /dev/uinput — permission denied.")
        print("   Run this script with sudo, or set up a udev rule:")
        print('   echo \'KERNEL=="uinput", MODE="0660", GROUP="input"\' | '
              "sudo tee /etc/udev/rules.d/99-uinput.rules")
        print("   sudo udevadm control --reload-rules && sudo udevadm trigger")
        sys.exit(1)


def check_mouse_tool():
    """Verify xdotool or ydotool is installed."""
    if USE_WAYLAND:
        if not shutil.which("ydotool"):
            print("❌ ydotool not found (required for Wayland).")
            print("   Run: sudo apt install ydotool")
            print("   Then start the daemon: sudo ydotoold &")
            sys.exit(1)
    else:
        if not shutil.which("xdotool"):
            print("❌ xdotool not found (required for X11).")
            print("   Run: sudo apt install xdotool")
            sys.exit(1)

# ── Main ──────────────────────────────────────────────────────────────

def main():
    log.info("MouseShare Linux Companion starting …")

    # 1. Check /dev/uinput
    check_uinput()

    # 2. Check mouse positioning tool
    check_mouse_tool()

    # 3. Detect display server
    detect_display_server()

    # 3b. Detect screen resolution
    detect_screen_resolution()

    # 4. Create virtual input device
    device = create_virtual_device()

    # 5. Log connection target
    log.info("Will connect to Mac at %s:%d", MAC_IP, PORT)

    # 6. Run the async event loop
    try:
        asyncio.run(tcp_client(device))
    except KeyboardInterrupt:
        log.info("Shutting down …")
    finally:
        device.close()
        log.info("Virtual device closed. Goodbye.")


if __name__ == "__main__":
    main()
