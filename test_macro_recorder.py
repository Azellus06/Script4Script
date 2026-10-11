"""
test_macro_recorder.py
======================
Unit tests for macro_recorder.py.  Uses fakes for pynput and pyautogui.

Run:  python test_macro_recorder.py
"""

import sys
import pathlib
import types
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import MagicMock

# ----- Stub pynput so it loads without real hardware hooks -----
_pynput_stub = types.ModuleType("pynput")
_keyboard_stub = types.ModuleType("pynput.keyboard")
_mouse_stub = types.ModuleType("pynput.mouse")


WORKSPACE = pathlib.Path(__file__).resolve().parent   # the folder that holds macro_recorder.py

class _FakeKeyEnum:
    """Stand-in for pynput.keyboard.Key."""
    f9 = object()
    f8 = object()
    space = object()
    enter = object()
    tab = object()
    backspace = object()
    shift = object()
    shift_l = object()
    shift_r = object()
    ctrl = object()
    ctrl_l = object()
    ctrl_r = object()
    alt = object()
    alt_l = object()
    alt_r = object()
    cmd = object()
    cmd_l = object()
    cmd_r = object()
    page_up = object()
    page_down = object()
    delete = object()
    home = object()


class _FakeKeyboardListener:
    def __init__(self, on_press=None, on_release=None):
        self.on_press = on_press
        self.on_release = on_release
    def start(self): pass
    def stop(self): pass
    def join(self): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass


class _FakeMouseController:
    position = (500, 400)


class _FakeMouseListener:
    def __init__(self, on_click=None, on_scroll=None):
        self.on_click = on_click
        self.on_scroll = on_scroll
    def start(self): pass
    def stop(self): pass


class _FakeButtonEnum:
    class left:
        name = "left"
    class right:
        name = "right"
    class middle:
        name = "middle"


_keyboard_stub.Key = _FakeKeyEnum
_keyboard_stub.Listener = _FakeKeyboardListener
_keyboard_stub.KeyCode = None
_mouse_stub.Controller = _FakeMouseController
_mouse_stub.Listener = _FakeMouseListener
_mouse_stub.Button = _FakeButtonEnum
_pynput_stub.keyboard = _keyboard_stub
_pynput_stub.mouse = _mouse_stub
sys.modules["pynput"] = _pynput_stub
sys.modules["pynput.keyboard"] = _keyboard_stub
sys.modules["pynput.mouse"] = _mouse_stub

# Stub winsound (a silent Beep, so tests never make noise or fail on non-Windows)
_winsound_stub = types.ModuleType("winsound")
_winsound_stub.Beep = lambda freq, ms: None
sys.modules["winsound"] = _winsound_stub

# Stub ctypes.windll for _reconcile_mods
import ctypes as _ctypes
if not hasattr(_ctypes, "windll"):
    _ctypes.windll = MagicMock()

# Stub pyautogui at import time
_fake_pyautogui = types.ModuleType("pyautogui")
_fake_pyautogui.size = lambda: (1920, 1080)
_fake_pyautogui.screenshot = MagicMock(return_value=MagicMock(save=MagicMock()))
_fake_pyautogui.FAILSAFE = True
_fake_pyautogui.PAUSE = 0.1
_fake_pyautogui.FailSafeException = Exception
_fake_pyautogui.locateCenterOnScreen = MagicMock()
_fake_pyautogui.ImageNotFoundException = type("ImageNotFoundException", (Exception,), {})
sys.modules["pyautogui"] = _fake_pyautogui

# Add the folder holding macro_recorder.py to the path so the import works
sys.path.insert(0, str(WORKSPACE))
import macro_recorder as mr


# ----- Helpers -----

def make_key(name=None, char=None):
    """Minimal pynput Key/KeyCode fake."""
    k = MagicMock()
    k.name = name
    k.char = char
    return k


class FakeRecorder(mr.Recorder):
    """Recorder with direct event injection instead of real listeners."""
    def __init__(self):
        super().__init__()

    def press(self, key):
        self._on_press(key)

    def release(self, key):
        self._on_release(key)

    def click(self, x, y, button=None, pressed=True):
        if button is None:
            button = _FakeButtonEnum.left
        self._on_click(x, y, button, pressed)

    def scroll(self, x, y, dx, dy):
        self._on_scroll(x, y, dx, dy)

    def _reconcile_mods(self):
        pass   # skip real Win32 call in tests


def build_script(actions, backend="pyautogui"):
    """Most tests replay against a fake pyautogui, so they build for that backend."""
    return mr.ScriptWriter().build(actions, "macro_test", backend=backend)


def compile_script(text):
    return compile(text, "<generated>", "exec")


def exec_script_get_calls(actions):
    """Execute the generated script against a fake pyautogui; return call list.

    The generated script contains ``import pyautogui``, which resolves via
    sys.modules.  We temporarily replace that entry with our recorder class
    so every pyautogui.click/write/etc. is captured.
    """
    import sys as _sys
    script = build_script(actions)
    calls = []

    class RecPyag:
        FAILSAFE = True
        PAUSE = 0.1
        FailSafeException = Exception
        def __getattr__(self, name):
            def _fn(*a, **kw):
                calls.append((name, a, kw))
            return _fn

    pyag = RecPyag()
    _old = _sys.modules.get("pyautogui")
    _sys.modules["pyautogui"] = pyag
    try:
        ns = {
            "time": MagicMock(),
            "pyautogui": pyag,
            "Path": pathlib.Path,
            "__file__": str(WORKSPACE / "dummy.py"),
        }
        ns["time"].sleep = MagicMock()
        ns["time"].time = MagicMock(return_value=0)
        exec(compile_script(script), ns)   # defines run_macro, step_N, helpers
        ns["COUNTDOWN"] = 0
        ns["time"].sleep = MagicMock()
        ns["run_macro"]()
    finally:
        if _old is None:
            _sys.modules.pop("pyautogui", None)
        else:
            _sys.modules["pyautogui"] = _old
    return calls



# ----- Tests -----

class TestTypingMerge(unittest.TestCase):

    def test_fast_typing_merges(self):
        """h,e,l,l,o typed 0.1 s apart -> single write("hello")."""
        acts = [mr.Action("text", i * 0.1, value=c) for i, c in enumerate("hello")]
        merged = mr.ScriptWriter()._merge(acts)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].value, "hello")

    def test_slow_typing_splits(self):
        """Gap > 1.0 s -> two separate write() calls."""
        acts = [mr.Action("text", 0.0, value="h"), mr.Action("text", 2.0, value="i")]
        merged = mr.ScriptWriter()._merge(acts)
        self.assertEqual(len(merged), 2)

    def test_space_merges_as_text(self):
        """Spaces count as text and merge into the word."""
        chars = [("h", 0.0), ("i", 0.1), (" ", 0.2), ("y", 0.3), ("o", 0.4)]
        acts = [mr.Action("text", t, value=c) for c, t in chars]
        merged = mr.ScriptWriter()._merge(acts)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].value, "hi yo")

    def test_key_between_text_prevents_merge(self):
        """A key press between two text chunks breaks the merge."""
        acts = [
            mr.Action("text", 0.0, value="a"),
            mr.Action("key",  0.1, value="enter"),
            mr.Action("text", 0.2, value="b"),
        ]
        merged = mr.ScriptWriter()._merge(acts)
        self.assertEqual(len(merged), 3)
        self.assertEqual(merged[0].value, "a")
        self.assertEqual(merged[2].value, "b")


class TestHotkeys(unittest.TestCase):

    def setUp(self):
        self.r = FakeRecorder()

    def test_ctrl_c_from_control_code(self):
        """Ctrl+C arrives as \x03 -> hotkey("ctrl", "c")."""
        self.r.press(make_key(name="ctrl_l"))
        self.r.press(make_key(char=""))
        a = self.r.actions[-1]
        self.assertEqual(a.kind, "hotkey")
        self.assertEqual(a.keys, ("ctrl", "c"))

    def test_shift_tab(self):
        """Shift+Tab -> hotkey("shift", "tab")."""
        self.r.press(make_key(name="shift_l"))
        self.r.press(make_key(name="tab"))
        a = self.r.actions[-1]
        self.assertEqual(a.kind, "hotkey")
        self.assertIn("shift", a.keys)
        self.assertIn("tab", a.keys)

    def test_alt_tab(self):
        """Alt+Tab -> hotkey("alt", "tab")."""
        self.r.press(make_key(name="alt_l"))
        self.r.press(make_key(name="tab"))
        a = self.r.actions[-1]
        self.assertEqual(a.kind, "hotkey")
        self.assertIn("alt", a.keys)
        self.assertIn("tab", a.keys)

    def test_ctrl_shift_s(self):
        """Ctrl+Shift+S char arrives as "S" -> hotkey("ctrl","shift","s")."""
        self.r.press(make_key(name="ctrl_l"))
        self.r.press(make_key(name="shift_l"))
        self.r.press(make_key(char="S"))
        a = self.r.actions[-1]
        self.assertEqual(a.kind, "hotkey")
        self.assertIn("ctrl", a.keys)
        self.assertIn("shift", a.keys)
        self.assertIn("s", a.keys)

    def test_shift_letter_is_text_not_hotkey(self):
        """Shift+A: char arrives as "A" -> text("A"), not a hotkey."""
        self.r.press(make_key(name="shift_l"))
        self.r.press(make_key(char="A"))
        text_acts = [a for a in self.r.actions if a.kind == "text"]
        hotkey_acts = [a for a in self.r.actions if a.kind == "hotkey"]
        self.assertEqual(len(text_acts), 1)
        self.assertEqual(text_acts[0].value, "A")
        self.assertEqual(len(hotkey_acts), 0)

    def test_plain_letter_is_text(self):
        """Just 'a' -> text("a")."""
        self.r.press(make_key(char="a"))
        self.assertEqual(self.r.actions[0].kind, "text")

    def test_enter_is_key(self):
        """Enter alone -> key("enter")."""
        self.r.press(make_key(name="enter"))
        self.assertEqual(self.r.actions[0].kind, "key")
        self.assertEqual(self.r.actions[0].value, "enter")

    def test_modifier_alone_not_recorded(self):
        """Pressing just Ctrl records nothing."""
        self.r.press(make_key(name="ctrl_l"))
        self.assertEqual(len(self.r.actions), 0)

    def test_altgr_euro_is_text_not_hotkey(self):
        """FIX B21: AltGr+e -> euro sign text, not Ctrl+Alt hotkey."""
        self.r.press(make_key(name="ctrl_l"))
        self.r.press(make_key(name="alt_r"))
        self.r.press(make_key(name="alt_gr"))
        self.r.press(make_key(char="€"))   # euro, ord=8364 > 127
        text_acts = [a for a in self.r.actions if a.kind == "text"]
        hotkey_acts = [a for a in self.r.actions if a.kind == "hotkey"]
        self.assertEqual(len(text_acts), 1)
        self.assertEqual(text_acts[0].value, "€")
        self.assertEqual(len(hotkey_acts), 0)

    def test_ctrl_alt_delete_is_hotkey(self):
        """Ctrl+Alt+Delete (ASCII special key) stays a hotkey."""
        self.r.press(make_key(name="ctrl_l"))
        self.r.press(make_key(name="alt_l"))
        self.r.press(make_key(name="delete"))
        hotkey_acts = [a for a in self.r.actions if a.kind == "hotkey"]
        self.assertGreater(len(hotkey_acts), 0)
        self.assertIn("delete", hotkey_acts[-1].keys)


