"""
Macro Recorder - Step 4 (full version)

Run it, do your task normally, press F9 to stop.
It saves a ready-to-run macro_YYYY-MM-DD_HHMMSS.py next to this file
(plus a macro_..._images folder if you used picture markers),
then asks if you want to replay it right away.

Install once:  pip install pynput pyautogui pillow opencv-python

Hotkeys while recording:
  F9         stop and save
  F8         image click   - hover over a button, press F8, then click it as normal
  Shift+F8   wait for image - press it while the thing you are waiting for is on screen
  Ctrl+F8    new step       - starts a new step_N() function in the script
"""

import ctypes
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from pynput import keyboard, mouse

try:
    import winsound   # Windows-only (built in): used for the beeps
except ImportError:
    winsound = None   # not on Windows: beeps are simply skipped

# On Windows with display scaling (125%, 150%...), make pynput report the same
# pixel positions that pyautogui will click later. Without this, clicks can
# land slightly off. This must run BEFORE pyautogui is imported below.
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

import pyautogui  # noqa: E402  (used here only to take marker screenshots)

# ---------- Settings ----------
START_DELAY = 3         # seconds to switch to your target window before recording starts
BEEP = True             # beep when recording starts/stops and when a marker is saved
MIN_WAIT = 0.3          # pauses shorter than this don't become time.sleep() lines
MERGE_GAP = 1.0         # letters typed within this many seconds merge into one write()
DRAG_MIN_PIXELS = 10    # mouse moved less than this between press and release = plain click
SCROLL_MERGE_GAP = 0.5  # wheel notches within this many seconds merge into one scroll()
SCROLL_UNIT = 120       # Windows: one wheel notch = 120 units in pyautogui.scroll()
IMAGE_W, IMAGE_H = 100, 60   # size (pixels) of the picture saved around your mouse

KEY_STOP = keyboard.Key.f9      # stops recording
KEY_MARKER = keyboard.Key.f8    # F8 / Shift+F8 / Ctrl+F8 markers

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
    "click": "click", "image_click": "image click", "drag": "drag",
    "scroll": "scroll", "hscroll": "sideways scroll", "wait_image": "wait for image",
    "text": "typed text", "key": "key press", "hotkey": "shortcut",
}

# Code pasted into generated scripts that use pictures. __FOLDER__ is replaced
# with the name of the picture folder.
IMAGE_HELPERS = '''# ---------- Picture helpers (used by image clicks and wait-for-image) ----------
IMAGE_DIR = Path(__file__).parent / "__FOLDER__"   # folder with the marker pictures
CONFIDENCE = 0.8     # how close a match must be, 0.0-1.0 (lower = more forgiving)
IMAGE_TIMEOUT = 5    # seconds to look for a picture before clicking the old spot instead
WAIT_TIMEOUT = 30    # seconds to wait for a "wait for image" picture before stopping
NOT_FOUND = getattr(pyautogui, "ImageNotFoundException", Exception)


def find_image(name, timeout):
    """Search the screen for a picture until it appears or time runs out. Returns its center or None."""
    path = IMAGE_DIR / name
    if not path.exists():
        raise SystemExit(f"Missing picture: {path}")
    end = time.time() + timeout
    while True:
        try:
            spot = pyautogui.locateCenterOnScreen(str(path), confidence=CONFIDENCE)
        except NOT_FOUND:
            spot = None
        if spot is not None:
            return spot
        if time.time() >= end:
            return None
        time.sleep(0.5)


def click_image(name, dx, dy, fallback_x, fallback_y, button="left"):
    """Click where the picture is now (plus the recorded offset); else click the old spot."""
    spot = find_image(name, IMAGE_TIMEOUT)
    if spot is not None:
        pyautogui.click(spot.x + dx, spot.y + dy, button=button)
    else:
        print(f"Could not find {name}, clicking the recorded spot instead.")
        pyautogui.click(fallback_x, fallback_y, button=button)


def wait_for_image(name):
    """Pause until the picture appears on screen; stop the macro if it never does."""
    if find_image(name, WAIT_TIMEOUT) is None:
        raise SystemExit(f"Stopped: {name} did not appear within {WAIT_TIMEOUT} seconds.")
'''


def beep(freq, ms):
    """Short beep so you know something registered (skipped if BEEP is off)."""
    if BEEP and winsound:
        winsound.Beep(freq, ms)


def beep_async(freq, ms):
    """Beep in a background thread so the key listener is never held up."""
    threading.Thread(target=beep, args=(freq, ms), daemon=True).start()


