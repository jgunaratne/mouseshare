#!/usr/bin/env python3
"""
MouseShare — Windows Companion Client

Receives keyboard and mouse events from the macOS MouseShare app over a
USB-C TCP connection and injects them into Windows via the SendInput API
so they control the machine as if real hardware were attached.

Runs in the Windows system tray with status icon and right-click menu.

Dependencies:
    pip install -r requirements.txt

Usage:
    pythonw mouseshare.py      # No console window (recommended)
    python  mouseshare.py      # With console for debugging
"""

import asyncio
import ctypes
import ctypes.wintypes
import json
import logging
import struct
import sys
import threading

# ── Configuration ──────────────────────────────────────────────────────

MAC_IP = "192.168.100.1"
PORT = 9876

# Pixels from the left edge before we hand control back to the Mac.
EDGE_THRESHOLD = 5

# Seconds to wait before retrying after a dropped connection.
RECONNECT_DELAY = 2.0

# ── Logging ────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mouseshare")

# ── Win32 API Constants ────────────────────────────────────────────────

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001

SM_CXSCREEN = 0
SM_CYSCREEN = 1

WHEEL_DELTA = 120

# ── Win32 API Structures ──────────────────────────────────────────────

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.wintypes.LONG),
        ("dy", ctypes.wintypes.LONG),
        ("mouseData", ctypes.wintypes.DWORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.wintypes.WORD),
        ("wScan", ctypes.wintypes.WORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

class _INPUTunion(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
    ]

class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("union", _INPUTunion),
    ]

# ── Win32 API Functions ───────────────────────────────────────────────

user32 = ctypes.windll.user32
SendInput = user32.SendInput
SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
SendInput.restype = ctypes.c_uint
SetCursorPos = user32.SetCursorPos
GetSystemMetrics = user32.GetSystemMetrics

# ── Screen Resolution ─────────────────────────────────────────────────

SCREEN_WIDTH = GetSystemMetrics(SM_CXSCREEN)
SCREEN_HEIGHT = GetSystemMetrics(SM_CYSCREEN)

# ── Mac HID → Windows Virtual Keycode Mapping ─────────────────────────

VK_BACK = 0x08; VK_TAB = 0x09; VK_RETURN = 0x0D
VK_CAPITAL = 0x14; VK_ESCAPE = 0x1B; VK_SPACE = 0x20
VK_PRIOR = 0x21; VK_NEXT = 0x22; VK_END = 0x23; VK_HOME = 0x24
VK_LEFT = 0x25; VK_UP = 0x26; VK_RIGHT = 0x27; VK_DOWN = 0x28
VK_INSERT = 0x2D; VK_DELETE = 0x2E
VK_LWIN = 0x5B; VK_RWIN = 0x5C
VK_NUMPAD0 = 0x60; VK_NUMPAD1 = 0x61; VK_NUMPAD2 = 0x62
VK_NUMPAD3 = 0x63; VK_NUMPAD4 = 0x64; VK_NUMPAD5 = 0x65
VK_NUMPAD6 = 0x66; VK_NUMPAD7 = 0x67; VK_NUMPAD8 = 0x68
VK_NUMPAD9 = 0x69; VK_MULTIPLY = 0x6A; VK_ADD = 0x6B
VK_SUBTRACT = 0x6D; VK_DECIMAL = 0x6E; VK_DIVIDE = 0x6F
VK_F1 = 0x70; VK_F2 = 0x71; VK_F3 = 0x72; VK_F4 = 0x73
VK_F5 = 0x74; VK_F6 = 0x75; VK_F7 = 0x76; VK_F8 = 0x77
VK_F9 = 0x78; VK_F10 = 0x79; VK_F11 = 0x7A; VK_F12 = 0x7B
VK_NUMLOCK = 0x90
VK_LSHIFT = 0xA0; VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2; VK_RCONTROL = 0xA3
VK_LMENU = 0xA4; VK_RMENU = 0xA5
VK_OEM_1 = 0xBA; VK_OEM_PLUS = 0xBB; VK_OEM_COMMA = 0xBC
VK_OEM_MINUS = 0xBD; VK_OEM_PERIOD = 0xBE; VK_OEM_2 = 0xBF
VK_OEM_3 = 0xC0; VK_OEM_4 = 0xDB; VK_OEM_5 = 0xDC
VK_OEM_6 = 0xDD; VK_OEM_7 = 0xDE