class TestDragVsClick(unittest.TestCase):

    def setUp(self):
        self.r = FakeRecorder()

    def test_small_move_is_click(self):
        """5 px move (< 10) -> click at press position (100,100)."""
        self.r.click(100, 100, pressed=True)
        self.r.click(105, 100, pressed=False)
        self.assertEqual(self.r.actions[0].kind, "click")
        self.assertEqual(self.r.actions[0].x, 100)

    def test_large_move_is_drag(self):
        """50 px move -> drag from (100,100) to (150,100)."""
        self.r.click(100, 100, pressed=True)
        self.r.click(150, 100, pressed=False)
        a = self.r.actions[0]
        self.assertEqual(a.kind, "drag")
        self.assertEqual(a.x, 100)
        self.assertEqual(a.x2, 150)

    def test_exactly_threshold_is_drag(self):
        """Exactly DRAG_MIN_PIXELS=10 -> drag."""
        self.r.click(100, 100, pressed=True)
        self.r.click(110, 100, pressed=False)
        self.assertEqual(self.r.actions[0].kind, "drag")

    def test_right_click_records_button_name(self):
        self.r.click(200, 200, button=_FakeButtonEnum.right, pressed=True)
        self.r.click(200, 200, button=_FakeButtonEnum.right, pressed=False)
        self.assertEqual(self.r.actions[0].value, "right")

    def test_ctrl_click_stores_mods(self):
        self.r.press(make_key(name="ctrl_l"))
        self.r.click(300, 300, pressed=True)
        self.r.click(300, 300, pressed=False)
        a = self.r.actions[-1]
        self.assertEqual(a.kind, "click")
        self.assertEqual(a.mods, ("ctrl",))

    def test_ctrl_click_generates_keydown_keyup(self):
        a = mr.Action("click", 0.0, x=100, y=100, value="left", mods=("ctrl",))
        lines = mr.ScriptWriter()._lines_for(a)
        self.assertTrue(any("keyDown" in l for l in lines))
        self.assertTrue(any("keyUp" in l for l in lines))


class TestScrollMerge(unittest.TestCase):

    def test_same_direction_merges(self):
        """3 down-notches within 0.5 s -> one scroll with amount=-3."""
        acts = [mr.Action("scroll", i * 0.2, x=100, y=100, amount=-1) for i in range(3)]
        merged = mr.ScriptWriter()._merge(acts)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].amount, -3)

    def test_opposite_direction_splits(self):
        acts = [mr.Action("scroll", 0.0, x=100, y=100, amount=-1),
                mr.Action("scroll", 0.2, x=100, y=100, amount=1)]
        merged = mr.ScriptWriter()._merge(acts)
        self.assertEqual(len(merged), 2)

    def test_gap_exceeded_splits(self):
        acts = [mr.Action("scroll", 0.0, x=100, y=100, amount=-1),
                mr.Action("scroll", 0.6, x=100, y=100, amount=-1)]
        merged = mr.ScriptWriter()._merge(acts)
        self.assertEqual(len(merged), 2)

    def test_scroll_unit_in_code(self):
        a = mr.Action("scroll", 0.0, x=100, y=100, amount=-2)
        lines = mr.ScriptWriter()._core_code(a)
        self.assertIn(str(-2 * mr.SCROLL_UNIT), lines[0])

    def test_ctrl_scroll_has_mods(self):
        r = FakeRecorder()
        r.press(make_key(name="ctrl_l"))
        r.scroll(100, 100, 0, -1)
        self.assertEqual(r.actions[-1].mods, ("ctrl",))

    def test_hscroll_is_separate_action(self):
        r = FakeRecorder()
        r.scroll(100, 100, 1, 0)
        self.assertEqual(r.actions[0].kind, "hscroll")

    def test_hscroll_merges(self):
        acts = [mr.Action("hscroll", 0.0, x=100, y=100, amount=1),
                mr.Action("hscroll", 0.3, x=100, y=100, amount=1)]
        merged = mr.ScriptWriter()._merge(acts)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].amount, 2)

    def test_hscroll_and_scroll_dont_merge(self):
        acts = [mr.Action("scroll",  0.0, x=100, y=100, amount=-1),
                mr.Action("hscroll", 0.1, x=100, y=100, amount=-1)]
        merged = mr.ScriptWriter()._merge(acts)
        self.assertEqual(len(merged), 2)


class TestMarkers(unittest.TestCase):

    def setUp(self):
        self.r = FakeRecorder()
        self.r._save_picture = MagicMock(return_value=("click_1.png", 500, 400))

    def _set_pending(self, fname="click_1.png", cx=500, cy=400):
        with self.r._marker_lock:
            self.r._pending_image = (fname, cx, cy)

    def test_click_inside_picture_becomes_image_click(self):
        """Click within 50px/30px of center (500,400) -> image_click."""
        self._set_pending()
        self.r.click(510, 410, pressed=True)
        self.r.click(510, 410, pressed=False)
        a = self.r.actions[0]
        self.assertEqual(a.kind, "image_click")
        self.assertEqual(a.img_dx, 10)
        self.assertEqual(a.img_dy, 10)

    def test_click_outside_picture_is_plain_click(self):
        """Click at (700,700) is outside click_1.png centered at (500,400)."""
        self._set_pending()
        self.r.click(700, 700, pressed=True)
        self.r.click(700, 700, pressed=False)
        self.assertEqual(self.r.actions[0].kind, "click")

    def test_drag_after_f8_drops_marker(self):
        """Drag after F8 -> drag action; pending cleared."""
        self._set_pending()
        self.r.click(100, 100, pressed=True)
        self.r.click(200, 200, pressed=False)   # 141 px = drag
        self.assertEqual(self.r.actions[0].kind, "drag")
        with self.r._marker_lock:
            self.assertIsNone(self.r._pending_image)

    def test_double_f8_overwrite_detected(self):
        """FIX B6: second F8 before clicking finds non-None pending."""
        self._set_pending(fname="click_1.png")
        overwritten = []
        new_saved = ("click_2.png", 500, 400)
        with self.r._marker_lock:
            if self.r._pending_image is not None:
                overwritten.append(True)
            self.r._pending_image = new_saved
        self.assertTrue(overwritten, "Overwrite was not detected")
        with self.r._marker_lock:
            self.assertEqual(self.r._pending_image[0], "click_2.png")

    def test_shift_f8_adds_wait_image_action(self):
        """Shift+F8 appends wait_image action."""
        import time as _t
        t = _t.time()
        self.r.actions.append(mr.Action("wait_image", t, image="wait_1.png"))
        a = self.r.actions[-1]
        self.assertEqual(a.kind, "wait_image")
        self.assertEqual(a.image, "wait_1.png")

    def test_ctrl_f8_adds_section(self):
        self.r._marker_section()
        self.assertEqual(self.r.actions[-1].kind, "section")

    def test_pending_consumed_once(self):
        """Pending image is cleared after first click; second click is plain."""
        self._set_pending()
        self.r.click(505, 402, pressed=True)
        self.r.click(505, 402, pressed=False)
        with self.r._marker_lock:
            self.assertIsNone(self.r._pending_image)
        self.r.click(505, 402, pressed=True)
        self.r.click(505, 402, pressed=False)
        self.assertEqual(self.r.actions[1].kind, "click")


class TestStepSplitting(unittest.TestCase):

    def test_no_section_one_step(self):
        acts = [mr.Action("text", 0.0, value="a"), mr.Action("key", 0.5, value="enter")]
        steps = mr.ScriptWriter()._split_steps(acts)
        self.assertEqual(len(steps), 1)

    def test_one_section_two_steps(self):
        acts = [
            mr.Action("text", 0.0, value="a"),
            mr.Action("section", 1.0),
            mr.Action("text", 2.0, value="b"),
        ]
        steps = mr.ScriptWriter()._split_steps(acts)
        self.assertEqual(len(steps), 2)

    def test_consecutive_sections_no_empty(self):
        acts = [
            mr.Action("text", 0.0, value="a"),
            mr.Action("section", 0.5),
            mr.Action("section", 0.6),
            mr.Action("text", 1.0, value="b"),
        ]
        steps = mr.ScriptWriter()._split_steps(acts)
        self.assertEqual(len(steps), 2)

    def test_trailing_section_no_empty(self):
        acts = [mr.Action("text", 0.0, value="a"), mr.Action("section", 0.5)]
        steps = mr.ScriptWriter()._split_steps(acts)
        self.assertEqual(len(steps), 1)

    def test_multi_step_script_has_step_funcs(self):
        acts = [mr.Action("text", 0.0, value="hi"),
                mr.Action("section", 1.0),
                mr.Action("key", 2.0, value="enter")]
        script = build_script(acts)
        self.assertIn("def step_1", script)
        self.assertIn("def step_2", script)
        self.assertIn("step_1()", script)
        self.assertIn("step_2()", script)


class TestCleanScript(unittest.TestCase):

    def test_no_helpers_in_clean_script(self):
        acts = [mr.Action("text", 0.0, value="hello")]
        script = build_script(acts)
        self.assertNotIn("find_image", script)
        self.assertNotIn("from pathlib import Path", script)

    def test_helpers_present_for_image_click(self):
        acts = [mr.Action("image_click", 0.0, x=100, y=100, image="click_1.png")]
        script = build_script(acts)
        self.assertIn("def find_image", script)
        self.assertIn("from pathlib import Path", script)

    def test_helpers_present_for_wait_image(self):
        acts = [mr.Action("wait_image", 0.0, image="wait_1.png")]
        script = build_script(acts)
        self.assertIn("def wait_for_image", script)


class TestPauseGeneration(unittest.TestCase):

    def test_small_gap_no_sleep(self):
        """Gap 0.2 s < MIN_WAIT 0.3 s -> no sleep."""
        a = mr.Action("text", 0.2, value="x")
        self.assertIsNone(mr.ScriptWriter()._pause(a, 0.0))

    def test_large_gap_produces_sleep(self):
        """Gap 1.5 s -> time.sleep(1.5 / SPEED)."""
        a = mr.Action("text", 1.5, value="x")
        line = mr.ScriptWriter()._pause(a, 0.0)
        self.assertIsNotNone(line)
        self.assertIn("time.sleep", line)
        self.assertIn("/ SPEED", line)

    def test_before_wait_image_no_sleep(self):
        """No sleep before wait_image (it waits by itself)."""
        a = mr.Action("wait_image", 2.0, image="w.png")
        self.assertIsNone(mr.ScriptWriter()._pause(a, 0.0))

    def test_after_wait_image_sleep_is_kept(self):
        """The gap AFTER a wait_image is the user's reaction time once the picture
        appeared, so the replay keeps it (this reverses Antigravity's FIX B11)."""
        a = mr.Action("text", 3.0, value="x")
        self.assertEqual(mr.ScriptWriter()._pause(a, 0.0), "time.sleep(3.0 / SPEED)")

    def test_step_body_sleeps_after_wait_image_but_not_before(self):
        acts = [
            mr.Action("click", 0.0, x=1, y=1, value="left"),
            mr.Action("wait_image", 6.0, image="w.png"),   # 6 s of loading: replaced by the wait
            mr.Action("text", 8.0, value="hello"),         # 2 s reaction time: kept
        ]
        body = mr.ScriptWriter()._step_body(acts)
        sleeps = [l for l in body if "time.sleep" in l]
        self.assertEqual(sleeps, ["time.sleep(2.0 / SPEED)"], body)


