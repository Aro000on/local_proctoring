def blocked_shortcut(vk, ctrl=False, alt=False, shift=False, win=False):
    if vk in {0x5B, 0x5C}:
        return "Win"
    if win:
        name = {0x45: "E", 0x52: "R", 0x44: "D", 0x4D: "M", 0x09: "Tab"}.get(vk)
        if vk == 0x4C:
            return None
        return "Win+" + (name or "key")
    if vk == 0x2C:
        return "PrtScn"
    if alt and vk == 0x09:
        return "Ctrl+Alt+Tab" if ctrl else "Alt+Tab"
    if alt and vk == 0x73:
        return "Alt+F4"
    if alt and vk == 0x1B:
        return "Alt+Esc"
    if alt and vk == 0x20:
        return "Alt+Space"
    if ctrl and vk == 0x1B:
        return "Ctrl+Shift+Esc" if shift else "Ctrl+Esc"
    if ctrl and vk in {0x43, 0x56}:
        return "Ctrl+C" if vk == 0x43 else "Ctrl+V"
    if vk == 0x2D and (ctrl or shift):
        return "Ctrl/Shift+Insert"
    return None


def restricted_window(class_name, executable=""):
    if class_name in {"CabinetWClass", "ExploreWClass"}:
        return "File Explorer"
    if class_name == "TaskManagerWindow" or executable.replace("\\", "/").rsplit("/", 1)[-1].lower() == "taskmgr.exe":
        return "Task Manager"
    return None


class ForegroundInspector:
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.types = ctypes, wintypes
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user.GetForegroundWindow.restype = wintypes.HWND
        self.user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]

    def inspect(self):
        c, t = self.ctypes, self.types
        hwnd = self.user.GetForegroundWindow()
        if not hwnd:
            return None
        name = c.create_unicode_buffer(256)
        self.user.GetClassNameW(hwnd, name, len(name))
        pid = t.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, c.byref(pid))
        executable = ""
        handle = self.kernel.OpenProcess(0x1000, False, pid.value)
        if handle:
            try:
                path = c.create_unicode_buffer(32768)
                length = t.DWORD(len(path))
                if self.kernel.QueryFullProcessImageNameW(handle, 0, path, c.byref(length)):
                    executable = path.value
            finally:
                self.kernel.CloseHandle(handle)
        restricted = restricted_window(name.value, executable)
        return {"application": restricted, "pid": pid.value} if restricted else None
