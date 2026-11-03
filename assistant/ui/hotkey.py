"""Global hotkey with tap vs hold. pynput watches the keyboard on its own thread and we only
emit Qt signals from there (queued to the UI thread). The keys still reach the focused app;
Ctrl+Shift+Space does nothing in most apps."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

# ----------------------------------------------------------------------------
# Key names
#   A global hotkey via pynput: tap to open the panel, hold to talk.
# ----------------------------------------------------------------------------

ALIASES = {"control": "ctrl", "ctl": "ctrl", "option": "alt", "cmd": "win", "super": "win", "windows": "win"}


def parse_combo(combo: str) -> frozenset[str]:
    keys = [ALIASES.get(k.strip().lower(), k.strip().lower()) for k in combo.split("+") if k.strip()]
    return frozenset(keys)


def pretty(combo: str) -> str:
    return "+".join(k.capitalize() if len(k) > 1 else k.upper() for k in combo.split("+"))


def canonical(key) -> str | None:
    """Map a pynput key to our names: ctrl, shift, alt, win, space, enter, a, b, f1…"""
    from pynput import keyboard

    if isinstance(key, keyboard.Key):
        name = key.name  # ctrl_l, shift_r, alt_gr, cmd, space…
        for base in ("ctrl", "shift", "alt", "cmd"):
            if name.startswith(base):
                return "win" if base == "cmd" else base
        return name
    vk = getattr(key, "vk", None)
    if vk is not None and (0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A):
        return chr(vk).lower()  # with Ctrl held, .char is a control code, so use the virtual key
    char = getattr(key, "char", None)
    return char.lower() if char else None


# ----------------------------------------------------------------------------
# The keyboard hook
#   pynput calls from its own thread; Qt signals carry presses to the UI thread.
# ----------------------------------------------------------------------------

class Hotkey(QObject):
    pressed = Signal()  # all keys of the combo are down
    released = Signal()  # any key of the combo went up after that

    def __init__(self, combo: str):
        super().__init__()
        self.want = parse_combo(combo)
        self.down: set[str] = set()
        self.active = False
        self._listener = None

    def start(self) -> None:
        from pynput import keyboard

        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()

    def _on_press(self, key) -> None:
        name = canonical(key)
        if name is None:
            return
        self.down.add(name)
        if not self.active and self.want <= self.down:
            self.active = True
            self.pressed.emit()

    def _on_release(self, key) -> None:
        name = canonical(key)
        self.down.discard(name)
        if self.active and not self.want <= self.down:
            self.active = False
            self.released.emit()