@dataclass
class Action:
    """One recorded event."""
    kind: str             # click, image_click, drag, scroll, hscroll, wait_image,
                          # text, key, hotkey, or section (a "new step" marker)
    time: float           # when it started
    x: int = 0            # mouse position (click start, drag start, scroll spot)
    y: int = 0
    x2: int = 0           # drag only: where the button was released
    y2: int = 0
    value: str = ""       # mouse button name, typed text, or key name
    amount: int = 0       # scroll/hscroll: wheel notches (down/left = negative)
    keys: tuple = ()      # hotkey only, e.g. ("ctrl", "c")
    mods: tuple = ()      # modifiers held during a click/drag/scroll, e.g. ("ctrl",)
    image: str = ""       # image_click / wait_image: picture file name, e.g. "click_1.png"
    img_dx: int = 0       # image_click: click point minus the picture's center
    img_dy: int = 0
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
        # The script and its picture folder share this name, e.g. macro_2026-09-28_143005
        self.session = datetime.now().strftime("macro_%Y-%m-%d_%H%M%S")
        self.image_dir = Path(__file__).parent / f"{self.session}_images"
        self._mouse = mouse.Controller()   # lets us read the mouse position on F8
        self._marker_count = 0             # numbers the pictures: click_1.png, wait_2.png...
        self._pending_image = None         # (file, center_x, center_y) waiting for your next click

    def _current_mods(self):
        """Modifiers held right now in a fixed order, e.g. ('ctrl', 'shift')."""
        return tuple(m for m in MODIFIER_ORDER if m in self.held)

    # ----- markers (F8 family) -----
    def _save_picture(self, purpose):
        """Screenshot the area around the mouse. Returns (file, center_x, center_y) or None."""
        x, y = self._mouse.position
        screen_w, screen_h = pyautogui.size()
        # Center the picture on the mouse, but keep it fully on screen near the edges
        left = max(0, min(x - IMAGE_W // 2, screen_w - IMAGE_W))
        top = max(0, min(y - IMAGE_H // 2, screen_h - IMAGE_H))
        self._marker_count += 1
        name = f"{purpose}_{self._marker_count}.png"
        try:
            self.image_dir.mkdir(exist_ok=True)
            pyautogui.screenshot(region=(left, top, IMAGE_W, IMAGE_H)).save(self.image_dir / name)
        except Exception as error:
            print(f"  Could not save the picture ({error}). "
                  "Did you run: pip install pillow opencv-python ?")
            return None
        return name, left + IMAGE_W // 2, top + IMAGE_H // 2

    def _marker_image_click(self):
        """F8: save a picture; your next click on it becomes an image click."""
        saved = self._save_picture("click")
        if saved:
            self._pending_image = saved
            print(f"  F8: saved {saved[0]} - now click it.")
            beep_async(800, 100)

    def _marker_wait(self):
        """Shift+F8: save a picture and make the script wait until it appears."""
        saved = self._save_picture("wait")
        if saved:
            self.actions.append(Action("wait_image", time.time(), image=saved[0]))
            print(f"  Shift+F8: saved {saved[0]} - the script will wait for it.")
            beep_async(1200, 100)

    def _marker_section(self):
        """Ctrl+F8: everything from here on goes into the next step_N() function."""
        self.actions.append(Action("section", time.time()))
        print("  Ctrl+F8: new step started.")
        beep_async(600, 100)

    # ----- mouse -----
    def _on_click(self, x, y, button, pressed):
        if pressed:
            self._press = (x, y, time.time(), self._current_mods())  # remember the press
            return
        if self._press is None:
            return
        px, py, t0, mods = self._press
        self._press = None
        pending, self._pending_image = self._pending_image, None   # a marker is used once

        if max(abs(x - px), abs(y - py)) >= DRAG_MIN_PIXELS:
            # Moved a lot = a drag from the press point to the release point
            if pending:
                print("  The picture from F8 was not used (you dragged instead of clicking).")
            self.actions.append(Action("drag", t0, x=px, y=py, x2=x, y2=y,
                                       value=button.name, mods=mods, end_time=time.time()))
            return

        # Barely moved = a normal click (recorded at the press position)
        action = Action("click", t0, x=px, y=py, value=button.name, mods=mods)
        if pending:
            name, cx, cy = pending
            if abs(px - cx) <= IMAGE_W // 2 and abs(py - cy) <= IMAGE_H // 2:
                # Click landed inside the picture: turn it into an image click
                action = replace(action, kind="image_click", image=name,
                                 img_dx=px - cx, img_dy=py - cy)
            else:
                print("  Your click was outside the F8 picture, so it stays a normal click.")
        self.actions.append(action)

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
        if key == KEY_STOP:
            return False          # returning False stops the keyboard listener
        if key == KEY_MARKER:     # the modifiers tell us which marker it is
            if "shift" in self.held:
                self._marker_wait()
            elif "ctrl" in self.held:
                self._marker_section()
            else:
                self._marker_image_click()
            return

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
        print("Recording! Do your task now.")
        print("  F9 = stop | F8 = image click | Shift+F8 = wait for image | Ctrl+F8 = new step")

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

    def _split_steps(self, merged):
        """Cut the action list at every 'section' marker: [[step 1 actions], [step 2 actions]]."""
        steps, current = [], []
        for a in merged:
            if a.kind == "section":
                if current:
                    steps.append(current)
                current = []
            else:
                current.append(a)
        if current:
            steps.append(current)
        return steps

    def _core_code(self, a):
        """The pyautogui line(s) for one Action (without modifiers or comment)."""
        if a.kind == "click":
            if a.value == "left":
                return [f"pyautogui.click({a.x}, {a.y})"]
            return [f'pyautogui.click({a.x}, {a.y}, button="{a.value}")']
        if a.kind == "image_click":
            extra = "" if a.value == "left" else f', button="{a.value}"'
            return [f'click_image("{a.image}", {a.img_dx}, {a.img_dy}, {a.x}, {a.y}{extra})']
        if a.kind == "wait_image":
            return [f'wait_for_image("{a.image}")']
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

    def _pause(self, a, prev_end):
        """A time.sleep line for the gap before `a`. None if the gap is tiny, or for
        wait-for-image, which waits by itself instead of sleeping."""
        if prev_end is None or a.kind == "wait_image":
            return None
        gap = a.time - prev_end
        return f"time.sleep({gap:.1f} / SPEED)" if gap >= MIN_WAIT else None

    def _step_body(self, step):
        """Code lines for one step's actions, with sleeps between them."""
        body, prev_end = [], None
        for a in step:
            pause = self._pause(a, prev_end)
            if pause:
                body.append(pause)
            body += self._lines_for(a)
            prev_end = a.end_time
        return body

    def build(self, actions, session):
        """Return the full text of the generated script."""
        # Sort by start time because a click/drag is saved when the button is released
        merged = self._merge(sorted(actions, key=lambda a: a.time))
        steps = self._split_steps(merged)
        uses_images = any(a.kind in ("image_click", "wait_image") for a in merged)

        made = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            f"# Auto-generated by Macro Recorder on {made}",
            "import time",
            "import pyautogui",
        ]
        if uses_images:
            lines.append("from pathlib import Path")
        lines += [
            "",
            "# ---------- Settings (edit these) ----------",
            "SPEED = 1.0      # 2.0 = twice as fast, 0.5 = half speed (divides every wait)",
            "REPEAT = 1       # how many times to run the whole macro",
            "COUNTDOWN = 3    # seconds to wait before it starts (time to switch windows)",
            "",
            "pyautogui.FAILSAFE = True        # mouse into a screen corner = emergency stop",
            "pyautogui.PAUSE = 0.1 / SPEED    # pyautogui's built-in pause after each action",
        ]
        if uses_images:
            lines += ["", IMAGE_HELPERS.replace("__FOLDER__", f"{session}_images").rstrip("\n")]

        if len(steps) > 1:
            # Several steps: one function per step, and run_macro() calls them in order
            for n, step in enumerate(steps, 1):
                started = datetime.fromtimestamp(step[0].time).strftime("%H:%M:%S")
                lines += ["", "", f"def step_{n}():", f'    """Step {n} (recorded at {started})."""']
                lines += ["    " + line for line in self._step_body(step)]
            lines += ["", "", "def run_macro():",
                      '    """One full pass: runs every step in order (reorder or remove lines to change it)."""']
            for n, step in enumerate(steps, 1):
                if n > 1:
                    pause = self._pause(step[0], steps[n - 2][-1].end_time)
                    if pause:
                        lines.append("    " + pause)
                lines.append(f"    step_{n}()")
        else:
            body = self._step_body(steps[0]) if steps else ["pass"]
            lines += ["", "", "def run_macro():",
                      '    """One full pass of everything you recorded."""']
            lines += ["    " + line for line in body]

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

    def save(self, actions, session):
        """Write the script next to this file and return its path."""
        path = Path(__file__).parent / f"{session}.py"
        path.write_text(self.build(actions, session), encoding="utf-8")
        return path


def main():
    recorder = Recorder()
    actions = recorder.run()
    if not actions:
        print("Nothing was recorded, so no file was saved.")
        return
    path = ScriptWriter().save(actions, recorder.session)
    print(f"Saved {len(actions)} actions to: {path}")
    if recorder.image_dir.exists():
        print(f"Pictures are in: {recorder.image_dir}  (keep this folder next to the script)")

    # Instant replay: run the new script in a separate Python process
    if input("Run it now? (y/n): ").strip().lower() == "y":
        subprocess.run([sys.executable, str(path)])
    else:
        print(f"Replay it later with:  python \"{path.name}\"")


if __name__ == "__main__":
    main()