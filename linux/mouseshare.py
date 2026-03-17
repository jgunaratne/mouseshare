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
import socket
import struct
import subprocess
import sys
import re
import time

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

# ── Mac Edge Configuration ─────────────────────────────────────────────
# Which edge the Mac selected (updated via edgeConfig messages from Mac).
mac_edge = "right"

# The edge on THIS screen that returns control to the Mac (opposite of Mac's edge).
OPPOSITE_EDGE = {
    "right": "left",
    "left": "right",
    "top": "bottom",
    "bottom": "top",
}

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
    """Detect the *logical* screen resolution and update the global constants.

    Display scaling (e.g. 150 % or 200 %) means the physical pixel count
    reported by xrandr differs from the logical coordinate space used by
    xdotool.  We must use the logical resolution so that normalised
    coordinates from the Mac map correctly.

    Strategy:
      1. xdotool getdisplaygeometry  — returns logical size directly (X11).
      2. xrandr + scale-factor env   — physical size ÷ scale factor.
      3. xdpyinfo                    — last-resort fallback.
    """
    global SCREEN_WIDTH, SCREEN_HEIGHT

    # ── Try xdotool getdisplaygeometry (best on X11 — already logical) ──
    if not USE_WAYLAND and shutil.which("xdotool"):
        try:
            result = subprocess.run(
                ["xdotool", "getdisplaygeometry"],
                capture_output=True, text=True, timeout=5,
            )
            parts = result.stdout.strip().split()
            if len(parts) == 2:
                SCREEN_WIDTH = int(parts[0])
                SCREEN_HEIGHT = int(parts[1])
                log.info(
                    "Screen resolution (xdotool, logical): %dx%d",
                    SCREEN_WIDTH, SCREEN_HEIGHT,
                )
                return
        except Exception as exc:
            log.debug("xdotool getdisplaygeometry failed: %s", exc)

    # ── Try xrandr (apply scale factor if present) ──
    if shutil.which("xrandr"):
        try:
            result = subprocess.run(
                ["xrandr"], capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                # Match the currently-active mode, e.g.  "1920x1080+0+0"
                m = re.search(r"(\d+)x(\d+)\+\d+\+\d+", line)
                if m:
                    phys_w = int(m.group(1))
                    phys_h = int(m.group(2))
                    scale = _get_display_scale_factor()
                    SCREEN_WIDTH = int(phys_w / scale)
                    SCREEN_HEIGHT = int(phys_h / scale)
                    if scale != 1.0:
                        log.info(
                            "Screen resolution (xrandr): %dx%d physical, "
                            "scale=%.2f, logical=%dx%d",
                            phys_w, phys_h, scale,
                            SCREEN_WIDTH, SCREEN_HEIGHT,
                        )
                    else:
                        log.info(
                            "Screen resolution (xrandr): %dx%d",
                            SCREEN_WIDTH, SCREEN_HEIGHT,
                        )
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


def _get_display_scale_factor() -> float:
    """Detect the display scale factor from environment or gsettings.

    Returns 1.0 if no scaling is detected or detection fails.
    """
    # GDK_SCALE (set by GNOME / GTK apps)
    gdk_scale = os.environ.get("GDK_SCALE")
    if gdk_scale:
        try:
            scale = float(gdk_scale)
            if scale > 0:
                log.debug("Scale factor from GDK_SCALE: %.2f", scale)
                return scale
        except ValueError:
            pass

    # QT_SCALE_FACTOR (KDE / Qt apps)
    qt_scale = os.environ.get("QT_SCALE_FACTOR")
    if qt_scale:
        try:
            scale = float(qt_scale)
            if scale > 0:
                log.debug("Scale factor from QT_SCALE_FACTOR: %.2f", scale)
                return scale
        except ValueError:
            pass

    # gsettings (GNOME)
    if shutil.which("gsettings"):
        try:
            result = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "text-scaling-factor"],
                capture_output=True, text=True, timeout=3,
            )
            text_scale = float(result.stdout.strip())

            result2 = subprocess.run(
                ["gsettings", "get", "org.gnome.mutter", "experimental-features"],
                capture_output=True, text=True, timeout=3,
            )
            # Check for fractional scaling
            uses_fractional = "scale-monitor-framebuffer" in result2.stdout

            if not uses_fractional:
                # Integer scaling — check the scaling factor
                result3 = subprocess.run(
                    ["gsettings", "get", "org.gnome.desktop.interface", "scaling-factor"],
                    capture_output=True, text=True, timeout=3,
                )
                m = re.search(r"uint32\s+(\d+)", result3.stdout)
                if m:
                    int_scale = int(m.group(1))
                    if int_scale > 1:
                        log.debug("Scale factor from gsettings (integer): %d", int_scale)
                        return float(int_scale)

            # For fractional scaling, xrandr --listmonitors shows the
            # effective transform, but xdotool getdisplaygeometry (tried
            # first) is the most reliable source.
        except Exception as exc:
            log.debug("gsettings scale detection failed: %s", exc)

    return 1.0

