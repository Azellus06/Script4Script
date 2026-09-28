"""
Macro Recorder - Step 2 (core + combos, drag, scroll)

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
START_DELAY = 3         # seconds to switch to your target window before recording starts
MIN_WAIT = 0.3          # pauses shorter than this don't become time.sleep() lines
MERGE_GAP = 1.0         # letters typed within this many seconds merge into one write()
DRAG_MIN_PIXELS = 10    # mouse moved less than this between press and release = plain click
SCROLL_MERGE_GAP = 0.5  # wheel notches within this many seconds merge into one scroll()
SCROLL_UNIT = 120       # Windows: one wheel notch = 120 units in pyautogui.scroll()

# pynput modifier key name -> the name pyautogui uses
MODIFIER_NAMES = {
    "ctrl": "ctrl", "ctrl_l": "ctrl", "ctrl_r": "ctrl",
    "shift": "shift", "shift_l": "shift", "shift_r": "shift",
    "alt": "alt", "alt_l": "alt", "alt_r": "alt",
    "cmd": "win", "cmd_l": "win", "cmd_r": "win",
}
MODIFIER_ORDER = ("ctrl", "shift", "alt", "win")  # order used inside hotkey(...)

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
    kind: str             # "click", "drag", "scroll", "text", "key", or "hotkey"
    time: float           # when it started
    x: int = 0            # mouse position (click start, drag start, scroll spot)
    y: int = 0
    x2: int = 0           # drag only: where the button was released
    y2: int = 0
    value: str = ""       # mouse button name, typed text, or key name
    amount: int = 0       # scroll only: wheel notches (negative = down)
    keys: tuple = ()      # hotkey only, e.g. ("ctrl", "c")
    end_time: float = 0   # when it ended (differs from `time` for drags and merged text/scroll)

    def __post_init__(self):
        if not self.end_time:
            self.end_time = self.time


class Recorder:
    """Listens to the mouse and keyboard and collects Action objects."""

    def __init__(self):
        self.actions = []
        self.held = set()    # modifiers currently held down, e.g. {"ctrl"}
        self._press = None   # (x, y, time) of the mouse button that is currently down

    # ----- mouse -----
    def _on_click(self, x, y, button, pressed):
        if pressed:
            self._press = (x, y, time.time())    # remember where the button went down
            return
        if self._press is None:
            return
        px, py, t0 = self._press
        self._press = None
        if max(abs(x - px), abs(y - py)) < DRAG_MIN_PIXELS:
            # Barely moved = a normal click (recorded at the press position)
            self.actions.append(Action("click", t0, x=px, y=py, value=button.name))
        else:
            # Moved a lot = a drag from the press point to the release point
            self.actions.append(Action("drag", t0, x=px, y=py, x2=x, y2=y,
                                       value=button.name, end_time=time.time()))

    def _on_scroll(self, x, y, dx, dy):
        # dy: +1 = one notch up, -1 = one notch down. Sideways scroll (dx) is ignored.
        if dy:
            self.actions.append(Action("scroll", time.time(), x=x, y=y, amount=int(dy)))

    # ----- keyboard -----
    @staticmethod
    def _char_to_key(char):
        """Ctrl+letter arrives as a control code ('\\x03' = Ctrl+C); turn it back into 'c'."""
        if len(char) == 1 and 1 <= ord(char) <= 26:
            return chr(ord(char) + 96)
        if char.isprintable():
            return char.lower()
        return None

    def _add_hotkey(self, now, key_name):
        """Save the held modifiers + this key as one combo, e.g. ("ctrl", "c")."""
        mods = [m for m in MODIFIER_ORDER if m in self.held]
        self.actions.append(Action("hotkey", now, keys=tuple(mods + [key_name])))

    def _on_press(self, key):
        if key == keyboard.Key.f9:
            return False          # returning False stops the keyboard listener
        if key == keyboard.Key.f8:
            return                # reserved for markers (step 4), not recorded

        now = time.time()
        name = getattr(key, "name", None)

        # A modifier going down: remember it, but don't record it by itself
        if name in MODIFIER_NAMES:
            self.held.add(MODIFIER_NAMES[name])
            return
        if name == "alt_gr":
            return

        # Shift alone is not a "combo" for letters (Shift+a already arrives as "A")
        combo = self.held & {"ctrl", "alt", "win"}
        char = getattr(key, "char", None)

        # Normal letters, digits, symbols
        if char is not None:
            if combo:
                key_name = self._char_to_key(char)
                if key_name:
                    self._add_hotkey(now, key_name)
            elif char.isprintable():
                self.actions.append(Action("text", now, value=char))
            return

        # Space is text so words merge (but Ctrl+Space is a combo)
        if key == keyboard.Key.space and not combo:
            self.actions.append(Action("text", now, value=" "))
            return

        # Special keys: enter, tab, backspace, arrows, F1-F12...
        if name is None:
            return
        key_name = KEY_NAMES.get(name, name)
        if self.held:                              # e.g. Shift+Tab, Alt+Tab
            self._add_hotkey(now, key_name)
        else:
            self.actions.append(Action("key", now, value=key_name))

    def _on_release(self, key):
        name = getattr(key, "name", None)
        if name in MODIFIER_NAMES:
            self.held.discard(MODIFIER_NAMES[name])

    # ----- run -----
    def run(self):
        """Record until F9 is pressed, then return the list of actions."""
        for i in range(START_DELAY, 0, -1):
            print(f"Recording starts in {i}... (switch to your target window)")
            time.sleep(1)
        print("Recording! Do your task now. Press F9 to stop.")

        mouse_listener = mouse.Listener(on_click=self._on_click, on_scroll=self._on_scroll)
        mouse_listener.start()                       # runs in a background thread
        with keyboard.Listener(on_press=self._on_press,
                               on_release=self._on_release) as kb_listener:
            kb_listener.join()                       # waits here until F9
        mouse_listener.stop()
        return self.actions


class ScriptWriter:
    """Turns a list of Actions into Python code and saves it."""

    def _merge(self, actions):
        """Combine repeated events: h,e,l,l,o -> 'hello'; three scroll notches -> one scroll."""
        merged = []
        for a in actions:
            last = merged[-1] if merged else None
            gap = a.time - last.end_time if last else None
            if last and a.kind == "text" and last.kind == "text" and gap < MERGE_GAP:
                last.value += a.value
                last.end_time = a.time
            elif (last and a.kind == "scroll" and last.kind == "scroll"
                  and gap < SCROLL_MERGE_GAP and (a.amount > 0) == (last.amount > 0)):
                last.amount += a.amount              # same direction: add the notches up
                last.end_time = a.time
            else:
                merged.append(replace(a))            # copy so the originals stay untouched
        return merged

    def _to_code(self, a):
        """One Action -> one or two lines of pyautogui code."""
        if a.kind == "click":
            if a.value == "left":
                return f"pyautogui.click({a.x}, {a.y})"
            return f'pyautogui.click({a.x}, {a.y}, button="{a.value}")'
        if a.kind == "drag":
            duration = max(0.3, round(a.end_time - a.time, 1))   # too fast a drag can fail
            return (f"pyautogui.moveTo({a.x}, {a.y})\n"
                    f'pyautogui.dragTo({a.x2}, {a.y2}, duration={duration}, button="{a.value}")')
        if a.kind == "scroll":
            return f"pyautogui.scroll({a.amount * SCROLL_UNIT}, x={a.x}, y={a.y})"
        if a.kind == "hotkey":
            return f"pyautogui.hotkey({', '.join(repr(k) for k in a.keys)})"
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
        # Sort by start time because a click/drag is saved when the button is released
        for a in self._merge(sorted(actions, key=lambda a: a.time)):
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