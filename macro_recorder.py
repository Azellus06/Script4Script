"""
Macro Recorder

Record:    python macro_recorder.py
           Do your task normally, press F9 to stop.
           It saves three things in recordings/macro_<date>_<time>/ next to this file:
             macro_<date>_<time>.jsonl      the raw recording (every action, saved as it happens)
             macro_<date>_<time>.py         a ready-to-run script made from the recording
             macro_<date>_<time>_images/    pictures from the F8 markers (only if you used them)
           It never asks anything in the terminal, so it also works when started without a console (.pyw).

Regenerate: python macro_recorder.py generate macro_<date>_<time>.jsonl
           Rebuilds a script from a saved recording without re-recording, for example after
           changing MERGE_GAP or MIN_WAIT below. Writes macro_<date>_<time>_regen.py.

Install once:  pip install pynput pyautogui pillow opencv-python pydirectinput-rgx

Games (Roblox...): scripts replay with pydirectinput by default, which games accept.
           Add --backend pyautogui to either command (or edit BACKEND in the script) for normal apps.
           Keys held longer than HOLD_MIN seconds replay as a real hold (walking with W).
           Camera / mouse-look is not supported. Use windowed or borderless mode for F8 pictures.
           If the game runs as administrator, run the script as administrator too.

Hotkeys while recording:
  F9         stop and save
  Shift+F9   pause / resume - nothing is recorded while paused (use it for passwords!)
  F8         image click   - hover over a button, press F8, then click it as normal
  Shift+F8   wait for image - press it while the thing you are waiting for is on screen
  Ctrl+F8    new step       - starts a new step_N() function in the script

WARNING: everything you type is saved as plain text in the .jsonl and .py files.
"""
import argparse
import ctypes
import json
import sys
import threading
import time
from dataclasses import asdict, dataclass, fields, replace
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
BACKEND = "pydirectinput"    # library new scripts replay with: "pydirectinput" works in games
                             # (Roblox...), "pyautogui" is for normal apps. You can also change
                             # BACKEND inside any generated script, or use --backend.
BACKENDS = ("pydirectinput", "pyautogui")
HOLD_MIN = 0.3          # other keys / mouse buttons held this long or more replay as a hold
MOVEMENT_KEYS = "wasd"  # your walk keys, one letter each: "esdf" if that is what your game uses
GAME_KEYS = tuple(MOVEMENT_KEYS) + ("up", "down", "left", "right", "space", "shift")
GAME_HOLD_MIN = 0.03    # movement keys keep even a quick tap's real length (a 0.1 s tap = a small step)
RECORDINGS_DIR = "recordings"   # every session gets its own folder in here, next to this file
EVENT_MIN_WAIT = 0.02   # waits next to key/button down-up events are kept this precisely (circles need it)
REPEAT_WINDOW = 1.5     # a held key repeats faster than this; a gap longer than this = a new press

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

