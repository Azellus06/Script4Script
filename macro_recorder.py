"""
Macro Recorder - Step 1 (core)

Run it, do your task normally, press F9 to stop.
It saves a ready-to-run macro_YYYY-MM-DD_HHMMSS.py next to this file.

Install once:  pip install pynput pyautogui
"""

import ctypes
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from pynput import keyboard, mouse

# On Windows with display scaling (125%, 150%...), make pynput report the same
# pixel positions that pyautogui will click later. Without this, clicks can
# land slightly off.
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

# ---------- Settings ----------
START_DELAY = 3   # seconds to switch to your target window before recording starts
MIN_WAIT = 0.3    # pauses shorter than this don't become time.sleep() lines
MERGE_GAP = 1.0   # letters typed within this many seconds merge into one write()

# Keys that are only "held" (Shift, Ctrl, Alt, Windows). Ignored in step 1;
# step 2 will turn combos like Ctrl+C into hotkey().
MODIFIER_KEYS = {
    "shift", "shift_l", "shift_r", "ctrl", "ctrl_l", "ctrl_r",
    "alt", "alt_l", "alt_r", "alt_gr", "cmd", "cmd_l", "cmd_r",
}

# pynput key name -> pyautogui key name (only where they differ)
KEY_NAMES = {
    "page_up": "pageup",
    "page_down": "pagedown",
    "caps_lock": "capslock",
    "num_lock": "numlock",
    "scroll_lock": "scrolllock",
    "print_screen": "printscreen",
    "menu": "apps",
}


@dataclass
class Action:
    """One recorded event."""
    kind: str             # "click", "text", or "key"
    time: float           # when it started
    x: int = 0            # click position
    y: int = 0
    value: str = ""       # mouse button name, typed text, or key name
    end_time: float = 0   # when it ended (differs from `time` only for merged text)

    def __post_init__(self):
        if not self.end_time:
            self.end_time = self.time


class Recorder:
    """Listens to the mouse and keyboard and collects Action objects."""

    def __init__(self):
        self.actions = []

    def _on_click(self, x, y, button, pressed):
        # Only record the press, not the release (drag support comes in step 2)
        if pressed:
            self.actions.append(Action("click", time.time(), x, y, button.name))

    def _on_press(self, key):
        if key == keyboard.Key.f9:
            return False          # returning False stops the keyboard listener
        if key == keyboard.Key.f8:
            return                # reserved for markers (step 4), not recorded

        now = time.time()
        char = getattr(key, "char", None)

        # Normal letters, digits, symbols
        if char is not None:
            if char.isprintable():        # skips control codes made by Ctrl+letter
                self.actions.append(Action("text", now, value=char))
            return

        if key == keyboard.Key.space:     # space is treated as text so words merge
            self.actions.append(Action("text", now, value=" "))
            return

        # Special keys: enter, tab, backspace, arrows, F1-F12...
        name = getattr(key, "name", None)
        if name is None or name in MODIFIER_KEYS:
            return
        self.actions.append(Action("key", now, value=KEY_NAMES.get(name, name)))

    def run(self):
        """Record until F9 is pressed, then return the list of actions."""
        for i in range(START_DELAY, 0, -1):
            print(f"Recording starts in {i}... (switch to your target window)")
            time.sleep(1)
        print("Recording! Do your task now. Press F9 to stop.")

        mouse_listener = mouse.Listener(on_click=self._on_click)
        mouse_listener.start()                       # runs in a background thread
        with keyboard.Listener(on_press=self._on_press) as kb_listener:
            kb_listener.join()                       # waits here until F9
        mouse_listener.stop()
        return self.actions


class ScriptWriter:
    """Turns a list of Actions into Python code and saves it."""

    def _merge_text(self, actions):
        """Combine letters typed close together: h,e,l,l,o -> 'hello'."""
        merged = []
        for a in actions:
            last = merged[-1] if merged else None
            if (a.kind == "text" and last is not None and last.kind == "text"
                    and a.time - last.end_time < MERGE_GAP):
                last.value += a.value
                last.end_time = a.time
            else:
                merged.append(replace(a))   # copy so the originals stay untouched
        return merged

    def _to_code(self, a):
        """One Action -> one line of pyautogui code."""
        if a.kind == "click":
            if a.value == "left":
                return f"pyautogui.click({a.x}, {a.y})"
            return f'pyautogui.click({a.x}, {a.y}, button="{a.value}")'
        if a.kind == "text":
            return f"pyautogui.write({a.value!r}, interval=0.05)"
        return f'pyautogui.press("{a.value}")'

    def build(self, actions):
        """Return the full text of the generated script."""
        lines = [
            "# Auto-generated by Macro Recorder",
            "import time",
            "import pyautogui",
            "",
        ]
        prev_end = None
        for a in self._merge_text(actions):
            # Turn the pause since the previous action into a sleep line
            if prev_end is not None and a.time - prev_end >= MIN_WAIT:
                lines.append(f"time.sleep({a.time - prev_end:.1f})")
            lines.append(self._to_code(a))
            prev_end = a.end_time
        return "\n".join(lines) + "\n"

    def save(self, actions):
        """Write the script next to this file and return its path."""
        name = datetime.now().strftime("macro_%Y-%m-%d_%H%M%S.py")
        path = Path(__file__).parent / name
        path.write_text(self.build(actions), encoding="utf-8")
        return path


def main():
    actions = Recorder().run()
    if not actions:
        print("Nothing was recorded, so no file was saved.")
        return
    path = ScriptWriter().save(actions)
    print(f"Saved {len(actions)} actions to: {path}")
    print(f"Replay it with:  python \"{path.name}\"")


if __name__ == "__main__":
    main()
