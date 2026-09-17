from __future__ import annotations

import ctypes
import hashlib
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from .authority import AuthorityPolicy

_EFFECT_ACTION = {
    "local": "ui_local",
    "external_submit": "send_message",
    "publish": "publish",
    "purchase": "purchase",
    "credential": "credential_change",
    "irreversible": "format",
}


def _admin_token() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _effect_decision(effect: str) -> dict[str, object]:
    key = str(effect or "").strip().lower()
    action = _EFFECT_ACTION.get(key)
    if action is None:
        raise ValueError(f"desktop effect desconocido: {effect}")
    decision = AuthorityPolicy().evaluate(action)
    return decision.as_dict()


class DesktopObserver:
    @staticmethod
    def windows(*, limit: int = 80) -> list[dict[str, Any]]:
        if os.name != "nt":
            raise RuntimeError("desktop observation is Windows-only")
        import win32gui
        import win32process

        rows: list[dict[str, Any]] = []

        def callback(hwnd, _extra):
            if len(rows) >= max(1, min(200, int(limit))):
                return
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd).strip()
            if not title:
                return
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            rows.append({
                "hwnd": int(hwnd), "title": title, "pid": int(pid),
                "rect": [left, top, right, bottom],
            })

        win32gui.EnumWindows(callback, None)
        return rows

    @staticmethod
    def cursor() -> dict[str, int]:
        if os.name != "nt":
            raise RuntimeError("cursor observation is Windows-only")
        import win32api

        x, y = win32api.GetCursorPos()
        return {"x": int(x), "y": int(y)}

    @staticmethod
    def screenshot(directory: str | Path | None = None) -> dict[str, object]:
        if os.name != "nt":
            raise RuntimeError("screenshot capture is Windows-only")
        import win32api
        import win32con
        import win32gui
        import win32ui

        x = win32api.GetSystemMetrics(76)
        y = win32api.GetSystemMetrics(77)
        width = win32api.GetSystemMetrics(78)
        height = win32api.GetSystemMetrics(79)
        desktop = win32gui.GetDesktopWindow()
        source_dc = win32gui.GetWindowDC(desktop)
        src = win32ui.CreateDCFromHandle(source_dc)
        mem = src.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(src, width, height)
        mem.SelectObject(bitmap)
        mem.BitBlt((0, 0), (width, height), src, (x, y), win32con.SRCCOPY)
        target_root = Path(directory).expanduser() if directory else (
            Path.home() / ".lilith" / "desktop" / "screens"
        )
        target_root.mkdir(parents=True, exist_ok=True)
        path = target_root / f"screen-{int(time.time())}-{uuid.uuid4().hex[:8]}.bmp"
        try:
            bitmap.SaveBitmapFile(mem, str(path))
        finally:
            win32gui.DeleteObject(bitmap.GetHandle())
            mem.DeleteDC()
            src.DeleteDC()
            win32gui.ReleaseDC(desktop, source_dc)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return {
            "path": str(path),
            "sha256": digest,
            "mime_type": "image/bmp",
            "bounds": [int(x), int(y), int(width), int(height)],
        }