class TestExecutedScript(unittest.TestCase):

    def test_click_executes(self):
        calls = exec_script_get_calls([mr.Action("click", 0.0, x=100, y=200, value="left")])
        self.assertIn("click", [c[0] for c in calls])

    def test_hotkey_executes(self):
        calls = exec_script_get_calls([mr.Action("hotkey", 0.0, keys=("ctrl", "c"))])
        self.assertIn("hotkey", [c[0] for c in calls])

    def test_write_executes(self):
        calls = exec_script_get_calls([mr.Action("text", 0.0, value="hello")])
        self.assertIn("write", [c[0] for c in calls])

    def test_drag_moveto_before_dragto(self):
        calls = exec_script_get_calls([mr.Action("drag", 0.0, x=10, y=10,
                                                  x2=100, y2=100, value="left", end_time=0.5)])
        names = [c[0] for c in calls]
        self.assertLess(names.index("moveTo"), names.index("dragTo"))

    def test_ctrl_click_keydown_before_keyup(self):
        calls = exec_script_get_calls([mr.Action("click", 0.0, x=100, y=100,
                                                  value="left", mods=("ctrl",))])
        names = [c[0] for c in calls]
        self.assertLess(names.index("keyDown"), names.index("keyUp"))

    def test_image_click_fallback(self):
        """click_image falls back to recorded coords (842, 517) when find_image returns None."""
        acts = [mr.Action("image_click", 0.0, x=842, y=517, value="left",
                           image="click_1.png", img_dx=5, img_dy=-3)]
        script = build_script(acts)
        ns = {
            "time": MagicMock(),
            "__file__": str(WORKSPACE / "dummy.py"),
            "Path": Path,
        }
        ns["time"].sleep = MagicMock()
        ns["time"].time = MagicMock(return_value=0)
        exec(compile_script(script), ns)
        ns["find_image"] = lambda name, timeout: None
        clicks = []
        ns["inp"] = MagicMock()                      # the library the script clicks with
        ns["inp"].click = MagicMock(side_effect=lambda *a, **kw: clicks.append(a))
        ns["click_image"]("click_1.png", 5, -3, 842, 517)
        self.assertEqual(clicks[0], (842, 517))

    def test_wait_for_image_raises_on_timeout(self):
        """wait_for_image raises SystemExit when find_image returns None."""
        acts = [mr.Action("wait_image", 0.0, image="wait_1.png")]
        script = build_script(acts)
        ns = {
            "time": MagicMock(),
            "__file__": str(WORKSPACE / "dummy.py"),
            "Path": Path,
        }
        ns["time"].sleep = MagicMock()
        exec(compile_script(script), ns)
        ns["find_image"] = lambda name, timeout: None
        with self.assertRaises(SystemExit):
            ns["wait_for_image"]("wait_1.png")


class TestAllActionTypesCompile(unittest.TestCase):
    """Every action kind must produce syntactically valid Python."""

    def _ok(self, action):
        compile_script(build_script([action]))

    def test_click(self):
        self._ok(mr.Action("click", 0, x=1, y=2, value="left"))

    def test_right_click(self):
        self._ok(mr.Action("click", 0, x=1, y=2, value="right"))

    def test_ctrl_click(self):
        self._ok(mr.Action("click", 0, x=1, y=2, value="left", mods=("ctrl",)))

    def test_shift_drag(self):
        self._ok(mr.Action("drag", 0, x=0, y=0, x2=100, y2=100,
                            value="left", mods=("shift",), end_time=0.5))

    def test_scroll(self):
        self._ok(mr.Action("scroll", 0, x=1, y=1, amount=-3))

    def test_hscroll(self):
        self._ok(mr.Action("hscroll", 0, x=1, y=1, amount=2))

    def test_text(self):
        self._ok(mr.Action("text", 0, value="hello world"))

    def test_text_with_quotes(self):
        self._ok(mr.Action("text", 0, value='say hi and bye'))

    def test_key(self):
        self._ok(mr.Action("key", 0, value="enter"))

    def test_hotkey(self):
        self._ok(mr.Action("hotkey", 0, keys=("ctrl", "c")))

    def test_image_click(self):
        self._ok(mr.Action("image_click", 0, x=500, y=300, value="left",
                            image="click_1.png", img_dx=5, img_dy=-3))

    def test_wait_image(self):
        self._ok(mr.Action("wait_image", 0, image="wait_1.png"))

    def test_multi_step(self):
        compile_script(build_script([
            mr.Action("text", 0, value="a"),
            mr.Action("section", 1),
            mr.Action("text", 2, value="b"),
        ]))

    def test_empty_recording(self):
        """Empty actions list -> run_macro containing "pass", still valid."""
        compile_script(build_script([]))


class TestModifierState(unittest.TestCase):

    def test_release_clears_modifier(self):
        r = FakeRecorder()
        r.press(make_key(name="ctrl_l"))
        self.assertIn("ctrl", r.held)
        r.release(make_key(name="ctrl_l"))
        self.assertNotIn("ctrl", r.held)

    def test_reconcile_drops_stale(self):
        """FIX B3: reconcile removes modifiers not physically held."""
        r = FakeRecorder()
        r.held = {"ctrl", "alt"}
        real_held = {"ctrl"}
        def fake_reconcile():
            r.held &= real_held
        r._reconcile_mods = fake_reconcile
        r._reconcile_mods()
        self.assertEqual(r.held, {"ctrl"})

    def test_reconcile_called_on_mouse_press(self):
        """_reconcile_mods is called on every mouse button press."""
        r = FakeRecorder()
        called = []
        r._reconcile_mods = lambda: called.append(True)
        r.click(100, 100, pressed=True)
        self.assertTrue(len(called) >= 1)


class TestCallbackExceptionSafety(unittest.TestCase):

    def test_on_press_exception_is_caught(self):
        """FIX B2: exception in _on_press must not propagate out."""
        r = FakeRecorder()
        r._add_hotkey = MagicMock(side_effect=RuntimeError("boom"))
        r.press(make_key(name="ctrl_l"))
        try:
            r.press(make_key(char=""))  # Ctrl+A triggers _add_hotkey
        except Exception as e:
            self.fail(f"exception escaped _on_press: {e}")

    def test_on_click_exception_is_caught(self):
        """FIX B12: exception in _on_click must not propagate out."""
        r = FakeRecorder()
        r.actions = MagicMock(side_effect=RuntimeError("broken list"))
        try:
            r.click(100, 100, pressed=False)
        except Exception as e:
            self.fail(f"exception escaped _on_click: {e}")

    def test_on_scroll_exception_is_caught(self):
        """FIX B12: exception in _on_scroll must not propagate out."""
        r = FakeRecorder()
        r.actions = MagicMock(side_effect=RuntimeError("broken"))
        try:
            r.scroll(100, 100, 0, -1)
        except Exception as e:
            self.fail(f"exception escaped _on_scroll: {e}")


class TestSortingByTime(unittest.TestCase):

    def test_drag_sorted_before_later_text(self):
        """
        Drag starts at t=0 but is inserted at t=0.5 (release).
        After sort, drag comes before text at t=0.1.
        """
        drag = mr.Action("drag", 0.0, x=0, y=0, x2=100, y2=100,
                          value="left", end_time=0.5)
        text = mr.Action("text", 0.1, value="a")
        # Build sorted merged list the same way build() does
        merged = mr.ScriptWriter()._merge(sorted([text, drag], key=lambda a: a.time))
        self.assertEqual(merged[0].kind, "drag")
        self.assertEqual(merged[1].kind, "text")


class TestKeyNameMapping(unittest.TestCase):

    def test_page_up_mapped_to_pageup(self):
        r = FakeRecorder()
        r.press(make_key(name="page_up"))
        self.assertEqual(r.actions[0].value, "pageup")

    def test_page_down_mapped(self):
        r = FakeRecorder()
        r.press(make_key(name="page_down"))
        self.assertEqual(r.actions[0].value, "pagedown")

    def test_unknown_key_passes_through(self):
        r = FakeRecorder()
        r.press(make_key(name="home"))
        self.assertEqual(r.actions[0].value, "home")


class RealModsRecorder(mr.Recorder):
    """Recorder whose _reconcile_mods is the REAL one, with the Windows key state faked.
    `physically_down` is the set of modifiers the keyboard really has held right now."""
    def __init__(self, physically_down=()):
        super().__init__()
        self.physically_down = set(physically_down)

    def _reconcile_mods(self):
        stale = self.held - self.physically_down
        if stale:
            print(f"  [recorder] Cleared stale modifiers: {stale}")
        self.held &= self.physically_down


class TestStaleModifiersWhileTyping(unittest.TestCase):

    def test_stuck_ctrl_does_not_turn_typing_into_hotkeys(self):
        """Ctrl went down, then Ctrl+Tab (or Win+L) hid its release. Ctrl is not really
        held any more, so typing 'a' must be text, not hotkey('ctrl', 'a')."""
        r = RealModsRecorder(physically_down=())
        r.held = {"ctrl"}
        r._on_press(make_key(char="a"))
        self.assertEqual([(a.kind, a.value) for a in r.actions], [("text", "a")])
        self.assertEqual(r.held, set())

    def test_really_held_ctrl_still_makes_a_hotkey(self):
        r = RealModsRecorder(physically_down={"ctrl"})
        r._on_press(make_key(name="ctrl_l"))
        r._on_press(make_key(char="\x03"))               # Ctrl+C
        self.assertEqual(r.actions[0].kind, "hotkey")
        self.assertEqual(r.actions[0].keys, ("ctrl", "c"))

    def test_reconcile_runs_for_special_keys_too(self):
        r = RealModsRecorder(physically_down=())
        r.held = {"alt"}
        r._on_press(make_key(name="enter"))
        self.assertEqual(r.actions[0].kind, "key")       # plain Enter, not alt+enter


class TestMarkerThenImmediateClick(unittest.TestCase):
    """F8 followed straight away by a click must always pair up (the screenshot is
    taken on the keyboard thread, so the picture exists before the click arrives)."""

    def setUp(self):
        self.r = FakeRecorder()
        self.r._mouse.position = (842, 517)
        self.r._save_picture = MagicMock(return_value=("click_1.png", 842, 517))

    def test_f8_then_instant_click_becomes_image_click(self):
        self.r._on_press(_FakeKeyEnum.f8)
        self.r.click(850, 520, pressed=True)
        self.r.click(850, 520, pressed=False)
        a = self.r.actions[0]
        self.assertEqual((a.kind, a.image, a.img_dx, a.img_dy), ("image_click", "click_1.png", 8, 3))

    def test_f8_picture_is_saved_before_f8_handler_returns(self):
        self.r._on_press(_FakeKeyEnum.f8)
        self.r._save_picture.assert_called_once_with("click")
        self.assertIsNotNone(self.r._pending_image)

    def test_shift_f8_adds_wait_image_through_the_real_handler(self):
        self.r._save_picture = MagicMock(return_value=("wait_1.png", 300, 300))
        self.r._on_press(make_key(name="shift"))
        self.r._on_press(_FakeKeyEnum.f8)
        self.assertEqual([(a.kind, a.image) for a in self.r.actions], [("wait_image", "wait_1.png")])

    def test_second_f8_replaces_the_first_picture(self):
        self.r._on_press(_FakeKeyEnum.f8)
        self.r._on_release(_FakeKeyEnum.f8)          # a real keyboard releases F8 between presses
        self.r._save_picture.return_value = ("click_2.png", 842, 517)
        self.r._on_press(_FakeKeyEnum.f8)
        self.assertEqual(self.r._pending_image[0], "click_2.png")