MAC_TO_WIN_KEYCODE: dict[int, int] = {
    # Letters
    0: ord('A'), 11: ord('B'), 8: ord('C'), 2: ord('D'), 14: ord('E'),
    3: ord('F'), 5: ord('G'), 4: ord('H'), 34: ord('I'), 38: ord('J'),
    40: ord('K'), 37: ord('L'), 46: ord('M'), 45: ord('N'), 31: ord('O'),
    35: ord('P'), 12: ord('Q'), 15: ord('R'), 1: ord('S'), 17: ord('T'),
    32: ord('U'), 9: ord('V'), 13: ord('W'), 7: ord('X'), 16: ord('Y'),
    6: ord('Z'),
    # Number row
    29: ord('0'), 18: ord('1'), 19: ord('2'), 20: ord('3'), 21: ord('4'),
    23: ord('5'), 22: ord('6'), 26: ord('7'), 28: ord('8'), 25: ord('9'),
    # Punctuation
    49: VK_SPACE, 36: VK_RETURN, 51: VK_BACK, 48: VK_TAB, 53: VK_ESCAPE,
    43: VK_OEM_COMMA, 47: VK_OEM_PERIOD, 44: VK_OEM_2, 41: VK_OEM_1,
    39: VK_OEM_7, 33: VK_OEM_4, 30: VK_OEM_6, 42: VK_OEM_5,
    50: VK_OEM_3, 27: VK_OEM_MINUS, 24: VK_OEM_PLUS,
    # Modifiers
    56: VK_LSHIFT, 60: VK_RSHIFT, 59: VK_LCONTROL, 62: VK_RCONTROL,
    58: VK_LMENU, 61: VK_RMENU, 55: VK_LWIN, 54: VK_RWIN, 57: VK_CAPITAL,
    # Function keys
    122: VK_F1, 120: VK_F2, 99: VK_F3, 118: VK_F4, 96: VK_F5,
    97: VK_F6, 98: VK_F7, 100: VK_F8, 101: VK_F9, 109: VK_F10,
    103: VK_F11, 111: VK_F12,
    # Navigation
    123: VK_LEFT, 124: VK_RIGHT, 125: VK_DOWN, 126: VK_UP,
    115: VK_HOME, 119: VK_END, 116: VK_PRIOR, 121: VK_NEXT,
    117: VK_DELETE, 114: VK_INSERT,
    # Numpad
    71: VK_NUMLOCK, 82: VK_NUMPAD0, 83: VK_NUMPAD1, 84: VK_NUMPAD2,
    85: VK_NUMPAD3, 86: VK_NUMPAD4, 87: VK_NUMPAD5, 88: VK_NUMPAD6,
    89: VK_NUMPAD7, 91: VK_NUMPAD8, 92: VK_NUMPAD9,
    65: VK_DECIMAL, 69: VK_ADD, 78: VK_SUBTRACT, 67: VK_MULTIPLY,
    75: VK_DIVIDE, 76: VK_RETURN,
}

EXTENDED_KEYS = {
    VK_INSERT, VK_DELETE, VK_HOME, VK_END, VK_PRIOR, VK_NEXT,
    VK_LEFT, VK_RIGHT, VK_UP, VK_DOWN,
    VK_LWIN, VK_RWIN, VK_RCONTROL, VK_RMENU, VK_DIVIDE,
}

# ── Input Injection ───────────────────────────────────────────────────

_pressed_keys: set[int] = set()
_connection_status = "Disconnected"


def _send_mouse_input(flags: int, dx: int = 0, dy: int = 0, data: int = 0):
    mi = MOUSEINPUT(
        dx=dx, dy=dy,
        mouseData=ctypes.wintypes.DWORD(data & 0xFFFFFFFF),
        dwFlags=ctypes.wintypes.DWORD(flags),
        time=0, dwExtraInfo=None,
    )
    inp = INPUT(type=INPUT_MOUSE)
    inp.union.mi = mi
    SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def _send_key_input(vk: int, down: bool):
    flags = 0
    if not down:
        flags |= KEYEVENTF_KEYUP
    if vk in EXTENDED_KEYS:
        flags |= KEYEVENTF_EXTENDEDKEY
    ki = KEYBDINPUT(
        wVk=ctypes.wintypes.WORD(vk), wScan=0,
        dwFlags=ctypes.wintypes.DWORD(flags),
        time=0, dwExtraInfo=None,
    )
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.union.ki = ki
    SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def _release_all_keys():
    for vk in list(_pressed_keys):
        _send_key_input(vk, down=False)
    # Also release mouse buttons
    if -1 in _pressed_keys:
        _send_mouse_input(MOUSEEVENTF_LEFTUP)
    if -2 in _pressed_keys:
        _send_mouse_input(MOUSEEVENTF_RIGHTUP)
    if _pressed_keys:
        log.info("Released %d stuck key(s)/button(s)", len(_pressed_keys))
    _pressed_keys.clear()