class DesktopActor:
    @staticmethod
    def _check(effect: str) -> dict[str, object]:
        decision = _effect_decision(effect)
        if not bool(decision["allowed"]):
            raise PermissionError(str(decision["reason"]))
        return decision

    @staticmethod
    def _foreground_matches(expected: str | None) -> bool:
        if not expected:
            return True
        import win32gui

        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        return expected.casefold() in title.casefold()

    @classmethod
    def focus(cls, title_contains: str, *, effect: str = "local") -> dict[str, object]:
        cls._check(effect)
        import win32con
        import win32gui
        import win32process

        needle = str(title_contains).strip().casefold()
        if not needle:
            raise ValueError("title_contains es requerido")
        matches = [row for row in DesktopObserver.windows() if needle in row["title"].casefold()]
        if not matches:
            raise LookupError(f"window not found: {title_contains}")
        hwnd = int(matches[0]["hwnd"])
        foreground = win32gui.GetForegroundWindow()
        current_tid = int(ctypes.windll.kernel32.GetCurrentThreadId())
        target_tid, _ = win32process.GetWindowThreadProcessId(hwnd)
        foreground_tid = 0
        if foreground:
            foreground_tid, _ = win32process.GetWindowThreadProcessId(foreground)
        attached: list[int] = []
        try:
            for tid in {int(target_tid), int(foreground_tid)}:
                if (
                    tid
                    and tid != current_tid
                    and ctypes.windll.user32.AttachThreadInput(current_tid, tid, True)
                ):
                    attached.append(tid)
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        finally:
            for tid in reversed(attached):
                ctypes.windll.user32.AttachThreadInput(current_tid, tid, False)
        if win32gui.GetForegroundWindow() != hwnd:
            raise RuntimeError("Windows refused foreground activation")
        return matches[0]

    @classmethod
    def click(
        cls,
        x: int,
        y: int,
        *,
        button: str = "left",
        clicks: int = 1,
        effect: str = "local",
        expected_window: str | None = None,
    ) -> dict[str, object]:
        decision = cls._check(effect)
        if not cls._foreground_matches(expected_window):
            raise RuntimeError("foreground window does not match expected_window")
        import win32api
        import win32con

        flags = {
            "left": (win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP),
            "right": (win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP),
            "middle": (win32con.MOUSEEVENTF_MIDDLEDOWN, win32con.MOUSEEVENTF_MIDDLEUP),
        }
        if button not in flags:
            raise ValueError("button debe ser left, right o middle")
        win32api.SetCursorPos((int(x), int(y)))
        down, up = flags[button]
        for _ in range(max(1, min(3, int(clicks)))):
            win32api.mouse_event(down, 0, 0, 0, 0)
            win32api.mouse_event(up, 0, 0, 0, 0)
            time.sleep(0.08)
        return {"x": int(x), "y": int(y), "button": button, "decision": decision}

    @classmethod
    def type_text(
        cls,
        text: str,
        *,
        effect: str = "local",
        expected_window: str | None = None,
    ) -> dict[str, object]:
        decision = cls._check(effect)
        if not cls._foreground_matches(expected_window):
            raise RuntimeError("foreground window does not match expected_window")
        from ctypes import wintypes

        extra_t = ctypes.c_size_t

        class MouseInput(ctypes.Structure):
            _fields_ = [
                ("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", extra_t),
            ]

        class KeyInput(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", extra_t),
            ]

        class HardwareInput(ctypes.Structure):
            _fields_ = [
                ("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD),
            ]

        class InputData(ctypes.Union):
            _fields_ = [("mi", MouseInput), ("ki", KeyInput), ("hi", HardwareInput)]

        class Input(ctypes.Structure):
            _anonymous_ = ("data",)
            _fields_ = [("type", wintypes.DWORD), ("data", InputData)]

        send_input = ctypes.windll.user32.SendInput
        flags_unicode, flags_keyup = 0x0004, 0x0002
        units = text.encode("utf-16-le")
        sent = 0
        for offset in range(0, len(units), 2):
            unit = int.from_bytes(units[offset:offset + 2], "little")
            for flags in (flags_unicode, flags_unicode | flags_keyup):
                event = Input()
                event.type = 1
                event.ki = KeyInput(0, unit, flags, 0, 0)
                if send_input(1, ctypes.byref(event), ctypes.sizeof(Input)) != 1:
                    raise OSError(ctypes.get_last_error(), "SendInput failed")
            sent += 1
        return {"characters": sent, "decision": decision}

    @classmethod
    def hotkey(
        cls,
        keys: list[str],
        *,
        effect: str = "local",
        expected_window: str | None = None,
    ) -> dict[str, object]:
        decision = cls._check(effect)
        if not cls._foreground_matches(expected_window):
            raise RuntimeError("foreground window does not match expected_window")
        if not keys or len(keys) > 5:
            raise ValueError("keys debe contener entre 1 y 5 teclas")
        import win32api
        import win32con
        mapping = {
            "ctrl": win32con.VK_CONTROL, "shift": win32con.VK_SHIFT,
            "alt": win32con.VK_MENU, "win": win32con.VK_LWIN,
            "enter": win32con.VK_RETURN, "tab": win32con.VK_TAB,
            "esc": win32con.VK_ESCAPE, "space": win32con.VK_SPACE,
            "backspace": win32con.VK_BACK, "delete": win32con.VK_DELETE,
            "left": win32con.VK_LEFT, "right": win32con.VK_RIGHT,
            "up": win32con.VK_UP, "down": win32con.VK_DOWN,
            "home": win32con.VK_HOME, "end": win32con.VK_END,
        }
        resolved: list[int] = []
        for raw in keys:
            key = str(raw).strip().lower()
            if key in mapping:
                resolved.append(mapping[key])
            elif len(key) == 1 and key.isalnum():
                resolved.append(ord(key.upper()))
            elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
                resolved.append(win32con.VK_F1 + int(key[1:]) - 1)
            else:
                raise ValueError(f"tecla no soportada: {raw}")
        for vk in resolved:
            win32api.keybd_event(vk, 0, 0, 0)
        for vk in reversed(resolved):
            win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
        return {"keys": list(keys), "decision": decision}


class AdminCommandBroker:
    """Execute PowerShell after declared + inferred sovereign-local authority checks."""

    _COMMAND_ACTIONS = (
        (("clear-disk", "format-volume", "remove-partition", "diskpart", "clean all"), "wipe_disk"),
        (("restart-computer", "stop-computer", "shutdown.exe", "bcdedit"), "reboot"),
        (("new-netfirewallrule", "set-netfirewallrule", "remove-netfirewallrule", "netsh advfirewall"), "firewall_change"),
        (("new-service", "set-service", "remove-service", "sc.exe create", "sc.exe delete"), "service_change"),
        (("set-itemproperty", "new-itemproperty", "remove-itemproperty", "reg.exe add", "reg.exe delete"), "registry_write"),
        (("winget install", "choco install", "msiexec", "install-package"), "system_install"),
        (("cmdkey", "set-localuser", "new-localuser", "net user"), "credential_change"),
    )

    @classmethod
    def infer_action(cls, command: str, declared: str) -> str:
        lowered = command.casefold()
        for needles, action in cls._COMMAND_ACTIONS:
            if any(token in lowered for token in needles):
                return action
        return str(declared).strip().lower()

    @classmethod
    def run(
        cls,
        command: str,
        *,
        action: str,
        cwd: str,
        timeout: int = 120,
        recovery_available: bool = False,
    ) -> dict[str, object]:
        command = str(command).strip()
        if not command:
            raise ValueError("command es requerido")
        root = Path(cwd).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"cwd no existe: {root}")
        inferred_action = cls.infer_action(command, action)
        decision = AuthorityPolicy().evaluate(
            inferred_action,
            path=str(root),
            recovery_available=recovery_available,
        )
        if not decision.allowed:
            return {
                "executed": False,
                "authority": decision.as_dict(),
                "declared_action": str(action).strip().lower(),
                "effective_action": inferred_action,
            }
        if decision.requires_elevation and not _admin_token():
            return {
                "executed": False,
                "authority": decision.as_dict(),
                "declared_action": str(action).strip().lower(),
                "effective_action": inferred_action,
                "error": "Windows administrator token required",
            }
        destructive_tokens = (
            "clear-disk", "format-volume", "remove-partition",
            "delete volume", "clean all", "wipe_disk", "repartition",
        )
        if any(token in command.casefold() for token in destructive_tokens):
            blocked = AuthorityPolicy().evaluate("wipe_disk", path=str(root))
            return {"executed": False, "authority": blocked.as_dict()}
        try:
            result = subprocess.run(
                [
                    "powershell.exe", "-NoProfile", "-NonInteractive",
                    "-ExecutionPolicy", "Bypass", "-Command", command,
                ],
                cwd=str(root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(1, min(3600, int(timeout))),
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "executed": True, "timeout": True,
                "stdout": str(exc.stdout or "")[-8000:],
                "stderr": str(exc.stderr or "")[-8000:],
                "authority": decision.as_dict(),
            }
        return {
            "executed": True,
            "exit_code": int(result.returncode),
            "stdout": (result.stdout or "")[-8000:],
            "stderr": (result.stderr or "")[-8000:],
            "authority": decision.as_dict(),
            "declared_action": str(action).strip().lower(),
            "effective_action": inferred_action,
            "admin_token": _admin_token(),
        }