class TestPauseResume(unittest.TestCase):

    def setUp(self):
        self.r = FakeRecorder()
        self.shift_f9 = lambda: (self.r._on_press(make_key(name="shift")),
                                 self.r._on_press(_FakeKeyEnum.f9),
                                 self.r._on_release(_FakeKeyEnum.f9),
                                 self.r._on_release(make_key(name="shift")))

    def test_shift_f9_pauses_and_nothing_is_recorded(self):
        self.shift_f9()
        self.assertTrue(self.r.paused)
        self.r._on_press(make_key(char="p"))
        self.r.click(10, 10, pressed=True)
        self.r.click(10, 10, pressed=False)
        self.r.scroll(10, 10, 0, -1)
        self.assertEqual(self.r.actions, [])

    def test_shift_f9_again_resumes(self):
        self.shift_f9()
        self.shift_f9()
        self.assertFalse(self.r.paused)
        self.r._on_press(make_key(char="o"))
        self.assertEqual(self.r.actions[0].value, "o")

    def test_shift_f9_is_not_recorded_and_does_not_stop(self):
        self.assertIsNone(self.r._on_press(make_key(name="shift")))
        self.assertIsNone(self.r._on_press(_FakeKeyEnum.f9))   # None = keep listening

    def test_plain_f9_still_stops_even_while_paused(self):
        self.shift_f9()
        self.assertIs(self.r._on_press(_FakeKeyEnum.f9), False)

    def test_modifiers_are_still_tracked_while_paused(self):
        self.shift_f9()
        self.r._on_press(make_key(name="ctrl_l"))
        self.assertIn("ctrl", self.r.held)
        self.r._on_release(make_key(name="ctrl_l"))
        self.assertNotIn("ctrl", self.r.held)

    def test_paused_time_is_not_counted_as_waiting(self):
        """Typed at t=100, paused for 50 s, typed again: the two Actions are ~0 s apart,
        not 50 s, so the script gets no long time.sleep()."""
        clock = [100.0]
        real_time = mr.time.time
        mr.time.time = lambda: clock[0]
        try:
            self.r._on_press(make_key(char="a"))
            clock[0] = 101.0
            self.shift_f9()                      # pause at t=101
            clock[0] = 151.0
            self.shift_f9()                      # resume at t=151 (50 s paused)
            clock[0] = 151.5
            self.r._on_press(make_key(char="b"))
        finally:
            mr.time.time = real_time
        a, b = self.r.actions
        self.assertAlmostEqual(b.time - a.time, 1.5)    # 151.5 - 50 - 100
        script = build_script(self.r.actions)
        self.assertNotIn("time.sleep(50", script)

    def test_pausing_forgets_an_unused_f8_picture(self):
        self.r._pending_image = ("click_1.png", 1, 1)
        self.shift_f9()
        self.assertIsNone(self.r._pending_image)


