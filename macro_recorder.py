"""
Macro Recorder - Step 3
(core + combos/drag/scroll + modifier clicks + settings, comments, beep, replay)

Run it, do your task normally, press F9 to stop.
It saves a ready-to-run macro_YYYY-MM-DD_HHMMSS.py next to this file,
then asks if you want to replay it right away.

Install once:  pip install pynput pyautogui
"""

import ctypes
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from pynput import keyboard, mouse

try:
    import winsound   # Windows-only (built in): used for the start/stop beep
except ImportError:
    winsound = None   # not on Windows: beeps are simply skipped

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
BEEP = True             # beep when recording starts and stops
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

# Words used in the "# click at 14:30:02" comments of the generated script
LABELS = {
    "click": "click", "drag": "drag", "scroll": "scroll", "hscroll": "sideways scroll",
    "text": "typed text", "key": "key press", "hotkey": "shortcut",
}


def beep(freq, ms):
    """Short beep so you know recording started/stopped (skipped if BEEP is off)."""
    if BEEP and winsound:
        winsound.Beep(freq, ms)


@dataclass
class Action:
    """One recorded event."""
    kind: str             # "click", "drag", "scroll", "hscroll", "text", "key", or "hotkey"
    time: float           # when it started
    x: int = 0            # mouse position (click start, drag start, scroll spot)
    y: int = 0
    x2: int = 0           # drag only: where the button was released
    y2: int = 0
    value: str = ""       # mouse button name, typed text, or key name
    amount: int = 0       # scroll/hscroll: wheel notches (down/left = negative)
    keys: tuple = ()      # hotkey only, e.g. ("ctrl", "c")
    mods: tuple = ()      # modifiers held during a click/drag/scroll, e.g. ("ctrl",)
    end_time: float = 0   # when it ended (differs from `time` for drags and merged text/scroll)

    def __post_init__(self):
        if not self.end_time:
            self.end_time = self.time