# ── Virtual Input Devices ─────────────────────────────────────────────
#
# We create SEPARATE uinput devices for keyboard and mouse.  Modern
# Linux input stacks (libinput) classify a device that advertises both
# EV_KEY keyboard codes *and* EV_REL axes as a pointer, which can cause
# keyboard events from it to be ignored or deprioritised.  Splitting
# them ensures reliable key injection.

def create_virtual_devices():
    """Create separate uinput devices for keyboard and mouse.

    Returns (keyboard_device, mouse_device).
    """

    # ── Keyboard device ──
    keyboard_caps = {
        ecodes.EV_KEY: list(set(MAC_TO_LINUX_KEYCODE.values())),
        # Enable key-repeat so held keys auto-repeat as expected.
        ecodes.EV_REP: [ecodes.REP_DELAY, ecodes.REP_PERIOD],
    }
    keyboard_dev = UInput(keyboard_caps, name="MouseShare Virtual Keyboard")
    log.info("Virtual keyboard device created: %s", keyboard_dev.device.path)

    # ── Mouse device ──
    mouse_caps = {
        ecodes.EV_KEY: [
            ecodes.BTN_LEFT,
            ecodes.BTN_RIGHT,
            ecodes.BTN_MIDDLE,
        ],
        ecodes.EV_REL: [
            ecodes.REL_X,
            ecodes.REL_Y,
            ecodes.REL_WHEEL,
            ecodes.REL_HWHEEL,
        ],
    }
    mouse_dev = UInput(mouse_caps, name="MouseShare Virtual Mouse")
    log.info("Virtual mouse device created: %s", mouse_dev.device.path)

    return keyboard_dev, mouse_dev

# ── Event Injection ───────────────────────────────────────────────────

# Track currently pressed keys/buttons so we can release them all
# when control switches back to the Mac (prevents stuck keys).
_pressed_keys: set[int] = set()
_pressed_buttons: set[int] = set()


def _release_all_keys(keyboard_dev: UInput, mouse_dev: UInput):
    """Release every key and button that is currently held down."""
    released = 0
    for code in list(_pressed_keys):
        keyboard_dev.write(ecodes.EV_KEY, code, 0)
        released += 1
    if _pressed_keys:
        keyboard_dev.syn()
    _pressed_keys.clear()

    for code in list(_pressed_buttons):
        mouse_dev.write(ecodes.EV_KEY, code, 0)
        released += 1
    if _pressed_buttons:
        mouse_dev.syn()
    _pressed_buttons.clear()

    if released:
        log.info("Released %d stuck key(s)/button(s)", released)