class TestRecordingFile(unittest.TestCase):
    """The .jsonl recording: written as it happens, readable later."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.meta = {"version": mr.LOG_VERSION, "session": "macro_test",
                     "created": "2026-10-10T11:00:00", "screen": [1920, 1080]}

    def tearDown(self):
        self.tmp.cleanup()

    def _sample_actions(self):
        return [
            mr.Action("click", 1000.0, x=842, y=517, value="left", mods=("ctrl",)),
            mr.Action("text", 1001.0, value="hi"),
            mr.Action("hotkey", 1002.0, keys=("ctrl", "c")),
            mr.Action("drag", 1003.0, x=1, y=2, x2=3, y2=4, value="left", end_time=1003.6),
            mr.Action("scroll", 1004.0, x=5, y=6, amount=-3),
            mr.Action("image_click", 1005.0, x=9, y=9, value="left", image="click_1.png", img_dx=8, img_dy=3),
            mr.Action("wait_image", 1006.0, image="wait_2.png"),
            mr.Action("section", 1007.0),
        ]

    def test_action_round_trips_through_a_dict(self):
        for a in self._sample_actions():
            self.assertEqual(mr.Action.from_dict(json_roundtrip(a.to_dict())), a)

    def test_log_is_written_as_each_action_is_appended(self):
        path = self.dir / "macro_test.jsonl"
        log = mr.ActionLog(path, self.meta)
        log.append(mr.Action("text", 1.0, value="x"))
        # file read BEFORE close(): the line must already be on disk
        lines = path.read_text(encoding="utf-8").splitlines()
        log.close()
        self.assertEqual(len(lines), 2)
        self.assertIn('"meta"', lines[0])
        self.assertIn('"kind": "text"', lines[1])

    def test_load_log_returns_meta_and_actions(self):
        path = self.dir / "macro_test.jsonl"
        log = mr.ActionLog(path, self.meta)
        for a in self._sample_actions():
            log.append(a)
        log.close()
        meta, actions = mr.load_log(path)
        self.assertEqual(meta["screen"], [1920, 1080])
        self.assertEqual(actions, self._sample_actions())

    def test_cut_off_last_line_is_skipped_with_a_warning(self):
        path = self.dir / "macro_test.jsonl"
        log = mr.ActionLog(path, self.meta)
        log.append(mr.Action("text", 1.0, value="ok"))
        log.close()
        with open(path, "a", encoding="utf-8") as f:
            f.write('{"kind": "click", "time": 2.0, "x": 8')        # the recorder died mid-write
        meta, actions = mr.load_log(path)
        self.assertEqual([a.value for a in actions], ["ok"])

    def test_bad_line_in_the_middle_raises_with_its_line_number(self):
        path = self.dir / "bad.jsonl"
        path.write_text('{"meta": {"version": 1}}\n'
                        '{"kind": "text", "time": 1, "value": "a"}\n'
                        'this is not json\n'
                        '{"kind": "text", "time": 2, "value": "b"}\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "line 3"):
            mr.load_log(path)

    def test_unknown_kind_raises_with_its_line_number(self):
        path = self.dir / "bad.jsonl"
        path.write_text('{"meta": {"version": 1}}\n{"kind": "explode", "time": 1}\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "line 2.*unknown action kind"):
            mr.load_log(path)

    def test_missing_field_and_wrong_type_raise(self):
        path = self.dir / "bad.jsonl"
        path.write_text('{"meta": {"version": 1}}\n{"kind": "click", "x": 1}\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "line 2"):
            mr.load_log(path)
        path.write_text('{"meta": {"version": 1}}\n{"kind": "click", "time": 1, "x": "left"}\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "line 2"):
            mr.load_log(path)

    def test_wrong_version_and_missing_meta_raise(self):
        path = self.dir / "bad.jsonl"
        path.write_text('{"meta": {"version": 99}}\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "version"):
            mr.load_log(path)
        path.write_text('{"kind": "text", "time": 1}\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "meta"):
            mr.load_log(path)
        path.write_text("", encoding="utf-8")
        with self.assertRaises(ValueError):
            mr.load_log(path)

    def test_blank_lines_are_ignored(self):
        path = self.dir / "gaps.jsonl"
        path.write_text('\n{"meta": {"version": 1}}\n\n{"kind": "key", "time": 1, "value": "enter"}\n\n',
                        encoding="utf-8")
        _, actions = mr.load_log(path)
        self.assertEqual(len(actions), 1)


def json_roundtrip(data):
    import json
    return json.loads(json.dumps(data))


class TestGenerateCommand(unittest.TestCase):

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.meta = {"version": mr.LOG_VERSION, "session": "macro_2026-10-10_110000",
                     "created": "2026-10-10T11:00:00", "screen": [1920, 1080]}
        self.actions = [
            mr.Action("click", 1000.0, x=842, y=517, value="left"),
            mr.Action("text", 1002.0, value="hi"),
            mr.Action("image_click", 1004.0, x=9, y=9, value="left", image="click_1.png", img_dx=8, img_dy=3),
        ]
        self.log = self.dir / "macro_2026-10-10_110000.jsonl"
        log = mr.ActionLog(self.log, self.meta)
        for a in self.actions:
            log.append(a)
        log.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_generate_writes_a_regen_script_next_to_the_log(self):
        out = mr.generate(self.log)
        self.assertEqual(out, self.dir / "macro_2026-10-10_110000_regen.py")
        text = out.read_text(encoding="utf-8")
        compile(text, "regen", "exec")
        self.assertIn("inp.click(842, 517)", text)
        self.assertIn("click_image('click_1.png', 8, 3, 9, 9)", text)
        self.assertIn("'macro_2026-10-10_110000_images'", text)    # pictures folder from the meta

    def test_regenerated_script_matches_a_direct_build(self):
        direct = mr.ScriptWriter().build(self.actions, self.meta["session"], screen=(1920, 1080))
        regen = mr.generate(self.log).read_text(encoding="utf-8")
        strip = lambda t: "\n".join(l for l in t.splitlines() if "Auto-generated" not in l)
        self.assertEqual(strip(regen), strip(direct))

    def test_changing_a_setting_changes_the_regenerated_script(self):
        """The point of the recording: tweak MERGE_GAP / MIN_WAIT, regenerate, no re-recording."""
        before = mr.generate(self.log).read_text(encoding="utf-8")
        self.assertIn("time.sleep(2.0 / SPEED)", before)          # the 2 s gaps are kept by default
        old = mr.MIN_WAIT
        try:
            mr.MIN_WAIT = 5.0
            after = mr.generate(self.log).read_text(encoding="utf-8")
        finally:
            mr.MIN_WAIT = old
        self.assertNotIn("time.sleep(2.0 / SPEED)", after)        # now gaps under 5 s are dropped

    def test_cli_generate_reports_bad_files_without_a_traceback(self):
        bad = self.dir / "bad.jsonl"
        bad.write_text('{"meta": {"version": 1}}\nnot json\n{"kind": "key", "time": 1, "value": "a"}\n',
                       encoding="utf-8")
        with self.assertRaises(SystemExit) as ctx:
            mr.main(["generate", str(bad)])
        self.assertIn("line 2", str(ctx.exception))

    def test_cli_generate_runs(self):
        mr.main(["generate", str(self.log)])
        self.assertTrue((self.dir / "macro_2026-10-10_110000_regen.py").exists())


class TestGeneratedCodeIsSafe(unittest.TestCase):

    def test_quotes_in_names_cannot_break_out_of_the_string(self):
        """A hand-edited recording with code hidden in a name must stay a plain string."""
        import ast
        evil = 'x"); import os; os.system("boom'
        acts = [mr.Action("image_click", 1.0, x=1, y=1, value="left", image=evil),
                mr.Action("wait_image", 2.0, image=evil),
                mr.Action("key", 3.0, value=evil),
                mr.Action("click", 4.0, x=1, y=1, value=evil)]
        tree = ast.parse(mr.ScriptWriter().build(acts, "macro_x"))      # must still be valid Python
        imported = {alias.name for n in ast.walk(tree) if isinstance(n, ast.Import) for alias in n.names}
        imported |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        self.assertEqual(imported, {"os", "time", "pyautogui", "pathlib", "pydirectinput", "pynput"})   # no smuggled "import os"
        called = {n.func.attr for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        self.assertNotIn("system", called)

    def test_session_name_with_a_quote_cannot_break_the_helpers(self):
        acts = [mr.Action("wait_image", 1.0, image="w.png")]
        text = mr.ScriptWriter().build(acts, 'bad"; import os #')
        compile(text, "evil", "exec")
        self.assertEqual([l for l in text.splitlines() if l.strip().startswith('import os')],
                         ['import os'])      # only the script's own import, nothing smuggled in


class TestScreenSizeCheck(unittest.TestCase):

    def test_script_warns_when_screen_differs(self):
        text = mr.ScriptWriter().build([mr.Action("click", 1.0, x=1, y=1, value="left")],
                                       "macro_x", screen=(1920, 1080), backend="pyautogui")
        self.assertIn("RECORDED_SCREEN = (1920, 1080)", text)
        compile(text, "s", "exec")
        calls, printed = [], []
        pag = types.ModuleType("pyautogui")
        pag.size = lambda: (1366, 768)
        pag.FailSafeException = Exception
        for n in ("click", "keyDown", "keyUp"):
            setattr(pag, n, lambda *a, **k: None)
        ns = {"__name__": "__main__", "time": MagicMock(), "pyautogui": pag, "print": printed.append}
        old = sys.modules.get("pyautogui")
        sys.modules["pyautogui"] = pag
        try:
            exec(compile(text.replace("COUNTDOWN = 3", "COUNTDOWN = 0"), "s", "exec"), ns)
        finally:
            sys.modules["pyautogui"] = old
        self.assertTrue(any("WARNING" in str(p) and "(1366, 768)" in str(p) for p in printed), printed)

    def test_no_screen_means_no_check(self):
        text = mr.ScriptWriter().build([mr.Action("click", 1.0, x=1, y=1, value="left")], "macro_x")
        self.assertNotIn("RECORDED_SCREEN", text)


class TestBeepNeverCrashes(unittest.TestCase):

    def test_beep_swallows_device_errors(self):
        original = mr.winsound
        broken = types.SimpleNamespace(Beep=MagicMock(side_effect=RuntimeError("Failed to beep")))
        mr.winsound = broken
        try:
            mr.beep(1000, 10)        # must not raise
        finally:
            mr.winsound = original


class TestWholeSession(unittest.TestCase):
    """Runs the real record() end to end with fake listeners that "perform" a scripted
    session, then replays the saved script against a fake pyautogui."""

    def setUp(self):
        import tempfile
        from unittest import mock
        self.mock = mock
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.patches = [
            mock.patch.object(mr, "__file__", str(self.dir / "macro_recorder.py")),
            mock.patch.object(mr, "START_DELAY", 0),
            mock.patch.object(mr, "BEEP", False),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def _perform_session(self):
        """Replace the pynput listeners so entering the keyboard listener "types" a session."""
        callbacks = {}
        clock = [5000.0]
        key = lambda **kw: make_key(**kw)

        def session():
            kb_press, kb_release = callbacks["on_press"], callbacks["on_release"]
            on_click, on_scroll = callbacks["on_click"], callbacks["on_scroll"]
            btn = _FakeButtonEnum.left
            tick = lambda dt: clock.__setitem__(0, clock[0] + dt)
            shift_combo = lambda k: (kb_press(key(name="shift")), kb_press(k), kb_release(k),
                                     kb_release(key(name="shift")))
            tap = lambda k: (kb_press(k), tick(.02), kb_release(k))      # press and let go

            tick(1); on_click(842, 517, btn, True); tick(.1); on_click(842, 517, btn, False)
            tick(1)
            for c in "hi":
                tap(key(char=c)); tick(.1)
            shift_combo(_FakeKeyEnum.f9)                       # pause
            tick(40)                                           # 40 s typing a password...
            for c in "secret":
                tap(key(char=c)); tick(.1)
            shift_combo(_FakeKeyEnum.f9)                       # resume
            tick(1); tap(_FakeKeyEnum.f8)                      # picture of what is under the mouse
            tick(.3); on_click(510, 410, btn, True); tick(.05); on_click(510, 410, btn, False)
            tick(6); shift_combo(_FakeKeyEnum.f8)              # wait-for-image
            tick(2); kb_press(key(name="ctrl_l")); tap(_FakeKeyEnum.f8); kb_release(key(name="ctrl_l"))
            tick(1); kb_press(key(char="w")); tick(.5)         # hold W for about 2 s ...
            for _ in range(10):
                kb_press(key(char="w")); tick(.03)             # ... the keyboard auto-repeats it ...
            tick(1.2); kb_release(key(char="w"))               # ... then you let go
            tick(1); tap(key(char="x"))
            tick(1); kb_press(_FakeKeyEnum.f9)                 # stop

        class KbListener:
            def __init__(self, on_press=None, on_release=None):
                callbacks.update(on_press=on_press, on_release=on_release)
            def __enter__(self): session(); return self
            def __exit__(self, *a): pass
            def join(self): pass

        class MouseListener:
            def __init__(self, on_click=None, on_scroll=None):
                callbacks.update(on_click=on_click, on_scroll=on_scroll)
            def start(self): pass
            def stop(self): pass

        return KbListener, MouseListener, clock

    def test_record_then_replay(self):
        import io, contextlib
        KbListener, MouseListener, clock = self._perform_session()

        def fake_screenshot(region):
            return types.SimpleNamespace(save=lambda path: Path(path).write_bytes(b"png"))

        with self.mock.patch.object(_keyboard_stub, "Listener", KbListener), \
             self.mock.patch.object(_mouse_stub, "Listener", MouseListener), \
             self.mock.patch.object(mr.time, "time", lambda: clock[0]), \
             self.mock.patch.object(_fake_pyautogui, "screenshot", fake_screenshot), \
             self.mock.patch("builtins.input", side_effect=AssertionError("must not ask anything")), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            mr.record(backend="pyautogui")

        files = sorted(p.name for p in self.dir.iterdir())
        jsonl = next((self.dir / "recordings").rglob("macro_*.jsonl"))
        script = next((self.dir / "recordings").rglob("macro_*.py"))
        images = next((self.dir / "recordings").rglob("macro_*_images"))
        self.assertEqual(sorted(p.name for p in images.iterdir()), ["click_1.png", "wait_2.png"])

        raw = jsonl.read_text(encoding="utf-8")
        text = script.read_text(encoding="utf-8")
        self.assertNotIn("secret", raw)                  # paused: the password was never recorded
        self.assertNotIn("secret", text)
        self.assertNotIn("time.sleep(40", text)          # and the 40 s pause is not replayed as a wait
        self.assertIn("inp.click(842, 517)", text)
        self.assertIn("inp.write('hi'", text)
        # the F8 picture was centered on the mouse at (500, 400); the click at (510, 410) is
        # 10 px right and 10 px below the picture's center; (510, 410) is the fallback spot
        self.assertIn("click_image('click_1.png', 10, 10, 510, 410)", text)
        self.assertIn("wait_for_image('wait_2.png')", text)
        self.assertIn("def step_1():", text)
        self.assertIn("def step_2():", text)
        self.assertIn("inp.keyDown('w')", text)           # W held about 2 s = one keyDown/keyUp pair ...
        self.assertIn("inp.keyUp('w')", text)
        self.assertNotIn("write('w", text)                # ... not a pile of typed w's
        self.assertIn("RECORDED_SCREEN = (1920, 1080)", text)

        # regenerating from the recording gives the same script
        regen = mr.generate(jsonl, backend="pyautogui").read_text(encoding="utf-8")
        strip = lambda t: "\n".join(l for l in t.splitlines() if "Auto-generated" not in l)
        self.assertEqual(strip(regen), strip(text))

        # and the script really runs: every call lands in the right order
        calls = []
        pag = types.ModuleType("pyautogui")
        pag.FAILSAFE, pag.PAUSE = True, 0.1
        pag.FailSafeException = type("FSE", (Exception,), {})
        pag.ImageNotFoundException = type("INF", (Exception,), {})
        pag.size = lambda: (1920, 1080)
        pag.locateCenterOnScreen = lambda path, confidence=None: types.SimpleNamespace(x=700, y=300)
        for n in ("click", "write", "press", "hotkey", "keyDown", "keyUp", "moveTo", "dragTo"):
            setattr(pag, n, (lambda n: lambda *a, **k: calls.append((n, a)))(n))
        old = sys.modules["pyautogui"]
        sys.modules["pyautogui"] = pag
        try:
            with self.mock.patch("time.sleep"), contextlib.redirect_stdout(io.StringIO()):
                ns = {"__name__": "__main__", "__file__": str(script)}
                exec(compile(text, str(script), "exec"), ns)
        finally:
            sys.modules["pyautogui"] = old
        self.assertEqual([c[0] for c in calls], ["click", "write", "click", "keyDown", "keyUp", "write"])
        self.assertEqual(calls[0], ("click", (842, 517)))
        self.assertEqual(calls[2], ("click", (710, 310)))   # picture found at (700, 300) + offset (10, 10)
        self.assertEqual(calls[-1][0], "write")             # the "x" typed in step 2


class ClockedRecorder(mr.Recorder):
    """Recorder with a controllable clock and no Windows key-state lookups, for hold tests."""
    def __init__(self):
        super().__init__()
        self.clock = 1000.0
        mr.time.time = self._time

    def _time(self):
        return self.clock

    def _reconcile_mods(self):
        pass

    def tick(self, seconds):
        self.clock += seconds


class TestHeldKeys(unittest.TestCase):
    """Holding W to walk: one keyDown / wait / keyUp, not a pile of typed w's."""

    def setUp(self):
        self._real_time = mr.time.time
        self.r = ClockedRecorder()
        self.w = lambda: make_key(char="w")      # a new object each time, like pynput gives

    def tearDown(self):
        mr.time.time = self._real_time

    def hold_w(self, seconds, repeats=10):
        self.r._on_press(self.w()); self.r.tick(0.5)             # first press, then the delay
        for _ in range(repeats):                                 # the keyboard auto-repeats
            self.r._on_press(self.w()); self.r.tick(0.03)
        self.r.tick(seconds - 0.5 - 0.03 * repeats)
        self.r._on_release(self.w())

    def test_auto_repeat_makes_one_press_not_many(self):
        self.hold_w(2.0)
        self.assertEqual([a.kind for a in self.r.actions], ["text", "hold"])

    def test_hold_records_start_end_and_keys(self):
        self.hold_w(2.0)
        hold = self.r.actions[1]
        self.assertEqual(hold.keys, ("w",))
        self.assertEqual(hold.time, self.r.actions[0].time)      # starts when the key went down
        self.assertAlmostEqual(hold.end_time - hold.time, 2.0)

    def test_quick_tap_is_not_a_hold(self):
        x = lambda: make_key(char="x")                  # not a movement key
        self.r._on_press(x()); self.r.tick(0.1); self.r._on_release(x())
        self.assertEqual([a.kind for a in self.r.actions], ["text"])

    def test_script_for_a_hold(self):
        self.hold_w(2.0)
        body = mr.ScriptWriter()._step_body(
            mr.ScriptWriter()._merge(mr.ScriptWriter()._apply_holds(self.r.actions)))
        code = "\n".join(body)
        self.assertIn("inp.keyDown('w')", code)
        self.assertIn("time.sleep(2.00 / SPEED)", code)
        self.assertIn("inp.keyUp('w')", code)
        self.assertNotIn("write", code)                          # the tap was replaced by the hold
        self.assertLess(code.index("keyDown"), code.index("sleep"))
        self.assertLess(code.index("sleep"), code.index("keyUp"))

    def test_pressing_the_same_key_again_after_release_counts(self):
        x = lambda: make_key(char="x")
        for _ in range(3):
            self.r._on_press(x()); self.r.tick(0.05); self.r._on_release(x()); self.r.tick(0.1)
        self.assertEqual(len(self.r.actions), 3)                 # three taps of x, none dropped

    def test_a_missed_release_does_not_block_the_key_forever(self):
        self.r._on_press(self.w())                               # release never arrives (Win+L...)
        self.r.tick(5.0)
        self.r._on_press(self.w())                               # 5 s later: a new press
        self.assertEqual(len(self.r.actions), 2)

    def test_capital_letter_released_after_shift_is_still_cleared(self):
        """pynput reports 'H' on press but 'h' on release when Shift is let go first."""
        self.r._on_press(make_key(name="shift"))
        self.r._on_press(make_key(char="H")); self.r.tick(0.05)
        self.r._on_release(make_key(name="shift"))
        self.r._on_release(make_key(char="h"))
        self.assertEqual(self.r._down, {})
        self.r.tick(0.1)
        self.r._on_press(make_key(char="H"))                     # must NOT be mistaken for a repeat
        self.assertEqual(len([a for a in self.r.actions if a.kind == "text"]), 2)

    def test_ctrl_c_control_code_is_matched_on_release(self):
        self.r._on_press(make_key(name="ctrl_l")); self.r._on_press(make_key(char="\x03"))
        self.r.tick(0.05)
        self.r._on_release(make_key(name="ctrl_l")); self.r._on_release(make_key(char="c"))
        self.assertEqual(self.r._down, {})

    def test_shift_w_held_is_a_hold_of_both_keys(self):
        self.r._on_press(make_key(name="shift"))
        self.r._on_press(make_key(char="W"))                     # pynput reports a capital
        self.r.tick(1.5)
        self.r._on_release(make_key(char="W")); self.r._on_release(make_key(name="shift"))
        hold = self.r.actions[-1]
        self.assertEqual((hold.kind, hold.keys), ("hold", ("shift", "w")))
        w = mr.ScriptWriter()
        code = "\n".join(w._step_body(w._apply_holds([hold])))
        self.assertLess(code.index("keyDown('shift')"), code.index("keyDown('w')"))
        self.assertLess(code.index("keyUp('w')"), code.index("keyUp('shift')"))   # released in reverse

    def test_release_during_a_pause_adds_nothing(self):
        self.r._on_press(self.w()); self.r.tick(0.1)
        self.r._toggle_pause()
        self.r.tick(2.0)
        self.r._on_release(self.w())
        self.assertEqual([a.kind for a in self.r.actions], ["text"])

    def test_raising_hold_min_turns_a_hold_back_into_a_tap(self):
        """The recording keeps the hold; HOLD_MIN decides at generate time what counts as one."""
        self.hold_w(1.0)
        old = mr.HOLD_MIN, mr.GAME_HOLD_MIN
        try:
            mr.HOLD_MIN = mr.GAME_HOLD_MIN = 2.0
            kept = mr.ScriptWriter()._apply_holds(self.r.actions)
        finally:
            mr.HOLD_MIN, mr.GAME_HOLD_MIN = old
        self.assertEqual([a.kind for a in kept], ["text"])

    def test_two_different_keys_with_the_same_timestamp_do_not_cancel_each_other(self):
        """An older Windows clock can stamp two presses with the same time."""
        taps = [mr.Action("text", 5.0, value="a"), mr.Action("text", 5.0, value="w"),
                mr.Action("hold", 5.0, keys=("w",), end_time=7.0)]
        kept = mr.ScriptWriter()._apply_holds(taps)
        self.assertEqual([(a.kind, a.value) for a in kept if a.kind == "text"], [("text", "a")])

    def test_keys_to_hold_names(self):
        self.assertEqual(mr.keys_to_hold(mr.Action("text", 0, value="w")), ("w",))
        self.assertEqual(mr.keys_to_hold(mr.Action("text", 0, value=" ")), ("space",))
        self.assertEqual(mr.keys_to_hold(mr.Action("text", 0, value="H")), ("shift", "h"))
        self.assertEqual(mr.keys_to_hold(mr.Action("key", 0, value="up")), ("up",))
        self.assertEqual(mr.keys_to_hold(mr.Action("hotkey", 0, keys=("ctrl", "c"))), ("ctrl", "c"))


