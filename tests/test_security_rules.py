import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from security_rules import blocked_shortcut, restricted_window


class SecurityRulesTests(unittest.TestCase):
    def test_shortcuts(self):
        self.assertEqual(blocked_shortcut(0x73, alt=True), "Alt+F4")
        self.assertEqual(blocked_shortcut(0x1B, ctrl=True, shift=True), "Ctrl+Shift+Esc")
        self.assertEqual(blocked_shortcut(0x1B, alt=True), "Alt+Esc")
        self.assertEqual(blocked_shortcut(0x20, alt=True), "Alt+Space")
        self.assertEqual(blocked_shortcut(0x45, win=True), "Win+E")
        self.assertEqual(blocked_shortcut(0x52, win=True), "Win+R")
        self.assertEqual(blocked_shortcut(0x43, ctrl=True), "Ctrl+C")

    def test_normal_typing_and_secure_shortcuts(self):
        self.assertIsNone(blocked_shortcut(0x43))
        self.assertIsNone(blocked_shortcut(0x73))
        self.assertIsNone(blocked_shortcut(0x2E, ctrl=True, alt=True))
        self.assertIsNone(blocked_shortcut(0x4C, win=True))
        self.assertIsNone(blocked_shortcut(0x51, ctrl=True, shift=True))

    def test_shell_is_not_file_explorer_window(self):
        self.assertIsNone(restricted_window("Shell_TrayWnd", r"C:\Windows\explorer.exe"))
        self.assertIsNone(restricted_window("Progman", r"C:\Windows\explorer.exe"))
        self.assertEqual(restricted_window("CabinetWClass", r"C:\Windows\explorer.exe"), "File Explorer")

    def test_task_manager(self):
        self.assertEqual(restricted_window("TaskManagerWindow"), "Task Manager")
        self.assertEqual(restricted_window("other", r"C:\Windows\System32\Taskmgr.exe"), "Task Manager")
        self.assertIsNone(restricted_window("other", "not-taskmgr.exe"))


if __name__ == "__main__":
    unittest.main()