def inject_event(event: dict, keyboard_dev: UInput, mouse_dev: UInput):
    """Decode a SharedEvent dict and inject the appropriate input.

    Returns (True, normalised_y, edge_name) if the cursor has hit the return
    edge and control should be returned to the Mac; otherwise (False, 0, None).
    """
    global mac_edge

    event_type = event.get("type")

    if event_type == "mouseMove":
        x, y = _inject_mouse_move(event)
        return_edge = OPPOSITE_EDGE.get(mac_edge, "left")
        triggered, norm_x, norm_y = _check_edge(return_edge, x, y)
        if triggered:
            return (True, norm_y, return_edge)

    elif event_type == "leftMouseDown":
        _pressed_buttons.add(ecodes.BTN_LEFT)
        mouse_dev.write(ecodes.EV_KEY, ecodes.BTN_LEFT, 1)
        mouse_dev.syn()

    elif event_type == "leftMouseUp":
        _pressed_buttons.discard(ecodes.BTN_LEFT)
        mouse_dev.write(ecodes.EV_KEY, ecodes.BTN_LEFT, 0)
        mouse_dev.syn()

    elif event_type == "rightMouseDown":
        _pressed_buttons.add(ecodes.BTN_RIGHT)
        mouse_dev.write(ecodes.EV_KEY, ecodes.BTN_RIGHT, 1)
        mouse_dev.syn()

    elif event_type == "rightMouseUp":
        _pressed_buttons.discard(ecodes.BTN_RIGHT)
        mouse_dev.write(ecodes.EV_KEY, ecodes.BTN_RIGHT, 0)
        mouse_dev.syn()

    elif event_type in ("keyDown", "keyUp"):
        mac_code = event.get("keyCode")
        if mac_code is None:
            return (False, 0, None)
        linux_code = MAC_TO_LINUX_KEYCODE.get(mac_code)
        if linux_code is None:
            log.warning("Unknown Mac keycode %d — add it to the mapping table", mac_code)
            return (False, 0, None)
        value = 1 if event_type == "keyDown" else 0
        if value == 1:
            _pressed_keys.add(linux_code)
        else:
            _pressed_keys.discard(linux_code)
        keyboard_dev.write(ecodes.EV_KEY, linux_code, value)
        keyboard_dev.syn()

    elif event_type == "scrollWheel":
        dx = event.get("scrollDeltaX", 0)
        dy = event.get("scrollDeltaY", 0)
        if dy:
            mouse_dev.write(ecodes.EV_REL, ecodes.REL_WHEEL, int(dy))
        if dx:
            mouse_dev.write(ecodes.EV_REL, ecodes.REL_HWHEEL, int(dx))
        if dy or dx:
            mouse_dev.syn()

    elif event_type == "edgeConfig":
        # Mac is telling us which edge it selected.
        new_edge = event.get("edge", "right")
        mac_edge = new_edge
        log.info("Mac edge configured to '%s' — return edge is '%s'", mac_edge, OPPOSITE_EDGE.get(mac_edge, "left"))

    elif event_type == "returnControl":
        # Release any keys/buttons still held down to prevent stuck keys.
        _release_all_keys(keyboard_dev, mouse_dev)
        log.info("Mac sent returnControl — acknowledged, all keys released")

    elif event_type == "heartbeat":
        log.debug("Heartbeat received from Mac")

    return (False, 0, None)


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

def _check_edge(edge: str, x: int, y: int) -> tuple[bool, float, float]:
    """Check whether the cursor is at the given screen edge.

    Returns (triggered, normalizedX, normalizedY).
    """
    norm_x = x / SCREEN_WIDTH if SCREEN_WIDTH else 0
    norm_y = y / SCREEN_HEIGHT if SCREEN_HEIGHT else 0
    if edge == "left":
        return (x <= EDGE_THRESHOLD, 0.0, norm_y)
    elif edge == "right":
        return (x >= SCREEN_WIDTH - 1 - EDGE_THRESHOLD, 1.0, norm_y)
    elif edge == "top":
        return (y <= EDGE_THRESHOLD, norm_x, 0.0)
    elif edge == "bottom":
        return (y >= SCREEN_HEIGHT - 1 - EDGE_THRESHOLD, norm_x, 1.0)
    return (False, 0.0, 0.0)

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
    """Poll cursor position and send returnControl when the return edge is reached."""
    return_edge = OPPOSITE_EDGE.get(mac_edge, "left")
    log.info("Edge detection active — push cursor to %s screen edge to return control to Mac", return_edge)
    while not stop_event.is_set():
        pos = _get_cursor_position()
        if pos:
            x, y = pos
            triggered, norm_x, norm_y = _check_edge(return_edge, x, y)
            if triggered:
                log.info("%s edge reached at (%d, %d) — returning control to Mac", return_edge.title(), x, y)
                return_event = {
                    "type": "returnControl",
                    "normalizedX": norm_x,
                    "normalizedY": norm_y,
                    "edge": return_edge,
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


async def tcp_client(keyboard_dev: UInput, mouse_dev: UInput):
    """Connect to the Mac and process events in a loop.
    Reconnects automatically on failure."""

    while True:
        writer = None
        try:
            log.info("Connecting to Mac at %s:%d …", MAC_IP, PORT)
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(MAC_IP, PORT),
                timeout=5.0,
            )

            # Enable TCP keepalive to detect dead connections quickly.
            sock = writer.get_extra_info("socket")
            if sock is not None:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                # Seconds before first keepalive probe.
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30)
                # Seconds between probes.
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
                # Number of failed probes before declaring connection dead.
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
                log.debug("TCP keepalive enabled (idle=30s, interval=10s, count=3)")

            log.info("Connected to Mac")

            while True:
                # Read 4-byte length header with timeout.
                header = await asyncio.wait_for(
                    _read_exact(reader, 4),
                    timeout=120.0,
                )
                length = struct.unpack("!I", header)[0]

                if length == 0 or length > 1_000_000:
                    log.warning("Invalid message length: %d — dropping connection", length)
                    break

                # Read the JSON payload with timeout.
                payload = await asyncio.wait_for(
                    _read_exact(reader, length),
                    timeout=10.0,
                )
                event = json.loads(payload.decode("utf-8"))

                # Inject the event and check if the cursor hit the return edge.
                should_return, norm_y, return_edge = inject_event(event, keyboard_dev, mouse_dev)

                if should_return:
                    log.info("%s edge hit — sending returnControl to Mac", return_edge.title())
                    return_event = {
                        "type": "returnControl",
                        "normalizedX": 0,
                        "normalizedY": norm_y,
                        "edge": return_edge,
                    }
                    ret_payload = json.dumps(return_event).encode("utf-8")
                    ret_header = struct.pack("!I", len(ret_payload))
                    writer.write(ret_header + ret_payload)
                    await writer.drain()

        except asyncio.TimeoutError:
            log.warning("Connection timed out")
        except (ConnectionError, OSError, asyncio.IncompleteReadError) as exc:
            log.warning("Connection lost: %s", exc)
        except Exception as exc:
            log.error("Unexpected error: %s", exc, exc_info=True)
        finally:
            # Release any stuck keys/buttons before reconnecting.
            _release_all_keys(keyboard_dev, mouse_dev)
            # Close the writer cleanly.
            if writer is not None:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

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

