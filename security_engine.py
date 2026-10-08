import sys
import time

from PyQt6.QtCore import QObject, QEvent, QTimer, Qt
from PyQt6.QtGui import QClipboard
from PyQt6.QtWidgets import QApplication
from screeninfo import get_monitors
from security_rules import blocked_shortcut, ForegroundInspector


def monitor_inventory():
    screens = get_monitors()
    if not screens:
        raise RuntimeError("Не удалось определить подключённые мониторы")
    return [{"width": s.width, "height": s.height, "x": s.x, "y": s.y}
            for s in screens]


class SecurityEngine(QObject):
    def __init__(self, bus, parent=None):
        super().__init__(parent)
        self.bus = bus
        self.listener = None
        self.active = False
        self.pressed = set()
        self.suppressed = set()
        self.last_warning = {}
        self.clipboard_timer = QTimer(self)
        self.clipboard_timer.setInterval(500)
        self.clipboard_timer.timeout.connect(self.clear_clipboard)
        self.monitor_timer = QTimer(self)
        self.monitor_timer.setInterval(2000)
        self.monitor_timer.timeout.connect(self.check_monitors)
        self.global_hook = sys.platform == "win32"
        self.inspector = None
        self.window_timer = QTimer(self)
        self.window_timer.setInterval(400)
        self.window_timer.timeout.connect(self.check_system_windows)
        self.last_system_warning = 0.0

    def capabilities(self):
        return {"keyboard": "Windows selective global hook" if self.global_hook else "Qt window only",
                "traffic": "Qt WebEngine HTTP(S) origins only; no system DNS/firewall",
                "clipboard": "Qt clear every 500 ms; no clipboard history control",
                "monitors": "screeninfo before start and every 2 s",
                "system_windows": "foreground Explorer/Task Manager detection, best-effort focus recovery; no launch prevention",
                "secure_shortcuts": "Ctrl+Alt+Del and Win+L cannot be guaranteed blocked by user-mode hook"}

    def warn(self, combo):
        now = time.monotonic()
        if now - self.last_warning.get(combo, -1e9) > 1:
            self.last_warning[combo] = now
            self.bus.emit("HOTKEY_BLOCKED", {"combination": combo})

    def win32_filter(self, msg, data):
        vk = data.vkCode
        down = msg in (0x100, 0x104)
        up = msg in (0x101, 0x105)
        if not self.active or not (down or up):
            return True
        if up:
            self.pressed.discard(vk)
            if vk in self.suppressed:
                self.suppressed.discard(vk)
                self.listener.suppress_event()
            return True
        self.pressed.add(vk)
        ctrl = bool(self.pressed & {0x11, 0xA2, 0xA3})
        alt = bool(self.pressed & {0x12, 0xA4, 0xA5}) or bool(data.flags & 0x20)
        shift = bool(self.pressed & {0x10, 0xA0, 0xA1})
        if ctrl and shift and vk == 0x51:
            self.bus.emit("EMERGENCY_EXIT")
            return True
        win = bool(self.pressed & {0x5B, 0x5C})
        if (ctrl and alt and vk == 0x2E) or (win and vk == 0x4C):
            self.bus.emit("SECURE_SHORTCUT_ATTEMPT", {"combination": "Ctrl+Alt+Del" if vk == 0x2E else "Win+L", "blocked": False})
        combo = blocked_shortcut(vk, ctrl, alt, shift, win)
        if combo:
            self.suppressed.add(vk)
            self.warn(combo)
            self.listener.suppress_event()
        return True

    def eventFilter(self, watched, event):
        if not self.active or event.type() != QEvent.Type.KeyPress:
            return False
        key, modifiers = event.key(), event.modifiers()
        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        if ctrl and shift and key == Qt.Key.Key_Q:
            self.bus.emit("EMERGENCY_EXIT")
            return True
        combo = None
        if key in (Qt.Key.Key_Meta, Qt.Key.Key_Super_L, Qt.Key.Key_Super_R):
            combo = "Win/Meta"
        elif key == Qt.Key.Key_Print:
            combo = "PrtScn"
        elif alt and key == Qt.Key.Key_Tab:
            combo = "Alt+Tab"
        elif ctrl and key in (Qt.Key.Key_C, Qt.Key.Key_V):
            combo = "Ctrl+C" if key == Qt.Key.Key_C else "Ctrl+V"
        elif key == Qt.Key.Key_Insert and (ctrl or shift):
            combo = "Ctrl/Shift+Insert"
        elif alt and key == Qt.Key.Key_F4:
            combo = "Alt+F4"
        elif alt and key == Qt.Key.Key_Escape:
            combo = "Alt+Esc"
        elif alt and key == Qt.Key.Key_Space:
            combo = "Alt+Space"
        elif ctrl and key == Qt.Key.Key_Escape:
            combo = "Ctrl+Shift+Esc" if shift else "Ctrl+Esc"
        if combo:
            self.warn(combo)
            return True
        return False

    def check_system_windows(self):
        if not self.active or self.inspector is None:
            return
        try:
            result = self.inspector.inspect()
            now = time.monotonic()
            if result and now - self.last_system_warning >= 1:
                self.last_system_warning = now
                self.bus.emit("SYSTEM_WINDOW_DETECTED", result)
        except OSError as exc:
            self.bus.emit("ENGINE_ERROR", {"component": "window_monitor", "message": str(exc)})

    def clear_clipboard(self, warn=True):
        clipboard = QApplication.clipboard()
        modes = [QClipboard.Mode.Clipboard]
        if clipboard.supportsSelection():
            modes.append(QClipboard.Mode.Selection)
        if clipboard.supportsFindBuffer():
            modes.append(QClipboard.Mode.FindBuffer)
        cleared = False
        for mode in modes:
            mime = clipboard.mimeData(mode)
            if mime is not None and mime.formats():
                clipboard.clear(mode)
                cleared = True
        if cleared and warn:
            self.bus.emit("CLIPBOARD_CLEARED")

    def check_monitors(self):
        try:
            if self.global_hook and (self.listener is None or not self.listener.is_alive()):
                self.bus.emit("ENGINE_ERROR", {"component": "keyboard", "message": "Windows hook остановился"})
                return
            monitors = monitor_inventory()
            if len(monitors) > 1:
                self.bus.emit("MULTIPLE_MONITORS", {"monitors": monitors})
        except Exception as exc:
            self.bus.emit("ENGINE_ERROR", {"component": "monitors", "message": str(exc)})

    def start(self):
        monitors = monitor_inventory()
        if len(monitors) != 1:
            raise RuntimeError(f"Для теста нужен один экран. Обнаружено: {len(monitors)}")
        self.active = True
        try:
            if self.global_hook:
                from pynput import keyboard
                self.listener = keyboard.Listener(win32_event_filter=self.win32_filter)
                self.listener.start()
                self.listener.wait()
                if not self.listener.is_alive():
                    raise RuntimeError("Не удалось установить Windows keyboard hook")
                self.inspector = ForegroundInspector()
                self.window_timer.start()
            QApplication.instance().installEventFilter(self)
            self.clear_clipboard(warn=False)
            self.clipboard_timer.start()
            self.monitor_timer.start()
        except Exception:
            self.stop()
            raise
        return monitors

    def stop(self):
        self.active = False
        self.clipboard_timer.stop()
        self.monitor_timer.stop()
        self.window_timer.stop()
        self.inspector = None
        app = QApplication.instance()
        if app:
            app.removeEventFilter(self)
        if self.listener:
            self.listener.stop()
            self.listener.join(timeout=2)
            self.listener = None
        self.pressed.clear()
        self.suppressed.clear()