class Recorder:
    """Listens to the mouse and keyboard and collects Action objects."""

    def __init__(self):
        self.actions = []
        self.held = set()    # modifiers currently held down, e.g. {"ctrl"}
        self._press = None   # (x, y, time, mods) of the mouse button that is currently down

    def _current_mods(self):
        """Modifiers held right now in a fixed order, e.g. ('ctrl', 'shift')."""
        return tuple(m for m in MODIFIER_ORDER if m in self.held)

    # ----- mouse -----
    def _on_click(self, x, y, button, pressed):
        if pressed:
            self._press = (x, y, time.time(), self._current_mods())  # remember the press
            return
        if self._press is None:
            return
        px, py, t0, mods = self._press
        self._press = None
        if max(abs(x - px), abs(y - py)) < DRAG_MIN_PIXELS:
            # Barely moved = a normal click (recorded at the press position)
            self.actions.append(Action("click", t0, x=px, y=py, value=button.name, mods=mods))
        else:
            # Moved a lot = a drag from the press point to the release point
            self.actions.append(Action("drag", t0, x=px, y=py, x2=x, y2=y,
                                       value=button.name, mods=mods, end_time=time.time()))

    def _on_scroll(self, x, y, dx, dy):
        # dy: +1 = one notch up, -1 = down.  dx: +1 = one notch right, -1 = left.
        now, mods = time.time(), self._current_mods()
        if dy:
            self.actions.append(Action("scroll", now, x=x, y=y, amount=int(dy), mods=mods))
        if dx:
            self.actions.append(Action("hscroll", now, x=x, y=y, amount=int(dx), mods=mods))

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
        keys = self._current_mods() + (key_name,)
        self.actions.append(Action("hotkey", now, keys=keys))

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
        beep(1000, 200)                              # high beep = recording started
        print("Recording! Do your task now. Press F9 to stop.")

        mouse_listener = mouse.Listener(on_click=self._on_click, on_scroll=self._on_scroll)
        mouse_listener.start()                       # runs in a background thread
        with keyboard.Listener(on_press=self._on_press,
                               on_release=self._on_release) as kb_listener:
            kb_listener.join()                       # waits here until F9
        mouse_listener.stop()
        beep(600, 400)                               # low beep = recording stopped
        print("Recording stopped.")
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
            elif (last and a.kind in ("scroll", "hscroll") and last.kind == a.kind
                  and a.mods == last.mods and gap < SCROLL_MERGE_GAP
                  and (a.amount > 0) == (last.amount > 0)):
                last.amount += a.amount              # same direction: add the notches up
                last.end_time = a.time
            else:
                merged.append(replace(a))            # copy so the originals stay untouched
        return merged

    def _core_code(self, a):
        """The pyautogui line(s) for one Action (without modifiers or comment)."""
        if a.kind == "click":
            if a.value == "left":
                return [f"pyautogui.click({a.x}, {a.y})"]
            return [f'pyautogui.click({a.x}, {a.y}, button="{a.value}")']
        if a.kind == "drag":
            duration = max(0.3, round(a.end_time - a.time, 1))   # too fast a drag can fail
            return [f"pyautogui.moveTo({a.x}, {a.y})",
                    f'pyautogui.dragTo({a.x2}, {a.y2}, duration={duration} / SPEED, '
                    f'button="{a.value}")']
        if a.kind == "scroll":
            return [f"pyautogui.scroll({a.amount * SCROLL_UNIT}, x={a.x}, y={a.y})"]
        if a.kind == "hscroll":
            return [f"pyautogui.hscroll({a.amount * SCROLL_UNIT}, x={a.x}, y={a.y})"]
        if a.kind == "hotkey":
            return [f"pyautogui.hotkey({', '.join(repr(k) for k in a.keys)})"]
        if a.kind == "text":
            return [f"pyautogui.write({a.value!r}, interval=0.05)"]
        return [f'pyautogui.press("{a.value}")']

    def _lines_for(self, a):
        """Full code for one Action: hold modifiers, do it, release them, add a comment."""
        lines = ([f"pyautogui.keyDown({m!r})" for m in a.mods]
                 + self._core_code(a)
                 + [f"pyautogui.keyUp({m!r})" for m in reversed(a.mods)])
        label = LABELS[a.kind]
        if a.mods:
            label = "+".join(a.mods) + "+" + label   # e.g. "ctrl+click"
        clock = datetime.fromtimestamp(a.time).strftime("%H:%M:%S")
        lines[0] += f"  # {label} at {clock}"
        return lines

    def build(self, actions):
        """Return the full text of the generated script."""
        body = []
        prev_end = None
        # Sort by start time because a click/drag is saved when the button is released
        for a in self._merge(sorted(actions, key=lambda a: a.time)):
            # Turn the pause since the previous action into a sleep line
            if prev_end is not None and a.time - prev_end >= MIN_WAIT:
                body.append(f"time.sleep({a.time - prev_end:.1f} / SPEED)")
            body.extend(self._lines_for(a))
            prev_end = a.end_time

        made = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            f"# Auto-generated by Macro Recorder on {made}",
            "import time",
            "import pyautogui",
            "",
            "# ---------- Settings (edit these) ----------",
            "SPEED = 1.0      # 2.0 = twice as fast, 0.5 = half speed (divides every wait)",
            "REPEAT = 1       # how many times to run the whole macro",
            "COUNTDOWN = 3    # seconds to wait before it starts (time to switch windows)",
            "",
            "pyautogui.FAILSAFE = True        # mouse into a screen corner = emergency stop",
            "pyautogui.PAUSE = 0.1 / SPEED    # pyautogui's built-in pause after each action",
            "",
            "",
            "def run_macro():",
            '    """One full pass of everything you recorded."""',
        ]
        lines += ["    " + line for line in body] or ["    pass"]
        lines += [
            "",
            "",
            'if __name__ == "__main__":',
            '    print(f"Starting in {COUNTDOWN} seconds... (mouse into a screen corner to stop)")',
            "    time.sleep(COUNTDOWN)",
            "    try:",
            "        for i in range(REPEAT):",
            '            print(f"Run {i + 1} of {REPEAT}")',
            "            run_macro()",
            '        print("Done.")',
            "    except pyautogui.FailSafeException:",
            '        print("Stopped: the mouse hit a screen corner.")',
        ]
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

    # Instant replay: run the new script in a separate Python process
    if input("Run it now? (y/n): ").strip().lower() == "y":
        subprocess.run([sys.executable, str(path)])
    else:
        print(f"Replay it later with:  python \"{path.name}\"")


if __name__ == "__main__":
    main()