# Virtual-key codes used by _reconcile_mods() to detect stale modifier state.
# FIX B3: GetAsyncKeyState returns 0x8000 when the physical key is held down.
_VK_MOD_MAP = {
    0xA2: "ctrl",   # VK_LCONTROL
    0xA3: "ctrl",   # VK_RCONTROL
    0xA0: "shift",  # VK_LSHIFT
    0xA1: "shift",  # VK_RSHIFT
    0x12: "alt",    # VK_MENU (both sides)
    0x5B: "win",    # VK_LWIN
    0x5C: "win",    # VK_RWIN
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

# Words used in the "# click at 14:30:02" comments of the generated script
LABELS = {
    "click": "click", "image_click": "image click", "drag": "drag",
    "scroll": "scroll", "hscroll": "sideways scroll", "wait_image": "wait for image",
    "text": "typed text", "key": "key press", "hotkey": "shortcut", "hold": "key hold",
    "keydown": "key down", "keyup": "key up", "mousedown": "mouse down", "mouseup": "mouse up",
}
EVENT_KINDS = ("keydown", "keyup", "mousedown", "mouseup")   # made at generate time from holds
KINDS = set(LABELS) | {"section"}   # every kind an Action can have (a recording is checked against this)


def hold_min(keys):
    """Shortest press that is kept as a timed down/up: tiny for movement keys (WASD, arrows...),
    HOLD_MIN for everything else so normal typing still becomes simple write() lines."""
    return GAME_HOLD_MIN if keys and keys[-1] in GAME_KEYS else HOLD_MIN


def keys_to_hold(action):
    """The key names to press and keep down for a text/key/hotkey Action, e.g. ('w',)
    for a typed w, ('shift', 'w') for Shift+W, ('space',) for a typed space."""
    if action.kind == "hotkey":
        return tuple(action.keys)
    if action.kind == "key":
        return (action.value,)
    if action.value == " ":
        return ("space",)
    if len(action.value) == 1 and action.value.isupper():
        return ("shift", action.value.lower())      # a typed capital H = Shift + h
    return (action.value,)

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
        inp.click(spot.x + dx, spot.y + dy, button=button)
    else:
        print(f"Could not find {name}, clicking the recorded spot instead.")
        inp.click(fallback_x, fallback_y, button=button)


def wait_for_image(name):
    """Pause until the picture appears on screen; stop the macro if it never does."""
    if find_image(name, WAIT_TIMEOUT) is None:
        raise SystemExit(f"Stopped: {name} did not appear within {WAIT_TIMEOUT} seconds.")
'''

# Code pasted into every generated script: picks the library that does the clicking and typing.
# Everything the script does goes through "inp", so flipping BACKEND at the top is all it takes.
BACKEND_BLOCK = '''# ---------- Input library ("inp" below is whichever one BACKEND names) ----------
if BACKEND == "pydirectinput":
    try:
        import pydirectinput   # install with: pip install pydirectinput-rgx
    except ImportError:
        raise SystemExit('BACKEND is "pydirectinput" but it is not installed. Run: pip install pydirectinput-rgx')
    pydirectinput.FAILSAFE = True        # mouse into the TOP-LEFT corner = emergency stop
    pydirectinput.PAUSE = pyautogui.PAUSE   # same small pause as pyautogui (0 when the macro holds keys)

    class GameInput:
        """pydirectinput plus the tweaks games need (see Script4Script.md)."""

        def __getattr__(self, name):     # anything not defined below goes straight to pydirectinput
            return getattr(pydirectinput, name)

        def _move(self, x, y):
            """Move like a real mouse: some games ignore a pointer that jumps without moving."""
            if x is None:
                return
            pydirectinput.moveTo(x, y)
            if NUDGE:
                pydirectinput.moveRel(1, 0, relative=True)    # a tiny real-mouse-style move...
                pydirectinput.moveRel(-1, 0, relative=True)   # ...and back
            time.sleep(0.05)

        def click(self, x=None, y=None, button="left"):
            self._move(x, y)
            pydirectinput.click(button=button)

        def write(self, text, interval=0.0):
            pydirectinput.write(text, interval=interval, auto_shift=True)   # keeps capitals and symbols

        def press(self, key, presses=1, interval=0.0):
            for _ in range(presses):                      # a real key stays down a moment; games miss instant taps
                pydirectinput.keyDown(key)
                time.sleep(0.03)
                pydirectinput.keyUp(key)

        def hotkey(self, *keys):
            pydirectinput.hotkey(*keys, interval=0.02, wait=0.02)
            time.sleep(0.05)                              # so two shortcuts in a row don't run together

        def mouseDown(self, x=None, y=None, button="left"):
            self._move(x, y)
            pydirectinput.mouseDown(button=button)

        def mouseUp(self, x=None, y=None, button="left"):
            pydirectinput.mouseUp(button=button)          # released where the mouse already is

        def scroll(self, amount, x=None, y=None):
            self._move(x, y)
            pydirectinput.scroll(round(amount / 120))     # pyautogui counts 120 per notch, pydirectinput 1

        def hscroll(self, amount, x=None, y=None):
            self._move(x, y)
            pydirectinput.hscroll(round(amount / 120))

    inp = GameInput()
    FAILSAFE_ERRORS = (pyautogui.FailSafeException, pydirectinput.FailSafeException)
else:
    inp = pyautogui
    FAILSAFE_ERRORS = (pyautogui.FailSafeException,)
'''


def beep(freq, ms):
    """Short beep so you know something registered (skipped if BEEP is off)."""
    if BEEP and winsound:
        try:
            winsound.Beep(freq, ms)
        except Exception:
            pass   # no sound device: a missing beep must never crash a recording


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

    def to_dict(self):
        """Plain dict for the .jsonl recording (tuples become lists)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        """Rebuild an Action from one recording line. Raises ValueError if it is malformed."""
        if not isinstance(data, dict) or "kind" not in data or "time" not in data:
            raise ValueError("an action needs at least 'kind' and 'time'")
        if not isinstance(data["kind"], str) or data["kind"] not in KINDS:
            raise ValueError(f"unknown action kind {data['kind']!r}")
        values = {k: v for k, v in data.items() if k in {f.name for f in fields(cls)}}
        try:
            for name in ("time", "end_time"):
                if name in values:
                    values[name] = float(values[name])
            for name in ("x", "y", "x2", "y2", "amount", "img_dx", "img_dy"):
                if name in values:
                    values[name] = int(values[name])
            for name in ("keys", "mods"):
                if name in values:
                    values[name] = tuple(str(k) for k in values[name])
            for name in ("value", "image"):
                if name in values:
                    values[name] = str(values[name])
        except (TypeError, ValueError) as error:
            raise ValueError(f"bad field value ({error})") from error
        return cls(**values)


LOG_VERSION = 1   # bump this if the .jsonl layout ever changes


class ActionLog(list):
    """A list of Actions that also writes every new one to a .jsonl file straight away,
    so a crash or power cut loses nothing that was already recorded.
    Line 1 is a "meta" header; every other line is one action."""

    def __init__(self, path, meta):
        super().__init__()
        self._lock = threading.Lock()   # the mouse and keyboard threads both append
        self._file = open(path, "w", encoding="utf-8")
        self._write({"meta": meta})

    def _write(self, record):
        self._file.write(json.dumps(record) + "\n")
        self._file.flush()              # flush now, not at exit, so a crash loses nothing

    def append(self, action):
        with self._lock:
            super().append(action)
            self._write(action.to_dict())

    def close(self):
        with self._lock:
            self._file.close()


def load_log(path):
    """Read a .jsonl recording. Returns (meta dict, list of Actions).
    A cut-off last line (the recorder died mid-write) is skipped with a warning.
    Any other bad line raises ValueError that names the line number."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    last = max((i for i, text in enumerate(lines) if text.strip()), default=-1)
    meta, actions = None, []
    for i, text in enumerate(lines):
        if not text.strip():
            continue
        try:
            data = json.loads(text)
            if isinstance(data, dict) and "meta" in data:
                meta = data["meta"]
                if not isinstance(meta, dict) or meta.get("version") != LOG_VERSION:
                    raise ValueError(f"unsupported log version (this program reads version {LOG_VERSION})")
            elif meta is None:
                raise ValueError("the first line must be the 'meta' header (is this a macro recording?)")
            else:
                actions.append(Action.from_dict(data))
        except json.JSONDecodeError as error:
            if i == last and meta is not None:
                print(f"Warning: line {i + 1} is cut off (the recording was probably interrupted). Ignoring it.")
                break
            raise ValueError(f"line {i + 1}: not valid JSON ({error})") from error
        except ValueError as error:
            raise ValueError(f"line {i + 1}: {error}") from error
    if meta is None:
        raise ValueError("the file is empty or has no 'meta' header")
    return meta, actions


class Recorder:
    """Listens to the mouse and keyboard and collects Action objects."""

    def __init__(self):
        self.actions = []
        self.held = set()    # modifiers currently held down, e.g. {"ctrl"}
        self._press = None   # (x, y, time, mods) of the mouse button that is currently down
        # The script, recording and picture folder share this name, e.g. macro_2026-09-28_143005
        self.session = datetime.now().strftime("macro_%Y-%m-%d_%H%M%S")
        # Everything from one session lives in recordings/<session>/ (created when recording starts)
        self.folder = Path(__file__).parent / RECORDINGS_DIR / self.session
        self.image_dir = self.folder / f"{self.session}_images"
        self.log_path = self.folder / f"{self.session}.jsonl"
        self._mouse = mouse.Controller()   # lets us read the mouse position on F8
        self._marker_count = 0             # numbers the pictures: click_1.png, wait_2.png...
        self._pending_image = None         # (file, center_x, center_y) waiting for your next click
        # The mouse thread reads _pending_image while the keyboard thread sets it
        self._marker_lock = threading.Lock()
        self.paused = False                # Shift+F9 toggles this
        self._paused_at = 0.0              # when the current pause began
        self._pause_offset = 0.0           # total seconds spent paused so far
        # Keys that are down right now (not modifiers): key id -> {"start", "seen", "action"}.
        # Lets us skip auto-repeat presses and measure how long a key was held.
        self._down = {}

    @staticmethod
    def _key_id(key):
        """A name for a key that is the same on press and release. The Windows virtual-key code
        is best: it stays the same whatever Ctrl or Shift is doing (with Ctrl held, Backspace
        can arrive as Key.backspace on press and as the character '\\x7f' on release, and a
        press whose release looks different is mistaken for auto-repeat). Without a code, letters
        are mapped to the plain lowercase letter ('H' -> 'h', Ctrl+C's '\\x03' -> 'c')."""
        for source in (key, getattr(key, "value", None)):
            vk = getattr(source, "vk", None)
            if isinstance(vk, int) and not isinstance(vk, bool):
                return ("vk", vk)
        char = getattr(key, "char", None)
        if isinstance(char, str) and char:
            return Recorder._char_to_key(char) or char
        return key

    def _now(self):
        """Current time minus the time spent paused, so a pause never becomes a long sleep()."""
        return time.time() - self._pause_offset

    def _toggle_pause(self):
        """Shift+F9: stop or start recording without ending the session."""
        if self.paused:
            self._pause_offset += time.time() - self._paused_at
            self.paused = False
            print("  RESUMED - recording again.")
            beep_async(900, 150)
        else:
            self._paused_at = time.time()
            self.paused = True
            self._press = None                  # forget a half-finished click
            with self._marker_lock:
                self._pending_image = None      # and an unused F8 picture
            print("  PAUSED - nothing is recorded until you press Shift+F9 again.")
            beep_async(500, 150)

    def _current_mods(self):
        """Modifiers held right now in a fixed order, e.g. ('ctrl', 'shift')."""
        return tuple(m for m in MODIFIER_ORDER if m in self.held)

    def _reconcile_mods(self):
        """FIX B3: Drop modifiers from self.held that are NOT physically pressed.
        Called on every mouse press and every non-modifier key press, to clear phantom
        modifiers left behind by Alt+Tab, Win+L, etc. (Windows only)."""
        if sys.platform != "win32":
            return
        try:
            gaks = ctypes.windll.user32.GetAsyncKeyState
            real = {mod for vk, mod in _VK_MOD_MAP.items() if gaks(vk) & 0x8000}
            stale = self.held - real
            if stale:
                print(f"  [recorder] Cleared stale modifiers: {stale}")
            self.held &= real
        except Exception as exc:
            print(f"  [recorder] _reconcile_mods failed: {exc}")

    # ----- markers (F8 family) -----
    def _save_picture(self, purpose):
        """Screenshot the area around the mouse. Returns (file, center_x, center_y) or None.
        Runs right on the keyboard thread on purpose: a small screenshot takes well under
        100 ms, and doing it in the background lets your very next click arrive before the
        picture exists, so the click and the picture would not get paired up."""
        x, y = self._mouse.position
        screen_w, screen_h = pyautogui.size()
        # Center the picture on the mouse, but keep it fully on screen near the edges
        left = max(0, min(x - IMAGE_W // 2, screen_w - IMAGE_W))
        top = max(0, min(y - IMAGE_H // 2, screen_h - IMAGE_H))
        self._marker_count += 1
        name = f"{purpose}_{self._marker_count}.png"
        try:
            self.image_dir.mkdir(parents=True, exist_ok=True)
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
            with self._marker_lock:
                # FIX B6: warn if a previous F8 picture was never clicked
                if self._pending_image is not None:
                    print("  Warning: the previous F8 picture was never clicked and is now replaced.")
                self._pending_image = saved
            print(f"  F8: saved {saved[0]} - now click it.")
            beep_async(800, 100)

    def _marker_wait(self):
        """Shift+F8: save a picture and make the script wait until it appears."""
        now = self._now()
        saved = self._save_picture("wait")
        if saved:
            self.actions.append(Action("wait_image", now, image=saved[0]))
            print(f"  Shift+F8: saved {saved[0]} - the script will wait for it.")
            beep_async(1200, 100)

    def _marker_section(self):
        """Ctrl+F8: everything from here on goes into the next step_N() function."""
        self.actions.append(Action("section", self._now()))
        print("  Ctrl+F8: new step started.")
        beep_async(600, 100)

    # ----- mouse -----
    def _on_click(self, x, y, button, pressed):
        try:
            if self.paused:
                return                # Shift+F9 pause: nothing is recorded
            if pressed:
                # FIX B3: reconcile modifier state on every mouse press
                self._reconcile_mods()
                self._press = (x, y, self._now(), self._current_mods())
                return
            if self._press is None:
                return
            px, py, t0, mods = self._press
            self._press = None
            # FIX B9: consume pending image atomically (screenshot thread may write it)
            with self._marker_lock:
                pending, self._pending_image = self._pending_image, None

            if max(abs(x - px), abs(y - py)) >= DRAG_MIN_PIXELS:
                # Moved a lot = a drag from the press point to the release point
                if pending:
                    print("  The picture from F8 was not used (you dragged instead of clicking).")
                self.actions.append(Action("drag", t0, x=px, y=py, x2=x, y2=y,
                                           value=button.name, mods=mods, end_time=self._now()))
                return

            # Barely moved = a normal click (recorded at the press position)
            action = Action("click", t0, x=px, y=py, value=button.name, mods=mods,
                            end_time=self._now())   # release time: a long press is a hold
            if pending:
                name, cx, cy = pending
                if abs(px - cx) <= IMAGE_W // 2 and abs(py - cy) <= IMAGE_H // 2:
                    # Click landed inside the picture: turn it into an image click
                    action = replace(action, kind="image_click", image=name,
                                     img_dx=px - cx, img_dy=py - cy)
                else:
                    print("  Your click was outside the F8 picture, so it stays a normal click.")
            self.actions.append(action)
        except Exception as exc:
            # FIX B2/B12: surface errors; a bare raise would silently kill the listener
            print(f"  [recorder] _on_click error: {exc}")

    def _on_scroll(self, x, y, dx, dy):
        try:
            if self.paused:
                return
            # dy: +1 = one notch up, -1 = down.  dx: +1 = one notch right, -1 = left.
            now, mods = self._now(), self._current_mods()
            if dy:
                self.actions.append(Action("scroll", now, x=x, y=y, amount=int(dy), mods=mods))
            if dx:
                self.actions.append(Action("hscroll", now, x=x, y=y, amount=int(dx), mods=mods))
        except Exception as exc:
            print(f"  [recorder] _on_scroll error: {exc}")

    # ----- keyboard -----
    @staticmethod
    def _char_to_key(char):
        """Ctrl+letter arrives as a control code ('\\x03' = Ctrl+C); turn it back into 'c'."""
        if char == "\x7f":                  # Ctrl+Backspace
            return "backspace"
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
        try:
            name = getattr(key, "name", None)

            # A modifier going down: remember it, but don't record it by itself
            if name in MODIFIER_NAMES:
                self.held.add(MODIFIER_NAMES[name])
                return

            # Any other key: first forget modifiers that are not really held down any more
            self._reconcile_mods()

            # Holding a key makes the keyboard repeat it many times a second. Only the first
            # press counts; the repeats just prove the key is still down.
            now = self._now()
            kid = self._key_id(key)
            entry = self._down.get(kid)
            if entry is not None and now - entry["seen"] < REPEAT_WINDOW:
                entry["seen"] = now
                return
            entry = self._down[kid] = {"start": now, "seen": now, "action": None}

            if key == KEY_STOP:
                if "shift" in self.held:      # Shift+F9 = pause / resume
                    self._toggle_pause()
                    return
                return False          # returning False stops the keyboard listener
            if self.paused:
                return                # nothing is recorded while paused
            if key == KEY_MARKER:     # the modifiers tell us which marker it is
                if "shift" in self.held:
                    self._marker_wait()
                elif "ctrl" in self.held:
                    self._marker_section()
                else:
                    self._marker_image_click()
                return

            before = len(self.actions)
            self._record_key(key, name, now)
            if len(self.actions) > before:
                entry["action"] = self.actions[-1]    # remembered so the release can add a hold
        except Exception as exc:
            # FIX B2/B12: never let an exception escape a pynput callback silently
            print(f"  [recorder] _on_press error: {exc}")

    def _record_key(self, key, name, now):
        """Turn one fresh key press into a text / key / hotkey Action."""
        # FIX B4/B21: AltGr on Windows synthesises Ctrl_L + Alt_R key-downs,
        # so self.held already has {"ctrl", "alt"} when the AltGr keycode fires.
        # Drop alt_gr itself; the real character (e.g. '€') arrives as a separate
        # KeyCode event handled below.
        if name == "alt_gr":
            return

        # Shift alone is not a "combo" for letters (Shift+a already arrives as "A")
        combo = self.held & {"ctrl", "alt", "win"}
        char = getattr(key, "char", None)

        # Normal letters, digits, symbols
        if char is not None:
            # FIX B21: AltGr characters (e.g. '€' on EU keyboards) arrive with
            # combo == {"ctrl", "alt"} due to the synthetic keys above.
            # Treat them as plain text, not as Ctrl+Alt hotkeys.
            if combo == {"ctrl", "alt"} and char.isprintable() and ord(char) > 127:
                self.actions.append(Action("text", now, value=char))
                return
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
            # Keys with no .char and no .name (rare hardware-specific keys): skip silently.
            return
        key_name = KEY_NAMES.get(name, name)
        if self.held:                              # e.g. Shift+Tab, Alt+Tab
            self._add_hotkey(now, key_name)
        else:
            self.actions.append(Action("key", now, value=key_name))

    def _on_release(self, key):
        try:
            name = getattr(key, "name", None)
            if name in MODIFIER_NAMES:
                self.held.discard(MODIFIER_NAMES[name])
                return
            entry = self._down.pop(self._key_id(key), None)
            if entry is None or entry["action"] is None or self.paused:
                return
            now = self._now()
            keys = keys_to_hold(entry["action"])
            if now - entry["start"] >= hold_min(keys):
                # Held a while (walking with W, sprinting with Shift+W...): add a "hold" that
                # replaces the quick press recorded when the key went down.
                self.actions.append(Action("hold", entry["start"], end_time=now, keys=keys))
        except Exception as exc:
            print(f"  [recorder] _on_release error: {exc}")

    # ----- run -----
    @staticmethod
    def _check_screenshots():
        """Take one tiny screenshot before recording. It warms things up so the first F8 is
        fast, and reports a missing Pillow now instead of in the middle of your recording."""
        try:
            pyautogui.screenshot(region=(0, 0, 10, 10))
        except Exception as error:
            print(f"WARNING: screenshots do not work ({error}). F8 markers will fail. "
                  "Try: pip install pillow")

    def run(self):
        """Record until F9 is pressed, then return the list of actions."""
        print("NOTE: everything you type is saved as plain text. "
              "Press Shift+F9 to pause before typing a password.")
        self._check_screenshots()
        for i in range(START_DELAY, 0, -1):
            print(f"Recording starts in {i}... (switch to your target window)")
            time.sleep(1)

        meta = {"version": LOG_VERSION, "session": self.session,
                "created": datetime.now().isoformat(timespec="seconds"),
                "screen": [int(v) for v in pyautogui.size()]}
        self.folder.mkdir(parents=True, exist_ok=True)
        self.actions = ActionLog(self.log_path, meta)   # every action is also saved to disk
        beep(1000, 200)                              # high beep = recording started
        print("Recording! Do your task now.")
        print("  F9 = stop | Shift+F9 = pause/resume | F8 = image click | "
              "Shift+F8 = wait for image | Ctrl+F8 = new step")

        mouse_listener = mouse.Listener(on_click=self._on_click, on_scroll=self._on_scroll)
        mouse_listener.start()                       # runs in a background thread
        try:
            with keyboard.Listener(on_press=self._on_press,
                                   on_release=self._on_release) as kb_listener:
                kb_listener.join()                   # waits here until F9
        finally:
            mouse_listener.stop()
            self.actions.close()
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

    @staticmethod
    def _typing_times(actions):
        """(time, keys) of every typed character that belongs to ordinary typing, like the
        letters of "Hello World". A burst of typed characters (each within MERGE_GAP of the
        last) is typing when it has two non-movement characters side by side ("he", "or").
        A burst of only W/A/S/D/arrows/space is movement, so those keys keep their timing."""
        plain = lambda a: len(a.value) == 1 and a.value.lower() not in GAME_KEYS and a.value != " "
        texts = sorted((a for a in actions if a.kind == "text"), key=lambda a: a.time)
        found, run = set(), []
        for a in texts + [None]:
            if a is not None and (not run or a.time - run[-1].time <= MERGE_GAP):
                run.append(a)
                continue
            if any(plain(x) and plain(y) for x, y in zip(run, run[1:])):
                found |= {(x.time, keys_to_hold(x)) for x in run}
            run = [a] if a is not None else []
        return found

    def _apply_holds(self, actions):
        """Turn holds into separate down / up events on the timeline.
        A key held HOLD_MIN seconds or more was recorded twice: a quick press when it went
        down, and a "hold" when it came up. The quick press is dropped and the hold becomes a
        keydown at its start and a keyup at its end. A mouse press that long gets the same
        mousedown / mouseup treatment. Because the down and up are separate, holds can overlap
        (W still down while D goes down), which is what walking in a circle needs."""
        typing = self._typing_times(actions)
        # Quick taps inside ordinary typing stay plain text; only long holds are kept there
        keep = lambda a: (a.end_time - a.time >= hold_min(a.keys)
                          and ((a.time, tuple(a.keys)) not in typing or a.end_time - a.time >= HOLD_MIN))
        holds = {(a.time, tuple(a.keys)) for a in actions if a.kind == "hold" and keep(a)}
        out = []
        for a in actions:
            if a.kind == "hold":
                if keep(a):
                    out += [Action("keydown", a.time, keys=a.keys),
                            Action("keyup", a.end_time, keys=a.keys)]
            elif a.kind in ("text", "key", "hotkey") and (a.time, keys_to_hold(a)) in holds:
                continue                                  # the quick press the hold replaces
            elif a.kind == "click" and a.end_time - a.time >= HOLD_MIN:
                out += [Action("mousedown", a.time, x=a.x, y=a.y, value=a.value, mods=a.mods),
                        Action("mouseup", a.end_time, x=a.x, y=a.y, value=a.value, mods=a.mods)]
            else:
                out.append(a)
        # By time; at the same instant a release goes before a press (stable sort keeps the rest)
        return sorted(out, key=lambda a: (a.time, 0 if a.kind in ("keyup", "mouseup") else 1))

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
        """The input line(s) for one Action (without modifiers or comment)."""
        # Text from a recording is turned into code with repr(), so odd characters in a
        # recording (or a hand-edited .jsonl) can never break out of the quotes.
        if a.kind == "keydown":
            return [f"inp.keyDown({k!r})" for k in a.keys]
        if a.kind == "keyup":
            return [f"inp.keyUp({k!r})" for k in reversed(a.keys)]
        if a.kind == "mousedown":
            return [f"inp.mouseDown({a.x}, {a.y}, button={a.value!r})"]
        if a.kind == "mouseup":
            return [f"inp.mouseUp({a.x}, {a.y}, button={a.value!r})"]
        if a.kind == "click":
            if a.value == "left":
                return [f"inp.click({a.x}, {a.y})"]
            return [f"inp.click({a.x}, {a.y}, button={a.value!r})"]
        if a.kind == "image_click":
            extra = "" if a.value == "left" else f", button={a.value!r}"
            return [f"click_image({a.image!r}, {a.img_dx}, {a.img_dy}, {a.x}, {a.y}{extra})"]
        if a.kind == "wait_image":
            return [f"wait_for_image({a.image!r})"]
        if a.kind == "drag":
            duration = max(0.3, round(a.end_time - a.time, 1))   # too fast a drag can fail
            return [f"inp.moveTo({a.x}, {a.y})",
                    f"inp.dragTo({a.x2}, {a.y2}, duration={duration} / SPEED, "
                    f"button={a.value!r})"]
        if a.kind == "scroll":
            return [f"inp.scroll({a.amount * SCROLL_UNIT}, x={a.x}, y={a.y})"]
        if a.kind == "hscroll":
            return [f"inp.hscroll({a.amount * SCROLL_UNIT}, x={a.x}, y={a.y})"]
        if a.kind == "hotkey":
            return [f"inp.hotkey({', '.join(repr(k) for k in a.keys)})"]
        if a.kind == "text":
            return [f"inp.write({a.value!r}, interval=0.05)"]
        return [f"inp.press({a.value!r})"]

    def _lines_for(self, a):
        """Full code for one Action: hold modifiers, do it, release them, add a comment."""
        down = [f"inp.keyDown({m!r})" for m in a.mods] if a.kind != "mouseup" else []
        up = [f"inp.keyUp({m!r})" for m in reversed(a.mods)] if a.kind != "mousedown" else []
        lines = down + self._core_code(a) + up       # Ctrl held through a mousedown ... mouseup
        label = LABELS[a.kind]
        if a.mods:
            label = "+".join(a.mods) + "+" + label   # e.g. "ctrl+click"
        clock = datetime.fromtimestamp(a.time).strftime("%H:%M:%S")
        lines[0] += f"  # {label} at {clock}"
        return lines

    def _pause(self, a, prev_end, precise=False):
        """A time.sleep line for the gap before `a`. None if there is no previous action,
        if the gap is under MIN_WAIT, or if `a` is a wait-for-image (it waits by itself).
        The gap AFTER a wait-for-image is kept: it is the time you took to react once the
        picture appeared, and the replay should give the screen the same time to settle.
        precise=True (next to a key/button down or up) keeps gaps as small as EVENT_MIN_WAIT
        and writes two decimals, so overlapping holds keep their real timing."""
        if prev_end is None or a.kind == "wait_image":
            return None
        gap = a.time - prev_end
        if precise:
            return f"time.sleep({gap:.2f} / SPEED)" if gap >= EVENT_MIN_WAIT else None
        return f"time.sleep({gap:.1f} / SPEED)" if gap >= MIN_WAIT else None

    def _step_body(self, step):
        """Code lines for one step's actions, with sleeps between them."""
        body, prev_end, prev = [], None, None
        for a in step:
            precise = a.kind in EVENT_KINDS or (prev is not None and prev.kind in EVENT_KINDS)
            pause = self._pause(a, prev_end, precise)
            if pause:
                body.append(pause)
            body += self._lines_for(a)
            prev_end = a.end_time if prev_end is None else max(prev_end, a.end_time)
            prev = a
        return body

    def build(self, actions, session, screen=None, backend=BACKEND):
        """Return the full text of the generated script.
        `screen` is the (width, height) it was recorded on; the script warns if it differs.
        `backend` is the library the script starts with ("pydirectinput" or "pyautogui");
        the script contains both and the BACKEND line at its top flips between them."""
        if backend not in BACKENDS:
            raise ValueError(f"backend must be one of {BACKENDS}, not {backend!r}")
        # Sort by start time because a click/drag is saved when the button is released
        merged = self._merge(sorted(self._apply_holds(actions), key=lambda a: a.time))
        steps = self._split_steps(merged)
        uses_images = any(a.kind in ("image_click", "wait_image") for a in merged)
        uses_events = any(a.kind in EVENT_KINDS for a in merged)

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
            f'BACKEND = "{backend}"   # "pydirectinput" works in games (Roblox...); "pyautogui" is for normal apps',
            "SPEED = 1.0      # 2.0 = twice as fast, 0.5 = half speed (divides every wait)",
            "REPEAT = 1       # how many times to run the whole macro",
            "COUNTDOWN = 3    # seconds to wait before it starts (time to switch windows)",
            "NUDGE = True     # games: wiggle the mouse 1 px after moving it so the game notices (try False if clicks misbehave)",
            "",
            "pyautogui.FAILSAFE = True        # mouse into a screen corner = emergency stop",
            ("pyautogui.PAUSE = 0               # no extra pause: held keys must keep their exact timing"
             if uses_events else
             "pyautogui.PAUSE = 0.1 / SPEED    # pyautogui's built-in pause after each action"),
            "",
            BACKEND_BLOCK.rstrip("\n"),
        ]
        if screen:
            width, height = (int(v) for v in screen)
            lines += ["", f"RECORDED_SCREEN = ({width}, {height})   # screen size when this was recorded"]
        if uses_images:
            lines += ["", IMAGE_HELPERS.replace('"__FOLDER__"', repr(f"{session}_images")).rstrip("\n")]

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

        lines += ["", "", 'if __name__ == "__main__":']
        if screen:
            lines += [
                "    if tuple(pyautogui.size()) != RECORDED_SCREEN:",
                '        print(f"WARNING: this screen is {tuple(pyautogui.size())} but the macro was "',
                '              f"recorded on {RECORDED_SCREEN}. Clicks may land in the wrong place.")',
            ]
        lines += [
            '    print(f"Starting in {COUNTDOWN} seconds... (mouse into a screen corner to stop)")',
            "    time.sleep(COUNTDOWN)",
            "    try:",
            "        for i in range(REPEAT):",
            '            print(f"Run {i + 1} of {REPEAT}")',
            "            run_macro()",
            '        print("Done.")',
            "    except FAILSAFE_ERRORS:",
            '        print("Stopped: the mouse hit the emergency-stop corner.")',
        ]
        return "\n".join(lines) + "\n"

    def save(self, actions, session, folder=None, suffix="", screen=None, backend=BACKEND):
        """Write the script (default: next to this file) and return its path. record() passes
        the session's own folder in recordings/.
        The pictures folder must sit next to the script, so `folder` is where both live."""
        folder = Path(folder) if folder else Path(__file__).parent
        path = folder / f"{session}{suffix}.py"
        path.write_text(self.build(actions, session, screen, backend), encoding="utf-8")
        return path


def record(backend=BACKEND):
    """Record a new macro, save the recording and the script, offer to replay."""
    recorder = Recorder()
    actions = recorder.run()
    print(f"Raw recording saved to: {recorder.log_path}")
    if not actions:
        print("Nothing was recorded, so no script was made.")
        return
    path = ScriptWriter().save(actions, recorder.session, folder=recorder.folder,
                               screen=pyautogui.size(), backend=backend)
    print(f"Saved {len(actions)} actions to: {path}")
    print(f"The script replays with {backend}. To switch, change BACKEND at the top of the script.")
    if recorder.image_dir.exists():
        print(f"Pictures are in: {recorder.image_dir}  (keep this folder next to the script)")

    print(f"Replay it with:  python \"{path}\"")


def generate(log_file, backend=BACKEND):
    """Rebuild a script from a saved .jsonl recording, without recording again."""
    log_file = Path(log_file)
    meta, actions = load_log(log_file)
    session = Path(str(meta.get("session") or log_file.stem)).name   # names the pictures folder
    if any(a.kind in ("image_click", "wait_image") for a in actions) \
            and not (log_file.parent / f"{session}_images").exists():
        print(f"Warning: the pictures folder {session}_images was not found next to the "
              "recording. The script will stop when it needs a picture.")
    path = ScriptWriter().save(actions, session, folder=log_file.parent, suffix="_regen",
                               screen=meta.get("screen"), backend=backend)
    print(f"Wrote {len(actions)} actions to: {path}")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Record your mouse and keyboard and turn them into a replay script.")
    parser.add_argument("--backend", choices=BACKENDS, default=BACKEND,
                        help=f"library the script replays with (default: {BACKEND})")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("record", help="record a new macro (this is the default)")
    gen = commands.add_parser("generate", help="rebuild a script from a saved .jsonl recording")
    gen.add_argument("log", help="path to a macro_*.jsonl recording")
    gen.add_argument("--backend", choices=BACKENDS, default=argparse.SUPPRESS,
                     help="library the script replays with")
    args = parser.parse_args(argv)

    if args.command == "generate":
        try:
            generate(args.log, backend=args.backend)
        except (OSError, ValueError) as error:
            sys.exit(f"Error: {error}")
    else:
        record(backend=args.backend)


if __name__ == "__main__":
    main()