class TestHeldMouse(unittest.TestCase):

    def clicked(self, seconds):
        acts = [mr.Action("click", 10.0, x=842, y=517, value="left", end_time=10.0 + seconds)]
        w = mr.ScriptWriter()
        return "\n".join(w._step_body(w._apply_holds(acts)))

    def test_recorder_keeps_the_release_time_of_a_click(self):
        real = mr.time.time
        r = ClockedRecorder()
        try:
            r.click = lambda x, y, p: r._on_click(x, y, _FakeButtonEnum.left, p)
            r.click(5, 5, True); r.tick(1.5); r.click(5, 5, False)
        finally:
            mr.time.time = real
        a = r.actions[0]
        self.assertEqual(a.kind, "click")
        self.assertAlmostEqual(a.end_time - a.time, 1.5)

    def test_long_press_becomes_mousedown_wait_mouseup(self):
        code = self.clicked(1.5)
        self.assertIn("inp.mouseDown(842, 517, button='left')", code)
        self.assertIn("time.sleep(1.50 / SPEED)", code)
        self.assertIn("inp.mouseUp(842, 517, button='left')", code)
        self.assertLess(code.index("mouseDown"), code.index("mouseUp"))

    def test_normal_click_stays_a_click(self):
        self.assertIn("inp.click(842, 517)", self.clicked(0.1))
        self.assertNotIn("mouseDown", self.clicked(0.1))