def inject_event(event: dict) -> tuple[bool, float]:
    event_type = event.get("type")

    if event_type == "mouseMove":
        nx = event.get("normalizedX", 0)
        ny = event.get("normalizedY", 0)
        x = int(nx * SCREEN_WIDTH)
        y = int(ny * SCREEN_HEIGHT)
        x = max(0, min(x, SCREEN_WIDTH - 1))
        y = max(0, min(y, SCREEN_HEIGHT - 1))
        SetCursorPos(x, y)
        if x <= EDGE_THRESHOLD:
            norm_y = y / SCREEN_HEIGHT if SCREEN_HEIGHT else 0
            return (True, norm_y)

    elif event_type == "leftMouseDown":
        _pressed_keys.add(-1)
        _send_mouse_input(MOUSEEVENTF_LEFTDOWN)

    elif event_type == "leftMouseUp":
        _pressed_keys.discard(-1)
        _send_mouse_input(MOUSEEVENTF_LEFTUP)

    elif event_type == "rightMouseDown":
        _pressed_keys.add(-2)
        _send_mouse_input(MOUSEEVENTF_RIGHTDOWN)

    elif event_type == "rightMouseUp":
        _pressed_keys.discard(-2)
        _send_mouse_input(MOUSEEVENTF_RIGHTUP)

    elif event_type in ("keyDown", "keyUp"):
        mac_code = event.get("keyCode")
        if mac_code is None:
            return (False, 0)
        vk = MAC_TO_WIN_KEYCODE.get(mac_code)
        if vk is None:
            log.warning("Unknown Mac keycode %d", mac_code)
            return (False, 0)
        down = event_type == "keyDown"
        if down:
            _pressed_keys.add(vk)
        else:
            _pressed_keys.discard(vk)
        _send_key_input(vk, down)

    elif event_type == "scrollWheel":
        dx = event.get("scrollDeltaX", 0)
        dy = event.get("scrollDeltaY", 0)
        if dy:
            _send_mouse_input(MOUSEEVENTF_WHEEL, data=int(dy * WHEEL_DELTA))
        if dx:
            _send_mouse_input(MOUSEEVENTF_HWHEEL, data=int(dx * WHEEL_DELTA))

    elif event_type == "returnControl":
        _release_all_keys()
        log.info("Mac sent returnControl — all keys released")

    return (False, 0)


# ── TCP Client ────────────────────────────────────────────────────────

async def _read_exact(reader: asyncio.StreamReader, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = await reader.read(n - len(data))
        if not chunk:
            raise ConnectionError("Connection closed by remote")
        data += chunk
    return data


async def tcp_client(tray_update=None):
    """Connect to the Mac and process events. Reconnects on failure."""
    global _connection_status

    while True:
        writer = None
        try:
            _connection_status = "Connecting…"
            if tray_update:
                tray_update()

            log.info("Connecting to Mac at %s:%d …", MAC_IP, PORT)
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(MAC_IP, PORT),
                timeout=5.0,
            )
            log.info("Connected to Mac")
            _connection_status = "Connected"
            if tray_update:
                tray_update()

            while True:
                header = await asyncio.wait_for(
                    _read_exact(reader, 4), timeout=30.0,
                )
                length = struct.unpack("!I", header)[0]
                if length == 0 or length > 1_000_000:
                    log.warning("Invalid message length: %d", length)
                    break

                payload = await asyncio.wait_for(
                    _read_exact(reader, length), timeout=10.0,
                )
                event = json.loads(payload.decode("utf-8"))
                should_return, norm_y = inject_event(event)

                if should_return:
                    log.info("Left edge hit — sending returnControl")
                    ret = json.dumps({
                        "type": "returnControl",
                        "normalizedX": 0, "normalizedY": norm_y, "edge": "left",
                    }).encode("utf-8")
                    writer.write(struct.pack("!I", len(ret)) + ret)
                    await writer.drain()

        except asyncio.TimeoutError:
            log.warning("Connection timed out")
        except (ConnectionError, OSError, asyncio.IncompleteReadError) as exc:
            log.warning("Connection lost: %s", exc)
        except Exception as exc:
            log.error("Unexpected error: %s", exc, exc_info=True)
        finally:
            _release_all_keys()
            _connection_status = "Disconnected"
            if tray_update:
                tray_update()
            if writer is not None:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

        log.info("Reconnecting in %.0f seconds …", RECONNECT_DELAY)
        await asyncio.sleep(RECONNECT_DELAY)


