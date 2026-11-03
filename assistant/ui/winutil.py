"""Small Windows helpers: bring a window to the front from a global hotkey, dark title bars."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QWidget


# ----------------------------------------------------------------------------
# Focus
# ----------------------------------------------------------------------------

def force_foreground(widget: QWidget) -> None:
    """Windows blocks background apps from stealing focus. Attaching to the foreground
    thread's input for a moment is the standard, allowed way to take focus after a hotkey."""
    widget.show()
    widget.raise_()
    widget.activateWindow()
    if sys.platform != "win32":
        return
    import ctypes

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    hwnd = int(widget.winId())
    fg = user32.GetForegroundWindow()
    if fg == hwnd:
        return
    fg_thread = user32.GetWindowThreadProcessId(fg, None)
    me = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(fg_thread, me, True)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    user32.AttachThreadInput(fg_thread, me, False)


# ----------------------------------------------------------------------------
# Title bar colour
# ----------------------------------------------------------------------------

def dark_title_bar(widget: QWidget, dark: bool = True) -> None:
    """Title bar colour for normal windows; dark=False gives a light one (the Frost settings)."""
    if sys.platform != "win32":
        return
    import ctypes

    value = ctypes.c_int(1 if dark else 0)
    for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (20 on new builds, 19 on older Windows 10)
        if ctypes.windll.dwmapi.DwmSetWindowAttribute(int(widget.winId()), attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
            break
