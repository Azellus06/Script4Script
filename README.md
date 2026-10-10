# Macro Recorder

A "script maker" for Windows. Do a task once with your mouse and keyboard, press **F9**, and it writes a ready-to-run Python script that repeats it. No hunting for coordinates by hand.

It works in normal apps and in games, for clicking menus, walking with held keys and typing chat. Tested in Roblox: clicks register and held keys replay.

## Install

```
pip install pynput pyautogui pillow opencv-python pydirectinput-rgx
```

Built and tested on Windows. Python 3.10 or newer. See [Platform support](#platform-support) for Linux and macOS.

## Quick start

```
python macro_recorder.py
```

1. A 3 second countdown gives you time to switch to your target window. A high beep means recording started.
2. Do your task normally.
3. Press **F9**. A low beep means it saved.

Everything is saved in `recordings/macro_<date>_<time>/` next to `macro_recorder.py`:

| File | What it is |
|---|---|
| `macro_<date>_<time>.jsonl` | the raw recording, saved action by action as you go |
| `macro_<date>_<time>.py` | the ready-to-run script |
| `macro_<date>_<time>_images/` | pictures from F8 markers (only if you used them) |

Run the script with `python macro_<date>_<time>.py`. The recorder never asks anything in the terminal, so it also works from a `.pyw` launcher.

## Platform support

| Platform | Status |
|---|---|
| **Windows** | Fully supported and tested, including Roblox. |
| **Linux (X11)** | Not tested on a real desktop; should work with `pyautogui`. Use `--backend pyautogui` (or set `BACKEND = "pyautogui"`), because `pydirectinput` is Windows-only. Screenshots may need `sudo apt install scrot python3-tk`. |
| **Linux (Wayland)** | Global key and mouse capture is mostly blocked, so recording and replay may not work. Log in with an Xorg session instead. |
| **macOS** | Not tested. `pynput` needs Accessibility and Input Monitoring permission. Use the `pyautogui` backend. |

On non-Windows systems the Windows-only parts (start/stop beeps, the screen-scaling fix and the stuck-modifier check) are skipped automatically. There are no beeps, so watch the terminal. The game tweaks only apply to the `pydirectinput` backend.

## Hotkeys while recording

| Key | What it does |
|---|---|
| **F9** | stop and save |
| **Shift+F9** | pause / resume. Nothing is recorded while paused (use it for passwords) |
| **F8** | image click: hover over a button, press F8, then click it as normal |
| **Shift+F8** | wait for image: press it while the thing you are waiting for is on screen |
| **Ctrl+F8** | new step: starts a new `step_N()` function in the script |

## What gets recorded

- Clicks (left, right, middle), with Ctrl / Shift / Alt held.
- Drags, replayed with their real duration.
- Scrolling, up and down and sideways. Notches in a row merge into one `scroll()` line.
- Typed text. Letters typed close together merge into one `write('Hello World')`.
- Special keys (Enter, Tab, arrows, F-keys) and shortcuts such as `Ctrl+C` or `Ctrl+Backspace`.
- **Held keys and held mouse buttons** with their real timing. Holds can overlap, so walking in a circle (W+D, then D+S, then S+A...) replays correctly.
- Pauses between actions become `time.sleep(...)` lines. Time spent paused is not replayed.
- Every action gets a comment such as `# click at 14:30:02`.

## Playing back

The generated script has settings at the top:

```python
BACKEND = "pydirectinput"   # "pydirectinput" works in games; "pyautogui" is for normal apps
SPEED = 1.0      # 2.0 = twice as fast, 0.5 = half speed
REPEAT = 1       # how many times to run the whole macro (0 = forever until you stop it)
LOOP_DELAY = 0   # seconds to wait between runs
COUNTDOWN = 3    # seconds to wait before it starts
STOP_KEY = "f10" # press this key to stop at any time ("" turns it off)
NUDGE = True     # games: wiggle the mouse 1 px after moving it
```

- **Loop forever:** set `REPEAT = 0` and the macro repeats until you press the stop key. `LOOP_DELAY` adds a wait (in seconds) between runs.
- **F10 stops the macro** at any moment. It lets go of every key and mouse button it was holding, so nothing stays stuck down.
- Moving the mouse into a screen corner also stops it (top-left corner with `pydirectinput`).
- The script warns you if your screen size differs from the one it was recorded on.
- Scripts with several steps get one `step_N()` function each, called in order by `run_macro()`. Reorder or delete lines freely.

## Image clicks and waiting for pictures

Buttons move. **F8** saves a small picture around your mouse, and your next click on it becomes an image click. On replay the script searches the screen for that picture and clicks it wherever it is now. If it cannot find it within 5 seconds, it clicks the recorded spot instead.

**Shift+F8** saves a picture of something you are waiting for (a loading screen ending, a button appearing). The script pauses until it shows up and stops if it never does (30 seconds).

Matching uses `confidence=0.8`. Change `CONFIDENCE`, `IMAGE_TIMEOUT` and `WAIT_TIMEOUT` in the script if needed. In games, use windowed or borderless mode so pictures can be matched.

## Rebuilding a script from a recording

```
python macro_recorder.py generate recordings\macro_2026-10-10_152217\macro_2026-10-10_152217.jsonl
```

This writes `macro_..._regen.py` ("regenerated") next to the recording without recording again. Use it after changing a setting, after updating the recorder, or to switch libraries:

```
python macro_recorder.py generate <file>.jsonl --backend pyautogui
```

The `.jsonl` file has one action per line, so you can open it and edit it by hand. A damaged or cut-off last line (power cut, crash) is tolerated, and errors name the line number.

## Games (Roblox, Minecraft...)

Games usually ignore normal Windows input, so scripts replay with **pydirectinput** by default. The script also does these things for games:

- Moves the mouse like a real one (move, tiny nudge and back, short rest) before clicking.
- Keeps capitals and symbols when typing chat.
- Presses keys for a moment instead of an instant tap.
- Spaces out back-to-back shortcuts.
- Turns off the extra pause after every call while holding keys, so timing stays exact.

**Movement keys.** The recorder keeps the real length of even a quick tap of a movement key. Set yours in `macro_recorder.py`:

```python
MOVEMENT_KEYS = "wasd"   # "esdf" for that layout
```

Arrows, Space and Shift always count as movement. Ordinary typing stays plain text: two normal letters side by side (like "he") mark a burst as typing.

**Limits**
- Camera and mouse-look are not supported.
- Image clicks replay as plain clicks, not holds.
- If the game runs as administrator, run the script as administrator too.
- Some games ban macros. Check the rules of each game.
- If clicks misbehave, set `NUDGE = False`, or flip `BACKEND` to compare.

## Settings in `macro_recorder.py`

| Setting | Default | Meaning |
|---|---|---|
| `START_DELAY` | 3 | seconds before recording starts |
| `BEEP` | True | beeps for start, stop and markers |
| `MIN_WAIT` | 0.3 | pauses shorter than this don't become `sleep()` lines |
| `MERGE_GAP` | 1.0 | letters typed within this many seconds merge into one `write()` |
| `DRAG_MIN_PIXELS` | 10 | less movement than this between press and release is a plain click |
| `SCROLL_MERGE_GAP` | 0.5 | wheel notches within this many seconds merge |
| `IMAGE_W`, `IMAGE_H` | 100, 60 | size of the saved F8 picture |
| `BACKEND` | pydirectinput | library new scripts replay with |
| `HOLD_MIN` | 0.3 | other keys / mouse buttons held this long become a hold |
| `MOVEMENT_KEYS` | wasd | your walk keys |
| `GAME_HOLD_MIN` | 0.03 | shortest movement-key tap that keeps its length |
| `EVENT_MIN_WAIT` | 0.02 | smallest wait kept next to key/button down and up |
| `RECORDINGS_DIR` | recordings | folder (next to the recorder) for all sessions |

## Safety

- **Everything you type is saved as plain text** in the `.jsonl` and `.py` files. Press **Shift+F9** to pause before typing a password, and don't commit recordings to git. Add `recordings/` to `.gitignore`.
- Generated text is written with `repr()`, so odd characters in a recording can't break out of a string and run as code.
- Only run recordings you made yourself.

## How it works

- `pynput` listens to the mouse and keyboard on background threads.
- Windows DPI awareness is switched on first, so coordinates match your real screen on scaled displays.
- Stale modifier keys (after Alt+Tab or Win+L) are cleared using `GetAsyncKeyState`.
- Keyboard auto-repeat is ignored, and keys are identified by their Windows key code, so Ctrl+Backspace twice records twice.
- A hold is saved as separate key-down and key-up events on one timeline, which is what makes overlapping keys replay correctly.
- `pyautogui` takes the F8 screenshots and does the picture matching (with Pillow and OpenCV).
- Main parts: `Action` (one event), `ActionLog` (the `.jsonl` file), `Recorder` (listeners), `ScriptWriter` (turns actions into code).

## Tests

```
python test_macro_recorder.py
```

190 tests with fake keyboards, mice and libraries, so they run anywhere without touching your real input. They include a whole recorded session replayed from start to finish and a real Notepad recording. One test checks key names against the real `pydirectinput` and is skipped where it isn't installed.

## Files

| File | Purpose |
|---|---|
| `macro_recorder.py` | the whole program |
| `test_macro_recorder.py` | automated tests (optional to use) |
| `Script4Script.md` | research notes on why normal input fails in Roblox and what to try |
| `recordings/` | your sessions (keep out of git) |