# ── System Tray ───────────────────────────────────────────────────────

def _create_tray_icon():
    """Create and return a pystray Icon for the system tray."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        log.warning("pystray/Pillow not installed — running without tray icon.")
        log.warning("Install with: pip install pystray Pillow")
        return None

    def _make_icon(color="green"):
        """Draw a simple colored circle icon."""
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        colors = {
            "green": (76, 175, 80),
            "yellow": (255, 193, 7),
            "red": (158, 158, 158),
        }
        fill = colors.get(color, colors["red"])
        draw.ellipse([8, 8, 56, 56], fill=(*fill, 255))
        return img

    def on_quit(icon, item):
        icon.stop()
        # Signal the asyncio loop to stop
        sys.exit(0)

    def _update_icon(icon):
        """Update icon color and tooltip based on connection status."""
        if _connection_status == "Connected":
            icon.icon = _make_icon("green")
        elif _connection_status == "Connecting…":
            icon.icon = _make_icon("yellow")
        else:
            icon.icon = _make_icon("red")
        icon.title = f"MouseShare — {_connection_status}"

    menu = pystray.Menu(
        pystray.MenuItem(
            lambda _: f"Status: {_connection_status}",
            action=None, enabled=False,
        ),
        pystray.MenuItem(
            lambda _: f"Mac: {MAC_IP}:{PORT}",
            action=None, enabled=False,
        ),
        pystray.MenuItem(
            lambda _: f"Screen: {SCREEN_WIDTH}×{SCREEN_HEIGHT}",
            action=None, enabled=False,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit MouseShare", on_quit),
    )

    icon = pystray.Icon(
        "MouseShare",
        _make_icon("red"),
        "MouseShare — Disconnected",
        menu,
    )

    return icon, _update_icon


def _install_autostart():
    """Add a shortcut to the Windows Startup folder."""
    import os
    import winreg

    script_path = os.path.abspath(__file__)
    pythonw = sys.executable.replace("python.exe", "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, "MouseShare", 0, winreg.REG_SZ, f'"{pythonw}" "{script_path}"')
        winreg.CloseKey(key)
        log.info("Auto-start registered in Windows Registry.")
    except Exception as exc:
        log.error("Failed to set auto-start: %s", exc)


def _remove_autostart():
    """Remove the auto-start registry entry."""
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, "MouseShare")
        winreg.CloseKey(key)
        log.info("Auto-start removed from Windows Registry.")
    except FileNotFoundError:
        log.info("Auto-start was not registered.")
    except Exception as exc:
        log.error("Failed to remove auto-start: %s", exc)


# ── Main ──────────────────────────────────────────────────────────────

def main():
    log.info("MouseShare Windows Companion starting …")
    log.info("Screen resolution: %dx%d", SCREEN_WIDTH, SCREEN_HEIGHT)
    log.info("Will connect to Mac at %s:%d", MAC_IP, PORT)

    # Handle --install / --uninstall flags
    if "--install" in sys.argv:
        _install_autostart()
        print("✅ MouseShare will start automatically on boot.")
        print(f"   Remove with: python {sys.argv[0]} --uninstall")
        return
    if "--uninstall" in sys.argv:
        _remove_autostart()
        print("✅ MouseShare auto-start removed.")
        return

    # Try to create tray icon
    tray_result = _create_tray_icon()

    if tray_result:
        icon, update_fn = tray_result

        def run_async_client():
            """Run the TCP client in a background thread."""
            def tray_update():
                try:
                    update_fn(icon)
                except Exception:
                    pass
            try:
                asyncio.run(tcp_client(tray_update=tray_update))
            except Exception as exc:
                log.error("Client thread error: %s", exc)

        client_thread = threading.Thread(target=run_async_client, daemon=True)
        client_thread.start()

        # icon.run() blocks on the main thread (required by Windows)
        icon.run()
    else:
        # No tray — run directly
        try:
            asyncio.run(tcp_client())
        except KeyboardInterrupt:
            log.info("Shutting down …")
        finally:
            _release_all_keys()
            log.info("Goodbye.")


if __name__ == "__main__":
    main()