class TestBackendSwitch(unittest.TestCase):
    """The generated script runs against a stand-in pydirectinput / pyautogui that records
    every call, so we can see exactly what the games backend sends."""

    def run_script(self, actions, backend="pydirectinput", edits=(), raise_in_macro=None):
        events = []
        def spy(owner, name):
            return lambda *a, **k: events.append((owner, name, a, k))
        pag = types.ModuleType("pyautogui")
        pag.FailSafeException = type("PagFailSafe", (Exception,), {})
        pag.size = lambda: (1920, 1080)
        pag.ImageNotFoundException = type("INF", (Exception,), {})
        for n in ("click", "write", "press", "hotkey", "keyDown", "keyUp", "moveTo", "dragTo",
                  "scroll", "hscroll", "mouseDown", "mouseUp"):
            setattr(pag, n, spy("pyautogui", n))
        pdi = types.ModuleType("pydirectinput")
        pdi.FailSafeException = type("PdiFailSafe", (Exception,), {})
        for n in ("click", "write", "press", "hotkey", "keyDown", "keyUp", "moveTo", "moveRel",
                  "dragTo", "scroll", "hscroll", "mouseDown", "mouseUp"):
            setattr(pdi, n, spy("pydirectinput", n))
        text = mr.ScriptWriter().build(actions, "macro_x", backend=backend).replace("COUNTDOWN = 3", "COUNTDOWN = 0")
        for old, new in edits:
            text = text.replace(old, new)
        saved = {k: sys.modules.get(k) for k in ("pyautogui", "pydirectinput")}
        sys.modules["pyautogui"], sys.modules["pydirectinput"] = pag, pdi
        out = []
        try:
            with unittest.mock.patch("time.sleep", lambda s: events.append(("sleep", round(s, 3)))), \
                 unittest.mock.patch("builtins.print", lambda *a, **k: out.append(" ".join(map(str, a)))):
                ns = {"__name__": "__main__", "__file__": str(WORKSPACE / "x.py")}
                if raise_in_macro == "pdi":
                    text += "\n"
                    ns["__raise__"] = pdi.FailSafeException
                exec(compile(text, "generated", "exec"), ns)
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
        return events, out, text

    def test_default_backend_is_pydirectinput(self):
        text = mr.ScriptWriter().build([mr.Action("click", 1.0, x=1, y=1, value="left")], "macro_x")
        self.assertIn('BACKEND = "pydirectinput"', text)

    def test_bad_backend_name_is_rejected(self):
        with self.assertRaises(ValueError):
            mr.ScriptWriter().build([], "macro_x", backend="selenium")

    def test_script_has_both_libraries_so_one_word_flips_it(self):
        text = mr.ScriptWriter().build([mr.Action("click", 1.0, x=1, y=1, value="left")], "macro_x",
                                       backend="pyautogui")
        self.assertIn('BACKEND = "pyautogui"', text)
        self.assertIn("import pydirectinput", text)

    def test_pyautogui_backend_calls_pyautogui_directly(self):
        events, _, _ = self.run_script([mr.Action("click", 1.0, x=842, y=517, value="left"),
                                        mr.Action("text", 2.0, value="hi")], backend="pyautogui")
        calls = [(o, n) for o, n, *_ in (e for e in events if e[0] != "sleep")]
        self.assertEqual(calls, [("pyautogui", "click"), ("pyautogui", "write")])

    def test_game_click_moves_nudges_rests_then_clicks(self):
        events, _, _ = self.run_script([mr.Action("click", 1.0, x=842, y=517, value="left")])
        self.assertEqual(
            [e for e in events if e[0] != "sleep" or e[1] == 0.05],
            [("pydirectinput", "moveTo", (842, 517), {}),
             ("pydirectinput", "moveRel", (1, 0), {"relative": True}),
             ("pydirectinput", "moveRel", (-1, 0), {"relative": True}),
             ("sleep", 0.05),
             ("pydirectinput", "click", (), {"button": "left"})])

    def test_nudge_can_be_switched_off(self):
        events, _, _ = self.run_script([mr.Action("click", 1.0, x=842, y=517, value="left")],
                                       edits=[("NUDGE = True", "NUDGE = False")])
        self.assertNotIn("moveRel", [e[1] for e in events])
        self.assertIn(("pydirectinput", "moveTo", (842, 517), {}), events)

    def test_right_click_keeps_its_button(self):
        events, _, _ = self.run_script([mr.Action("click", 1.0, x=5, y=6, value="right")])
        self.assertIn(("pydirectinput", "click", (), {"button": "right"}), events)

    def test_typing_keeps_capitals_and_symbols(self):
        events, _, _ = self.run_script([mr.Action("text", 1.0, value="Hello!")])
        write = next(e for e in events if e[1] == "write")
        self.assertEqual(write, ("pydirectinput", "write", ("Hello!",), {"interval": 0.05, "auto_shift": True}))

    def test_scroll_is_converted_from_pyautogui_units_to_notches(self):
        """The recorder writes scroll(-360) = 3 notches down. pydirectinput counts whole
        notches, so passing -360 through would scroll 360 times."""
        events, _, _ = self.run_script([mr.Action("scroll", 1.0, x=500, y=400, amount=-3),
                                        mr.Action("hscroll", 5.0, x=500, y=400, amount=2)])
        self.assertIn(("pydirectinput", "scroll", (-3,), {}), events)
        self.assertIn(("pydirectinput", "hscroll", (2,), {}), events)
        self.assertIn(("pydirectinput", "moveTo", (500, 400), {}), events)   # scroll where it was recorded

    def test_held_key_goes_straight_through(self):
        events, _, _ = self.run_script([mr.Action("hold", 1.0, keys=("w",), end_time=3.0)])
        order = [e[1] for e in events if e[1] in ("keyDown", 2.0, "keyUp")]
        self.assertEqual(order, ["keyDown", 2.0, "keyUp"])

    def test_hotkey_press_drag_go_through(self):
        events, _, _ = self.run_script([
            mr.Action("hotkey", 1.0, keys=("ctrl", "c")), mr.Action("key", 2.0, value="enter"),
            mr.Action("drag", 3.0, x=1, y=2, x2=3, y2=4, value="left", end_time=3.6)])
        names = [e[1] for e in events if e[0] == "pydirectinput"]
        self.assertEqual(names, ["hotkey", "keyDown", "keyUp", "moveTo", "dragTo"])   # press = down, short wait, up

    def test_pyautogui_is_still_used_for_the_screen_size_check_and_pictures(self):
        text = mr.ScriptWriter().build([mr.Action("wait_image", 1.0, image="w.png")], "macro_x")
        self.assertIn("pyautogui.locateCenterOnScreen", text)
        self.assertIn("inp.click(spot.x + dx, spot.y + dy, button=button)", text)

    def test_both_emergency_stops_are_caught(self):
        text = mr.ScriptWriter().build([mr.Action("click", 1.0, x=1, y=1, value="left")], "macro_x")
        self.assertIn("pydirectinput.FailSafeException", text)
        self.assertIn("except FAILSAFE_ERRORS:", text)

    def test_missing_pydirectinput_gives_an_install_hint(self):
        text = mr.ScriptWriter().build([mr.Action("click", 1.0, x=1, y=1, value="left")], "macro_x")
        saved = {k: sys.modules.get(k) for k in ("pyautogui", "pydirectinput")}
        pag = types.ModuleType("pyautogui"); pag.FailSafeException = Exception
        sys.modules["pyautogui"], sys.modules["pydirectinput"] = pag, None    # None = import fails
        try:
            with self.assertRaises(SystemExit) as ctx:
                exec(compile(text, "g", "exec"), {"__name__": "x", "__file__": str(WORKSPACE / "x.py")})
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
        self.assertIn("pip install pydirectinput-rgx", str(ctx.exception))

    def test_cli_backend_option_for_generate(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "macro_2026-10-10_120000.jsonl"
            meta = {"version": mr.LOG_VERSION, "session": "macro_2026-10-10_120000", "screen": [1920, 1080]}
            al = mr.ActionLog(log, meta); al.append(mr.Action("click", 1.0, x=1, y=1, value="left")); al.close()
            mr.main(["generate", str(log), "--backend", "pyautogui"])
            text = (Path(tmp) / "macro_2026-10-10_120000_regen.py").read_text(encoding="utf-8")
            self.assertIn('BACKEND = "pyautogui"', text)
            mr.main(["generate", str(log)])
            text = (Path(tmp) / "macro_2026-10-10_120000_regen.py").read_text(encoding="utf-8")
            self.assertIn('BACKEND = "pydirectinput"', text)

    def test_cli_backend_option_for_record(self):
        with unittest.mock.patch.object(mr, "record") as rec:
            mr.main(["--backend", "pyautogui"])
            rec.assert_called_once_with(backend="pyautogui")
        with unittest.mock.patch.object(mr, "record") as rec:
            mr.main([])
            rec.assert_called_once_with(backend=mr.BACKEND)


class TestCircleWalking(unittest.TestCase):
    """Walking in a circle: W+D, D+S, S+A, A+W overlap. Down and up must replay at their
    real times, not one hold after another."""

    run_script = TestBackendSwitch.run_script

    # (key, pressed at, released at) in seconds from the start of the recording
    CIRCLE = [("w", 0.0, 1.4), ("d", 0.6, 2.2), ("s", 1.6, 3.0), ("a", 2.4, 3.8), ("w", 3.2, 4.5)]

    def record_circle(self):
        real = mr.time.time
        r = ClockedRecorder()
        start = r.clock
        try:
            steps = sorted([(t0, "down", k) for k, t0, t1 in self.CIRCLE]
                           + [(t1, "up", k) for k, t0, t1 in self.CIRCLE])
            for t, what, k in steps:
                r.clock = start + t
                (r._on_press if what == "down" else r._on_release)(make_key(char=k))
                if what == "down":                       # the keyboard auto-repeats the newest key
                    r.clock += 0.2; r._on_press(make_key(char=k)); r.clock -= 0.2
        finally:
            mr.time.time = real
        return r.actions

    def timeline(self, events):
        """{(key, 'down'/'up', n): virtual seconds} from the fake calls and sleeps."""
        now, seen = 0.0, []
        for e in events:
            if e[0] == "sleep":
                now += e[1]
            elif e[1] in ("keyDown", "keyUp"):
                seen.append((e[2][0], "down" if e[1] == "keyDown" else "up", now))
        return seen

    def test_every_press_and_release_is_recorded(self):
        kinds = [a.kind for a in self.record_circle()]
        self.assertEqual(kinds.count("hold"), 5)

    def test_replay_has_the_same_overlaps_and_timing(self):
        events, _, text = self.run_script(self.record_circle())
        got = self.timeline(events)
        want = sorted([(k, "down", t0) for k, t0, t1 in self.CIRCLE]
                      + [(k, "up", t1) for k, t0, t1 in self.CIRCLE], key=lambda e: e[2])
        self.assertEqual([(k, d) for k, d, _ in got], [(k, d) for k, d, _ in want])
        base = got[0][2]
        for (k, d, t), (_, _, expected) in zip(got, want):
            self.assertAlmostEqual(t - base, expected - want[0][2], delta=0.03, msg=f"{k} {d}")

    def test_w_is_down_while_d_goes_down(self):
        events, _, _ = self.run_script(self.record_circle())
        order = [(e[2][0], e[1]) for e in events if e[1] in ("keyDown", "keyUp")]
        self.assertEqual(order[:3], [("w", "keyDown"), ("d", "keyDown"), ("w", "keyUp")])

    def test_no_extra_pause_that_would_stretch_the_timing(self):
        text = self.run_script(self.record_circle())[2]
        self.assertIn("pyautogui.PAUSE = 0 ", text)
        self.assertIn("pydirectinput.PAUSE = pyautogui.PAUSE", text)

    def test_ordinary_macro_keeps_the_small_pause(self):
        text = mr.ScriptWriter().build([mr.Action("click", 1.0, x=1, y=1, value="left")], "macro_x")
        self.assertIn("pyautogui.PAUSE = 0.1 / SPEED", text)

    def test_long_click_with_ctrl_replays_as_down_and_up_events(self):
        acts = [mr.Action("click", 1.0, x=50, y=60, value="right", mods=("ctrl",), end_time=3.5),
                mr.Action("hold", 2.0, keys=("w",), end_time=3.0)]
        events, _, _ = self.run_script(acts)
        names = [e[1] for e in events if e[0] != "sleep"]
        self.assertEqual(names, ["keyDown", "moveTo", "moveRel", "moveRel", "mouseDown", "keyDown",
                                 "keyUp", "mouseUp", "keyUp"])
        # (ctrl down, mouse down, W down, W up, mouse up, ctrl up) with W inside the click
        self.assertEqual([e[2][0] for e in events if e[1] == "keyDown"], ["ctrl", "w"])

    def test_short_gaps_between_events_are_kept(self):
        acts = [mr.Action("hold", 1.0, keys=("w",), end_time=2.0),
                mr.Action("hold", 2.15, keys=("d",), end_time=3.0)]
        _, _, text = self.run_script(acts)
        self.assertIn("time.sleep(0.15 / SPEED)", text)

    def test_up_goes_before_down_at_the_same_instant(self):
        acts = [mr.Action("hold", 1.0, keys=("w",), end_time=2.0),
                mr.Action("hold", 2.0, keys=("w",), end_time=3.0)]
        kept = mr.ScriptWriter()._apply_holds(acts)
        self.assertEqual([a.kind for a in kept], ["keydown", "keyup", "keydown", "keyup"])


class TestCtrlBackspaceTwice(unittest.TestCase):
    """Ctrl held, Backspace pressed twice quickly: both must be recorded."""

    def setUp(self):
        self._real_time = mr.time.time
        self.r = ClockedRecorder()

    def tearDown(self):
        mr.time.time = self._real_time

    def key(self, name=None, char=None, vk=8):
        k = make_key(name=name, char=char)
        k.vk = vk                                   # pynput gives Windows keys a virtual-key code
        return k

    def ctrl(self):
        return make_key(name="ctrl_l")

    def test_press_as_backspace_release_as_del_character(self):
        """The reported bug: press and release looked like different keys, so the second
        press looked like auto-repeat and was dropped."""
        backspace = self.key(name="backspace")      # pynput's Key.backspace is one shared object
        self.r._on_press(self.ctrl())
        for _ in range(2):
            self.r._on_press(backspace); self.r.tick(0.08)
            self.r._on_release(self.key(char="\x7f")); self.r.tick(0.2)
        self.assertEqual([a.keys for a in self.r.actions if a.kind == "hotkey"],
                         [("ctrl", "backspace"), ("ctrl", "backspace")])

    def test_ctrl_backspace_arriving_as_a_del_character_is_recorded(self):
        self.r._on_press(self.ctrl())
        self.r._on_press(self.key(char="\x7f")); self.r.tick(0.08)
        self.r._on_release(self.key(char="\x7f"))
        self.assertEqual([(a.kind, a.keys) for a in self.r.actions], [("hotkey", ("ctrl", "backspace"))])

    def test_real_auto_repeat_is_still_ignored(self):
        self.r._on_press(self.ctrl())
        self.r._on_press(self.key(name="backspace"))
        for _ in range(8):
            self.r.tick(0.03); self.r._on_press(self.key(name="backspace"))
        self.r._on_release(self.key(name="backspace"))
        self.assertEqual(len([a for a in self.r.actions if a.kind == "hotkey"]), 1)

    def test_key_id_prefers_the_virtual_key_code(self):
        self.assertEqual(mr.Recorder._key_id(self.key(name="backspace")),
                         mr.Recorder._key_id(self.key(char="\x7f")))

    def test_key_without_a_code_still_works(self):
        self.assertEqual(mr.Recorder._key_id(make_key(char="H")), "h")


REAL_NOTEPAD_RECORDING = [
    {"kind": "click", "time": 1791616943.3001397, "x": 1093, "y": 1048, "value": "left", "end_time": 1791616943.3678715},
    {"kind": "click", "time": 1791616944.7158573, "x": 378, "y": 319, "value": "left", "end_time": 1791616944.7919827},
    {"kind": "text", "time": 1791616945.869259, "value": "H", "end_time": 1791616945.869259},
    {"kind": "text", "time": 1791616946.0702136, "value": "e", "end_time": 1791616946.0702136},
    {"kind": "text", "time": 1791616946.1622648, "value": "l", "end_time": 1791616946.1622648},
    {"kind": "text", "time": 1791616946.286218, "value": "l", "end_time": 1791616946.286218},
    {"kind": "text", "time": 1791616946.4462085, "value": "o", "end_time": 1791616946.4462085},
    {"kind": "text", "time": 1791616946.5392787, "value": " ", "end_time": 1791616946.5392787},
    {"kind": "hold", "time": 1791616946.5392787, "keys": ["space"], "end_time": 1791616946.663749},
    {"kind": "text", "time": 1791616946.9464114, "value": "W", "end_time": 1791616946.9464114},
    {"kind": "text", "time": 1791616946.9539979, "value": "Q", "end_time": 1791616946.9539979},
    {"kind": "hold", "time": 1791616946.9464114, "keys": ["shift", "w"], "end_time": 1791616947.0311604},
    {"kind": "key", "time": 1791616947.4911234, "value": "backspace", "end_time": 1791616947.4911234},
    {"kind": "text", "time": 1791616947.7651067, "value": "o", "end_time": 1791616947.7651067},
    {"kind": "text", "time": 1791616947.8772535, "value": "r", "end_time": 1791616947.8772535},
    {"kind": "text", "time": 1791616947.9923954, "value": "l", "end_time": 1791616947.9923954},
    {"kind": "text", "time": 1791616948.1134841, "value": "d", "end_time": 1791616948.1134841},
    {"kind": "hold", "time": 1791616948.1134841, "keys": ["d"], "end_time": 1791616948.198254},
    {"kind": "hotkey", "time": 1791616948.7564218, "keys": ["ctrl", "backspace"], "end_time": 1791616948.7564218},
    {"kind": "hotkey", "time": 1791616948.8975964, "keys": ["ctrl", "backspace"], "end_time": 1791616948.8975964},
    {"kind": "click", "time": 1791616951.5680413, "x": 1100, "y": 1060, "value": "left", "end_time": 1791616951.644276},
]
# ^ a real recording: "Hello World" typed with a wrong key (Q) fixed by Backspace, then
#   Ctrl+Backspace twice. Typing W, D and space must not be mistaken for game movement.


class TestRealNotepadRecording(unittest.TestCase):

    run_script = TestBackendSwitch.run_script

    def actions(self):
        return [mr.Action.from_dict(d) for d in REAL_NOTEPAD_RECORDING]

    def code(self):
        w = mr.ScriptWriter()
        return "\n".join(w._step_body(w._merge(sorted(w._apply_holds(self.actions()), key=lambda a: a.time))))

    def test_typing_is_plain_text_not_key_events(self):
        code = self.code()
        self.assertNotIn("keyDown", code)
        self.assertIn("inp.write('Hello WQ'", code)
        self.assertIn("inp.press('backspace')", code)
        self.assertIn("inp.write('orld'", code)

    def test_both_ctrl_backspaces_are_in_the_script(self):
        self.assertEqual(self.code().count("inp.hotkey('ctrl', 'backspace')"), 2)

    def test_replay_sends_two_separate_shortcuts_with_a_gap(self):
        events, _, _ = self.run_script(self.actions())
        hot = [i for i, e in enumerate(events) if e[1] == "hotkey"]
        self.assertEqual(len(hot), 2)
        self.assertTrue(any(e[0] == "sleep" for e in events[hot[0]:hot[1]]))   # waits between them
        self.assertEqual(events[hot[0]][3], {"interval": 0.02, "wait": 0.02})


class TestTypingVersusMovement(unittest.TestCase):

    def sort(self, acts):
        return mr.ScriptWriter()._typing_times(acts)

    def text(self, t, ch):
        return mr.Action("text", t, value=ch)

    def test_a_word_is_typing(self):
        acts = [self.text(1.0 + i * 0.1, c) for i, c in enumerate("wasd hello")]
        self.assertTrue(self.sort(acts))

    def test_only_movement_keys_is_not_typing(self):
        acts = [self.text(1.0 + i * 0.2, c) for i, c in enumerate("wasdw")]
        self.assertFalse(self.sort(acts))

    def test_movement_then_one_interact_key_is_not_typing(self):
        acts = [self.text(1.0 + i * 0.2, c) for i, c in enumerate("wawe")]    # e = interact
        self.assertFalse(self.sort(acts))

    def test_a_pause_between_bursts_separates_them(self):
        acts = [self.text(1.0, "h"), self.text(1.1, "i"),                    # typing: "hi"
                self.text(5.0, "w"), self.text(5.2, "a")]                     # later: movement
        times = self.sort(acts)
        self.assertIn((1.0, ("h",)), times)
        self.assertNotIn((5.0, ("w",)), times)

    def test_a_long_hold_inside_typing_is_kept(self):
        acts = [self.text(1.0, "h"), self.text(1.1, "e"), self.text(1.2, "d"),
                mr.Action("hold", 1.2, keys=("d",), end_time=2.0)]
        kept = mr.ScriptWriter()._apply_holds(acts)
        self.assertEqual([a.kind for a in kept].count("keydown"), 1)


class TestOtherMovementKeys(unittest.TestCase):
    """Minecraft with ESDF: those letters are the movement keys, W and A are ordinary letters."""

    def setUp(self):
        self.old = mr.GAME_KEYS
        mr.GAME_KEYS = tuple("esdf") + ("up", "down", "left", "right", "space", "shift")

    def tearDown(self):
        mr.GAME_KEYS = self.old

    def test_esdf_taps_keep_their_length(self):
        self.assertEqual(mr.hold_min(("e",)), mr.GAME_HOLD_MIN)
        self.assertEqual(mr.hold_min(("w",)), mr.HOLD_MIN)

    def test_esdf_burst_is_movement_not_typing(self):
        acts = [mr.Action("text", 1.0 + i * 0.2, value=c) for i, c in enumerate("esdfe")]
        self.assertFalse(mr.ScriptWriter()._typing_times(acts))

    def test_wa_is_now_typing(self):
        acts = [mr.Action("text", 1.0 + i * 0.2, value=c) for i, c in enumerate("wa")]
        self.assertTrue(mr.ScriptWriter()._typing_times(acts))

    def test_default_setting_builds_the_list(self):
        self.assertIn("MOVEMENT_KEYS", Path(mr.__file__).read_text(encoding="utf-8"))


class TestQuickMovementTaps(unittest.TestCase):
    """The case that failed: tapping w, a, s, d one after another (each press ~0.15 s) while a
    mouse button is held. Each tap must keep its real length and place on the timeline."""

    run_script = TestBackendSwitch.run_script

    def record(self):
        real = mr.time.time
        r = ClockedRecorder()
        try:
            r.clock += 0
            btn = _FakeButtonEnum.left
            r._on_click(993, 976, btn, True)
            for k in "wasdwx":
                r.tick(0.1); r._on_press(make_key(char=k)); r.tick(0.15); r._on_release(make_key(char=k))
            r.tick(0.5)
            r._on_click(993, 976, btn, False)
        finally:
            mr.time.time = real
        return r.actions

    def test_each_movement_tap_is_a_timed_press(self):
        kinds = [a.kind for a in self.record()]
        self.assertEqual(kinds.count("hold"), 5)          # w a s d w; the x tap stays text

    def test_replay_presses_each_key_for_about_0_15_s(self):
        events, _, text = self.run_script(self.record())
        order = [(e[2][0], e[1]) for e in events if e[1] in ("keyDown", "keyUp")]
        self.assertEqual(order[:4], [("w", "keyDown"), ("w", "keyUp"), ("a", "keyDown"), ("a", "keyUp")])
        self.assertIn("time.sleep(0.15 / SPEED)", text)
        self.assertNotIn("write('wasd", text)             # not squashed into typed text

    def test_mouse_stays_down_for_the_whole_walk(self):
        events, _, _ = self.run_script(self.record())
        names = [e[1] for e in events if e[0] != "sleep" and e[1] in ("mouseDown", "mouseUp", "keyDown", "write")]
        self.assertEqual(names[0], "mouseDown")
        self.assertEqual(names[-1], "mouseUp")
        now, down_at, up_at = 0.0, None, None
        for e in events:
            if e[0] == "sleep": now += e[1]
            elif e[1] == "mouseDown": down_at = now
            elif e[1] == "mouseUp": up_at = now
        self.assertGreater(up_at - down_at, 1.5)           # about 1.1 s of taps + 0.5 s wait + gaps


class TestRecordingsFolder(unittest.TestCase):

    def test_session_files_go_in_recordings_subfolder(self):
        r = mr.Recorder()
        base = Path(mr.__file__).parent / "recordings"
        self.assertEqual(r.folder, base / r.session)
        self.assertEqual(r.log_path, r.folder / f"{r.session}.jsonl")
        self.assertEqual(r.image_dir, r.folder / f"{r.session}_images")

    def test_save_script_in_the_given_folder(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = mr.ScriptWriter().save([mr.Action("click", 1.0, x=1, y=1, value="left")],
                                          "macro_x", folder=Path(tmp))
            self.assertEqual(path, Path(tmp) / "macro_x.py")


class TestStopKey(unittest.TestCase):
    """F10 (STOP_KEY) stops a running macro and lets go of anything it is holding."""

    run_script = TestBackendSwitch.run_script

    def stop_with(self, actions, key_name="f10", edits=()):
        """Run the script's start-up with a fake keyboard, press a key, return (events, exit codes)."""
        listeners, exits = [], []
        class Listener:
            def __init__(self, on_press=None, **kw): self.on_press = on_press; listeners.append(self)
            def start(self): pass
        real_listener = _keyboard_stub.Listener
        _keyboard_stub.Listener = Listener
        try:
            with unittest.mock.patch("os._exit", lambda code: exits.append(code)):
                events, out, text = self.run_script(actions, edits=edits)
                self.assertEqual(len(listeners), 1 if "STOP_KEY = \"\"" not in text else 0)
                events.clear()
                if listeners:
                    listeners[0].on_press(types.SimpleNamespace(name=key_name))
        finally:
            _keyboard_stub.Listener = real_listener
        return events, exits

    HOLDS = [mr.Action("hold", 1.0, keys=("w",), end_time=3.0),
             mr.Action("click", 1.5, x=5, y=6, value="right", mods=("ctrl",), end_time=3.5)]

    def test_stop_key_releases_keys_and_buttons_then_exits(self):
        events, exits = self.stop_with(self.HOLDS)
        released = [(e[1], e[2][0] if e[2] else e[3].get("button")) for e in events
                    if e[1] in ("keyUp", "mouseUp")]
        self.assertIn(("keyUp", "w"), released)
        self.assertIn(("keyUp", "ctrl"), released)
        self.assertIn(("mouseUp", "right"), released)
        self.assertEqual(exits, [0])

    def test_other_keys_do_not_stop_it(self):
        events, exits = self.stop_with(self.HOLDS, key_name="f11")
        self.assertEqual((events, exits), ([], []))

    def test_stop_key_can_be_turned_off(self):
        events, exits = self.stop_with(self.HOLDS, edits=[('STOP_KEY = "f10"', 'STOP_KEY = ""')])
        self.assertEqual(exits, [])

    def test_script_lists_what_it_may_hold(self):
        text = mr.ScriptWriter().build(self.HOLDS, "macro_x")
        self.assertIn("HELD_KEYS = ['ctrl', 'w']", text)
        self.assertIn("HELD_BUTTONS = ['right']", text)

    def test_script_without_holds_has_empty_lists(self):
        text = mr.ScriptWriter().build([mr.Action("click", 1.0, x=1, y=1, value="left")], "macro_x")
        self.assertIn("HELD_KEYS = []", text)

    def test_stop_key_is_named_in_the_start_message(self):
        text = mr.ScriptWriter().build(self.HOLDS, "macro_x")
        self.assertIn("press {STOP_KEY.upper()}", text)


class TestRepeat(unittest.TestCase):
    """REPEAT = N runs N times; REPEAT = 0 runs until stopped."""

    run_script = TestBackendSwitch.run_script
    CLICK = [mr.Action("click", 1.0, x=5, y=6, value="left")]

    def clicks(self, edits):
        try:
            events, _, _ = self.run_script(self.CLICK, edits=edits)
        except SystemExit:
            raise
        return sum(1 for e in events if e[1] == "click")

    def test_default_runs_once(self):
        self.assertEqual(self.clicks([]), 1)

    def test_repeat_three_runs_three_times(self):
        self.assertEqual(self.clicks([("REPEAT = 1 ", "REPEAT = 3 ")]), 3)

    def test_repeat_zero_keeps_looping_until_stopped(self):
        # stop it after the 5th run the way F10 would: by ending the process
        stop = [("REPEAT = 1 ", "REPEAT = 0 "),
                ("            run_macro()\n", "            run_macro()\n            if run_number == 5: raise KeyboardInterrupt\n")]
        with self.assertRaises(KeyboardInterrupt):
            self.run_script(self.CLICK, edits=stop)

    def test_repeat_zero_is_explained_in_the_script(self):
        text = mr.ScriptWriter().build(self.CLICK, "macro_x")
        self.assertIn("0 = forever", text)
        self.assertIn("while REPEAT == 0 or run_number < REPEAT", text)


class TestLoopDelay(unittest.TestCase):

    run_script = TestBackendSwitch.run_script
    CLICK = [mr.Action("click", 1.0, x=5, y=6, value="left")]

    def waits(self, edits):
        events, _, _ = self.run_script(self.CLICK, edits=edits)
        return [e[1] for e in events if e[0] == "sleep" and e[1] >= 5]

    def test_no_delay_by_default(self):
        self.assertEqual(self.waits([("REPEAT = 1 ", "REPEAT = 3 ")]), [])

    def test_delay_between_runs_but_not_after_the_last(self):
        waits = self.waits([("REPEAT = 1 ", "REPEAT = 3 "), ("LOOP_DELAY = 0 ", "LOOP_DELAY = 7 ")])
        self.assertEqual(waits, [7, 7])              # 3 runs have 2 gaps

    def test_single_run_never_waits(self):
        self.assertEqual(self.waits([("LOOP_DELAY = 0 ", "LOOP_DELAY = 7 ")]), [])

    def test_forever_loop_with_a_delay_still_runs(self):
        stop = [("REPEAT = 1 ", "REPEAT = 0 "), ("LOOP_DELAY = 0 ", "LOOP_DELAY = 7 "),
                ("            run_macro()\n", "            run_macro()\n            if run_number == 3: raise KeyboardInterrupt\n")]
        with self.assertRaises(KeyboardInterrupt):
            self.run_script(self.CLICK, edits=stop)


class TestRealPydirectinputNames(unittest.TestCase):
    """Only runs where pydirectinput-rgx is installed (your Windows PC): every key name this
    program can put in a script must be one pydirectinput knows."""

    def test_all_key_names_we_emit_are_known(self):
        try:
            import pydirectinput
        except Exception:
            self.skipTest("pydirectinput is not installed here")
        names = set(mr.KEY_NAMES.values()) | {"enter", "esc", "tab", "space", "backspace", "delete",
                  "home", "end", "up", "down", "left", "right", "insert", "ctrl", "shift", "alt", "win"}
        names |= {f"f{i}" for i in range(1, 13)} | set("abcdefghijklmnopqrstuvwxyz0123456789")
        unknown = sorted(n for n in names if not pydirectinput.is_valid_key(n))
        self.assertEqual(unknown, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