# ── Resolution Detection with Retry ──────────────────────────────────


DISPLAY_READY_RETRIES = 10
DISPLAY_READY_DELAY = 3.0  # seconds


def _detect_resolution_with_retry():
    """Try to detect the screen resolution, retrying if the display isn't ready.

    On boot the systemd service may start before the graphical session is
    fully initialised.  In that case xdotool will fail and xrandr may
    report incorrect values.  We retry a few times with a short delay to
    give the display server time to come up.
    """
    for attempt in range(1, DISPLAY_READY_RETRIES + 1):
        detect_screen_resolution()

        # Quick sanity check: try xdotool to see if the display is reachable.
        if not USE_WAYLAND and shutil.which("xdotool"):
            try:
                result = subprocess.run(
                    ["xdotool", "getdisplaygeometry"],
                    capture_output=True, text=True, timeout=3,
                )
                if result.returncode == 0 and result.stdout.strip():
                    log.info("Display is ready (attempt %d/%d)", attempt, DISPLAY_READY_RETRIES)
                    return
            except Exception:
                pass

            # Display not reachable yet — wait and retry.
            if attempt < DISPLAY_READY_RETRIES:
                log.info(
                    "Display not ready yet — retrying in %.0fs (%d/%d)",
                    DISPLAY_READY_DELAY, attempt, DISPLAY_READY_RETRIES,
                )
                time.sleep(DISPLAY_READY_DELAY)
        else:
            # Wayland or no xdotool — accept whatever we got.
            return

    log.warning("Display did not become ready after %d attempts — using detected resolution %dx%d",
                DISPLAY_READY_RETRIES, SCREEN_WIDTH, SCREEN_HEIGHT)


# ── Main ──────────────────────────────────────────────────────────────

def main():
    log.info("MouseShare Linux Companion starting …")

    # 1. Check /dev/uinput
    check_uinput()

    # 2. Check mouse positioning tool
    check_mouse_tool()

    # 3. Detect display server
    detect_display_server()

    # 3b. Detect screen resolution (retry if display isn't ready yet)
    _detect_resolution_with_retry()

    # 4. Create virtual input devices (separate keyboard + mouse)
    keyboard_dev, mouse_dev = create_virtual_devices()

    # 5. Log connection target
    log.info("Will connect to Mac at %s:%d", MAC_IP, PORT)

    # 6. Run the async event loop
    try:
        asyncio.run(tcp_client(keyboard_dev, mouse_dev))
    except KeyboardInterrupt:
        log.info("Shutting down …")
    finally:
        keyboard_dev.close()
        mouse_dev.close()
        log.info("Virtual devices closed. Goodbye.")


if __name__ == "__main__":
    main()
