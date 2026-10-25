# Copyright (c) 2026 Kiravik. All rights reserved. RallyForge Bot was created by Kiravik.
"""
Core engine for the Evony rally bot (ADB + OpenCV + OCR). Used by app.py (GUI) and by the CLI.

CLI:
  python rally_bot.py devices                      list emulators the bot can see
  python rally_bot.py test [image.png]             what would the bot do on this screenshot?
  python rally_bot.py crop NAME IMG X Y W H        save a cropped template (540x960 coords)
  python rally_bot.py addmonster NAME IMG ROW      save a monster picture (ROW 1 = top-most Join button)
  python rally_bot.py monsters                     list saved monster pictures
  python rally_bot.py generals                     list saved general pictures
  python rally_bot.py tabs IMG                     which general tab / favourites state does this screenshot show?
  python rally_bot.py run                          start the bot on every configured device

New config keys (config.json):
  "devices":      []  -> use "device_id" only | ["auto"] -> every detected emulator | ["id1","id2"]
  "presets":      [1,2,3,4]      presets to use (1-8)
  "preset_mode":  "rotate" | "random" | "fixed"
  "per_device":   {"127.0.0.1:5565": {"presets": [3,4], "max_joins": 20}}   per-instance overrides
  "verify_march": true           check the march really started (needs templates/march_screen.png)
  "monster_mode": "off" | "skip" | "only",  "monster_list": ["Witch"],  "monster_unknown": "join" | "skip"
  "monster_presets": {"Witch": [1, 2], "Pan": [3]}   use these presets (= their general) against that monster

Optional templates (all 540x960 crops):
  join_button, rally_icon          (required, as before)
  march_screen   the "Reset" button on the march screen (only exists there)
  city_screen    something only visible in the city (e.g. the globe button, bottom right)
  rally_screen   something only visible on the rally list (optional)
  no_general     the EMPTY general slot on the march screen (optional): a preset showing it is skipped
"""
import gc, hashlib, hmac, json, os, random, re, shutil, struct, subprocess, sys, tempfile, threading, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import cv2
import numpy as np
import pytesseract

# Windows: never pop up "application error" boxes for child processes (tesseract can fail to start, 0xc000142, when
# many emulators start at once) - the failure then comes back as an exception we retry instead of a dialog nobody sees.
try:
    import ctypes
    ctypes.windll.kernel32.SetErrorMode(0x8007)
except Exception:
    pass                                    # not Windows; runs before the log exists
_OCR_SLOTS = threading.Semaphore(3)         # at most 3 tesseract processes at the same time (8 emulators would flood Windows)


def _ocr_guard(fn):
    def run(*a, **k):
        err = None
        for i in range(3):
            with _OCR_SLOTS:
                try:
                    return fn(*a, **k)
                except Exception as e:      # tesseract could not start: wait a moment and try again
                    err = e
            time.sleep(0.6 + i)
        raise err
    return run


pytesseract.image_to_string = _ocr_guard(pytesseract.image_to_string)
pytesseract.image_to_data = _ocr_guard(pytesseract.image_to_data)

AUTHOR = "Kiravik"                        # the creator of this software
COPYRIGHT = "Copyright (c) 2026 Kiravik. All rights reserved."
VERSION = "2.5.16"                       # shown in the window; the updater compares this with the published version
DEFAULT_UPDATE_URL = "https://raw.githubusercontent.com/28vicky/rallyforge-bot/main/version.json"
os.environ.setdefault("OMP_THREAD_LIMIT", "1")       # Tesseract would otherwise use every CPU core per call
RES = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))          # bundled files
def _user_dir():
    if os.name != "nt":
        return Path(__file__).parent / "userdata"
    new, old = Path(os.environ["APPDATA"]) / "RallyForgeBot", Path(os.environ["APPDATA"]) / "EvonyRallyBot"
    if old.exists() and not new.exists():              # keep the settings of an older install
        try:
            old.rename(new)
        except OSError:
            return old
    return new


USER = _user_dir()                                                   # settings + captured buttons
USER.mkdir(parents=True, exist_ok=True)
(USER / "templates").mkdir(exist_ok=True)
CONFIG = USER / "config.json"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
EXPECTED_SIZE = (540, 960)

DEFAULTS = {
    "device_id": "127.0.0.1:5555",
    "devices": [],                       # [] = just device_id, ["auto"] = all detected
    "per_device": {},
    "adb_path": "auto",
    "threshold": 0.85,
    "search_region": [0, 190, 540, 680],
    "poll_seconds": [1.0, 2.0],
    "click_delay_ms": 1800,                 # pace of the bot: lower = clicks faster, higher = slower
    "adaptive_delay": True,                 # after a failed click the bot slows down for a while, then speeds up again
    "adaptive_max": 2.0,                    # slowest it will go: this many times the click delay
    "frozen_seconds": 120,                  # screen that should be moving (Join buttons / lost) has not changed -> restart Evony (0 = off)
    "stuck_seconds": 600,                   # emulator not answering / still stuck this long -> restart the emulator (0 = off)
    "max_emulator_restarts": 3,             # per hour, then it stops and tells you
    "stop_cmd": "",                         # optional command that closes THIS emulator instance
    "schedules_on": False,
    "schedule": "",                         # e.g. "08:00-12:00, 18:00-23:30" or "Mon-Fri 08:00-22:00; Sat 10:00-14:00"
    "low_memory": False,                    # light mode for PCs with little RAM
    "ocr_parallel": 0,                      # text readings at the same time (0 = automatic: 2, or 1 in low-memory mode)
    "min_free_ram_mb": 0,                   # wait for this much free RAM before starting an emulator (0 = off)
    "ram_wait_seconds": 600,                # ...for at most this long
    "keep_running_on_close": False,         # False = closing the window stops every bot and quits; True = bots keep running in the background
    "auto_start": False,                    # start every emulator by itself whenever the app opens
    "start_gap_seconds": 40,                # pause between starting one emulator and the next (spreads the load)
    "max_running_emulators": 0,             # run at most this many instances at once, queue the rest (0 = no limit)
    "close_emulator_on_stop": False,        # close the emulator when its instance is stopped (frees its RAM)
    "update_url": "https://raw.githubusercontent.com/28vicky/rallyforge-bot/main/version.json",                       # address of version.json (https://...). Empty = no update checks
    "update_mode": "ask",                   # off | ask | auto. Each release also says itself: "auto" (beta) or "ask" (live)
    "update_key": "",                       # optional secret: updates must be signed with it (make_update.py --key)
    "telegram_token": "", "telegram_chat": "", "discord_webhook": "",
    "notify_problems": True,                # restarts, stuck emulator, stopped instance
    "notify_joins": False,                  # a message for every joined rally
    "cpu_level": 2,                         # 1 = fastest scanning, 2 = balanced, 3 = light, 4 = lightest
    "screenshot_mode": "auto",              # auto = raw (lighter for the emulator) then png | png
    "cv_threads": 1,                        # CPU threads used for picture matching per bot
    "perf_log_seconds": 60,                 # print a speed / load summary this often (0 = never)
    "after_join_steps": [],
    "step_timeout": 8,
    "cooldown_after_join": [4, 7],
    "max_joins": 0,                      # per device, 0 = unlimited
    # timing: read the RALLY countdown and YOUR march travel time, join only if there is enough spare time
    "rally_timer_source": "progress_bar",   # where the rally countdown is read
    "march_timer_source": "",               # the game shows no march time on the rally list or the march screen: not read ("Spare time" covers it)
    "safety_margin": 4,                     # join only if rally_left - march_time >= this many seconds
    "march_guess": 10,                      # march time used when it is not read (the walk to a rally is usually 5-10 s)
    "max_rally_seconds": 0,                 # skip rallies with MORE than this left (0 = no limit)
    "log_timers": True,                     # log what was read for every Join button
    # monster filter: "off" = join any | "skip" = skip monsters in monster_list | "only" = join only those
    "monster_mode": "off",
    "monster_list": [],                     # names = file names in the monsters folder (without .png)
    "use_preset_generals": False,           # True = never pick generals: march with each game preset as saved (empty preset -> next one)
    "save_unknown_monsters": False,         # keep a picture + name strip of every unknown monster (beta, per instance - the "subject 0" emulator)
    "game_auto_join": False,                # let the game's own Auto-Join do the joining; the bot only renews it (accounts with the Rally Monthly Card)
    "auto_join_renew_min": 120,             # renew when the game's Auto-Join has less than this many minutes left
    "auto_join_check_min": 10,              # how often the bot looks at the Auto-Join timer (minutes)
    "auto_join_tap": [405, 934],            # Auto-Join button on the Alliance War list (540x960)
    "auto_join_start_tap": [409, 939],      # Start button on the Auto-Join screen (540x960)
    "cerberus": "skip",                     # Cerberus rallies: "join" or "skip" (per instance)
    "monster_unknown": "join",              # monster with no picture yet: "join" or "skip"
    "monster_threshold": 0.8,
    "monster_skip": {},                     # {"Witch": [1, 2, 3]} = levels of that monster to skip (all levels listed = whole monster)
    "device_names": {},
    # navigation: templates tapped, in this order, to get from anywhere (e.g. the city) to the rally list.
    # Any template called popup_*.png is tapped whenever it is seen (use it for pop-up close buttons).
    "rally_nav_steps": ["rally_icon"],
    # emulator + game lifecycle (off by default)
    "auto_launch_emulator": True,           # start the emulator if it is closed
    "auto_launch_game": True,               # start Evony if it is not open and wait until it is stable
    "emulator_launch_gap": 15,              # seconds between starting two emulators (they boot one after another)
    "reclaim_elsewhere": True,              # someone logged in on another device: wait, then reload (False = ignore)
    "pause_minutes": 15,                    # Pause lasts this long, then the bot runs again by itself (0 = until you press Resume / Pause again)
    "reclaim_grace_min": 10,                # minutes to leave the game alone after being kicked off
    "lost_seconds": 240,                    # not on the rally list for this long -> restart Evony (0 = never)
    "launch_cmd": "",                       # optional command that starts THIS emulator instance
    "evony_package": "com.topgamesinc.evony",
    "emulator_boot_timeout": 240,
    "game_load_timeout": 240,               # game not loaded / stable after this many seconds -> restart the emulator
    "load_restart_emulator": True,
    "stable_checks": 3,                     # known screen this many times in a row = loaded
    "settle_seconds": 8,
    "restart_settle_seconds": 25,           # after a game / emulator (re)start: settle at least this long, no red wifi icon (owner 25 Oct)
    "restart_skip_max_idle": 180,           # over the restart budget, still restart when the list showed no rallies for this long (s)
    "game_restarts_per_hour": 2,
    "chat_check_minutes": 10,               # no rally on the list for this long -> read the Alliance chat to tell quiet from broken (0 = off)            # Evony restarts per instance per hour, except must-restarts (frozen game etc.); 0 = no limit                    # after the game looks loaded: wait this long (and until the screen is calm) before any task
    "watchdog_seconds": 60,
    "no_progress_seconds": 300,           # no log line for this long while running -> force restart the emulator (0 = off)
    # generals: names = pictures in the generals folder, "" = keep whatever the preset set
    "general_main": [],                     # a list, best first: the bot tries the next one if a general is not available
    "popup_outside_tap": [8, 480],          # where to tap to close an unknown dialog (outside it, left edge)
    "popup_x_tap": [477, 141],              # last try on an unknown dialog: the usual X spot (offer pop-ups with a round X on the frame corner)
    "general_assistant_optional": False,    # True: when no assistant general can be selected, join with the main general only
    "general_assistant": [],                # (a single name as text still works)
    "general_idle_only": True,              # skip generals that are busy (not "Idle")
    "general_tab": 0,                       # 0 = leave, 1-5 = tab number from the left (used when a role has none)
    "general_favourites": "leave",          # leave | on | off  (the heart filter)
    "general_main_tab": None,               # per role; None = use general_tab
    "general_main_fav": None,               # per role; None = use general_favourites
    "general_assistant_tab": None,
    "general_assistant_fav": None,
    "general_main_tap": [90, 532],          # the Main General portrait (NOT the magnifier: that opens the info view)
    "general_assistant_tap": [90, 688],     # the Assistant General portrait
    "general_tap_x": 90,                    # x of the portrait; y follows the card the magnifier is on
    "max_troops": 1,                        # skip a preset that already holds more than this many troops (0 = off)
    "market_items": [],                     # [{name, pay: gems|resources|both}], picture saved as template mk_<name>
    "market_pay": "gems",                   # default payment for items without their own: gems / resources / both
    "market_refresh_mode": "count",         # none | count | all (all = until the gem floor / no more refreshing)
    "market_refresh_n": 3,                  # 3 = the game's free refreshes
    "market_paid_refresh": False,           # refresh with gems when the free refreshes are used up
    "market_max_gems": 0,                   # most gems to spend in one visit (0 = no limit; required for paid refreshes)
    "market_refresh_cost": 50,              # gems a paid refresh costs if it cannot be read
    "market_match": 0.78,
    "market_min_gems": 0,                   # never let a gem purchase / paid refresh take the gems below this (0 = no floor)
    "market_min_gold": 0,                   # never let a gold purchase take the gold below this (gem-priced goods are still bought)
    "tg_api_id": "",                        # from https://my.telegram.org -> API development tools
    "tg_api_hash": "",
    "tg_phone": "",                         # your number with country code, e.g. +911234567890
    "tg_boss_bot": "@EvonyBossBot",         # the bot whose answers hold the boss coordinates
    "tg_boss_command": "",                  # text sent to it to ask for bosses (as you type it in the chat)
    "tg_auto_min": 0,                       # ask again every N minutes (0 = only when you press the button)
    "tg_min_level": 0,                      # ignore bosses below this level when a task uses {boss_x} {boss_y}
    "tg_pick": "nearest",                   # nearest | highest
    "city_nav": "auto",                     # auto = find the camera by the whole-city picture (own city style), falling back to the squares; squares = always walk by the squares
    "city_tap_dy": 22,                      # px below the building's square that a city tap aims at (the lower part of the building; its height then does not matter)
    "tg_feed_chats": "",                    # chats / channels / bots to watch for coordinates (comma separated: @username, link or the name in your chat list)
    "tg_keep_min": 60,                      # forget a coordinate in the live list after this many minutes (0 = keep until cleared)
    "sidebar_open": True,                   # emulator list on the left (toggle with the header button, Ctrl+B or F9)
    "ui_pal": "ivory",                      # ivory | midnight | amber | sage
    "ui_mode": "light",                     # dark | light | auto
    "ui_accent": "",                        # "" = the theme's own colour, else #rrggbb
    "ui_btn": "raised", "ui_border": "subtle", "ui_round": "normal",   # raised|tinted, subtle|accent, sharp|normal|round
    "ui_text": 100,                         # whole-window text size in percent (85-150)
    "ui_name": 24,                          # emulator name heading size in px (18-40)
    "rally_enabled": True,                  # join rallies (first priority); off = the bot only does the tasks
    "game_reset_utc": "auto",                # when the game's daily reset happens: auto = Evony's own (midnight US Pacific, follows daylight saving), or HH:MM UTC
    "tasks_first": True,                    # a task/duty that is due goes before waiting for new rallies (a rally already on screen is always joined first)
    "city_enabled": False,                  # run the tasks (Tasks tab) while no rally is waiting
    "zoom_out": False,                      # zoom the city out (once per game start) before going to a building
    "zoom_foreground": False,               # zoom fallback: real Ctrl+wheel over the emulator window (moves the mouse ~2 s)
    "city_tasks": [],                       # [{name, enabled, target, steps, every_min}], see run_step()
    "duties": {},                           # {duty_id: {on, every_min}} - ready-made duties, see DUTY_LIBRARY
    "wheel": {"button": 1, "spins": 10, "reserve": 0, "chips": "off"},   # Tavern > Wheel of Fortune: biggest button (1/10/100), spins to do (0 = until the coins are used up), coins to keep
    "champions": {"plan": {}},                      # plan {champion: {taps, greets}}; empty = all on the champion the page opens on
    "crazy_eggs": {"eggs": {}, "lucky": "max", "min_hammers": 0},   # eggs {cost: {on, daily}}, lucky = egg cost / "max" / "" (none)
    "city_icons": {},                       # extra / changed right-side icon positions {name: [x, y]}
    "rally_nav_tap": [],                    # from the city to the rally list when there is no rally_icon picture [x, y]
    "city_to_tap": [35, 35],                # from the rally list to the city (Back arrow)
    "city_label_tap": [],                   # a spot on empty ground to tap so building names show while mapping
    "bot_presets": [],                      # the bot's own 6 presets (I-VI), see default_bot_presets(); [] = use "presets"
    "general_max_scrolls": 8,
    "general_view": "grid",                 # grid = thumbnails (fast) | list = detail rows (slow, reads names)
    "menu_dots_tap": [],                    # only if the 3-dots button is not found by its picture
    "general_on_fail": "skip",              # skip = do not join | march = march with the preset's generals                     # {"emulator-5556": "Main account"} shown instead of the ID
    "monster_presets": {},                  # {"Witch": [1, 2], "Pan": [3]} = presets (so general) per monster
    "monster_offset": [-21, -168],          # portrait centre relative to the Join word centre
    # legacy keys (no longer used by the bot, kept so an old GUI does not crash)
    "max_seconds_left": 0,
    "min_seconds_left": 0,
    "timer_source": "join_button",
    # presets
    "presets": [1],
    "preset_mode": "rotate",
    "preset_x": [55, 117, 178, 239, 300, 361, 423, 484],
    "preset_tap_y": 105,
    "bad_preset_seconds": 20,            # skip a preset this long if it fails to march (a rally only lasts ~5 min)
    "march_tap": [410, 910],
    "verify_march": True,
    # stamina: when the march needs more than we have, use stamina ITEMS (never gems) and carry on
    "stamina_auto": True,
    "stamina_sizes": [10, 25, 50, 100],      # which stamina items may be used
    "stamina_largest_first": True,
    "stamina_items": "all",                  # which items may be used: all / 100 only / 50 only / 10 only / 50 + 100
    "stamina_order": "large first",          # large first / small first
    "stamina_amount": "full (Max)",          # full (Max) = slide to the end (up to 9,999) / game default (2 items) / exact number
    "stamina_count": 2,                      # how many items when 'exact number'

    "stamina_xy": [385, 854],                # the stamina number on the march screen (opens the Use Item screen)
    # list scrolling: swipe x1,y1,x2,y2,ms ; go back to top after N scrolls (0 = never)
    "swipe": [270, 750, 270, 350, 300],
    "max_scrolls": 3,
}


def merge_device_cfg(cfg, serial):
    """Global settings + this emulator's own overrides. An emulator that only sets its own 'presets'
    list switches the bot presets on/off to match it (their other toggles and generals stay)."""
    ov = cfg.get("per_device", {}).get(serial, {})
    m = {**cfg, **ov}
    if "presets" in ov and "bot_presets" not in ov and cfg.get("bot_presets"):
        want = {int(x) for x in ov["presets"]}
        m["bot_presets"] = [{**bp, "enabled": int(bp.get("n", i + 1)) in want}
                            for i, bp in enumerate(cfg["bot_presets"])]
    return m


def live_settings(cfg, serial, seen):
    """Settings saved while this instance runs: take them over in place (every part of the bot holds this same dict),
    so a change works from the next action without Stop/Start. seen = [last config.json time]. -> True if taken over."""
    try:
        m = CONFIG.stat().st_mtime
    except OSError:
        return False
    if m == seen[0]:
        return False
    seen[0] = m
    try:
        new = merge_device_cfg(load_config(), serial)
        changed = [k for k, v in new.items() if cfg.get(k) != v]
        if not changed:                                      # the file was written (e.g. a duty record) but nothing changed
            return False
        cfg.update(new)
        set_pacing(cfg)
    except Exception:
        return False
    return True


# ------------------------------------------------------------------ log files
LOG_DIR = USER / "logs"
_log_lock = threading.Lock()
_SECRET_KEYS = ("telegram_token", "telegram_chat", "discord_webhook", "update_key", "tg_api_id", "tg_api_hash",
                "tg_phone")


def log_to_file(line):
    """Append a line to today's log file (kept: the last 7 days)."""
    try:
        with _log_lock:
            LOG_DIR.mkdir(exist_ok=True)
            f = LOG_DIR / f"bot_{time.strftime('%Y%m%d')}.log"
            new = not f.exists()
            with open(f, "a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%H:%M:%S')} {line}\n")
            if new:
                for old in sorted(LOG_DIR.glob("bot_*.log"))[:-7]:
                    old.unlink(missing_ok=True)
    except Exception:
        pass                                             # the logger itself: nowhere left to report to


_quiet_seen = {}


def quiet_err(where, e):
    """An error the bot can carry on after: one '[dbg]' line in the log file instead of nothing.
    The same place + error type is written at most once a minute, so a loop cannot flood the log."""
    try:
        now, key = time.time(), (where, type(e).__name__)
        if now - _quiet_seen.get(key, 0) < 60:
            return
        if len(_quiet_seen) > 500:
            _quiet_seen.clear()
        _quiet_seen[key] = now
        log_to_file(f"[dbg] {where}: {type(e).__name__}: {str(e)[:200]}")
    except Exception:
        pass                                             # never let error reporting break the caller


def recent_log(max_lines=2000):
    try:
        files = sorted(LOG_DIR.glob("bot_*.log"))
        lines = []
        for f in files[-3:]:
            lines += f.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-max_lines:])
    except Exception:
        return ""


_LOG_LINE = re.compile(r"^(\d\d):(\d\d):(\d\d) (?:\[([^\]]+)\])?")


def _log_files(hours):
    """The daily log files that can hold lines from the last `hours` hours, oldest first, with their day (epoch at 00:00)."""
    out = []
    first = time.strftime("%Y%m%d", time.localtime(time.time() - hours * 3600))
    for f in sorted(LOG_DIR.glob("bot_*.log")):
        day = f.stem[4:]
        if len(day) == 8 and day.isdigit() and day >= first:
            out.append((f, time.mktime(time.strptime(day, "%Y%m%d"))))
    return out


def log_names(hours=168):
    """Instance names that appear in the saved logs (for the 'only this instance' choice)."""
    names = set()
    for f, _ in _log_files(hours):
        try:
            for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
                m = _LOG_LINE.match(ln)
                if m and m.group(4) and len(m.group(4)) > 1 and m.group(4) not in ("perf",):
                    names.add(m.group(4))
        except Exception as _e:
            quiet_err("log_names", _e)
    return sorted(names, key=str.lower)


def export_log(path, hours=24, name=""):
    """Write a zip with the log lines of the last `hours` hours (only instance `name` if given). -> number of lines."""
    import zipfile
    cut = time.time() - hours * 3600
    tag = f"[{name}]" if name else ""
    lines, keep = [], False
    for f, day0 in _log_files(hours):
        try:
            text = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for ln in text:
            m = _LOG_LINE.match(ln)
            if m:                                          # a new line: keep it if it is in the time range (and the instance matches)
                t = day0 + int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
                keep = t >= cut and (not tag or ln[9:].startswith(tag))
                if keep:
                    lines.append(f"{f.stem[4:]} {ln}")
            elif keep:                                     # a continued line (e.g. an error trace) stays with its line
                lines.append(ln)
    head = f"RallyForge log - version {VERSION} - last {hours} h - {name or 'all instances'} - saved {time.strftime('%Y-%m-%d %H:%M')}\n"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(Path(path).stem + ".txt", head + "\n".join(lines) + "\n")
    return len(lines)


def build_report(path, include_debug=True, description="", devices=None):
    """A zip to send for support: the recent log, your settings without passwords / tokens, and the app version."""
    import zipfile, platform
    cfg = load_config()
    for k in _SECRET_KEYS:
        cfg.pop(k, None)
    cfg.pop("device_names", None)
    info = (f"RallyForge Bot {VERSION}\n{platform.platform()}\nPython {platform.python_version()}\n"
            f"Templates: {', '.join(sorted(list_templates('')))}\n")
    try:
        import ctypes

        class _MS(ctypes.Structure):
            _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                        ("tpf", ctypes.c_ulonglong), ("apf", ctypes.c_ulonglong), ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong),
                        ("ae", ctypes.c_ulonglong)]
        ms = _MS()
        ms.l = ctypes.sizeof(_MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
        info += f"PC memory: {ms.tp // 2**20} MB total, {ms.ap // 2**20} MB free\n"
    except Exception as _e:
        quiet_err("build_report", _e)
    info += f"CPU threads: {os.cpu_count()}\nSaved at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
    if devices:
        info += "Emulators: " + ", ".join(f"{d.get('name') or d.get('id')} ({d.get('id')})" for d in devices) + "\n"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("what_went_wrong.txt", (description or "(nothing written)").strip() + "\n")
        z.writestr("log.txt", recent_log(5000))
        for extra in ("startup.log", "duties_state.json"):
            f = USER / extra
            if f.exists():
                z.write(f, extra)
        z.writestr("settings.json", json.dumps(cfg, indent=2, default=str))
        z.writestr("info.txt", info)
        dbg = USER / "debug"
        if include_debug and dbg.exists():
            for img in sorted(dbg.glob("*.png"), key=lambda f: f.stat().st_mtime)[-8:]:      # the newest ones
                z.write(img, f"debug/{img.name}")


def migrate_cfg(cfg):
    """Older configs pointed the general tap at the magnifier (x~477), which opens the info view."""
    if not cfg.get("_reset_auto_v1"):                   # one time (owner 23 Oct): the old default 00:00 UTC was wrong and 07:00
        cfg["_reset_auto_v1"] = True                    # does not follow daylight saving - both become Evony's own reset time
        if str(cfg.get("game_reset_utc") or "").strip() in ("", "00:00", "0:00", "07:00", "7:00"):
            cfg["game_reset_utc"] = "auto"
        set_game_reset(cfg)
    for k, new in (("general_main_tap", [90, 532]), ("general_assistant_tap", [90, 688])):
        v = cfg.get(k)
        if not v or (len(v) == 2 and v[0] > 300):
            cfg[k] = new
    try:                                             # Viking sets used to be one on/off switch stored as [1]; they now have levels
        vs = globals().get("VIKING_SETS") or {}
        for box in [cfg] + list((cfg.get("per_device") or {}).values()):
            ms = box.get("monster_skip") if isinstance(box, dict) else None
            for nm, lv in list((ms or {}).items()):
                if nm in vs and [int(x) for x in lv] == [1]:
                    ms[nm] = [l for l, _p in vs[nm]]
                elif nm == "Viking (Easy)" and set(int(x) for x in lv) == set(VIKING_EASY_MEASURED):
                    ms[nm] = [l for l, _p in vs[nm]]          # Easy used to list only 6 levels: "skip all" stays "skip all" with 1-50
    except Exception as _e:
        quiet_err("migrate_cfg", _e)
    try:                                             # 'Junior Hydra' used to be its own monster: it is a level of 'Hydra'
        for box in [cfg] + list((cfg.get("per_device") or {}).values()):
            if not isinstance(box, dict):
                continue
            if isinstance(box.get("monster_list"), list):
                box["monster_list"] = list(dict.fromkeys("Hydra" if n == "Junior Hydra" else n for n in box["monster_list"]))
            for k in ("monster_skip", "monster_presets"):
                d = box.get(k)
                if isinstance(d, dict) and "Junior Hydra" in d:
                    v = d.pop("Junior Hydra")
                    d.setdefault("Hydra", v)
    except Exception as _e:
        quiet_err("migrate_cfg", _e)
    for box in [cfg] + [v for v in (cfg.get("per_device") or {}).values() if isinstance(v, dict)]:
        if box.get("march_timer_source") == "join_button":    # nothing is there to read (always 'march ?'); 'Spare time' covers the march
            box["march_timer_source"] = ""
    if cfg.get("bad_preset_seconds") == 120:             # the old default was far too long for a 5-minute rally
        cfg["bad_preset_seconds"] = 20
    if not cfg.get("_stamina_full_default"):             # 2.4.53: stamina items fill up to Max by default (once; a later change by the user stays)
        cfg["_stamina_full_default"] = True
        if cfg.get("stamina_amount", "game default") == "game default":
            cfg["stamina_amount"] = "full (Max)"
    return cfg


def load_config():
    cfg = dict(DEFAULTS)
    for path in (RES / "config.json", CONFIG):          # a config bundled in the .exe first, then your own
        if path.exists():
            try:
                cfg.update(json.loads(path.read_text()))
            except Exception as _e:
                quiet_err("load_config", _e)
    set_game_reset(cfg)
    return migrate_cfg(cfg)


def save_json(path, obj, indent=1, encoding="utf-8"):
    """Write JSON safely: write a temp file next to it, flush it to disk, then swap it in. A crash or power cut
    mid-write leaves the old file whole instead of a half-written one. Windows can refuse the swap for a moment
    while another thread reads the file, so it retries a few times. Raises OSError like write_text did."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    data = json.dumps(obj, indent=indent)
    try:
        with open(tmp, "w", encoding=encoding) as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        for i in range(6):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if i == 5:
                    raise
                time.sleep(0.05 * (i + 1))
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def save_config(cfg):
    save_json(CONFIG, cfg, indent=2, encoding=None)
    set_game_reset(cfg)


_RESET_MIN = [None]                       # minutes after 00:00 UTC at which the game day changes; None = Evony's own (auto)


def evony_reset_min(t=None):
    """Evony's daily reset is midnight US Pacific time and follows US daylight saving: 07:00 UTC in summer
    (second Sunday of March to first Sunday of November), 08:00 UTC in winter."""
    import calendar
    t = time.time() if t is None else t
    y = time.gmtime(t).tm_year
    def nth_sunday(month, n):
        first = calendar.weekday(y, month, 1)               # Monday = 0 ... Sunday = 6
        return 1 + (6 - first) % 7 + 7 * (n - 1)
    start = calendar.timegm((y, 3, nth_sunday(3, 2), 10, 0, 0))   # 02:00 PST = 10:00 UTC
    end = calendar.timegm((y, 11, nth_sunday(11, 1), 9, 0, 0))    # 02:00 PDT = 09:00 UTC
    return 7 * 60 if start <= t < end else 8 * 60


def reset_min(t=None):
    return evony_reset_min(t) if _RESET_MIN[0] is None else _RESET_MIN[0]


def set_game_reset(cfg):
    v = str((cfg or {}).get("game_reset_utc") or "auto").strip().lower()
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", v)
    _RESET_MIN[0] = int(m.group(1)) * 60 + int(m.group(2)) if m else None


# ------------------------------------------------------------ tool discovery
def adb_candidates():
    roots = [os.environ.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "LOCALAPPDATA")]
    roots = [r for r in roots if r] + ["C:\\", "D:\\"]
    rel = [
        "BlueStacks_nxt/HD-Adb.exe", "BlueStacks_nxt_cn/HD-Adb.exe", "BlueStacks/HD-Adb.exe",
        "LDPlayer/LDPlayer9/adb.exe", "LDPlayer/LDPlayer4.0/adb.exe", "LDPlayer/LDPlayer3.0/adb.exe",
        "leidian/LDPlayer9/adb.exe",
        "Nox/bin/adb.exe", "Nox/bin/nox_adb.exe",
        "Microvirt/MEmu/adb.exe",
        "Netease/MuMuPlayer-12.0/shell/adb.exe", "Netease/MuMuPlayerGlobal-12.0/shell/adb.exe",
        "Genymobile/Genymotion/tools/adb.exe",
        "Android/Sdk/platform-tools/adb.exe",
    ]
    return [Path(r) / p for r in roots for p in rel]


def find_adb(configured="auto"):
    if configured and configured != "auto" and Path(configured).exists():
        return configured
    sys_adb = shutil.which("adb")
    cands = [RES / "platform-tools" / "adb.exe"]
    if sys_adb:
        cands.append(Path(sys_adb))
    cands += adb_candidates()
    for c in cands:
        if c.exists():
            return str(c)
    return sys_adb


def setup_tesseract():
    bundled = RES / "tesseract" / "tesseract.exe"
    cands = [bundled,
             Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
             Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe")]
    for c in cands:
        if c.exists():
            pytesseract.pytesseract.tesseract_cmd = str(c)
            if c == bundled:
                os.environ["TESSDATA_PREFIX"] = str(RES / "tesseract" / "tessdata")
            return True
    return shutil.which("tesseract") is not None


TAP_JITTER_PX = 3                           # random offset of every tap (pixels at 540x960)


_BS_DEFAULT = (r"C:\ProgramData\BlueStacks_nxt\bluestacks.conf",
               r"C:\ProgramData\BlueStacks_nxt_cn\bluestacks.conf",
               r"C:\ProgramData\BlueStacks\bluestacks.conf")


def bluestacks_confs():
    """Every bluestacks.conf that can be found: the usual places, any BlueStacks* folder under ProgramData, the data folder
    BlueStacks wrote into the registry (it can be on another drive), and the other drives' ProgramData."""
    now = time.time()
    if _BS_CACHE[1] and now - _BS_CACHE[0] < 60:
        return list(_BS_CACHE[1])
    out = list(_BS_DEFAULT)
    try:
        import glob
        roots = [os.environ.get("ProgramData", r"C:\ProgramData")] + [f"{d}:\\ProgramData" for d in "CDEFGH"]
        for root in roots:                                   # BlueStacks can keep its data on another drive (e.g. E:\ProgramData)
            if os.path.isdir(root):
                out += glob.glob(os.path.join(root, "BlueStacks*", "bluestacks.conf"))
    except Exception as _e:
        quiet_err("bluestacks_confs", _e)
    try:
        import winreg
        for key in ("SOFTWARE\\BlueStacks_nxt", "SOFTWARE\\BlueStacks_nxt_cn", "SOFTWARE\\BlueStacks", "SOFTWARE\\BlueStacks_msi5"):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0, winreg.KEY_READ | view) as k:
                        for val in ("DataDir", "UserDefinedDir"):
                            try:
                                d = winreg.QueryValueEx(k, val)[0]
                                out += [os.path.join(d, "bluestacks.conf"), os.path.join(os.path.dirname(d.rstrip("\\/")), "bluestacks.conf")]
                            except OSError as _e:
                                quiet_err("bluestacks_confs", _e)
                except OSError as _e:
                    quiet_err("bluestacks_confs", _e)
    except Exception as _e:
        quiet_err("bluestacks_confs", _e)
    seen, res = set(), []
    for c in out:
        if c.lower() not in seen and os.path.isfile(c):
            seen.add(c.lower())
            res.append(c)
    _BS_CACHE[0], _BS_CACHE[1] = now, list(res)
    return res


_BS_CACHE = [0.0, []]
BLUESTACKS_CONFS = tuple(_BS_DEFAULT)


def mumu_names(adb=None):
    """{127.0.0.1:port: instance name} from MuMu 12's own manager."""
    mgr = mumu_manager(adb)
    if not mgr:
        return {}
    found = {}

    def walk(o, key=None):
        if isinstance(o, dict):
            port = o.get("adb_port")
            nm = o.get("name") or o.get("player_name") or o.get("title")
            if port and nm:
                found[f"127.0.0.1:{port}"] = str(nm)
            for k, v in o.items():
                walk(v, k)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    try:
        out = subprocess.run([str(mgr), "info", "-v", "all"], capture_output=True, text=True,
                             timeout=20, creationflags=NOWIN).stdout
        walk(json.loads(out))
    except Exception as _e:
        quiet_err("mumu_names", _e)
    return found


def emulator_names(adb=None):
    """Best effort: map device id -> the instance name shown in the emulator (MuMu, BlueStacks, LDPlayer)."""
    names = dict(mumu_names(adb))
    for conf in bluestacks_confs():
        try:
            txt = Path(conf).read_text(errors="ignore")
        except Exception:
            continue
        ports = dict(re.findall(r'bst\.instance\.(\w+)\.adb_port="(\d+)"', txt))
        disp = dict(re.findall(r'bst\.instance\.(\w+)\.display_name="([^"]*)"', txt))
        for inst, port in ports.items():
            n = disp.get(inst) or inst
            names[f"127.0.0.1:{port}"] = n
            names[f"emulator-{int(port) - 1}"] = n
    dirs = [Path(adb).parent] if adb else []
    dirs += [c.parent for c in adb_candidates() if "ldplayer" in str(c).lower() or "leidian" in str(c).lower()]
    for d in dirs:
        con = d / "ldconsole.exe"
        if not con.exists():
            continue
        try:
            out = subprocess.run([str(con), "list2"], capture_output=True, text=True,
                                 creationflags=NOWIN, timeout=8).stdout
        except Exception:
            continue
        for line in out.splitlines():
            parts = line.split(",")
            if len(parts) >= 2 and parts[0].strip().isdigit():
                idx = int(parts[0])
                names[f"emulator-{5554 + 2 * idx}"] = parts[1].strip()
        break
    return names


def candidate_ports():
    ports = set()
    for conf in bluestacks_confs():
        try:
            ports |= {int(p) for p in re.findall(r'adb_port="(\d+)"', Path(conf).read_text(errors="ignore"))}
        except Exception as _e:
            quiet_err("candidate_ports", _e)
    ports |= {5555 + 10 * i for i in range(12)}            # BlueStacks / generic (12 instances)
    ports |= {62001, 62025, 62026, 62027}                 # Nox
    ports |= {21503 + 10 * i for i in range(12)}          # MEmu
    ports |= {7555} | {16384 + 32 * i for i in range(16)}  # MuMu (16 instances - 8 was too few for big farms)
    # LDPlayer registers itself as emulator-5554, 5556... and shows up in `adb devices` by itself.
    return sorted(ports)


def _try_connect(adb, port):
    try:
        subprocess.run([adb, "connect", f"127.0.0.1:{port}"], capture_output=True,
                       creationflags=NOWIN, timeout=4)
    except Exception as _e:
        quiet_err("_try_connect", _e)


def detect_devices(adb):
    """Return ids of all working emulators/devices (BlueStacks, LDPlayer, Nox, MEmu, MuMu, ...)."""
    if not adb:
        return []
    with ThreadPoolExecutor(max_workers=16) as ex:
        for p in candidate_ports():
            ex.submit(_try_connect, adb, p)
    try:
        out = subprocess.run([adb, "devices"], capture_output=True, text=True,
                             creationflags=NOWIN, timeout=15).stdout
    except Exception:
        return []
    return [l.split("\t")[0] for l in out.splitlines()[1:] if l.strip().endswith("device")]


def resolve_devices(cfg, adb, log):
    want = cfg.get("devices") or [cfg["device_id"]]
    if isinstance(want, str):
        want = [want]
    if "auto" in want:
        found = detect_devices(adb)
        log(f"[+] Detected {len(found)} device(s): {', '.join(found) or 'none'}")
        return found
    return list(dict.fromkeys(want))


# ------------------------------------------------------------------- device
class Device:
    def __init__(self, serial, adb):
        self.serial, self.adb = serial, adb
        self.mode = "auto"                   # auto | png (set from the config)
        self.raw_fail = 0
        self.used = "png"
        self.real = EXPECTED_SIZE            # real screen size, updated on every screenshot
        self.sx = self.sy = 1.0              # real / 540x960 scale used for taps
        self.guard = None                    # (cfg, log, stop): the universal pop-up closer (see arm_guard)
        self.guard_city = False              # True while a task works on the city screen: an unknown screen = a pop-up
        self.expect_city = False             # True while a duty walks over the city: any window that is not the city = unauthorised, closed at once
        self._guard_t = 0.0
        self._guard_busy = False
        self._guard_unknown = 0

    def arm_guard(self, cfg, log, stop):
        """Turn on the universal pop-up closer: whatever the bot is doing, a known pop-up that appears is closed."""
        self.guard = (cfg, log, stop)

    def _guard_check(self, img):
        """At most every 2 s, on a screenshot that is being taken anyway (no extra load): close a known pop-up
        (popup_* pictures) and, while a city task is running, any unknown screen (Back / top-left arrow)."""
        cfg, log, stop = self.guard
        self._guard_busy = True
        try:
            self._guard_t = time.time()
            th = min(cfg.get("threshold", 0.85), 0.85)
            for name in list_templates("popup_"):
                if name == "popup_exit_cancel":
                    continue
                h = find(img, name, th)
                if h:
                    tap_hit(self, h)
                    log(f"[*] Closed a pop-up ({name})")
                    self._guard_unknown = 0
                    time.sleep(0.8)
                    return self.screenshot()
            if self.guard_city and has_template("city_screen") and screen_state(img, cfg) == "unknown":
                self._guard_unknown += 1
                if self._guard_unknown >= 2:
                    self._guard_unknown = 0
                    if clear_popups(self, cfg, log, stop, rounds=3):
                        return self.screenshot()
            elif getattr(self, "expect_city", False) and has_template("city_screen") and screen_state(img, cfg) == "unknown":
                self._guard_unknown += 1
                if self._guard_unknown >= 2:                    # two looks in a row at something that is not the city: an unauthorised window
                    self._guard_unknown = 0
                    log("[!] An unexpected window is open while walking over the city - closing it")
                    ensure_view(self, cfg, stop, log, "city", tries=6)
                    return self.screenshot()
            else:
                self._guard_unknown = 0
        except Exception as _e:
            quiet_err("Device._guard_check", _e)
        finally:
            self._guard_busy = False
        return img

    def _run(self, *a, timeout=20):
        if len(a) > 1 and a[0] == "shell" and a[1] == "input":      # a tap / swipe / text: never while the instance is paused
            pause_gate()
            dl = getattr(_TL, "duty_deadline", None)         # a duty over its time budget: its next action aborts it (the runner
            if dl and time.time() > dl:                       # then closes pop-ups, leaves to the city and tries once more)
                _TL.duty_deadline = None
                raise RuntimeError("took too long - stopped (a window or page it does not know?)")
        return subprocess.run([self.adb, "-s", self.serial, *a], capture_output=True,
                              timeout=timeout, creationflags=NOWIN)

    def text(self, value):
        """Type text into the focused box (digits and letters)."""
        safe = re.sub(r"[^A-Za-z0-9.,@_-]", "", str(value))
        if safe:
            self._run("shell", "input", "text", safe)

    def connect(self):
        if ":" in self.serial:
            subprocess.run([self.adb, "connect", self.serial], capture_output=True,
                           creationflags=NOWIN, timeout=10)
        out = subprocess.run([self.adb, "devices"], capture_output=True, text=True,
                             creationflags=NOWIN, timeout=15).stdout
        if f"{self.serial}\tdevice" not in out:
            raise RuntimeError(f"Device {self.serial} not available. Turn on ADB in the emulator "
                               "settings (BlueStacks: Settings > Advanced > Android Debug Bridge) "
                               "and check the device ID.")

    def ensure_screen(self, log=None):
        """Startup check: the emulator screen must be 540x960 at 240 dpi. Reads `wm size` / `wm density`, sets them when
        different and checks again. -> (True, message) when it is right, (False, message) when it could not be fixed."""
        say = log or (lambda m: None)

        def read():
            size = dens = None
            try:
                out = (self._run("shell", "wm", "size", timeout=10).stdout or b"").decode("utf8", "ignore")
                found = re.findall(r"(?:Override|Physical) size:\s*(\d+)x(\d+)", out)
                if found:
                    size = tuple(map(int, found[-1]))          # an override line comes last and wins
                out = (self._run("shell", "wm", "density", timeout=10).stdout or b"").decode("utf8", "ignore")
                found = re.findall(r"(?:Override|Physical) density:\s*(\d+)", out)
                if found:
                    dens = int(found[-1])
            except Exception as _e:
                quiet_err("ensure_screen.read", _e)
            return size, dens

        want_size, want_dpi = EXPECTED_SIZE, 240
        size, dens = read()
        if size == want_size and dens == want_dpi:
            return True, f"Screen {size[0]}x{size[1]} at {dens} dpi - OK"
        say(f"[!] Screen is {size and '%dx%d' % size} at {dens} dpi, expected {want_size[0]}x{want_size[1]} at {want_dpi} dpi - trying to set it")
        try:
            self._run("shell", "wm", "size", f"{want_size[0]}x{want_size[1]}", timeout=10)
            self._run("shell", "wm", "density", str(want_dpi), timeout=10)
        except Exception as _e:
            quiet_err("Device.ensure_screen", _e)
        time.sleep(2.0)
        size, dens = read()
        if size == want_size and dens == want_dpi:
            return True, f"Screen set to {size[0]}x{size[1]} at {dens} dpi"
        return False, (f"Could not set the screen to {want_size[0]}x{want_size[1]} at {want_dpi} dpi (it is "
                       f"{size and '%dx%d' % size} at {dens} dpi). Set the resolution in the emulator's own settings.")

    def _grab_raw(self):
        """Raw pixels are much lighter for the emulator than PNG (no compression on its side)."""
        r = self._run("exec-out", "screencap", timeout=15)
        data = r.stdout or b""
        if len(data) < 16:
            return None
        w, h, fmt = struct.unpack("<III", data[:12])
        if not (100 <= w <= 4000 and 100 <= h <= 4000) or fmt not in (1, 2, 5):
            return None
        need = w * h * 4
        for hdr in (12, 16):
            if len(data) == hdr + need:
                px = np.frombuffer(data, np.uint8, count=need, offset=hdr).reshape(h, w, 4)
                return cv2.cvtColor(px, cv2.COLOR_BGRA2BGR if fmt == 5 else cv2.COLOR_RGBA2BGR)
        return None

    def _grab(self):
        if self.mode != "png" and self.raw_fail < 3:
            try:
                img = self._grab_raw()
            except Exception:
                img = None
            if img is not None:
                self.used = "raw"
                return img
            self.raw_fail += 1
            self.used = "png"
        r = self._run("exec-out", "screencap", "-p", timeout=15)
        img = None
        if r.stdout:
            img = cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)
            if img is None:                  # very old adb mangles line endings
                img = cv2.imdecode(np.frombuffer(r.stdout.replace(b"\r\n", b"\n"), np.uint8),
                                   cv2.IMREAD_COLOR)
        if img is None:                      # fallback: screencap to device, pull
            safe = re.sub(r"\W", "_", self.serial)
            local = Path(tempfile.gettempdir()) / f"evony_{safe}.png"
            self._run("shell", "screencap", "-p", "/sdcard/evony_screen.png")
            self._run("pull", "/sdcard/evony_screen.png", str(local))
            img = cv2.imread(str(local))
        if img is None:
            raise RuntimeError("Screenshot failed")
        return img

    def screenshot(self):
        """Always returns a 540x960 image so templates work on any emulator resolution."""
        t0 = time.time()
        img = self._grab()
        p = getattr(_TL, "perf", None)
        if p is not None:
            p["shots"] += 1
            p["shot_ms"] += (time.time() - t0) * 1000
        h, w = img.shape[:2]
        self.real = (w, h)
        if (w, h) != EXPECTED_SIZE:
            self.sx, self.sy = w / EXPECTED_SIZE[0], h / EXPECTED_SIZE[1]
            img = cv2.resize(img, EXPECTED_SIZE, interpolation=cv2.INTER_AREA)
        else:
            self.sx = self.sy = 1.0
        if self.guard and not self._guard_busy and time.time() - self._guard_t > 2.0:
            img = self._guard_check(img)
        if not getattr(self, "_lag_busy", False):
            img = self._lag_wait(img)
        return img

    def _lag_wait(self, img):
        """Evony shows a red wifi icon mid-screen while the game lags (usually the PC is overloaded); taps made then
        get lost and rallies are missed. Owner 25 Oct: hold this instance still until the icon goes (up to 2 min), then
        let the game settle 20 s with no icon before the next tap. Only a lag of 2+ min non-stop counts as a strike
        (lag_streak) - the rally loop restarts Evony on that. Time spent here does not count as 'lost' (lag_spent)."""
        try:
            if not lag_icon(img):
                return img
        except Exception:
            return img
        self._lag_busy = True
        log = self.guard[1] if self.guard else None
        stop = self.guard[2] if self.guard else None
        self.lag_count = getattr(self, "lag_count", 0) + 1
        t0 = time.time()
        self.lag_times = [t for t in getattr(self, "lag_times", []) if t0 - t < 600] + [t0]   # lag spells in the last 10 min
        stopped = lambda: stop is not None and stop.is_set()
        try:
            if log:
                log(f"[!] Game lagging (red wifi icon) - waiting for it to clear (#{self.lag_count} this run)")
            calm_from = None
            while time.time() - t0 < 120 and not stopped():
                time.sleep(2.0)
                img = self.screenshot()
                if lag_icon(img):
                    calm_from = None
                    continue
                calm_from = calm_from or time.time()
                if time.time() - calm_from >= 20:                # 20 s with no icon: the game has caught up
                    self.lag_streak = 0
                    if log:
                        log(f"[+] Game settled after {time.time() - t0:.0f}s")
                    return img
            if not stopped():
                self.lag_streak = getattr(self, "lag_streak", 0) + 1
                if log:
                    log(f"[!] Still lagging after 2 min - carrying on ({self.lag_streak} in a row)")
        finally:
            self.lag_spent = getattr(self, "lag_spent", 0.0) + (time.time() - t0)
            self._lag_busy = False
        return img

    def tap(self, x, y):
        self.acted = time.time()
        j = TAP_JITTER_PX                                   # a few pixels off each time, like a finger (never the exact same pixel)
        x, y = x + random.randint(-j, j), y + random.randint(-j, j)
        self._run("shell", "input", "tap", str(int(x * self.sx)), str(int(y * self.sy)))

    def swipe(self, x1, y1, x2, y2, ms=300):
        self.acted = time.time()
        self._run("shell", "input", "swipe", str(int(x1 * self.sx)), str(int(y1 * self.sy)),
                  str(int(x2 * self.sx)), str(int(y2 * self.sy)), str(int(ms)))

    def swipe_bg(self, x1, y1, x2, y2, ms=2500):
        """A slow swipe that keeps running while the caller does something else (the finger stays down)."""
        t = threading.Thread(target=self.swipe, args=(x1, y1, x2, y2, ms), daemon=True)
        t.start()
        return t

    def hold_start(self, x, y, x2, y2):
        """Put a finger on the screen at (x, y), move it to (x2, y2) in a few small steps and KEEP IT DOWN
        (the game shows the building names only while a finger that has moved is still down). Call hold_end()
        to lift it. -> True when the finger is held; False when this emulator cannot hold a touch (then use swipe_bg)."""
        if getattr(self, "no_motion", False):
            return False
        sx, sy = self.sx, self.sy
        r = self._run("shell", "input", "motionevent", "DOWN", str(int(x * sx)), str(int(y * sy)))
        if r.returncode != 0 or b"nknown" in (r.stderr or b"") + (r.stdout or b""):
            self.no_motion = True
            return False
        for i in range(1, 5):
            self._run("shell", "input", "motionevent", "MOVE", str(int((x + (x2 - x) * i / 4) * sx)),
                      str(int((y + (y2 - y) * i / 4) * sy)))
        self._held = (x2, y2)
        return True

    def hold_end(self):
        h = getattr(self, "_held", None)
        if h:
            for k in (1, 2, 3):                                  # keep dragging a little before lifting, so the game never reads it as a tap
                self._run("shell", "input", "motionevent", "MOVE", str(int(h[0] * self.sx)),
                          str(int((h[1] - 14 * k) * self.sy)))
            h = (h[0], h[1] - 42)
            self._run("shell", "input", "motionevent", "UP", str(int(h[0] * self.sx)), str(int(h[1] * self.sy)))
            self._held = None

    def _touch_dev(self):
        """Find the emulator's touch screen device and its raw ranges (cached). -> (path, maxx, maxy, slots) or None."""
        t = getattr(self, "_touch", 0)
        if t != 0:
            return t
        info = None
        try:
            out = self._run("shell", "getevent", "-p", timeout=10).stdout.decode("utf-8", "ignore")
            for blk in re.split(r"(?m)^add device \d+:", out)[1:]:
                path = blk.strip().split()[0]
                mx = re.search(r"(?:0035|ABS_MT_POSITION_X)\s*:[^\n]*?max (\d+)", blk)
                my = re.search(r"(?:0036|ABS_MT_POSITION_Y)\s*:[^\n]*?max (\d+)", blk)
                if mx and my:
                    sl = re.search(r"(?:002f|ABS_MT_SLOT)\s*:[^\n]*?max (\d+)", blk)
                    info = (path, int(mx.group(1)), int(my.group(1)), bool(sl))
                    break
        except Exception:
            info = None
        self._touch = info
        return info

    def pinch_in(self, cx=270, cy=480, start=230, end=45, steps=12, delay=0.04, tilt=0.6, pressure=0, major=0, fingers=2, dev_path=None):
        """Two fingers move TOWARD each other (the game zooms OUT). Raw multi-touch events sent in one shell
        command. (cx, cy, start, end) are in 540x960 screen pixels. -> True when the events were sent."""
        t = self._touch_dev()
        if not t:
            return False
        path, mx, my, has_slot = t
        if dev_path:
            path = dev_path
        w, h = EXPECTED_SIZE
        fx, fy = mx / float(w), my / float(h)
        cmds = []

        def ev(a, b, c):
            cmds.append(f"sendevent {path} {a} {b} {c}")

        def pos(slot, x, y):
            if has_slot:
                ev(3, 47, slot)
            ev(3, 53, int(x * fx))
            ev(3, 54, int(y * fy))
            if pressure:
                ev(3, 58, pressure)
            if major:
                ev(3, 48, major)
            if not has_slot:
                ev(0, 2, 0)                                  # SYN_MT_REPORT
        d = start
        for i in range(steps + 1):
            d = start + (end - start) * i / steps
            if i == 0:
                if has_slot:
                    ev(3, 47, 0); ev(3, 57, 801)
                    pos(0, cx - d, cy - d * tilt)
                    if fingers > 1:
                        ev(3, 47, 1); ev(3, 57, 802)
                else:
                    pass
                ev(1, 330, 1)
            pos(0, cx - d, cy - d * tilt)
            if fingers > 1:
                pos(1, cx + d, cy + d * tilt)
            ev(0, 0, 0)
            cmds.append(f"sleep {delay}")
        if has_slot:
            ev(3, 47, 0); ev(3, 57, -1)
            if fingers > 1:
                ev(3, 47, 1); ev(3, 57, -1)
        ev(1, 330, 0)
        ev(0, 0, 0)
        r = self._run("shell", "sh", "-c", "'" + "; ".join(cmds) + "'", timeout=30)
        self.last_pinch = f"touch {path} max {mx}x{my} slots={has_slot} rc={r.returncode} {(r.stderr or b'')[:120]!r}"
        return r.returncode == 0

    def back(self):
        self._run("shell", "input", "keyevent", "4")


# ------------------------------------------------------------------- vision
_cache = {}
_cache_lock = threading.Lock()


def template_path(name):
    for base in (USER / "templates", RES / "templates"):
        p = base / f"{name}.png"
        if p.exists():
            return p
    return None


LAG_REGION = (120, 330, 300, 300)            # the red wifi "lag" icon sits in the middle of the screen


def lag_icon(img):
    """True while Evony's red wifi (connection lag) icon is on screen. Needs lag_wifi.png."""
    if not has_template("lag_wifi"):
        return False
    x, y, w, h = LAG_REGION
    area, t = img[y:y + h, x:x + w], tpl("lag_wifi")
    for f in (1.0, 0.9, 1.1):
        tt = t if f == 1.0 else cv2.resize(t, None, fx=f, fy=f)
        if float(cv2.matchTemplate(area, tt, cv2.TM_CCOEFF_NORMED).max()) >= 0.6:
            return True
    return False


def has_template(name):
    return template_path(name) is not None


def save_template(name, img):
    cv2.imwrite(str(USER / "templates" / f"{name}.png"), img)
    with _cache_lock:
        _cache.pop(name, None)


def tpl(name):
    with _cache_lock:
        if name not in _cache:
            p = template_path(name)
            if not p:
                raise FileNotFoundError(f"Missing template '{name}'")
            _cache[name] = cv2.imread(str(p))
        return _cache[name]


def find_all(screen, name, threshold=0.85, region=None):
    ox = oy = 0
    area = screen
    if region:
        x, y, w, h = region
        area, ox, oy = screen[y:y + h, x:x + w], x, y
    t = tpl(name)
    th, tw = t.shape[:2]
    if area.shape[0] < th or area.shape[1] < tw:
        return []
    res = cv2.matchTemplate(area, t, cv2.TM_CCOEFF_NORMED)
    ys, xs = np.where(res >= threshold)
    hits = []
    for y, x in sorted(zip(ys, xs), key=lambda p: -res[p[0], p[1]]):
        if all(abs(x + ox - (h[0] - tw // 2)) > tw or abs(y + oy - (h[1] - th // 2)) > th for h in hits):
            hits.append((int(x + ox + tw // 2), int(y + oy + th // 2), float(res[y, x]), (tw, th)))
    return sorted(hits, key=lambda h: h[1])


def find(screen, name, threshold=0.85, region=None):
    h = find_all(screen, name, threshold, region)
    return h[0] if h else None


STATE_REGIONS = {                       # where those pictures live: searching only there is ~10x lighter
    "general_screen": (80, 0, 380, 90),
    "march_screen": (40, 850, 280, 110),
    "city_screen": (380, 810, 160, 150),
    "rally_screen": (0, 840, 330, 120),
}


def _state(screen, cfg, full):
    th = cfg["threshold"]
    _TL.joins_seen = False

    def seen(name):
        return has_template(name) and bool(find(screen, name, th, None if full else STATE_REGIONS.get(name)))

    if seen("general_screen"):
        return "general"
    if seen("march_screen"):
        return "march"
    if seen("city_screen"):
        return "city"
    if has_template("join_button") and find_all(screen, "join_button", th, cfg["search_region"]):
        _TL.joins_seen = True
        return "rally_list"
    if seen("rally_screen"):
        return "rally_list"
    return "unknown"


def screen_state(screen, cfg):
    """'general' | 'march' | 'city' | 'rally_list' | 'unknown'.
    Uses join_button plus the optional templates march_screen / city_screen / rally_screen.
    They are looked for in their usual place first; the whole screen is only searched now and then,
    for pictures you captured somewhere else."""
    st = _state(screen, cfg, False)
    if st == "unknown" and time.time() - getattr(_TL, "full_at", 0) > 5:
        _TL.full_at = time.time()
        st = _state(screen, cfg, True)
    return st


TIMER_BOXES = {                       # offsets from Join-word centre, at 540x960
    "join_button": (-45, 10, 45, 30),
    "progress_bar": (-300, -2, -170, 23),
}


_ocr_cache = {}
_ocr_lock = threading.Lock()


def read_seconds(screen, hit, source="join_button", alt=0):
    cx, cy = hit[0], hit[1]
    x1, y1, x2, y2 = TIMER_BOXES[source]
    crop = screen[max(cy + y1, 0):cy + y2, max(cx + x1, 0):cx + x2]
    if crop.size == 0:
        return None
    key = hashlib.md5(crop.tobytes() + bytes([alt])).digest()           # the same picture = the same answer
    p = getattr(_TL, "perf", None)
    with _ocr_lock:
        if key in _ocr_cache:
            if p is not None:
                p["hits"] += 1
            return _ocr_cache[key]
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, None, fx=(4, 3, 6)[alt], fy=(4, 3, 6)[alt], interpolation=cv2.INTER_CUBIC)
    if alt == 0:
        _, b = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    elif alt == 1:
        _, b = cv2.threshold(g, 150, 255, cv2.THRESH_BINARY)
    else:
        b = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, -8)
    if b.mean() < 127:
        b = 255 - b
    b = cv2.copyMakeBorder(b, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)
    with _ocr_gate:                                       # few Tesseract processes at once = less RAM and CPU
        t0 = time.time()
        txt = pytesseract.image_to_string(
            b, config="--psm 7 -c tessedit_char_whitelist=0123456789:").strip()
    if p is not None:
        p["ocr"] += 1
        p["ocr_ms"] += (time.time() - t0) * 1000
    m = re.search(r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})", txt)
    secs = None
    if m:
        h, mi, sec = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
        secs = h * 3600 + mi * 60 + sec
    with _ocr_lock:
        if len(_ocr_cache) > 600:
            _ocr_cache.clear()
        _ocr_cache[key] = secs
    return secs


# ----------------------------------------------------------------- monsters
MON_SIZE = 76            # portrait template size at 540x960
MON_TOP = 195            # anything above this y is hidden under the header/tabs
_mon_cache = {}
_mon_names = [0.0, []]


def monster_dirs():
    return [USER / "monsters", RES / "monsters"]


def _base_monster(stem):
    """'Kraken #2' (another look of the same monster) counts as 'Kraken'."""
    n = re.sub(r"\s#\d+$", "", stem)
    n = {"Bayar Knight": "Knight Bayard", "Bayard Knight": "Knight Bayard"}.get(n, n)
    if n in ("Epic Hydra", "Mythical Hydra"):                                   # the two extra Hydra names in the rally list
        return "Hydra"
    m = re.fullmatch(r"(?:Junior|Medium|Senior|Legendary)\s+(.+)", n)       # 'Senior Hydra' is a level of 'Hydra'
    if m:
        rest = {"Bayar Knight": "Knight Bayard", "Bayard Knight": "Knight Bayard", "Knight Bayard": "Knight Bayard", "Bayar": "Knight Bayard"}.get(m.group(1), m.group(1))
        if rest in MONSTER_NAMES:
            return rest
    return n


def _raw_monsters():
    names = set()
    for d in monster_dirs():
        if d.exists():
            names |= {p.stem for p in d.glob("*.png")}
    return sorted(names, key=str.lower)


def list_monsters(refresh=False):
    if refresh or time.time() - _mon_names[0] > 5:
        base = {_base_monster(n) for n in _raw_monsters()}
        _mon_names[:] = [time.time(), sorted(base, key=str.lower)]
    return _mon_names[1]


def _monster_tpl(name):
    with _cache_lock:
        if name not in _mon_cache:
            img = None
            for d in monster_dirs():
                p = d / f"{name}.png"
                if p.exists():
                    img = cv2.imread(str(p))
                    break
            _mon_cache[name] = img
        return _mon_cache[name]


def identify_monster(screen, hit, cfg):
    """Which monster is this rally row against? -> (name | None if unknown | 'hidden', score)."""
    dx, dy = cfg["monster_offset"]
    cx, cy = hit[0] + dx, hit[1] + dy
    half, pad = MON_SIZE // 2, 25
    if cy - half < MON_TOP:
        return "hidden", 0.0
    x0, y0 = max(cx - half - pad, 0), max(cy - half - pad, 0)
    x1, y1 = min(cx + half + pad, screen.shape[1]), min(cy + half + pad, screen.shape[0])
    area = screen[y0:y1, x0:x1]
    best, best_name = 0.0, None
    for name in _raw_monsters():
        t = _monster_tpl(name)
        if t is None or area.shape[0] < t.shape[0] or area.shape[1] < t.shape[1]:
            continue
        score = float(cv2.minMaxLoc(cv2.matchTemplate(area, t, cv2.TM_CCOEFF_NORMED))[1])
        if score > best:
            best, best_name = score, name
    if best >= cfg["monster_threshold"]:
        return _base_monster(best_name), best
    return None, best


_unknown_seen = []      # 40-bit look-alike hashes already saved (loaded from the folder on first use)
_unknown_loaded = [False]
MONSTER_NAMES = ["Zombie", "Redcap", "Centaur", "Skeleton Dragon", "Werewolf", "Manticore", "Yasha", "Peryton", "Minotaur",
                 "Griffin", "Ifrit", "Kamaitachi", "Fafnir", "Behemoth", "Phoenix", "Jormungandr", "Typhon", "Ammit",
                 "Stymphalian Bird", "Kraken", "Azazel", "Leviathan", "Garmr", "Sphinx", "Garuda", "Warlord", "Witch",
                 "Knight Bayard", "Cerberus", "Golem", "Hydra", "Lava Turtle", "Pan", "Ymir",
                 "Pumpkin Monster", "Skullcrawler", "Thunder Scorpion", "Bird of Hurricane"]
MONSTER_CATALOG = [["Normal monsters",[["Zombie",[[1,"36.5K"]]],["Redcap",[[2,"219K"]]],["Centaur",[[3,"328.5K"]]],["Skeleton Dragon",[[4,"787.2K"]]],["Werewolf",[[5,"1.3M"]]],["Manticore",[[6,"2.2M"]]],["Yasha",[[7,"3.4M"]]],["Peryton",[[8,"6.5M"]]],["Minotaur",[[9,"9.9M"]]],["Griffin",[[10,"17.9M"]]],["Ifrit",[[11,"59.6M"]]],["Kamaitachi",[[12,"89.4M"]]],["Fafnir",[[13,"134.1M"]]],["Behemoth",[[14,"187.7M"]]],["Phoenix",[[15,"262.7M"]]],["Jormungandr",[[16,"394.1M"]]],["Typhon",[[17,"551.8M"]]],["Ammit",[[18,"800.1M"]]],["Stymphalian Bird",[[19,"1.1B"]]],["Kraken",[[20,"1.6B"]]],["Azazel",[[21,"2.6B"]]],["Leviathan",[[22,"3.3B"]]],["Garmr",[[23,"4.7B"]]]]],["Event monsters",[["Lava Turtle",[[1,"12.4M"],[2,"22.3M"],[3,"74.5M"],[4,"147.5M"],[5,"225.2M"],[6,"668.1M"],[7,"1.5B"]]],["Golem",[[1,"12.4M"],[2,"22.3M"],[3,"74.5M"],[4,"147.5M"],[5,"225.2M"],[6,"668.1M"],[7,"1.5B"]]],["Witch",[[1,"13M"],[2,"24.6M"],[3,"85.6M"],[4,"154.8M"],[5,"225.2M"],[6,"668.1M"],[7,"1.5B"]]],["Warlord",[[1,"13M"],[2,"24.6M"],[3,"85.6M"],[4,"154.8M"],[5,"225.2M"],[6,"668.1M"],[7,"1.5B"]]],["Ymir",[[1,"23.4M"],[2,"70.8M"],[3,"120.2M"],[4,"206.4M"],[5,"361.3M"],[6,"722.6M"],[7,"1.8B"]]],["Sphinx",[[1,"12.4M"],[2,"22.3M"],[3,"74.5M"],[4,"147.5M"],[5,"225.2M"],[6,"472.9M"],[7,"914.4M"],[8,"2B"]]],["Pan",[[1,"22.3M"],[2,"42.8M"],[3,"122.9M"],[4,"206.4M"],[5,"361.3M"],[6,"711.7M"],[7,"1.8B"]]],["Hydra",[[1,"84.9M"],[2,"120.6M"],[3,"198.9M"],[4,"328.4M"],[5,"678.8M"],[6,"1.5B"]]],["Knight Bayard",[[1,"65.5M"],[2,"147.5M"],[3,"225.2M"],[4,"668.1M"],[5,"1.5B"]]],["Cerberus",[[1,"68.2M"],[2,"153.4M"],[3,"234.2M"],[4,"694.9M"],[5,"1.5B"]]],["Aglaope",[[1,"120.2M"]]]]],["Limited-time event",[["Pumpkin Monster",[[1,"708.5K"],[2,"1.9M"],[3,"5.9M"],[4,"16.1M"],[5,"53.6M"],[6,"80.4M"],[7,"134.1M"],[8,"268.1M"]]],["Nian",[[1,"59.6M"],[2,"71.5M"],[3,"89.4M"],[4,"134.1M"],[5,"236.5M"],[6,"472.9M"]]],["Cranky Snowman",[[4,"1.6M"],[5,"2.7M"],[6,"3.5M"],[7,"11M"]]],["Bird of Hurricane",[[1,""]]]]]]   # [category, [[name, [[level, power], ...]], ...]] from evonyguidewiki.com
_SUMMONED = [["Fenrir",[[1,"27.4K"],[2,"186.1K"],[3,"492.7K"]]],["Garuda",[[1,"1.3M"],[2,"1.7M"],[3,"125M"]]],["Achelois",[[1,"17.9M"],[2,"89.4M"],[3,"187.7M"]]],["Basilisk",[[1,"130M"]]],["Draugr",[[1,"130M"]]],["Taotie",[[1,"124.6M"],[2,"398.1M"],[3,"805.1M"]]]]
MONSTER_CATALOG.append(["Summoned", _SUMMONED + [[_n, [[1, ""]]] for _n in (
    "Gugler Knight", "Outlander Elites")] + [[_n, [[1, ""], [2, ""]]] for _n in (
    "Savage Tiger Gladiator", "Arachne", "Carcinus", "Harpy", "Serpopard", "Dugger", "Nasu",
    "Barbary Pirate", "Temple Guard")]])     # summoned / auto-join monsters; no level data yet for the plain ones
VIKING_EASY_MEASURED = {1, 10, 20, 30, 40, 50}      # the other Easy levels are estimated between these, so they only count when the card's level is known
VIKING_SETS = {"Viking (Easy)": [[1, "1.1M"], [2, "1.2M"], [3, "1.2M"], [4, "1.3M"], [5, "1.3M"], [6, "1.4M"], [7, "1.5M"], [8, "1.5M"], [9, "1.6M"], [10, "1.7M"], [11, "1.8M"], [12, "1.9M"], [13, "2M"], [14, "2.1M"], [15, "2.2M"], [16, "2.3M"], [17, "2.5M"], [18, "2.6M"], [19, "2.7M"], [20, "2.9M"], [21, "3.1M"], [22, "3.2M"], [23, "3.4M"], [24, "3.6M"], [25, "3.8M"], [26, "4M"], [27, "4.2M"], [28, "4.4M"], [29, "4.6M"], [30, "4.9M"], [31, "5.2M"], [32, "5.4M"], [33, "5.7M"], [34, "6M"], [35, "6.4M"], [36, "6.7M"], [37, "7.1M"], [38, "7.5M"], [39, "7.9M"], [40, "8.3M"], [41, "8.8M"], [42, "9.2M"], [43, "9.7M"], [44, "10.3M"], [45, "10.8M"], [46, "11.4M"], [47, "12M"], [48, "12.7M"], [49, "13.4M"], [50, "14.1M"]], "Viking (Normal)": [[1, "3.2M"], [2, "3.4M"], [3, "3.7M"], [4, "3.9M"], [5, "4.2M"], [6, "4.5M"], [7, "4.8M"], [8, "5.2M"], [9, "5.6M"], [10, "5.9M"], [11, "6.4M"], [12, "6.8M"], [13, "7.3M"], [14, "7.8M"], [15, "8.4M"], [16, "9M"], [17, "9.6M"], [18, "10.3M"], [19, "11M"], [20, "11.8M"], [21, "12.6M"], [22, "13.5M"], [23, "14.5M"], [24, "15.5M"], [25, "16.6M"], [26, "17.8M"], [27, "19M"], [28, "20.4M"], [29, "21.8M"], [30, "23.4M"], [31, "25M"], [32, "26.8M"], [33, "28.7M"], [34, "30.7M"], [35, "32.9M"], [36, "35.2M"], [37, "37.7M"], [38, "40.4M"], [39, "43.2M"], [40, "46.3M"], [41, "49.6M"], [42, "53.1M"], [43, "56.8M"], [44, "60.8M"], [45, "65.1M"], [46, "69.8M"], [47, "74.7M"], [48, "80M"], [49, "85.6M"], [50, "91.7M"]], "Viking (Hard)": [[1, "10.6M"], [2, "11.3M"], [3, "12M"], [4, "12.7M"], [5, "13.5M"], [6, "14.3M"], [7, "15.2M"], [8, "16.1M"], [9, "17.1M"], [10, "18.1M"], [11, "19.3M"], [12, "20.4M"], [13, "21.7M"], [14, "23M"], [15, "24.4M"], [16, "26M"], [17, "27.5M"], [18, "29.2M"], [19, "31M"], [20, "32.9M"], [21, "35M"], [22, "37.1M"], [23, "39.4M"], [24, "41.8M"], [25, "44.4M"], [26, "47.1M"], [27, "50M"], [28, "53.1M"], [29, "56.3M"], [30, "59.8M"], [31, "63.4M"], [32, "67.3M"], [33, "71.5M"], [34, "75.9M"], [35, "80.5M"], [36, "85.5M"], [37, "90.7M"], [38, "96.3M"], [39, "102.2M"], [40, "108.5M"], [41, "115.1M"], [42, "122.2M"], [43, "129.7M"], [44, "137.7M"], [45, "146.1M"], [46, "155.1M"], [47, "164.6M"], [48, "174.8M"], [49, "185.5M"], [50, "196.9M"]], "Viking (Hell)": [[1, "17.9M"], [2, "19.5M"], [3, "21.3M"], [4, "23.3M"], [5, "25.4M"], [6, "27.8M"], [7, "30.3M"], [8, "33.1M"], [9, "36.1M"], [10, "39.5M"], [11, "43.1M"], [12, "47.1M"], [13, "51.4M"], [14, "56.1M"], [15, "61.3M"], [16, "66.9M"], [17, "73.1M"], [18, "79.8M"], [19, "87.1M"], [20, "95.1M"], [21, "103.9M"], [22, "113.5M"], [23, "123.9M"], [24, "135.3M"], [25, "147.7M"], [26, "161.3M"], [27, "176.2M"], [28, "192.4M"], [29, "210.1M"], [30, "229.4M"], [31, "250.5M"], [32, "273.5M"], [33, "298.7M"], [34, "326.2M"], [35, "356.2M"], [36, "388.9M"], [37, "424.7M"], [38, "463.8M"], [39, "506.4M"], [40, "551.8M"], [41, "569.7M"], [42, "588.3M"], [43, "607.4M"], [44, "627.2M"], [45, "647.6M"], [46, "668.7M"], [47, "690.4M"], [48, "712.9M"], [49, "736.1M"], [50, "760.1M"]]}
MONSTER_CATALOG.append(["Vikings", [[_n, _l] for _n, _l in VIKING_SETS.items()] + [["Viking Icebreaker",[[1,"89.4M"]]],["Viking Berserker",[[2,"134.1M"]]],["Viking Chief",[[3,"262.7M"]]],["King of the Vikings",[[4,"1B"]]]]])        # Easy / Normal / Hard / Hell, level + power
MONSTER_NAMES += [_n for _c, _ms in MONSTER_CATALOG for _n, _l in _ms if _n not in MONSTER_NAMES]
MONSTER_NAMES.append("Viking") if "Viking" not in MONSTER_NAMES else None      # the card only says "Lv.N Viking"; the set comes from the power


def read_auto_join_time(screen):
    """'Remaining Auto-Join time: HH:MM:SS' on the game's Auto-Join screen -> seconds, or None."""
    crop = screen[120:215, 40:500]
    for bright in (False, True):
        try:
            lines = ocr_lines(crop, scale=3.0, bright=bright)
        except Exception:
            lines = []
        for text, _ in lines:
            m = re.search(r"(\d{1,2})\s*[:.]\s*(\d{2})\s*[:.]\s*(\d{2})", text)
            if m and "monthly" not in text.lower():
                h, mi, se = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if mi < 60 and se < 60 and h <= 24:
                    return h * 3600 + mi * 60 + se
    return None


def fmt_hms(sec):
    return "%d:%02d:%02d" % (sec // 3600, sec % 3600 // 60, sec % 60)


AJ_LEFT = {}                                  # serial -> (seconds left on the game's Auto-Join, time it was read)
AJ_ASK = {}                                   # serial -> True: check Auto-Join at the next look at the rally list (Black Market left the shop for it)


def auto_join_renew(dev, cfg, log, stop, act):
    """On the Alliance War list: open the game's Auto-Join, read the time left and press Start when it is under the limit
    (or already stopped). Start works while it is running and resets it to 8 hours. -> seconds until the next check."""
    every = max(1, int(cfg.get("auto_join_check_min", 10))) * 60
    limit = max(1, int(cfg.get("auto_join_renew_min", 120))) * 60
    act("step", text="Checking the game's Auto-Join")
    dev.tap(*cfg["auto_join_tap"])
    time.sleep(2.0)
    left = None
    for _ in range(3):
        if stop.is_set():
            return 30
        left = read_auto_join_time(dev.screenshot())
        if left is not None:
            break
        time.sleep(1.0)
    if left is None:
        log("[!] Auto-Join: could not read the time left - closing it and trying again in 2 minutes")
        dev.back()
        return 120
    AJ_LEFT[dev.serial] = (left, time.time())
    log(f"[*] Auto-Join: {fmt_hms(left)} left (renew under {limit // 60} min)")
    if left <= limit:
        act("step", text="Renewing the game's Auto-Join")
        dev.tap(*cfg["auto_join_start_tap"])
        time.sleep(2.0)
        now_left = read_auto_join_time(dev.screenshot())
        if now_left is not None and now_left > left + 600:
            AJ_LEFT[dev.serial] = (now_left, time.time())
            log(f"[+] Auto-Join renewed - now {fmt_hms(now_left)}")
            act("renew", left=fmt_hms(now_left))
        else:
            log("[!] Auto-Join: pressed Start but the timer did not reset - will look again soon")
            dev.back()
            return 120
    dev.back()
    time.sleep(1.0)
    return every


_MONSTER_ALIASES = {"Bayar Knight": "Knight Bayard", "Bayard Knight": "Knight Bayard", "Bayar": "Knight Bayard", "Bayard": "Knight Bayard",
                    **{f"{g} {m}": m for g in ("Junior", "Medium", "Senior") for m in ("Hydra", "Cerberus", "Nian")},
                    "Junior Knight Bayard": "Knight Bayard", "Senior Bayar Knight": "Knight Bayard", "Senior Knight Bayard": "Knight Bayard"}


def read_monster_label(strip):
    """The boss name on the strip under a rally portrait, matched to the known monster names when it is close."""
    import difflib
    best_raw, best = "", ("", 0.0)
    for bright in (False, True):
        try:
            lines = ocr_lines(strip, scale=3.0, bright=bright)
        except Exception:
            lines = []
        for text, _ in lines:
            t = re.sub(r"[^A-Za-z ]", " ", text)
            t = re.sub(r"\b(Legendary|Epic|Rare|Mythical|Lv|Level)\b", " ", t, flags=re.I)
            t = " ".join(t.split())
            if len(t) > len(best_raw):
                best_raw = t
            for nm in MONSTER_NAMES + list(_MONSTER_ALIASES) + WORLD_BOSS_NAMES:   # world bosses too: "Behemoth King" must not read as "Behemoth"
                nn = re.sub(r"[^a-z ]", " ", nm.lower())
                nn = " ".join(nn.split())
                r = difflib.SequenceMatcher(None, t.lower(), nn).ratio()
                if r > best[1]:
                    best = (_MONSTER_ALIASES.get(nm, nm), r)
    return best[0] if best[1] >= 0.75 else best_raw


def learn_monster(label, port, cfg, log=None):
    """The name under the portrait was read with confidence: keep the portrait as a monster picture of that name
    (another look of an already known monster becomes 'Name #2', 'Name #3', ...), so the bot knows it from now on."""
    label = label.replace("_", " ").strip()
    if label not in MONSTER_NAMES:
        return False
    d = USER / "monsters"
    d.mkdir(exist_ok=True)
    mine = [p for p in d.glob("*.png") if _base_monster(p.stem) == label]
    if len(mine) >= 6:
        return False
    for p in mine:
        t = cv2.imread(str(p))
        if t is not None and t.shape == port.shape and float(cv2.matchTemplate(port, t, cv2.TM_CCOEFF_NORMED)[0][0]) >= cfg.get("monster_threshold", 0.8):
            return False
    stem = label if not (d / f"{label}.png").exists() else next(f"{label} #{i}" for i in range(2, 9) if not (d / f"{label} #{i}.png").exists())
    cv2.imwrite(str(d / f"{stem}.png"), port)
    with _cache_lock:
        _mon_cache.clear()
    list_monsters(refresh=True)
    if log:
        log(f"[+] Learned a new picture for {label} ({stem})")
    return True


def collect_unknown_monster(screen, hit, cfg, log=None):
    """A rally against a monster the bot has no picture of: keep its portrait (ready to use as a monster picture)
    and the name strip under it, once per look-alike, in the 'unknown_monsters' folder."""
    try:
        dx, dy = cfg["monster_offset"]
        cx, cy = int(hit[0] + dx), int(hit[1] + dy)
        half = MON_SIZE // 2
        if cy - half < MON_TOP or cx - half < 0 or cx + half > screen.shape[1] or cy + half > screen.shape[0]:
            return
        port = screen[cy - half:cy + half, cx - half:cx + half]
        g = cv2.resize(cv2.cvtColor(port, cv2.COLOR_BGR2GRAY), (8, 8), interpolation=cv2.INTER_AREA)
        bits = "".join("1" if v > g.mean() else "0" for v in g.flatten())
        h40 = int(bits, 2) >> 24
        key = f"{h40:010x}"
        d = USER / "unknown_monsters"
        d.mkdir(exist_ok=True)
        if not _unknown_loaded[0]:
            _unknown_loaded[0] = True
            for f in sorted(d.glob("*__*.png"), key=lambda p: p.stat().st_mtime):
                k = f.stem.rsplit("__", 1)[-1]
                if re.fullmatch(r"[0-9a-f]{10}", k):
                    v = int(k, 16)
                    if any(bin(v ^ o).count("1") <= 5 for o in _unknown_seen):      # look-alike of an older picture: remove it
                        for g in (f, f.with_name(f.stem + "_name.png")):
                            try:
                                g.unlink()
                            except OSError as _e:
                                quiet_err("collect_unknown_monster", _e)
                    else:
                        _unknown_seen.append(v)
                        img0 = cv2.imread(str(f))
                        if img0 is not None and img0.shape[:2] == (MON_SIZE, MON_SIZE):
                            learn_monster(f.stem.rsplit("__", 1)[0], img0, cfg, log)
        if any(bin(h40 ^ k).count("1") <= 5 for k in _unknown_seen):
            return
        if len(list(d.glob("*.png"))) >= 300:
            return
        nx, ny = int(hit[0] - 21), int(hit[1] - 113)
        strip = screen[max(ny - 18, 0):ny + 18, max(nx - 150, 0):nx + 150]
        label = ""
        if strip.size:
            label = read_monster_label(strip)
        label = re.sub(r"[^A-Za-z0-9 ()]", "", label).strip()[:30].replace(" ", "_") or "monster"
        _unknown_seen.append(h40)
        cv2.imwrite(str(d / f"{label}__{key}.png"), port)
        learn_monster(label, port, cfg, log)
        if strip.size:
            cv2.imwrite(str(d / f"{label}__{key}_name.png"), strip)
        if log:
            log(f"[+] New monster saved to the unknown_monsters folder: {label.replace('_', ' ')}")
    except Exception as _e:
        quiet_err("collect_unknown_monster", _e)


def monster_levels(name):
    """All catalogue levels of a monster (empty when it is not in the catalogue)."""
    for _c, ms in MONSTER_CATALOG:
        for n, lv in ms:
            if n.lower() == (name or "").lower():
                return [x[0] for x in lv]
    return []


def monster_rule_skip(name, cfg, level=None):
    """The per-monster / per-level skip list (cfg['monster_skip']). -> reason string when it must be skipped, else ''."""
    rules = cfg.get("monster_skip") or {}
    if not name or name == "hidden":
        return ""
    lv = None
    for k, v in rules.items():
        if k.lower() == name.lower():
            lv = {int(x) for x in v}
    if not lv:
        return ""
    allv = set(monster_levels(name)) or lv
    if allv <= lv:
        return f"{name} is switched off in the monster list"
    if level is not None and int(level) in lv:
        return f"{name} Lv{level} is switched off in the monster list"
    return ""


def monster_needs_level(name, cfg):
    """True when only some levels of this monster are switched off, so the level has to be read."""
    rules = cfg.get("monster_skip") or {}
    for k, v in rules.items():
        if k.lower() == (name or "").lower() and v:
            allv = set(monster_levels(name)) or {int(x) for x in v}
            return not allv <= {int(x) for x in v}
    return False


def read_monster_level(strip):
    """'Lv7' (or 'Level 7') in the text above the Join button -> int, else None."""
    for bright in (False, True):
        try:
            lines = ocr_lines(strip, scale=3.0, bright=bright)
        except Exception:
            lines = []
        for text, _ in lines:
            m = re.search(r"(?:Lv|Level|LV|Lu|Ly)\.?\s*(\d{1,2})", text)
            if m:
                return int(m.group(1))
    return None


def _power_num(txt):
    """'668.1M' -> 668100000.0 (None when it is not a power value)."""
    m = re.search(r"(\d{1,4}(?:[.,]\d{1,2})?)\s*([KMBkmb8])\b", (txt or "").replace(" ", ""))
    if not m:
        return None
    mult = {"K": 1e3, "M": 1e6, "B": 1e9, "8": 1e9}[m.group(2).upper()]
    return float(m.group(1).replace(",", ".")) * mult


def joined_button(screen, hit):
    """True when this rally's button is the grey "Joined" (this account already joined) instead of the green "Join"."""
    try:
        x, y = int(hit[0]), int(hit[1])
        w, h = (hit[3] if len(hit) > 3 and hit[3] else (119, 43))
        box = screen[max(y - h // 2, 0):y + h // 2, max(x - w // 2, 0):x + w // 2]
        if box.size == 0:
            return False
        hsv = cv2.cvtColor(box, cv2.COLOR_BGR2HSV)
        green = cv2.inRange(hsv, (35, 90, 60), (90, 255, 255))
        return cv2.countNonZero(green) < 0.15 * box.shape[0] * box.shape[1]
    except Exception:
        return False


def read_monster_power(screen, hit, hard=False):
    """The monster's power (blue bar at the top right of the rally card) -> (number | None, raw text)."""
    x0, y0 = max(int(hit[0]) - 110, 0), max(int(hit[1]) - 258, 0)
    crop = screen[y0:max(int(hit[1]) - 205, y0 + 1), x0:int(hit[0]) + 50]
    if crop.size == 0:
        return None, ""
    raw = ""
    # 1) just the number: the text line only (no dark band under it), right of the crossed-swords icon, digits + K/M/B
    band = crop[0:28, 55:160] if crop.shape[0] >= 20 and crop.shape[1] >= 120 else None
    if band is not None and band.size:
        g = cv2.resize(cv2.cvtColor(band, cv2.COLOR_BGR2GRAY), None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        for thr in ((150, "otsu", 175) if hard else (150,)):
            if thr == "otsu":
                _, b = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
            else:
                _, b = cv2.threshold(g, thr, 255, cv2.THRESH_BINARY_INV)
            b = cv2.copyMakeBorder(b, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
            try:
                with _ocr_gate:
                    txt = pytesseract.image_to_string(b, config="--psm 7 -c tessedit_char_whitelist=0123456789.KMB").strip()
            except Exception:
                txt = ""
            v = _power_num(txt)
            if v:
                return v, txt
            raw = raw or txt
    for scale in ((3.0, 2.0, 4.0) if hard else (3.0,)):          # older way: whole box, all characters
        for bright in (True, False):
            try:
                lines = ocr_lines(crop, scale=scale, bright=bright)
            except Exception:
                lines = []
            for text, _ in lines:
                raw = raw or text
                v = _power_num(text)
                if v:
                    return v, text
    if not hard:
        return None, raw
    try:                                                # keep the picture of a failed read so it can be studied
        d = USER / "debug"
        d.mkdir(exist_ok=True)
        old = sorted(d.glob("power_fail_*.png"), key=lambda f: f.stat().st_mtime)
        for f in old[:max(0, len(old) - 19)]:
            f.unlink()
        cv2.imwrite(str(d / f"power_fail_{int(time.time())}.png"), crop)
    except Exception as _e:
        quiet_err("read_monster_power", _e)
    return None, raw


def level_from_power(name, power):
    """Which catalogue level of this monster has this power (within 2%)? -> level or None."""
    best = None
    for _c, ms in MONSTER_CATALOG:
        for n, lv in ms:
            if n.lower() != (name or "").lower():
                continue
            for l, p in lv:
                v = _power_num(p)
                if v and abs(v - power) / v <= 0.02 and (best is None or abs(v - power) < best[0]):
                    best = (abs(v - power), l)
    return best[1] if best else None


def viking_resolve(power, level=None):
    """A rally card says 'Lv.N Viking' and shows a power. The same level has a different power in each set (Easy / Normal /
    Hard / Hell), so (level, power) names the set. -> ('Viking (Hell)', 3) or (None, level)."""
    import math
    if level is not None and power:                       # the card's own level is right: the power only has to tell the SET
        near = None                                      # (Easy / Normal / Hard / Hell differ 2-3x), so a rough match is enough
        for name, lv in VIKING_SETS.items():
            v = next((_power_num(p) for l, p in lv if l == level), None)
            if not v:
                continue
            d = abs(math.log(power / v))
            if near is None or d < near[0]:
                near = (d, name)
        if near and near[0] < math.log(1.5):
            return near[1], level
    best = None
    for name, lv in VIKING_SETS.items():
        for l, p in lv:
            v = _power_num(p)
            if not v or power is None or (level is not None and l != level):
                continue
            if name == "Viking (Easy)" and level is None and l not in VIKING_EASY_MEASURED:
                continue
            err = abs(v - power) / v
            if err <= 0.03 and (best is None or err < best[0]):
                best = (err, name, l)
    return (best[1], best[2]) if best else (None, level)


# Point bosses: hit once for alliance points, nobody needs to join them. World bosses (owner 23 Oct): they show on the
# rally list but cannot be joined as alliance rallies. Never joined, whatever the monster list says.
NEVER_JOIN = {"silver lionheart knight", "thunder scorpion", "bird of hurricane", "behemoth king", "lord of lava"}
WORLD_BOSS_NAMES = [" ".join(w.capitalize() for w in n.split()) for n in sorted(NEVER_JOIN)]
# A world boss whose picture is close to a normal monster (owner 23 Oct: Behemoth King was joined as "Behemoth"):
# when the picture says one of these, the name under the portrait is read as well.
WORLD_BOSS_LOOKALIKE = {"Behemoth"}


def never_join_present():
    """True when a picture of a never-join boss is installed (then every rally row is checked against it)."""
    return any(n.lower() in NEVER_JOIN for n in list_monsters())


def monster_allowed(name, cfg, level=None):
    """Apply the skip / only rule. -> (allowed, reason)."""
    if name and str(name).lower() in NEVER_JOIN:
        return False, f"{name} is a points boss - never joined"
    why = monster_rule_skip(name, cfg, level)
    if why:
        return False, why
    mode = cfg["monster_mode"]
    if name == "hidden" and cfg.get("monster_skip"):          # name not readable yet: never join blind when some monsters/levels are switched off
        return False, "monster picture not fully on screen"
    if mode == "off":
        return True, ""
    if name == "hidden":
        return False, "monster picture not fully on screen"
    if name is None:
        ok = cfg["monster_unknown"] == "join"
        return ok, f"monster with no picture -> {'join' if ok else 'skip'}"
    listed = name.lower() in {n.lower() for n in cfg["monster_list"]}
    if mode == "skip":
        return (not listed), (f"{name} is on the skip list" if listed else f"{name} is allowed")
    return listed, (f"{name} is on the join list" if listed else f"{name} is not on the join list")


def cerberus_allowed(screen, hit, cfg):
    """Cerberus rallies are skipped unless cfg['cerberus'] == 'join'. Reads the boss name above the Join button."""
    return True, ""                         # 2.4.74: the monster list decides (Cerberus is a normal monster there); the old hidden "Cerberus rallies" switch is retired
    cx, cy = int(hit[0] - 21), int(hit[1] - 113)
    x0, y0 = max(cx - 140, 0), max(cy - 24, 0)
    if cy - 24 < 0:
        return True, ""
    reg = (x0, y0, min(280, screen.shape[1] - x0), min(48, screen.shape[0] - y0))
    if find_text(screen, ["Cerberus"], reg, cutoff=0.7):
        return False, "Cerberus rally (skipped for this instance)"
    return True, ""


def war_tab_is_event(screen):
    """On the Alliance War screen the selected tab is lit (gold). True when 'Event War' (right) is the lit one."""
    hsv = cv2.cvtColor(screen[92:122], cv2.COLOR_BGR2HSV)[..., 2]
    left, right = float(hsv[:, 50:230].mean()), float(hsv[:, 310:490].mean())
    war_tab_is_event.last = (left, right)
    return right > left + 25


def read_screenshot(path):
    """Load a screenshot and bring it to 540x960."""
    img = cv2.imread(str(path))
    if img is None:
        raise ValueError("Could not read that image.")
    if img.shape[1::-1] != EXPECTED_SIZE:
        img = cv2.resize(img, EXPECTED_SIZE, interpolation=cv2.INTER_AREA)
    return img


def save_monster(name, img, cx, cy):
    """Save the round portrait centred on (cx, cy) of a 540x960 screenshot as a monster picture."""
    name = re.sub(r'[\\/:*?"<>|]', "", str(name)).strip()
    if not name:
        raise ValueError("Give the monster a name.")
    half = MON_SIZE // 2
    cx = min(max(int(cx), half), img.shape[1] - half)
    cy = min(max(int(cy), half), img.shape[0] - half)
    (USER / "monsters").mkdir(exist_ok=True)
    path = USER / "monsters" / f"{name}.png"
    if not cv2.imwrite(str(path), img[cy - half:cy + half, cx - half:cx + half]):
        raise ValueError(f"Could not write {path}")
    with _cache_lock:
        _mon_cache.clear()
    list_monsters(refresh=True)
    return path


def add_monster(name, src, row, cfg=None):
    """CLI helper: save a monster picture by row number (1 = top-most Join button in the screenshot)."""
    cfg = cfg or load_config()
    img = read_screenshot(src)
    if not has_template("join_button"):
        raise ValueError("templates/join_button.png is missing.")
    hits = find_all(img, "join_button", cfg["threshold"], cfg["search_region"]) or \
        find_all(img, "join_button", 0.7, cfg["search_region"])
    if not hits:
        raise ValueError("No Join button recognised in that screenshot. It must show the rally list with at "
                         "least one Join button, and templates/join_button.png must match this emulator.")
    if not 1 <= row <= len(hits):
        raise ValueError(f"Found {len(hits)} Join button(s) in that screenshot; choose a row from 1 to {len(hits)}.")
    cx, cy = hits[row - 1][0] + cfg["monster_offset"][0], hits[row - 1][1] + cfg["monster_offset"][1]
    if cy - MON_SIZE // 2 < MON_TOP:
        raise ValueError("That row's monster picture is cut off at the top. Scroll so the whole row is visible.")
    return save_monster(name, img, cx, cy)


def timer_crop(screen, hit, source):
    x1, y1, x2, y2 = TIMER_BOXES[source]
    return screen[max(hit[1] + y1, 0):hit[1] + y2, max(hit[0] + x1, 0):hit[0] + x2]


def judge(screen, hit, cfg, extra=0.0):
    """Read the rally countdown and the march time. -> (rally_left, march_time, would_join, reason).
    'extra' = seconds the bot itself needs between reading the screen and pressing March."""
    rally = read_seconds(screen, hit, cfg["rally_timer_source"])
    march = read_seconds(screen, hit, cfg["march_timer_source"]) if cfg["march_timer_source"] else None
    if march is not None and march >= 300 and cfg["march_timer_source"]:
        # a long march time is often a misread digit: read again two other ways and take the majority
        votes = [march] + [v for v in (read_seconds(screen, hit, cfg["march_timer_source"], 1),
                                       read_seconds(screen, hit, cfg["march_timer_source"], 2)) if v is not None]
        best = max(set(votes), key=lambda v: (votes.count(v), -v))
        march = best
    if rally is None:
        return rally, march, False, "rally timer unreadable"
    if cfg["max_rally_seconds"] and rally > cfg["max_rally_seconds"]:
        return rally, march, False, "more time left than max_rally_seconds"
    guess = int(cfg.get("march_guess", 10) or 0)
    walk = march if march is not None else guess          # walk time not read: count the usual walk so stamina is not wasted
    spare = rally - walk - int(extra)
    note = "" if march is not None else f", march counted as {guess}s"
    if spare < cfg["safety_margin"]:
        return rally, march, False, f"not enough time: {spare}s spare{note}"
    return rally, march, True, f"{spare}s spare{note}"


def analyze(screen, cfg):
    """What would the bot do? -> list of (x, y, score, rally_left, march_time, would_join, reason, monster)."""
    out = []
    for hit in find_all(screen, "join_button", cfg["threshold"], cfg["search_region"]):
        rally, march, ok, why = judge(screen, hit, cfg)
        mon, mscore = identify_monster(screen, hit, cfg)
        m_ok, m_why = monster_allowed(mon, cfg)
        if not m_ok:
            ok, why = False, m_why
        elif ok:
            c_ok, c_why = cerberus_allowed(screen, hit, cfg)
            if not c_ok:
                ok, why = False, c_why
        label = "hidden" if mon == "hidden" else (f"{mon} {mscore:.2f}" if mon else f"unknown (best {mscore:.2f})")
        out.append((hit[0], hit[1], hit[2], rally, march, ok, why, label))
    return out


def tap_hit(dev, hit, spread=0.25):
    cx, cy, _, (w, h) = hit
    dev.tap(cx + random.uniform(-w * spread, w * spread), cy + random.uniform(-h * spread, h * spread))


# ------------------------------------------------------------------ presets
class PresetRotator:
    """Hands out presets in rotate / random / fixed order and benches ones that fail to march."""

    def __init__(self, presets, mode="rotate"):
        self.presets = [int(p) for p in presets if 1 <= int(p) <= 8] or [1]
        self.mode, self.i, self.bad = mode, 0, {}

    def next(self):
        now = time.time()
        ok = [p for p in self.presets if self.bad.get(p, 0) <= now]
        if not ok:
            return None
        if self.mode == "random":
            return random.choice(ok)
        if self.mode == "fixed":
            return ok[0]
        for _ in range(len(self.presets)):
            p = self.presets[self.i % len(self.presets)]
            self.i += 1
            if p in ok:
                return p
        return ok[0]

    def available(self):
        now = time.time()
        return any(self.bad.get(p, 0) <= now for p in self.presets)

    def mark_bad(self, preset, seconds):
        self.bad[preset] = time.time() + seconds

    def seconds_until_free(self):
        return max(0.0, min(self.bad.values(), default=0) - time.time())


def default_bot_presets():
    """The bot's own presets I-VI. Each matches the game preset with the same number."""
    return [{"n": i, "enabled": i == 1, "skip_if_no_general": True, "pick_generals": False,
             "assistant_optional": False, "max_troops": "", "main": [], "assistant": []} for i in range(1, 9)]


def bot_preset_table(cfg):
    """{number: settings} of the bot presets, or {} when the old plain 'presets' list is in use."""
    out = {}
    for i, bp in enumerate(cfg.get("bot_presets") or []):
        if not isinstance(bp, dict):
            continue
        n = int(bp.get("n", i + 1))
        if 1 <= n <= 8:
            out[n] = {"enabled": bool(bp.get("enabled", True)),
                      "skip_if_no_general": bool(bp.get("skip_if_no_general", True)),
                      "pick": bool(bp.get("pick_generals", False)),
                      "asst_opt": bool(bp.get("assistant_optional", False)),
                      "max_troops": (int(bp["max_troops"]) if str(bp.get("max_troops", "")).strip().lstrip("-").isdigit() else None),
                      "main": general_list(bp.get("main")), "assistant": general_list(bp.get("assistant"))}
    return out


def preset_picks(cfg, n):
    """True when the bot has to search for generals on this preset (slow, ~30-40 s); False for a plain game preset."""
    pcfg, _ = preset_cfg(cfg, n)
    if pcfg.get("use_preset_generals"):
        return False
    return bool(pcfg["general_main"] or pcfg["general_assistant"])


def general_list(v):
    if isinstance(v, str):
        v = re.split(r"[,;\n]", v)
    return [str(x).strip() for x in (v or []) if str(x).strip()]


def active_presets(cfg):
    """Preset numbers the bot rotates through: the enabled bot presets, else the plain list."""
    t = bot_preset_table(cfg)
    if t:
        on = [n for n in sorted(t) if t[n]["enabled"]]
        return on or [1]
    return cfg["presets"]


def preset_cfg(cfg, n):
    """cfg with this preset's own generals (an empty line falls back to the general lists in the settings)."""
    bp = bot_preset_table(cfg).get(n)
    if not bp:
        return cfg, True
    c = dict(cfg)
    if not bp["pick"]:                     # generals are only chosen by the bot when the preset asks for it
        c["use_preset_generals"] = True
    if bp["max_troops"] is not None:
        c["max_troops"] = bp["max_troops"]
    if bp["asst_opt"]:                     # this preset may join with the main general only
        c["general_assistant_optional"] = True
    if bp["main"]:
        c["general_main"] = bp["main"]
    if bp["assistant"]:
        c["general_assistant"] = bp["assistant"]
    return c, bp["skip_if_no_general"]


def _ocr_band(img, fx=3.0, block=31, off=12):
    g = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), None, fx=fx, fy=fx, interpolation=cv2.INTER_CUBIC)
    b = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, off)
    if b.mean() < 127:
        b = 255 - b
    b = cv2.copyMakeBorder(b, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)
    return pytesseract.image_to_string(b, config="--psm 6")


def slot_has_general(shot, role, cfg, y=None):
    """False when the Main/Assistant slot on the march screen is empty. The game then shows a '+' and
    'Select a Main General.' (or a blank name line). If the OCR cannot run we assume there is a general."""
    if has_template("no_general") and role == "main" and find(shot, "no_general", 0.85, (20, 440, 500, 330)):
        return False
    if y is None:
        pos = cfg["general_main_tap"] if role == "main" else cfg["general_assistant_tap"]
        y = pos[1] if pos else (532 if role == "main" else 688)
    y = int(y)
    band = shot[max(0, y - 90):y + 100, 150:450]
    if band.size == 0:
        return True
    try:
        if "select" in _ocr_band(band).lower():
            return False
        c = shot[max(0, y - 30):y + 26, 150:430]
        letters = re.sub(r"[^A-Za-z]", "", _ocr_band(c, 2.5))
    except Exception:
        return True
    return len(letters) >= 4


def slot_matches(shot, role, names, cfg):
    """Does the Main/Assistant slot on the march screen already hold one of the listed generals? -> its name or None.
    Reads the name text in the slot, so a preset that already has the right general needs no searching."""
    pos = cfg["general_main_tap"] if role == "main" else cfg["general_assistant_tap"]
    y = int((pos[1] if pos else (532 if role == "main" else 688)))
    band = shot[max(0, y - 50):y + 40, 150:450]
    if band.size == 0:
        return None
    try:
        text = re.sub(r"[^a-z ]", " ", _ocr_band(band, 2.5).lower())
    except Exception:
        return None
    for n in names:
        for tok in re.findall(r"[a-z]{4,}", n.lower()):
            if tok in text.split() or tok in text:
                return n
    return None


def troops_in_march(shot):
    """The troop count on the march screen ('1 / 2,050,200' -> 1), or None if it cannot be read."""
    c = shot[780:828, 80:260]
    if c.size == 0:
        return None
    g = cv2.resize(cv2.cvtColor(c, cv2.COLOR_BGR2GRAY), None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    for t in (120, 105, 135):                    # bright text on a dark bar; the slash needs a gentle threshold
        b = 255 - cv2.threshold(g, t, 255, cv2.THRESH_BINARY)[1]
        b = cv2.copyMakeBorder(b, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)
        try:
            txt = pytesseract.image_to_string(b, config="--psm 7")
        except Exception:
            return None
        m = re.search(r"([\d.,]+)\s*/", txt)
        digits = re.sub(r"\D", "", m.group(1)) if m else ""
        if digits:
            return int(digits)
    return None


def read_stamina(shot):
    """The stamina on the march screen ('5,333 / 33' -> (5333, 33) = (owned, needed)), or None if it cannot be read."""
    c = shot[832:880, 200:480]                 # wide enough for big numbers such as '123,456 / 28' (digits were cut off before)
    if c.size == 0:
        return None
    g = cv2.resize(cv2.cvtColor(c, cv2.COLOR_BGR2GRAY), None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    for t in (120, 105, 135):
        b = 255 - cv2.threshold(g, t, 255, cv2.THRESH_BINARY)[1]
        b = cv2.copyMakeBorder(b, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)
        try:
            txt = pytesseract.image_to_string(b, config="--psm 7")
        except Exception:
            return None
        m = re.search(r"([\d.,]+)\s*/\s*([\d.,]+)", txt)
        if m:
            a, n = re.sub(r"\D", "", m.group(1)), re.sub(r"\D", "", m.group(2))
            if a and n:
                return int(a), int(n)
    return None


STAMINA_CAP = 9999                                       # the game's limit: stamina items over it are lost (Use Item screen text)


def _stamina_use_button(shot, y0, y1):
    """The green 'Use (n)' button of a stamina item row between y0 and y1 -> (x, y) centre, or None. OCR of the whole
    screen cannot read white text on green (24 Oct: 'Use (1,220)' never found, every refill skipped), so: find the green
    button, then read its white label on its own. A gem-price button never says 'Use', so it is never returned."""
    try:
        y0, y1 = max(0, int(y0)), min(shot.shape[0], int(y1))
        band = shot[y0:y1, 340:520]
        if band.size == 0:
            return None
        m = cv2.inRange(cv2.cvtColor(band, cv2.COLOR_BGR2HSV), (45, 120, 60), (85, 255, 255))
        n, lab, st, cen = cv2.connectedComponentsWithStats(m)
        for i in sorted(range(1, n), key=lambda i: -st[i, cv2.CC_STAT_AREA]):
            x, y, w, h, a = st[i]
            if w < 70 or h < 25 or a < 1200:
                continue
            c = band[y:y + h, x:x + w]
            g = cv2.resize(cv2.cvtColor(c, cv2.COLOR_BGR2GRAY), None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
            for t in (170, 150, 190):
                b = 255 - cv2.threshold(g, t, 255, cv2.THRESH_BINARY)[1]
                b = cv2.copyMakeBorder(b, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)
                txt = pytesseract.image_to_string(b, config="--psm 7").lower()
                if re.search(r"\bu[s5]e\b|^u[s5]e", txt.strip()):
                    return int(340 + x + w // 2), int(y0 + y + h // 2)
        return None
    except Exception as _e:
        quiet_err("_stamina_use_button", _e)
        return None


def refill_stamina(dev, cfg, log, stop, needed):
    """Use stamina items. 'full' presses the game's Max button (the game itself limits how much one use can add)."""
    return _refill_once(dev, cfg, log, stop, needed)


def _refill_once(dev, cfg, log, stop, needed):
    """On the march screen: open the stamina Use Item screen and use stamina items (never gems, never the Buy banner)
    until the march can go. -> True if the stamina now covers `needed`."""
    nap = lambda lo, hi=None: _nap(stop, lo, hi)
    _items = {"100 only": [100], "50 only": [50], "10 only": [10], "50 + 100": [50, 100]}.get(str(cfg.get("stamina_items", "all")), None)
    _large = (str(cfg.get("stamina_order", "large first")) != "small first")
    sizes = sorted({int(x) for x in (_items or cfg.get("stamina_sizes", [10, 25, 50, 100]))}, reverse=_large)
    if not sizes:
        return False
    before = read_stamina(dev.screenshot())
    cur_st = before[0] if before else None

    def _open_use_item():
        for xy in (tuple(cfg["stamina_xy"]), (478, 853), (345, 853)):  # the saved spot, then the round "+" button, then the number
            dev.tap(*xy)
            nap(1.0, 1.5)
            sh = dev.screenshot()
            if find_text(sh, ["Use Item"], region=(120, 20, 300, 90)):
                return True
            if screen_state(sh, cfg) != "march":             # something else opened: back to the march screen, then try the next spot
                dev.back()
                nap(0.8, 1.2)
        return False

    def _to_march():
        for _ in range(3):
            if screen_state(dev.screenshot(), cfg) == "march":
                return
            dev.back()
            nap(0.8, 1.2)

    if not _open_use_item():
        log("[!] Stamina: the Use Item screen did not open")
        return False
    on_items = True
    scrolled = False
    try:                                                     # the sizes the item list shows, so a size the
        _sh = dev.screenshot()                               # bot cannot read shows up in the log; picture kept as debug/stamina_list.png
        _stamina_snap(_sh, "list")
        _seen = []
        for _, ws in ocr_lines(_sh[300:940, 0:340]):
            for k, w in enumerate(ws):
                if k + 1 < len(ws) and ws[k + 1][0].lower().startswith("stam") and re.sub(r"\D", "", w[0]):
                    _seen.append(re.sub(r"\D", "", w[0]))
        log(f"[i] Stamina items on the list: {', '.join(_seen) or 'sizes not readable'}")
    except Exception as _e:
        quiet_err("_refill_once.list", _e)
    used_any = False
    ROW_Y = {10: 442, 50: 604, 100: 766}                     # the title line of each item row on the Use Item screen (the Use button is ~49 px lower)

    def _row_y(shot, size):
        """Exact-number match ('100' must not match '10' or '50'), then the fixed spot - but only when no item row could
        be read at all. If other rows were read and this size is not among them, the account has none of it (the fixed
        spot would then open a different item, e.g. the 50 row when looking for 100)."""
        seen_rows = False
        try:
            for _, ws in ocr_lines(shot[300:940, 0:340]):
                for k, w in enumerate(ws):
                    if k + 1 < len(ws) and ws[k + 1][0].lower().startswith("stam") and re.sub(r"\D", "", w[0]):
                        seen_rows = True
                        if re.sub(r"\D", "", w[0]) == str(size):
                            return int(w[2]) + 300
        except Exception as _e:
            quiet_err("_refill_once._row_y", _e)
        return None if seen_rows else ROW_Y.get(size)

    def _dialog_open(shot):
        """The amount window (item picture, 'Own: n', 'You can receive', slider, Max / Use). Either text line is enough:
        the OCR sometimes cannot read 'Own: 7,218' at all (seen on Oogly), and then the open window was taken as closed."""
        if find_text(shot, ["You can receive", "can receive"], region=(40, 410, 440, 100), cutoff=0.72):
            return True
        return bool(find_text(shot, ["Own:", "Own"], region=(60, 310, 300, 80), cutoff=0.6)) and \
            bool(find_text(shot, ["Max", "Minimum"], region=(60, 650, 420, 70), cutoff=0.8))

    for size in sizes + ["any"]:                             # every size in the chosen order, then any Use button left
        if stop.is_set():
            break
        if size == "any" and used_any:
            break
        if not on_items:
            if not _open_use_item():
                log("[!] Stamina: the Use Item screen did not open again")
                break
            on_items = True
            scrolled = False
        shot = dev.screenshot()
        if size == "any":                                   # the size labels could not be read (e.g. '100' misread): every row on this
            _stamina_snap(shot, "items_list")               # screen is a stamina item, so any 'Use' button (never a gem price) is fine
            use = _stamina_use_button(shot, 300, 940)
            if not use:
                log("[i] Stamina: no Use button on the stamina item list - no items owned")
                break
            size = "other"
        else:
            ry = _row_y(shot, size)
            if not ry:                                      # 4 sizes do not fit on the screen (the 100 row sits below): look at the
                if scrolled:                                # other end of the list once - top if we scrolled down, bottom if not
                    dev.swipe(270, 560, 270, 880, 700)
                else:
                    dev.swipe(270, 880, 270, 560, 700)
                nap(0.8, 1.1)
                scrolled = not scrolled
                shot = dev.screenshot()
                ry = _row_y(shot, size)
            if not ry:
                continue                                    # the account has none of this size / not a size we know
            use = _stamina_use_button(shot, ry + 5, ry + 95)
            if not use:                                     # no 'Use' = none owned; the button shows a gem price - never tap it
                log(f"[i] Stamina: no {size}-stamina items (only a gem price) - next size")
                continue
        if isinstance(size, int) and cur_st is not None and STAMINA_CAP - cur_st < size \
                and str(cfg.get("stamina_amount", "full (Max)")).startswith("full"):
            continue                                        # not even one of this size fits under the limit
        dev.tap(*use)
        nap(1.0, 1.4)
        shot = dev.screenshot()
        if not _dialog_open(shot):                          # slow emulator: look again, then tap Use once more before giving up
            nap(1.0, 1.4)
            shot = dev.screenshot()
        if not _dialog_open(shot) and screen_state(shot, cfg) != "march" and (size == "other" or _row_y(shot, size)):
            dev.tap(*use)
            nap(1.2, 1.6)
            shot = dev.screenshot()
        if not _dialog_open(shot):
            _stamina_snap(shot, "no_dialog")
            # The tap may have used the item at once (no amount window - seen 24 Oct on Skip: 6 -> 29 stamina while the bot
            # said the window did not open, then skipped the rally). Check the march screen before trying the next size.
            _to_march()
            on_items = False
            nap(0.6, 0.9)
            st_now = read_stamina(dev.screenshot())
            if st_now and before and st_now[0] > before[0]:
                used_any = True
                log(f"[+] Stamina: {size}-stamina item used without the amount window ({before[0]:,} -> {st_now[0]:,})")
                cur_st = st_now[0]
                if st_now[0] >= needed and not str(cfg.get("stamina_amount", "full (Max)")).startswith("full"):
                    break
                before = st_now
            else:
                log(f"[i] Stamina: the amount window for {size} did not open - trying the next size")
            continue
        _stamina_snap(shot, "dialog")
        mode = str(cfg.get("stamina_amount", "full (Max)"))
        def _count(sh):
            try:
                for _, ws in ocr_lines(sh[540:590, 130:410]):
                    m = re.search(r"(\d+)\s*/\s*(\d+)", " ".join(w[0] for w in ws))
                    if m:
                        return int(m.group(1)), int(m.group(2))
            except Exception as _e:
                quiet_err("_refill_once._count", _e)
            return None
        if mode.startswith("full"):                          # fill up to the game's limit (9,999): as many items of this size as fit
            room = STAMINA_CAP - (cur_st if cur_st is not None else 0)
            fit = room // size if isinstance(size, int) else None
            _shot_max = dev.screenshot()
            cnt = _count(_shot_max)
            for _sw in range(4):                             # first the whole bar (NOT the game's Max button: it stops at 100 stamina)
                if cnt and cnt[0] >= cnt[1]:
                    break
                dev.swipe(137, 503, 440, 503, 700 if _sw % 2 == 0 else 1300)   # a slower drag when a quick one did not move the thumb
                nap(0.6, 0.9)
                _shot_max = dev.screenshot()
                cnt = _count(_shot_max)
            if cnt and fit is not None and cnt[1] > fit:    # more items than fit under the limit: the rest would be lost
                want = max(1, fit)
                if cnt[1] - want > 12:                      # far off: drag the thumb close, then fine-tune with - / +
                    dev.swipe(137, 503, int(137 + 303 * want / cnt[1]), 503, 900)
                    nap(0.6, 0.9)
                    cnt = _count(dev.screenshot()) or cnt
                for _t in range(40):
                    if not cnt or cnt[0] == want or stop.is_set():
                        break
                    dev.tap(*((83, 505) if cnt[0] > want else (451, 505)))
                    nap(0.2, 0.35)
                    cnt = _count(dev.screenshot()) or cnt
                _shot_max = dev.screenshot()
            _stamina_snap(_shot_max, "after_slide")
            log(f"[i] Stamina: using {cnt[0] if cnt else '?'} of {cnt[1] if cnt else '?'} item(s)"
                + (f" (room for {fit} under the {STAMINA_CAP:,} limit)" if fit is not None else ""))
        elif mode.startswith("exact"):
            want = max(1, int(cfg.get("stamina_count", 2) or 1))
            def _count(sh):
                try:
                    for _, ws in ocr_lines(sh[540:590, 130:410]):
                        m = re.search(r"(\d+)\s*/\s*(\d+)", " ".join(w[0] for w in ws))
                        if m:
                            return int(m.group(1)), int(m.group(2))
                except Exception as _e:
                    quiet_err("_refill_once._count", _e)
                return None
            cnt = _count(shot)
            for _t in range(60):
                if not cnt or cnt[0] == want or stop.is_set():
                    break
                if cnt[0] < want and cnt[0] >= cnt[1]:
                    break                                    # cannot go higher
                dev.tap(*((451, 505) if cnt[0] < want else (83, 505)))
                nap(0.25, 0.4)
                cnt = _count(dev.screenshot())
            log(f"[i] Stamina: using {cnt[0] if cnt else '?'} item(s) (wanted {want})")
        used = False
        for attempt in range(3):                            # the slider already sits at 2 items; press the green Use inside the window
            dev.tap(383 + random.randint(-12, 12), 685 + random.randint(-6, 6))
            nap(1.2, 1.7)
            shot = dev.screenshot()
            if not _dialog_open(shot):
                used = True
                break
            log(f"[i] Stamina: the amount window is still open - pressing Use again ({attempt + 2})")
        _stamina_snap(shot, "after_use")
        if not used:
            log("[!] Stamina: the Use button did not close the window - next size")
            dev.tap(502, 273)                               # the red X of the amount window
            nap(0.8, 1.2)
            continue
        used_any = True
        log(f"[+] Stamina: used {size}-stamina items")
        _to_march()                                         # check the march screen: enough now? else open Use Item again for the next size
        on_items = False
        nap(0.6, 0.9)
        st_now = read_stamina(dev.screenshot())
        if st_now:
            cur_st = st_now[0]
        if str(cfg.get("stamina_amount", "full (Max)")).startswith("full"):
            small = min(x for x in sizes if isinstance(x, int))  # owner 24 Oct: fill FULL with every size, not just enough for this march
            if st_now and STAMINA_CAP - st_now[0] < small:
                break
            if st_now:
                log(f"[i] Stamina: {st_now[0]:,} - filling on with the next size (limit {STAMINA_CAP:,})")
            continue
        if st_now and st_now[0] >= needed:
            break
        if st_now:
            log(f"[i] Stamina: {st_now[0]:,} still under {needed:,} - trying the next size")
    if on_items:
        _to_march()
    nap(0.8, 1.2)
    shot_end = dev.screenshot()
    st = read_stamina(shot_end)
    log(f"[i] Stamina after using items: {st[0]:,} (needs {needed:,})" if st else "[i] Stamina after using items: not readable")
    _stamina_snap(shot_end, "march_after")
    if st and st[0] >= needed:                              # the number on the march screen decides, however the items got used
        return True
    if used_any and (st is None or st[0] == before[0] if before else True) and screen_state(shot_end, cfg) == "march":
        log("[i] The stamina number did not change after using items - it may be misread, marching anyway")
        return True
    return False


def _stamina_snap(img, tag):
    try:
        d = USER / "debug"
        d.mkdir(exist_ok=True)
        cv2.imwrite(str(d / f"stamina_{tag}.png"), img)
    except Exception as _e:
        quiet_err("_stamina_snap", _e)


def preset_xy(cfg, n):
    xs = cfg["preset_x"]
    return xs[min(n, len(xs)) - 1], cfg["preset_tap_y"]


def save_debug(serial, screen, limit=40):
    """Keep a few screenshots of screens the bot did not recognise (for making templates)."""
    d = USER / "debug"
    d.mkdir(exist_ok=True)
    shots = sorted(d.glob("127_*.png"), key=lambda f: f.stat().st_mtime) + sorted(d.glob("emulator_*.png"), key=lambda f: f.stat().st_mtime)
    for old in shots[:max(0, len(shots) - limit + 1)]:            # keep the newest ones: drop the oldest instead of never saving again
        try:
            old.unlink()
        except Exception as _e:
            quiet_err("save_debug", _e)
    safe = re.sub(r"\W", "_", serial)
    cv2.imwrite(str(d / f"{safe}_{int(time.time())}.png"), screen)


def housekeeping(log=None):
    """Keep the data folder small on PCs that run for weeks:
    debug pictures: only the newest 60 and nothing older than 3 days; old program versions: only the current and the
    previous one (also after a hand-made install); the log files clean themselves (5 days)."""
    freed, n = 0, 0
    d = USER / "debug"
    if d.exists():
        files = sorted((f for f in d.iterdir() if f.is_file()), key=lambda f: f.stat().st_mtime, reverse=True)
        cutoff = time.time() - 3 * 86400
        for i, f in enumerate(files):
            try:
                st = f.stat()
                if i >= 60 or st.st_mtime < cutoff:
                    freed += st.st_size
                    f.unlink()
                    n += 1
            except OSError as _e:
                quiet_err("housekeeping", _e)
    try:
        cur = (APP_DIR / "current.txt").read_text().strip() if (APP_DIR / "current.txt").exists() else ""
        prev = (APP_DIR / "previous.txt").read_text().strip() if (APP_DIR / "previous.txt").exists() else ""
        for v in APP_DIR.glob("v*"):
            if v.is_dir() and v.name not in (cur, prev):
                freed += sum(f.stat().st_size for f in v.rglob("*") if f.is_file())
                shutil.rmtree(v, ignore_errors=True)
                n += 1
        for t in APP_DIR.glob(".tmp-*"):                    # half-finished downloads
            shutil.rmtree(t, ignore_errors=True)
    except Exception as _e:
        quiet_err("housekeeping", _e)
    if log and n:
        log(f"[i] Cleaned up {n} old file(s)/version(s), {freed / 1048576:.0f} MB freed")
    return freed


# ------------------------------------------------------------------ helpers
_TL = threading.local()                 # per bot thread: pace + speed counters


def pause_gate():
    """Block while this instance is paused from the GUI (taps and swipes call it, so a pause takes effect at the very next
    action, also during start-up, the game-loading wait and long tasks). -> True when it had to wait."""
    ctl, stop = getattr(_TL, "ctl", None), getattr(_TL, "stop", None)
    waited = False
    while ctl is not None and not ctl.run.is_set() and not (stop is not None and stop.is_set()):
        waited = True
        time.sleep(0.3)
    return waited
CPU_LEVELS = {1: 1.0, 2: 1.5, 3: 2.5, 4: 4.0}      # how much longer the pause between scans gets


def set_pacing(cfg):
    configure_resources(cfg)
    ms = float(cfg.get("click_delay_ms", 1000) or 1000)
    _TL.scale = min(max(ms / 1000.0, 0.1), 6.0)
    _TL.boost, _TL.boost_max = 1.0, max(1.0, float(cfg.get("adaptive_max", 2.0)))
    _TL.adaptive = bool(cfg.get("adaptive_delay", True))
    _TL.cpu = CPU_LEVELS.get(int(cfg.get("cpu_level", 2) or 2), 1.5)
    _TL.perf = {"shots": 0, "shot_ms": 0.0, "ocr": 0, "ocr_ms": 0.0, "hits": 0, "loops": 0, "t0": time.time()}
    try:
        cv2.setNumThreads(max(1, int(cfg.get("cv_threads", 1))))
    except Exception as _e:
        quiet_err("set_pacing", _e)


def pace(seconds):
    return seconds * getattr(_TL, "scale", 1.0) * getattr(_TL, "boost", 1.0)


def slow_down():
    """Something failed: wait longer after clicks for a while. -> new factor or None."""
    if not getattr(_TL, "adaptive", False):
        return None
    new = min(_TL.boost * 1.25, _TL.boost_max)
    changed = new > _TL.boost + 0.01
    _TL.boost = new
    return new if changed else None


def speed_up():
    if getattr(_TL, "adaptive", False) and _TL.boost > 1.0:
        _TL.boost = max(1.0, _TL.boost * 0.9)


def _nap(stop, lo, hi=None):
    stop.wait(pace(random.uniform(lo, hi if hi is not None else lo)))


def perf_report():
    p = getattr(_TL, "perf", None)
    if not p or not p["loops"]:
        return None
    mins = max((time.time() - p["t0"]) / 60.0, 0.01)
    txt = (f"[perf] {p['loops'] / mins:.0f} scans/min, screenshot {p['shot_ms'] / max(p['shots'], 1):.0f} ms, "
           f"text reads {p['ocr'] / mins:.0f}/min ({p['ocr_ms'] / max(p['ocr'], 1):.0f} ms each, "
           f"{p['hits']} answered from memory)")
    _TL.perf = {"shots": 0, "shot_ms": 0.0, "ocr": 0, "ocr_ms": 0.0, "hits": 0, "loops": 0, "t0": time.time()}
    try:
        import gc
        gc.collect()                                     # hand freed memory back regularly (8 bots share one process)
    except Exception as _e:
        quiet_err("perf_report", _e)
    return txt


def list_templates(prefix):
    names = set()
    for base in (USER / "templates", RES / "templates"):
        if base.exists():
            names |= {p.stem for p in base.glob(f"{prefix}*.png")}
    return sorted(names)


class DeviceControl:
    """Per-instance switches: stop and pause/resume."""

    def __init__(self):
        self.stop = threading.Event()
        self.run = threading.Event()
        self.run.set()
        self.no_stamina = 0                 # rallies in a row that failed for lack of stamina (UI shows "NO STAMINA" from 3)

    def reset(self):
        self.no_stamina = 0
        self.stop.clear()
        self.run.set()

    def pause(self):
        self.run.clear()

    def resume(self):
        self.run.set()

    @property
    def paused(self):
        return not self.run.is_set()


# ----------------------------------------------------------------- generals
GEN_BOX = (120, 130)                    # old fixed picture size (kept for old code)
GEN_FROM_CENTRE = (60, 92)
GEN_SLOTS_X, GEN_SLOTS_Y = (101, 269, 439), (319, 568)     # card centres in the grid view
GEN_CARDS_X, GEN_CARDS_Y = (22, 191, 360), (203, 452)      # top-left of each card in the grid view
GEN_PORTRAIT = (4, 32, 152, 136)        # the whole portrait art inside a card (x, y, w, h), without the stars,
                                        # level badge and the "(Idle)" text, which change


CARD_PITCH, CARD_H = 249.5, 235          # distance between card rows, card height (540x960)
LIST_TOP, LIST_BOTTOM = 190, 925         # the part of the screen where generals can be


def card_tops(img):
    """y of the top edge of every card row in a grid-view general list (also when the list is scrolled, or
    has a banner above it). The rows are always the same distance apart, so find where the dark gaps
    between the rows fall."""
    v = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[..., 2]
    dark = (v[:, 30:170] < 55).mean(axis=1)
    acc = np.zeros(250)
    for y in range(LIST_TOP, LIST_BOTTOM + 10):
        acc[int(y % CARD_PITCH)] += dark[y]
    k = 11
    sm = np.convolve(np.r_[acc[-k:], acc, acc[:k]], np.ones(k) / k, mode="same")[k:-k]
    gap = float(np.argmax(sm))
    return [int(round(gap + n * CARD_PITCH + 9)) for n in range(-1, 5)
            if LIST_TOP - CARD_H < gap + n * CARD_PITCH + 9 < LIST_BOTTOM]


def card_at(img, x, y):
    """(left, top) of the card under (x, y), or None."""
    left = min(GEN_CARDS_X, key=lambda v: abs(v + 79 - x))
    for top in card_tops(img):
        if top - 4 <= y <= top + CARD_H + 4:
            return left, top
    return None


def card_stars(img, left, top):
    """How many gold stars the card shows (0-5)."""
    hsv = cv2.cvtColor(img[max(top, 0):top + 30, left:left + 140], cv2.COLOR_BGR2HSV)
    n = 0
    for i in range(5):
        box = hsv[8:24, 39 + int(i * 17.4):53 + int(i * 17.4)]
        if box.size and ((box[..., 0] >= 12) & (box[..., 0] <= 35) & (box[..., 1] >= 110) & (box[..., 2] >= 170)).mean() > 0.25:
            n += 1
    return n


def card_is_idle(img, left, top):
    """The card's status text is green when the general is '(Idle)' (free to be chosen)."""
    c = img[max(top + 168, 0):top + 196, left + 35:left + 125]
    if c.size == 0:
        return True
    b, g, r = [c[..., i].astype(int) for i in range(3)]
    return int(((g > 140) & (r < 110) & (b < 110)).sum()) >= 10


def _dark_fraction(img, y0, y1):
    v = cv2.cvtColor(img[max(y0, 0):max(y1, 0), 30:170], cv2.COLOR_BGR2HSV)[..., 2]
    return 0.0 if v.size == 0 else float((v < 55).mean())


def card_row_valid(img, top):
    """True when a whole row of cards (with the dark gap above and below it) is on screen."""
    return (top + CARD_H <= LIST_BOTTOM and top >= LIST_TOP - 12
            and _dark_fraction(img, top - 12, top - 3) >= 0.8
            and _dark_fraction(img, top + CARD_H + 2, top + CARD_H + 11) >= 0.8)


def read_card_name(img, left, top):
    """Best-effort OCR of the name under a card (the font is stylised, so names may need a touch-up)."""
    c = img[top + 198:top + 232, left + 34:left + 136]
    if c.size == 0:
        return ""
    g = cv2.resize(cv2.cvtColor(c, cv2.COLOR_BGR2GRAY), None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    b = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, -10)
    if b.mean() < 127:
        b = 255 - b
    b = cv2.copyMakeBorder(b, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)
    try:
        txt = pytesseract.image_to_string(b, config="--psm 6")
    except Exception:
        return ""
    words = [w for w in re.sub(r"[^A-Za-z.\- ]", " ", txt).split()
             if len(re.sub(r"[^A-Za-z]", "", w)) >= 3 or (w[:1].isupper() and len(w) >= 2)]
    small = {"of", "the", "de", "von", "van", "la", "le", "du", "di", "der", "bin", "ibn"}
    return " ".join((w[:1].upper() + w[1:]) if (w[:1].islower() and w.lower() not in small) else w
                    for w in (w.strip(".-") for w in words) if w)


KNOWN_GENERALS = [
"Abd al-Rahman I",
"Alexander Nevsky",
"Alexander Suvorov",
"Alfred the Great",
"Amir Timur",
"Andrew Jackson",
"Antara",
"Arminius",
"Askia Muhammad I",
"Attila",
"Aurelian",
"Baibars",
"Ban Chao",
"Barbarossa",
"Casimir Pulaski",
"Catherine II",
"Charles Martel",
"Charles XII",
"Charles the Great",
"Cleopatra",
"Constance I",
"Cyrus the Great",
"Dmitry",
"Douglas",
"Edward III",
"Edward Teach",
"Edward the Black Prince",
"Emperor Qin Shihuang",
"Empress Dowager Cixi",
"Empress Wu",
"Eulji Mundeok",
"Frederick the Great",
"Gaius Marius",
"Genghis Khan",
"George A. Custer",
"George Dewey",
"Gnarr",
"Gustavus Adolphus",
"Gwanggaeto the Great",
"Gwon Ryul",
"Hamilcar Barca",
"Hannibal",
"Harald",
"Harun al-Rashid",
"Henry V",
"Hernando Cortes",
"Himiko",
"Honda Tadakatsu",
"Hua Mulan",
"Huo Qubing",
"Isabella",
"Ivan IV",
"Jadwiga",
"James Bowie",
"Jefferson",
"Joan of Arc",
"John Buford",
"John Churchill",
"John I of Portugal",
"Jumong",
"Khalid",
"Kim Si-min",
"King Arthur",
"King Sejong",
"Kusunoki Masashige",
"Laudon",
"Li Jing",
"Li Shimin",
"Lincoln",
"Lu Ban",
"Ly Thuong Kiet",
"Mansa Musa",
"Margaret I",
"Martinus",
"Matthias I",
"Menshikov",
"Mihai",
"Minamoto no Yoshitsune",
"Miyamoto Musashi",
"Mordred",
"Napoleon",
"Nathanael Greene",
"Nero",
"Oda Nobunaga",
"Oleg of Novgorod",
"Pallas",
"Peter the Great",
"Philip Sheridan",
"Poligenus",
"Pompey",
"Prince Rhaegar",
"Pyrrhus",
"Queen Boudica",
"Queen Jindeok",
"Queen Victoria",
"Richard the Lionheart",
"Robert Guiscard",
"Robert the Bruce",
"Roland",
"Saladin",
"Sanada Yukimura",
"Seleucus I",
"Shajar al-Durr",
"Sherman",
"Shimazu Yoshihiro",
"Skanderbeg",
"Soult",
"Spartacus",
"Subutai",
"Suleiman the Magnificent",
"Takeda Shingen",
"Thutmose III",
"Tokugawa Ieyasu",
"Toyotomi Hideyoshi",
"Trajan",
"Tran Hung Dao",
"Turenne",
"Uesugi Kenshin",
"Ulysses S. Grant",
"Vladimir the Great",
"Washington",
"Wei Qing",
"William Wallace",
"Winfield Scott",
"Xiang Yu",
"Yi Song-gye",
"Yue Fei",
"Zucca",
"Elektra",

"Theodora",
"Scipio Africanus",
# generals with a picture in the shipped catalogue (catalog/art) - 23 Oct 2026
"Aethelflaed",
"Ahmose I",
"Akechi Mitsuhide",
"Alessandra",
"Alexander Hamilton",
"Alexander the Great",
"Algernon Sidney",
"Amr ibn al-As",
"Andre Massena",
"Artemis",
"Artemisia I",
"Arthur Wellesley",
"Ashoka",
"Babur",
"Baldwin IV",
"Basil II",
"Beowulf",
"Bertrand du Guesclin",
"Blanche",
"Brian Boru",
"Caesar",
"Carus",
"Caterina Sforza",
"Champlain",
"Charles V",
"Charles VI",
"Cheng Yaojin",
"Christina",
"Cimon",
"Clovis I",
"Cnut the Great",
"Constantine the Great",
"Coyolxauhqui",
"Daniel Morgan",
"Darius I",
"David Farragut",
"Demetrius I",
"El Cid",
"Eleanor",
"Elise",
"Elizabeth I",
"Epaminondas",
"Flavius Aetius",
"Francis Drake",
"Francis I of France",
"Franz Joseph I",
"Freyja",
"Frigg",
"Gaius Octavius",
"Gao Changgong",
"George Monck",
"Gilgamesh",
"Godfrey of Bouillon",
"Gonzalo",
"Guan Yu",
"Guinevere",
"Gunther",
"Haakon Haraldsson",
"Hammurabi",
"Harald III",
"Hatshepsut",
"Helena",
"Henry IV",
"Hera",
"Heraclius",
"Hermes",
"Hersilia",
"Hestia",
"Hojo Ujiyasu",
"Hugh O'Donnell",
"Huitzilopochtli",
"Idunn",
"Ii Naomasa",
"Irene d'Arneau",
"Isaac Brock",
"James Conrad",
"James Cook",
"Jan Karol Chodkiewicz",
"Jayavarman II",
"Jester",
"Jiang Ziya",
"John Adams",
"John Hawkwood",
"John Hunyadi",
"Justinian I",
"Kiso Yoshinaka",
"Kleos",
"Lafayette",
"Lautaro",
"Leo III",
"Leonidas I",
"Liu Bei",
"Lorenzo de' Medici",
"Louis II",
"Louis IX",
"Louis XIV",
"Lu Xun",
"Lucilla",
"Ludwig",
"Lysander",
"Maarten Tromp",
"Maeda Toshiie",
"Marcian",
"Marco Polo",
"Marcus Agrippa",
"Margaret",
"Maria Carolina",
"Maria Theresa",
"Mark Antony",
"Mason Weaver",
"Matsunaga Hisahide",
"Maurice de Saxe",
"Maurice of Nassau",
"Maximilian I",
"Mehmed II",
"Melisande",
"Merlin",
"Michael VIII",
"Mikhail Kutuzov",
"Miltiades",
"Montezuma I",
"Mouri Motonari",
"Mu Guiying",
"Napoleon (Prime)",
"Narcissus",
"Narses",
"Niccolo Piccinino",
"Niwa Nagahide",
"Nordic Barbarian King",
"Oda Oichi",
"Olaf Skotkonung",
"Olav II",
"Ono no Imoko",
"Otto the Great",
"Pachacuti",
"Pericles",
"Petronas",
"Philip II",
"Philippa",
"Presley O'Bannon",
"Prince Eugene",
"Princess Kaguya",
"Princess Lucy",
"Probus",
"Pythia",
"Qin Qiong",
"Queen Anne",
"Queen Tamar",
"Raged King",
"Ragnar",
"Raimondo",
"Ramesses II",
"Roger II",
"Romulus",
"Rostam",
"Rumyantsev",
"Sappho",
"Sargon I",
"Sei Shonagon",
"Septimius Severus",
"Shapur I",
"Shaybani",
"Sigurd",
"Simeon the Great",
"Stephen I of Hungary",
"Stephen II",
"Stephen the Great",
"Stilicho",
"Suchet",
"Sulla",
"Sun Ce",
"Sun Wu",
"Surena",
"Sweyn Forkbeard",
"Tachibana Dosetsu",
"Tachibana Ginchiyo",
"Takenaka Shigeharu",
"Themistocles",
"Theobald I",
"Theodosius I",
"Tishtrya",
"Tomyris",
"Totila",
"Toyotomi Hideyori",
"Trivia Hecate",
"Vercingetorix",
"Visconti",
"Vlad al III",
"Wallenstein",
"Washington (Prime)",
"William Marshal",
"Yeon Gaesomun",
"Yi Sun-sin",
"Yodo-dono",
"Yolande of Aragon",
"Zachary Taylor",
"Zhang Fei",
"Zhang Liang",
"Zhao Yun",
"Zhou Yu",
"Zhu Di",
"Zhuge Liang",
"Zizka",
]   # the game's recruitable generals (read from the Tavern list)


def snap_name(name, known):
    """Use the spelling of a known general (saved ones and the built-in list of the game's generals) when the OCR
    result is close; the stylised font often loses the first letter or a full stop ("Yrus the Great")."""
    import difflib
    if not name:
        return name
    key = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())
    pool = {}
    for n in list(known) + KNOWN_GENERALS:
        pool.setdefault(key(n), n)
    k = key(name)
    if k in pool:
        return pool[k]
    close = difflib.get_close_matches(k, list(pool), n=1, cutoff=0.78)
    return pool[close[0]] if close else name


def portrait_rect(cx, cy, img=None):
    """The whole portrait of the grid card at (cx, cy) -> (x, y, w, h) at 540x960, or None if that card is cut off
    by the edge of the list. With a screenshot, the real row positions are used (the list can be scrolled)."""
    dx, dy, w, h = GEN_PORTRAIT
    if img is None:
        x0 = min(GEN_CARDS_X, key=lambda v: abs(v + 79 - cx))
        y0 = min(GEN_CARDS_Y, key=lambda v: abs(v + 116 - cy))
        return (x0 + dx, y0 + dy, w, h)
    c = card_at(img, cx, cy)
    if not c:
        return None
    x0, y0 = c
    if y0 + dy < LIST_TOP + 8 or y0 + dy + h > LIST_BOTTOM:
        return None                                       # cut off at the top or bottom of the list
    return (x0 + dx, y0 + dy, w, h)
_gen_cache = {}
_gen_names = [0.0, []]


def generals_dirs():
    return [USER / "generals", RES / "generals", USER / "catalog" / "art", RES / "catalog" / "art"]


CATALOG_TAB = {"Ground Troop": 2, "Mounted Troop": 2, "Ranged Troop": 2, "Siege Machine": 2, "Defense": 2, "Mixed": 2,
               "Administration": 3, "Duty": 4, "Subordinate City": 5}      # troop type (Portraits filter) -> tab of the Generals list


def is_known_general(name):
    key = lambda t: re.sub(r"[^a-z0-9]", "", str(t).lower())
    return key(name) in {key(n) for n in KNOWN_GENERALS}


def load_catalog():
    """{name: {'region': [..], 'troops': [..]}} of the generals the game has (built by crawl_catalog.py, shipped with the app)."""
    out = {}
    for d in (RES / "catalog", USER / "catalog"):
        try:
            for g in json.loads((d / "catalog.json").read_text(encoding="utf-8")).get("generals", []):
                if is_known_general(g["name"]):            # hides junk entries left by older, poor crawls
                    out[g["name"]] = g
        except Exception:
            continue
    return out


def catalog_tab(name):
    """The tab of the Generals list a catalogue general lives in (2-5), or 0 when unknown."""
    g = load_catalog().get(name)
    for t in (g or {}).get("troops", []):
        if t in CATALOG_TAB:
            return CATALOG_TAB[t]
    return 0


def list_generals(refresh=False):
    if refresh or time.time() - _gen_names[0] > 5:
        names = set()
        for d in generals_dirs():
            if d.exists():
                names |= {p.stem for p in d.glob("*.png")}
        names -= set(load_general_meta().get("__hidden__", []))
        _gen_names[:] = [time.time(), sorted(names, key=str.lower)]
    return _gen_names[1]


def _gen_tpl(name):
    with _cache_lock:
        if name not in _gen_cache:
            img = None
            for d in generals_dirs():
                p = d / f"{name}.png"
                if p.exists():
                    img = cv2.imread(str(p))
                    break
            _gen_cache[name] = img
        return _gen_cache[name]


def _portrait_card_art(name):
    """True when the only picture of this general is a full-body card from Tavern > Portraits (catalogue art, taller
    than wide). Those are for showing the general; on the 'Select a General' screen the bot finds it by name instead."""
    if not _is_catalog_art(name):
        return False
    t = _gen_tpl(name)
    return t is not None and t.shape[0] > t.shape[1] * 1.15


def _is_catalog_art(name):
    for d in generals_dirs()[:2]:
        if (d / f"{name}.png").exists():
            return False
    return True


def find_general(screen, name, threshold=0.8, copy="any", idle_only=False):
    """Find a general's portrait in the list area. -> (x, y, score, (w, h)) or None.
    If you own several copies (different stars / levels): copy = "any" (best match), "stars" (most gold stars
    among those on screen) or a number 1, 2, 3... (reading order among those on screen).
    With idle_only, copies that are busy are ignored; _TL.busy_seen tells you when that was the only reason."""
    _TL.busy_seen = False
    _TL.best = 0.0
    t0 = _gen_tpl(name)
    if t0 is None:
        return None
    top = 195
    area = screen[top:900]
    catalog = _is_catalog_art(name)                      # art from the shipped catalogue: slightly different framing
    if catalog:
        threshold = min(threshold, 0.62)
    res, t = None, t0
    for sc in ((0.94, 1.0, 1.06) if catalog else (1.0,)):
        tt = t0 if sc == 1.0 else cv2.resize(t0, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA)
        if area.shape[0] < tt.shape[0] or area.shape[1] < tt.shape[1]:
            continue
        r = cv2.matchTemplate(area, tt, cv2.TM_CCOEFF_NORMED)
        m = float(r.max())
        _TL.best = max(_TL.best, m)
        if res is None or m > float(res.max()):
            res, t = r, tt
    if res is None:
        return None
    w, h = t.shape[1], t.shape[0]
    _, best, _, _ = cv2.minMaxLoc(res)
    if best < threshold:
        return None
    ys, xs = np.where(res >= threshold)
    hits = []
    for y, x in sorted(zip(ys, xs), key=lambda p: -res[p[0], p[1]]):
        cx, cy = int(x + w // 2), int(y + top + h // 2)
        if all(abs(cx - a) > w // 2 or abs(cy - b) > h // 2 for a, b, _ in hits):
            hits.append((cx, cy, float(res[y, x])))
    if idle_only:
        free = []
        for hit in hits:
            c = card_at(screen, hit[0], hit[1])
            if c is None or card_is_idle(screen, *c):
                free.append(hit)
        if not free:
            _TL.busy_seen = True
            return None
        hits = free
    if copy in (None, "", "any", 0):
        pick = hits[0]
    elif copy == "stars":
        def stars(hit):
            c = card_at(screen, hit[0], hit[1])
            return card_stars(screen, *c) if c else 0
        pick = max(hits, key=lambda a: (stars(a), a[2]))
    else:
        order = sorted(hits, key=lambda a: (round(a[1] / 100.0), a[0]))
        n = int(copy)
        if n > len(order):
            return None
        pick = order[n - 1]
    return (pick[0], pick[1], pick[2], (w, h))


def save_general(name, img, cx, cy):
    """Save the whole portrait of the grid-view card nearest to (cx, cy) in a 540x960 screenshot."""
    rect = portrait_rect(cx, cy, img)
    if rect is None:
        raise ValueError("That card is cut off by the edge of the list. Scroll so it is fully visible.")
    return save_general_region(name, img, rect)


def save_general_region(name, img, rect):
    """Save the part of a 540x960 screenshot given as (x, y, w, h) as the picture of a general."""
    name = _clean_general_name(name)
    x, y, w, h = [int(v) for v in rect]
    x, y = max(x, 0), max(y, 0)
    w, h = min(w, img.shape[1] - x), min(h, img.shape[0] - y)
    if w < 30 or h < 30:
        raise ValueError("The picture is too small - drag a bigger box.")
    (USER / "generals").mkdir(exist_ok=True)
    path = USER / "generals" / f"{name}.png"
    if not cv2.imwrite(str(path), img[y:y + h, x:x + w]):
        raise ValueError(f"Could not write {path}")
    _unhide_general(name)
    with _cache_lock:
        _gen_cache.clear()
    list_generals(refresh=True)
    return path


# ------------------------------------------------------ scanning the general list
SCAN_STEP = 240                          # how far each scroll moves the list (a bit less than one row)


def same_art(a, b, threshold=0.65):
    """Two portrait crops of the same size show the same general (stars / level are not part of the crop)."""
    if a.shape != b.shape:
        return False
    return float(cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)[0][0]) >= threshold


def capture_cards(img):
    """Every fully visible, non-empty card of a grid-view general list ->
    [{'left','top','image','stars','idle'}], reading order."""
    dx, dy, w, h = GEN_PORTRAIT
    out = []
    for top in card_tops(img):
        if not card_row_valid(img, top):
            continue
        for left in GEN_CARDS_X:
            crop = img[top + dy:top + dy + h, left + dx:left + dx + w]
            if crop.shape[:2] != (h, w) or crop.std() < 18 or crop.mean() < 25:          # an empty slot
                continue
            out.append({"left": left, "top": top, "image": crop.copy(),
                        "stars": card_stars(img, left, top), "idle": card_is_idle(img, left, top)})
    return out


def scan_generals(dev, cfg, log, stop, progress=None, max_scrolls=90, found=None, tab=0, add_new=True):
    """Scroll through a grid-view general list (already open in the game) and capture every different general.
    -> [{'name', 'image', 'stars', 'tabs'}]. Copies of the same general (same portrait) are listed once.
    found / tab: keep adding to the list of an earlier scan and remember which tab each general was seen in."""
    known = set(list_generals(refresh=True))
    found = [] if found is None else found

    def same(a, b):
        return float(np.mean(cv2.absdiff(a[LIST_TOP:LIST_BOTTOM], b[LIST_TOP:LIST_BOTTOM]))) < 1.5

    prev = None
    for k in range(30):                                   # to the top of the list: long quick swipes, check every second one
        if stop.is_set():
            break
        dev.swipe(270, 250, 270, 900, 220)
        _nap(stop, 0.25, 0.4)
        if k % 2 == 1:
            shot = dev.screenshot()
            if prev is not None and same(prev, shot):
                break
            prev = shot
    prev, back_tries = None, 0
    for i in range(max_scrolls + 1):
        if stop.is_set():
            break
        shot = dev.screenshot()
        cards = capture_cards(shot)
        if i == 0 and not cards:
            raise ValueError("I cannot see any generals. Open the general list in the game, switch it to GRID view "
                             "(the button next to the favourites heart), scroll to the top and try again.")
        matched = 0
        for c in cards:
            old = next((f for f in found if same_art(f["image"], c["image"], 0.65 if add_new else 0.5)), None)
            if not old and not add_new:                   # other tabs only tag generals the All tab already listed
                continue
            if old:
                matched += 1
                old["stars"] = max(old["stars"], c["stars"])
                old.setdefault("tabs", set()).add(tab)
                continue
            c["tabs"] = {tab}
            c["name"] = snap_name(read_card_name(shot, c["left"], c["top"]), known | {f["name"] for f in found if f["name"]})
            found.append(c)
        log(f"[*] Scanned {len(found)} different generals so far")
        if progress:
            progress(len(found))
        if prev is not None and same(prev, shot):         # the list did not move: this is the end
            break
        if i > 0 and cards and matched == 0 and back_tries < 3:      # no overlap: the scroll was too big
            back_tries += 1
            dev.swipe(270, 450, 270, 450 + SCAN_STEP // 2, 800)
            _nap(stop, 0.9, 1.3)
            continue
        back_tries = 0
        prev = shot
        dev.swipe(270, 780, 270, 780 - SCAN_STEP, 800)
        _nap(stop, 0.9, 1.3)
    n = 0
    for f in found:                                       # names the OCR could not read
        if not f["name"]:
            n += 1
            f["name"] = f"General {n}"
    return found


def scan_all_tabs(dev, cfg, log, stop, progress=None, open_screen=True):
    """Open Generals from the city (3-dots menu), switch to thumbnail view and scan all five tabs. Every general
    remembers the tabs it appeared in. -> [{'name', 'image', 'stars', 'tabs': [1-5]}]"""
    if open_screen and not open_generals_screen(dev, cfg, log, stop):
        raise ValueError("I could not open the Generals screen. Open the city screen and try again "
                         "(or open Generals yourself and scan from there).")
    found = []
    try:
        if not set_list_view(dev, "grid", log, stop):
            raise ValueError("I could not switch the general list to thumbnail (grid) view. Switch it yourself with "
                             "the little button under the tabs and try again.")
        for tab in range(1, 6):
            if stop.is_set():
                break
            log(f"[*] Tab {tab} of 5: {GEN_TAB_NAMES[tab - 1]}")
            if tab_row(dev.screenshot()) is None:                 # a pop-up covered the Generals screen: close it, carry on
                clear_popups(dev, cfg, log, stop)
                if tab_row(dev.screenshot()) is None and not open_generals_screen(dev, cfg, log, stop):
                    log("[!] Lost the Generals screen - stopping the scan")
                    break
            if not select_gen_tab(dev, tab, log, stop):
                log(f"[!] Could not open the {GEN_TAB_NAMES[tab - 1]} tab - skipped")
                continue
            set_list_view(dev, "grid", log, stop)
            try:
                scan_generals(dev, cfg, log, stop, progress, found=found, tab=tab, add_new=(tab == 1 or not found))
            except ValueError as e:
                if tab == 1:
                    raise
                log(f"[i] {GEN_TAB_NAMES[tab - 1]}: no generals in this tab")
        for f in found:
            f["tabs"] = sorted(f.get("tabs") or [])
    finally:
        if open_screen:
            close_generals_screen(dev, cfg, log, stop)
    return found


def save_general_image(name, portrait):
    """Save an already cropped portrait (from scan_generals) as the picture of a general."""
    name = _clean_general_name(name)
    (USER / "generals").mkdir(exist_ok=True)
    if not cv2.imwrite(str(USER / "generals" / f"{name}.png"), portrait):
        raise ValueError("Could not write the picture.")
    _unhide_general(name)
    with _cache_lock:
        _gen_cache.clear()
    list_generals(refresh=True)
    return name


# ---- managing generals: per-general tab / favourites, rename, delete, picture
def _gen_meta_file():
    return USER / "generals" / "generals.json"


def load_general_meta():
    """{name: {"tab": 0-5, "fav": "leave|on|off"}, "__hidden__": [names]} (bundled file first, yours on top)."""
    meta = {}
    for d in (RES / "generals", USER / "generals"):
        try:
            data = json.loads((d / "generals.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            meta.update(data)
    return meta


def _save_general_meta(meta):
    (USER / "generals").mkdir(exist_ok=True)
    save_json(_gen_meta_file(), meta, indent=2)


def get_general_meta(name):
    m = load_general_meta().get(name)
    return m if isinstance(m, dict) else {}


_KEEP = "\0keep"


def set_general_meta(name, tab=None, fav=None, copy=None, tabs=None, cat=_KEEP):
    """tab: 0-5 or None (= use the role setting). fav: leave|on|off or None (= use the role setting).
    copy: None / "any", "stars" or a number - which copy to pick when you own several."""
    meta = load_general_meta()
    entry = {}
    if copy not in (None, "", "any"):
        entry["copy"] = copy if copy == "stars" else int(copy)
    if tab not in (None, ""):
        entry["tab"] = max(0, min(5, int(tab)))
    if fav not in (None, ""):
        entry["fav"] = fav
    old = meta.get(name) if isinstance(meta.get(name), dict) else {}
    cat = old.get("cat", "") if cat is _KEEP or cat == _KEEP else str(cat or "").strip()[:40]
    if cat:
        entry["cat"] = cat
    if tabs is None:
        tabs = old.get("tabs")
    if tabs:
        entry["tabs"] = sorted({int(t) for t in tabs if 1 <= int(t) <= 5})
    if entry:
        meta[name] = entry
    else:
        meta.pop(name, None)
    _save_general_meta(meta)


def _unhide_general(name):
    meta = load_general_meta()
    hidden = [n for n in meta.get("__hidden__", []) if n != name]
    if hidden != meta.get("__hidden__", []):
        meta["__hidden__"] = hidden
        _save_general_meta(meta)


def _clean_general_name(name):
    name = re.sub(r'[\\/:*?"<>|]', "", str(name)).strip()
    if not name or name == "__hidden__":
        raise ValueError("Give the general a name.")
    return name


def general_image(name):
    """The saved picture of a general (BGR) or None."""
    t = _gen_tpl(name)
    return None if t is None else t.copy()


def delete_general(name):
    p = USER / "generals" / f"{name}.png"
    if p.exists():
        p.unlink()
    meta = load_general_meta()
    if any((d / f"{name}.png").exists() for d in generals_dirs()[1:]):
        meta["__hidden__"] = sorted(set(meta.get("__hidden__", [])) | {name})   # a bundled picture cannot be erased
    meta.pop(name, None)
    _save_general_meta(meta)
    with _cache_lock:
        _gen_cache.clear()
    list_generals(refresh=True)


def rename_general(old, new):
    new = _clean_general_name(new)
    if new == old:
        return new
    if new.lower() in {n.lower() for n in list_generals(refresh=True)}:
        raise ValueError(f"There is already a general called '{new}'.")
    img = general_image(old)
    if img is None:
        raise ValueError(f"No picture for '{old}'.")
    (USER / "generals").mkdir(exist_ok=True)
    if not cv2.imwrite(str(USER / "generals" / f"{new}.png"), img):
        raise ValueError("Could not write the picture.")
    entry = get_general_meta(old)
    delete_general(old)
    meta = load_general_meta()
    if entry:
        meta[new] = entry
    _save_general_meta(meta)
    _unhide_general(new)
    with _cache_lock:
        _gen_cache.clear()
    list_generals(refresh=True)
    return new


def selected_tab(screen):
    """Which of the 5 tabs on the 'Select a General' screen is selected (1-5), read from its gold border."""
    r = screen[86].astype(int)
    xs = np.where((r[:, 2] > 150) & (r[:, 1] > 110) & (r[:, 0] < 110))[0]
    if len(xs) < 40:
        return None
    return int(min(max(round((xs.min() - 16) / 88) + 1, 1), 5))


def tab_centres(selected):
    """x centre of every tab when tab `selected` is the wide one."""
    x, out = 16, []
    for i in range(1, 6):
        w = 158 if i == selected else 82
        out.append(x + w // 2)
        x += w + 6
    return out


def favourite_on(screen):
    """True/False for the heart filter, or None if templates/fav_off.png is missing."""
    if not has_template("fav_off"):
        return None
    return find(screen, "fav_off", 0.85, region=(150, 140, 80, 60)) is None


GEN_TAB_NAMES = ["All", "Military", "Development", "Duty", "Subordinate City"]
VIEW_LIST_X = {"toggle": 133, "heart": 189, "idle": 244}          # the buttons under the tabs while the list is in LIST view
VIEW_GRID_X = {"toggle": 160, "heart": 278, "idle": 395}          # ... and in GRID view (they get text labels)


def tab_row(screen):
    """On a Generals / 'Select a General' screen: (selected tab 1-5, y of the tab row), or None. The four tabs that
    are not selected show a small picture (helmets, swords, hammer, crown, tower); the one missing is the selected tab."""
    band = screen[40:340]
    found, ys = {}, []
    for i in range(1, 6):
        t = tpl(f"gtab{i}")
        if t is None:
            return None
        res = cv2.matchTemplate(band, t, cv2.TM_CCOEFF_NORMED)
        _, sc, _, loc = cv2.minMaxLoc(res)
        if sc >= 0.72:
            found[i] = loc[0] + t.shape[1] // 2
            ys.append(loc[1] + t.shape[0] // 2 + 40)
    if len(found) != 4 or max(ys) - min(ys) > 10:
        return None
    return [i for i in range(1, 6) if i not in found][0], int(sorted(ys)[len(ys) // 2])


def view_icon(screen, ty=None):
    """-> ('grid' | 'list', x, y) from the little view button under the tabs, or None."""
    top = 40 if ty is None else max(ty + 15, 40)
    band = screen[top:top + (300 if ty is None else 90)]
    best = None
    for kind in ("grid", "list"):
        t = tpl(f"gview_{kind}")
        if t is None:
            continue
        _, sc, _, loc = cv2.minMaxLoc(cv2.matchTemplate(band, t, cv2.TM_CCOEFF_NORMED))
        if sc >= 0.7 and (best is None or sc > best[0]):
            best = (sc, kind, loc[0] + t.shape[1] // 2, loc[1] + t.shape[0] // 2 + top)
    return best[1:] if best else None


def is_grid_view(screen):
    """True while the general list shows portraits in a grid (thumbnail view)."""
    v = view_icon(screen)
    if v:
        return v[0] == "grid"
    try:
        return len(capture_cards(screen)) >= 3
    except Exception:
        return False


def set_list_view(dev, want, log, stop):
    """Switch the general list to 'grid' (thumbnails, fast) or 'list' (detail rows). True when it ended up as wanted."""
    for attempt in range(3):
        shot = dev.screenshot()
        v = view_icon(shot)
        grid = (v[0] == "grid") if v else is_grid_view(shot)
        if grid == (want == "grid"):
            return True
        if v is None:
            return False
        dev.tap(v[1], v[2])
        log(f"[*] Switching the general list to {want} view")
        _nap(stop, 1.6, 2.2)
    v = view_icon(dev.screenshot())
    return bool(v) and (v[0] == "grid") == (want == "grid")


def select_gen_tab(dev, want, log, stop):
    """Tap tab 1-5 (All, Military, Development, Duty, Subordinate City). True when it is selected."""
    for _ in range(4):
        shot = dev.screenshot()
        tr = tab_row(shot)
        if tr is None:
            log("[!] Could not read which general tab is selected")
            return False
        if tr[0] == want:
            return True
        dev.tap(tab_centres(tr[0])[want - 1], tr[1])
        _nap(stop, 1.2, 1.8)
    return False


def find_close_x(img, rmin=14, rmax=28):
    """Round close button (pale X on a dark disc) of a pop-up -> list of (x, y, score), best first."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gb = cv2.medianBlur(g, 5)
    cs = cv2.HoughCircles(gb, cv2.HOUGH_GRADIENT, dp=1.2, minDist=20, param1=90, param2=22, minRadius=rmin, maxRadius=rmax)
    out = []
    if cs is None:
        return out
    H, W = g.shape
    for x, y, r in cs[0]:
        x, y, r = int(x), int(y), int(r)
        if x - r < 0 or y - r < 0 or x + r >= W or y + r >= H:
            continue
        patch = g[y - r:y + r + 1, x - r:x + r + 1].astype(np.float32)
        yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
        d = np.hypot(xx, yy)
        inner = d <= r * 0.62
        if inner.sum() < 20:
            continue
        diag = inner & ((np.abs(xx - yy) <= max(1.5, r * 0.11)) | (np.abs(xx + yy) <= max(1.5, r * 0.11)))
        axes = inner & ((np.abs(xx) <= r * 0.12) | (np.abs(yy) <= r * 0.12))
        off = inner & ~diag & ~axes
        if diag.sum() < 8 or off.sum() < 8:
            continue
        md, mo = patch[diag].mean(), patch[off].mean()
        ring_dark = patch[(d > r * 0.7) & (d < r * 0.95)].mean()
        # a pale X on a darker disc: diagonals clearly brighter than the rest, and also than the axes
        contrast = md - mo
        axis_gap = md - (patch[axes].mean() if axes.sum() else mo)
        if contrast > 45 and axis_gap > 35 and md > 120 and mo < 110:
            out.append((x, y, float(contrast + axis_gap)))
    out.sort(key=lambda t: -t[2])
    return out


VIEW_TOGGLE = (497, 905)                 # round city <-> landscape button, bottom right


def cancel_quit_dialog(dev, shot, log):
    """The game's 'Are you sure you want to exit the game?' box (Back on the city opens it): tap Cancel. True if it was there."""
    try:
        if find_text(shot, ["exit the game?", "exit the game", "sure you want to exit"], region=(0, 380, 540, 260), cutoff=0.72) is None:
            return False
        c = find_text(shot, ["Cancel"], region=(0, 480, 540, 200), cutoff=0.7)
        x, y = c if c else (177, 567)
        dev.tap(int(x), int(y))
        log("[*] The game asked to exit - tapped Cancel")
        time.sleep(1.0)
        return True
    except Exception:
        return False


def _green_button_word(shot, word):
    """A green game button (white text on green) in the lower half whose text contains `word` -> its centre or None.
    Plain OCR often misses white-on-green text, so each wide green block is read on its own, inverted."""
    h, w = shot.shape[:2]
    y0 = int(h * 0.55)
    hsv = cv2.cvtColor(shot[y0:int(h * 0.92)], cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (40, 90, 70), (85, 255, 255))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 9), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(m)
    for i in sorted(range(1, n), key=lambda k: -stats[k][4]):
        x, y, bw, bh, area = stats[i]
        if bw < 100 or bh < 25 or bw > w * 0.9 or bw < bh * 2.2:
            continue
        crop = cv2.cvtColor(shot[y0 + y:y0 + y + bh, x:x + bw], cv2.COLOR_BGR2GRAY)
        crop = cv2.resize(crop, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
        for t in (170, 140, 200):
            b = 255 - cv2.threshold(crop, t, 255, cv2.THRESH_BINARY)[1]
            try:
                txt = pytesseract.image_to_string(b, config="--psm 7").lower()
            except Exception:
                return None
            if word in txt.replace(" ", ""):
                return (int(x + bw / 2), int(y0 + y + bh / 2))
    return None


_CHECKIN_TAPS = {}                                         # (serial, day) -> Check-in taps; at most 2 a day, so a misread never loops


def daily_checkin(dev, shot, log, stop):
    """The game's Daily Rewards window (Sign-in Rewards + Advanced Daily Rewards, green "Check-in" button): tap Check-in
    to claim today's reward. Only when both the window title and the button are read. -> True when it tapped."""
    key = (getattr(dev, "serial", ""), time.strftime("%Y-%m-%d"))
    if _CHECKIN_TAPS.get(key, 0) >= 2:
        return False
    h, w = shot.shape[:2]
    p = find_text(shot, ["Check-in", "Check in"], region=(0, int(h * 0.55), w, int(h * 0.35))) or _green_button_word(shot, "check")
    if not p or not find_text(shot, ["Daily Rewards", "Sign-in Rewards", "Advanced Daily Rewards"], region=(0, int(h * 0.12), w, int(h * 0.50))):
        return False
    _CHECKIN_TAPS[key] = _CHECKIN_TAPS.get(key, 0) + 1
    dev.tap(*p)
    log("[+] Daily Rewards: tapped Check-in (today's sign-in reward)")
    _nap(stop, 1.5, 2.2)
    return True


CITY_POPUP_WORDS = ["Event starts in", "Truce Agreement", "Server War is about to begin"]   # event pop-ups that sit over the city (the city is still recognised behind them)


DANGER_PATTERNS = [                         # account / money dialogs: the bot never confirms these - it closes them with Cancel / X / Back
    r"switch\s*account", r"current\s*progress", r"delete\s*(your|the)?\s*(account|progress|data)", r"log\s*out",
    r"(bind|link|unlink)\s*(your|this|to|with)?\s*(account|facebook|apple|google|email)", r"apple\s*id",
    r"(use|spend|cost)\w*\s*[\d,]+\s*gems?", r"confirm\s*(the)?\s*(purchase|payment)",
]


def safety_stop(dev, shot, cfg, log, stop):
    """A dangerous dialog (switch/link/delete account, log out, gem spend, payment) is on screen -> close it with Cancel /
    X / Back (never its confirm button), keep the picture. -> True if it was such a dialog."""
    try:
        txt = " ".join(t for t, _ in ocr_lines(shot[150:820, 20:520])).lower()
    except Exception:
        return False
    hit = next((p for p in DANGER_PATTERNS if re.search(p, txt)), None)
    if not hit:
        return False
    try:
        (USER / "debug").mkdir(exist_ok=True)
        cv2.imwrite(str(USER / "debug" / f"safety_stop_{dev.serial.replace(':', '_')}.png"), shot)
    except Exception:
        pass
    log(f"[!] SAFETY: an account / payment window is open ({hit}) - closing it with Cancel / X / Back, never the confirm button (picture saved)")
    # Owner 24 Oct: never stop, and do not restart the game for a pop-up either (Evony shows many) - just close it safely.
    # If it keeps coming back, the usual 'unknown screen 3 times in 10 min' rule restarts the game.
    try:
        cancel = find_text(shot, ["Cancel", "No", "Not now", "Later"], region=(20, 150, 500, 750), cutoff=0.85)
        if cancel:
            dev.tap(*cancel)
        else:
            x_btn = find(shot, "close_x", 0.8) if has_template("close_x") else None
            if x_btn:
                dev.tap(*x_btn)
            else:
                dev.back()                                   # Android Back = Cancel on Evony dialogs
        _nap(stop, 0.8, 1.2)
    except Exception as _e:
        quiet_err("safety_stop", _e)
    return True


_STUCK_POPUPS = {}                         # serial -> times clear_popups ended still lost (3 in 10 min -> game restart)
POPUP_LIB = USER / "popup_library"          # every unknown pop-up the bot meets: picture + what closed it (grows by itself)
_poplib_lock = threading.Lock()
_poplib_cache = {}


def _pop_hash(shot):
    """64-bit picture fingerprint of the middle of the screen (small changes like timers keep it nearly the same)."""
    try:
        g = cv2.resize(cv2.cvtColor(shot[100:860, 20:520], cv2.COLOR_BGR2GRAY), (9, 8), interpolation=cv2.INTER_AREA)
        return int("".join("1" if b else "0" for b in (g[:, 1:] > g[:, :-1]).flatten()), 2)
    except Exception:
        return None


def _poplib_load():
    if not _poplib_cache:
        try:
            _poplib_cache.update(json.loads((POPUP_LIB / "index.json").read_text(encoding="utf-8")))
        except Exception:
            pass
    return _poplib_cache


def _poplib_find(h):
    """The library entry for this pop-up (fingerprint within 8 bits), or (None, None)."""
    if h is None:
        return None, None
    for k, v in _poplib_load().items():
        if bin(int(k, 16) ^ h).count("1") <= 8:
            return k, v
    return None, None


def _poplib_save():
    try:
        POPUP_LIB.mkdir(parents=True, exist_ok=True)
        (POPUP_LIB / "index.json").write_text(json.dumps(_poplib_cache, indent=1), encoding="utf-8")
    except Exception:
        pass


def _poplib_seen(h, shot, log):
    """Save a pop-up the bot has not met before (picture + entry). -> its entry."""
    with _poplib_lock:
        k, v = _poplib_find(h)
        if v is None and h is not None:
            k = f"{h:016x}"
            v = {"fix": None, "xy": None, "seen": 0, "first": time.strftime("%Y-%m-%d %H:%M"), "file": f"popup_{k}.png"}
            if len(_poplib_cache) < 500:
                _poplib_cache[k] = v
                try:
                    POPUP_LIB.mkdir(parents=True, exist_ok=True)
                    cv2.imwrite(str(POPUP_LIB / v["file"]), shot)
                except Exception:
                    pass
                log(f"[i] New pop-up saved to the library ({v['file']})")
        if v is not None:
            v["seen"] = v.get("seen", 0) + 1
            v["last"] = time.strftime("%Y-%m-%d %H:%M")
            _poplib_save()
        return v


def _poplib_learn(pending, shot, log):
    """After a tap on an unknown pop-up: if the screen changed, remember that tap as the fix for that pop-up."""
    if not pending:
        return
    h0, how, xy = pending
    h1 = _pop_hash(shot)
    if h1 is None or bin(h0 ^ h1).count("1") <= 8:
        return                                              # still the same window: that tap did not close it
    with _poplib_lock:
        k, v = _poplib_find(h0)
        if v is not None and v.get("fix") != how:
            v["fix"], v["xy"] = how, [int(xy[0]), int(xy[1])]
            _poplib_save()
            log(f"[i] Learned how to close pop-up {k}: {how} at {tuple(v['xy'])}")


def popup_seen(dev, shot, how):
    """Keep the picture of every pop-up / page the bot closed WITHOUT knowing it (by X, by a tap outside, by Back): the
    library in debug\\popups grows by itself and recurring ones become popup_* pictures (owner 24 Oct). Newest 300 kept."""
    try:
        d = USER / "debug" / "popups"
        d.mkdir(parents=True, exist_ok=True)
        serial = re.sub(r"\W", "_", str(getattr(dev, "serial", "")))
        cv2.imwrite(str(d / f"{time.strftime('%Y%m%d_%H%M%S')}_{serial}_{how}.png"), shot)
        old = sorted(d.glob("*.png"), key=lambda q: q.stat().st_mtime)
        for q in old[:-300]:
            q.unlink()
    except Exception as _e:
        quiet_err("popup_seen", _e)


def clear_popups(dev, cfg, log, stop, rounds=5, want_city=True):
    """Close whatever covers the game (offers, event pop-ups, full-screen sales, dialogs) so the bot can carry on with
    what it was doing. Uses your popup_* pictures first; on a screen it does not know it presses Back, but only when it
    can recognise the city (Back on the city would open the quit dialog). -> number of things closed."""
    th = min(cfg["threshold"], 0.85)
    closed = 0
    outside_done = False
    x_spot_done = False
    back_arrow_taps = 0
    pending = None                                         # (fingerprint, how, xy) of the last tap on an unknown pop-up
    known_done = set()
    for _ in range(rounds):
        if stop.is_set():
            break
        shot = dev.screenshot()
        _poplib_learn(pending, shot, log)
        pending = None
        if screen_state(shot, cfg) == "unknown" and safety_stop(dev, shot, cfg, log, stop):
            return closed
        if cancel_quit_dialog(dev, shot, log):
            closed += 1
            continue
        if find_text(shot, ["new game updates", "restart the game"], region=(20, 300, 500, 300), cutoff=0.75):
            rs = find_text(shot, ["Restart"], region=(60, 380, 420, 320), cutoff=0.8) or find_text(shot, ["Restart"], cutoff=0.8)
            if not rs:
                log("[!] Game update window - the Restart button could not be read")
                return closed
            dev.tap(*rs)                                    # the game's 'new updates, please restart' window: only Restart closes it
            LOADING[dev.serial] = ("restart", time.time())
            log("[*] Game update window - tapped Restart, the game reloads")
            _nap(stop, 8, 10)
            return closed + 1
        hit = None
        for name in list_templates("popup_"):
            if name == "popup_exit_cancel":
                continue
            h = find(shot, name, th)
            if h:
                hit = (name, h)
                break
        if hit:
            tap_hit(dev, hit[1])
            log(f"[*] Closed a pop-up ({hit[0]})")
            closed += 1
            _nap(stop, 0.8, 1.3)
            continue
        if tab_row(shot) is not None:                      # a Generals screen: nothing in the way
            break
        st = screen_state(shot, cfg)
        if st in ("city", "unknown") and find_text(shot, CITY_POPUP_WORDS, region=(30, 90, 480, 720), cutoff=0.8):
            cx = find_close_x(shot[90:810, 30:510])        # an event pop-up over the city (e.g. Server War): its X if it has one, else tap outside it
            if cx:
                dev.tap(cx[0][0] + 30, cx[0][1] + 90)
                popup_seen(dev, shot, "x")
                log("[*] Closed an event pop-up over the city with its X button")
            else:
                ox, oy = cfg.get("popup_outside_tap") or (8, 480)
                dev.tap(int(ox), int(oy))
                popup_seen(dev, shot, "outside")
                log(f"[*] Event pop-up over the city has no X - tapped outside it ({ox},{oy})")
            closed += 1
            _nap(stop, 1.0, 1.5)
            continue
        if st != "unknown" or not has_template("city_screen"):
            break
        if daily_checkin(dev, shot, log, stop):            # the game's Daily Rewards window: claim today's sign-in reward first
            closed += 1
            continue
        if tap_alliance_war_row(dev, shot, log):          # the Alliance page is not a pop-up: go on into Alliance War
            _nap(stop, 1.5, 2.2)
            break
        if world_map(shot):                                # the world map: go to the city only when the caller needs it
            if not want_city:
                log("[*] On the world map - working from here")
                break
            dev.tap(*VIEW_TOGGLE)
            log("[*] On the world map - tapped the globe to go to the city")
            closed += 1
            _nap(stop, 3.0, 4.0)
            continue
        try:                                               # a full page (Alliance Science, event, ...) has the gold Back arrow top left: it is not a pop-up
            _hsv = cv2.cvtColor(shot[8:80, 5:75], cv2.COLOR_BGR2HSV)
            _gold = cv2.countNonZero(cv2.inRange(_hsv, (12, 110, 150), (32, 255, 255)))
            _bar = cv2.cvtColor(shot[8:58, 120:420], cv2.COLOR_BGR2HSV)   # a full page has a red title bar; the world map / city do not
            _red = float((((_bar[:, :, 0] < 10) | (_bar[:, :, 0] > 170)) & (_bar[:, :, 1] > 80) & (_bar[:, :, 2] > 40)).mean())
            if _red < 0.5:                                 # gold in the corner is the player's portrait (world map): use the globe switch instead
                _gold = 0
        except Exception:
            _gold = 0
        full_page = _gold > 250
        if _gold > 250 and back_arrow_taps < 6:            # a full page, not a pop-up: its Back arrow
            back_arrow_taps += 1
            dev.tap(*cfg.get("city_to_tap", (35, 35)))
            popup_seen(dev, shot, "back")
            log("[*] A full page is open (not a pop-up) - tapped the Back arrow")
            closed += 1
            _nap(stop, 1.2, 1.8)
            continue
        _ph = _pop_hash(shot)
        _pe = None if full_page else _poplib_seen(_ph, shot, log)        # every unknown pop-up goes into the library
        if _pe and _pe.get("fix") and _pe.get("xy") and _ph not in known_done:
            known_done.add(_ph)                            # met before: use the tap that closed it last time
            dev.tap(*_pe["xy"])
            pending = (_ph, _pe["fix"], _pe["xy"])
            log(f"[*] Known pop-up - closing it the way that worked before ({_pe['fix']} at {tuple(_pe['xy'])})")
            closed += 1
            _nap(stop, 1.0, 1.5)
            continue
        cx = find_close_x(shot)                            # unknown window, step 1: its round X button
        if cx:
            dev.tap(cx[0][0], cx[0][1])
            pending = (_ph, "x", cx[0][:2]) if _ph is not None else None
            popup_seen(dev, shot, "x")
            log(f"[*] Closed a pop-up with its X button ({cx[0][0]},{cx[0][1]})")
            closed += 1
            _nap(stop, 0.8, 1.3)
            continue
        if not outside_done and not full_page:             # step 2: no X - wait until it is steady, tap outside it
            outside_done = True
            prev = shot
            for _w in range(6):
                _nap(stop, 0.6, 0.8)
                cur = dev.screenshot()
                if float(np.mean(cv2.absdiff(prev[40:920:8, ::8], cur[40:920:8, ::8]))) < 1.5:
                    break
                prev = cur
            ox, oy = cfg.get("popup_outside_tap") or (8, 480)
            dev.tap(int(ox), int(oy))
            pending = (_ph, "outside", (ox, oy)) if _ph is not None else None
            popup_seen(dev, shot, "outside")
            log(f"[*] Unknown pop-up - no X button, tapped outside it ({ox},{oy})")
            closed += 1
            _nap(stop, 1.0, 1.5)
            continue
        if not x_spot_done and not full_page:              # tapping outside did not close it: try the usual X spot once
            x_spot_done = True
            ox, oy = cfg.get("popup_x_tap") or (477, 141)
            dev.tap(int(ox), int(oy))
            pending = (_ph, "x spot", (ox, oy)) if _ph is not None else None
            popup_seen(dev, shot, "xspot")
            log(f"[*] Unknown pop-up - tapping outside did not close it, tapped the usual X spot ({ox},{oy})")
            closed += 1
            _nap(stop, 1.0, 1.5)
            continue
        _now = time.time()
        _tg = [t for t in getattr(_TL, "toggles", []) if _now - t < 90]
        _TL.toggles = _tg
        if len(_tg) < 3 and (not _tg or _now - _tg[-1] > 5):      # the game may be on the landscape view: the switch flips city <-> landscape, so after a toggle that
            _tg.append(_now)                                       # lands on an unknown screen, flip it back before ever pressing Back
            _TL.toggle_at = _now
            dev.tap(*VIEW_TOGGLE)
            log("[*] Unknown screen - tapped the city/landscape switch (bottom right)")
            _nap(stop, 2.0, 3.0)
            closed += 1
            continue
        before = shot[40:920:8, ::8].copy()
        dev.back()
        closed += 1
        log("[*] Unknown screen / pop-up - pressed Back")
        _nap(stop, 0.9, 1.4)
        after = dev.screenshot()
        if float(np.mean(cv2.absdiff(before, after[40:920:8, ::8]))) < 2.0:      # Back did nothing: use the on-screen arrow
            dev.tap(36, 45)
            log("[*] Back did nothing - tapped the arrow at the top left")
            _nap(stop, 0.9, 1.3)
        if has_template("popup_exit_cancel"):
            again = dev.screenshot()
            c = find(again, "popup_exit_cancel", th)
            if c:
                tap_hit(dev, c)
                log("[*] Back opened the quit dialog - cancelled it")
                _nap(stop, 0.8, 1.2)
    try:                                                   # still lost after everything: count it; 3 times in 10 min -> restart the game
        _end = dev.screenshot()
        _lost = screen_state(_end, cfg) == "unknown" and not world_map(_end) and tab_row(_end) is None
    except Exception:
        _lost = False
    _now = time.time()
    _hist = [t for t in _STUCK_POPUPS.get(dev.serial, []) if _now - t < 600] + ([_now] if _lost else [])
    _STUCK_POPUPS[dev.serial] = [] if not _lost else _hist
    if len(_hist) >= 3 and not stop.is_set():
        _STUCK_POPUPS[dev.serial] = []
        log("[!] Could not get past an unknown screen 3 times in 10 min - restarting the game")
        try:
            (USER / "debug").mkdir(exist_ok=True)
            cv2.imwrite(str(USER / "debug" / "stuck_before_restart.png"), _end)
        except Exception:
            pass
        force_restart_game(dev, cfg, log, stop, must=True)
        closed += 1
    return closed


def open_generals_screen(dev, cfg, log, stop):
    """From the city: close any pop-up, tap the round 3-dots button, then 'Generals'. If a pop-up gets in the way,
    it is closed and the steps are repeated. True when the Generals screen is open."""
    for attempt in range(3):
        if stop.is_set():
            return False
        clear_popups(dev, cfg, log, stop)
        if _open_generals_once(dev, cfg, log, stop):
            return True
        if attempt < 2:
            log("[*] Did not get there - checking for a pop-up and trying again")
    return False


def _open_generals_once(dev, cfg, log, stop):
    """From the city: tap the round 3-dots button on the right, then 'Generals'. True when the Generals screen is open."""
    th = cfg["threshold"]
    if has_template("city_screen") and not leave_to_city(dev, cfg, stop):
        log("[!] Could not get back to the city screen first")
        return False
    shot = dev.screenshot()
    hit = find(shot, "menu_dots", min(th, 0.8)) if has_template("menu_dots") else None
    if hit:
        tap_hit(dev, hit)
    elif cfg.get("menu_dots_tap"):
        dev.tap(*cfg["menu_dots_tap"])
    else:
        log("[!] Cannot find the 3-dots menu button (templates/menu_dots.png is missing)")
        return False
    _nap(stop, 1.2, 1.6)
    for attempt in range(2):
        shot = dev.screenshot()
        hit = find(shot, "menu_generals", min(th, 0.8)) if has_template("menu_generals") else None
        if hit:
            tap_hit(dev, hit)
            break
        pos = find_text(shot, ["Generals"], cutoff=0.85)
        if pos:
            dev.tap(*pos)
            break
        _nap(stop, 0.8, 1.0)
    else:
        log("[!] Could not find 'Generals' in the menu")
        return False
    for _ in range(10):
        _nap(stop, 0.6, 0.9)
        if tab_row(dev.screenshot()):
            return True
    log("[!] The Generals screen did not open")
    return False


def close_generals_screen(dev, cfg, log, stop):
    for _ in range(4):
        if has_template("city_screen") and screen_state(dev.screenshot(), cfg) == "city":
            return
        dev.back()
        _nap(stop, 0.8, 1.2)



def find_general_text(screen, name, idle_only=False):
    """LIST view: find a general by reading the names on screen (the rows show 'Lv36 Petronas ( ... )').
    -> a hit like find_general() gives, or None. _TL.busy_seen is set when it was found but is not Idle."""
    import difflib
    _TL.busy_seen = False
    want = re.sub(r"[^a-z ]", "", name.lower()).strip()
    top = 300
    lines = ocr_lines(screen[top:LIST_BOTTOM, 215:], scale=2.0)
    rows = sorted(((sum(w[2] for w in ws) / len(ws) + top, text) for text, ws in lines), key=lambda r: r[0])
    for i, (y, text) in enumerate(rows):
        m = re.search(r"L[vy]\s*\d+\s+(.+)", text)
        if not m:
            continue
        cand = re.split(r"[(\[{]", m.group(1))[0]
        cand = re.sub(r"[^a-z ]", "", cand.lower()).strip()
        if not cand or difflib.SequenceMatcher(None, cand, want).ratio() < 0.78:
            continue
        near = " ".join(t for yy, t in rows[i:i + 3] if abs(yy - y) < 70).lower()
        if idle_only and "idle" not in near:
            _TL.busy_seen = True
            continue
        return (110, int(y) + 100, 1.0, (60, 60))
    return None


def choose_general(dev, name, cfg, log, stop, role="main"):
    """On the 'Select a General' screen: set tab/favourites, find the portrait, pick the general."""
    nap = lambda lo, hi=None: _nap(stop, lo, hi)
    th = cfg["threshold"]
    if has_template("general_screen"):
        for _ in range(10):
            if find(dev.screenshot(), "general_screen", th):
                break
            nap(0.5)
        else:
            log("[!] The 'Select a General' screen did not open")
            return False
    screen = dev.screenshot()
    view = "list" if cfg.get("general_view") == "list" else "grid"
    if view == "grid" and general_image(name) is None:     # no picture of this general on this PC: find it by its name
        view = "list"
        log(f"[i] No picture of '{name}' yet - looking for it by name (list view)")
    elif view == "grid" and _portrait_card_art(name):      # only the full-body Tavern portrait: it does not look like the list's thumbnail
        view = "list"
        log(f"[i] '{name}' has only a Tavern portrait picture - looking for it by name (list view)")
    tr = tab_row(screen)
    if tr is not None:                                   # both screens: the buttons are found by their pictures
        if not set_list_view(dev, view, log, stop):
            log(f"[i] Could not switch the general list to {view} view - carrying on")
        screen = dev.screenshot()
    elif view == "grid" and has_template("select_button") and find(screen, "select_button", th):      # detail view -> grid view
        dev.tap(133, 169)
        log("[*] Switching the general list to grid view")
        nap(2.0, 2.5)
        screen = dev.screenshot()

    gm = get_general_meta(name)                      # settings saved for this general in the manager
    want = gm["tab"] if "tab" in gm else cfg.get(f"general_{role}_tab")
    want = int(cfg["general_tab"] if want in (None, "") else want)
    if want == 0 and "tab" not in gm:
        want = catalog_tab(name)                     # the catalogue knows which tab this general lives in
    if 1 <= want <= 5 and tr is not None:
        if not select_gen_tab(dev, want, log, stop):
            log(f"[!] Could not switch to general tab {want}")
        screen = dev.screenshot()
    elif 1 <= want <= 5:
        for _ in range(3):
            cur = selected_tab(screen)
            if cur is None:
                log("[!] Could not read which general tab is selected")
                break
            if cur == want:
                break
            dev.tap(tab_centres(cur)[want - 1], 104)
            nap(1.2, 1.8)
            screen = dev.screenshot()
        else:
            log(f"[!] Could not switch to general tab {want}")

    fav = gm["fav"] if "fav" in gm else cfg.get(f"general_{role}_fav")
    fav = cfg["general_favourites"] if fav in (None, "") else fav
    if fav in ("on", "off"):
        tr2 = tab_row(screen)
        reg = (100, max(tr2[1] + 10, 0), 440, 90) if tr2 else (150, 140, 80, 60)
        if not has_template("fav_off"):
            log("[i] templates/fav_off.png is missing - cannot read the favourites filter")
        else:
            hit = find(screen, "fav_off", 0.85, region=reg)
            vi = view_icon(screen, tr2[1] if tr2 else None)
            heart = (hit[0], hit[1]) if hit else ((vi[1] + 56, vi[2]) if vi else (189, 169))
            if (hit is None) != (fav == "on"):
                dev.tap(*heart)
                nap(1.2, 1.8)
                screen = dev.screenshot()
                if (find(screen, "fav_off", 0.85, region=reg) is None) != (fav == "on"):
                    log("[!] The favourites filter did not change")

    def same(a, b):
        return float(np.mean(cv2.absdiff(a[200:900], b[200:900]))) < 1.5

    TAB_NAMES = {0: "the open tab", 1: "All", 2: "Military", 3: "Development", 4: "Duty", 5: "Subordinate City"}

    def scan(tab_now):
        """Scroll to the top of the list, then down, looking for the general. -> (hit, busy, best)"""
        prev = None
        for _ in range(8):                                   # go to the top of the list
            shot = dev.screenshot()
            if prev is not None and same(prev, shot):
                break
            prev = shot
            dev.swipe(270, 330, 270, 820, 500)
            nap(0.7, 1.0)
        hit, prev, busy, best_all = None, None, False, 0.0
        log(f"[i] Looking for {role} general '{name}': {TAB_NAMES.get(tab_now, tab_now)} tab, favourites {fav or 'leave'}, {view} view")
        for _ in range(int(cfg["general_max_scrolls"]) * (3 if view == "list" else 1) + 1):  # scan downwards
            shot = dev.screenshot()
            if view == "list":
                hit = find_general_text(shot, name, idle_only=cfg.get("general_idle_only", True))
            else:
                hit = find_general(shot, name, copy=gm.get("copy", "any"), idle_only=cfg.get("general_idle_only", True))
            busy = busy or getattr(_TL, "busy_seen", False)
            best_all = max(best_all, getattr(_TL, "best", 0.0))
            if hit or (prev is not None and same(prev, shot)):
                break
            prev = shot
            if view == "list":
                dev.swipe(270, 800, 270, 500, 700)
            else:
                dev.swipe(270, 820, 270, 330, 700)
            nap(0.8, 1.2)
        return hit, busy, best_all

    tab_now = want
    if not 1 <= want <= 5:                               # 'Leave as is': the game shows the tab it had open last (often Development)
        try:
            _tr = tab_row(dev.screenshot())
            tab_now = _tr[0] if _tr is not None else (selected_tab(dev.screenshot()) or 0)
        except Exception as _e:
            quiet_err("choose_general.tab_now", _e)
            tab_now = 0
    hit, busy, best_all = scan(tab_now)
    if not hit and not busy and 2 <= tab_now <= 5 and not stop.is_set():
        # not in the chosen tab (a general can sit in another category than the one saved for it): the All tab lists every general
        log(f"[i] '{name}' is not in the {TAB_NAMES[tab_now]} tab - looking in All")
        switched = False
        if tab_row(dev.screenshot()) is not None:
            switched = select_gen_tab(dev, 1, log, stop)
        else:
            cur = selected_tab(dev.screenshot())
            if cur is not None and cur != 1:
                dev.tap(tab_centres(cur)[0], 104)
                nap(1.2, 1.8)
                switched = selected_tab(dev.screenshot()) == 1
        if switched:
            hit, busy2, best2 = scan(1)
            busy, best_all = busy or busy2, max(best_all, best2)
            if hit:
                log(f"[i] Found '{name}' in the All tab" + (f" - its saved tab ({TAB_NAMES[want]}) may be wrong; check it in Manage generals" if want == tab_now else ""))
    if not hit:
        if busy:
            log(f"[!] {role.capitalize()} general '{name}' is busy (not Idle)")
        else:
            log(f"[!] {role.capitalize()} general '{name}' was not found in the list "
                f"(check its tab / favourites setting; best picture match {best_all:.2f} over the whole list)")
        try:
            save_debug("general_" + name, dev.screenshot())
        except Exception as _e:
            quiet_err("choose_general", _e)
        return False

    pressed = False
    if view == "list" and has_template("select_button"):  # list rows have their own Select button; the portrait opens the info page
        name_y = int(hit[1]) - 100                         # find_general_text puts the hit 100 px below the name line
        y0 = max(name_y - 90, 0)
        shot0 = dev.screenshot()
        cands = find_all(shot0, "select_button", 0.7, (0, y0, 540, 270))   # the whole row around the name, above and below
        sb = min(cands, key=lambda c: abs(c[1] - name_y)) if cands else None
        if not sb:
            try:                                           # keep the screen so the list-view Select button can be fixed
                save_debug("general_list_" + re.sub(r"\W", "_", name), shot0)
            except Exception as _e:
                quiet_err("choose_general", _e)
        if sb:
            tap_hit(dev, sb)
            pressed = True
    if not pressed:
        if view == "list":                                # never tap the portrait in list view: it opens the info page
            log(f"[!] Found {name} in the list but not its Select button - not tapping the portrait")
            return False
        tap_hit(dev, hit)
    nap(1.0, 1.5)
    shot = dev.screenshot()
    st_now = screen_state(shot, cfg)
    if st_now not in ("march", "general") and not (has_template("general_screen") and find(shot, "general_screen", th)) \
            and not (has_template("select_button") and find(shot, "select_button", th)):
        log(f"[!] Tapping {name} opened another page (the general's info page?) - pressing Back")
        dev.back()                                       # never stay stuck on the info page
        nap(1.0, 1.5)
        return False
    if has_template("select_button"):                    # tapping a card may open a detail panel
        sb = find(shot, "select_button", th)
        if sb:
            tap_hit(dev, sb)
            nap(1.0, 1.5)
            shot = dev.screenshot()
    if has_template("general_screen") and find(shot, "general_screen", th):
        log(f"[!] Still on the general list after tapping {name}")
        return False
    return True


def general_slot(dev, role, cfg):
    """Where to tap to open the general list for 'main' or 'assistant' (the assistant slot only
    appears after the main general is chosen, and the layout moves, so look for it every time)."""
    th = cfg["threshold"]
    scrolled = False
    if has_template("general_search"):
        for attempt in range(3):
            hits = find_all(dev.screenshot(), "general_search", 0.8, (380, 150, 160, 760))
            px = cfg.get("general_tap_x", 90)
            if role == "main" and hits:
                return px, hits[0][1]              # same row as the magnifier, but on the portrait
            if role == "assistant" and len(hits) >= 2:
                return px, hits[1][1]
            if role == "assistant" and len(hits) == 1 and hits[0][1] < 620:
                return px, hits[0][1] + 156                # an empty assistant slot has no magnifier: it sits one card below the main one
            if attempt == 1 and role == "assistant":           # maybe the assistant slot is just off screen
                dev.swipe(270, 700, 270, 520, 300)
                scrolled = True
            elif role == "main":                               # the main slot is at the top: never scroll for it
                break
            _nap(threading.Event(), 0.6, 0.9)
        if scrolled:                                           # put the march screen back to the top
            dev.swipe(270, 520, 270, 760, 300)
            _nap(threading.Event(), 0.5, 0.8)
    if role == "assistant" and has_template("assistant_card"):
        card = find(dev.screenshot(), "assistant_card", th)
        if card:
            return cfg.get("general_tap_x", 90), card[1]
    pos = cfg["general_main_tap"] if role == "main" else cfg["general_assistant_tap"]
    return tuple(pos) if pos else None


def general_categories():
    """{category: [general names]} from the categories you gave your generals in the General Manager."""
    out = {}
    for n, m in load_general_meta().items():
        if isinstance(m, dict) and m.get("cat") and not n.startswith("__"):
            out.setdefault(m["cat"], []).append(n)
    return {c: sorted(v, key=str.lower) for c, v in out.items()}


def general_choices(cfg, role):
    """The generals to try for 'main' / 'assistant', best first (a single name as text is accepted too).
    An entry '@Name' stands for every general in that category (in name order)."""
    v = cfg.get(f"general_{role}")
    if isinstance(v, str):
        v = [v]
    out, cats = [], None
    for x in (v or []):
        x = str(x).strip()
        if not x:
            continue
        if x.startswith("@"):
            cats = cats if cats is not None else general_categories()
            key = {c.lower(): c for c in cats}.get(x[1:].strip().lower())
            for n in (cats.get(key) or []):
                if n not in out:
                    out.append(n)
        elif x not in out:
            out.append(x)
    return out


def set_generals(dev, cfg, log, stop):
    """From the march screen: choose the main and then the assistant general. A general that is not in the list,
    not Idle or cannot be selected is skipped and the next one on the list is tried. -> True if everything worked."""
    taken = None
    for role in ("main", "assistant"):
        names = [n for n in general_choices(cfg, role) if n != taken]      # one general cannot fill both slots
        if not names:
            continue
        on_list, done = False, None
        have = slot_matches(dev.screenshot(), role, names, cfg)
        if have:                                           # the preset already holds a listed general: just keep it
            log(f"[+] {role.capitalize()} general is already {have} - no search needed")
            if role == "main":
                taken = have
            continue
        for i, name in enumerate(names):
            if name not in list_generals() and not is_known_general(name):
                log(f"[!] '{name}' is not a general the bot knows and has no picture - skipped")
                continue
            if not on_list:
                pos = general_slot(dev, role, cfg)
                if not pos:
                    if role == "assistant" and cfg.get("general_assistant_optional"):
                        log("[!] Assistant slot not found - joining without an assistant general")
                        done = "(none)"
                        break
                    log(f"[!] {role.capitalize()} general slot not found (add templates/general_search.png "
                        f"or set the tap position)")
                    return False
                dev.tap(*pos)
                _nap(stop, 1.0, 1.5)
            if choose_general(dev, name, cfg, log, stop, role):
                done = name
                break
            on_list = (screen_state(dev.screenshot(), cfg) == "general") if has_template("general_screen") else True
            later = [n for n in names[i + 1:] if n in list_generals()]
            if later:
                log(f"[*] Trying the next {role} general: {later[0]}")
        if done is None and role == "assistant" and cfg.get("general_assistant_optional"):
            log(f"[*] No assistant general available ({', '.join(names)}) - joining with the main general only")
            if has_template("general_screen") and screen_state(dev.screenshot(), cfg) == "general":
                dev.back()                                     # close the general list, back to the march screen
                _nap(stop, 0.8, 1.2)
            continue
        if done is None:
            log(f"[!] None of the {role} generals could be selected ({', '.join(names)})")
            return False
        if done == "(none)":
            continue
        if has_template("march_screen"):                       # wait until we are back on the march screen
            for _ in range(8):
                if screen_state(dev.screenshot(), cfg) == "march":
                    break
                _nap(stop, 0.4, 0.6)
        log(f"[+] {role.capitalize()} general set to {done}")
        if role == "main":
            taken = done
        _nap(stop, 0.8, 1.2)                                   # the assistant slot appears now
    return True


# ---------------------------------------------------------------- general catalogue (Tavern > Portraits)
PORT_XS, PORT_PITCH, PORT_W, PORT_H = (20, 191, 362), 260, 163, 248     # cards of the Portraits screen (540 wide)
PORT_ART = (50, 62, 100, 102)                                          # x, y, w, h of the picture kept inside a card
PORT_TROOPS = ["All", "Ground Troop", "Mounted Troop", "Ranged Troop", "Siege Machine", "Defense", "Mixed",
               "Administration", "Duty", "Subordinate City"]


def black_top(img):
    """Rows of black at the top of a screenshot (phones with a notch); layouts below are measured from there."""
    rows = np.where(img.mean(axis=(1, 2)) > 12)[0]
    return int(rows[0]) if len(rows) else 0


def portrait_tops(img, y_min):
    """y of the top of every fully visible card row of the Portraits screen (the rows are 260 px apart)."""
    H = img.shape[0]
    v = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[..., 2]
    dark = (v[:, 25:510] < 60).mean(axis=1)
    acc = np.zeros(PORT_PITCH)
    for y in range(y_min, H - 80):
        acc[y % PORT_PITCH] += dark[y]
    k = 11
    sm = np.convolve(np.r_[acc[-k:], acc, acc[:k]], np.ones(k) / k, mode="same")[k:-k]
    gap = float(np.argmax(sm))
    return [int(round(gap + n * PORT_PITCH + 6)) for n in range(-1, 7)
            if y_min - 20 < gap + n * PORT_PITCH + 6 and gap + n * PORT_PITCH + 6 + PORT_H <= H - 70]


def read_portrait_name(img, x, top):
    c = img[top + 204:top + 242, x + 18:x + 146]            # the inside of the name plate (cream text on red/purple)
    if c.size == 0:
        return ""
    g = cv2.resize(cv2.cvtColor(c, cv2.COLOR_BGR2GRAY), None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    b = 255 - cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    b = cv2.copyMakeBorder(b, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    try:
        txt = pytesseract.image_to_string(b, config="--psm 6")
    except Exception:
        return ""
    txt = txt.replace("|", "I").replace("\n", " ")
    return " ".join(re.sub(r"[^A-Za-z.'\- ]", " ", txt).split()).strip(" .-")


def read_portrait_name2(img, x, top):
    """A second, differently prepared reading of the same name plate (single line, other scale / threshold)."""
    c = img[top + 204:top + 242, x + 18:x + 146]
    if c.size == 0:
        return ""
    g = cv2.resize(cv2.cvtColor(c, cv2.COLOR_BGR2GRAY), None, fx=3, fy=3, interpolation=cv2.INTER_LANCZOS4)
    b = 255 - cv2.threshold(cv2.GaussianBlur(g, (3, 3), 0), 150, 255, cv2.THRESH_BINARY)[1]
    b = cv2.copyMakeBorder(b, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    try:
        txt = pytesseract.image_to_string(b, config="--psm 7")
    except Exception:
        return ""
    return " ".join(re.sub(r"[^A-Za-z.'\- ]", " ", txt.replace("|", "I")).split()).strip(" .-")


_NAME_CONNECT = {"the", "of", "de", "di", "da", "del", "der", "den", "von", "van", "ibn", "bin", "al", "el", "la", "le", "du", "des", "y", "and", "d"}
_ROMAN = {"I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"}


def plausible_general_name(name):
    """Looks like a general's name (words that start with a capital, connectors like 'the' / 'of', roman numerals) and not
    like OCR junk. Used for generals that are not in the built-in list yet (the point of the library crawl)."""
    words = str(name).split()
    if not 1 <= len(words) <= 5 or len(re.sub(r"[^A-Za-z]", "", name)) < 4:
        return False
    for w in words:
        core = w.strip(".'-")
        if not core:
            return False
        if w.lower() in _NAME_CONNECT or core in _ROMAN:
            continue
        if not core[0].isupper() or len(core) < 3 or core.isupper() and len(core) > 4 or core[1:].lower() != core[1:] and not core[1:].isalpha():
            return False
        if re.search(r"[bcdfghjklmnpqrstvwxz]{5,}", core.lower()):
            return False
    return True


def _words_to_labels(lines, gap=22):
    """OCR words of one strip -> [(label, x centre)] (words closer than `gap` pixels form one label)."""
    ws = sorted((w for _, row in lines for w in row), key=lambda w: w[1])
    out = []
    for w in ws:
        if out and w[1] - w[3] / 2 - out[-1][2] < gap:
            out[-1][0].append(w[0])
            out[-1][2] = w[1] + w[3] / 2
            out[-1][1] = (out[-1][1] + w[1]) / 2
        else:
            out.append([[w[0]], w[1], w[1] + w[3] / 2])
    return [(" ".join(o[0]), int(o[1])) for o in out]


def crawl_portraits(dev, cfg, log, stop, progress=None):
    """Walk the whole Tavern > Portraits screen: every region tab x every troop-type filter, scrolling through the grid,
    and record each general (name, picture, regions, troop types). Open 'Recruit General' (Tavern) or 'Portraits' first.
    -> {name: {'name', 'region': [..], 'troops': [..], 'image'}}"""
    import difflib
    nap = lambda lo, hi=None: _nap(stop, lo, hi)
    shot = dev.screenshot()
    off = black_top(shot)
    H = shot.shape[0]
    if find_text(shot, ["Recruit General"], region=(60, off, 420, 100)):
        pos = find_text(shot, ["Portraits"], region=(250, H - 120, 290, 120))
        if not pos:
            raise ValueError("I see 'Recruit General' but not the Portraits button.")
        dev.tap(*pos)
        nap(1.5, 2.0)
        shot = dev.screenshot()
    if not find_text(shot, ["Portraits"], region=(120, off, 300, 100)):
        raise ValueError("Open the Tavern (Recruit General) or its Portraits screen first, then start again.")
    ty = off + 108                                        # y of the region tabs
    dy = off + 170                                        # y of the troop-type drop-down
    found = {}
    names_seen = set()

    def drain_screen(region, troop):
        img = dev.screenshot()
        for _ in range(6):                                # wait until the grid has stopped moving
            nap(0.35)
            again = dev.screenshot()
            if float(np.mean(cv2.absdiff(img[off + 200:H - 80], again[off + 200:H - 80]))) < 1.0:
                break
            img = again
        n_new = 0
        for top in portrait_tops(img, off + 200):
            for x in PORT_XS:
                crop = img[top + 8:top + 200, x + 8:x + PORT_W - 8]
                if crop.std() < 20:
                    continue
                ax, ay, aw, ah = PORT_ART
                art = img[top + ay:top + ay + ah, x + ax:x + ax + aw].copy()
                name = next((n for n, g in found.items() if same_art(g["image"], art, 0.8)), "")   # same picture = same general
                if not name:
                    name = snap_name(read_portrait_name(img, x, top), [])
                    if not is_known_general(name):         # not in the built-in list: a new general, or a misread card
                        second = snap_name(read_portrait_name2(img, x, top), [])
                        key = lambda t: re.sub(r"[^a-z0-9]", "", str(t).lower())
                        if is_known_general(second):
                            name = second
                        elif not (plausible_general_name(name) and key(name) == key(second)):      # accept a new name only when two different readings agree
                            log(f"[!] Skipped a card that could not be read twice the same ({region}/{troop}): '{name}' / '{second}'")
                            continue
                        log(f"[+] New general found: {name}")
                    base, k = name, 2
                    while name in found:                  # same name, different picture (e.g. two skins)
                        name, k = f"{base} {k}", k + 1
                g = found.get(name)
                if g is None:
                    label = img[top + 196:top + 246, x + 6:x + 157].copy()
                    g = found[name] = {"name": name, "region": [], "troops": [], "image": art, "label": label}
                    n_new += 1
                if region and region not in g["region"]:
                    g["region"].append(region)
                if troop and troop != "All" and troop not in g["troops"]:
                    g["troops"].append(troop)
        return n_new

    def scroll_grid(region, troop):
        prev = None
        for _ in range(60):                               # to the top
            cur = dev.screenshot()
            if prev is not None and float(np.mean(cv2.absdiff(prev[off + 200:H - 80], cur[off + 200:H - 80]))) < 1.5:
                break
            prev = cur
            dev.swipe(270, off + 300, 270, H - 150, 400)
            nap(0.5, 0.8)
        prev = None
        for _ in range(80):
            if stop.is_set():
                return
            drain_screen(region, troop)
            if progress:
                progress(len(found))
            cur = dev.screenshot()
            if prev is not None and float(np.mean(cv2.absdiff(prev[off + 200:H - 80], cur[off + 200:H - 80]))) < 1.5:
                return
            prev = cur
            dev.swipe(270, H - 160, 270, H - 160 - 480, 700)
            nap(0.9, 1.3)

    def pick_troop(label):
        """Open the drop-down and choose `label`. True when done."""
        dev.tap(402, dy)
        nap(0.9, 1.2)
        img = dev.screenshot()
        opts = ocr_lines(img[off + 190:off + 770, 240:540])
        want = label.lower()
        for text, ws in opts:
            if difflib.SequenceMatcher(None, text.lower().strip(), want).ratio() >= 0.8:
                cy = int(sum(w[2] for w in ws) / len(ws)) + off + 190
                dev.tap(380, cy)
                nap(1.0, 1.4)
                return True
        i = PORT_TROOPS.index(label)                      # not read: use the fixed positions
        dev.tap(380, off + 216 + int(i * 54.7))
        nap(1.0, 1.4)
        return True

    # the four region tabs of the Portraits screen (the selected tab cannot be read by OCR, so use fixed positions)
    for label, x in (("Europe", 64), ("America", 192), ("Japan", 316), ("Korea", 444)):
        if stop.is_set():
            break
        log(f"[*] Region: {label}")
        dev.tap(x, ty)
        nap(1.2, 1.6)
        for troop in PORT_TROOPS:
            if stop.is_set():
                break
            pick_troop(troop)
            log(f"[*]   {label} / {troop}: {len(found)} generals so far")
            scroll_grid(label, troop)
    return found


def merge_catalog(found, log=print):
    """Add the generals of a crawl that the library does not have yet to USER/catalog (catalog.json + art/ + labels/).
    Generals already in the library are left as they are. -> number added."""
    root = USER / "catalog"
    (root / "art").mkdir(parents=True, exist_ok=True)
    (root / "labels").mkdir(exist_ok=True)
    try:
        data = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    except Exception:
        data = {"generals": []}
    have = {g["name"]: g for g in data.get("generals", [])}
    added = 0
    for name, g in sorted(found.items(), key=lambda kv: kv[0].lower()):
        safe = _clean_general_name(name)
        old = have.get(safe)
        if old:                                            # known: only add the region / troop types it was seen under
            old["region"] = sorted(set(old.get("region", [])) | set(g["region"]))
            old["troops"] = sorted(set(old.get("troops", [])) | set(g["troops"]))
            if not (root / "art" / f"{safe}.png").exists():   # ... and its picture when the library had none
                cv2.imwrite(str(root / "art" / f"{safe}.png"), g["image"])
                old["art"] = f"{safe}.png"
                added += 1
            continue
        cv2.imwrite(str(root / "art" / f"{safe}.png"), g["image"])
        if g.get("label") is not None:
            cv2.imwrite(str(root / "labels" / f"{safe}.png"), g["label"])
        have[safe] = {"name": safe, "region": g["region"], "troops": g["troops"], "art": f"{safe}.png"}
        added += 1
    save_json(root / "catalog.json", {"built": time.strftime("%Y-%m-%d"), "generals": sorted(have.values(), key=lambda g: g["name"].lower())}, indent=1)
    log(f"[+] General library: {added} new general(s) added, {len(have)} in total ({root})")
    return added


def run_tavern_catalog(dev, cfg, log, stop):
    """The Tavern's ring menu is open: Recruit > Portraits, read every general (picture + name) and add the new ones to
    the general library. Nothing is recruited and the shop is not refreshed (the Refresh button is never touched)."""
    shot = dev.screenshot()
    if not find_text(shot, ["Recruit General"], region=(60, 0, 420, 120)):
        p = find_text(shot, ["Recruit"], region=(100, 120, 340, 230), cutoff=0.85)     # the ring sits a little differently on every view: find the word, the icon is just above it
        if p:
            dev.tap(p[0], p[1] - 38)
        else:
            dev.tap(295, 222)
        _nap(stop, 2.2, 3.0)
        shot = dev.screenshot()
        if not find_text(shot, ["Recruit General"], region=(60, 0, 420, 120)):
            raise RuntimeError("the Recruit General page did not open")
    found = crawl_portraits(dev, cfg, log, stop, progress=lambda n: None)
    if found:
        merge_catalog(found, log)
    else:
        log("[!]  General library: no generals were read")
    return len(found)


def save_catalog(found, log=print):
    """Write the crawl result to USER/catalog (catalog.json + art/<name>.png). Copy that folder next to the app to ship it."""
    root = USER / "catalog"
    import shutil
    for sub in ("art", "labels"):                          # start clean: an older, worse crawl must not linger
        shutil.rmtree(root / sub, ignore_errors=True)
    (root / "art").mkdir(parents=True, exist_ok=True)
    gens = []
    for name, g in sorted(found.items(), key=lambda kv: kv[0].lower()):
        safe = _clean_general_name(name)
        cv2.imwrite(str(root / "art" / f"{safe}.png"), g["image"])
        if g.get("label") is not None:
            (root / "labels").mkdir(exist_ok=True)
            cv2.imwrite(str(root / "labels" / f"{safe}.png"), g["label"])
        gens.append({"name": safe, "region": g["region"], "troops": g["troops"], "art": f"{safe}.png"})
    save_json(root / "catalog.json", {"built": time.strftime("%Y-%m-%d"), "generals": gens}, indent=1)
    log(f"[+] Saved {len(gens)} generals to {root}")
    return root


# ---------------------------------------------------------------- updates
APP_DIR = USER / "app"                  # downloaded versions live here: app/v1.2.0/rally_bot.py ...
_NAME_PY = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_\-]*\.py$")
_NAME_UI = re.compile(r"^ui/[A-Za-z0-9_][A-Za-z0-9_\-]*\.(js|css|html)$")
_LAST_MANIFEST = [None]
_NAME_MON = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_\-# ()]*\.png$")
_NAME_PNG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_\- ]*\.png$")


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:4]) or (0,)


def _canonical(manifest):
    return json.dumps({k: v for k, v in manifest.items() if k != "sig"}, sort_keys=True, separators=(",", ":"))


def sign_manifest(manifest, key):
    return hmac.new(key.encode("utf-8"), _canonical(manifest).encode("utf-8"), hashlib.sha256).hexdigest()


def _safe_url(url):
    import urllib.parse
    u = urllib.parse.urlparse(url)
    if u.scheme == "https" or (u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1")):
        return url
    raise ValueError("Update addresses have to start with https://")


def _download(url, limit=12_000_000, timeout=25):
    import urllib.request
    req = urllib.request.Request(_safe_url(url), headers={"User-Agent": f"RallyForgeBot/{VERSION}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("The update file is too big.")
    return data


def _download_checked(url, sha, name, limit=12_000_000, log=print):
    """Download and compare the checksum. GitHub's cache can serve an old copy for a few minutes right after a release:
    then wait and ask again with a cache-busting address instead of failing at once."""
    last = None
    for attempt in range(4):
        u = url if attempt == 0 else url + ("&" if "?" in url else "?") + f"cb={int(time.time())}{attempt}"
        data = _download(u, limit)
        if hashlib.sha256(data).hexdigest() == sha:
            return data
        last = name
        log(f"[i] {name}: the server still shows an older copy - trying again in a few seconds ({attempt + 1}/4)")
        time.sleep(6 * (attempt + 1))
    raise ValueError(f"{last} is not fully published yet - try again in a few minutes.")


def sync_monster_pack(log=print):
    """The published list can carry a pack of monster pictures. Download the ones this PC does not have yet (never
    replaces a picture you already have). -> number of new pictures."""
    import urllib.parse
    got = _LAST_MANIFEST[0]
    if not got:
        return 0
    man, base = got
    n = 0
    (USER / "monsters").mkdir(exist_ok=True)
    for name, meta in (man.get("monsters") or {}).items():
        try:
            if not _NAME_MON.match(name) or (USER / "monsters" / name).exists():
                continue
            data = _download(urllib.parse.urljoin(base, (meta or {}).get("url", "monsters/" + urllib.parse.quote(name))), 1_000_000)
            if hashlib.sha256(data).hexdigest() != str((meta or {}).get("sha256", "")) or not data.startswith(b"\x89PNG"):
                continue
            (USER / "monsters" / name).write_bytes(data)
            n += 1
        except Exception:
            continue
    if n:
        with _cache_lock:
            _mon_cache.clear()
        try:
            list_monsters(refresh=True)
        except Exception as _e:
            quiet_err("sync_monster_pack", _e)
        log(f"[+] {n} new monster picture(s) added to your library.")
    try:
        sync_catalog_pack(log)
    except Exception as _e:
        quiet_err("sync_monster_pack", _e)
    try:
        sync_culture_pack(log)
    except Exception as _e:
        quiet_err("sync_monster_pack", _e)
    return n


_NAME_CUL = re.compile(r"^(?P<c>[a-z]{2,20})/(map\.json|pins\.json|feats\.npz|map\.jpg|kf_\d{2,3}\.jpg)$")


def sync_culture_pack(log=print):
    """The published list can carry the culture reference maps (cultures/<culture>/...). Only missing or changed files
    are fetched; a culture whose map.json changed is reloaded."""
    import urllib.parse
    got = _LAST_MANIFEST[0]
    if not got:
        return 0
    man, base = got
    pack = man.get("cultures") or {}
    root = USER / "cultures"
    n, changed = 0, set()
    for name, meta in pack.items():
        try:
            mm = _NAME_CUL.match(name)
            if not mm or mm.group("c") not in CULTURES:
                continue
            want = str((meta or {}).get("sha256", ""))
            target = root / name
            if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == want:
                continue
            data = _download(urllib.parse.urljoin(base, (meta or {}).get("url", "cultures/" + urllib.parse.quote(name))), 8_000_000)
            if hashlib.sha256(data).hexdigest() != want:
                continue
            if name.endswith(".jpg") and not data.startswith(b"\xff\xd8"):
                continue
            if name.endswith(".npz") and not data.startswith(b"PK"):
                continue
            if name.endswith(".json"):
                json.loads(data.decode("utf-8"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            changed.add(mm.group("c"))
            n += 1
        except Exception:
            continue
    for c in changed:
        _CULTURE_MAPS.pop(c, None)
    if n:
        log(f"[+] City culture maps updated ({', '.join(sorted(changed))}).")
    return n


_NAME_CAT = re.compile(r"^(catalog\.json|art/[^/\\:*?\"<>|]{1,80}\.png)$")


def clean_catalog_junk(log=print):
    """Delete catalogue pictures that are not a real general: junk names from early scans ("a ae", "Unknown 45") and
    second copies ("Washington 2"). Only USER/catalog/art - the user's own captured pictures (USER/generals) stay."""
    n = 0
    for p in (USER / "catalog" / "art").glob("*.png"):
        try:
            if not is_known_general(p.stem):
                p.unlink()
                n += 1
        except OSError as _e:
            quiet_err("clean_catalog_junk", _e)
    if n:
        with _cache_lock:
            _gen_cache.clear()
        log(f"[i] General catalogue: {n} junk / duplicate picture(s) removed")
    return n


def sync_catalog_pack(log=print):
    """The published list can carry the general catalogue (catalog.json + a picture of every general), so testers who
    only update (no reinstall) get the full Listed generals picker too. Only missing or changed files are fetched."""
    import urllib.parse
    got = _LAST_MANIFEST[0]
    if not got:
        return 0
    man, base = got
    pack = man.get("catalog") or {}
    if not pack:
        return 0
    root = USER / "catalog"
    (root / "art").mkdir(parents=True, exist_ok=True)
    clean_catalog_junk(log)
    n = 0
    for name, meta in pack.items():
        try:
            if not _NAME_CAT.match(name):
                continue
            want = str((meta or {}).get("sha256", ""))
            target = root / name
            if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == want:
                continue
            if target.exists() and name != "catalog.json":
                old = hashlib.sha256(target.read_bytes()).hexdigest()
                if old not in [str(x) for x in ((meta or {}).get("replaces") or [])]:
                    continue                                   # never replace a picture already on this PC (only a published picture the update names as outdated)
            data = _download(urllib.parse.urljoin(base, (meta or {}).get("url", "catalog/" + urllib.parse.quote(name))), 3_000_000)
            if hashlib.sha256(data).hexdigest() != want:
                continue
            if name.endswith(".png") and not data.startswith(b"\x89PNG"):
                continue
            if name == "catalog.json":
                json.loads(data.decode("utf-8"))                # must be valid
            target.write_bytes(data)
            n += 1
        except Exception:
            continue
    if n:
        with _cache_lock:
            _gen_cache.clear()
        try:
            list_generals(refresh=True)
        except Exception as _e:
            quiet_err("sync_catalog_pack", _e)
        log(f"[+] General catalogue updated ({n} file(s)).")
    return n


def check_update(cfg):
    """-> {'version', 'notes', 'manifest', 'base'} if a newer version is published, None if you are up to date.
    Raises ValueError / OSError with a readable message when something is wrong."""
    url = (cfg.get("update_url") or "").strip() or DEFAULT_UPDATE_URL        # blank = the built-in address
    if not url:
        raise ValueError("No update address is set.")
    try:
        manifest = json.loads(_download(url, 1_000_000).decode("utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"Could not reach the update server ({type(e).__name__}) - check the internet connection and try again later.")
    key = (cfg.get("update_key") or "").strip()
    if key and not hmac.compare_digest(str(manifest.get("sig", "")), sign_manifest(manifest, key)):
        raise ValueError("The published update is not signed with your update key - ignored.")
    ver = str(manifest.get("version", ""))
    if not re.fullmatch(r"\d+(\.\d+){1,3}", ver):
        raise ValueError("The published version number is not valid.")
    files, pics = manifest.get("files") or {}, manifest.get("templates") or {}
    for need in ("rally_bot.py", "web_app.py"):
        if need not in files:
            raise ValueError(f"The update does not contain {need}.")
    for name, meta in files.items():
        if not (_NAME_PY.match(name) or _NAME_UI.match(name)):
            raise ValueError(f"Refusing the file name '{name}'.")
    for name in pics:
        if not _NAME_PNG.match(name):
            raise ValueError(f"Refusing the picture name '{name}'.")
    for name, meta in {**files, **pics}.items():
        if not re.fullmatch(r"[0-9a-f]{64}", str((meta or {}).get("sha256", ""))):
            raise ValueError(f"No valid checksum for {name}.")
    _LAST_MANIFEST[0] = (manifest, url)
    if version_tuple(ver) <= version_tuple(VERSION):
        return None
    return {"version": ver, "notes": str(manifest.get("notes", "")), "manifest": manifest, "base": url}


def install_update(info, log=print):
    """Download the files of 'info', check every checksum and that the code compiles, then make it the active version.
    The new code runs after the next start (through launcher.py / the exe)."""
    import urllib.parse
    man, base, ver = info["manifest"], info["base"], info["version"]
    APP_DIR.mkdir(exist_ok=True)
    tmp, dest = APP_DIR / f".tmp-{ver}", APP_DIR / f"v{ver}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    try:
        for name, meta in man["files"].items():
            log(f"[*] Downloading {name}")
            data = _download_checked(urllib.parse.urljoin(base, meta.get("url", name)), meta["sha256"], name, log=log)
            if name.endswith(".py"):
                compile(data, name, "exec")                   # a broken file is never installed
            (tmp / name).parent.mkdir(parents=True, exist_ok=True)
            (tmp / name).write_bytes(data)
        m = re.search(rb'(?m)^VERSION\s*=\s*"([^"]+)"', (tmp / "rally_bot.py").read_bytes())
        if not m or m.group(1).decode() != ver:
            raise ValueError("rally_bot.py does not carry the version the update announces.")
        pics = []
        for name, meta in (man.get("templates") or {}).items():
            data = _download_checked(urllib.parse.urljoin(base, meta.get("url", name)), meta["sha256"], name, 2_000_000, log=log)
            if not data.startswith(b"\x89PNG"):
                raise ValueError(f"{name} is not a picture - download refused.")
            pics.append((name, data, bool(meta.get("overwrite"))))
        (tmp / "manifest.json").write_text(json.dumps(man, indent=2), encoding="utf-8")
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    for name, data, overwrite in pics:                        # new pictures never replace ones you captured
        target = USER / "templates" / name
        if overwrite or not target.exists():
            target.write_bytes(data)
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)
    old = (APP_DIR / "current.txt").read_text().strip() if (APP_DIR / "current.txt").exists() else ""
    (APP_DIR / "previous.txt").write_text(old)
    (APP_DIR / "current.txt").write_text(dest.name)
    for d in APP_DIR.glob("v*"):                               # keep the new and the previous version only
        if d.is_dir() and d.name not in (dest.name, old):
            shutil.rmtree(d, ignore_errors=True)
    log(f"[+] Version {ver} installed. It starts the next time the app is opened.")
    return dest


# ------------------------------------------------------------------ resources
_ocr_gate = threading.BoundedSemaphore(2)       # how many Tesseract readings may run at the same time
_ocr_gate_size = [2]
_low_prio_done = [False]


def memory_info():
    """{'total_mb', 'free_mb', 'used_pct', 'proc_mb'} for this PC and for this app (None where unknown)."""
    info = {"total_mb": None, "free_mb": None, "used_pct": None, "proc_mb": None}
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class MEMSTAT(ctypes.Structure):
                _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            m = MEMSTAT()
            m.dwLength = ctypes.sizeof(MEMSTAT)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            info["total_mb"], info["free_mb"] = m.ullTotalPhys / 2 ** 20, m.ullAvailPhys / 2 ** 20
            k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
            p = PMC()
            p.cb = ctypes.sizeof(PMC)
            if psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(p), p.cb):
                info["proc_mb"] = p.WorkingSetSize / 2 ** 20
        else:
            mem = {k: int(v.split()[0]) for k, v in (l.split(":", 1) for l in open("/proc/meminfo"))
                   if k in ("MemTotal", "MemAvailable")}
            info["total_mb"], info["free_mb"] = mem["MemTotal"] / 1024, mem["MemAvailable"] / 1024
            rss = next(int(l.split()[1]) for l in open("/proc/self/status") if l.startswith("VmRSS"))
            info["proc_mb"] = rss / 1024
        if info["total_mb"]:
            info["used_pct"] = 100.0 * (1 - info["free_mb"] / info["total_mb"])
    except Exception as _e:
        quiet_err("memory_info", _e)
    return info


_MEM_SEEN = {"first": None, "peak": 0.0, "cpu": None}


def memory_report(info=None):
    """One '[mem]' log line: this app's memory now / at start / peak, PC free RAM, the app's CPU use since the
    last line and threads. Logged every 10 min so a slow leak or creeping load is easy to spot."""
    info = info or memory_info()
    proc, free = info.get("proc_mb"), info.get("free_mb")
    if proc:
        _MEM_SEEN["first"] = _MEM_SEEN["first"] or proc
        _MEM_SEEN["peak"] = max(_MEM_SEEN["peak"], proc)
    t, now = os.times(), time.time()
    cpu_s, cpu_txt = t.user + t.system, "?"
    if _MEM_SEEN["cpu"]:
        c0, w0 = _MEM_SEEN["cpu"]
        cpu_txt = f"{100.0 * (cpu_s - c0) / max(now - w0, 1) / (os.cpu_count() or 1):.0f}%"
    _MEM_SEEN["cpu"] = (cpu_s, now)
    mb = lambda v: f"{v:.0f} MB" if v else "?"
    return (f"[mem] app {mb(proc)} (start {mb(_MEM_SEEN['first'])}, peak {mb(_MEM_SEEN['peak'])}), "
            f"PC free {mb(free)}, app CPU {cpu_txt}, threads {threading.active_count()}, "
            f"alert keys {len(_notify_at)}")


def trim_memory():
    """Give memory back: collect garbage and (Windows) ask the system to take unused pages out of this app's
    working set. They are loaded again on demand, so this only matters while the PC is short of RAM."""
    gc.collect()
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            k32.SetProcessWorkingSetSize.argtypes = [wintypes.HANDLE, ctypes.c_size_t, ctypes.c_size_t]
            k32.SetProcessWorkingSetSize(k32.GetCurrentProcess(), ctypes.c_size_t(-1).value, ctypes.c_size_t(-1).value)
        except Exception as _e:
            quiet_err("trim_memory", _e)


def lower_priority():
    """Run below normal priority so the emulators always get the CPU first."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            k32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            k32.SetPriorityClass(k32.GetCurrentProcess(), 0x00004000)         # BELOW_NORMAL_PRIORITY_CLASS
        else:
            os.nice(5)
    except Exception as _e:
        quiet_err("lower_priority", _e)


def configure_resources(cfg):
    """Apply the low-memory settings (safe to call again and again)."""
    global _ocr_gate
    want = int(cfg.get("ocr_parallel") or (1 if cfg.get("low_memory") else 2))
    if want != _ocr_gate_size[0]:
        _ocr_gate, _ocr_gate_size[0] = threading.BoundedSemaphore(max(1, want)), max(1, want)
    if cfg.get("low_memory") and not _low_prio_done[0]:
        _low_prio_done[0] = True
        lower_priority()


def wait_for_ram(cfg, log, stop, what):
    """Wait until the PC has enough free RAM for 'what' (e.g. starting another emulator). -> False on timeout."""
    need = int(cfg.get("min_free_ram_mb") or 0)
    if need <= 0:
        return True
    end, last = time.time() + int(cfg.get("ram_wait_seconds", 600)), 0.0
    while not stop.is_set():
        free = memory_info()["free_mb"]
        if free is None or free >= need:
            return True
        if time.time() - last > 30:
            last = time.time()
            log(f"[*] Waiting for free RAM before {what}: {free:.0f} MB free, {need} MB wanted")
        if time.time() > end:
            return False
        stop.wait(min(5, max(0.2, end - time.time())))
    return False


def close_emulator(dev, cfg, log):
    """Close this emulator with its own stop command (MuMu / LDPlayer / your stop_cmd) to free its RAM."""
    cmd = emulator_stop_command(dev.serial, dev.adb, cfg)
    if not cmd:
        log("[i] I do not know how to close this emulator (set a stop command for it).")
        return False
    try:
        subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, timeout=60, creationflags=NOWIN)
        log("[+] Emulator closed.")
        return True
    except Exception as e:
        log(f"[!] Could not close the emulator: {e}")
        return False


# --------------------------------------------------------------- alerts
_notify_at = {}


def send_alert(cfg, text):
    """Send a message to Telegram and / or Discord. -> (ok, what happened)."""
    import urllib.parse
    import urllib.request
    results, targets = [], 0
    tok, chat = (cfg.get("telegram_token") or "").strip(), str(cfg.get("telegram_chat") or "").strip()
    if tok and chat:
        targets += 1
        try:
            data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
            with urllib.request.urlopen(urllib.request.Request(
                    f"https://api.telegram.org/bot{tok}/sendMessage", data=data), timeout=10) as r:
                ok = json.loads(r.read().decode() or "{}").get("ok", False)
            results.append("Telegram: sent" if ok else "Telegram: refused the message")
        except Exception as e:
            results.append(f"Telegram: failed ({type(e).__name__}: {e})")
    hook = (cfg.get("discord_webhook") or "").strip()
    if hook:
        targets += 1
        try:
            req = urllib.request.Request(hook, data=json.dumps({"content": text[:1900]}).encode(),
                                         headers={"Content-Type": "application/json",
                                                  "User-Agent": "RallyForgeBot/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                r.read()
            results.append("Discord: sent")
        except Exception as e:
            results.append(f"Discord: failed ({type(e).__name__}: {e})")
    if not targets:
        return False, "No Telegram token + chat id or Discord webhook is set."
    return all(r.endswith("sent") for r in results), "; ".join(results)


def notify(cfg, serial, text, kind="problem"):
    """Fire-and-forget alert (never slows the bot down, never repeats the same text within 30 s)."""
    if kind == "problem" and not cfg.get("notify_problems", True):
        return
    if kind == "join" and not cfg.get("notify_joins", False):
        return
    if not (cfg.get("discord_webhook") or (cfg.get("telegram_token") and cfg.get("telegram_chat"))):
        return
    key, now = (serial, text), time.time()
    if now - _notify_at.get(key, 0) < 30:
        return
    if len(_notify_at) > 200:                            # texts carry numbers/times, so keys never repeat: drop the old ones
        for k in [k for k, t in list(_notify_at.items()) if now - t >= 30]:
            _notify_at.pop(k, None)
    _notify_at[key] = now
    name = (cfg.get("device_names") or {}).get(serial) or serial
    threading.Thread(target=send_alert, args=(cfg, f"[{name}] {text}"), daemon=True).start()


# ------------------------------------------------------------- schedules
_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _parse_days(txt):
    out = set()
    for tok in re.split(r"\s*,\s*", txt.strip().lower()):
        if not tok:
            continue
        if "-" in tok:
            a, b = [t.strip()[:3] for t in tok.split("-", 1)]
            if a not in _DAYS or b not in _DAYS:
                raise ValueError(f"unknown day in '{tok}'")
            i = _DAYS.index(a)
            while True:
                out.add(i)
                if i == _DAYS.index(b):
                    break
                i = (i + 1) % 7
        else:
            if tok[:3] not in _DAYS:
                raise ValueError(f"unknown day '{tok}'")
            out.add(_DAYS.index(tok[:3]))
    return out


def parse_schedule(text):
    """'Mon-Fri 08:00-12:00, 18:00-23:30; Sat 10:00-14:00' -> [(days, start_min, end_min)]. No days = every day."""
    rules = []
    for part in re.split(r"[;\n]+", text or ""):
        part = part.strip()
        if not part:
            continue
        days = set(range(7))
        m = re.match(r"^([A-Za-z][A-Za-z,\- ]*?)\s+(\d.*)$", part)
        if m:
            days, part = _parse_days(m.group(1)), m.group(2)
        for rng in part.split(","):
            rng = rng.strip()
            if not rng:
                continue
            mm = re.fullmatch(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", rng)
            if not mm:
                raise ValueError(f"cannot read '{rng}' (write it like 08:00-12:00)")
            h1, m1, h2, m2 = map(int, mm.groups())
            if h1 > 24 or h2 > 24 or m1 > 59 or m2 > 59:
                raise ValueError(f"'{rng}' is not a valid time")
            rules.append((days, h1 * 60 + m1, h2 * 60 + m2))
    return rules


def schedule_active(rules, now):
    """Should the bot be running at 'now' (a datetime)?"""
    minute, wd = now.hour * 60 + now.minute, now.weekday()
    for days, a, b in rules:
        if a == b:
            if wd in days:
                return True
        elif a < b:
            if wd in days and a <= minute < b:
                return True
        elif (wd in days and minute >= a) or ((wd - 1) % 7 in days and minute < b):    # past midnight
            return True
    return False


# ------------------------------------------------------------- profiles
def profiles_dir():
    d = USER / "profiles"
    d.mkdir(exist_ok=True)
    return d


def list_profiles():
    return sorted((p.stem for p in profiles_dir().glob("*.json")), key=str.lower)


def save_profile(name, cfg):
    name = re.sub(r'[\\/:*?"<>|]', "", str(name)).strip()
    if not name:
        raise ValueError("Give the profile a name.")
    save_json(profiles_dir() / f"{name}.json", cfg, indent=2)
    return name


def load_profile(name):
    cfg = dict(DEFAULTS)
    cfg.update(json.loads((profiles_dir() / f"{name}.json").read_text(encoding="utf-8")))
    return migrate_cfg(cfg)


AUTO_BACKUP_PREFIX = "Auto backup "


def auto_backup(reason, log=None, keep=10):
    """Save the settings exactly as they are on disk as a profile 'Auto backup <date> - <reason>' (Profiles > Load restores
    it), plus a copy of the generals list. Only the newest `keep` auto backups are kept; your own profiles are never touched."""
    try:
        if not CONFIG.exists():
            return None
        raw = json.loads(CONFIG.read_text())
        name = save_profile(f"{AUTO_BACKUP_PREFIX}{time.strftime('%Y-%m-%d %H-%M')} - {reason}", raw)
        bdir = USER / "backups"
        bdir.mkdir(exist_ok=True)
        gm = USER / "generals" / "generals.json"
        if gm.exists():
            shutil.copy2(gm, bdir / f"generals_{time.strftime('%Y%m%d_%H%M')}.json")
        for pattern, d in ((f"{AUTO_BACKUP_PREFIX}*.json", profiles_dir()), ("generals_*.json", bdir)):
            old = sorted(d.glob(pattern), key=lambda f: f.stat().st_mtime, reverse=True)[keep:]
            for f in old:
                try:
                    f.unlink()
                except OSError as _e:
                    quiet_err("auto_backup", _e)
        if log:
            log(f"[i] Settings backed up as profile '{name}' (Profiles > Load to restore)")
        return name
    except Exception as e:
        if log:
            log(f"[!] Could not back up the settings: {e}")
        return None


def backup_before_new_version(log=None):
    """The first time a new version starts, back up the settings before it changes anything."""
    marker = USER / "backups" / "last_version.txt"
    try:
        last = marker.read_text().strip()
    except OSError:
        last = ""
    if last != VERSION:
        auto_backup(f"before {VERSION}", log)
        try:
            marker.parent.mkdir(exist_ok=True)
            marker.write_text(VERSION)
        except OSError as _e:
            quiet_err("backup_before_new_version", _e)


def delete_profile(name):
    p = profiles_dir() / f"{name}.json"
    if p.exists():
        p.unlink()


# ------------------------------------------------- emulator + game lifecycle
_launch_lock = threading.Lock()          # emulators are started one after another


def _adb(adb, serial, *args, timeout=15):
    try:
        r = subprocess.run([adb, "-s", serial, *args], capture_output=True, text=True,
                           timeout=timeout, creationflags=NOWIN)
        return r.stdout or ""
    except Exception:
        return ""


def device_online(adb, serial):
    if ":" in serial:
        try:
            subprocess.run([adb, "connect", serial], capture_output=True, creationflags=NOWIN, timeout=10)
        except Exception as _e:
            quiet_err("device_online", _e)
    try:
        out = subprocess.run([adb, "devices"], capture_output=True, text=True,
                             creationflags=NOWIN, timeout=15).stdout
    except Exception:
        return False
    return f"{serial}\tdevice" in out


def boot_done(adb, serial):
    return _adb(adb, serial, "shell", "getprop", "sys.boot_completed").strip() == "1"


def evony_package(adb, serial, cfg):
    pkg = cfg["evony_package"]
    out = _adb(adb, serial, "shell", "pm", "list", "packages")
    if not out or f"package:{pkg}" in out:
        return pkg
    found = re.findall(r"package:(\S*evony\S*)", out, re.I)
    return found[0] if found else pkg


def app_foreground(adb, serial, pkg):
    """True / False if Evony has the screen, None if it cannot be told."""
    out = _adb(adb, serial, "shell", "dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'")
    if not out.strip():
        return None
    return pkg in out


_FRESH_START = set()              # serials whose game was just (re)started: the first settled picture is saved once


def start_app(adb, serial, pkg):
    _FRESH_START.add(serial)
    _adb(adb, serial, "shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1")


def ldconsole_path(adb=None):
    dirs = [Path(adb).parent] if adb else []
    dirs += [c.parent for c in adb_candidates() if "ldplayer" in str(c).lower() or "leidian" in str(c).lower()]
    for d in dirs:
        if (d / "ldconsole.exe").exists():
            return d / "ldconsole.exe"
    return None


def bluestacks_instance(serial, name=None):
    """BlueStacks instance id (e.g. Pie64_3) for an ADB address; when the port is not found (a stopped instance, or the
    conf lists another port) the instance's display name is tried (the name in BlueStacks = the name in the bot)."""
    inst = _bluestacks_by_port(serial)
    if inst or not name:
        return inst
    _m = re.fullmatch(r"127\.0\.0\.1:(\d+)", serial)
    if _m and int(_m.group(1)) >= 16000:          # a MuMu 12 port: not a BlueStacks instance even if a BlueStacks one has the same name
        return None
    want = str(name).strip().lower()
    hits = set()
    for conf in bluestacks_confs():
        try:
            txt = Path(conf).read_text(errors="ignore")
        except Exception:
            continue
        for i, dn in re.findall(r'bst\.instance\.(\w+)\.display_name="([^"]*)"', txt):
            if dn.strip().lower() == want:
                hits.add(i)
    return hits.pop() if len(hits) == 1 else None


def _bluestacks_by_port(serial):
    m = re.fullmatch(r"127\.0\.0\.1:(\d+)", serial)
    port = int(m.group(1)) if m else None
    if port is None:
        m = re.fullmatch(r"emulator-(\d+)", serial)
        port = int(m.group(1)) + 1 if m else None
    if port is None:
        return None
    for conf in bluestacks_confs():
        try:
            txt = Path(conf).read_text(errors="ignore")
        except Exception:
            continue
        for inst, p in re.findall(r'bst\.instance\.(\w+)\.adb_port="(\d+)"', txt):
            if int(p) == port:
                return inst
    return None


def bluestacks_player(adb=None):
    cands = [Path(adb).parent / "HD-Player.exe"] if adb else []
    pfs = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
    pfs += [f"{d}:\\Program Files" for d in "CDEFGH"]
    for pf in pfs:
        for d in ("BlueStacks_nxt", "BlueStacks_nxt_cn", "BlueStacks"):
            cands.append(Path(pf) / d / "HD-Player.exe")
    try:
        import winreg
        for key in ("SOFTWARE\\BlueStacks_nxt", "SOFTWARE\\BlueStacks_nxt_cn", "SOFTWARE\\BlueStacks"):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0, winreg.KEY_READ | view) as k:
                        cands.append(Path(winreg.QueryValueEx(k, "InstallDir")[0]) / "HD-Player.exe")
                except OSError as _e:
                    quiet_err("bluestacks_player", _e)
    except Exception as _e:
        quiet_err("bluestacks_player", _e)
    return next((c for c in cands if c.exists()), None)


_LAUNCH_FILE = USER / "emulator_launch.json"
_LAUNCH_SEEN = set()


def _launch_memory():
    try:
        return json.loads(_LAUNCH_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def remember_launch(serial, pid=None):
    """Note how a running BlueStacks instance was started (HD-Player.exe path + --instance, read from the process that owns
    its ADB port), so the bot can start it again after closing it, even when bluestacks.conf is somewhere unexpected."""
    m = re.fullmatch(r"(?:127\.0\.0\.1|localhost):(\d+)", serial)
    if not m or os.name != "nt":
        return None
    try:
        if pid is None:
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
                                 timeout=20, creationflags=NOWIN).stdout
            pids = [int(p) for lp, p in re.findall(r"TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)", out) if lp == m.group(1)]
            if not pids:
                return None
            pid = pids[0]
        line = subprocess.run(["powershell", "-NoProfile", "-Command",
                               f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"],
                              capture_output=True, text=True, timeout=25, creationflags=NOWIN).stdout.strip()
        exe = re.match(r'\s*"([^"]+\.exe)"|\s*(\S+\.exe)', line, re.I)
        inst = re.search(r'--instance[\s=]+"?([\w.-]+)', line)
        exe = (exe.group(1) or exe.group(2)) if exe else ""
        if not exe or not inst or Path(exe).name.lower() != "hd-player.exe" or not Path(exe).exists():
            return None
        mem = _launch_memory()
        mem[serial] = {"exe": exe, "instance": inst.group(1)}
        save_json(_LAUNCH_FILE, mem, indent=1)
        return mem[serial]
    except Exception:
        return None


def mumu_manager(adb=None):
    cands = [Path(adb).parent / "MuMuManager.exe"] if adb and adb != "auto" else []
    roots = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
    roots += [f"{d}:\\Program Files" for d in "DEFG"]
    for r in roots:
        for sub in (r"Netease\MuMu Player 12\shell", r"Netease\MuMuPlayer-12.0\shell", r"Netease\MuMu\nx_main",
                    r"Netease\MuMuPlayer\nx_main", r"Netease\MuMu Player 12\nx_main"):
            cands.append(Path(r) / sub / "MuMuManager.exe")
    return next((c for c in cands if c.exists()), None)


def mumu_index(serial, adb, name=None):
    """MuMu 12 instance number for an ADB address like 127.0.0.1:16448, or None.
    A stopped instance has no ADB port in MuMu's list, so the instance NAME (the one you gave it in MuMu, the same as
    its name in the bot) is used as a second way to find it."""
    m = re.fullmatch(r"127\.0\.0\.1:(\d+)", serial)
    mgr = mumu_manager(adb) if m else None
    if not mgr:
        return None
    port = int(m.group(1))

    def walk(o, key=None):
        if isinstance(o, dict):
            if str(o.get("adb_port")) == str(port):
                return str(o.get("index", key))
            for k, v in o.items():
                r = walk(v, k)
                if r is not None:
                    return r
        elif isinstance(o, list):
            for v in o:
                r = walk(v)
                if r is not None:
                    return r
        return None
    try:
        out = subprocess.run([str(mgr), "info", "-v", "all"], capture_output=True, text=True,
                             timeout=20, creationflags=NOWIN).stdout
        data = json.loads(out)
        found = walk(data)
        if found not in (None, "None"):
            return found
        if name and port >= 16000:            # MuMu 12 lives on ports 16384+; BlueStacks (5555+) must never be matched to a MuMu instance of the same name
            want = str(name).strip().lower()
            hits = []

            def by_name(o, key=None):
                if isinstance(o, dict):
                    if str(o.get("name", "")).strip().lower() == want and ("index" in o or key is not None):
                        hits.append(str(o.get("index", key)))
                    for k, v in o.items():
                        by_name(v, k)
                elif isinstance(o, list):
                    for v in o:
                        by_name(v)
            by_name(data)
            hits = [x for x in hits if x not in ("None", "")]
            if len(set(hits)) == 1:
                return hits[0]
    except Exception as _e:
        quiet_err("mumu_index", _e)
    if 16384 <= port <= 16384 + 32 * 99 and (port - 16384) % 32 == 0:      # MuMu 12 default ports
        return str((port - 16384) // 32)
    return None


def emulator_stop_command(serial, adb, cfg):
    """Command that closes this emulator instance nicely, or None. Your own stop_cmd always wins."""
    if cfg.get("stop_cmd"):
        return cfg["stop_cmd"]
    idx = mumu_index(serial, adb, (cfg.get("device_names") or {}).get(serial))
    if idx is not None:
        return [str(mumu_manager(adb)), "control", "-v", idx, "shutdown"]
    m = re.fullmatch(r"emulator-(\d+)", serial)
    if m and int(m.group(1)) >= 5554 and int(m.group(1)) % 2 == 0:
        con = ldconsole_path(adb)
        if con:
            return [str(con), "quit", "--index", str((int(m.group(1)) - 5554) // 2)]
    return None


PROTECTED = {"adb.exe", "python.exe", "pythonw.exe", "evonyrallybot.exe", "system", "svchost.exe",
             "explorer.exe", "services.exe", "wininit.exe", "csrss.exe", "lsass.exe"}


def kill_listener(serial, log):
    """Last resort: end the process that owns this emulator's ADB port (the emulator's VM)."""
    m = re.fullmatch(r"(?:127\.0\.0\.1|localhost):(\d+)", serial)
    if not m or os.name != "nt":
        return False
    port = m.group(1)
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
                             timeout=20, creationflags=NOWIN).stdout
        pids = {int(p) for lp, p in re.findall(r"TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)", out) if lp == port}
        killed = False
        for pid in pids:
            name = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                                  text=True, timeout=15, creationflags=NOWIN).stdout.split(",")[0].strip('" \r\n')
            if not name or name.lower() in PROTECTED:
                continue
            if name.lower() == "hd-player.exe":
                remember_launch(serial, pid)                # so the same instance can be started again afterwards
            log(f"[*] Ending {name} (process {pid}) that owns port {port}")
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=30,
                           creationflags=NOWIN)
            killed = True
        return killed
    except Exception as e:
        log(f"[!] Could not end the emulator process: {e}")
        return False


def emulator_launch_command(serial, adb, cfg):
    """Command that starts this emulator instance, or None. Your own launch_cmd always wins."""
    if cfg.get("launch_cmd"):
        return cfg["launch_cmd"]
    idx = mumu_index(serial, adb, (cfg.get("device_names") or {}).get(serial))
    if idx is not None:
        return [str(mumu_manager(adb)), "control", "-v", idx, "launch"]
    m = re.fullmatch(r"emulator-(\d+)", serial)
    if m and int(m.group(1)) >= 5554 and int(m.group(1)) % 2 == 0:          # LDPlayer: 5554 + 2 * index
        con = ldconsole_path(adb)
        if con:
            return [str(con), "launch", "--index", str((int(m.group(1)) - 5554) // 2)]
    inst = bluestacks_instance(serial, (cfg.get("device_names") or {}).get(serial))
    player = bluestacks_player(adb) if inst else None
    if inst and player:
        return [str(player), "--instance", inst]
    mem = _launch_memory().get(serial)                       # how this instance was running last time (noted from its process)
    if mem and Path(mem.get("exe", "")).exists() and mem.get("instance"):
        return [mem["exe"], "--instance", mem["instance"]]
    return None


def ready_screen(screen, cfg):
    """A screen the game only shows once it has finished loading."""
    if screen_state(screen, cfg) != "unknown":
        return True
    return has_template("rally_icon") and find(screen, "rally_icon", 0.85) is not None


def wait_calm(dev, cfg, log, stop, secs=20, limit=120):
    """Wait until the game has shown no red wifi icon for `secs` seconds in a row (gives up after `limit` s)."""
    t0, calm_from = time.time(), time.time()
    while not stop.is_set() and time.time() - t0 < limit:
        try:
            if lag_icon(dev.screenshot()):
                calm_from = time.time()
        except Exception:
            calm_from = time.time()
        if time.time() - calm_from >= secs:
            return True
        _nap(stop, 2)
    return False


def settle_game(dev, cfg, log, stop):
    """After a start / restart / reconnect: give the game a few calm seconds (late pop-ups, icons still appearing,
    animations) before any task starts. Needs `settle_seconds` to have passed AND the screen to be a known one and
    unchanged for 3 checks in a row."""
    secs = max(cfg.get("settle_seconds", 8) or 0, cfg.get("restart_settle_seconds", 25) or 0)   # owner 25 Oct: 20-30 s after a (re)start
    if not secs or secs <= 0:
        return
    log(f"[*] Letting the game settle ({secs}s) before starting")
    t_end, limit = time.time() + secs, time.time() + secs + 90
    prev, calm, odd_since = None, 0, None
    while not stop.is_set() and time.time() < limit:
        try:
            screen = dev.screenshot()
        except Exception:
            _nap(stop, 2)
            continue
        closed = False
        for name in list_templates("popup_"):
            h = find(screen, name, 0.85)
            if h:
                tap_hit(dev, h)
                log(f"[*] Closed popup ({name})")
                closed = True
                break
        if closed:
            prev, calm, odd_since = None, 0, None
            t_end = max(t_end, time.time() + 3)
            _nap(stop, 1.5)
            continue
        small = cv2.resize(cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY), (54, 96), interpolation=cv2.INTER_AREA)
        ok = ready_screen(screen, cfg)
        try:
            if lag_icon(screen):                             # red wifi: not calm, and the settle time starts again
                ok, t_end = False, max(t_end, time.time() + secs)
        except Exception as _e:
            quiet_err("settle_game", _e)
        still = prev is not None and float(np.mean(cv2.absdiff(prev, small))) < 2.5
        prev = small
        if ok:
            odd_since = None
            calm = calm + 1 if still else 0
        else:
            calm = 0
            odd_since = odd_since or time.time()
            if time.time() - odd_since > 8:                  # something is in the way
                odd_since = None
                clear_popups(dev, cfg, log, stop, rounds=2)
        if time.time() >= t_end and calm >= 3:
            log("[+] The game has settled")
            return
        _nap(stop, 1.2)
    if not stop.is_set():
        log("[*] The game did not look fully calm - carrying on")


def save_start_picture(dev, cfg, log, keep=30):
    """Right after a game start has settled (nothing moved yet): save one picture in debug/game_start/<serial>/ and,
    when it is the city, log how far it is from the previous start's city picture. Only looks - never taps."""
    try:
        shot = dev.screenshot()
        view = view_of(shot, cfg) or "unknown"
        out = USER / "debug" / "game_start" / re.sub(r"[^\w.-]", "_", dev.serial)
        out.mkdir(parents=True, exist_ok=True)
        prev = sorted(out.glob("*_city.png"))
        name = f"{time.strftime('%Y%m%d-%H%M%S')}_{view}.png"
        cv2.imwrite(str(out / name), shot)
        if view == "city" and prev:
            old = cv2.imread(str(prev[-1]))
            if old is not None and old.shape == shot.shape:
                d = float(np.mean(cv2.absdiff(old[100:860:4, ::4], shot[100:860:4, ::4])))
                sx, sy, conf = city_shift(old, shot)
                log(f"[i] Game start picture ({view}) vs last start: difference {d:.1f} (0-5 = same place), "
                    f"shift {sx:.0f},{sy:.0f} (confidence {conf:.2f})")
        else:
            log(f"[i] Game start picture saved ({view})")
        for p in sorted(out.glob("*.png"))[:-keep]:
            p.unlink(missing_ok=True)
    except Exception as e:
        log(f"[!] Could not save the game start picture: {e}")



KICKED = {}                       # serial -> time (epoch s) when the wait after being kicked ends (shown in the emulator list)
TAKEBACK = {}                     # serial -> Event: set from the interface to end the wait after being kicked
GAME_RESTART_REQ = set()          # serials whose Evony should be closed and opened again (Restart game from the interface)
EMU_RESTART_REQ = set()           # serials whose emulator should be closed and started again (Restart emulator from the interface)


def reclaim_if_kicked(dev, cfg, log, stop, screen):
    """The game was opened on another device ("someone login with your account"). Wait out the grace time without
    touching anything, then tap Restart. -> True if that happened (the caller starts over), else False."""
    if not cfg.get("reclaim_elsewhere", True):
        return False
    if not find_text(screen, ["disconnected", "someone login", "logged in elsewhere", "another device"],
                     region=(60, 250, 360, 300), cutoff=0.8):
        return False
    mins = float(cfg.get("reclaim_grace_min", 10) or 0)
    log(f"[!] Logged in on another device - leaving the game alone for {mins:g} min")
    notify(cfg, dev.serial, f"Game opened on another device - waiting {mins:g} min before reclaiming it.")
    t_end = time.time() + mins * 60
    ev = TAKEBACK.setdefault(dev.serial, threading.Event())
    ev.clear()
    KICKED[dev.serial] = t_end
    while time.time() < t_end and not stop.is_set() and not ev.is_set():
        left = max(0, int(t_end - time.time()))
        try:
            log("@act " + json.dumps({"k": "step", "text": f"KICKED - the game was opened on another device. "
                                      f"Taking it back in {left // 60}:{left % 60:02d}  (or press 'Take game back now')"}))
        except Exception as _e:
            quiet_err("reclaim_if_kicked", _e)
        ev.wait(2)
    KICKED.pop(dev.serial, None)
    if stop.is_set():
        return True
    if ev.is_set():
        log("[*] 'Take game back now' pressed - not waiting any longer")
    try:
        log("@act " + json.dumps({"k": "step", "text": "Taking the game back..."}))
    except Exception as _e:
        quiet_err("reclaim_if_kicked", _e)
    hit = find_text(dev.screenshot(), ["Restart"], cutoff=0.8)
    if hit:
        dev.tap(hit[0], hit[1])
        log("[*] Wait is over - tapped Restart to take the game back")
        _nap(stop, 8, 10)
    else:
        dev.tap(358, 566)                       # the Restart button sits at the same place in every dialog
        log("[*] Wait is over - tapped where the Restart button is")
        _nap(stop, 8, 10)
    return True


_LOAD_RESTARTS = {}
_START_FAILS = {}            # serial -> emulator starts in a row that did not finish booting


LOADING = {}                      # serial -> (what, since epoch s) while the emulator boots / Evony loads (shown in the instance list)


def wait_stable(dev, cfg, log, stop, pkg=None, allow_emu_restart=True):
    """Wait until the game shows a known screen several times in a row (loading screens do not count)."""
    LOADING.setdefault(dev.serial, ("loading", time.time()))
    try:
        return _wait_stable(dev, cfg, log, stop, pkg, allow_emu_restart)
    finally:
        LOADING.pop(dev.serial, None)


def _wait_stable(dev, cfg, log, stop, pkg=None, allow_emu_restart=True):
    good, last_note, last_fg = 0, 0.0, time.time()
    not_ready_since, last_clear, last_kick = None, 0.0, 0.0
    loading_since, app_restarted = None, False                 # stuck on the loading / "Connecting" screen -> close and reopen Evony once
    end = time.time() + cfg["game_load_timeout"]
    while time.time() < end and not stop.is_set():
        if pause_gate():                                       # paused meanwhile: no clicks, no timeout, start counting again after the resume
            good, not_ready_since = 0, None
            end = time.time() + cfg["game_load_timeout"]
        try:
            screen = dev.screenshot()
        except Exception:
            good = 0
            _nap(stop, 3)
            continue
        if time.time() - last_kick > 4:
            last_kick = time.time()
            if reclaim_if_kicked(dev, cfg, log, stop, screen):
                good, not_ready_since = 0, None
                end = time.time() + cfg["game_load_timeout"]
                continue
        closed = False
        for name in list_templates("popup_"):
            h = find(screen, name, 0.85)
            if h:
                tap_hit(dev, h)
                log(f"[*] Closed popup ({name})")
                closed = True
                break
        if closed:
            good = 0
            _nap(stop, 1.5)
            continue
        if ready_screen(screen, cfg):
            good += 1
            if good >= cfg["stable_checks"]:
                log("[+] The game is loaded and stable")
                settle_game(dev, cfg, log, stop)
                if dev.serial in _FRESH_START and not stop.is_set():
                    _FRESH_START.discard(dev.serial)
                    save_start_picture(dev, cfg, log)
                return not stop.is_set()
        else:
            good = 0
            now = time.time()
            not_ready_since = not_ready_since or now
            loading_since = loading_since or now
            if not app_restarted and now - loading_since > max(90, cfg["game_load_timeout"] / 2):
                app_restarted = True                           # half the wait gone and still loading: Evony hangs (often on "Connecting")
                try:
                    pkg = pkg or evony_package(dev.adb, dev.serial, cfg)
                    log("[!] The game is stuck on the loading screen - closing and opening Evony again")
                    _adb(dev.adb, dev.serial, "shell", "am", "force-stop", pkg)
                    _nap(stop, 3, 4)
                    start_app(dev.adb, dev.serial, pkg)
                    _nap(stop, 8)
                except Exception as e:
                    log(f"[!] Could not reopen Evony: {e}")
                good, not_ready_since, loading_since = 0, None, None
                end = time.time() + cfg["game_load_timeout"]
                continue
            if now - last_note > 30:
                last_note = now
                log("[*] Waiting for the game to finish loading...")
            if now - not_ready_since > 10 and now - last_clear > 10:   # not a loading screen any more: a pop-up / page is in the way
                last_clear = now
                if clear_popups(dev, cfg, log, stop, rounds=3):
                    not_ready_since = None
                    _nap(stop, 1.5)
                    continue
                if game_screen_shown(dev.screenshot()):          # 25 Oct: Rob sat 3 min on an open Alliance page "waiting for loading"
                    log("[*] The game is already open on another screen - finding the way back")
                    if _ensure_view(dev, cfg, stop, log, "city"):
                        not_ready_since = loading_since = None
                        continue
        if ready_screen(screen, cfg):
            not_ready_since = loading_since = None
        if pkg and time.time() - last_fg > 15:
            last_fg = time.time()
            if app_foreground(dev.adb, dev.serial, pkg) is False:
                log("[*] Evony is not in front - starting it again")
                start_app(dev.adb, dev.serial, pkg)
        _nap(stop, 3)
    if not stop.is_set():
        if allow_emu_restart and cfg.get("load_restart_emulator", True) and cfg.get("auto_launch_emulator"):
            now = time.time()
            hist = _LOAD_RESTARTS.setdefault(dev.serial, [])
            hist[:] = [t for t in hist if now - t < 3600]
            if len(hist) < int(cfg.get("max_emulator_restarts", 3)):
                hist.append(now)
                notify(cfg, dev.serial, "The game is stuck on loading - restarting the emulator.")
                return restart_emulator(dev, cfg, log, stop, f"the game did not load within {cfg['game_load_timeout']}s")
        log("[!] The game did not look stable in time - carrying on anyway")
    return not stop.is_set()


def bring_online(dev, cfg, log, stop, wait=True, check_ready=False):
    """Make sure the emulator is running and Evony is open (and, if we had to start it, loaded)."""
    try:
        return _bring_online(dev, cfg, log, stop, wait, check_ready)
    finally:
        LOADING.pop(dev.serial, None)


def _bring_online(dev, cfg, log, stop, wait=True, check_ready=False):
    adb, serial = dev.adb, dev.serial
    restarted = False
    if serial not in _LAUNCH_SEEN and device_online(adb, serial):
        _LAUNCH_SEEN.add(serial)
        threading.Thread(target=remember_launch, args=(serial,), daemon=True).start()
    if not device_online(adb, serial):
        if not cfg["auto_launch_emulator"]:
            raise RuntimeError(f"Device {serial} is offline (enable 'start the emulator automatically').")
        cmd = emulator_launch_command(serial, adb, cfg)
        if not cmd:
            _inst = bluestacks_instance(serial, (cfg.get("device_names") or {}).get(serial))
            _confs = bluestacks_confs()
            _why = ("BlueStacks instance not found in bluestacks.conf (checked %d file(s) for port/name)" % len(_confs)
                    if not _inst and _confs else
                    "BlueStacks player (HD-Player.exe) not found" if _inst else "no launcher found for this emulator type")
            log(f"[!] Cannot start {serial}: {_why}")
            raise RuntimeError("The emulator is off and I do not know how to start it (" + _why + "). "
                               "Set a launch command for this instance.")
        if not wait_for_ram(cfg, log, stop, "starting the emulator"):
            if stop.is_set():
                return False
            notify(cfg, serial, "Not enough free RAM to start the emulator.")
            raise RuntimeError("There was not enough free RAM to start the emulator in time.")
        fails = _START_FAILS.get(serial, 0)
        if fails:                                          # last start hung: a half-open instance ignores "launch", so close it fully first
            log(f"[*] The last start did not finish ({fails}x) - closing this emulator fully before starting it again")
            LOADING[serial] = ("restart", time.time())
            stop_cmd = emulator_stop_command(serial, adb, cfg)
            if stop_cmd:
                try:
                    subprocess.run(stop_cmd, shell=isinstance(stop_cmd, str), creationflags=NOWIN,
                                   capture_output=True, timeout=60)
                except Exception as e:
                    log(f"[!] Closing the emulator did not work: {e}")
                _nap(stop, 10)
            kill_listener(serial, log)                     # anything still holding the port (stuck VM)
            _nap(stop, 10)
            if stop.is_set():
                return False
        log("[*] The emulator is off - starting it")
        LOADING[serial] = ("starting", time.time())
        with _launch_lock:
            subprocess.Popen(cmd, shell=isinstance(cmd, str), creationflags=NOWIN,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            gap = cfg.get("emulator_launch_gap", 15)
            stop.wait(max(gap, 45) if cfg.get("low_memory") else gap)    # one emulator boots at a time
        restarted = True
        end = time.time() + cfg["emulator_boot_timeout"]
        while time.time() < end and not stop.is_set():
            if device_online(adb, serial) and boot_done(adb, serial):
                break
            _nap(stop, 4)
        else:
            if stop.is_set():
                return False
            _START_FAILS[serial] = fails + 1
            if fails + 1 >= 2:
                notify(cfg, serial, f"The emulator failed to start {fails + 1} times in a row - "
                                    "it may need a manual restart (or a PC restart).")
            raise RuntimeError("The emulator did not finish starting in time.")
        _START_FAILS.pop(serial, None)
        log("[+] The emulator is up")
        heavy_release(serial)                              # the heavy part is over: loading Evony does not hold up other restarts
        _nap(stop, 5)
    pkg = None
    if cfg["auto_launch_game"]:
        pkg = evony_package(adb, serial, cfg)
        if restarted or app_foreground(adb, serial, pkg) is False:
            log("[*] Starting Evony")
            LOADING.setdefault(serial, ("loading", time.time()))
            start_app(adb, serial, pkg)
            restarted = True
            _nap(stop, 8)
    if wait and (restarted or (check_ready and cfg["auto_launch_game"])):
        return wait_stable(dev, cfg, log, stop, pkg)       # also when Evony was already open but still loading
    return not stop.is_set()


_HEAVY_SLOTS = 2                           # emulators that may restart at once (the boot itself is also spaced by _launch_lock)
_HEAVY_MAX_WAIT = 30                       # owner 25 Oct: never wait more than 30 s for another emulator - a long wait = missed rallies
_HEAVY_HOLD = set()                        # serials holding a slot
_HEAVY_QUEUE = []                          # serials waiting, first come first served
_HEAVY_COND = threading.Condition()


def heavy_release(serial):
    """Give the slot back. Safe to call twice (once when the emulator is up, again when the restart ends)."""
    with _HEAVY_COND:
        _HEAVY_HOLD.discard(serial)
        _HEAVY_COND.notify_all()


def heavy_acquire(serial, stop, log=None):
    """Take one of the emulator-restart slots, in the order the instances asked. Waits at most _HEAVY_MAX_WAIT s,
    then goes anyway (25 Oct: Rory and GK-P waited 10+ min behind Rob's 5-min game load). A holder asking again
    gets it at once (24 Oct deadlock). -> False only when stopped while waiting."""
    with _HEAVY_COND:
        if serial in _HEAVY_HOLD:
            return True
        if not _HEAVY_QUEUE and len(_HEAVY_HOLD) < _HEAVY_SLOTS:
            _HEAVY_HOLD.add(serial)
            return True
        _HEAVY_QUEUE.append(serial)
    if log:
        log("[*] Another emulator is restarting - waiting for my turn (30 s at most)")
    LOADING[serial] = ("waiting", time.time())
    end = time.time() + _HEAVY_MAX_WAIT
    try:
        with _HEAVY_COND:
            while not stop.is_set():
                if _HEAVY_QUEUE and _HEAVY_QUEUE[0] == serial and len(_HEAVY_HOLD) < _HEAVY_SLOTS:
                    _HEAVY_HOLD.add(serial)
                    return True
                if time.time() >= end:
                    if log:
                        log("[*] Waited 30 s for the other restart - going ahead now")
                    return True                    # not counted as a slot: the next one in line is not held up by us
                _HEAVY_COND.wait(1)
            return False
    finally:
        with _HEAVY_COND:
            if serial in _HEAVY_QUEUE:
                _HEAVY_QUEUE.remove(serial)
            _HEAVY_COND.notify_all()
        if LOADING.get(serial, ("",))[0] == "waiting":
            LOADING[serial] = ("restart", time.time())


def restart_emulator(dev, cfg, log, stop, why=""):
    """Close a stuck emulator (nicely first, then by force), start it again and wait until Evony is loaded."""
    pause_gate()                                                  # never restart an emulator while it is paused
    if not heavy_acquire(dev.serial, stop, log):
        return False
    try:
        return _restart_emulator(dev, cfg, log, stop, why)
    finally:
        heavy_release(dev.serial)


def _restart_emulator(dev, cfg, log, stop, why=""):
    adb, serial = dev.adb, dev.serial
    log(f"[!] Restarting the emulator ({why})")
    cmd = emulator_stop_command(serial, adb, cfg)
    if cmd:
        try:
            subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, timeout=40, creationflags=NOWIN)
        except Exception as e:
            log(f"[!] Stop command failed: {e}")
        for _ in range(8):                                  # give it a few seconds to go away
            if stop.is_set():
                return False
            if not device_online(adb, serial):
                break
            stop.wait(3)
    if device_online(adb, serial):
        kill_listener(serial, log)
        stop.wait(4)
    try:
        subprocess.run([adb, "disconnect", serial], capture_output=True, timeout=10, creationflags=NOWIN)
    except Exception as _e:
        quiet_err("_restart_emulator", _e)
    if stop.is_set():
        return False
    stop.wait(5)
    return bring_online(dev, cfg, log, stop, check_ready=True)


_RALLY_SEEN = {}          # serial -> last time that instance saw rally cards (green countdown bars) on its list


_RALLY_ENDS = {}          # serial -> [(monster, end time, seen at)] rallies read on its list (last hour)
_ALLIES = {}              # frozenset({serial, serial}) -> last time both saw the same rally = same alliance


_RALLY_LEVEL = {}         # (monster, end time // 15) -> level read on the card (so a missed-rally check respects level rules)


def note_rally(serial, mon, left, level=None):
    """Remember a rally this instance read; two instances that read the same rally (monster + end time) share an alliance."""
    try:
        now = time.time()
        end = now + float(left)
    except Exception:
        return
    mine = [r for r in _RALLY_ENDS.get(serial, []) if now - r[2] < 3600][-60:]
    mine.append((mon, end, now))
    if level:
        _RALLY_LEVEL[(mon, round(end / 15))] = level
        if len(_RALLY_LEVEL) > 500:
            _RALLY_LEVEL.clear()
    _RALLY_ENDS[serial] = mine
    for other, rows in list(_RALLY_ENDS.items()):
        if other != serial and any(m == mon and abs(e - end) <= 12 and now - t < 3600 for m, e, t in rows):
            _ALLIES[frozenset((serial, other))] = now


def missed_rallies(serial, cfg):
    """Rallies (monster, seconds left) that other instances in the same alliance read in the last 90 s, still open
    for more than 45 s, which this instance never read - its list is out of date. Monsters this instance does not
    join are ignored. No alliance match yet: any other instance counts."""
    now = time.time()
    allies = {s for pair in list(_ALLIES) if serial in pair for s in pair if s != serial}   # owner 25 Oct: only the same alliance -
    # instances on other servers (or not matched yet) see other rallies; comparing with them made lone instances restart for nothing
    mine = _RALLY_ENDS.get(serial, [])
    out, seen = [], set()
    for other in allies:
        for m, e, t in list(_RALLY_ENDS.get(other, [])):
            if now - t > 90 or e - now < 45 or (m, round(e / 15)) in seen:
                continue
            if any(mm == m and abs(ee - e) <= 12 for mm, ee, _ in mine):
                continue
            try:
                lv = _RALLY_LEVEL.get((m, round(e / 15))) or _RALLY_LEVEL.get((m, round(e / 15) - 1)) or _RALLY_LEVEL.get((m, round(e / 15) + 1))
                if not monster_allowed(m, cfg, lv)[0]:
                    continue
            except Exception as _e:
                quiet_err("missed_rallies", _e)
            seen.add((m, round(e / 15)))
            out.append((m, int(e - now)))
    return out


def others_see_rallies(serial, within=150, allies_only=True):
    """True when an instance known to be in the same alliance saw rallies on its list in the last `within` seconds.
    Instances in another alliance (or not matched yet) do not count: their list can be busy while this one is truly quiet.
    allies_only=False: any other instance counts (an instance whose list never loaded cannot be matched to its alliance)."""
    now = time.time()
    allies = {s for pair in list(_ALLIES) if serial in pair for s in pair if s != serial}
    return any((s in allies if allies_only else s != serial) and now - t < within for s, t in list(_RALLY_SEEN.items()))


# ------------------------------------------------------------ alliance chat check (owner 25 Oct)
# A rally list can stop updating while the game looks fine. Every rally posts "<name> started an Alliance War: LvN
# <Monster>(K:.. X:..,Y:..) Join Now / N secs ago" in the Alliance chat tab (unless an alliance hides war messages),
# so the chat tells whether rallies are really happening - for any user, any server, with or without other instances.
CHAT_BAR = (200, 910)          # the chat line at the bottom of the city / map view (same place on both)
CHAT_ALLIANCE_TAB = (338, 94)  # chat tabs: 4 equal tabs, Alliance = 2nd from the right (3rd only during Battlefield)
CHAT_STATE = {}                # serial -> {"last", "nowar", "off", "broken", "noally"}
_AGO = re.compile(r"(\d{1,3})?\s*(sec|secs|second|seconds|min|mins|minute|minutes|hour|hours|day|days)\s*ago", re.I)


def _chat_age(text):
    """'19 secs ago' -> 19. The number is sometimes lost by OCR on the yellow bubbles ('min ago'): then a middle guess."""
    m = _AGO.search(text or "")
    if not m:
        return None
    u = m.group(2).lower()
    unit = 1 if u.startswith("sec") else 60 if u.startswith("min") else 3600 if u.startswith("hour") else 86400
    n = int(m.group(1)) if m.group(1) else (30 if unit == 1 else 1 if not u.endswith("s") else 2)
    return n * unit


def read_alliance_chat(dev, cfg, log, stop):
    """Open chat > Alliance tab, read it, close it. -> ("noally", None) | ("ok", [(monster, age_s), ...] war messages,
    newest last) | ("fail", why)."""
    import difflib
    if not ensure_view(dev, cfg, stop, None, "city") and not ensure_view(dev, cfg, stop, None, "landscape"):
        return "fail", "could not reach the city or map view"
    dev.tap(*CHAT_BAR)
    _nap(stop, 1.5, 2.0)
    try:
        shot = dev.screenshot()
        if not find_text(shot, ["Chat"], region=(150, 5, 240, 60), cutoff=0.7):
            return "fail", "the chat did not open"
        if not find_text(shot, ["Alliance"], region=(180, 65, 360, 60), cutoff=0.7):
            dev.tap(*CHAT_ALLIANCE_TAB)
            _nap(stop, 1.2, 1.6)
            shot = dev.screenshot()
            if not find_text(shot, ["Alliance"], region=(180, 65, 360, 60), cutoff=0.7):
                try:
                    cv2.imwrite(str(USER / "debug" / f"chat_check_fail_{re.sub(r'[^0-9A-Za-z]', '_', dev.serial)}.png"), shot)
                except Exception as _e:
                    quiet_err("read_alliance_chat", _e)
                return "fail", "the Alliance chat tab was not found"
        if find_text(shot, ["join an alliance", "join alliance", "not in an alliance"], region=(0, 100, 540, 800), cutoff=0.75):
            return "noally", None
        lines = sorted(ocr_lines(shot[100:880]), key=lambda l: min(w[2] for w in l[1]))
        wars, pending = [], None
        for text, words in lines:
            low = text.lower()
            if "alliance war" in low or "started an" in low:
                pending = [None, None]
            if pending is not None and pending[0] is None:
                names = MONSTER_NAMES + [n for grp in MONSTER_CATALOG for n, _ in grp[1]]
                exact = [n for n in names if n.lower() in low]
                best = max(exact, key=len) if exact else None
                if not best:                                 # the light-blue part of a name can be lost ('Pumpkin' for Pumpkin Monster)
                    firsts = [n for n in names if len(n.split()) > 1 and re.search(r"\b" + re.escape(n.split()[0].lower()) + r"\b", low)]
                    best = firsts[0] if len(firsts) == 1 else None
                if not best:
                    sc = {n: difflib.SequenceMatcher(None, n.lower(), low).find_longest_match(0, len(n), 0, len(low)).size / len(n)
                          for n in names if len(n) >= 4}
                    cand = max(sc, key=sc.get) if sc else None
                    best = cand if cand and sc[cand] >= 0.85 else None
                if best:
                    pending[0] = best
            age = _chat_age(text)
            if age is not None and pending is not None:
                wars.append((pending[0] or "?", age))
                pending = None
        return "ok", wars
    finally:
        dev.back()                                           # Back closes the chat page (never pressed on the city itself)
        _nap(stop, 1.0, 1.4)


def chat_war_check(dev, cfg, log, stop, serial):
    """-> "broken" (wars started in chat that this list never showed / not in an alliance), "quiet", "ok" or "skip"."""
    st = CHAT_STATE.setdefault(serial, {"last": 0.0, "nowar": 0, "off": False, "broken": 0, "noally": 0})
    now = time.time()
    st["last"] = now
    res, wars = read_alliance_chat(dev, cfg, log, stop)
    if res == "fail":
        log(f"[i] Chat check: {wars} - skipped")
        return "skip"
    if res == "noally":
        st["noally"] += 1
        log(f"[!] Chat check: the Alliance chat says to join an alliance ({st['noally']}x in a row)")
        return "broken"
    st["noally"] = 0
    if not wars:
        st["nowar"] += 1
        if st["nowar"] >= 6 and now - _RALLY_SEEN.get(serial, 0) < 3600:   # joins rallies but chat never shows a war: hidden
            st["off"] = True
            log("[i] Chat check: this alliance's chat never shows rallies - chat check turned off for this instance")
        else:
            log("[i] Chat check: no rally messages in the Alliance chat - quiet time")
        return "quiet"
    st["nowar"] = 0
    seen = _RALLY_SEEN.get(serial, 0)
    # a rally started 30 s - 4 min ago is still open: the list must have shown rally cards since it started
    missed = [(m, a) for m, a in wars if 30 <= a <= 240 and seen < now - a]
    if missed:
        names = ", ".join(sorted({m for m, _ in missed}))
        log(f"[!] Chat check: the Alliance chat shows rallies started {min(a for _, a in missed)}-{max(a for _, a in missed)}s ago "
            f"({names}) but this rally list showed none - the game is not updating")
        return "broken"
    recent = [w for w in wars if w[1] <= 240]
    log("[i] Chat check: " + (f"{len(recent)} recent rally message(s), the list showed rallies - all fine" if recent
                              else "no recent rally in the Alliance chat - quiet time"))
    return "ok" if recent else "quiet"



_GAME_RESTARTS = {}        # serial -> times Evony was restarted (last hour)
_ON_LIST = {}              # serial -> last time the instance was on its rally list


def force_restart_game(dev, cfg, log, stop, must=False):
    """Close Evony completely, open it again and wait until it is stable (used when the bot is lost).
    Owner 25 Oct: restarts must be rare (73 in one morning; each costs minutes and loads the PC -> more lag -> more
    restarts). At most game_restarts_per_hour (2) per instance unless `must` (frozen game, ring menu, asked from the
    interface); over that the bot lets the game settle and carries on the soft way (reopen the list, recovery).
    A really stuck instance is still caught by lost/stuck_seconds (emulator restart) and the watchdog."""
    now = time.time()
    hist = [t for t in _GAME_RESTARTS.get(dev.serial, []) if now - t < 3600]
    cap = int(cfg.get("game_restarts_per_hour", 2) or 0)
    seen = _RALLY_SEEN.get(dev.serial, 0)
    idle_max = int(cfg.get("restart_skip_max_idle", 180) or 0)
    # Working = on its rally list lately AND not missing rallies its own alliance sees. An instance with no ally running
    # (alone on its server) is judged only by itself: on the list = fine, even when the list is empty (quiet time).
    on_list = now - _ON_LIST.get(dev.serial, 0) < idle_max
    working = on_list and (now - seen < idle_max or not others_see_rallies(dev.serial, idle_max))
    if not must and cap and len(hist) >= cap and working:
        _GAME_RESTARTS[dev.serial] = hist
        log(f"[i] Not restarting Evony ({len(hist)} restarts this hour already, the list showed rallies "
            f"{now - seen:.0f}s ago) - letting the game settle and trying again the soft way")
        wait_calm(dev, cfg, log, stop, 20)
        return not stop.is_set()
    if not must and cap and len(hist) >= cap:              # owner 25 Oct: the budget must never cost an hour of rallies
        log(f"[!] {len(hist)} Evony restarts this hour already, but " + ("its alliance sees rallies this list does not show" if on_list
            else "it has not been on the rally list for 3 min") + " - restarting anyway")
    _GAME_RESTARTS[dev.serial] = hist + [now]
    pkg = evony_package(dev.adb, dev.serial, cfg)
    log("[!] Lost for too long - restarting Evony")
    LOADING[dev.serial] = ("restart", time.time())
    _adb(dev.adb, dev.serial, "shell", "am", "force-stop", pkg)
    _nap(stop, 3, 4)
    start_app(dev.adb, dev.serial, pkg)                   # a game restart is light: it never waits for other emulators (25 Oct)
    _nap(stop, 8)
    if wait_stable(dev, cfg, log, stop, pkg, allow_emu_restart=False) or stop.is_set():
        return not stop.is_set()
    return restart_emulator(dev, cfg, log, stop, "Evony did not load after a game restart")


# ---------------------------------------------------------------- one device
# ------------------------------------------------------- Telegram account link (boss coordinates)
TG_SESSION = USER / "tg_session"         # Telethon adds ".session"; this file IS your Telegram login - keep it private
BOSS_FILE = USER / "bosses.json"
_BOSS_RE = re.compile(r"(?:Lv\.?\s*(\d+)\s+)?([A-Za-z][A-Za-z' \-]{1,30}?)\s*\(\s*xy:?\s*(\d{1,4})\s*[ ,]\s*(\d{1,4})\s*\)\s*(?:(\d+(?:\.\d+)?)\s*km)?",
                      re.I)
_bosses = []
_boss_lock = threading.Lock()


def parse_bosses(text):
    """'Lv7 Witch (xy: 719 539) 94km' lines -> [{level, name, x, y, km}, ...]. Lines without 'xy:' are ignored."""
    out = []
    for line in str(text).splitlines():
        m = _BOSS_RE.search(line)
        if not m:
            continue
        lvl, name, x, y, km = m.groups()
        name = re.sub(r"^(?:Lv\.?\s*\d+\s*)", "", name).strip()
        out.append({"level": int(lvl) if lvl else None, "name": name, "x": int(x), "y": int(y),
                    "km": float(km) if km else None})
    return out


def set_bosses(items, source=""):
    with _boss_lock:
        _bosses[:] = items
        try:
            save_json(BOSS_FILE, {"updated": int(time.time()), "source": source, "items": items}, indent=1)
        except Exception as _e:
            quiet_err("set_bosses", _e)


FEED_FILE = USER / "coords_feed.json"
_feed, _feed_stats = [], {"messages": 0, "with_coords": 0, "chats": [], "last": 0}
_feed_lock = threading.Lock()


LIB_FILE = USER / "coords_library.json"      # every coordinate ever seen; never removed by the timer (only by "Clear library")
_lib, _lib_loaded = [], [False]


def _lib_load():
    if not _lib_loaded[0]:
        _lib_loaded[0] = True
        try:
            _lib[:] = json.loads(LIB_FILE.read_text(encoding="utf-8")).get("items", [])
        except Exception as _e:
            quiet_err("_lib_load", _e)


def lib_add(items, chat, now):
    """Remember every coordinate for good: same name + X,Y only refreshes 'last seen' and counts."""
    with _feed_lock:
        _lib_load()
        for it in items:
            for f in _lib:
                if f["x"] == it["x"] and f["y"] == it["y"] and f.get("name") == it.get("name"):
                    f["last"] = now
                    f["n"] = f.get("n", 1) + 1
                    if it.get("level"):
                        f["level"] = it["level"]
                    break
            else:
                _lib.insert(0, {"name": it.get("name"), "level": it.get("level"), "x": it["x"], "y": it["y"], "km": it.get("km"),
                                "chat": chat, "first": now, "last": now, "n": 1})
        del _lib[5000:]
        try:
            save_json(LIB_FILE, {"items": _lib}, indent=None)
        except Exception as _e:
            quiet_err("lib_add", _e)


def lib_get(name=None, min_level=0):
    """For the bot (kill-a-monster tasks): every saved coordinate, newest first; filter by monster name / level."""
    with _feed_lock:
        _lib_load()
        return [dict(f) for f in _lib if (not name or name.lower() in str(f.get("name") or "").lower()) and (f.get("level") or 0) >= min_level]


def lib_clear():
    with _feed_lock:
        _lib[:] = []
        _lib_loaded[0] = True
        LIB_FILE.unlink(missing_ok=True)


_feed_keep = [None]                  # how long (min) the live list keeps a coordinate; set from the settings, 0 = until cleared


def feed_set_keep(keep_min):
    """Change how long coordinates are kept (applies at once, also to the saved list)."""
    _feed_keep[0] = int(keep_min or 0)
    feed_get(_feed_keep[0])


def feed_add(items, chat="", keep_min=60):
    """A chat message with positions: add them to the live list (the same X,Y in the same chat only refreshes its time)."""
    now = time.time()
    if _feed_keep[0] is not None:
        keep_min = _feed_keep[0]
    lib_add(items, chat, now)
    with _feed_lock:
        if keep_min:
            _feed[:] = [f for f in _feed if now - f["t"] <= keep_min * 60]
        for it in items:
            for f in _feed:
                if f["x"] == it["x"] and f["y"] == it["y"] and f.get("chat") == chat and f.get("name") == it.get("name"):
                    f["t"] = now
                    break
            else:
                _feed.insert(0, {**it, "chat": chat, "t": now})
        del _feed[500:]
        try:
            save_json(FEED_FILE, {"items": _feed}, indent=1)
        except Exception as _e:
            quiet_err("feed_add", _e)


def feed_note(chat, had_coords):
    with _feed_lock:
        _feed_stats["messages"] += 1
        _feed_stats["with_coords"] += 1 if had_coords else 0
        _feed_stats["last"] = time.time()


def feed_get(keep_min=60):
    now = time.time()
    with _feed_lock:
        if not _feed and FEED_FILE.exists():
            try:
                _feed[:] = json.loads(FEED_FILE.read_text(encoding="utf-8")).get("items", [])
            except Exception as _e:
                quiet_err("feed_get", _e)
        if keep_min:
            n = len(_feed)
            _feed[:] = [f for f in _feed if now - f["t"] <= keep_min * 60]
            if len(_feed) != n:
                try:
                    save_json(FEED_FILE, {"items": _feed}, indent=1)
                except Exception as _e:
                    quiet_err("feed_get", _e)
        return list(_feed), dict(_feed_stats)


def feed_clear():
    with _feed_lock:
        _feed.clear()
        _feed_stats.update({"messages": 0, "with_coords": 0})
        try:
            FEED_FILE.unlink(missing_ok=True)
        except Exception as _e:
            quiet_err("feed_clear", _e)


def get_bosses():
    with _boss_lock:
        if not _bosses and BOSS_FILE.exists():
            try:
                _bosses[:] = json.loads(BOSS_FILE.read_text(encoding="utf-8")).get("items", [])
            except Exception as _e:
                quiet_err("get_bosses", _e)
        return list(_bosses)


def pick_boss(cfg=None):
    """The boss the tasks use for {boss_x} / {boss_y}: the nearest (or the highest level) of at least tg_min_level."""
    cfg = cfg or {}
    lo = int(cfg.get("tg_min_level", 0) or 0)
    items = [b for b in get_bosses() if (b.get("level") or 0) >= lo]
    km = lambda b: b.get("km") if b.get("km") is not None else 1e9
    if cfg.get("tg_pick", "nearest") == "highest":
        items.sort(key=lambda b: (-(b.get("level") or 0), km(b)))
    else:
        items.sort(key=km)
    return items[0] if items else None


class TgLink:
    """Your own Telegram account (phone-number login) used to talk to a boss-finder bot and read its answers.
    Needs the 'telethon' package and an api_id / api_hash from https://my.telegram.org (API development tools)."""

    def __init__(self, api_id, api_hash, log=print):
        import asyncio
        self.api_id, self.api_hash, self.log = int(api_id), str(api_hash).strip(), log
        self.loop = asyncio.new_event_loop()
        self.client = None
        self.phone = self.code_hash = None
        self.bot = None
        self.on_update = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        import asyncio
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _call(self, coro, timeout=60):
        import asyncio
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    async def _connect(self):
        from telethon import TelegramClient
        if self.client is None:
            self.client = TelegramClient(str(TG_SESSION), self.api_id, self.api_hash)
        if not self.client.is_connected():
            await self.client.connect()
        return await self.client.is_user_authorized()

    def connect(self):
        """-> True if this PC is already logged in."""
        return self._call(self._connect(), 40)

    def send_code(self, phone):
        async def go():
            await self._connect()
            sent = await self.client.send_code_request(phone.strip())
            self.phone, self.code_hash = phone.strip(), sent.phone_code_hash
        self._call(go(), 40)

    def sign_in(self, code=None, password=None):
        """-> 'ok' or 'password' (two-step verification is on: call again with password=...)."""
        async def go():
            from telethon.errors import SessionPasswordNeededError
            try:
                if password is not None:
                    await self.client.sign_in(password=password)
                else:
                    await self.client.sign_in(phone=self.phone, code=str(code).strip(), phone_code_hash=self.code_hash)
            except SessionPasswordNeededError:
                return "password"
            return "ok"
        return self._call(go(), 40)

    async def _resolve(self, name):
        """@username, t.me link, number (chat id) or the chat's name as in your Telegram list -> entity, or None."""
        n = re.sub(r"^https?://t\.me/", "", str(name).strip()).lstrip("@")
        if not n:
            return None
        try:
            return await self.client.get_entity(int(n) if re.fullmatch(r"-?\d+", n) else n)
        except Exception as _e:
            quiet_err("TgLink._resolve", _e)
        async for d in self.client.iter_dialogs():
            if (d.name or "").strip().lower() == n.lower():
                return d.entity
        return None

    def listen_feed(self, chats, on_update=None, keep_min=60):
        """Watch every message (new or edited, also from other bots) of the named chats and add each position in it to the live list."""
        names = [t.strip() for t in re.split(r"[,;\n]+", str(chats)) if t.strip()]
        if not names:
            return []

        async def go():
            from telethon import events
            await self._connect()
            ents, labels = [], []
            for nm in names:
                e = await self._resolve(nm)
                if e is None:
                    self.log(f"[!] Telegram: no chat called '{nm}' - use the name as in your chat list, or @username")
                    continue
                ents.append(e)
                labels.append(getattr(e, "title", None) or getattr(e, "username", None) or str(nm))
            if not ents:
                raise RuntimeError("none of the chats to watch could be found")

            async def handler(event):
                txt = event.raw_text or ""
                items = parse_bosses(txt)
                try:
                    chat = (await event.get_chat())
                    chat = getattr(chat, "title", None) or getattr(chat, "username", None) or getattr(chat, "first_name", "") or ""
                except Exception:
                    chat = ""
                feed_note(chat, bool(items))
                if items:
                    feed_add(items, chat, keep_min)
                if on_update:
                    on_update(items)
            for ev in (events.NewMessage(chats=ents), events.MessageEdited(chats=ents)):
                self.client.add_event_handler(handler, ev)
            _feed_stats["chats"] = labels
            return labels
        return self._call(go(), 60)

    def listen(self, bot, on_update=None):
        """Read every message the bot sends (new or edited) and keep the boss list up to date."""
        self.bot = bot.strip().lstrip("@")
        self.on_update = on_update

        async def go():
            from telethon import events
            await self._connect()

            async def handler(event):
                items = parse_bosses(event.raw_text or "")
                if items:
                    set_bosses(items, source=self.bot)
                    if self.on_update:
                        self.on_update(items)
            for ev in (events.NewMessage(from_users=self.bot), events.MessageEdited(from_users=self.bot)):
                self.client.add_event_handler(handler, ev)
        self._call(go(), 40)

    def fetch(self, command, wait=15):
        """Send `command` to the bot and collect its answers for up to `wait` seconds. -> [boss, ...]"""
        async def go():
            import asyncio
            await self._connect()
            t0 = time.time() - 2
            await self.client.send_message(self.bot, command)
            texts, quiet_since = [], None
            end = time.time() + wait
            while time.time() < end:
                await asyncio.sleep(1.5)
                msgs = await self.client.get_messages(self.bot, limit=8)
                new = [m.raw_text for m in msgs if not m.out and m.date.timestamp() >= t0 and m.raw_text]
                if len(new) > len(texts):
                    texts, quiet_since = new, time.time()
                elif texts and time.time() - quiet_since > 3.5:
                    break
            items = []
            for t in reversed(texts):
                items += parse_bosses(t)
            return items
        items = self._call(go(), wait + 30)
        if items:
            set_bosses(items, source=self.bot)
        return items

    def log_out(self):
        async def go():
            if self.client is not None:
                try:
                    await self.client.log_out()
                except Exception as _e:
                    quiet_err("log_out.go", _e)
        try:
            self._call(go(), 20)
        except Exception as _e:
            quiet_err("TgLink.log_out", _e)
        for f in USER.glob("tg_session*"):
            f.unlink(missing_ok=True)

    def close(self):
        async def go():
            if self.client is not None:
                await self.client.disconnect()
        try:
            self._call(go(), 10)
        except Exception as _e:
            quiet_err("TgLink.close", _e)
        self.loop.call_soon_threadsafe(self.loop.stop)


# ------------------------------------------------------------ city map + tasks
CITY_FILE = USER / "city_map.json"
BUILDING_NAMES = [
    "Keep", "Academy", "Arsenal", "Barracks", "Archer Camp", "Stables", "Garrison", "Warehouse", "Shrine",
    "Embassy", "Victory Column", "Research Factory", "Art Hall", "Market", "Prison", "Tavern",
    "Council of State", "Ideal Land", "Holy Palace", "Trap Factory", "Wisdom Dome", "Bunker", "Rally Spot",
    "Battlefield", "Bounty Cave", "Walls", "Quarry", "Sawmill", "Mine", "Farm", "Subordinate City", "Wonder",
    "Watchtower", "Fountain of the Gods", "Triumphal Arch", "Military Academy", "Pasture", "Hospital",
    "Workshop", "Smithy", "Siege Workshop", "Beacon Tower", "Treasury", "Marketplace",
]
BUILDING_NAMES += ["Stable", "Forge", "War Hall", "Portal", "Black Market", "Dragon Cliff", "Archer Tower"]   # last two read from the PC client / emulator labels
BUILDING_ALIASES = {"Stables": "Stable", "Smithy": "Forge", "Black Market": "Market"}          # the game's wording -> our name
EXPECTED_BUILDINGS = [                                                # what a finished city map must contain
    "Keep", "Rally Spot", "Holy Palace", "Workshop", "Military Academy", "Council of State", "Research Factory",
    "Stable", "Hospital", "Trap Factory", "Walls", "Shrine", "Art Hall", "Arsenal", "Victory Column", "Wisdom Dome",
    "Barracks", "Academy", "Embassy", "Prison", "Watchtower", "Tavern", "Pasture", "Wonder", "Triumphal Arch",
    "Warehouse", "Archer Camp", "Forge", "Market", "War Hall",
]
OPTIONAL_BUILDINGS = ("Military Academy", "Triumphal Arch")           # not every city has them (keep level / not built)
RESOURCE_BUILDINGS = ("Quarry", "Sawmill", "Mine", "Farm")
LABEL_VIEW = (10, 95, 435, 705)                                        # where building names are read (x, y, w, h)
CITY_VIEW = (80, 100, 360, 690)         # x, y, w, h of the map area that is not covered by buttons (540x960)
DEFAULT_CITY_ICONS = {                   # the buttons on the right of the city screen (540x960)
    "Daily event": [491, 105], "Login Gifts": [491, 190], "Follow Us": [491, 260],
    "Event Center": [491, 345], "Event 2": [491, 410],
    "Alliance": [491, 785], "Mail": [491, 855],
}
_CITY_LOCK = threading.Lock()


def load_city():
    try:
        return json.loads(CITY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_city(data):
    CITY_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")


def city_shift(a, b):
    """How far the map content moved between two screenshots -> (dx, dy, confidence)."""
    x, y, w, h = CITY_VIEW
    ga = cv2.cvtColor(a[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY).astype("float32")
    gb = cv2.cvtColor(b[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY).astype("float32")
    win = cv2.createHanningWindow((w, h), cv2.CV_32F)
    (dx, dy), resp = cv2.phaseCorrelate(ga, gb, win)
    return dx, dy, resp


def patch_shift(before, after):
    """Where did the picture go? Three patches of the map area in `before` are looked for in `after`.
    -> (dx, dy, score) of the map content; score < 0.6 = not found (moved too far, or a repeating pattern)."""
    x, y, w, h = CITY_VIEW
    best = (0.0, 0.0, 0.0)
    for px, py, pw, ph in ((x + 80, y + 245, 200, 200), (x + 30, y + 120, 150, 150), (x + 180, y + 420, 150, 150)):
        t = before[py:py + ph, px:px + pw]
        r = cv2.matchTemplate(after[y:y + h, x:x + w], t, cv2.TM_CCOEFF_NORMED)
        _, v, _, l = cv2.minMaxLoc(r)
        if v > best[2]:
            best = (float(l[0] + x - px), float(l[1] + y - py), float(v))
    return best


_ORB = [None]


def feat_shift(before, after):
    """Map shift by feature points: hundreds of distinctive points matched between the two map views; the shift that
    most of them agree on wins (repeating walls / troop rows cannot fool that, a single patch can - Kiravik 23 Oct).
    -> (dx, dy, agreeing points) of the map content, or None."""
    try:
        x, y, w, h = CITY_VIEW
        if _ORB[0] is None:
            _ORB[0] = cv2.ORB_create(2500)
        ga = cv2.cvtColor(before[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY)
        gb = cv2.cvtColor(after[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY)
        ka, da = _ORB[0].detectAndCompute(ga, None)
        kb, db = _ORB[0].detectAndCompute(gb, None)
        if da is None or db is None or len(ka) < 30 or len(kb) < 30:
            return None
        m = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(da, db)
        if len(m) < 30:
            return None
        d = np.float32([kb[q.trainIdx].pt for q in m]) - np.float32([ka[q.queryIdx].pt for q in m])
        best_n, best = 0, None
        for i in range(0, len(d), max(1, len(d) // 400)):   # each shift as a candidate: how many agree within 6 px
            n = int(np.sum(np.all(np.abs(d - d[i]) < 6, axis=1)))
            if n > best_n:
                best_n, best = n, d[i]
        if best_n < 40:
            return None
        inl = d[np.all(np.abs(d - best) < 6, axis=1)]
        return float(inl[:, 0].mean()), float(inl[:, 1].mean()), best_n
    except Exception:
        return None


def safe_shift(before, after, cmd_dx, cmd_dy):
    """-> (dx, dy) of the map content between the two screenshots.
    First by following a patch of the picture (direction-free: at the city's slanted edge the game slides the camera
    ALONG the edge, so a straight drag moves the map sideways - Kiravik 23 Oct 18:47, 180 px). Only when the patch
    is not found: phase correlation, bounded by the finger move; then the finger itself."""
    f = feat_shift(before, after)
    if f:
        return f[0], f[1]
    pdx, pdy, score = patch_shift(before, after)
    if score >= 0.6:
        return pdx, pdy
    dx, dy, resp = city_shift(before, after)
    G = 1.9                                                # the game moves the map up to ~1.5x as far as the finger (measured on a real city): allow it
    ok = (resp >= 0.05
          and min(0, cmd_dx * G) - 30 <= dx <= max(0, cmd_dx * G) + 30
          and min(0, cmd_dy * G) - 30 <= dy <= max(0, cmd_dy * G) + 30)
    if ok:
        return dx, dy
    x, y, w, h = CITY_VIEW
    diff = float(cv2.absdiff(before[y:y + h, x:x + w], after[y:y + h, x:x + w]).mean())
    if diff < 2.0:                                         # the picture did not change: the map did not move (edge)
        return 0.0, 0.0
    return cmd_dx * 1.45, cmd_dy * 1.45                    # it moved but the match is not trustworthy: use the finger (the map follows it ~1.45x)


def match_building(text, cutoff=0.82):
    import difflib
    t = " ".join(text.split()).strip(" .,:;|-_")
    if t.lower() == "inn":                                 # the Tavern is called Inn in one city style
        return "Tavern"
    if len(t) < 4:
        return None
    low = {n.lower(): n for n in BUILDING_NAMES}
    m = difflib.get_close_matches(t.lower(), list(low), n=1, cutoff=cutoff)
    if not m:
        return None
    return BUILDING_ALIASES.get(low[m[0]], low[m[0]])


def ocr_lines(img, scale=2.0, bright=False):
    """OCR with word boxes grouped into lines: [(text, [(word, cx, cy, w, h), ...]), ...] in image pixels.
    bright=True keeps only light pixels (the names are light text on dark pills)."""
    g = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    if bright:
        g = 255 - cv2.threshold(g, 150, 255, cv2.THRESH_BINARY)[1]
    try:
        d = pytesseract.image_to_data(g, config="--psm 11", output_type=pytesseract.Output.DICT)
    except Exception:
        return []
    lines = {}
    for i, w in enumerate(d["text"]):
        w = w.strip()
        if not w or float(d["conf"][i]) < 30:
            continue
        key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
        cx = (d["left"][i] + d["width"][i] / 2) / scale
        cy = (d["top"][i] + d["height"][i] / 2) / scale
        lines.setdefault(key, []).append((w, cx, cy, d["width"][i] / scale, d["height"][i] / scale))
    return [(" ".join(x[0] for x in ws), ws) for ws in lines.values()]


def find_text(img, options, region=None, cutoff=0.8):
    """Find the first of `options` (list of strings, may be several words) on the screen by OCR.
    -> (x, y) centre in full-image coordinates, or None."""
    import difflib
    ox = oy = 0
    if region:
        x, y, w, h = region
        img, ox, oy = img[y:y + h, x:x + w], x, y
    lines = ocr_lines(img)
    for opt in options:
        ow = opt.lower().split()
        if not ow:
            continue
        for _, ws in lines:
            for i in range(len(ws) - len(ow) + 1):
                seg = ws[i:i + len(ow)]
                got = " ".join(s[0] for s in seg).lower().strip(".,:;!")
                if difflib.SequenceMatcher(None, got, " ".join(ow)).ratio() >= cutoff:
                    return (int(sum(s[1] for s in seg) / len(seg)) + ox, int(sum(s[2] for s in seg) / len(seg)) + oy)
    return None


def tap_alliance_war_row(dev, shot, log):
    """On the Alliance main page (rows: Alliance War / Gift / Treasure / Building / Help): tap the 'Alliance War' row.
    -> True if tapped."""
    try:
        row = None
        reg = (60, 330, 420, 440)
        t = find_text(shot, ["Alliance War"], region=reg, cutoff=0.8)
        if t and find_text(shot, ["Alliance Gift", "Alliance Treasure", "Alliance Help", "Alliance Building"], region=reg, cutoff=0.75):
            row = t
        if not row and has_template("war_button"):
            h = find(shot, "war_button", 0.7)
            if h:
                row = (int(h[0]), int(h[1]))
        if row:
            # loop guard (DD 23 Oct: 43 taps in 3.5 min, every 5 s, nothing logged as a problem): 6 taps in 2 min means
            # this is not really the Alliance page or the row does not open - stop tapping so recovery / restart takes over
            now = time.time()
            taps = [t for t in getattr(dev, "war_row_times", []) if now - t < 120]
            if len(taps) >= 6:
                if now - getattr(dev, "war_row_warned", 0) > 120:
                    dev.war_row_warned = now
                    log(f"[!] Tapped the 'Alliance War' row {len(taps)} times in 2 min without reaching the rally list - not tapping it again for now")
                dev.war_row_times = taps
                return False
            dev.war_row_times = taps + [now]
            dev.tap(int(row[0]), int(row[1]))
            log(f"[*] On the Alliance page - tapped the 'Alliance War' row ({int(row[0])},{int(row[1])})")
            return True
    except Exception as _e:
        quiet_err("tap_alliance_war_row", _e)
    return False


def read_building_labels(img):
    """The building names visible on the city screen: [(name, x, y), ...] in screen coordinates."""
    x, y, w, h = LABEL_VIEW
    out = []
    for bright in (False, True):                           # two readings: the plain picture and the light text only
        for text, ws in ocr_lines(img[y:y + h, x:x + w], bright=bright):
            words = [t[0] for t in ws]
            for n in range(min(4, len(words)), 0, -1):      # the longest run of words that is a building name
                hit = None
                for i in range(len(words) - n + 1):
                    name = match_building(" ".join(words[i:i + n]))
                    if name:
                        seg = ws[i:i + n]
                        hit = (name, int(sum(s[1] for s in seg) / n) + x, int(sum(s[2] for s in seg) / n) + y)
                        break
                if hit:
                    if not any(o[0] == hit[0] and abs(o[1] - hit[1]) < 40 and abs(o[2] - hit[2]) < 25 for o in out):
                        out.append(hit)
                    break
    return out


def _view_scale(a, b):
    """How much bigger things look in picture b than in picture a (ORB features) -> scale or None."""
    try:
        ga, gb = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
        orb = cv2.ORB_create(1500)
        ka, da = orb.detectAndCompute(ga[90:700, 60:480], None)
        kb, db = orb.detectAndCompute(gb[90:700, 60:480], None)
        if da is None or db is None:
            return None
        m = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(da, db)
        if len(m) < 12:
            return None
        pa = np.float32([ka[x.queryIdx].pt for x in m])
        pb = np.float32([kb[x.trainIdx].pt for x in m])
        M, inl = cv2.estimateAffinePartial2D(pa, pb, method=cv2.RANSAC, ransacReprojThreshold=4)
        if M is None or inl is None or int(inl.sum()) < 10:
            return None
        return float(np.hypot(M[0, 0], M[1, 0]))
    except Exception:
        return None


def emu_window(dev, name=None):
    """Windows handle of this emulator's game picture (MuMu: MuMuManager info render_wnd; else a window titled with the name)."""
    if os.name != "nt":
        return None
    import ctypes
    u32 = ctypes.windll.user32
    m = re.fullmatch(r"127\.0\.0\.1:(\d+)", dev.serial)
    mgr = mumu_manager(dev.adb) if m and int(m.group(1)) >= 16000 else None
    if mgr:
        try:
            idx = mumu_index(dev.serial, dev.adb, name)
            out = subprocess.run([str(mgr), "info", "-v", str(idx)], capture_output=True, text=True,
                                 timeout=20, creationflags=NOWIN).stdout
            info = json.loads(out)
            for k in ("render_wnd", "main_wnd"):
                v = str(info.get(k) or "")
                if v and int(v, 16) and u32.IsWindow(int(v, 16)):
                    return int(v, 16)
        except Exception as _e:
            quiet_err("emu_window", _e)
    if not name:
        return None
    want, hits = str(name).strip().lower(), []
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(h, _):
        n = u32.GetWindowTextLengthW(h)
        if n and u32.IsWindowVisible(h):
            b = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(h, b, n + 1)
            if b.value.strip().lower() == want:
                hits.append(h)
        return True
    u32.EnumWindows(proto(cb), 0)
    return hits[0] if hits else None


def wheel_zoom_out(dev, log, name=None, notches=6):
    """Ctrl + mouse wheel down sent to the emulator window (owner 23 Oct: zooms the city out on MuMu and BlueStacks).
    Window messages only - the owner's mouse and the window focus are not touched. -> True when sent."""
    h = emu_window(dev, name)
    if not h:
        log("[i] Zoom out: emulator window not found for Ctrl+wheel")
        return False
    import ctypes
    from ctypes import wintypes
    u32 = ctypes.windll.user32
    rc = wintypes.RECT()
    u32.GetWindowRect(h, ctypes.byref(rc))
    cx, cy = (rc.left + rc.right) // 2, (rc.top + rc.bottom) // 2
    lp = ((cy & 0xFFFF) << 16) | (cx & 0xFFFF)
    targets = [h]
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    u32.EnumChildWindows(h, proto(lambda c, _: targets.append(c) or True), 0)
    for t in targets:
        u32.PostMessageW(t, 0x0100, 0x11, 0x001D0001)                  # WM_KEYDOWN Ctrl
    for _ in range(notches):
        for t in targets:
            u32.PostMessageW(t, 0x020A, ((-120 & 0xFFFF) << 16) | 0x0008, lp)   # WM_MOUSEWHEEL down + MK_CONTROL
        time.sleep(0.12)
    for t in targets:
        u32.PostMessageW(t, 0x0101, 0x11, 0xC01D0001)                  # WM_KEYUP Ctrl
    log(f"[i] Zoom out: Ctrl+wheel x{notches} sent to window {h:#x} ({len(targets)} parts)")
    return True


_INPUT_LOCK = threading.Lock()


def user_idle_s():
    """Seconds since the owner last touched the keyboard or mouse on this PC (Windows), or a large number elsewhere."""
    try:
        import ctypes
        from ctypes import wintypes

        class LII(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
        li = LII()
        li.cbSize = ctypes.sizeof(LII)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
            return max(0.0, (ctypes.windll.kernel32.GetTickCount() - li.dwTime) / 1000.0)
    except Exception:
        pass
    return 1e9


def real_wheel_zoom_out(dev, log, name=None, notches=6, up=False, stop=None):
    """Real Ctrl + wheel down over the emulator window (SendInput): brings the window to the front for ~2 s, moves the
    mouse over it, scrolls, puts the mouse back. Used when window messages are ignored (MuMu, 23 Oct). -> True when sent.
    Never while the owner is using the PC (24 Oct 15:05: the emulator window popped up and the mouse moved under his
    hands): waits up to 2 min for 20 s without keyboard / mouse input, else gives up."""
    h = emu_window(dev, name)
    if not h:
        return False
    waited = 0.0
    while user_idle_s() < 20 and waited < 120:
        if stop is not None and stop.is_set():
            return False
        time.sleep(3)
        waited += 3
    if user_idle_s() < 20:
        log("[i] Zoom: you are using the PC - the bot leaves the mouse alone (zoom with the real wheel skipped)")
        return False
    import ctypes
    from ctypes import wintypes
    u32 = ctypes.windll.user32
    top = h
    for _ in range(4):                                    # the render window is a child: bring its top window forward
        p = u32.GetParent(top)
        if not p:
            break
        top = p

    class MI(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class KI(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]

    class U(ctypes.Union):
        _fields_ = [("mi", MI), ("ki", KI), ("pad", ctypes.c_byte * 32)]

    class INP(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", U)]

    def send(*items):
        arr = (INP * len(items))(*items)
        u32.SendInput(len(items), arr, ctypes.sizeof(INP))

    def key(vk, up=False):
        i = INP(type=1)
        i.u.ki = KI(vk, 0, 2 if up else 0, 0, 0)
        return i

    def wheel(d):
        i = INP(type=0)
        i.u.mi = MI(0, 0, d & 0xFFFFFFFF, 0x0800, 0, 0)
        return i
    with _INPUT_LOCK:                                     # one emulator at a time: the mouse is shared
        rc = wintypes.RECT()
        u32.GetWindowRect(h, ctypes.byref(rc))
        cx, cy = (rc.left + rc.right) // 2, (rc.top + rc.bottom) // 2
        old = wintypes.POINT()
        u32.GetCursorPos(ctypes.byref(old))
        prev = u32.GetForegroundWindow()
        u32.ShowWindow(top, 9)                            # SW_RESTORE (does nothing when not minimised)
        send(key(0x12), key(0x12, True))                  # Alt tap: Windows then allows SetForegroundWindow
        u32.SetForegroundWindow(top)
        u32.SetWindowPos(top, -1, 0, 0, 0, 0, 0x0043)     # HWND_TOPMOST for the scroll: other windows may cover it (PC 23 Oct)
        time.sleep(0.4)
        lowered = []
        for _ in range(3):                                # windows in front of it (e.g. the RallyForge window): minimise them for the scroll
            cov = u32.GetAncestor(u32.WindowFromPoint(wintypes.POINT(cx, cy)), 2)
            if not cov or cov == top or u32.IsChild(top, cov) or u32.GetAncestor(h, 2) == cov:
                break
            u32.ShowWindow(cov, 6)                        # SW_MINIMIZE the window in front of the emulator for the scroll
            lowered.append(cov)
            time.sleep(0.4)
        cov = u32.WindowFromPoint(wintypes.POINT(cx, cy))
        if cov not in (h, top) and not u32.IsChild(top, cov):
            def _wn(w):
                b, c = ctypes.create_unicode_buffer(200), ctypes.create_unicode_buffer(200)
                u32.GetWindowTextW(w, b, 200)
                u32.GetClassNameW(w, c, 200)
                return f"{b.value!r}/{c.value}"
            ct = u32.GetAncestor(cov, 2) or cov
            log(f"[i] Zoom out: window {_wn(top)} rect {rc.left},{rc.top},{rc.right},{rc.bottom} is covered by {_wn(ct)} - scrolling anyway")
        u32.SetCursorPos(cx, cy)
        time.sleep(0.15)
        send(key(0x11))
        for _ in range(notches):
            send(wheel(120 if up else -120))
            time.sleep(0.12)
        send(key(0x11, True))
        time.sleep(0.3)
        u32.SetCursorPos(old.x, old.y)
        u32.SetWindowPos(top, -2, 0, 0, 0, 0, 0x0013)     # HWND_NOTOPMOST again, no move/size/activate
        for w in reversed(lowered):
            u32.ShowWindow(w, 4)                          # SW_SHOWNOACTIVATE: back as it was, without taking focus
        if prev:
            u32.SetForegroundWindow(prev)
    log(f"[i] Zoom {'in' if up else 'out'}: real Ctrl+wheel x{notches} over the emulator window ({cx},{cy})" + (f", {len(lowered)} window(s) in front minimised for it" if lowered else ""))
    return True


def zoom_out_city(dev, stop, log, max_tries=3):
    """Zoom the city view out with a two-finger pinch (done once per emulator session). -> True when the view got wider."""
    if getattr(dev, "zoomed_out", False):
        return True
    try:                                                  # a city with a keyframe map: the screen locating on it means the
        cmap = city_map(dev)                              # city IS at the map's zoom (the widest) - nothing to do, no mouse
        if cmap is not None and cmap.locate(dev.screenshot()):
            dev.zoomed_out = True
            return True
    except Exception as _e:
        quiet_err("zoom_out_city", _e)
    ok_any = False
    lab = {}
    try:                                                  # developer aid: %APPDATA%\\RallyForgeBot\\pinch_lab.json
        lab = json.loads((USER / "pinch_lab.json").read_text(encoding="utf-8"))
    except Exception:
        lab = {}
    if lab.get("shell") and lab.get("on"):
        for c in ([lab["shell"]] if isinstance(lab["shell"], str) else lab["shell"]):
            r = dev._run("shell", "sh", "-c", "'" + c + "'", timeout=40)
            out = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "ignore")
            for i in range(0, min(len(out), 3000), 300):
                log(f"[i] lab> {out[i:i + 300]!r}")
    kw = lab.get("params") or {}
    for vi, var in enumerate((lab.get("variants") if lab.get("on") else None) or []):
        b = dev.screenshot()
        ok = dev.pinch_in(**var)
        _nap(stop, 1.5, 1.8)
        a = dev.screenshot()
        sc = _view_scale(b, a)
        log(f"[i] lab variant {vi} {var}: sent={ok} scale={sc} {getattr(dev, 'last_pinch', '')}")
    name = (load_config().get("device_names") or {}).get(dev.serial)
    for i in range(2):                                    # first way: Ctrl + wheel down on the emulator window
        before = dev.screenshot()
        if not wheel_zoom_out(dev, log, name):
            break
        _nap(stop, 1.2, 1.5)
        sc = _view_scale(before, dev.screenshot())
        log(f"[i] Zoom out (Ctrl+wheel) {i + 1}: view scale {sc:.2f}" if sc else f"[i] Zoom out (Ctrl+wheel) {i + 1}: scale not measurable")
        if sc and sc < 0.93:
            ok_any = True
        elif sc and sc >= 0.97:
            break
    if not ok_any and load_config().get("zoom_foreground", False):     # window messages ignored: real mouse + keyboard (opt-in)
        for i in range(2):
            before = dev.screenshot()
            if not real_wheel_zoom_out(dev, log, name, stop=stop):
                break
            _nap(stop, 1.2, 1.5)
            sc = _view_scale(before, dev.screenshot())
            log(f"[i] Zoom out (real Ctrl+wheel) {i + 1}: view scale {sc:.2f}" if sc else f"[i] Zoom out (real Ctrl+wheel) {i + 1}: scale not measurable")
            if sc and sc < 0.93:
                ok_any = True
            elif sc and sc >= 0.97:
                break
        if not ok_any:                                    # nothing changed: already as far out as it goes, or the input is ignored?
            before = dev.screenshot()
            if real_wheel_zoom_out(dev, log, name, notches=2, up=True, stop=stop):
                _nap(stop, 1.2, 1.5)
                sc = _view_scale(before, dev.screenshot())
                if sc and sc > 1.03:
                    log(f"[+] Zoom: zooming IN works (scale {sc:.2f}) - the city was already fully zoomed out; zooming back out")
                    real_wheel_zoom_out(dev, log, name, notches=6, stop=stop)
                    _nap(stop, 1.0, 1.3)
                    ok_any = True
                else:
                    log(f"[!] Zoom: zooming in did nothing either (scale {sc:.2f}) - the emulator ignores the Ctrl+wheel from the bot" if sc
                        else "[!] Zoom: zoom-in check not measurable")
    if ok_any:
        dev.zoomed_out = True
        return True
    for i in range(max_tries):
        if stop.is_set():
            return False
        before = dev.screenshot()
        sent = dev.pinch_in(**kw)
        log(f"[i] Zoom out: {getattr(dev, 'last_pinch', 'no touch device found')}")
        if not sent:
            log("[!] Zoom out: this emulator did not accept the two-finger pinch (no touch device or sendevent refused)")
            return False
        _nap(stop, 1.2, 1.5)
        after = dev.screenshot()
        sc = _view_scale(before, after)
        log(f"[i] Zoom out pinch {i + 1}: view scale {sc:.2f}" if sc else f"[i] Zoom out pinch {i + 1}: scale not measurable")
        if sc and sc < 0.93:
            ok_any = True
            if sc > 0.97:
                break
        elif sc and sc >= 0.97:
            break                                        # nothing changed: already as far out as it goes (or pinch ignored)
    dev.zoomed_out = ok_any
    return ok_any


class FlowRecorder:
    """Developer tool: records the owner playing a flow by hand (taps, long presses, swipes read from the emulator's
    touch events) with a small screenshot before and after each step, so a duty can be written from it later.
    Saved in USER/flows/<name>/: steps.json + NN_before.jpg (tap marked) + NN_after.jpg."""

    def __init__(self, serial, adb, name, log=print):
        self.serial, self.adb, self.log = serial, adb, log
        safe = re.sub(r"[^A-Za-z0-9 _-]", "", name).strip().replace(" ", "_") or "flow"
        self.dir = USER / "flows" / f"{time.strftime('%Y%m%d-%H%M%S')}_{safe}"
        self.name, self.steps, self.error = name, [], ""
        self.stop_ev = threading.Event()
        self.proc = None
        self.frames = []                     # (time, small image) - the last few seconds, for "before" pictures
        self.lock = threading.Lock()
        self.dev = None
        self.maxx = self.maxy = 0
        self.t0 = time.time()

    def _touch_device(self):
        out = subprocess.run([self.adb, "-s", self.serial, "shell", "getevent", "-pl"], capture_output=True,
                             text=True, timeout=15, creationflags=NOWIN).stdout
        best = None
        for block in re.split(r"\nadd device \d+: ", "\n" + out):
            m = re.match(r"\s*(\S+)", block)
            mx = re.search(r"ABS_MT_POSITION_X\s*:.*?max (\d+)", block)
            my = re.search(r"ABS_MT_POSITION_Y\s*:.*?max (\d+)", block)
            if m and mx and my:
                best = (m.group(1), int(mx.group(1)), int(my.group(1)))
                break
        if not best:
            raise RuntimeError("No touch screen found on this emulator (getevent shows no ABS_MT_POSITION).")
        return best

    def start(self):
        self.dev = Device(self.serial, self.adb)
        self.dev.connect()
        self.dev._lag_busy = True            # record the screen as it is: no lag waiting, no pop-up closing
        node, self.maxx, self.maxy = self._touch_device()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen([self.adb, "-s", self.serial, "shell", "getevent", "-lt", node],
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                     creationflags=NOWIN, bufsize=1)
        threading.Thread(target=self._frames_loop, daemon=True).start()
        threading.Thread(target=self._events_loop, daemon=True).start()
        self.log(f"[*] Recording flow '{self.name}' on {self.serial} - play it in the game now")

    def _frames_loop(self):
        while not self.stop_ev.is_set():
            try:
                img = cv2.resize(self.dev.screenshot(), (270, 480), interpolation=cv2.INTER_AREA)
                with self.lock:
                    self.frames.append((time.time(), img))
                    self.frames = self.frames[-8:]
            except Exception as e:
                self.error = f"screenshot: {e}"
            self.stop_ev.wait(0.6)

    def _to_screen(self, rx, ry):
        w, h = EXPECTED_SIZE
        if self.maxx > self.maxy:            # touch panel reported in landscape: swap to the portrait screen
            rx, ry, mx, my = ry, rx, self.maxy, self.maxx
        else:
            mx, my = self.maxx, self.maxy
        return round(rx * w / max(mx, 1)), round(ry * h / max(my, 1))

    def _events_loop(self):
        x = y = None                         # latest raw finger position
        touching, start, ended = False, None, False
        for line in self.proc.stdout:
            if self.stop_ev.is_set():
                break
            parts = line.replace("[", " ").replace("]", " ").split()
            if len(parts) < 3:
                continue
            code, val = parts[-2], parts[-1]
            try:
                if code == "ABS_MT_POSITION_X":
                    x = int(val, 16)
                elif code == "ABS_MT_POSITION_Y":
                    y = int(val, 16)
                elif code in ("BTN_TOUCH", "ABS_MT_TRACKING_ID"):
                    up = val == "UP" or val.lower() == "ffffffff"
                    if up and touching:
                        ended = True
                    elif not up:
                        touching = True
                elif code == "SYN_REPORT":
                    if touching and start is None and x is not None and y is not None:
                        start = (time.time(), x, y)
                    if ended:
                        if start is not None:
                            self._step(start, (time.time(), x, y))
                        touching, start, ended = False, None, False
            except ValueError:
                continue

    def _step(self, down, up):
        t1, rx1, ry1 = down
        t2, rx2, ry2 = up
        x1, y1 = self._to_screen(rx1, ry1)
        x2, y2 = self._to_screen(rx2, ry2)
        dist, held = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5, t2 - t1
        kind = "swipe" if dist > 25 else ("long_press" if held > 0.8 else "tap")
        n = len(self.steps) + 1
        with self.lock:
            before = [f for f in self.frames if f[0] <= t1 - 0.1]
            img = (before[-1] if before else (self.frames[0] if self.frames else (0, None)))[1]
        step = {"n": n, "kind": kind, "x": x1, "y": y1, "at_s": round(t1 - self.t0, 1), "held_s": round(held, 2)}
        if kind == "swipe":
            step.update(x2=x2, y2=y2)
        if img is not None:
            pic = img.copy()
            cv2.circle(pic, (x1 // 2, y1 // 2), 9, (0, 0, 255), 2)
            if kind == "swipe":
                cv2.arrowedLine(pic, (x1 // 2, y1 // 2), (x2 // 2, y2 // 2), (0, 0, 255), 2)
            cv2.imwrite(str(self.dir / f"{n:02d}_before.jpg"), pic, [cv2.IMWRITE_JPEG_QUALITY, 70])
        self.steps.append(step)

        def after():
            time.sleep(1.5)
            try:
                a = cv2.resize(self.dev.screenshot(), (270, 480), interpolation=cv2.INTER_AREA)
                cv2.imwrite(str(self.dir / f"{n:02d}_after.jpg"), a, [cv2.IMWRITE_JPEG_QUALITY, 70])
            except Exception as _e:
                quiet_err("_step.after", _e)
        threading.Thread(target=after, daemon=True).start()
        self.log(f"[*] Flow step {n}: {kind} at {x1},{y1}" + (f" -> {x2},{y2}" if kind == "swipe" else ""))
        self._save()

    def _save(self):
        data = {"name": self.name, "serial": self.serial, "screen": list(EXPECTED_SIZE), "touch_max": [self.maxx, self.maxy],
                "recorded": time.strftime("%Y-%m-%d %H:%M"), "version": VERSION, "steps": self.steps}
        save_json(self.dir / "steps.json", data, indent=1)

    def stop(self):
        self.stop_ev.set()
        try:
            if self.proc:
                self.proc.kill()
        except Exception as _e:
            quiet_err("FlowRecorder.stop", _e)
        time.sleep(1.6)                      # let the last "after" picture land
        self._save()
        self.log(f"[+] Flow '{self.name}' saved: {len(self.steps)} steps in {self.dir}")
        return {"steps": len(self.steps), "path": str(self.dir)}


class CityNav:
    """Moves around the city map and remembers where the camera is (relative to the top-left corner)."""

    def __init__(self, dev, stop, log, pause=0.9):
        self.dev, self.stop, self.log, self.pause = dev, stop, log, pause
        self.cx = self.cy = 0.0
        self.seen_city = False                              # set once the whole-city picture recognised the screen
        self.absolute = False                                # True: the camera place is read from the whole-city picture (no drifting)
        self.deadline = None                                 # time.time() after which every move gives up (a step's time limit)

    def halted(self):
        """Stop pressed, or this step's time limit passed: every camera move checks it (Kiravik 24 Oct 14:57: Stop clicked
        9 times while aiming at the Market, the instance kept going)."""
        return self.stop.is_set() or (self.deadline is not None and time.time() > self.deadline)

    def localize(self, shot=None):
        """Find where the camera is by matching the screen against the whole-city picture. Sets cx, cy (city picture
        coordinates of screen point 0,0). -> match score, or 0 when the screen is not recognised."""
        shot = self.dev.screenshot() if shot is None else shot
        r = locate_in_city(shot)
        if r:
            self.cx, self.cy = r[0], r[1]
            return r[2]
        return 0.0

    def goto_abs(self, mapx, mapy, tol=70):
        """Closed loop: look where we are, drag towards the point, look again - until the point is near the middle of the
        view. -> True when it is there."""
        x, y, w, h = CITY_VIEW
        mid_x, mid_y = x + w / 2, y + h / 2
        lost = 0
        for _ in range(12):
            if self.halted():
                return False
            sc = self.localize()
            if sc:
                self.last_score = sc
                self.seen_city = True
            if not sc:
                lost += 1
                if lost >= 7:
                    if not getattr(self, "quiet", False):
                        self.log("[!]   the screen is not recognised as the city (ad or window in the way?)")
                    return False
                _nap(self.stop, 0.6, 0.9)
                continue
            lost = 0
            px, py = mapx - self.cx, mapy - self.cy
            if abs(px - mid_x) <= tol and abs(py - mid_y) <= tol:
                self.log(f"[i]   camera at {round(self.cx)},{round(self.cy)} (match {sc:.2f}), target on screen at {round(px)},{round(py)}")
                return True
            mx = max(-210, min(210, px - mid_x))              # how far the picture has to move (finger moves the other way)
            my = max(-320, min(320, py - mid_y))
            if abs(mx) < 30 and abs(my) < 30:                 # a short drag may count as a TAP on a building: overshoot and come back
                ux, uy = (1.0 if mx >= 0 else -1.0), (1.0 if my >= 0 else -1.0)
                self.dev.swipe(270 + ux * 100, 440 + uy * 100, 270 - ux * 100, 440 - uy * 100, 450)
                _nap(self.stop, self.pause, self.pause + 0.3)
                continue                                      # next round sees the overshoot and drags back
            self.dev.swipe(270 + mx / 2, 440 + my / 2, 270 - mx / 2, 440 - my / 2, 450)
            _nap(self.stop, self.pause, self.pause + 0.3)
        return False

    def _settled(self):
        """Screenshot taken once the map has stopped moving (it glides after a swipe and springs back at the city's
        edge - measuring 1 s after the swipe saw the map 480 px from where it ended, Kiravik 23 Oct)."""
        last = self.dev.screenshot()
        for _ in range(4):                                 # animated water / troops never give an identical picture:
            _nap(self.stop, 0.3, 0.4)                      # settled = the map itself has not shifted between two frames
            cur = self.dev.screenshot()
            f = feat_shift(last, cur)
            if f is None or (abs(f[0]) < 4 and abs(f[1]) < 4):
                return cur
            last = cur
        return last

    def _drag(self, fx, fy, tx, ty, before=None):
        if self.halted():
            shot = before if before is not None else self.dev.screenshot()
            return 0.0, 0.0, shot
        before = self._settled() if before is None else before
        self.dev.swipe(int(fx), int(fy), int(tx), int(ty), 600)
        _nap(self.stop, self.pause, self.pause + 0.3)
        after = self._settled()
        dx, dy = safe_shift(before, after, tx - fx, ty - fy)
        if abs(dx) < 3 and abs(dy) < 3 and find_detail_icon(after):   # the drag was taken as a tap on a building
            self.log("[i]   the drag opened a building's ring instead of moving the map - closing it")
            close_ring(self.dev, self.stop, (270, 490))
        return dx, dy, after

    def to_origin(self, on_step=None):
        """Drag the map until it stops moving (the top-left corner of the city)."""
        for i in range(30):
            if self.stop.is_set():
                return False
            if on_step:
                on_step(i)
            dx, dy, _ = self._drag(210, 300, 330, 540)
            if abs(dx) < 4 and abs(dy) < 4:
                break
        self.cx = self.cy = 0.0
        return True

    def pan(self, want_dx, want_dy):
        """Move the camera by about (want_dx, want_dy) map pixels. -> the camera movement achieved."""
        mx = my = 0.0
        for _ in range(6):                                 # a few measured drags; the city edge (springs back) ends it
            rx, ry = want_dx - mx, want_dy - my
            if abs(rx) < 25 and abs(ry) < 25 or self.halted():
                break
            sx = max(-200, min(200, rx))              # one drag at a time; each is measured (patch tracking)
            sy = max(-300, min(300, ry))
            # dragging the finger left/up moves the camera right/down.
            # A short drag can be read by the game as a TAP on a building (that opened Prison, Rare Books ...), so a
            # short move is done as one long drag past the target and a second long drag back.
            ln = (sx * sx + sy * sy) ** 0.5
            if ln < 200:                                 # the game read a 120 px drag on a building as a TAP (ring opened,
                ux, uy = (sx / ln, sy / ln) if ln else (0.0, 1.0)   # camera stayed; Kiravik 19:13 x8): always drag far,
                ax, ay = sx + ux * 200, sy + uy * 200    # then come back
                d1x, d1y, _ = self._drag(270 + ax / 2, 440 + ay / 2, 270 - ax / 2, 440 - ay / 2)
                d2x, d2y, _ = self._drag(270 - ux * 100, 440 - uy * 100, 270 + ux * 100, 440 + uy * 100)
                dx, dy = d1x + d2x, d1y + d2y
            else:
                dx, dy, _ = self._drag(270 + sx / 2, 440 + sy / 2, 270 - sx / 2, 440 - sy / 2)
            if abs(dx) + abs(dy) < 0.25 * (abs(sx) + abs(sy)):   # moved much less than the finger: the edge
                mx -= dx
                my -= dy
                break
            mx -= dx
            my -= dy
        self.cx += mx
        self.cy += my
        return mx, my

    def goto(self, mapx, mapy):
        """Bring the map point to the middle of the view. -> its screen position."""
        if self.absolute:
            self.goto_abs(mapx, mapy)
            return mapx - self.cx, mapy - self.cy
        x, y, w, h = CITY_VIEW
        want_x, want_y = mapx - (x + w / 2), mapy - (y + h / 2)
        self.pan(want_x - self.cx, want_y - self.cy)
        for _ in range(3):                                   # the city is a diamond: near a corner the map will not slide
            rx, ry = want_x - self.cx, want_y - self.cy      # sideways until you have gone down (or up) far enough,
            if abs(rx) < 40 and abs(ry) < 40 or self.halted():   # so do the other direction first, then come back
                break
            if abs(ry) >= 25:
                self.pan(0, ry)
            self.pan(want_x - self.cx, 0)
            self.pan(want_x - self.cx, want_y - self.cy)
        return mapx - self.cx, mapy - self.cy


CORNER_PIC = None                                          # camera place (picture coordinates of screen point 0,0) when the map is dragged to the city's top-left corner; learned once, see learn_corner
CORNER_FILE = USER / "corner_pic.json"
PICTURE_BAD = {}                                          # serial -> True: this instance's city does not look like the stored picture, go straight to the squares


def corner_pic():
    if CORNER_PIC:
        return CORNER_PIC
    try:
        d = json.loads(CORNER_FILE.read_text(encoding="utf-8"))
        return (float(d["x"]), float(d["y"]))
    except Exception:
        return None


def _flag_on():
    try:
        return (USER / "city_nav_squares.txt").read_text(encoding="utf-8").strip().lower().startswith("test")
    except Exception:
        return False


def gain_probe(dev, stop, log):
    """Test aid (only with city_nav_squares.txt, on a city that fits the picture): swipe by known amounts and log how far the
    map really moved, to learn how to count squares in other city styles."""
    try:
        for fx, fy, tx, ty in [(300, 400, 140, 400), (300, 400, 300, 160), (230, 300, 380, 560), (300, 600, 300, 520), (300, 460, 300, 480)]:
            a = locate_in_city(dev.screenshot())
            dev.swipe(fx, fy, tx, ty, 450)
            _nap(stop, 1.0, 1.3)
            b = locate_in_city(dev.screenshot())
            if a and b:
                log(f"[i]   swipe probe: finger {tx - fx},{ty - fy} -> camera moved {round(b[0] - a[0])},{round(b[1] - a[1])}")
            else:
                log(f"[i]   swipe probe: finger {tx - fx},{ty - fy} -> picture not matched ({bool(a)},{bool(b)})")
    except Exception as e:
        log(f"[i]   swipe probe failed: {e}")


def learn_corner(nav, log):
    """Once, on a city that matches the stored picture: drag to the top-left corner and note where the picture says the
    camera is. Every other city style then counts its squares from the same place."""
    if corner_pic() is not None:
        return
    nav.to_origin()
    for _ in range(3):
        sc = nav.localize()
        if sc:
            try:
                CORNER_FILE.write_text(json.dumps({"x": round(nav.cx, 1), "y": round(nav.cy, 1), "score": round(sc, 2)}), encoding="utf-8")
            except Exception as _e:
                quiet_err("learn_corner", _e)
            log(f"[i] City corner learned at {round(nav.cx)},{round(nav.cy)} (match {sc:.2f})")
            return
        _nap(nav.stop, 0.6, 0.9)


_CITY_PIC = {}
SAFE_TAP_AREA = (95, 215, 335, 470)                        # x, y, w, h of the screen where a tap can only hit the city (emulator px)
SHRINE_ROUTE = False      # owner 21 Oct 20:48: the Shrine search (reading map labels) is dropped - buildings are found by the city picture + saved plot, checked by the menu name
SHRINE_LABEL_IN_PICTURE = (1372.2, 576.9)                   # the Shrine label in the whole-city picture (emulator px); the square offsets count from here


def city_picture():
    """The whole-city picture (grey, emulator px) used to find the camera. -> array or None."""
    if "g" not in _CITY_PIC:
        g = None
        for base in (RES / "city_map", Path(__file__).parent / "city_map", USER / "city_map", USER / "templates", RES / "templates", Path(__file__).parent):
            f = base / "city_whole.png"
            if f.exists():
                g = cv2.imread(str(f), cv2.IMREAD_GRAYSCALE)
                if g is not None:
                    break
        _CITY_PIC["g"] = g
    return _CITY_PIC["g"]


def locate_in_city(shot, min_score=0.55):
    """Where is the camera? Match the middle of the screen against the whole-city picture.
    -> (x, y, score): the picture coordinates of screen point (0, 0), or None."""
    g = city_picture()
    if g is None:
        return None
    t = cv2.cvtColor(shot[250:730, 60:440], cv2.COLOR_BGR2GRAY)     # the lower middle: the sky, clouds and top bubbles change, the buildings do not
    if float(t.std()) < 12:                                          # a black / blank screen (loading, window in the way): nothing to match
        return None
    res = cv2.matchTemplate(g, t, cv2.TM_CCOEFF_NORMED)
    _, mv, _, ml = cv2.minMaxLoc(res)
    if mv < min_score:
        return None
    r2 = res.copy()                                          # a second, different place that matches almost as well = lawns / walls look alike: do not trust
    r2[max(0, ml[1] - 60):ml[1] + 60, max(0, ml[0] - 60):ml[0] + 60] = -1
    second = float(r2.max())
    if mv < 0.88 and mv - second < 0.04:
        return None
    return ml[0] - 60, ml[1] - 250, float(mv)


def ring_label_free(shot, centre=(270, 450)):
    """Read the text the game prints under the tapped building, whatever it says (no list of names is used). Takes the
    text phrase nearest to the middle of the screen. -> (name, x, y) or None."""
    best = None
    for text, ws in ocr_lines(shot[120:760, 40:500]):
        ws = sorted(ws, key=lambda w: w[1])
        group = []
        def flush():
            nonlocal best
            if not group:
                return
            txt = " ".join(w[0] for w in group).strip(" .,:;|-_*")
            if len(txt) >= 4 and re.fullmatch(r"[A-Za-z' ]+", txt) and txt.lower() not in ("lv", "level", "vip", "i"):
                gx = sum(w[1] for w in group) / len(group) + 40
                gy = sum(w[2] for w in group) / len(group) + 120
                d = (gx - centre[0]) ** 2 + (gy - centre[1]) ** 2
                if d < 220 ** 2 and (best is None or d < best[0]):
                    best = (d, txt, int(gx), int(gy))
        last = None
        for w in ws:
            if last is not None and w[1] - last[1] > 90:
                flush()
                group = []
            group.append(w)
            last = w
        flush()
    if not best:
        return None
    txt, gx, gy = best[1], best[2], best[3]
    try:                                                    # read the label line again, bigger: long names ("Council of State") lose words in the first pass
        y0, y1 = max(0, gy - 17), min(shot.shape[0], gy + 17)
        x0, x1 = max(0, gx - 130), min(shot.shape[1], gx + 130)
        if True:
            raise ValueError                                # bigger re-read added junk from the ring icons: off
        big = cv2.resize(shot[y0:y1, x0:x1], None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
        for t2, ws2 in ocr_lines(big):
            ws2 = sorted(ws2, key=lambda w: w[1])
            t3 = " ".join(w[0] for w in ws2).strip(" .,:;|-_*")
            if re.fullmatch(r"[A-Za-z' ]+", t3) and len(t3) > len(txt) and txt.lower().split()[-1] in t3.lower():
                cx = sum(w[1] for w in ws2) / len(ws2) / 2.5 + x0
                txt, gx = t3, int(cx)
                break
    except Exception as _e:
        quiet_err("ring_label_free", _e)
    nm = match_building(txt, cutoff=0.85)                   # only to repair an OCR typo of a name the bot already spells
    if not nm:                                              # a long name read only in part ("Vict", "of State"): finish it from the names the bot knows
        tw = [w.lower() for w in txt.split() if len(w) >= 2]
        hits = []
        for known in BUILDING_NAMES:
            kw = [w.lower() for w in known.split()]
            if tw and all(any(k.startswith(w) or w.startswith(k) for k in kw) for w in tw) and sum(len(w) for w in tw) >= 4:
                hits.append(known)
        if len(hits) == 1:
            nm = BUILDING_ALIASES.get(hits[0], hits[0])
    return (nm or txt), gx, gy


TPL_W, TPL_H = 110, 80                                       # size of the saved picture of a building


def _tpl_path(serial, key):
    d = USER / "citytpl" / re.sub(r"[^\w]+", "_", str(serial))
    d.mkdir(parents=True, exist_ok=True)
    return d / (re.sub(r"[^\w]+", "_", key) + ".png")


def save_building_picture(serial, key, rest, rx, ry):
    """Save a small picture of the building (taken at rest, so no name label covers it), centred on its label."""
    hh, ww = rest.shape[:2]
    x0, y0 = int(rx - TPL_W / 2), int(ry - TPL_H / 2)
    if x0 < 0 or y0 < 0 or x0 + TPL_W > ww or y0 + TPL_H > hh:
        return False
    cv2.imwrite(str(_tpl_path(serial, key)), rest[y0:y0 + TPL_H, x0:x0 + TPL_W])
    return True


def match_building_picture(serial, key, shot, min_score=0.72):
    """Look for the saved picture of a building inside the city view. -> (screen x, screen y, score) or None."""
    path = _tpl_path(serial, key)
    if not path.exists():
        return None
    tpl = cv2.imread(str(path))
    if tpl is None:
        return None
    x0, y0, w, h = LABEL_VIEW
    area = shot[y0:y0 + h, x0:x0 + w]
    if area.shape[0] < tpl.shape[0] or area.shape[1] < tpl.shape[1]:
        return None
    res = cv2.matchTemplate(area, tpl, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    if score < min_score:
        return None
    return loc[0] + x0 + tpl.shape[1] // 2, loc[1] + y0 + tpl.shape[0] // 2, float(score)


def find_building(dev, stop, log, nav, name, approx, items=None, key=None):
    """Go to where a building should be and make sure it is really there by reading its name label (the stored
    position can drift by ~130 px). Looks around a little if the label is not at the expected place. The corrected
    position is written back to the city map. -> (screen x, screen y) to tap, or None."""
    canon = BUILDING_ALIASES.get(name, name)
    ax, ay = approx
    x0, y0, w, h = CITY_VIEW
    serial = dev.serial if hasattr(dev, "serial") else "default"
    if key:                                                  # 1) fast: go to the saved coordinates, match the saved picture
        nav.goto(ax, ay)
        _nap(stop, 0.3, 0.5)
        hit = match_building_picture(serial, key, dev.screenshot())
        if hit:
            px, py, sc = hit
            mx, my = nav.cx + px, nav.cy + py
            if abs(mx - ax) > 15 or abs(my - ay) > 15:
                log(f"[i] {name}: found by its picture (score {sc:.2f}), position corrected by {abs(mx - ax):.0f},{abs(my - ay):.0f} px")
                if items is not None:
                    items[key] = [round(mx), round(my)]
                    data = load_city()
                    if serial in data:
                        data[serial]["items"][key] = [round(mx), round(my)]
                        save_city(data)
            return px, py
        log(f"[i] {name}: its picture is not at the saved position - reading name labels instead")
    offsets = [(0, 0), (-130, 0), (130, 0), (0, -130), (0, 130), (-130, -130), (130, 130), (-130, 130), (130, -130)]
    for ox, oy in offsets:
        if stop.is_set():
            return None
        sx, sy = nav.goto(ax + ox, ay + oy)
        labels, _, _ = look_with_names(dev, stop, nav)
        cands = [(n, mx, my) for n, mx, my in labels if n == canon]
        if not cands:
            continue
        n, mx, my = min(cands, key=lambda c: (c[1] - ax) ** 2 + (c[2] - ay) ** 2)
        px, py = mx - nav.cx, my - nav.cy
        if not (x0 <= px <= x0 + w and y0 <= py <= y0 + h):
            continue
        if items is not None and key and (abs(mx - ax) > 15 or abs(my - ay) > 15):
            items[key] = [round(mx), round(my)]
            data = load_city()
            ser = dev.serial if hasattr(dev, "serial") else "default"
            if ser in data:
                data[ser]["items"][key] = [round(mx), round(my)]
                save_city(data)
        if (ox, oy) != (0, 0):
            log(f"[i] {name}: found {abs(ox) + abs(oy)} px away from where the map had it - position corrected")
        return px, py
    log(f"[!] {name}: could not see its name near the saved position")
    return None


def open_tavern_portraits(dev, cfg, log, stop):
    """From the city screen: find the Tavern (city map if recorded, otherwise by reading building names while
    sweeping the city), open it and go to Recruit General > Portraits."""
    nap = lambda lo, hi=None: _nap(stop, lo, hi)
    if screen_state(dev.screenshot(), cfg) != "city":
        log("[*] Not on the city screen - trying to get back to the city")
        clear_popups(dev, cfg, log, stop, rounds=3)
        leave_to_city(dev, cfg, stop)
    items = city_map_items(dev)
    if not items:
        raise ValueError(CITY_MAP_HELP)
    keys = [k for k in items if k.split("#")[0].lower() == "tavern"]
    if not keys:
        raise ValueError("The Tavern is not on this emulator's city map. Re-run the City Mapper (the Tavern has to be visible).")
    nav = CityNav(dev, stop, log)
    log("[*] Going to the Tavern on the city map")
    nav.to_origin()
    pos = find_building(dev, stop, log, nav, "Tavern", items[keys[0]], items, keys[0])
    tapped = bool(pos)
    if pos:
        dev.tap(*pos)
    if not tapped:
        raise ValueError("I could not find the Tavern in the city. Run the city mapper first or open the Tavern by hand.")
    nap(1.4, 1.9)
    for _ in range(4):
        shot = dev.screenshot()
        off = black_top(shot)
        if find_text(shot, ["Portraits"], region=(120, off, 300, 100)) or find_text(shot, ["Recruit General"], region=(60, off, 420, 100)):
            return True
        pos = find_text(shot, ["Recruit", "Recruit General", "Recruit Generals"])
        if pos:
            dev.tap(*pos)
            nap(1.5, 2.0)
            continue
        nap(1.0)
    raise ValueError("The Tavern opened but I could not find its Recruit General screen.")


def city_report(found):
    """Which of the expected buildings are on the map. -> (mapped, missing) lists of names."""
    have = {k.split("#")[0] for k in found}
    want = expected_buildings(found)
    return [n for n in want if n in have], [n for n in want if n not in have]


def expected_buildings(found):
    """The buildings this city should have: all of them, except the optional ones that were not seen."""
    have = {k.split("#")[0] for k in found}
    return [n for n in EXPECTED_BUILDINGS if n not in OPTIONAL_BUILDINGS or n in have]


def look_with_names(dev, stop, nav, shots=3):
    """Show the building names and read them: a finger moves ~10 px and STAYS DOWN while the screen is read, then it is
    lifted. The camera position (nav.cx, nav.cy) is kept right. -> (labels, held_img, rest_img) where labels are
    [(name, map_x, map_y), ...] in city-map coordinates."""
    rest = dev.screenshot()
    held = dev.hold_start(270, 440, 270, 430)
    th = None if held else dev.swipe_bg(270, 440, 270, 400, 2600)
    _nap(stop, 0.45, 0.55)
    best, best_labels = None, []
    try:
        for _ in range(max(1, shots)):
            img = dev.screenshot()
            lab = read_building_labels(img)
            if best is None:
                best, best_labels = img, list(lab)
            else:                                           # the same view read again: keep every name seen in any shot
                for l in lab:
                    if not any(o[0] == l[0] and abs(o[1] - l[1]) < 40 and abs(o[2] - l[2]) < 25 for o in best_labels):
                        best_labels.append(l)
            if len(best_labels) >= 6:
                break
            _nap(stop, 0.2, 0.3)
    finally:
        if held:
            dev.hold_end()
        elif th is not None:
            th.join(timeout=5)
    dxh, dyh = safe_shift(rest, best, 0, -10)               # the finger moved 10 px up
    labels = [(n, round(nav.cx - dxh + sx), round(nav.cy - dyh + sy)) for n, sx, sy in best_labels]
    nav.last_rest = rest                                    # the picture WITHOUT name labels, and where each name sits in it
    nav.last_rest_labels = [(n, int(sx - dxh), int(sy - dyh)) for n, sx, sy in best_labels]
    _nap(stop, 0.35, 0.5)                                   # let the map settle, then keep the camera position right
    after = dev.screenshot()
    dxa, dya = safe_shift(rest, after, 0, -52 if held else -10)
    nav.cx -= dxa
    nav.cy -= dya
    return labels, best, rest


def capture_city_frames(dev, cfg, log, stop, out_dir):
    """Developer tool: sweep the whole city and save what the screen looks like at every stop - once at rest and once
    while a finger is dragging (that is when the game shows the building names). The frames and the camera position of
    each are saved so the name reading and the plot map can be tuned offline."""
    import shutil
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    dev.arm_guard(cfg, log, stop)
    dev.guard_city = True
    nav = CityNav(dev, stop, log)
    log("[*] City capture: going to the top-left corner")
    nav.to_origin()
    meta, n = [], 0

    def snap(tag):
        nonlocal n
        cam0 = [round(nav.cx), round(nav.cy)]
        labels, drag, rest = look_with_names(dev, stop, nav)    # finger down + held while the names are read
        cv2.imwrite(str(out / f"{n:03d}_rest.png"), rest)
        cv2.imwrite(str(out / f"{n:03d}_drag.png"), drag)
        meta.append({"i": n, "tag": tag, "cam": cam0, "held": not getattr(dev, "no_motion", False),
                     "labels_map": [[a, b, c] for a, b, c in labels]})
        log(f"[*] City capture: frame {n}: {len(labels)} names ({', '.join(a for a, _, _ in labels)})")
        n += 1

    xs = [260, 500, 740, 980, 1220, 1460]
    ys = [280, 560, 840]
    order = []
    for r, y in enumerate(ys):                                # serpentine over the city, going to each stop (goto copes with the diamond shape)
        order += [(x, y) for x in (xs if r % 2 == 0 else xs[::-1])]
    for (x, y) in order:
        if stop.is_set():
            break
        nav.goto(x, y)
        log(f"[*] City capture: stop {n + 1}/{len(order)} wanted {x},{y} camera {round(nav.cx)},{round(nav.cy)}")
        snap(f"{x},{y}")
    dev.guard_city = False
    (out / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    log(f"[+] City capture: {n} frames saved to {out}")
    return n


# ------------------------------------------------------------ numbered plots (slots), the same for every city
SLOT_FILE = USER / "slots_v2.json"


def load_slots():
    try:
        return json.loads(SLOT_FILE.read_text(encoding="utf-8")).get("slots", [])
    except Exception:
        return []


def build_slots(items, merge=0):
    """Number the building plots of a city map: reading order, top to bottom then left to right. Plots that are closer
    than `merge` map pixels are one plot. -> [{'n', 'x', 'y', 'ref'}, ...]"""
    pts = []
    for k, (x, y) in items.items():
        name = k.split("#")[0]
        if name in RESOURCE_BUILDINGS:
            continue
        if merge and any(abs(x - p["x"]) < merge and abs(y - p["y"]) < merge for p in pts):
            continue
        if any(p["ref"] == name for p in pts):                  # one plot per building (copies of a building are not plots)
            continue
        pts.append({"x": int(x), "y": int(y), "ref": name})
    pts.sort(key=lambda p: (round(p["y"] / 140), p["x"]))
    for i, p in enumerate(pts, 1):
        p["n"] = i
    return pts


def save_slots(slots):
    save_json(SLOT_FILE, {"slots": slots}, indent=1)


def nearest_slot(slots, x, y, far=260, name=None, taken=()):
    """The plot a building is on: its own plot if its position is still near it, else the nearest free plot."""
    if name:
        own = next((p for p in slots if p["ref"] == name), None)
        if own and abs(own["x"] - x) < far and abs(own["y"] - y) < far and own["n"] not in taken:
            return own["n"]
    free = [p for p in slots if p["n"] not in taken]
    best = min(free, key=lambda p: (p["x"] - x) ** 2 + (p["y"] - y) ** 2, default=None)
    if best and abs(best["x"] - x) < far and abs(best["y"] - y) < far:
        return best["n"]
    return 0


def read_building_level(dev, cfg, log, stop, pos):
    """Test run, the 'tap it and read it' way: tap the building, press Detail, then More Information and read the header
    ('Walls Level 35'). Everything is closed again afterwards. -> (name, level) or None."""
    nap = lambda lo, hi=None: _nap(stop, lo, hi)
    dev.tap(*pos)
    nap(0.9, 1.3)
    shot = dev.screenshot()
    d = find_text(shot, ["Detail", "Details"], region=(0, 180, 540, 640))
    result = None
    if d:
        dev.tap(d[0], d[1] - 34)                            # the round icon sits just above its word
        nap(1.0, 1.4)
        shot = dev.screenshot()
        more = find_text(shot, ["More Information"], region=(60, 780, 420, 150))
        if more:
            dev.tap(*more)
            nap(0.9, 1.3)
            shot = dev.screenshot()
            x, y, w, h = 60, 130, 420, 80
            for text, _ in ocr_lines(shot[y:y + h, x:x + w]):
                m = re.search(r"([A-Za-z' ]+?)\s+Level\s*(\d+)", text)
                if m:
                    result = (m.group(1).strip(), int(m.group(2)))
                    break
                nm = match_building(text)                   # some buildings (Wonder...) have no level in the header
                if nm:
                    result = (nm, 0)
                    break
    for _ in range(4):                                      # close the pop-up / Detail page, back to the city
        if screen_state(dev.screenshot(), cfg) == "city":
            break
        dev.back()
        nap(0.7, 1.0)
    return result


def scan_city(dev, cfg, log, stop, progress=None):
    """Sweep the whole city, read the building names and save where each one is on the map. -> {name: [x, y]}"""
    dev.arm_guard(cfg, log, stop)
    dev.guard_city = True
    nav = CityNav(dev, stop, log)
    found = {}                                             # id -> [map x, map y]
    serial_key = dev.serial if hasattr(dev, "serial") else "default"
    x, y, w, h = CITY_VIEW
    t_start = time.time()
    prev = (load_city().get(serial_key) or {})
    est_total = int(prev.get("stops") or 30)               # how many stops a sweep takes (learned from the last scan)
    stats = {"stops": 0, "row": 0, "corner": 0, "sweep_started": False, "text": "Going to the top-left corner of the city"}

    def report(phase="sweeping"):
        if not progress:
            return
        mapped, missing = city_report(found)
        el = time.time() - t_start
        if phase == "done":
            pct = 100
        elif not stats["sweep_started"]:
            pct = min(12, int(stats["corner"] * 12 / 12))                       # getting to the corner = first 12%
        else:
            pct = min(97, 12 + int(85 * stats["stops"] / max(est_total, stats["stops"] + 1)))
        left = int(el * (100 - pct) / pct) if pct >= 8 else None                # rough time left
        try:
            progress({"phase": phase, "pct": pct, "left": left, "text": stats["text"], "stops": stats["stops"], "total": max(est_total, stats["stops"] + 1),
                      "row": stats["row"], "mapped": mapped, "missing": missing, "expected": expected_buildings(found),
                      "seconds": int(time.time() - t_start)})
        except Exception as _e:
            quiet_err("scan_city.report", _e)

    log("[*] City mapper: going to the top-left corner")
    report()

    def corner_step(i):
        stats["corner"] = i + 1
        report()
    nav.to_origin(corner_step)
    stats["sweep_started"] = True
    stats["text"] = "Reading the city"
    report()

    def grab():
        img = dev.screenshot()
        if screen_state(img, cfg) == "unknown" and clear_popups(dev, cfg, log, stop, rounds=3):
            pass                                           # a pop-up / sale screen was covering the city: closed it
        labels, _, _ = look_with_names(dev, stop, nav)     # finger down and held while the names are read
        n = 0
        stats["stops"] += 1
        rest_labels = list(getattr(nav, "last_rest_labels", []))
        for li, (name, mx, my) in enumerate(labels):
            if name in RESOURCE_BUILDINGS:                 # resource tiles are not needed on the map
                continue
            same = [k for k, v in found.items() if k.split("#")[0] == name and abs(v[0] - mx) < 160 and abs(v[1] - my) < 160]
            if same:
                continue
            k = 1
            while f"{name}#{k}" in found:
                k += 1
            found[f"{name}#{k}"] = [round(mx), round(my)]
            n += 1
            if li < len(rest_labels) and rest_labels[li][0] == name and getattr(nav, "last_rest", None) is not None:
                ok_pic = save_building_picture(serial_key, f"{name}#{k}", nav.last_rest, rest_labels[li][1], rest_labels[li][2])
                stats["pics"] = stats.get("pics", 0) + (1 if ok_pic else 0)
                if not ok_pic:
                    log(f"[i] City mapper: no picture saved for {name} (too close to the screen edge: {rest_labels[li][1]},{rest_labels[li][2]})")
            else:
                log(f"[i] City mapper: no picture for {name} (rest labels {[r[0] for r in rest_labels]})")
        report()
        return n

    for sweep in range(2):                                  # a second sweep only if some expected buildings are still missing
        if sweep == 1:
            if not city_report(found)[1] or stop.is_set():
                break
            log(f"[*] City mapper: second sweep for the missing ones ({', '.join(city_report(found)[1])})")
            stats["text"] = "Looking again for the buildings that were not clear: " + ", ".join(city_report(found)[1])
            report()
            nav.to_origin()
        rows = 0
        direction = 1
        stats["text"] = "Reading the city" if sweep == 0 else stats["text"]
        report()
        while rows < 16 and not stop.is_set():
            for i in range(24):                                # one row, left to right (or back)
                if stop.is_set():
                    break
                grab()                                         # read at EVERY stop (skipping stops left gaps in the map)
                mx, _ = nav.pan(direction * 240, 0)
                if abs(mx) < 10:
                    break
            grab()
            _, my = nav.pan(0, 420)                            # next row (the label area is ~610 high: a good overlap)
            rows += 1
            stats["row"] = rows
            log(f"[*] City mapper: row {rows}, {len(found)} buildings so far")
            if abs(my) < 20:
                break
            direction = -direction
    dev.guard_city = False
    stats["text"] = "Saving the map"
    mapped, missing = city_report(found)
    data = load_city()
    data[dev.serial if hasattr(dev, "serial") else "default"] = {"items": found, "scanned": int(time.time()),
                                                                 "missing": missing, "stops": stats["stops"],
                                                                 "seconds": int(time.time() - t_start)}
    save_city(data)
    report("done")
    log(f"[*] City mapper: {stats.get('pics', 0)} building pictures saved for tapping by coordinates")
    log(f"[+] City mapper: {len(mapped)} of {len(expected_buildings(found))} buildings mapped"
        + (f". Not found: {', '.join(missing)}" if missing else " - the city map is complete"))
    return found


# ------------------------------------------------------------------ black market
def _market_buttons(img):
    """The green buttons on the Black Market screen: ([6 price buttons], refresh button or None), each (x, y, w, h)."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (40, 90, 50), (85, 255, 200))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    price, refresh = [], None
    for c in cs:
        x, y, w, h = cv2.boundingRect(c)
        if y < 200 or y > img.shape[0] - 120 or h < 14:
            continue
        if 70 < w < 140 and 25 < h < 45 and 2.5 < w / h < 5:
            price.append((x, y, w, h))
        elif 170 < w < 260 and 38 < h < 70:
            refresh = (x, y, w, h)
    price.sort(key=lambda b: (round(b[1] / 40), b[0]))
    return price, refresh


def market_on_screen(img):
    price, refresh = _market_buttons(img)
    return len(price) >= 4 and refresh is not None


def market_slots(img):
    """Each item on sale: {btn, icon (search area), gem (paid with gems?), afford (price not red)}."""
    price, _ = _market_buttons(img)
    out = []
    for (x, y, w, h) in price:
        hsv = cv2.cvtColor(img[y:y + h, x:x + w], cv2.COLOR_BGR2HSV)
        left = hsv[:, :int(w * 0.38)]
        gem = cv2.countNonZero(cv2.inRange(left, (85, 40, 170), (110, 255, 255))) / left[:, :, 0].size > 0.02
        right = hsv[:, int(w * 0.35):]
        red = (cv2.countNonZero(cv2.inRange(right, (0, 150, 150), (8, 255, 255)))
               + cv2.countNonZero(cv2.inRange(right, (172, 150, 150), (179, 255, 255))))
        gold = (not gem) and cv2.countNonZero(cv2.inRange(left, (15, 120, 150), (35, 255, 255))) / left[:, :, 0].size > 0.03
        out.append({"btn": (x, y, w, h), "gem": gem, "gold": gold, "afford": red < 40,
                    "icon": (max(0, x - 8), max(0, y - int(h * 2.9)), w + 16, int(h * 2.8))})
    return out


def read_price(img, btn):
    x, y, w, h = btn
    c = img[y:y + h, x + int(w * 0.35):x + w - 4]
    g = cv2.resize(cv2.cvtColor(c, cv2.COLOR_BGR2GRAY), None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    b = 255 - cv2.threshold(g, 150, 255, cv2.THRESH_BINARY)[1]
    b = cv2.copyMakeBorder(b, 8, 8, 8, 8, cv2.BORDER_CONSTANT, value=255)
    try:
        txt = pytesseract.image_to_string(b, config="--psm 7 -c tessedit_char_whitelist=0123456789,.")
    except Exception:
        return None
    d = re.sub(r"\D", "", txt)
    return int(d) if d else None


def free_refreshes(img, refresh):
    """'Free Refreshes: 3' under the refresh button -> 3; 0 when it is not shown (the refresh then costs gems)."""
    x, y, w, h = refresh
    c = img[y + h + 2:y + h + 45, max(0, x - 60):x + w + 60]
    if c.size == 0:
        return 0
    g = cv2.resize(cv2.cvtColor(c, cv2.COLOR_BGR2GRAY), None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    for t in (110, 140, 125):
        b = 255 - cv2.threshold(g, t, 255, cv2.THRESH_BINARY)[1]
        try:
            txt = pytesseract.image_to_string(b, config="--psm 7").lower().strip(" |\n")
        except Exception:
            return None
        if "fre" in txt or "refr" in txt:
            m = re.search(r"(\d+)\W*$", txt)
            if m:
                return int(m.group(1))
    return 0


def _sig_diff(a, b):
    """How different two shop pictures are; a different number of goods (one sold out) counts as different."""
    if a is None or b is None:
        return None
    return 255.0 if a.shape != b.shape else float(abs(a - b).mean())


def _market_sig(img):
    """A small picture of the six items, to tell whether the shop changed after a refresh."""
    price, _ = _market_buttons(img)
    if len(price) < 4:
        return None
    parts = []
    for (x, y, w, h) in price[:6]:
        c = img[max(0, y - int(h * 2.9)):y, x:x + w]
        parts.append(cv2.resize(cv2.cvtColor(c, cv2.COLOR_BGR2GRAY), (24, 24)).astype("float32"))
    return np.concatenate(parts, axis=1)


def refresh_cost(img, refresh, default=50):
    """The gem cost on the refresh button (a gem icon and a number under 'Instant Refresh'). If it cannot be
    read, assume `default` so the gem limit still works."""
    x, y, w, h = refresh
    c = img[y + int(h * 0.45):y + h - 2, x + int(w * 0.5):x + int(w * 0.8)]
    if c.size:
        g = cv2.resize(cv2.cvtColor(c, cv2.COLOR_BGR2GRAY), None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
        for t in (200, 170, 140):
            b = 255 - cv2.threshold(g, t, 255, cv2.THRESH_BINARY)[1]
            b = cv2.copyMakeBorder(b, 8, 8, 8, 8, cv2.BORDER_CONSTANT, value=255)
            try:
                d = re.sub(r"\D", "", pytesseract.image_to_string(b, config="--psm 7 -c tessedit_char_whitelist=0123456789"))
            except Exception:
                break
            if d and 10 <= int(d) <= 1000:
                return int(d)
    return int(default)


def market_totals(img):
    """The gold (left) and gem (right) totals in the row under the shop picture -> (gold, gems); None when unread."""
    def num(x0, x1):
        best = None
        try:
            for _t, ws in ocr_lines(img[282:330, x0:x1], 3.0):
                runs, cur = [], ""                         # neighbouring number pieces belong together: OCR can split
                for w in ws:                               # '432,750' into '4' + '32,750' (Kulim 24 Oct: ~400k read as 32,750)
                    if re.fullmatch(r"[\d.,']+", w[0].strip()):
                        cur += w[0]
                    else:
                        if cur:
                            runs.append(cur)
                        cur = ""
                if cur:
                    runs.append(cur)
                for r in runs:
                    d = re.sub(r"\D", "", r)
                    if len(d) >= 3 and (best is None or len(d) > len(str(best))):
                        best = int(d)
        except Exception as _e:
            quiet_err("market_totals.num", _e)
        return best
    gold, gems = num(0, 275), num(265, 540)              # each half on its own: read together, one of them is often lost
    return gold, gems


def market_dialog(img):
    """A box over the shop -> "buy" ('Are you sure you want to purchase X?' + Confirm), "rebuy" ('You will consume 1
    Rebuy Chance...' + gem button + red X) or None."""
    try:
        lines = " ".join(t.lower() for t, _ in ocr_lines(img[300:600, 20:520], 2.0))   # below the shop's own "Rebuy Chance(s) for Today" line
        if "consume" in lines or "again" in lines:
            return "rebuy"
        if "sure" in lines or "purchase" in lines:
            return "buy"
    except Exception as _e:
        quiet_err("market_dialog", _e)
    if not market_on_screen(img):                          # text unread: tell them apart by the green button's height
        gb = _green_button(img, (60, 470, 420, 240))
        if gb and 530 <= gb[1] <= 605:
            return "buy"
        if gb and gb[1] > 620:
            return "rebuy"
    return None


REBUY_CLOSE = (486, 274)   # red X of the Rebuy box (540x960)


def _green_button(img, region):
    """The biggest green button inside region (x, y, w, h) -> centre (x, y) or None."""
    x0, y0, w0, h0 = region
    hsv = cv2.cvtColor(img[y0:y0 + h0, x0:x0 + w0], cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (40, 90, 50), (85, 255, 200))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in cs:
        x, y, w, h = cv2.boundingRect(c)
        if 80 < w < 260 and 25 < h < 70 and (best is None or w * h > best[2] * best[3]):
            best = (x, y, w, h)
    return (x0 + best[0] + best[2] / 2, y0 + best[1] + best[3] / 2) if best else None


def market_confirm(dev, log, stop):
    """After tapping a price: wait for the purchase question (up to ~4 s), tap Confirm, check the box closed.
    -> True when Confirm was tapped and the box is gone."""
    kind = None
    for _ in range(4):
        shot = dev.screenshot()
        kind = market_dialog(shot)
        if kind:
            break
        if stop.is_set():
            return False
        _nap(stop, 0.8, 1.1)
    else:
        log("[i] Black Market: no purchase question appeared")
        return False
    if kind == "rebuy":                                     # buying it again costs a Rebuy Chance: not decided yet -> close, do not buy
        log("[i] Black Market: this would use a Rebuy Chance - closed the box, not bought")
        dev.tap(*REBUY_CLOSE)
        _nap(stop, 0.9, 1.2)
        return None                                         # None = refused rebuy (the caller logs nothing more)
    for _ in range(2):
        p = find_text(shot, ["Confirm"], region=(60, 470, 420, 200), cutoff=0.6) or _green_button(shot, (60, 470, 420, 200))
        if not p:
            log("[!] Black Market: the Confirm button was not found")
            cv2.imwrite(str(USER / "debug" / "black_market_confirm.png"), shot)
            return False
        dev.tap(*p)
        _nap(stop, 1.0, 1.4)
        shot = dev.screenshot()
        if not market_dialog(shot):
            return True
    log("[!] Black Market: the purchase question is still open after tapping Confirm")
    cv2.imwrite(str(USER / "debug" / "black_market_confirm.png"), shot)
    return False


def market_wanted(cfg):
    out = []
    for it in cfg.get("market_items") or []:
        name = it.get("name", "").strip()
        tpl_name = "mk_" + re.sub(r"[^\w\-]", "_", name)
        if name and has_template(tpl_name):
            out.append((name, tpl_name, it.get("pay") or cfg.get("market_pay", "gems")))
    return out


def market_match(img, slot, tpl_name, threshold):
    x, y, w, h = slot["icon"]
    area = img[y:y + h, x:x + w]
    t = tpl(tpl_name)
    if area.shape[0] < t.shape[0] or area.shape[1] < t.shape[1]:
        return 0.0
    return float(cv2.matchTemplate(area, t, cv2.TM_CCOEFF_NORMED).max())


def _icon_hash(img):
    g = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (9, 8), interpolation=cv2.INTER_AREA).astype("int16")
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def run_market_capture(dev, cfg, log, stop):
    """The Market's ring menu is open: open the Black Market and save a picture of every item on sale into the item
    library (USER/market_library). Pictures already in the library (same look) are not saved again. Nothing is bought
    and nothing is refreshed."""
    img = dev.screenshot()
    if not market_on_screen(img):
        p = find_text(img, ["Black Market", "Black"], region=(0, 150, 400, 500), cutoff=0.7)
        if not p:
            raise RuntimeError("the Black Market icon is not in the Market's menu")
        dev.tap(p[0], p[1] - 40)
        _nap(stop, 2.2, 3.0)
        img = dev.screenshot()
    if not market_on_screen(img):
        raise RuntimeError("the Black Market did not open")
    lib = USER / "market_library"
    lib.mkdir(parents=True, exist_ok=True)
    known = []
    for f in lib.glob("*.png"):
        t = cv2.imread(str(f))
        if t is not None:
            known.append(_icon_hash(t))
    new = 0
    slots = market_slots(img)
    for i, sl in enumerate(slots, 1):
        x, y, w, h = sl["icon"]
        crop = img[y:y + h, x:x + w]
        if crop.size == 0:
            continue
        hh = _icon_hash(crop)
        if any(bin(hh ^ k).count("1") <= 5 for k in known):
            continue
        known.append(hh)
        cv2.imwrite(str(lib / f"item_{int(time.time())}_{i}.png"), crop)
        new += 1
    log(f"[+]  Black Market: {len(slots)} item(s) on sale, {new} new picture(s) saved to the library ({len(known)} in total)")
    cv2.imwrite(str(USER / "debug" / "black_market.png"), img)


def run_market(dev, cfg, log, stop):
    """On the Black Market screen: buy the wanted items (gems / resources / both), refresh the shop as set."""
    wanted = market_wanted(cfg)
    img = dev.screenshot()
    for _ in range(2):                                     # the Market's ring menu is open: tap the Black Market icon (above its caption)
        if market_on_screen(img):
            break
        p = find_text(img, ["Black Market", "Black"], region=(0, 150, 540, 600), cutoff=0.7)
        if p:
            dev.tap(p[0], p[1] - 40)
            _nap(stop, 2.2, 3.0)
        else:                                              # the menu was still opening (name read as e.g. 'Detat'): look again
            _nap(stop, 1.0, 1.4)
        img = dev.screenshot()
    if not market_on_screen(img):
        raise RuntimeError("the Black Market did not open")
    if not wanted:                                         # the shop is open (proves the way there) but there is nothing to buy
        gold, gems = market_totals(img)
        log(f"[i] Black Market open: gold {gold if gold is not None else '?'}, gems {gems if gems is not None else '?'}; "
            "no goods picked on the Market tab - nothing bought, no refresh")
        return
    mode = cfg.get("market_refresh_mode", "none")
    limit = int(cfg.get("market_refresh_n", 0) or 0) if mode == "count" else (float("inf") if mode == "all" else 0)
    paid_ok = bool(cfg.get("market_paid_refresh"))
    budget = int(cfg.get("market_max_gems", 0) or 0)
    th = float(cfg.get("market_match", 0.78))
    min_gems = int(cfg.get("market_min_gems", 0) or 0)
    min_gold = int(cfg.get("market_min_gold", 0) or 0)
    spent = refreshes = 0
    no_change = 0
    end = None                                               # why the run ended: "floor" / "nogems" (wait for gems) / "limit" (this visit)
    keep = bm_keep_going(cfg)                                # 'until the gem floor' + paid refresh: no time limit, other tasks go in between
    serial = getattr(dev, "serial", "")
    left_for = None                                          # keep-going: the shop was left for another task (or a frozen shop)
    last_prog = last_look = time.time()                      # last buy / refresh, last look at the other tasks
    pend = 0                                                 # gems spent since the gem total was last believed
    bought, failed = {}, set()                              # failed: slot numbers that did not sell (until the next refresh)
    gold, gems = market_totals(img)
    try:                                                     # the shop with its gold / gem row, to check the numbers read (a 6-digit
        (USER / "debug").mkdir(exist_ok=True)               # gem total was read as 32,750 on Kulim (tester system) 24 Oct)
        cv2.imwrite(str(USER / "debug" / f"bm_totals_{str(getattr(dev, 'serial', '')).replace(':', '_')}.png"), img)
    except Exception as _e:
        quiet_err("bm_totals_pic", _e)
    log(f"[*] Black Market: looking for {', '.join(w[0] for w in wanted)}; gold {gold if gold is not None else '?'}, "
        f"gems {gems if gems is not None else '?'} (keep at least {min_gold:,} gold, {min_gems:,} gems)")
    need_totals = bool(min_gems or min_gold or budget)   # totals are only read every round when a floor / limit needs them
    free_left = None                                     # once the free refreshes are used up they stay at 0: no more reading them
    cost_seen = None                                     # the paid refresh cost, read once and re-checked every 10 refreshes
    next_img = None                                      # the picture taken right after a refresh is reused (one screenshot less)
    if keep:
        _TL.duty_deadline = None                             # no 20-min budget: the stall check below takes its place
        left_for = bm_leave_for(cfg, serial)                 # e.g. Auto-Join not checked yet since the start: that goes first
    while not stop.is_set() and not left_for:
        if keep:
            now = time.time()
            if now - last_prog > BM_STALL_MIN * 60:          # nothing bought or refreshed for minutes: the shop / game froze
                log(f"[!] Black Market: nothing happened for {BM_STALL_MIN} min - restarting Evony, then back to the shop")
                left_for = "stall"
                break
            if now - last_look >= 20:
                last_look = now
                left_for = bm_leave_for(cfg, serial)
                if left_for:
                    break
        img = next_img if next_img is not None else dev.screenshot()
        next_img = None
        for _w in range(4):                                  # a confirm dialog / reward pop-up can sit over the shop for a moment
            if market_on_screen(img) or stop.is_set():
                break
            kd = market_dialog(img)
            if kd == "rebuy":
                dev.tap(*REBUY_CLOSE)
                _nap(stop, 0.9, 1.2)
            elif kd == "buy":                                 # a purchase question left open: answer it (we wanted the item)
                market_confirm(dev, log, stop)
            else:
                _nap(stop, 1.0, 1.5)
            img = dev.screenshot()
        if not market_on_screen(img):
            cv2.imwrite(str(USER / "debug" / "black_market_closed.png"), img)
            log("[!] Black Market: the shop is no longer on screen (picture saved: debug/black_market_closed.png)")
            break
        if need_totals:
            g2, m2, fresh_read = bm_gems_checked(dev, img, gems, pend, log)
            gold = g2 if g2 is not None else gold
            gems = m2 if m2 is not None else gems
            if fresh_read:
                pend = 0
        again = False
        for si, slot in enumerate(market_slots(img)):
            if si in failed:
                continue
            for name, tname, pay in wanted:
                if market_match(img, slot, tname, th) < th:
                    continue
                kind_ok = (slot["gem"] and pay in ("gems", "both")) or (not slot["gem"] and pay in ("resources", "both"))
                price = read_price(img, slot["btn"])
                if not kind_ok:
                    log(f"[i] {name} is for sale but paid with {'gems' if slot['gem'] else 'resources'} - not allowed")
                elif not slot["afford"]:
                    log(f"[i] {name} is for sale but you cannot afford it")
                elif slot["gem"] and budget and price and spent + price > budget:
                    log(f"[i] {name} costs {price:,} gems - over the gem limit")
                elif slot["gem"] and min_gems and (price is None or gems is None):
                    log(f"[i] {name}: price or gem total not readable - not bought (gem floor {min_gems:,})")
                elif slot["gem"] and min_gems and gems - price < min_gems:
                    log(f"[i] {name} costs {price:,} gems - would leave {gems - price:,}, under the floor of {min_gems:,}")
                elif slot.get("gold") and min_gold and (price is None or gold is None):
                    log(f"[i] {name}: price or gold total not readable - not bought (gold floor {min_gold:,})")
                elif slot.get("gold") and min_gold and gold - price < min_gold:
                    log(f"[i] {name} costs {price:,} gold - would leave {gold - price:,}, under the floor of {min_gold:,}")
                else:
                    bx, by, bw, bh = slot["btn"]
                    g0, m0 = market_totals(img)
                    dev.tap(bx + bw / 2, by + bh / 2)
                    ok_buy = market_confirm(dev, log, stop)       # "Are you sure you want to purchase X?" -> Confirm
                    after = dev.screenshot()
                    for _w in range(4):                          # the shop comes back after the dialog / reward pop-up
                        if market_on_screen(after) or stop.is_set():
                            break
                        _nap(stop, 0.8, 1.2)
                        after = dev.screenshot()
                    g1, m1 = market_totals(after) if market_on_screen(after) else (None, None)
                    paid = (slot["gem"] and m0 is not None and m1 is not None and m1 < m0) or \
                           ((not slot["gem"]) and g0 is not None and g1 is not None and g1 < g0)
                    gone = market_on_screen(after) and market_match(after, slot, tname, th) < th
                    if ok_buy is None:                       # a refused Rebuy Chance box: already logged, not a failure
                        failed.add(si)
                    elif not ok_buy or not (paid or (gone and (m1 is None or m0 is None))):
                        failed.add(si)                       # the purchase did not go through
                        log(f"[!] {name}: not bought (confirm {'tapped' if ok_buy else 'not found'}; "
                            f"{'gems' if slot['gem'] else 'gold'} {m0 if slot['gem'] else g0} -> {m1 if slot['gem'] else g1})")
                        if not market_on_screen(after):
                            cv2.imwrite(str(USER / "debug" / "black_market_not_bought.png"), after)
                    else:
                        bought[name] = bought.get(name, 0) + 1
                        last_prog = time.time()
                        if slot["gem"] and price:
                            spent += price
                            pend += price
                        log(f"[+] Bought {name} for {price if price is not None else '?'} "
                            f"{'gems' if slot['gem'] else 'resources'}")
                    again = True
                break
            if again:
                break
        if again:
            continue                                         # the shop changed after buying: look again
        if mode == "none" or refreshes >= limit:
            break
        _, refresh = _market_buttons(img)
        free = free_refreshes(img, refresh) if free_left != 0 else 0
        free_left = free
        if not free:
            if not paid_ok:
                log("[i] Black Market: no free refreshes left")
                break
            if cost_seen is None or refreshes % 10 == 0:
                cost_seen = refresh_cost(img, refresh, cfg.get("market_refresh_cost", 50))
            cost = cost_seen
            if budget and spent + cost > budget:
                log("[i] Black Market: the gem limit is reached")
                end = "limit"
                break
            if min_gems and (gems is None or gems - cost < min_gems):
                log(f"[i] Black Market: a paid refresh ({cost} gems) would go under the gem floor of {min_gems:,} - stopping")
                end = "floor" if gems is not None else None
                break
        if stop.is_set():                                    # Stop pressed: no new refresh
            break
        before = _market_sig(img)
        dev.tap(refresh[0] + refresh[2] / 2, refresh[1] + refresh[3] / 2)
        shot = None
        for _p in range(8):                                  # look every ~0.4 s until the shop shows new goods (was a fixed ~4 s wait)
            if stop.wait(0.4):
                break
            shot = dev.screenshot()
            if not market_on_screen(shot):
                break
            sg = _market_sig(shot)
            if (_sig_diff(sg, before) or 0) >= 2.0:
                for _s in range(4):                          # new goods: wait until two pictures in a row agree (icons done drawing)
                    if stop.wait(0.3):
                        break
                    s2 = dev.screenshot()
                    g2s = _market_sig(s2) if market_on_screen(s2) else None
                    if g2s is not None and _sig_diff(g2s, sg) < 2.0:
                        shot = s2
                        break
                    shot, sg = s2, g2s
                    if sg is None:
                        break
                break
        if stop.is_set():
            break
        if not market_on_screen(shot):                       # a dialog (confirm / not enough gems)
            p = find_text(shot, ["Confirm", "OK"])
            if p and not find_text(shot, ["enough", "insufficient", "Not enough"]):
                dev.tap(*p)
                _nap(stop, 1.0, 1.5)
            _nap(stop, 0.8, 1.2)
            shot = dev.screenshot()
        if not market_on_screen(shot):
            log("[!] Black Market: a dialog opened instead of refreshing (out of gems?) - stopping")
            end = "nogems" if (gems is not None and cost_seen and gems < 2 * cost_seen) else None
            dev.back()
            _nap(stop, 0.8, 1.2)
            break
        d = _sig_diff(_market_sig(shot), before)
        if d is not None and d < 2.0:
            no_change += 1
            if no_change >= 2:
                log("[!] Black Market: the shop did not change after refreshing (out of gems?) - stopping")
                end = "nogems" if (gems is not None and cost_seen and gems < 2 * cost_seen) else None
                if keep and not end:
                    left_for = "stall"                       # gems left but the shop does not move: frozen -> restart Evony
                break
            continue
        no_change = 0
        next_img = shot
        if not free:
            spent += cost
            pend += cost
        refreshes += 1
        last_prog = time.time()
        failed.clear()
        log(f"[*] Black Market: refresh {refreshes}" + (f" of {limit}" if mode == "count" else ""))
    total = ", ".join(f"{n} x{c}" for n, c in bought.items()) or "nothing"
    log(f"[+] Black Market done: bought {total}; {refreshes} refresh(es)")
    sg, si, sr = bm_spend_add(serial, spent, sum(bought.values()), refreshes)
    if bought or refreshes:
        log(f"[*] Black Market today: {sg:,} gems -> items x{si}, {sr} refreshes")
    if not keep or stop.is_set():
        return
    now = time.time()
    # owner 24 Oct: never 'done for the day' - low gems = an hour of normal work, then a shop visit reads the gems again
    if left_for == "stall":
        bm_save(serial, next_at=0, wait_gems=False)
        try:
            force_restart_game(dev, cfg, log, stop, must=True)
        except Exception as e:
            log(f"[!] Black Market: restarting Evony failed: {e}")
    elif left_for:
        bm_save(serial, next_at=0, wait_gems=False)
        log(f"[i] Black Market: leaving the shop for {left_for} - back right after")
    elif end in ("floor", "nogems"):
        nxt = now + BM_LOW_WAIT_MIN * 60
        row = (load_duty_state().get(serial or "default") or {}).get("black_market") or {}
        bm_save(serial, next_at=nxt, wait_gems=True, end_why=end, end_gems=gems)
        log(f"[i] Black Market: {'gem floor reached' if end == 'floor' else 'out of gems'}"
            f"{f' (gems {gems:,})' if isinstance(gems, int) else ''} - normal work until "
            f"{time.strftime('%H:%M', time.localtime(nxt))}, then a look at the gems")
        if not row.get("wait_gems"):                         # once per low spell, not every hour
            notify(cfg, serial, f"Black Market: {'gem floor reached' if end == 'floor' else 'out of gems'}"
                                f"{f' ({gems:,} gems)' if isinstance(gems, int) else ''} - looking again every hour")
    elif end == "limit":                                     # 'most gems in one visit': this visit only
        bm_save(serial, next_at=now + BM_AGAIN_MIN * 60, wait_gems=False)
        log(f"[i] Black Market: gem limit for this visit reached - next visit in {BM_AGAIN_MIN} min")
    else:                                                    # the shop closed / a dialog: straight back soon
        bm_save(serial, next_at=now + 120, wait_gems=False)
        log("[i] Black Market: visit ended early - back in 2 min")


def fix_building_name(text):
    """A building name read from the ring menu, made whole when only part was read ('pital' -> 'Hospital').
    Only when exactly one known building fits; otherwise the text is returned as it was."""
    t = (text or "").strip()
    if not t or t in BUILDING_NAMES or t in BUILDING_ALIASES:
        return t
    low = t.lower()
    hits = [b for b in BUILDING_NAMES if len(low) >= 4 and low in b.lower()]
    if len(hits) != 1:
        import difflib
        hits = difflib.get_close_matches(t, list(BUILDING_NAMES), n=2, cutoff=0.8)
        hits = hits[:1] if len(hits) == 1 else []
    return hits[0] if hits else t


def ring_building_name(shot):
    """The name the game prints under the building whose ring menu is open: a known building name (comma separated when
    two are read) or, failing that, whatever the label says. -> (name, ...) or None."""
    try:
        import difflib
        hits = []
        for t, _ in ocr_lines(shot[400:620, 110:430], 3.0):
            m = difflib.get_close_matches(t.strip(), list(BUILDING_NAMES), n=1, cutoff=0.7)
            if m:
                hits.append(m[0])
        return (", ".join(dict.fromkeys(hits)),) if hits else ring_label_free(shot)
    except Exception:
        return ring_label_free(shot)


def run_step(dev, cfg, log, stop, step):
    """One step of a city task. Steps: tap x,y | tap_text A|B | tap_img name | repeat_text A|B | wait s | back |
    swipe x1,y1,x2,y2. A step that finds nothing is not an error."""
    step = step.strip()
    if not step:
        return
    if "{boss_" in step:                                   # coordinates from the Telegram boss finder
        b = pick_boss(cfg)
        if not b:
            log("[i]   no boss coordinates yet (Telegram tab) - step skipped")
            return
        step = (step.replace("{boss_x}", str(b["x"])).replace("{boss_y}", str(b["y"]))
                .replace("{boss_level}", str(b.get("level") or "")).replace("{boss_name}", b["name"]))
    cmd, _, arg = step.partition(" ")
    cmd, arg = cmd.lower(), arg.strip()
    if cmd == "ring_name":
        shot = dev.screenshot()
        try:
            cv2.imwrite(str(USER / "debug" / f"ring_{(arg or 'x').replace(' ', '_')}.png"), shot)
        except Exception as _e:
            quiet_err("run_step", _e)
        got = ring_building_name(shot)
        log(f"[i]   the menu that opened is called: {got[0] if got else 'no name read'}" + (f" (expected {arg})" if arg else ""))
        return
    if cmd == "market":
        run_market(dev, cfg, log, stop)
        return
    if cmd == "crazy_eggs":
        run_crazy_eggs(dev, cfg, log, stop)
        return
    if cmd == "champions":
        run_champions(dev, cfg, log, stop)
        return
    if cmd == "tavern_catalog":
        run_tavern_catalog(dev, cfg, log, stop)
        return
    if cmd == "market_capture":
        run_market_capture(dev, cfg, log, stop)
        return
    if cmd == "wheel":
        run_wheel(dev, cfg, log, stop)
        return
    if cmd == "alliance_science":
        run_alliance_science(dev, cfg, log, stop)
        return
    if cmd == "activity_go":                               # More > Activity > tile > Go: the game itself walks to the building
        activity_go(dev, cfg, log, stop, arg or "Greet Champion", probe=True)
        return
    if cmd in RD_STEPS:                                    # recorded duties (tester flows 22 Oct)
        RD_STEPS[cmd](dev, cfg, log, stop)
        return
    if cmd == "view_reset_test":
        run_view_reset_test(dev, cfg, log, stop)
        return
    if cmd == "map_city":
        run_map_city(dev, cfg, log, stop)
        return
    if cmd == "text":
        dev.text(arg)
        _nap(stop, 0.8, 1.2)
        return
    if cmd == "tap":
        x, y = [float(v) for v in arg.replace(" ", "").split(",")[:2]]
        dev.tap(x, y)
    elif cmd == "swipe":
        v = [int(float(t)) for t in arg.replace(" ", "").split(",")[:4]]
        dev.swipe(*v, 400)
    elif cmd == "wait":
        _nap(stop, float(arg or 1), float(arg or 1) + 0.3)
        return
    elif cmd == "back":
        dev.back()
    elif cmd == "tap_img":
        h = find(dev.screenshot(), arg, 0.8) if has_template(arg) else None
        if h:
            tap_hit(dev, h)
        else:
            log(f"[i]   '{arg}' not on screen")
    elif cmd in ("tap_text", "repeat_text"):
        opts = [o.strip() for o in arg.split("|") if o.strip()]
        for _ in range(10 if cmd == "repeat_text" else 1):
            if stop.is_set():
                break
            p = find_text(dev.screenshot(), opts)
            if not p:
                if cmd == "tap_text":
                    log(f"[i]   text '{arg}' not on screen")
                break
            dev.tap(*p)
            _nap(stop, 0.9, 1.3)
        return
    else:
        log(f"[!] Unknown task step: {step}")
    _nap(stop, 0.8, 1.2)


def city_task_target(dev, cfg, log, stop, task, nav_state):
    """Open the building / icon a task is about. -> True when it was tapped."""
    tgt = str(task.get("target", "")).strip()
    kind, _, name = tgt.partition(":")
    kind, name = kind.lower(), name.strip()
    if kind == "icon":
        pos = {**DEFAULT_CITY_ICONS, **(cfg.get("city_icons") or {})}.get(name)
        if not pos:
            log(f"[!] Task '{task.get('name')}': no position for icon '{name}'")
            return False
        dev.tap(*pos)
        return True
    if kind == "self":                                     # the step finds its own way from the city (e.g. alliance_science)
        return True
    if kind == "learned":                                  # by its learned picture only (owner 23 Oct: the Activity route is a teacher, not the way)
        if cfg.get("zoom_out", False) and not getattr(dev, "zoomed_out", False):
            zoom_out_city(dev, stop, log)
        leave_to_city(dev, cfg, stop, log)
        return find_learned_building(dev, cfg, log, stop, name.strip())
    if kind in ("go", "via"):                              # a PINNED building goes by the keyframe map (locate -> a few measured drags
        tile, _, bname = name.partition("|")               # -> tap); the Activity tile is only the teacher for a building not pinned yet
        if cfg.get("zoom_out", False) and not getattr(dev, "zoomed_out", False):
            zoom_out_city(dev, stop, log)                   # learned pictures are taken / searched at the widest zoom
        if bname and _pinned(dev, bname.strip()) and city_map(dev) is not None:
            leave_to_city(dev, cfg, stop, log)
            if find_learned_building(dev, cfg, log, stop, bname.strip()):
                return True
            log(f"[i]   {bname.strip()}: not reached by the map - trying the Activity route")
        elif bname and task.get("no_tile"):                   # Black Market keep-going: the learned building picture first
            leave_to_city(dev, cfg, stop, log)
            if find_learned_building(dev, cfg, log, stop, bname.strip()):
                return True
        try:
            r = activity_go(dev, cfg, log, stop, tile.strip(), route=(kind == "via"))
        except Exception as e:
            log(f"[i]   Activity route to {bname or tile}: {e} - using the city map instead")
            leave_to_city(dev, cfg, stop, log)
            r = None
        if r == "complete" and task.get("no_tile"):
            log(f"[i]   {tile.strip()}: daily done today - going to the {bname.strip()} building instead")
            r = None                                       # the gems decide, not the daily quest (owner 24 Oct)
        elif r == "complete":
            rd_nothing()
            return None                                    # this daily is already complete today: nothing to open
        if r and task.get("go_page") == "wheel" and wheel_on_page(dev.screenshot()):
            return True                                    # Go on the Wheel tile opens the Wheel page itself (no Tavern menu)
        if r and (not bname or open_building_here(dev, cfg, log, stop, bname.strip())):
            return True
        if not bname:
            return False
        leave_to_city(dev, cfg, stop, log)
        if find_learned_building(dev, cfg, log, stop, bname.strip()):     # learned from an earlier Go (tile gone today)
            return True
        if city_map(dev) is not None:                      # not pinned yet (fresh map / culture without this pin): find it by its
            log(f"[i]   {bname.strip()}: no tile today and not pinned on the city map yet - looking for it by name once")
        leave_to_city(dev, cfg, stop, log)                 # name this once; opening it pins it on the map (owner 25 Oct)
        return city_task_target(dev, cfg, log, stop, {**task, "target": "building:" + bname.strip()}, nav_state)
    if kind == "xy":
        x, y = [float(v) for v in name.split(",")[:2]]
        dev.tap(x, y)
        return True
    if kind == "tpl":                                      # an icon found by its picture (right-hand column), else by its caption
        shot = dev.screenshot()
        ce_snap(shot, "city")
        base, _, cap = name.partition("|")
        h = find(shot, base, 0.68, (420, 100, 120, 600)) if has_template(base) else None
        if h:
            log(f"[i]   icon '{base}' found at ({h[0]}, {h[1]}) match {h[2]:.2f}")
            dev.tap(h[0], h[1])
            return True
        p = find_text(shot, [cap], region=(430, 120, 110, 560), cutoff=0.7) if cap else None
        if p:
            dev.tap(p[0], p[1] - 38)
            return True
        log(f"[!] Task '{task.get('name')}': icon '{base}' not found in the right-hand icon column")
        return False
    if kind == "label":                                    # an icon in the right-hand column, found by its caption (the column shifts with the events)
        shot = dev.screenshot()
        ce_snap(shot, "city")
        p = find_text(shot, [w for w in name.split("|") if w], region=(430, 120, 110, 560), cutoff=0.75)
        log(f"[i]   caption '{name}' found at {p}")
        if not p:
            log(f"[!] Task '{task.get('name')}': caption '{name}' not found in the right-hand icon column")
            return False
        dev.tap(p[0], p[1] - 38)                           # the icon sits just above its caption
        return True
    bases = [nm.split("#")[0].strip() for nm in str(name).split("+") if nm.strip()]
    if SHRINE_ROUTE and kind == "building" and bases and all(b.lower() in SQUARE_BASES for b in bases) and str(cfg.get("city_nav", "auto")).lower() != "picture":
        done = found_shrine = False                          # every building visit starts from the Shrine, in every city style
        for b in bases:
            if stop.is_set():
                break
            leave_to_city(dev, cfg, stop, log)
            r = shrine_plot_task(dev, cfg, log, stop, task, nav_state, b)
            if r == "noshrine":
                break
            found_shrine = True
            done = done or r is None
        if found_shrine:
            return None if done else False
        log("[i] Falling back to the stored city picture")
    if kind == "building" and not nav_state.get("picture_bad") and not PICTURE_BAD.get(getattr(dev, "serial", "")) and picture_items(dev) is not None:
        have = {k.split("#")[0].lower() for k in picture_items(dev)}
        for nm in str(name).split("+"):
            base = nm.split("#")[0].strip()
            if base and base.lower() not in have and base.lower() not in RESOURCE_LOWER:
                if not nav_state.get("anav"):
                    nv = CityNav(dev, stop, log)
                    nv.absolute = True
                    nv.quiet = True
                    nav_state["anav"] = nv
                serial_p = getattr(dev, "serial", "")
                if PLOT_NOMATCH.get(serial_p) or find_plot(dev, cfg, log, stop, nav_state["anav"], base) == "nomatch":
                    PLOT_NOMATCH[serial_p] = True
                    if SHRINE_ROUTE and "+" not in str(name):  # count the plots from the Shrine (fixed buildings are the same in every style)
                        leave_to_city(dev, cfg, stop, log)
                        r = shrine_plot_task(dev, cfg, log, stop, task, nav_state, base)
                        if r is None:
                            return None
                    nav_state["mode"] = "labels"               # another city style: walk by the names under the buildings
                    pr = building_priors().get(BUILDING_ALIASES.get(base, base)) or building_priors().get(base)
                    if pr:
                        nav_state.setdefault("extra", {})[f"{base}#1"] = [round(SHRINE_LABEL_IN_PICTURE[0] + pr[0]), round(SHRINE_LABEL_IN_PICTURE[1] + pr[1])]
                leave_to_city(dev, cfg, stop, log)
    if kind == "building" and not nav_state.get("picture_bad") and not (PICTURE_BAD.get(getattr(dev, "serial", "")) and own_city_map(dev)) and picture_items(dev) is not None and str(name).lower().split("+")[0].split("#")[0] in {k.split("#")[0].lower() for k in {**picture_items(dev), **nav_state.get("extra", {})}}:
        pitems = {**picture_items(dev), **nav_state.get("extra", {})}                          # the camera is found by the whole-city picture; positions come from the mapper or the built-in default
        pkeys = []
        for nm in str(name).lower().split("+"):                # "Keep+Tavern" = visit the Keep, then the Tavern
            want = nm.strip()
            pkeys += sorted(k for k in pitems if k.split("#")[0].lower() == want.split("#")[0] and (("#" not in want) or k.lower() == want))[:int(task.get("count", 1) or 1)]
        serial_ = getattr(dev, "serial", "")
        if "mode" not in nav_state:                         # 'labels' = read the names under the buildings (works in every city style); 'picture' = find the camera by the stored whole-city picture (own style)
            forced = str(cfg.get("city_nav", "auto")).lower() in ("labels", "squares") or _flag_on() or bool(PICTURE_BAD.get(serial_))
            nav_state["mode"] = "labels" if forced else "picture"
        nav = nav_state.get("anav")
        if nav is None:
            nav = CityNav(dev, stop, log)
            nav.absolute = True
            nav.quiet = True
            nav_state["anav"] = nav
        x0, y0, w, h = SAFE_TAP_AREA
        done_any = False

        def aim(k, tx, ty, first=False):
            """-> (x, y) on the screen where the building is, inside the safe tap area, or None."""
            if nav_state["mode"] == "labels":
                return aim_by_labels(dev, cfg, log, stop, k)
            nav.seen_city = False
            nav.deadline = time.time() + 150                # this step gives up by itself after 2.5 min
            nav.goto(tx, ty)
            nav.deadline = None
            if stop.is_set():
                return None
            if first and not nav.seen_city:                 # the stored picture does not fit this city style
                PICTURE_BAD[serial_] = True
                if own_city_map(dev):                       # this account has its own city map: the closed-loop older way
                    nav_state["picture_bad"] = True
                    return "older"
                nav_state["mode"] = "labels"
                log("[i] Finding the buildings by their names")
                return aim_by_labels(dev, cfg, log, stop, k)
            px_, py_ = tx - nav.cx, ty - nav.cy
            return (px_, py_) if (x0 <= px_ <= x0 + w and y0 <= py_ <= y0 + h) else None

        if cfg.get("zoom_out", False) and not getattr(dev, "zoomed_out", False):
            zoom_out_city(dev, stop, log)                   # buildings are found at the zoomed-out size (owner 23 Oct)
        for k in pkeys:
            tx, ty = pitems[k]
            log(f"[*] Going to {k}")
            dev.expect_city = True
            pos = aim(k, tx, ty, first=True)
            if pos == "older":
                break
            if not pos:
                log(f"[!] Task '{task.get('name')}': {k} could not be brought into view - skipped")
                continue
            px, py = pos
            opened = False
            wrong = 0                                       # how often a neighbour's menu opened instead
            for attempt in range(3):                        # tap the building; when no ring menu opens (a reward panel came up, the tap landed beside it) look again and retry
                close_reward_panel(dev, stop, log)
                if find_detail_icon(dev.screenshot()):      # a ring menu is still open from before: tapping the building would close it
                    close_ring(dev, stop, (px, py + 35))
                try:
                    pre = dev.screenshot()
                    cv2.circle(pre, (int(px), int(py + float(task.get("tap_dy", 0)))), 14, (0, 0, 255), 2)
                    cv2.imwrite(str(USER / "debug" / "before_tap.png"), pre)
                except Exception as _e:
                    quiet_err("city_task_target", _e)
                dev.expect_city = False                     # from the tap on, a window opening is expected
                lower = ((-115, -150, -90) if nav_state["mode"] == "labels" else (int(cfg.get("city_tap_dy", 22) or 0), 44, 0))[attempt]      # labels sit at the building's foot; picture mode aims at the LOWER part so a tall Keep does not move the tap off it
                dev.tap(int(px) + ((55, 70, 30)[attempt] if nav_state["mode"] == "labels" and k.startswith("Keep") else 0), int(py + float(task.get("tap_dy", 0)) + lower))
                _nap(stop, 1.0, 1.4)
                shot = dev.screenshot()
                ring_task = any(str(t).split()[0] in ("ring_name", "champions", "tavern_catalog", "market_capture", "wheel") for t in task.get("steps", []) if str(t).strip())
                if find_detail_icon(shot) or not ring_task:
                    if ring_task:                           # a menu opened - but is it the one we wanted? (a camera that is a little off opens the neighbour: Wisdom Dome instead of the Keep)
                        got = ring_building_name(shot)
                        want_nm = k.split("#")[0].lower()
                        if got and got[0] and want_nm not in got[0].lower() and any(b.lower() in got[0].lower() for b in BUILDING_NAMES):
                            log(f"[!]   the menu that opened is {got[0]}, not {k} (try {attempt + 1}/3) - closing it and aiming again")
                            wrong += 1
                            close_ring(dev, stop, (px, py + 35))
                            leave_to_city(dev, cfg, stop, log)
                            dev.expect_city = True
                            pos = aim(k, tx, ty)
                            if not pos or pos == "older":
                                break
                            px, py = pos
                            continue
                    opened = True
                    break
                log(f"[i]   no building menu opened at {k} (try {attempt + 1}/3) - looking again")
                close_reward_panel(dev, stop, log)
                dev.expect_city = True
                pos = aim(k, tx, ty)
                if not pos or pos == "older":
                    break
                px, py = pos
            if not opened:
                log(f"[!] Task '{task.get('name')}': the menu of {k} did not open - skipped")
                if wrong >= 3:                              # the saved spot is wrong (e.g. counted another way): forget it, the next try searches the plots again
                    forget_plot(dev, k.split("#")[0])
                    log(f"[i]   the saved spot of {k.split('#')[0]} opens another building - forgotten, it is searched again on the next try")
                if find_detail_icon(dev.screenshot()):
                    close_ring(dev, stop, (px, py + 35))
                leave_to_city(dev, cfg, stop)
                continue
            dev.expect_city = False
            for st in task.get("steps", []):
                run_step(dev, cfg, log, stop, st)
            leave_to_city(dev, cfg, stop, log)
            if find_detail_icon(dev.screenshot()):          # leave the city clean: close the ring menu the task opened
                close_ring(dev, stop, (px, py + 35))
            done_any = True
        if done_any or not nav_state.get("picture_bad"):
            return None if done_any else False
    if kind == "building":
        items = city_map_items(dev)
        keys = sorted(k for k in items if k.split("#")[0].lower() == name.lower().split("#")[0]
                      and (("#" not in name) or k.lower() == name.lower()))
        if not keys:
            log(f"[!] Task '{task.get('name')}': '{name}' is not on the city map. {CITY_MAP_HELP}")
            return False
        if cfg.get("zoom_out", False) and not getattr(dev, "zoomed_out", False):
            zoom_out_city(dev, stop, log)
        nav = nav_state.get("nav") or CityNav(dev, stop, log)
        if not nav_state.get("nav"):
            nav.to_origin()
            nav_state["nav"] = nav
        for k in keys[:int(task.get("count", 1) or 1)]:
            pos = find_building(dev, stop, log, nav, k.split("#")[0], items[k], items, k)
            if not pos and k.split("#")[0] == "Keep":
                pos = calibrate_keep(dev, stop, log, nav)
            if not pos:
                log(f"[!] Task '{task.get('name')}': {k} not found on screen - skipped")
                continue
            close_reward_panel(dev, stop, log)
            dev.tap(pos[0], pos[1] + float(task.get("tap_dy", 0)))
            _nap(stop, 1.0, 1.4)
            for st in task.get("steps", []):
                run_step(dev, cfg, log, stop, st)
            leave_to_city(dev, cfg, stop)
        return None                                        # steps already ran for each building
    if kind == "square":                                   # "square:1" = numbered square 1 (Keep) of the default city map
        nav = nav_state.get("nav") or CityNav(dev, stop, log)
        nav_state["nav"] = nav
        if not nav_state.get("shrine"):
            nav_state["shrine"] = locate_shrine(dev, stop, log, nav)
        if not nav_state.get("shrine"):
            return False
        try:
            nums = [int(v) for v in name.replace(";", ",").split(",") if v.strip()]
        except ValueError:
            log(f"[!] Task '{task.get('name')}': square target must be a number, e.g. square:1")
            return False
        for n in nums:
            open_square(dev, cfg, log, stop, nav, nav_state["shrine"], n)
            leave_to_city(dev, cfg, stop)
        return None
    log(f"[!] Task '{task.get('name')}': target must look like icon:Mail, building:Quarry or xy:100,200")
    return False


GAIN = 1.45                                               # the map follows a finger ~1.45x as far (measured on a real city)


def building_priors():
    """Where each building sits relative to the others (the built-in default squares). City styles differ by some tens of
    pixels, which the closed loop in aim_by_labels corrects as the real names come into view."""
    pr = {}
    for _n, (nm, x, y) in CITY_SQUARES.items():
        pr.setdefault(re.sub(r"\s*\d+$", "", nm), (x, y))
    return pr


def labels_on_screen(dev, stop):
    """Show the building names (a finger that has moved is held down) and read them -> [(name, x, y), ...] where x, y are
    the screen position AFTER the finger was lifted (the map may have moved a little)."""
    nav = CityNav(dev, stop, lambda *_: None)
    cx0, cy0 = nav.cx, nav.cy
    look_with_names(dev, stop, nav)
    dxa, dya = cx0 - nav.cx, cy0 - nav.cy                      # how far the map content moved between the rest picture and now
    return [(n, x + dxa, y + dya) for n, x, y in getattr(nav, "last_rest_labels", [])]


def pan_content(dev, stop, dx, dy):
    """Move the map content by about (dx, dy) screen px. Short finger moves can be read by the game as a TAP, so a small
    move is made as a long drag past the target and a second drag back."""
    if stop.is_set():
        return
    fx, fy = dx / GAIN, dy / GAIN
    fx, fy = max(-210, min(210, fx)), max(-320, min(320, fy))
    ln = (fx * fx + fy * fy) ** 0.5
    if ln < 1:
        return
    if ln < 110:
        ux, uy = fx / ln, fy / ln
        ax, ay = fx + ux * 110, fy + uy * 110                  # first beyond, then back
        dev.swipe(270 - ax / 2, 440 - ay / 2, 270 + ax / 2, 440 + ay / 2, 450)
        _nap(stop, 0.9, 1.2)
        dev.swipe(270 + ux * 55, 440 + uy * 55, 270 - ux * 55, 440 - uy * 55, 450)
    else:
        dev.swipe(270 - fx / 2, 440 - fy / 2, 270 + fx / 2, 440 + fy / 2, 450)
    _nap(stop, 0.9, 1.2)


SWEEP = [(0, 280), (280, 0), (0, -280), (0, -280), (-280, 0), (-280, 0), (0, 280), (0, 280), (0, 280),
         (280, 0), (280, 0), (280, 0), (0, -280), (0, -280), (0, -280), (0, -280)]   # content moves: a square spiral around the start


ZOOM_HINT = "City is zoomed in too far - pinch the city fully out once (two fingers together), then the bot can read the building names"


def aim_by_labels(dev, cfg, log, stop, name, tries=24):
    """Bring a building into the safe tap area by reading the names written under the buildings - works in every city
    style, no picture and no counting needed. Whatever names are in view tell where the wanted building should be
    (their relative places are known); the map is moved that way and the names are read again, until the wanted
    building's own name is in view. -> (x, y) of its name on the screen, or None."""
    pri = building_priors()
    want = name.split("#")[0]
    if want not in pri:
        return None
    x0, y0, w, h = SAFE_TAP_AREA
    lost = blank = 0
    t_end = time.time() + 150                              # this step gives up by itself after 2.5 min
    for i in range(tries):
        if stop.is_set() or time.time() > t_end:
            return None
        labs = labels_on_screen(dev, stop)
        hit = [l for l in labs if l[0] == want]
        if hit:
            n_, x, y = hit[0]
            if x0 <= x <= x0 + w and y0 <= y <= y0 + h:
                log(f"[i]   {want} found on screen at {x},{y}")
                return x, y
            pan_content(dev, stop, 270 - x, 440 - y)
            continue
        ests = [(lx + pri[want][0] - pri[ln][0], ly + pri[want][1] - pri[ln][1]) for ln, lx, ly in labs if ln in pri]
        if not ests:
            lost += 1
            blank = blank + 1 if not labs else 0
            if blank >= 3:                                 # not one name read three times running: the city is zoomed in too far
                log("[!] " + ZOOM_HINT)
                raise RuntimeError(ZOOM_HINT)
            if lost == 3 and not ensure_view(dev, cfg, stop, log, "city", tries=4):
                return None
            if lost > len(SWEEP):
                return None
            dx, dy = SWEEP[lost - 1]                       # no known building in view (city edge, forest): sweep outward in a growing square
            log(f"[i]   no known building name in view ({', '.join(l[0] for l in labs) or 'nothing read'}) - looking further ({lost}/{len(SWEEP)})")
            pan_content(dev, stop, dx, dy)
            continue
        lost = 0
        ex = sum(e[0] for e in ests) / len(ests)
        ey = sum(e[1] for e in ests) / len(ests)
        log(f"[i]   names in view: {', '.join(l[0] for l in labs)} -> {want} should be at about {round(ex)},{round(ey)}")
        pan_content(dev, stop, 270 - ex, 440 - ey)
    return None


_WM_NOTE = [0.0, ""]                                       # last time a 'not the world map' screen was saved, and what was read


def world_map(shot):
    """The world map (after the globe button): its bottom-left buttons 'Bookmarks' / 'World Map' are not in the city."""
    try:
        tp = template_path("world_map_btn")                  # picture of the World Map button (OCR differs per PC - Kiravik 23 Oct)
        if tp is not None:
            t = cv2.imread(str(tp))
            if t is not None:
                a = shot[740:920, 0:160]
                r = cv2.matchTemplate(a, t, cv2.TM_CCOEFF_NORMED)
                _, mx, _, (x, y) = cv2.minMaxLoc(r)
                if mx >= 0.85 and a[y:y + t.shape[0], x:x + t.shape[1]].mean() >= 0.8 * t.mean():   # not dimmed by a window on top
                    return True
        if find_text(shot, ["World Map", "Bookmarks"], region=(0, 600, 200, 330), cutoff=0.75) is not None:
            return True
        texts = [t for t, _ in ocr_lines(shot[600:930, 0:200], 2.5, bright=True)]
        texts += [t for t, _ in ocr_lines(shot[800:930, 80:460], 2.5, bright=True)]   # the X:0634 Y:0591 box at the bottom
        joined = " ".join(texts).lower()
        hit = ("world" in joined and "map" in joined) or "bookmark" in joined or re.search(r"x\W{0,2}\d{3,4}\W+y\W{0,2}\d{3,4}", joined) is not None
        if not hit and time.time() - _WM_NOTE[0] > 60:     # diagnostics: what was read when the world map was not recognised
            _WM_NOTE[0] = time.time()
            try:
                cv2.imwrite(str(USER / "debug" / "not_world_map.png"), shot)
            except Exception as _e:
                quiet_err("world_map", _e)
            _WM_NOTE[1] = joined[:120]
        return hit
    except Exception:
        return False


def view_of(shot, cfg):
    """Which view the game shows: 'city', 'landscape' (the rally-list view), 'world' (the world map) or 'other'."""
    st = screen_state(shot, cfg)
    if st == "city":
        return "city"
    if st == "rally_list":
        return "landscape"
    return "world" if world_map(shot) else "other"


GROUND_TAP = (8, 480)                     # left edge, middle: empty ground in the city and on the map (no button there)


def buttons_hidden(shot):
    """The game shows the city / map with ALL its buttons hidden: no avatar, no globe, no gold Back arrow, no window
    frame - just the picture, full of detail. -> True."""
    try:
        if has_template("city_screen") and find(shot, "city_screen", 0.8, STATE_REGIONS["city_screen"]):
            return False
        if world_map(shot):
            return False
        g = cv2.cvtColor(shot, cv2.COLOR_BGR2GRAY)
        if float(g.std()) < 30 or float(g[100:860].mean()) < 45:     # dark / dimmed = a window or a pop-up over it
            return False
        hsv = cv2.cvtColor(shot[8:80, 5:75], cv2.COLOR_BGR2HSV)
        if cv2.countNonZero(cv2.inRange(hsv, (12, 110, 150), (32, 255, 255))) > 250:   # gold Back arrow of a full page
            return False
        top = cv2.cvtColor(shot[0:70], cv2.COLOR_BGR2HSV)
        red = cv2.countNonZero(cv2.inRange(top, (0, 120, 80), (8, 255, 255))) + cv2.countNonZero(cv2.inRange(top, (170, 120, 80), (180, 255, 255)))
        return red < 0.25 * top.shape[0] * top.shape[1]                 # a red title bar = a game page, not the bare map
    except Exception:
        return False


_VIEW_FAILS = {}           # serial -> times ensure_view could not reach the wanted view (last 5 min)
_VIEW_RESTARTING = set()  # serials restarting Evony because recovery went round in circles


def game_screen_shown(shot):
    """Evony has finished loading and shows something the recovery routine can work with (not a loading screen): the world
    map, a full page (gold Back arrow / red title bar), a window over a dimmed view or the 'exit the game?' box."""
    try:
        if world_map(shot) or (has_template("popup_exit_cancel") and find(shot, "popup_exit_cancel", 0.85)):
            return True
        hsv = cv2.cvtColor(shot[8:80, 5:75], cv2.COLOR_BGR2HSV)
        if cv2.countNonZero(cv2.inRange(hsv, (12, 110, 150), (32, 255, 255))) > 250:   # gold Back arrow of a full page
            return True
        top = cv2.cvtColor(shot[0:70], cv2.COLOR_BGR2HSV)
        red = cv2.countNonZero(cv2.inRange(top, (0, 120, 80), (8, 255, 255))) + cv2.countNonZero(cv2.inRange(top, (170, 120, 80), (180, 255, 255)))
        return red > 0.25 * top.shape[0] * top.shape[1]                 # red title bar of a game page
    except Exception:
        return False


def ensure_view(dev, cfg, stop, log=None, want="city", tries=12):
    """Run the recovery routine; when it fails 3 times in 5 min (going round in circles, e.g. Rob 25 Oct 11:05: landscape ->
    city button -> 'unexpected window' -> Back -> landscape ... for minutes) restart Evony instead of trying again."""
    ok = _ensure_view(dev, cfg, stop, log, want, tries)
    serial = getattr(dev, "serial", None)
    if ok or stop.is_set() or serial is None or serial in _VIEW_RESTARTING:
        return ok
    now = time.time()
    fails = [t for t in _VIEW_FAILS.get(serial, []) if now - t < 300] + [now]
    _VIEW_FAILS[serial] = fails
    if len(fails) < 3:
        return ok
    _VIEW_FAILS[serial] = []
    _VIEW_RESTARTING.add(serial)
    try:
        lg = log or (lambda m: None)
        lg("[!] Recovery went round in circles 3 times in 5 min - restarting Evony")
        force_restart_game(dev, cfg, lg, stop)
        return _ensure_view(dev, cfg, stop, log, want, tries)
    finally:
        _VIEW_RESTARTING.discard(serial)


def _ensure_view(dev, cfg, stop, log=None, want="city", tries=12):
    """THE universal recovery routine. Whatever is on the screen - a building's window, stacked pages, a pop-up, a ring menu,
    a reward panel, the 'exit the game?' box - get out of it and land on the wanted view: 'city' or 'landscape'.
    Already there = nothing is pressed. Already in the other main view = the round city / landscape button is tapped.
    Back is never pressed on the city (it would ask to quit the game). -> True when the wanted view shows."""
    say = (lambda m: log(m)) if log else (lambda m: None)
    if not has_template("city_screen") and not has_template("rally_screen") and not has_template("join_button"):
        return True                                          # nothing to recognise the views by: do not press anything blindly
    toggled = 0
    unknown = 0
    quit_seen = False                                        # Back already opened 'exit the game?': we are on a main view - never Back again
    for i in range(tries):
        if stop.is_set():
            return False
        shot = dev.screenshot()
        if cancel_quit_dialog(dev, shot, lambda *_: None):
            say("[*] Recovery: the game asked to exit - tapped Cancel")
            quit_seen = True
            _nap(stop, 0.8, 1.2)
            continue
        v = view_of(shot, cfg)
        ring = v == "city" and find_detail_icon(shot)
        if v == want and not ring:
            return True
        if ring:
            close_ring(dev, stop, (270, 430))
            continue
        if v not in ("city", "landscape") and find_text(shot, ["disconnected", "someone login", "logged in elsewhere", "another device"],
                                                           region=(60, 250, 360, 300), cutoff=0.8):
            say("[!] Recovery: the game was opened on another device - not pressing anything (the main loop waits and reclaims it)")
            return False                                     # the duty fails; the main loop's kicked handling takes over
        if v == "world" and want == "city":                 # the world map: the globe button goes back to the city (Back would ask to quit)
            say("[*] Recovery: on the world map - tapped the globe to go to the city")
            dev.tap(*VIEW_TOGGLE)
            _nap(stop, 3.0, 4.0)
            continue
        if v in ("city", "landscape"):                      # the other main view: switch (twice at most)
            if toggled >= 2:
                return False
            toggled += 1
            say(f"[*] Recovery: showing the {v} view - switching to the {want} view")
            dev.tap(*VIEW_TOGGLE)
            _nap(stop, 4.0, 5.0) if want == "world" else _nap(stop, 2.2, 3.0)   # the world map takes longer to draw
            continue
        if close_reward_panel(dev, stop, None):
            continue
        hit = None
        th = min(cfg.get("threshold", 0.85), 0.85)
        for name in list_templates("popup_"):
            if name == "popup_exit_cancel":
                continue
            h = find(shot, name, th)
            if h:
                hit = (name, h)
                break
        if hit:
            tap_hit(dev, hit[1])
            say(f"[*] Recovery: closed a pop-up ({hit[0]})")
            _nap(stop, 0.8, 1.2)
            continue
        unknown += 1
        if want == "world" and toggled and unknown <= 3:   # just switched: the world map may still be loading - Back there asks to quit
            _nap(stop, 2.0, 2.5)
            continue
        cx = find_close_x(shot) if unknown % 2 == 1 else []
        if cx:
            dev.tap(cx[0][0], cx[0][1])
            say(f"[*] Recovery: closed a window with its X button ({cx[0][0]},{cx[0][1]})")
        elif unknown <= 2 and buttons_hidden(shot):        # the city/map with all game buttons hidden (a tap on empty ground hides them)
            dev.tap(*GROUND_TAP)
            say("[*] Recovery: the game's buttons were hidden - tapped the ground to show them")
            _nap(stop, 1.0, 1.4)
            continue
        elif (unknown >= 5 or quit_seen) and toggled < 2:   # backing out leads nowhere known / Back asked to quit: a main view not recognised
            toggled += 1
            dev.tap(*VIEW_TOGGLE)
            say("[*] Recovery: screen not recognised - tapped the city / landscape button")
        elif quit_seen:                                     # owner 25 Oct: Back on the city / map only asks to quit - stop here
            say("[!] Recovery: on a main view the bot does not recognise - not pressing Back (it only asks to quit)")
            return False
        else:
            dev.back()
            say("[*] Recovery: unexpected window - pressed Back")
        _nap(stop, 0.8, 1.2)
    return False


def leave_to_city(dev, cfg, stop, log=None):
    """Close whatever a task opened and land on the city view (see ensure_view)."""
    return ensure_view(dev, cfg, stop, log, "city")


# ------------------------------------------------------------ Crazy Eggs (Event Center > Activities > Crazy Eggs)
# Screen (540x960): 4 eggs in a 2x2 grid. Under each egg: "Scout Cost: N" (ready) / "Waiting: hh:mm:ss" / "Activated".
# Smashing needs no confirmation; the reward panel ("Congratulations!") closes by tapping outside it.
# Counters under "Reset Countdown": normal hammers (left) and the Lucky Hammer (right, 1 per day).
CE_BANDS = [(20, 505, 250, 70), (270, 505, 250, 70), (20, 765, 250, 70), (270, 765, 250, 70)]   # label under each egg
CE_EGG = [(160, 465), (377, 465), (160, 725), (377, 725)]                                         # tap point of each egg
CE_CLOSE = (30, 890)                                                                              # empty curtain, closes the reward panel
CE_HAMMERS = (115, 325, 72, 32)
CE_LUCKY = (375, 325, 60, 32)
CE_ACTIVITIES_TAB = (270, 100)                                                                    # Event Center tab, used when its text is not read
CE_COSTS = ["3", "6", "8", "12"]


def wheel_conf(cfg):
    c = dict(cfg.get("wheel") or {})
    b = int(float(c.get("button", 1) or 1))
    return {"button": b if b in WHEEL_BTN else 1, "spins": max(0, min(int(float(c.get("spins", 10) or 0)), 9999999)),
            "reserve": max(0, min(int(float(c.get("reserve", 0) or 0)), 999999999)),
            "chips": c.get("chips") if c.get("chips") in ("off", "needed", "all") else "off"}


def ce_conf(cfg):
    c = dict(cfg.get("crazy_eggs") or {})
    eggs = {k: {"on": True, "daily": 0} for k in CE_COSTS}
    for k, v in (c.get("eggs") or {}).items():
        eggs.setdefault(str(k), {"on": True, "daily": 0}).update(v or {})
    gap = c.get("min_gap")
    return {"eggs": eggs, "lucky": str(c.get("lucky", "max") or ""), "min_hammers": int(c.get("min_hammers", 0) or 0),
            "min_gap": 30 if gap in (None, "") else max(0, min(int(float(gap)), 600))}      # minutes between visits (0 = as soon as an egg is ready)


def ce_row(serial):
    return (load_duty_state().get(serial or "default") or {}).get("crazy_eggs") or {}


BM_AGAIN_MIN = 10        # Black Market 'until the gem floor' + paid refresh: comes back this many minutes after a run that did not finish


def bm_keep_going(cfg):
    """Refresh until the gem floor + paid refresh on: the Black Market runs all day until the gems are used up."""
    return cfg.get("market_refresh_mode") == "all" and bool(cfg.get("market_paid_refresh"))


def bm_save(serial, **kw):
    with _DUTY_LOCK:
        data = load_duty_state()
        row = data.setdefault(serial or "default", {}).setdefault("black_market", {})
        row.update(kw)
        save_duty_state(data)


BM_LOW_WAIT_MIN = 60     # gems at the floor / out of gems: normal work for an hour, then a shop visit reads the gems again (owner 24 Oct)
BM_STALL_MIN = 4         # keep-going run: nothing bought or refreshed for this long = the shop is frozen -> restart Evony, go back
BM_LEFT_FOR = {}         # serial -> {task id: time} the shop was left for (one stuck task cannot pull the bot out over and over)


def aj_renew_due(cfg, serial):
    """The game's Auto-Join needs a look now: not read since the bot started, or the time left is at the renew limit.
    Worked out from the last reading (AJ_LEFT) - no screen look."""
    if not cfg.get("game_auto_join"):                      # its own switch - not tied to bot joining (owner 25 Oct)
        return False
    r = AJ_LEFT.get(serial)
    if not r:
        return True
    limit = max(1, int(cfg.get("auto_join_renew_min", 120))) * 60
    return r[0] - (time.time() - r[1]) <= limit


def bm_leave_for(cfg, serial):
    """Inside a keep-going Black Market run: something that must run first -> its name, else None.
    Owner 24 Oct: Run now, any other due duty / task and the game's Auto-Join renew come first; the bot comes straight back."""
    now = time.time()
    seen = BM_LEFT_FOR.setdefault(serial or "default", {})
    fresh = lambda k: now - seen.get(k, 0) > 600
    if aj_renew_due(cfg, serial) and fresh("auto_join"):
        seen["auto_join"] = now
        AJ_ASK[serial] = True
        return "the game's Auto-Join check"
    try:
        due = city_tasks_due(cfg, getattr(_TL, "city_last", None) or {}, serial)
    except Exception as _e:
        quiet_err("bm_leave_for", _e)
        due = []
    for t in due:
        tid = t.get("name", "")
        if tid == "duty:black_market" or not fresh(tid):
            continue
        seen[tid] = now
        return DUTY_BY_ID[tid[5:]]["title"] if tid.startswith("duty:") and tid[5:] in DUTY_BY_ID else tid
    return None


def bm_priority_due(cfg, serial):
    """Auto-Join off + Black Market 'until the gem floor' due: the shop goes before joining rallies (owner 24 Oct)."""
    return bm_keep_going(cfg) and ((cfg.get("duties") or {}).get("black_market") or {}).get("on") \
        and duty_status(serial, "black_market", cfg)[0] == "due"


def bm_gems_checked(dev, img, prev, pend, log):
    """The gold / gem totals, with a sanity check on the gems: a total that moved by more than the bot just spent
    (pend) is read again (Kulim 24 Oct: ~400k read as 32,750). Two reads that agree are believed (a top-up in the game
    is fine); otherwise the expected total is kept. -> (gold, gems, believed_new_read)."""
    g, m = market_totals(img)
    if m is None or prev is None:
        return g, m, m is not None
    want = max(0, prev - pend)
    if abs(m - want) <= max(2000, pend // 10):
        return g, m, True
    reads = [m]
    for _ in range(3):
        time.sleep(0.6)
        s2 = dev.screenshot()
        if not market_on_screen(s2):
            break
        _, m2 = market_totals(s2)
        if m2 is None:
            continue
        if abs(m2 - want) <= max(2000, pend // 10):
            return g, m2, True
        reads.append(m2)
        if reads[-1] == reads[-2]:
            log(f"[i] Black Market: gems now {m2:,} (expected about {want:,} - read twice the same)")
            return g, m2, True
    log(f"[!] Black Market: gem total read as {m:,}, expected about {want:,} - keeping {want:,}")
    return g, want, False


def bm_spend_add(serial, gems, items, refreshes):
    """Per game day: gems spent, items bought, refreshes -> the 'Black Market today' line."""
    with _DUTY_LOCK:
        data = load_duty_state()
        row = data.setdefault(serial or "default", {}).setdefault("black_market", {})
        day = _game_day()
        if row.get("sp_day") != day:
            row.update(sp_day=day, sp_gems=0, sp_items=0, sp_ref=0)
        row["sp_gems"] = int(row.get("sp_gems", 0)) + int(gems or 0)
        row["sp_items"] = int(row.get("sp_items", 0)) + int(items or 0)
        row["sp_ref"] = int(row.get("sp_ref", 0)) + int(refreshes or 0)
        save_duty_state(data)
        return row["sp_gems"], row["sp_items"], row["sp_ref"]


def bm_spend_text(row):
    if row.get("sp_day") != _game_day() or not (row.get("sp_gems") or row.get("sp_items") or row.get("sp_ref")):
        return ""
    return f"today {int(row.get('sp_gems', 0)):,} gems, {int(row.get('sp_items', 0))} items, {int(row.get('sp_ref', 0))} refreshes"


def ce_save(serial, **kw):
    with _DUTY_LOCK:
        data = load_duty_state()
        row = data.setdefault(serial or "default", {}).setdefault("crazy_eggs", {})
        row.update(kw)
        save_duty_state(data)


def ce_read_slot(img, i):
    """-> ("ready", cost) | ("wait", seconds) | ("done",) | None for egg slot i (0 top-left, 1 top-right, 2 bottom-left, 3 bottom-right)."""
    x, y, w, h = CE_BANDS[i]
    txt = " ".join(t for t, _ in ocr_lines(img[y:y + h, x:x + w], 3.0)).lower().replace(";", ":")
    if "activ" in txt:
        return ("done",)
    m = re.search(r"(\d{1,2})\s*[:.]\s*(\d{2})\s*[:.]\s*(\d{2})", txt)
    if m:
        return ("wait", int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)))
    if "wait" in txt:
        return ("wait", 600)
    if "cost" in txt or "scout" in txt:
        nums = re.findall(r"\d+", txt.split("cost")[-1])
        if nums and 1 <= int(nums[-1]) <= 99:
            return ("ready", int(nums[-1]))
    return None


def ce_read_count(img, box, big=False):
    """Read the number in a counter box ("51.6K", "1"): several black/white cuts of the picture, first one that gives digits."""
    x, y, w, h = box
    g = cv2.resize(cv2.cvtColor(img[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY), None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    cfg = "--psm 7 -c tessedit_char_whitelist=0123456789.KkMm"
    for cut in (cv2.threshold(g, 150, 255, cv2.THRESH_BINARY)[1], g, cv2.threshold(g, 120, 255, cv2.THRESH_BINARY)[1]):
        try:
            txt = pytesseract.image_to_string(cut, config=cfg).strip()
        except Exception:
            return None
        m = re.search(r"(\d+(?:\.\d+)?)\s*([kKmM]?)", txt)
        if m:
            return int(float(m.group(1)) * {"k": 1000, "m": 1000000}.get(m.group(2).lower(), 1))
    return None


def ce_snap(img, tag):
    """Keep the last screenshot of each Crazy Eggs stage (overwritten every run) so a failure can be looked at."""
    try:
        d = USER / "debug"
        d.mkdir(exist_ok=True)
        cv2.imwrite(str(d / f"crazy_eggs_{tag}.png"), img)
    except Exception as _e:
        quiet_err("ce_snap", _e)


def ce_on_screen(img):
    if find_text(img, ["Event Center"], region=(100, 0, 340, 70), cutoff=0.8):    # the Event Center list itself is never the eggs page
        return False
    return bool(find_text(img, ["Reset Countdown", "Scout Cost", "Activate all"], cutoff=0.8))


CE_CONFIRM = (360, 720)          # the green Confirm button of the Lucky Hammer dialog (fallback tap point)


def ce_lucky_dialog(img):
    return bool(find_text(img, ["Confirm use?", "cooldown, but it can only"], cutoff=0.6))


def ce_lucky_confirm(dev, stop, force=False):
    """Tap Confirm on the Lucky Hammer dialog if it is open. True when it was tapped."""
    img = dev.screenshot()
    if not (force or ce_lucky_dialog(img)):
        return False
    p = find_text(img, ["Confirm"], region=(200, 600, 340, 200), cutoff=0.6) or CE_CONFIRM
    dev.tap(*p)
    _nap(stop, 1.8, 2.4)
    return True


def ce_close_panel(dev, stop):
    """Close the 'Congratulations!' reward panel by tapping the empty curtain."""
    for _ in range(3):
        if stop.is_set():
            return
        if not find_text(dev.screenshot(), ["Congratulations"], cutoff=0.72):
            return
        dev.tap(*CE_CLOSE)
        _nap(stop, 0.9, 1.3)


def ce_find_in_list(dev, stop, img):
    """Find the Crazy Eggs row on the Activities tab: the list order differs per account, so look
    down the whole list (until it stops moving), then back up to the top. -> (x, y) or None."""
    names = ["Crazy Eggs", "Crazy Egg"]
    p = find_text(img, names, cutoff=0.72)
    for sx, sy, ex, ey in ((270, 760, 270, 330), (270, 330, 270, 760)):     # down, then up
        last = img
        for _ in range(8):
            if p or stop.is_set():
                return p
            dev.swipe(sx, sy, ex, ey, 450)
            _nap(stop, 1.1, 1.5)
            img = dev.screenshot()
            p = find_text(img, names, cutoff=0.72)
            if not p and cv2.absdiff(img[140:900], last[140:900]).mean() < 2:   # end of the list
                break
            last = img
    return p


def run_crazy_eggs(dev, cfg, log, stop):
    """On the Crazy Eggs screen: smash the ready eggs, use the Lucky Hammer, then note when to come back."""
    if ce_lucky_confirm(dev, stop):
        log("[i]   Crazy Eggs: a Lucky Hammer dialog was left open - confirmed it")
        ce_close_panel(dev, stop)
    serial = getattr(dev, "serial", "default")
    conf = ce_conf(cfg)
    day = _game_day()

    def act(text):
        log("@act " + json.dumps({"k": "step", "text": text}))

    first = dev.screenshot()
    ce_snap(first, "0_start")
    if not ce_on_screen(first):                            # we are on the Event Center: Activities tab > Crazy Eggs
        for attempt in range(2):
            if stop.is_set():
                return
            p = find_text(dev.screenshot(), ["Activities"], region=(0, 70, 540, 70), cutoff=0.7) or CE_ACTIVITIES_TAB
            log(f"[i]   Crazy Eggs: tapping the Activities tab at {p}")
            dev.tap(*p)
            _nap(stop, 1.6, 2.2)
            img = dev.screenshot()
            ce_snap(img, "1_activities")
            p = ce_find_in_list(dev, stop, img)
            if p:
                dev.tap(*p)
                _nap(stop, 2.5, 3.2)
                if ce_on_screen(dev.screenshot()):
                    break
        else:
            ce_snap(dev.screenshot(), "2_failed_" + re.sub(r"\W", "_", str(serial)))   # one picture per instance
            raise RuntimeError("the Crazy Eggs screen did not open")
    row = ce_row(serial)
    smashes = dict(row.get("smashes") or {}) if row.get("smash_day") == day else {}
    slot_cost = {int(k): v for k, v in (row.get("slot_cost") or {}).items()}
    lucky_used = row.get("lucky_day") == day
    lucky_tries = int(row.get("lucky_tries", 0)) if row.get("lucky_try_day") == day else 0
    bad = {}                                               # slot -> times a tap did not change anything
    for _ in range(40):
        if stop.is_set():
            return
        ce_close_panel(dev, stop)
        img = dev.screenshot()
        eggs = [ce_read_slot(img, i) for i in range(4)]
        if any(e is None for e in eggs):                   # a reward panel is probably still open: tap the empty curtain and read again
            dev.tap(*CE_CLOSE)
            _nap(stop, 1.0, 1.4)
            img = dev.screenshot()
            eggs = [ce_read_slot(img, i) for i in range(4)]
        if all(e is None for e in eggs) and not ce_on_screen(img):   # not the eggs page (another event opened): stop instead of tapping around
            ce_snap(img, "2_failed")
            raise RuntimeError("the Crazy Eggs screen is not showing (another page opened)")
        ce_snap(img, "3_eggs")
        hammers = ce_read_count(img, CE_HAMMERS)
        lucky_n = ce_read_count(img, CE_LUCKY)
        for i, e in enumerate(eggs):
            if e and e[0] == "ready":
                slot_cost[i] = str(e[1])
        log(f"[i]   Crazy Eggs: {eggs} hammers={hammers} lucky={lucky_n}")
        pick = None
        for i, e in enumerate(eggs):
            if not (e and e[0] == "ready") or bad.get(i, 0) >= 2:
                continue
            c = conf["eggs"].get(str(e[1])) or {"on": True, "daily": 0}
            if not c.get("on", True):
                continue
            if int(c.get("daily", 0) or 0) and smashes.get(str(e[1]), 0) >= int(c["daily"]):
                continue
            if hammers is not None and hammers - e[1] < conf["min_hammers"]:
                continue
            pick = (i, e[1])
            break
        if pick:
            i, cost = pick
            act(f"Crazy Eggs: smashing the {cost}-hammer egg")
            dev.tap(*CE_EGG[i])
            _nap(stop, 1.6, 2.2)
            ce_snap(dev.screenshot(), "4_after_smash")
            after = ce_read_slot(dev.screenshot(), i)
            smashes[str(cost)] = smashes.get(str(cost), 0) + 1
            bad[i] = bad.get(i, 0) + 1 if after == ("ready", cost) else 0
            dev.tap(*CE_CLOSE)                             # close the reward panel (tapping the curtain is harmless if it is already gone)
            _nap(stop, 0.9, 1.3)
            ce_close_panel(dev, stop)
            ce_save(serial, smash_day=day, smashes=smashes, slot_cost=slot_cost)
            continue
        # the Lucky Hammer: one free forced try per day on the egg that is cooling down
        want = conf["lucky"]
        if want and not lucky_used and lucky_tries < 2 and lucky_n != 0:
            waits = [(i, e[1]) for i, e in enumerate(eggs) if e and e[0] == "wait"]
            if want == "max":
                want = str(max(int(k) for k in conf["eggs"]))
            slot = next((i for i, _ in waits if slot_cost.get(i) == want), None)
            if slot is None and waits and not any(slot_cost.get(i) for i, _ in waits):
                costs = sorted(int(k) for k in conf["eggs"])
                if int(want) == costs[-1]:
                    slot = max(waits, key=lambda t: t[1])[0]       # the dearest egg has the longest wait
                elif int(want) == costs[0]:
                    slot = min(waits, key=lambda t: t[1])[0]
            if slot is not None:
                lucky_tries += 1
                ce_save(serial, lucky_try_day=day, lucky_tries=lucky_tries)
                act(f"Crazy Eggs: Lucky Hammer on the {want}-hammer egg")
                dev.tap(*CE_EGG[slot])
                _nap(stop, 1.2, 1.7)
                if ce_lucky_confirm(dev, stop):
                    lucky_used = True
                    ce_save(serial, lucky_day=day)
                    ce_close_panel(dev, stop)
                else:
                    log("[i]   Crazy Eggs: no Lucky Hammer dialog appeared")
                    dev.tap(*CE_CLOSE)
                    _nap(stop, 0.8, 1.2)
                continue
        break
    # when to come back
    img = dev.screenshot()
    eggs = [ce_read_slot(img, i) for i in range(4)]
    waits = [e[1] for e in eggs if e and e[0] == "wait"]
    if eggs and all(e and e[0] == "done" for e in eggs):
        ce_save(serial, all_done_day=day, next_at=0)
        log("[+]  Crazy Eggs: all four eggs are activated")
    elif waits:
        gap = conf["min_gap"] * 60
        ce_save(serial, next_at=time.time() + max(min(waits) + 15, gap))
        log(f"[i]   Crazy Eggs: next egg ready in {min(waits) // 60} min" + (f"; next visit in {max(min(waits) + 15, gap) // 60} min (visit gap {conf['min_gap']} min)" if gap > min(waits) + 15 else ""))
    else:
        row = ce_row(serial)
        blank = int(row.get("blank", 0)) + 1 if row.get("blank_day") == day else 1
        if blank >= 2:                                      # 2 visits in a row with no egg ready or on a timer: all hatched / event over
            ce_save(serial, all_done_day=day, next_at=0, blank=0, blank_day=day)
            log("[+]  Crazy Eggs: no egg left to smash today - done until the next reset")
        else:
            ce_save(serial, next_at=time.time() + 1800, blank=blank, blank_day=day)
    if eggs and any(e and e[0] in ("ready", "wait") for e in eggs):
        ce_save(serial, blank=0)


# ------------------------------------------------------------ Champions (Keep > Champions)
# Same 17 champions on every account, but only a few are ACTIVE on each one (an inactive one shows an "Unlock" button
# in the bottom middle). Daily limits for ALL champions together: 15 taps ("Daily Empowerment Chance n/15") and 10 greets.
# A tap only counts ~5 s after the previous one. Greet: label "Greet n/10" + a 1-minute timer, or "Greet x10" when the
# account has the champion pack (one press uses all 10).
CHAMPIONS = ["Cleopatra VII", "Helen", "Elisabeth", "Ariadne", "Dulcinea", "Esmeralda", "Josephine", "Cassandra", "Judith",
             "Marcia", "Demeter", "Minerva", "Nefertiti", "Simonetta", "Goshasp", "Anna Maria Porcile", "Coatlicue"]
CH_TAPS, CH_GREETS = 15, 10
CH_LEFT, CH_RIGHT = (31, 486), (507, 486)          # arrows between champions (stop at both ends)
CH_BODY = (270, 520)                               # tap the champion
CH_GREET = (55, 782)                               # Greet button
CH_NAME_BOX = (90, 100, 360, 80)
CH_COUNT_BOX = (20, 590, 500, 55)                  # "Daily Empowerment Chance (n/15)"
CH_GREET_BOX = (0, 795, 150, 70)                   # "Greet 3/10" + timer
CH_DIALOG_TAP = (273, 866)                         # lower part of the greeting conversation
CH_UNLOCK_BOX = (140, 850, 260, 100)


def ch_conf(cfg):
    out = {}
    for name, v in ((cfg.get("champions") or {}).get("plan") or {}).items():
        if name in CHAMPIONS and isinstance(v, dict):
            taps = max(0, min(int(float(v.get("taps", 0) or 0)), CH_TAPS))
            greets = max(0, min(int(float(v.get("greets", 0) or 0)), CH_GREETS))
            if taps or greets:
                out[name] = {"taps": taps, "greets": greets}
    return {"plan": out}


def ch_row(serial):
    return (load_duty_state().get(serial or "default") or {}).get("champions") or {}


def ch_save(serial, **kw):
    with _DUTY_LOCK:
        data = load_duty_state()
        data.setdefault(serial or "default", {}).setdefault("champions", {}).update(kw)
        save_duty_state(data)


def _ch_norm(s):
    return re.sub(r"[^a-z]", "", s.lower())


def ch_name(img):
    """Name of the champion on the page (read from the banner), or None."""
    import difflib
    x, y, w, h = CH_NAME_BOX
    lines = [t for t, _ in ocr_lines(img[y:y + h, x:x + w], 3.0)]
    norm = {_ch_norm(n): n for n in CHAMPIONS}
    txt = _ch_norm(" ".join(lines))
    for k, n in sorted(norm.items(), key=lambda kv: -len(kv[0])):
        if k in txt:
            return n
    for l in lines:
        m = difflib.get_close_matches(_ch_norm(l), list(norm), n=1, cutoff=0.72)
        if m:
            return norm[m[0]]
    return None


def ch_on_page(img):
    if find_text(img, ["Champion"], region=(150, 20, 240, 80), cutoff=0.6):
        return True
    return ch_name(img) is not None                        # the champion's name banner only exists on this page


def ch_inactive(img):
    return bool(find_text(img, ["Unlock"], region=CH_UNLOCK_BOX, cutoff=0.7))


def ch_taps_used(img):
    x, y, w, h = CH_COUNT_BOX
    txt = " ".join(t for t, _ in ocr_lines(img[y:y + h, x:x + w], 3.0))
    m = re.search(r"(\d{1,2})\s*/\s*15", txt)
    return int(m.group(1)) if m else None


CH_GREET_SEARCH = (0, 640, 300, 300)                # where the Greet button can be (the game shifts it when it adds a button)


def ch_find_greet(img):
    """The Greet button by its word (24 Oct: a new event button pushed Greet up and a 'buy packs' button took its old
    place - a fixed spot would have tapped that). -> (tap (x, y), label box (x, y, w, h)) or (None, None)."""
    t = find_text(img, ["Greet"], region=CH_GREET_SEARCH, cutoff=0.75)
    pic = find(img, "ch_greet", 0.82, CH_GREET_SEARCH) if has_template("ch_greet") else None   # the button's own picture (icon + word), learned at the first find
    if t and pic and (abs(pic[0] - t[0]) > 60 or abs(pic[1] - t[1]) > 60):
        t = None                                           # word and picture disagree: trust neither
    if t:
        x, y = int(t[0]), int(t[1])
        if not pic:
            try:                                           # learn the button picture: 110 x 60 around the word (icon above it)
                save_template("ch_greet", img[max(0, y - 40):y + 20, max(0, x - 55):x + 55])
            except Exception as _e:
                quiet_err("ch_find_greet", _e)
        return (x, y - 12), (max(0, x - 75), max(0, y - 20), 150, 70)
    if pic:
        x, y = int(pic[0]), int(pic[1])
        return (x, y), (max(0, x - 75), max(0, y - 10), 150, 70)
    return None, None


def ch_greet_label(img, box=None):
    """-> {"pack": bool, "n": greets used or None, "wait": seconds left on the timer}"""
    x, y, w, h = box or CH_GREET_BOX
    txt = " ".join(t for t, _ in ocr_lines(img[y:y + h, x:x + w], 3.0)).lower().replace(";", ":")
    out = {"pack": bool(re.search(r"x\s*10", txt)), "n": None, "wait": 0}
    m = re.search(r"(\d{1,2})\s*/\s*10", txt)
    if m:
        out["n"] = int(m.group(1))
    m = re.search(r"(\d{1,2})\s*[:.]\s*(\d{2})\s*[:.]\s*(\d{2})", txt)
    if m:
        out["wait"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    return out


WHEEL_BTN = {100: (95, 924), 10: (269, 924), 1: (443, 924)}          # Spin buttons at the bottom of the Wheel of Fortune page
WHEEL_COST = {100: 9000, 10: 900, 1: 100}
WHEEL_COINS_BOX = (234, 128, 190, 48)
WHEEL_ICON = (141, 328)                                              # "Wheel of Fortune" in the Tavern's ring menu


def wheel_coins(img):
    x, y, w, h = WHEEL_COINS_BOX
    txt = " ".join(t for t, _ in ocr_lines(img[y:y + h, x:x + w], 3.0))
    d = re.sub(r"[^0-9]", "", txt)
    return int(d) if d else None


def wheel_on_page(img):
    return bool(find_text(img, ["Wheel of Fortune"], region=(100, 5, 340, 60), cutoff=0.7))


def wheel_reward_panel(img):
    return bool(find_text(img, ["Congratulations", "Continue"], region=(30, 150, 480, 650), cutoff=0.7))


def close_reward_panel(dev, stop, log=None):
    """A "Congratulations! / Continue" reward panel is lying over the city (left from an earlier task): close it,
    otherwise the next tap lands on the panel. -> True when one was closed."""
    closed = False
    for _ in range(4):
        if stop.is_set() or not wheel_reward_panel(dev.screenshot()):
            break
        closed = True
        dev.tap(270, 754)
        _nap(stop, 0.8, 1.1)
        if wheel_reward_panel(dev.screenshot()):
            dev.tap(270, 110)
            _nap(stop, 0.6, 0.9)
    if closed and log:
        log("[i]   closed a reward panel that was in the way")
    return closed


WHEEL_ADD = (405, 147)                                               # "+" next to the coin count -> Purchase Chips page
CHIP_ROWS = ("1K Chips", "500 Chips", "100 Chips")                   # rows with owned items; 5K Chips is sold for gems - never
CHIP_MINUS, CHIP_PLUS, CHIP_LEFT_BTN, CHIP_USE = (82, 526), (452, 526), (156, 661), (380, 661)
CHIP_RECEIVE_BOX = (50, 465, 390, 45)                               # "You can receive: n" (white, easier to read than "n / own")
CHIP_CLOSE = (500, 296)
CHIP_BATCH = 400                                                     # most +/- taps for one Use
CHIP_UNIT = {"1K Chips": 1000, "500 Chips": 500, "100 Chips": 100}


def wheel_spin_cost(spins, big):
    cost = 0
    for b in (100, 10, 1):
        if b <= big:
            n = spins // b
            cost += n * WHEEL_COST[b]
            spins -= n * b
    return cost


def chip_count(img, unit):
    """Items set in the chip window, from "You can receive: n" / chips per item. -> int or None."""
    x, y, w, h = CHIP_RECEIVE_BOX
    txt = " ".join(t for t, _ in ocr_lines(img[y:y + h, x:x + w], 3.0))
    m = re.search(r"([\d][\d,.]*)\s*$", txt.strip())
    if not m:
        return None
    n = int(re.sub(r"[^0-9]", "", m.group(1)) or 0)
    return n // unit if n and n % unit == 0 else None


def chip_use_green(img, y):
    """The row's button is the green "Use (owned)" one (the gem-price button is orange) -> True."""
    b, g, r = (float(v) for v in img[y + 8:y + 32, 410:500].reshape(-1, 3).mean(0))
    return g > r + 20 and g > b + 15


def chip_use_row(dev, name, want, log, stop):
    """Purchase Chips page is open: use `want` owned items of one row (None = all). Only a row whose button says "Use"
    is pressed (a gem price button is never touched). -> number of items used."""
    unit = CHIP_UNIT[name]
    shot = dev.screenshot()
    p = find_text(shot, [name], region=(90, 150, 260, 760), cutoff=0.95)
    if not p or not chip_use_green(shot, p[1]):
        return 0
    dev.tap(455, p[1] + 18)
    _nap(stop, 1.2, 1.6)
    shot = dev.screenshot()
    own = chip_count(shot, unit)                            # the window opens at the most = all owned
    if not own or not find_text(shot, [name], region=(150, 310, 300, 80), cutoff=0.95):
        log(f"[!]  Wheel: the {name} window did not look right - closed it")
        dev.tap(*CHIP_CLOSE)
        _nap(stop, 0.8, 1.0)
        return 0
    goal = own if want is None else min(want, own)
    cur = own
    if goal < own:                                          # go down from the minimum
        dev.tap(*CHIP_LEFT_BTN)                            # "Minimum"
        _nap(stop, 0.6, 0.8)
        for _ in range(4):
            cur = chip_count(dev.screenshot(), unit)
            if not cur or cur == goal or stop.is_set():
                break
            d = goal - cur
            for _ in range(min(abs(d), CHIP_BATCH)):
                dev.tap(*(CHIP_PLUS if d > 0 else CHIP_MINUS))
                _nap(stop, 0.08, 0.14)
            _nap(stop, 0.4, 0.6)
    if cur != goal:
        log(f"[!]  Wheel: could not set {goal} x {name} (shows {cur if cur else '?'}) - nothing used")
        dev.tap(*CHIP_CLOSE)
        _nap(stop, 0.8, 1.0)
        return 0
    dev.tap(*CHIP_USE)
    _nap(stop, 1.5, 2.0)
    log(f"[i]   Wheel: used {goal:,} x {name} (+{goal * unit:,} coins)")
    return goal


def wheel_add_chips(dev, conf, coins, log, stop):
    """Wheel page is open: top up the coins from chip items the account already owns (setting "needed" or "all").
    "needed" adds 1K items for the spins still to do (+ the coins to keep); with "until out" it uses them all."""
    mode = conf.get("chips", "off")
    big = int(conf["button"])
    target = int(conf["spins"] or 0) * big                 # presses x button = spins
    if mode == "off":
        return
    need = None
    if mode == "needed" and target:
        short = wheel_spin_cost(target, big) + int(conf["reserve"] or 0) - coins
        if short <= 0:
            return
        need = -(-short // 1000)
    dev.tap(*WHEEL_ADD)
    _nap(stop, 1.8, 2.3)
    if not find_text(dev.screenshot(), ["Purchase Chips"], region=(100, 5, 340, 60), cutoff=0.7):
        log("[!]  Wheel: the Purchase Chips page did not open - spinning with the coins there are")
    else:
        rows = CHIP_ROWS[:1] if need is not None else CHIP_ROWS
        for name in rows:
            if stop.is_set():
                break
            left = need
            while not stop.is_set():
                used = chip_use_row(dev, name, None if left is None else min(left, CHIP_BATCH), log, stop)
                if not used or left is None:
                    break
                left -= used
                if left <= 0:
                    break
    for _ in range(3):                                     # back to the wheel
        if wheel_on_page(dev.screenshot()):
            break
        dev.tap(46, 34)
        _nap(stop, 1.2, 1.6)


def wheel_spun_today(serial, add=0):
    """Spins already done today on this instance (a retried Wheel duty does not spin the same spins twice). -> count."""
    with _DUTY_LOCK:
        data = load_duty_state()
        row = data.setdefault(serial or "default", {}).setdefault("wheel_spins", {})
        if row.get("day") != _game_day():
            row.clear()
            row["day"], row["n"] = _game_day(), 0
        if add:
            row["n"] = int(row.get("n", 0)) + add
            save_duty_state(data)
        return int(row.get("n", 0))


def run_wheel(dev, cfg, log, stop):
    """Tavern's ring menu is open: open the Wheel of Fortune and spin with the button the user picked until the number
    of spins is done, the coins run out (or fall to the reserve to keep) or something unexpected is on the screen.
    The reward panel is closed by tapping its Continue / empty ground. Only the three Spin buttons are ever pressed."""
    conf = wheel_conf(cfg)
    big = int(conf["button"]) if int(conf["button"]) in WHEEL_BTN else 100
    target = int(conf["spins"] or 0) * big                 # "spins" = presses of the chosen button (owner 25 Oct); 0 = until the coins are used up
    serial = getattr(dev, "serial", "")
    before = wheel_spun_today(serial) if target else 0
    if target and before >= target:
        log(f"[+]  Wheel: {before} spin(s) already done today - nothing to spin")
        return before
    target -= before                                       # Rob 25 Oct 03:28: the retry after a stuck panel would spin 100 more
    reserve = int(conf["reserve"] or 0)
    shot = dev.screenshot()
    if not wheel_on_page(shot):
        p = find_text(shot, ["Wheel of Fortune", "Wheel of", "Fortune"], region=(0, 250, 300, 300), cutoff=0.7)
        dev.tap(p[0], p[1] - 40) if p else dev.tap(*WHEEL_ICON)
        _nap(stop, 2.2, 2.8)
        shot = dev.screenshot()
        if not wheel_on_page(shot):
            raise RuntimeError("the Wheel of Fortune page did not open")
    if conf.get("chips", "off") != "off":
        c0 = wheel_coins(shot)
        if c0 is not None:
            wheel_add_chips(dev, conf, c0, log, stop)
            if not wheel_on_page(dev.screenshot()):
                raise RuntimeError("could not get back to the Wheel of Fortune page after adding chips")
    done = 0
    fails = 0
    closes = 0
    last_pick = big
    while not stop.is_set():
        shot = dev.screenshot()
        if wheel_reward_panel(shot):                       # a reward panel is still up: its Continue button, else empty ground
            closes += 1
            if closes > 8:                                 # Rob 25 Oct 03:10: the x100 panel never closed -> 17 min without a log line
                rd_snap(shot, "wheel_panel_stuck")
                raise RuntimeError(f"the Wheel reward panel does not close (after {done} spin(s))")
            dev.tap(*(WHEEL_BTN[last_pick] if closes % 2 else (270, 110)))   # owner 25 Oct: the spin button's own spot or
                                                           # outside closes it (tapped only while the panel is seen = no extra spin)
            _nap(stop, 0.8, 1.1)
            continue
        closes = 0
        if not wheel_on_page(shot):
            raise RuntimeError(f"left the Wheel of Fortune page (after {done} spin(s))")   # failed = tried again later, not 'done'
        coins = wheel_coins(shot)
        if coins is None:
            raise RuntimeError(f"could not read the Wheel coins (after {done} spin(s))")
        left = (target - done) if target else None
        if left is not None and left <= 0:
            log(f"[+]  Wheel: {done} spin(s) done")
            break
        pick = next((b for b in (100, 10, 1) if b <= big and (left is None or b <= left) and coins - WHEEL_COST[b] >= reserve), None)
        if pick is None:
            log(f"[+]  Wheel: stopped after {done} spin(s) - {coins:,} coins left (cost/keep-at-least {reserve:,})")
            break
        dev.tap(*WHEEL_BTN[pick])
        last_pick = pick
        shown = spent = False
        for _ in range(14):
            _nap(stop, 0.5, 0.7)
            s2 = dev.screenshot()
            if wheel_reward_panel(s2):
                shown = True
                break
            c2 = wheel_coins(s2) if wheel_on_page(s2) else None
            if c2 is not None and coins - c2 >= WHEEL_COST[pick]:      # Spin once shows no panel: the coins dropping = it spun
                spent = True
                _nap(stop, 1.5, 2.0)                       # let the wheel stop turning
                break
        if not (shown or spent):
            fails += 1
            log(f"[i]   Wheel: the {pick}-spin button did not spin ({fails}/3)")
            if fails >= 3:
                raise RuntimeError(f"the {pick}-spin button did not spin 3 times (after {done} spin(s))")
            continue
        fails = 0
        done += pick
        wheel_spun_today(serial, pick)
        log(f"[i]   Wheel: {done} spin(s) done ({coins - WHEEL_COST[pick]:,} coins left)")
        for _ in range(4 if shown else 0):                                 # close the panel: the button's own spot (owner 25 Oct)
            if not wheel_reward_panel(dev.screenshot()):
                break
            dev.tap(*WHEEL_BTN[pick])
            _nap(stop, 0.8, 1.1)
            if not wheel_reward_panel(dev.screenshot()):
                break
            dev.tap(270, 110)
            _nap(stop, 0.6, 0.9)
    return done


def ch_snap(img, tag):
    try:
        d = USER / "debug"
        d.mkdir(exist_ok=True)
        cv2.imwrite(str(d / f"champions_{tag}.png"), img)
    except Exception as _e:
        quiet_err("ch_snap", _e)


def ch_open_page(dev, stop, log):
    """The Keep's ring menu is open: tap Champions. True when the Champion page shows."""
    for _ in range(2):
        shot = dev.screenshot()
        ch_snap(shot, "0_ring")
        if ch_on_page(shot) and ch_name(shot):
            return True
        p = find_text(shot, ["Champions", "Champion"], region=(0, 150, 540, 450), cutoff=0.7)
        if not p:
            d_ = find_detail_icon(shot)                      # the caption is hard to read on a busy picture: the Champion icon sits up-right of the Detail (i) icon
            if d_:
                log("[i]   Champions: caption not readable - using the place above the Detail icon")
                p = (d_[0] + 25, d_[1] - 88 + 42)
        if not p:
            log("[i]   Champions: the Champions icon is not in the Keep's menu")
            return False
        dev.tap(p[0], p[1] - 42)                             # the icon sits just above its caption
        _nap(stop, 2.0, 2.8)
        shot = dev.screenshot()
        if ch_on_page(shot) and ch_name(shot):
            return True
    return False


def ch_goto(dev, stop, log, target):
    """Walk to a champion with the arrows (they stop at both ends). -> True when the page shows `target`."""
    want = CHAMPIONS.index(target)
    for _ in range(22):
        if stop.is_set():
            return False
        cur = ch_name(dev.screenshot())
        if cur == target:
            return True
        if cur is None:
            _nap(stop, 1.0, 1.4)
            continue
        dev.tap(*(CH_RIGHT if CHAMPIONS.index(cur) < want else CH_LEFT))
        _nap(stop, 1.1, 1.5)
    return False


def ch_do_taps(dev, stop, log, name, want, serial, day):
    """Tap the champion `want` times (stops at 15 a day in total). -> taps done."""
    done = 0
    stuck = 0
    before = None                                          # the counter is only visible for a few seconds after a tap
    for _ in range(want + 6):
        if done >= want or stop.is_set():
            break
        dev.tap(*CH_BODY)
        _nap(stop, 1.0, 1.4)
        shot = dev.screenshot()
        if find_text(shot, ["interact with"], region=(0, 300, 540, 300), cutoff=0.7):          # "Are you sure you want to interact...?"
            box = find_text(shot, ["Don't show this again", "Don't show"], region=(100, 420, 440, 120), cutoff=0.65)
            dev.tap(216, box[1] if box and 460 <= box[1] <= 510 else 486)                        # the tick box (the box is always at x 216; its text starts right of it)
            _nap(stop, 0.5, 0.8)
            p = find_text(dev.screenshot(), ["Confirm"], region=(150, 500, 300, 120), cutoff=0.7) or (380, 562)
            dev.tap(*p)
            _nap(stop, 1.0, 1.4)
            shot = dev.screenshot()
        after = ch_taps_used(shot)                         # "Daily Empowerment Chance (n/15)" shows after a counted tap
        if after is None or (before is not None and after <= before):
            stuck += 1
            if stuck >= 3:
                log("[i]   Champions: taps are not counting (or all 15 are used) - stopping the taps")
                break
        else:
            stuck = 0
            done += 1
            used = {**(ch_row(serial).get("taps") or {})} if ch_row(serial).get("taps_day") == day else {}
            used[name] = used.get(name, 0) + 1
            ch_save(serial, taps_day=day, taps=used)
            before = after
            if after >= CH_TAPS:
                log("[i]   Champions: the 15 daily taps are used up")
                break
        _nap(stop, 4.4, 4.9)                                 # a tap only counts after the previous message has faded
    return done


def ch_dialogue(dev, stop):
    """Tap through the greeting conversation until the Greet button is back."""
    for _ in range(9):
        if stop.is_set():
            return
        dev.tap(*CH_DIALOG_TAP)
        _nap(stop, 1.6, 2.2)
        if find_text(dev.screenshot(), ["Greet"], region=CH_GREET_SEARCH, cutoff=0.7):
            return


def ch_do_greets(dev, stop, log, name, want, serial, day):
    """Greet the champion `want` times (stops at 10 a day in total). -> greets done."""
    done = 0
    for _ in range(want + 4):
        if done >= want or stop.is_set():
            break
        shot = dev.screenshot()
        spot, box = ch_find_greet(shot)
        if spot is None:                                   # no Greet word on the page: never tap blind (a pack / buy button may sit there)
            log("[!]   Champions: no Greet button found on the page - not tapping anything")
            rd_snap(shot, "champ_nogreet")
            break
        lab = ch_greet_label(shot, box)
        if lab["n"] is not None and lab["n"] >= CH_GREETS:
            log("[i]   Champions: the 10 daily greetings are used up")
            break
        if lab["wait"] > 0:
            log(f"[i]   Champions: greet timer, waiting {lab['wait']} s")
            if stop.wait(min(lab["wait"], 75) + 3):
                break
            spot, box = ch_find_greet(dev.screenshot())
            if spot is None:
                break
        dev.tap(*spot)
        _nap(stop, 1.6, 2.2)
        ch_dialogue(dev, stop)
        done += 1
        used = {**(ch_row(serial).get("greets") or {})} if ch_row(serial).get("greets_day") == day else {}
        used[name] = used.get(name, 0) + (CH_GREETS if lab["pack"] else 1)
        ch_save(serial, greets_day=day, greets=used)
        if lab["pack"]:
            log("[i]   Champions: 'Greet x10' (champion pack) - one press used all 10 greetings")
            return CH_GREETS
        _nap(stop, 1.0, 1.4)
    return done


def run_champions(dev, cfg, log, stop):
    """Keep's ring menu is open: tap Champions, then spend the day's taps and greetings on the champions you picked
    (or, with nothing picked, on the champion the page opens on / the first active one)."""
    serial = getattr(dev, "serial", "default")
    day = _game_day()
    if not ch_open_page(dev, stop, log):
        ch_snap(dev.screenshot(), "1_failed")
        raise RuntimeError("the Champion page did not open")
    shot = dev.screenshot()
    t_used, g_used = ch_taps_used(shot), ch_greet_label(shot).get("n")
    if t_used is not None and t_used >= CH_TAPS and g_used is not None and g_used >= CH_GREETS:
        log(f"[+]  Champions: today's taps ({t_used}/{CH_TAPS}) and greets ({g_used}/{CH_GREETS}) are all used - nothing to do")
        return
    plan = ch_conf(cfg)["plan"]
    row = ch_row(serial)
    taps_used = row.get("taps") or {} if row.get("taps_day") == day else {}
    greets_used = row.get("greets") or {} if row.get("greets_day") == day else {}
    if not plan:                                           # default: everything on the champion shown, or the first active one
        cur = None
        for _ in range(4):                                 # the name read is flaky: try a few pictures
            cur = ch_name(dev.screenshot())
            if cur or stop.is_set():
                break
            _nap(stop, 0.6, 0.9)
        if not cur:
            ch_snap(dev.screenshot(), "1_noname")
            raise RuntimeError("the champion's name could not be read (nothing was done)")
        plan = {cur: {"taps": CH_TAPS, "greets": CH_GREETS}}
    order = sorted(plan, key=lambda n: CHAMPIONS.index(n))
    if len(order) > 1 and ch_name(dev.screenshot()) in order and CHAMPIONS.index(ch_name(dev.screenshot())) > (CHAMPIONS.index(order[0]) + CHAMPIONS.index(order[-1])) / 2:
        order.reverse()                                    # start from the end that is nearer
    total_taps = total_greets = 0
    for name in order:
        if stop.is_set():
            break
        need_t = max(0, plan[name]["taps"] - int(taps_used.get(name, 0)))
        need_g = max(0, plan[name]["greets"] - int(greets_used.get(name, 0)))
        if not (need_t or need_g):
            continue
        if not ch_goto(dev, stop, log, name):
            log(f"[!]  Champions: could not reach {name}")
            continue
        shot = dev.screenshot()
        ch_snap(shot, "2_" + _ch_norm(name))
        if ch_inactive(shot):
            log(f"[i]   Champions: {name} is not active on this account - skipped")
            continue
        log("@act " + json.dumps({"k": "step", "text": f"Champions: {name} - {need_t} tap(s), {need_g} greet(s)"}))
        if need_t:
            n = ch_do_taps(dev, stop, log, name, need_t, serial, day)
            total_taps += n
            log(f"[+]  Champions: {name} - {n} of {need_t} taps")
        if need_g and not stop.is_set():
            n = ch_do_greets(dev, stop, log, name, need_g, serial, day)
            total_greets += n
            log(f"[+]  Champions: {name} - {n} of {need_g} greets")
    shot = dev.screenshot()
    used = ch_taps_used(shot)
    log(f"[+]  Champions done: {total_taps} tap(s), {total_greets} greet(s) this run" + (f"; taps today {used}/{CH_TAPS}" if used is not None else ""))


# ------------------------------------------------------------ test: does world map -> city reset the camera?
def _wait_view(dev, cfg, stop, want, secs=60):
    """Wait until the game shows `want` ('city' / 'landscape') and the picture is steady. -> last screenshot or None."""
    t0, prev = time.time(), None
    while time.time() - t0 < secs and not stop.is_set():
        _nap(stop, 1.5, 1.8)
        shot = dev.screenshot()
        v = view_of(shot, cfg)
        if want == "landscape" and v == "world":           # the world map never stands still (clouds, marches): seen once is enough
            return shot
        if v == want:
            if prev is not None and float(np.mean(cv2.absdiff(prev[100:860:6, ::6], shot[100:860:6, ::6]))) < 2.0:
                return shot
            prev = shot
        else:
            prev = None
    return None


def run_view_reset_test(dev, cfg, log, stop):
    """From the city: 3 rounds of (move the camera) -> globe -> world map -> globe -> city, saving the city picture each
    time (debug/view_reset/). If the 3 city pictures match, the city always opens at the same camera place."""
    out = USER / "debug" / "view_reset"
    out.mkdir(parents=True, exist_ok=True)
    moves = [(0, 0), (240, -300), (-260, 260)]
    pics = []
    for i, (dx, dy) in enumerate(moves):
        if stop.is_set():
            return
        if dx or dy:
            pan_content(dev, stop, dx, dy)
            _nap(stop, 1.5, 2.0)
            moved = dev.screenshot()
            cv2.imwrite(str(out / f"{i}_moved.png"), moved)
        dev.tap(*VIEW_TOGGLE)
        if _wait_view(dev, cfg, stop, "landscape") is None:
            cv2.imwrite(str(out / f"{i}_no_world.png"), dev.screenshot())
            log(f"[i]   reset test: bottom-left / coordinate text read: '{_WM_NOTE[1]}'")
            raise RuntimeError(f"round {i + 1}: the world map did not come up")
        dev.tap(*VIEW_TOGGLE)
        shot = _wait_view(dev, cfg, stop, "city", 90)
        if shot is None:
            raise RuntimeError(f"round {i + 1}: the city did not come back")
        cv2.imwrite(str(out / f"{i}_city.png"), shot)
        pics.append(shot)
        if dx or dy:
            d_moved = float(np.mean(cv2.absdiff(moved[100:860:4, ::4], shot[100:860:4, ::4])))
            log(f"[i]   reset test round {i + 1}: camera moved by {dx},{dy} before; difference moved -> after reset {d_moved:.1f}")
    for i in (1, 2):
        d = float(np.mean(cv2.absdiff(pics[0][100:860:4, ::4], pics[i][100:860:4, ::4])))
        sx, sy, conf = city_shift(pics[0], pics[i])
        log(f"[+]  reset test: city after round 1 vs round {i + 1}: difference {d:.1f} (0-5 = same place), shift {sx:.0f},{sy:.0f} (confidence {conf:.2f})")
    log("[i]   reset test pictures saved in debug/view_reset")


# ------------------------------------------------------------ alliance science
def as_remaining(img):
    """'Remaining Donations: 13/13' on the Alliance Science page -> (left, max) or None."""
    try:
        for t, _ws in ocr_lines(img[150:300, 0:360], 2.5):
            m = re.search(r"(\d{1,2})\s*[/\\l|]\s*(\d{1,2})", t)
            if m and ("onat" in t.lower() or "emain" in t.lower() or len(_ws) <= 3):
                a, b = int(m.group(1)), int(m.group(2))
                if 0 <= a <= b <= 50:
                    return a, b
        c = img[185:235, 0:240]
        g = cv2.resize(cv2.cvtColor(c, cv2.COLOR_BGR2GRAY), None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        for b in (g, 255 - cv2.threshold(g, 130, 255, cv2.THRESH_BINARY)[1]):
            t = pytesseract.image_to_string(b, config="--psm 6")
            m = re.search(r"(\d{1,2})\s*[/\\l|]\s*(\d{1,2})", t)
            if m and int(m.group(1)) <= int(m.group(2)) <= 50:
                return int(m.group(1)), int(m.group(2))
    except Exception as _e:
        quiet_err("as_remaining", _e)
    return None


def as_donate_button(img):
    """The green Donate button (top-most on the page, right side) -> (x, y) or None. Text first, colour as fallback."""
    p = find_text(img, ["Donate"], region=(300, 300, 240, 560), cutoff=0.75)
    if p:
        return p
    hsv = cv2.cvtColor(img[300:860, 300:540], cv2.COLOR_BGR2HSV)
    m = cv2.morphologyEx(cv2.inRange(hsv, (40, 90, 50), (85, 255, 230)), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = [cv2.boundingRect(c) for c in cs]
    boxes = [b for b in boxes if 80 < b[2] < 170 and 30 < b[3] < 65]
    if not boxes:
        return None
    x, y, w, h = min(boxes, key=lambda b: b[1])
    return x + w // 2 + 300, y + h // 2 + 300


def as_snap(img, tag):
    try:
        (USER / "debug").mkdir(exist_ok=True)
        cv2.imwrite(str(USER / "debug" / f"alliance_science_{tag}.png"), img)
    except Exception as _e:
        quiet_err("as_snap", _e)


def run_alliance_science(dev, cfg, log, stop):
    """From the city: Alliance button -> Alliance Science row -> tap Donate on the recommended science until the day's
    donations are used up. Only the free daily donations; nothing that costs gems is pressed."""
    rd_alliance_page(dev, cfg, stop)
    row = None
    words = ["Alliance Science", "Science", "Alliance Tech", "Technology", "Research"]
    moves = [(316, 778, 303, 548)] * 5 + [(303, 400, 316, 760)] * 7      # up past the fold first, then back down
    for i in range(len(moves) + 1):
        shot = dev.screenshot()
        if i == 0:
            as_snap(shot, "1_alliance")
        row = find_text(shot, words, region=(40, 200, 460, 740), cutoff=0.72)
        if not row:
            r = rd_find(shot, words, region=(40, 200, 460, 740), cutoff=0.72)
            row = (r[0], r[1]) if r else None
        if row or stop.is_set() or i == len(moves):
            break
        dev.swipe(*moves[i], 450)
        _nap(stop, 1.0, 1.4)
    if not row:
        as_snap(dev.screenshot(), "2_failed")
        raise RuntimeError("the Alliance Science row was not found on the Alliance page")
    dev.tap(*row)
    _nap(stop, 1.8, 2.4)
    shot = dev.screenshot()
    as_snap(shot, "3_science")
    rem = as_remaining(shot)
    btn = as_donate_button(shot)
    if rem is None and not (btn and find_text(shot, ["Alliance Science", "Science"], region=(0, 0, 540, 340), cutoff=0.7)):
        raise RuntimeError("the Alliance Science page did not open")
    if rem is None:                                        # counter not readable: tap one at a time while the green button stays
        log("[i]   Alliance Science: the donation counter is not readable - donating one at a time")
        n = 0
        while n < int(cfg.get("alliance_science_max", 13) or 13) and not stop.is_set():
            b = as_donate_button(dev.screenshot())
            if not b:
                break
            dev.tap(*b)
            n += 1
            _nap(stop, 0.7, 1.0)
        as_snap(dev.screenshot(), "5_done")
        log(f"[+]  Alliance Science: {n} donation tap(s)")
        return
    left, top = rem
    log(f"[i]   Alliance Science: donations left {left}/{top}")
    if left == 0:
        log("[+]  Alliance Science: all donations already done today")
        rd_nothing()
        return
    if not btn:
        raise RuntimeError("no Donate button on the recommended science")
    done = 0
    while left > 0 and done < 60 and not stop.is_set():
        for _ in range(min(left, 5)):                      # quick taps work; re-read the counter every 5
            dev.tap(*btn)
            _nap(stop, 0.3, 0.45)
            done += 1
        _nap(stop, 0.8, 1.1)
        shot = dev.screenshot()
        r = as_remaining(shot)
        if r is None:                                      # something covered the page (a dialog?) - do not keep tapping
            as_snap(shot, "4_unknown")
            log("[!]  Alliance Science: the page changed while donating - stopping")
            break
        if r[0] >= left:                                   # nothing was used: the button moved or the science is maxed
            nb = as_donate_button(shot)
            if not nb or nb == btn:
                log("[!]  Alliance Science: the donations did not go down - stopping")
                break
            btn = nb
        left = r[0]
    as_snap(dev.screenshot(), "5_done")
    log(f"[+]  Alliance Science done: {top - left} of {top} donations used today" + ("" if left == 0 else f", {left} left"))
    if left > 0:
        raise RuntimeError(f"{left} donation(s) left")


# ------------------------------------------------------------ recorded duties (tester flows, 22 Oct 2026)
# Built from the tester's recordings (project docs claude/flow_*.md, index claude/flows_index_22oct.md).
# Safety rules shared by all of them:
# - a button that DOES something (Claim, Free, Levy, Tax, Train, Send, Offer, March, Finish All ...) is pressed only when
#   its word is read on the screen in the expected place; the recorded spot is never pressed blind for those;
# - recorded spots are used only to OPEN things (top-bar icons, tabs, card slots) and the page is checked afterwards;
# - a line that mentions gems / Instant / Buy / Purchase / Clear / Refresh / Enter / packages is never pressed.
RD_DANGER = ("gem", "instant", "buy", "purchase", "clear", "refresh", "enter", "package", "recharge", "$", "x10", "10 pack")
RD_SPEEDUP_WORDS = ["Finish All", "Finish all"]


def rd_nothing():
    """A duty step found nothing left to do in the game (all claimed / daily limit reached). Two such runs in a row
    = the duty is done until the daily reset (duty_record)."""
    _TL.rd_nothing = True


def rd_snap(img, tag):
    try:
        (USER / "debug").mkdir(exist_ok=True)
        cv2.imwrite(str(USER / "debug" / f"duty_{tag}.png"), img)
    except Exception as _e:
        quiet_err("rd_snap", _e)


def rd_opt(cfg, duty_id, key, default=None):
    """A duty's own setting (Duties page), e.g. rd_opt(cfg, 'resource_tax', 'resource', 'Lumber')."""
    v = ((cfg.get("duties") or {}).get(duty_id) or {}).get(key)
    if v in (None, ""):
        for o in (DUTY_BY_ID.get(duty_id) or {}).get("opts") or []:
            if o["k"] == key:
                return o.get("def", default)
        return default
    return v


def rd_lines(img, region=None):
    ox = oy = 0
    if region:
        x, y, w, h = region
        img, ox, oy = img[max(0, y):y + h, max(0, x):x + w], max(0, x), max(0, y)
    return [(t, [(s[0], s[1] + ox, s[2] + oy, s[3], s[4]) for s in ws]) for t, ws in ocr_lines(img)]


def rd_find(img, words, region=None, cutoff=0.78, whole=False):
    """-> (x, y, line) of the first of `words` read on the screen, or None. Lines with a danger word (gems, Instant,
    Buy, Refresh...) are skipped unless the word itself is asked for. whole=True: the line must be the word and
    nothing else (e.g. a 'Free' button, not 'Free to open in 1d')."""
    import difflib
    lines = rd_lines(img, region)
    for opt in words:
        ow = opt.lower().split()
        if not ow:
            continue
        for text, ws in lines:
            low = text.lower()
            if any(d in low for d in RD_DANGER if d not in opt.lower()):
                continue
            if whole:
                if difflib.SequenceMatcher(None, low.strip(".,:;!() "), " ".join(ow)).ratio() >= cutoff:
                    return (int(sum(s[1] for s in ws) / len(ws)), int(sum(s[2] for s in ws) / len(ws)), text)
                continue
            for i in range(len(ws) - len(ow) + 1):
                seg = ws[i:i + len(ow)]
                got = " ".join(s[0] for s in seg).lower().strip(".,:;!")
                if difflib.SequenceMatcher(None, got, " ".join(ow)).ratio() >= cutoff:
                    return (int(sum(s[1] for s in seg) / len(seg)), int(sum(s[2] for s in seg) / len(seg)), text)
    return None


def rd_seen(img, words, region=None, cutoff=0.72):
    return bool(find_text(img, words, region=region, cutoff=cutoff))


def rd_tap(dev, stop, words, region=None, cutoff=0.78, whole=False, wait=(1.4, 1.9), shot=None):
    """Tap a button found by its word (see rd_find). -> True when tapped."""
    hit = rd_find(shot if shot is not None else dev.screenshot(), words, region, cutoff, whole)
    if not hit:
        return False
    dev.tap(hit[0], hit[1])
    _nap(stop, *wait)
    return True


def ring_label_big(shot, words):
    """Ring menu labels are small: read the menu area enlarged (light text and plain) and find one of `words`.
    -> (x, y) of the label in screen pixels, or None."""
    import difflib
    want = [w.lower() for w in words]
    for bright in (True, False):
        for text, ws in ocr_lines(shot[110:760, 0:540], 2.5, bright):
            for wd in ws:
                t = re.sub(r"[^a-z]", "", wd[0].lower())
                if len(t) >= 3 and any(t == w or (len(w) >= 4 and difflib.SequenceMatcher(None, t, w).ratio() >= 0.8) for w in want):
                    return int(wd[1]), int(wd[2]) + 110
    return None


def rd_ring(dev, stop, words, cutoff=0.75):
    """A building's ring menu is open: tap the item with this label (the icon sits just above its label)."""
    for i in range(2):
        shot = dev.screenshot()
        p = rd_find(shot, words, region=(0, 110, 540, 650), cutoff=cutoff) or ring_label_big(shot, words)
        if p:
            dev.tap(p[0], max(100, p[1] - 35))
            _nap(stop, 2.0, 2.6)
            return True
        _nap(stop, 0.8, 1.1)
    rd_snap(dev.screenshot(), "ring_" + words[0].replace(" ", "_"))
    return False


def rd_close_reward(dev, stop, spot=(270, 754)):
    """Close 'Congratulations!' panels (one or more) by tapping their Continue / empty curtain."""
    n = 0
    for _ in range(5):
        if stop.is_set():
            break
        if not wheel_reward_panel(dev.screenshot()):
            break
        dev.tap(*spot)
        n += 1
        _nap(stop, 0.9, 1.2)
    return n


def rd_city_label(dev, stop, names, dy=45):
    """Find a building by the name printed under it on the city screen (for buildings the city map does not know,
    e.g. Trade Carriage, World Civilizations) and tap the building just above the name. -> True when tapped."""
    x, y, w, h = LABEL_VIEW
    shot = dev.screenshot()
    for bright in (False, True):
        for text, ws in ocr_lines(shot[y:y + h, x:x + w], bright=bright):
            low = text.lower()
            for n in names:
                if n.lower() in low:
                    cx = int(sum(s[1] for s in ws) / len(ws)) + x
                    cy = int(sum(s[2] for s in ws) / len(ws)) + y
                    dev.tap(cx, max(110, cy - dy))
                    _nap(stop, 2.0, 2.6)
                    return True
    rd_snap(shot, "label_" + names[0].replace(" ", "_"))
    return False


def rd_numbers(text):
    return [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", text or "")]


def rd_keyboard_ok(dev, stop):
    """Close the Android text box after typing (Enter = OK)."""
    try:
        dev._run("shell", "input", "keyevent", "66")
    except Exception:
        dev.tap(460, 912)
    _nap(stop, 1.0, 1.4)


def rd_alliance_page(dev, cfg, stop):
    """From the city: open the Alliance page (same way as Alliance Science)."""
    shot = dev.screenshot()
    ab = find_text(shot, ["Alliance"], region=(400, 200, 140, 740), cutoff=0.7)   # the word under the button: its place moves with the icons above
    h = find(shot, "rally_icon", 0.8) if not ab and has_template("rally_icon") else None
    pos = (int(ab[0]), max(int(ab[1]) - 38, 0)) if ab else (h[0], h[1]) if h else tuple({**DEFAULT_CITY_ICONS, **(cfg.get("city_icons") or {})}.get("Alliance"))
    for attempt in range(2):
        if attempt and rd_seen(shot, ["Mail"], region=(0, 0, 540, 140)):    # the fixed spot hit Mail (owner 23 Oct): back out first
            dev.back()
            _nap(stop, 1.0, 1.3)
        dev.tap(*pos)
        _nap(stop, 1.8, 2.4)
        shot = dev.screenshot()
        if rd_seen(shot, ["Alliance"], region=(0, 0, 540, 140)) or rd_seen(shot, ["Alliance Gift", "Alliance Help", "Alliance Science", "Alliance War", "Alliance Treasure", "Alliance Shop", "Members"], region=(40, 200, 460, 760)):
            return shot
    rd_snap(shot, "alliance_page_failed")
    raise RuntimeError("the Alliance page did not open (screen not found)")


def rd_alliance_row(dev, stop, words):
    """On the Alliance page: tap the row with this name, scrolling the list up when it is below the fold."""
    for i in range(4):
        shot = dev.screenshot()
        row = rd_find(shot, words, region=(40, 300, 400, 640), cutoff=0.75)
        if row:
            dev.tap(row[0], row[1])
            _nap(stop, 1.8, 2.4)
            return True
        if stop.is_set():
            break
        dev.swipe(316, 778, 303, 548, 450)
        _nap(stop, 1.0, 1.4)
    rd_snap(dev.screenshot(), "alliance_row_" + words[0].replace(" ", "_"))
    return False


# --- simple city claims ---------------------------------------------------------------------------------------
LEVY_ALL_BTN = (390, 742)                  # orange "Free Levy All" button in the Levy window


def levy_free_times(shot):
    """'Free Levy Times: 15' in the Levy window -> 15, or None when not read."""
    t = " ".join(x for x, _ in rd_lines(shot, (30, 520, 480, 160))).lower()
    m = re.search(r"free\s*levy\s*times[^0-9]{0,4}(\d{1,3})", t)
    if m:
        return int(m.group(1))
    try:                                                 # light digits on beige: read the number box alone, thresholded
        g = cv2.resize(cv2.cvtColor(shot[573:607, 205:290], cv2.COLOR_BGR2GRAY), None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
        for th in (180, 170, 190, 160):
            b = cv2.threshold(g, th, 255, cv2.THRESH_BINARY_INV)[1]
            d = pytesseract.image_to_string(b, config="--psm 7 -c tessedit_char_whitelist=0123456789").strip()
            if d.isdigit():
                return int(d)
    except Exception:
        pass
    return None


def run_gold_levy(dev, cfg, log, stop):
    """Keep's ring menu is open: Levy -> Free Levy All. The 'Gems Levy' button is never pressed."""
    if not rd_ring(dev, stop, ["Levy"]):
        raise RuntimeError("no Levy button in the Keep menu")
    shot = dev.screenshot()
    rd_snap(shot, "levy_1")
    if not rd_seen(shot, ["Levy"], region=(0, 80, 540, 760)):
        raise RuntimeError("the Levy window did not open")
    left = levy_free_times(shot)
    if left == 0:
        log("[+]  Gold Levy: no free levies left today (Free Levy Times 0)")
        rd_nothing()
        return
    if not rd_tap(dev, stop, ["Free Levy All"], region=(180, 690, 360, 120), shot=shot, wait=(2.0, 2.6)):
        if left is None:
            raise RuntimeError("Free Levy Times not readable and no Free Levy All button read (nothing pressed)")
        dev.tap(*LEVY_ALL_BTN)                           # white text on the orange button is often not read: its fixed spot
        _nap(stop, 2.0, 2.6)
    rd_close_reward(dev, stop)
    after = dev.screenshot()
    rd_snap(after, "levy_2")
    now = levy_free_times(after)
    if left and now is not None and now >= left:
        raise RuntimeError(f"Free Levy All did not use the free levies ({left} -> {now})")
    log(f"[+]  Gold Levy: free levies used" + (f" ({left} -> {now})" if left is not None and now is not None else ""))


TAX_ROWS_Y = (382, 518, 655, 790)           # Food, Lumber, Stone, Iron rows on the Market's Tax page


def tax_cost_digit(shot):
    """The small green number after 'Cost' in the Tax popup (green = affordable). -> its digits as text, or None."""
    try:
        hsv = cv2.cvtColor(shot[495:545, 260:400], cv2.COLOR_BGR2HSV)
        m = cv2.inRange(hsv, (35, 80, 80), (90, 255, 255))
        if cv2.countNonZero(m) < 30:
            return None
        b = cv2.copyMakeBorder(cv2.resize(255 - m, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST), 20, 20, 20, 20,
                               cv2.BORDER_CONSTANT, value=255)
        d = pytesseract.image_to_string(b, config="--psm 7 -c tessedit_char_whitelist=0123456789").strip()
        return d or None
    except Exception:
        return None


def run_resource_tax(dev, cfg, log, stop):
    """Market's ring menu is open: Tax -> Tax on the resource the user picked -> Tax (only while the popup costs 0)."""
    res = str(rd_opt(cfg, "resource_tax", "resource", "Lumber"))
    if not rd_ring(dev, stop, ["Tax"]):
        raise RuntimeError("no Tax button in the Market menu")
    shot = dev.screenshot()
    rd_snap(shot, "tax_1")
    if not rd_seen(shot, ["Revenue", "Free"], region=(0, 80, 540, 600)):
        raise RuntimeError("the Tax page did not open")
    row = find_text(shot, [res + " Revenue", res], region=(60, 300, 300, 420), cutoff=0.72)
    if not row:                                             # the rows are pictures only (no names): always Food, Lumber, Stone, Iron
        order = ["food", "lumber", "stone", "iron"]
        if res.lower() not in order:
            raise RuntimeError(f"no {res} row on the Tax page")
        row = (420, TAX_ROWS_Y[order.index(res.lower())])
    box = (340, row[1] - 40, 200, 80)
    txt = " ".join(t for t, _ in rd_lines(shot, box))
    btn = rd_find(shot, ["Tax"], region=box, cutoff=0.8)
    if not btn and not rd_numbers(txt) and rd_seen(shot, ["Free"], region=(0, 150, 540, 150)):
        btn = (420, row[1])                                 # green Tax button, white text not read: its place in the row
    if not btn or rd_numbers(txt):                          # out of free taxes: the button shows a gem price
        log(f"[+]  Resource Tax: no free tax left today ({res})")
        rd_nothing()
        return
    dev.tap(btn[0], btn[1])
    _nap(stop, 1.8, 2.3)
    shot = dev.screenshot()
    rd_snap(shot, "tax_2")
    cost = next((t for t, _ in rd_lines(shot, (30, 380, 480, 300)) if "cost" in t.lower()), None)
    free = (cost is not None and not any(rd_numbers(cost))) or tax_cost_digit(shot) == "0"
    if not free:
        dev.back()
        raise RuntimeError("the Tax popup does not show a cost of 0 - not pressed")
    if not rd_tap(dev, stop, ["Tax"], region=(160, 570, 240, 80), whole=True, wait=(2.0, 2.6), shot=shot):
        dev.tap(270, 597)                                   # the popup's green Tax button (cost 0 was checked above)
        _nap(stop, 2.0, 2.6)
    rd_close_reward(dev, stop)
    log(f"[+]  Resource Tax: free taxes used on {res}")


LABOR_BTN = (433, 918)                      # orange Labor button, bottom right of the Prison page


def run_labor(dev, cfg, log, stop):
    """Prison's ring menu is open: Captives -> Labor -> Labor. Clear Labor CD (gems) is never pressed."""
    if not rd_ring(dev, stop, ["Captives"]):
        raise RuntimeError("no Captives button in the Prison menu")
    if not rd_tap(dev, stop, ["Labor"], region=(300, 870, 240, 90), whole=True, wait=(1.8, 2.3)):
        shot = dev.screenshot()
        rd_snap(shot, "labor_1")
        if not rd_seen(shot, ["Prison", "Release", "Reset"], region=(0, 0, 540, 960)):
            log("[+]  Labor: no Labor button (no captives?)")
            return
        dev.tap(*LABOR_BTN)                                 # the Prison page is open: orange Labor button, text not read
        _nap(stop, 1.8, 2.3)
    shot = dev.screenshot()
    rd_snap(shot, "labor_2")
    if rd_seen(shot, ["Cooldown"], region=(0, 300, 540, 450)):
        log("[+]  Labor: on cooldown")
        return
    if not rd_tap(dev, stop, ["Labor"], region=(160, 600, 280, 100), whole=True, wait=(2.0, 2.5), shot=shot):
        if not rd_seen(shot, ["instantly receive", "production"], region=(0, 300, 540, 200)):
            raise RuntimeError("no Labor button in the popup")
        dev.tap(270, 638)                                   # orange Labor button of the Labor popup, text not read
        _nap(stop, 2.0, 2.5)
    rd_close_reward(dev, stop)
    log("[+]  Labor: captives put to work (4 h of production)")


def run_general_chest(dev, cfg, log, stop):
    """Tavern's ring menu is open: Great General Chest -> Free (only the free daily open)."""
    if not rd_ring(dev, stop, ["Great General Chest", "General Chest"]):
        raise RuntimeError("no Great General Chest button in the Tavern menu")
    shot = dev.screenshot()
    rd_snap(shot, "gchest_1")
    if not rd_tap(dev, stop, ["Free"], region=(230, 660, 260, 110), whole=True, cutoff=0.85, wait=(2.5, 3.0), shot=shot):
        log("[+]  Great General Chest: the free open is not ready")
        return
    rd_close_reward(dev, stop)
    log("[+]  Great General Chest: free chest opened")


def run_vip_claim(dev, cfg, log, stop):
    """From the city: VIP badge under the avatar -> Claim the daily VIP chest. Packages / More VIP Time never."""
    dev.tap(128, 75)
    _nap(stop, 2.2, 2.8)
    shot = dev.screenshot()
    rd_snap(shot, "vip_1")
    if not rd_seen(shot, ["VIP"], region=(0, 0, 540, 200)):
        raise RuntimeError("the VIP page did not open")
    if not rd_tap(dev, stop, ["Claim"], region=(330, 560, 210, 120), whole=True, wait=(2.0, 2.5), shot=shot):
        log("[+]  VIP: daily chest already claimed")
        rd_nothing()
        return
    rd_close_reward(dev, stop)
    log("[+]  VIP: daily chest claimed")


def run_recruit_citizens(dev, cfg, log, stop):
    """From the city: citizens icon on the top bar -> Citizen List -> Recruit Citizens -> the free Recruit Once,
    then flip the card. Recruit x10 / Buy never."""
    dev.tap(280, 46)
    _nap(stop, 2.0, 2.6)
    shot = dev.screenshot()
    if not rd_seen(shot, ["Citizen"], region=(0, 0, 540, 160)):
        raise RuntimeError("the Citizen page did not open")
    if not rd_tap(dev, stop, ["Citizen List"], region=(270, 80, 270, 70), shot=shot):
        dev.tap(426, 113)
        _nap(stop, 1.4, 1.8)
    if not rd_tap(dev, stop, ["Recruit Citizens"], region=(60, 870, 420, 90), wait=(2.0, 2.5)):
        rd_snap(dev.screenshot(), "citizens_1")
        raise RuntimeError("no Recruit Citizens button")
    shot = dev.screenshot()
    rd_snap(shot, "citizens_2")
    if not rd_tap(dev, stop, ["Free"], region=(280, 860, 240, 100), whole=True, cutoff=0.85, wait=(2.0, 2.5), shot=shot):
        log("[+]  Recruit Citizens: the free recruit is not ready")
        return
    for xy in ((269, 676), (268, 549)):                     # flip the card, then close it
        dev.tap(*xy)
        _nap(stop, 1.4, 1.8)
    rd_close_reward(dev, stop)
    log("[+]  Recruit Citizens: free recruit done")


def run_civilization_card(dev, cfg, log, stop):
    """From the city: World Civilizations building (found by its name) -> the Free card pack. Purchase / Open 10 never."""
    if not rd_city_label(dev, stop, ["World Civilizations", "Civilizations", "Civilization"]):
        raise RuntimeError("the World Civilizations building is not on the city screen")
    shot = dev.screenshot()
    if not rd_seen(shot, ["Card Pack", "Open Card"], region=(0, 0, 540, 300)):
        p = rd_find(shot, ["Card Pack", "Cards"], region=(0, 110, 540, 650))      # a ring menu opened instead of the page
        if p:
            dev.tap(p[0], max(100, p[1] - 35))
            _nap(stop, 2.0, 2.6)
            shot = dev.screenshot()
    rd_snap(shot, "civ_1")
    if not rd_seen(shot, ["Card Pack", "Open Card"], region=(0, 0, 540, 300)):
        raise RuntimeError("the Open Card Pack page did not open")
    if not rd_tap(dev, stop, ["Free"], region=(0, 300, 180, 120), whole=True, cutoff=0.85, wait=(2.5, 3.0), shot=shot):
        log("[+]  Civilization card: the free pack is not ready")
        return
    rd_close_reward(dev, stop)
    log("[+]  Civilization card: free pack opened")


# --- Super Value Return page (Empire Depot icon) -----------------------------------------------------------------
def svr_open_tab(dev, stop, log, tab):
    """From the city: Empire Depot icon (bottom left) -> Super Value Return -> the tab with this name."""
    dev.tap(53, 825)
    _nap(stop, 2.2, 2.8)
    shot = dev.screenshot()
    if not rd_seen(shot, ["Super Value Return", "Value Return", "Super Value"], region=(40, 0, 460, 95)):
        rd_snap(shot, "svr_0")
        raise RuntimeError("the Super Value Return page did not open")
    moves = [(443, 133, 10, 130)] * 3 + [(10, 130, 500, 133)] * 5      # tab bar: look left to right, then back
    for i in range(len(moves) + 1):
        p = find_text(shot, [tab], region=(0, 95, 540, 60), cutoff=0.72)
        if p:
            dev.tap(*p)
            _nap(stop, 1.8, 2.3)
            return True
        if i == len(moves) or stop.is_set():
            break
        dev.swipe(*moves[i], 900)
        _nap(stop, 1.0, 1.3)
        shot = dev.screenshot()
    rd_snap(shot, "svr_tab")
    raise RuntimeError(f"no '{tab}' tab on the Super Value Return page")


def svr_claimable(dev, stop, log, title):
    shot = dev.screenshot()
    rd_snap(shot, "svr_" + title.split()[0].lower())
    if not rd_tap(dev, stop, ["Claimable"], region=(0, 220, 230, 110), wait=(2.2, 2.8), shot=shot):
        log(f"[+]  {title}: nothing to claim")
        rd_nothing()
        return
    rd_close_reward(dev, stop)
    log(f"[+]  {title}: claimed")


def run_empire_depot(dev, cfg, log, stop):
    svr_open_tab(dev, stop, log, "Empire Depot")
    svr_claimable(dev, stop, log, "Empire Depot")


def run_lucky_raffle(dev, cfg, log, stop):
    """Only the free 'Claimable' chest; the Enter buttons (gem / VIP entries) are never pressed."""
    svr_open_tab(dev, stop, log, "Lucky Raffle")
    svr_claimable(dev, stop, log, "Lucky Raffle")


def run_refining_card(dev, cfg, log, stop):
    """Refining Weekly Card tab: every green Claim (Daily Activity 20 / 50 / 80 ...). 'Continue' = not reached yet."""
    svr_open_tab(dev, stop, log, "Refining Weekly Card")
    n, swiped = 0, False
    for _ in range(10):
        if stop.is_set():
            break
        shot = dev.screenshot()
        if rd_tap(dev, stop, ["Claim"], region=(350, 560, 190, 390), whole=True, wait=(2.0, 2.5), shot=shot):
            n += 1
            rd_close_reward(dev, stop, spot=(270, 120))
            continue
        if swiped:
            break
        dev.swipe(443, 888, 460, 613, 800)
        swiped = True
        _nap(stop, 1.2, 1.5)
    log(f"[+]  Refining Weekly Card: {n} reward(s) claimed")


# --- Activity > Go: reach a building without the city map ------------------------------------------------------------
ACT_GO_BTN = (270, 750)                     # green Go button on an Activity task card
MORE_BTN = (488, 648)                       # the round "..." button in the right column (fallback; normally 110 px above the word Alliance)


def find_more_btn(shot):
    """The round '...' button: three orange dots in a row, ~17 px apart, in the right-hand column. -> (x, y) or None."""
    try:
        x0, y0 = 430, 420                                # below the event icons (their badges have orange dots too)
        hsv = cv2.cvtColor(shot[y0:880, x0:540], cv2.COLOR_BGR2HSV)
        m = cv2.inRange(hsv, (10, 120, 180), (30, 255, 255))
        n, _, st, cen = cv2.connectedComponentsWithStats(m)
        b = sorted((float(c[0]) + x0, float(c[1]) + y0) for c, a in zip(cen[1:], st[1:, 4]) if 40 <= a <= 100)
        for i, (ax, ay) in enumerate(b):
            row = [p for p in b if abs(p[1] - ay) <= 4 and 0 <= p[0] - ax <= 45]
            xs = sorted(p[0] for p in row)
            if len(xs) == 3 and all(12 <= xs[k + 1] - xs[k] <= 22 for k in range(2)):
                return int(xs[1]), int(ay)
    except Exception:
        pass
    return None


def open_more_menu(dev, cfg, stop, log):
    """City screen: open the '...' (More) menu. -> screenshot with 'Activity' in it, or None."""
    shot = dev.screenshot()
    mb = find_more_btn(shot)
    ab = None if mb else find_text(shot, ["Alliance"], region=(400, 200, 140, 740), cutoff=0.7)
    spots = ([mb] if mb else []) + ([(int(ab[0]) - 7, int(ab[1]) - 110)] if ab else []) + [MORE_BTN]
    for x, y in spots:
        dev.tap(x, y)
        for _ in range(4):                               # the menu can take a moment to open (animations, lag)
            _nap(stop, 0.9, 1.2)
            shot = dev.screenshot()
            if rd_seen(shot, ["Activity"], region=(0, 100, 540, 800)):
                return shot
        rd_snap(shot, "ago_more_miss")
        if view_of(shot, cfg) != "city":                 # something else opened: close it; on the bare city Back would ask to quit
            dev.back()
            _nap(stop, 0.8, 1.0)
        cancel_quit_dialog(dev, dev.screenshot(), lambda *_: None)
        _nap(stop, 0.5, 0.7)
    return None


def activity_go(dev, cfg, log, stop, tile, probe=False, route=False):
    was = getattr(dev, "_guard_busy", False)
    dev._guard_busy = True                               # the pop-up closer would shut the tile's Go card (seen 23 Oct)
    try:
        return _activity_go(dev, cfg, log, stop, tile, probe, route)
    finally:
        dev._guard_busy = was


def _activity_go(dev, cfg, log, stop, tile, probe=False, route=False):
    """More > Activity > tap the daily's tile > Go. The game moves the camera to the building (any culture, any city
    layout). probe=True saves a picture at every step (debug/duty_ago_*.png) and logs what is read. -> True when Go was pressed."""
    shot = open_more_menu(dev, cfg, stop, log)
    if shot is None:
        raise RuntimeError("the '...' menu with Activity did not open")
    rd_snap(shot, "ago_1_more")
    if not rd_tap(dev, stop, ["Activity"], region=(0, 100, 540, 800), wait=(1.6, 2.0), shot=shot):
        raise RuntimeError("no Activity in the '...' menu")
    key = max(tile.split(), key=len)                     # the game cuts tile labels ("eet Champion", "rade Carriage"): match the main word
    words = [tile, key] + ([key.rstrip("s")] if key.endswith("s") else [])
    hit = None
    tpl = "act_" + re.sub(r"[^a-z]+", "_", tile.lower()).strip("_")    # the tile's picture (labels are cut and hard to read)
    saw_chest = False
    for i in range(8):                                   # 3 tiles a row, the grid scrolls
        shot = dev.screenshot()
        rd_snap(shot, "ago_2_activity" if i == 0 else f"ago_scroll_{i}")
        if not saw_chest and has_template("act_done_chest") and find(shot, "act_done_chest", 0.85, (0, 260, 540, 700)):
            saw_chest = True
        if has_template(tpl):
            h = find(shot, tpl, 0.85, (0, 260, 540, 700))
            if h and 330 <= h[1] <= 885:                 # the whole picture on screen (a 76 px picture centred lower is cut off)
                hit = (h[0], h[1] - 45)                  # tapped 45 px lower below = the picture's centre
        if not hit:
            t = rd_find(shot, words, region=(0, 260, 540, 700), cutoff=0.75)
            hit = t if t and t[1] <= 760 else None
        if hit or stop.is_set():
            break
        dev.swipe(270, 820, 270, 520, 600)
        _nap(stop, 1.0, 1.2)
    if not hit:
        rd_snap(dev.screenshot(), "ago_2_notile")
        if not route and saw_chest:                       # a completed daily's tile turns into a reward chest (its picture is gone)
            log(f"[+]   {tile}: its tile is not on the Activity page but reward chests are - complete today")
            dev.back()
            _nap(stop, 0.8, 1.0)
            return "complete"
        raise RuntimeError(f"no '{tile}' tile on the Activity page")
    dev.tap(int(hit[0]), min(int(hit[1]) + 45, 900))      # the tile picture under its label
    _nap(stop, 1.4, 1.8)
    shot = dev.screenshot()
    rd_snap(shot, "ago_3_tile")
    if probe:
        log("[i]   Activity tile opened: " + " | ".join(t for t, _ in rd_lines(shot, (0, 200, 540, 700)))[:300])
    prog = re.search(r"progress[^0-9]*(\d+)\s*/\s*(\d+)", " ".join(t for t, _ in rd_lines(shot, (40, 380, 460, 200))).lower())
    if prog:
        log(f"[i]   {tile}: progress {prog.group(1)}/{prog.group(2)}")
        if not route and int(prog.group(1)) >= int(prog.group(2)) > 0:
            log(f"[+]   {tile}: already complete today")
            dev.back()
            _nap(stop, 0.8, 1.0)
            return "complete"
    go = rd_find(shot, ["Go"], region=(100, 680, 340, 140), cutoff=0.8)
    if go:
        dev.tap(int(go[0]), int(go[1]))
    elif prog or rd_seen(shot, ["Current Progress", "Complete all"], region=(40, 380, 460, 300)):
        dev.tap(*ACT_GO_BTN)                             # the card is open: its green Go button's fixed spot
    else:
        raise RuntimeError(f"no Go button on the '{tile}' card")
    _nap(stop, 2.5, 3.0)
    shot = dev.screenshot()
    rd_snap(shot, "ago_4_after_go")
    if probe:
        _nap(stop, 1.5, 2.0)
        later = dev.screenshot()
        rd_snap(later, "ago_5_later")
        name = ring_building_name(later)
        log(f"[i]   After Go: view {view_of(later, cfg)}, building label {name[0] if name else 'none'}; "
            + " | ".join(t for t, _ in rd_lines(later, (0, 100, 540, 760)))[:300])
    return True



def open_building_here(dev, cfg, log, stop, bname, spots=None, how="the Activity route"):
    """The building is on screen (after Go: in the middle): tap it and check its ring menu carries the right name.
    A building reached this way is remembered by its picture (learn_building) for the days its tile is gone.
    -> True when that building's ring menu is open."""
    import difflib
    want = bname.lower()
    before = dev.screenshot()
    for x, y in (spots or [(300, 460), (270, 490), (330, 430), (270, 420)]):
        if stop.is_set():
            return False
        dev.tap(x, y)
        _nap(stop, 1.2, 1.5)
        shot = dev.screenshot()
        if want == "keep" and ch_on_page(shot):            # the champion's heart can open the Champion page itself
            return True
        if not find_detail_icon(shot):
            continue
        nm = (ring_building_name(shot) or ("",))[0].lower()
        if want in nm or any(difflib.SequenceMatcher(None, want, w).ratio() >= 0.8 for w in re.findall(r"[a-z ]+", nm)):
            log(f"[i]   {bname} reached by {how} ({x},{y})")
            learn_building(dev, before, bname, x, y)
            return True
        log(f"[i]   after Go the menu at ({x},{y}) is '{nm or '?'}', not {bname}")
        close_ring(dev, stop, (x, y + 60))
    rd_snap(dev.screenshot(), "ago_6_nobuilding")
    return False


# --- learned building pictures: Activity Go teaches the bot where a building is (per instance, any city style) ----------
LEARN_DIR = USER / "city_learn"


def _pinned(dev, bname):
    """Does this building have a place on the instance's keyframe map (learned at an earlier open)?"""
    try:
        if json.loads(_learn_path(dev, bname).with_suffix(".json").read_text(encoding="utf-8")).get("map"):
            return True
    except Exception:
        pass
    return culture_pin(dev, bname) is not None


def _learn_path(dev, bname):
    return LEARN_DIR / re.sub(r"[^\w.-]+", "_", str(getattr(dev, "serial", "default"))) / (re.sub(r"[^a-z]+", "_", bname.lower()).strip("_") + ".png")


def learn_building(dev, shot, bname, x, y):
    """Save the building's own picture (around the spot that opened it, a little below so bubbles above it are left out)."""
    try:
        x0, y0 = max(0, x - 70), max(0, y - 45)
        crop = shot[y0:y0 + 130, x0:x0 + 140]
        if crop.shape[0] < 100 or crop.shape[1] < 100:
            return
        p = _learn_path(dev, bname)
        p.parent.mkdir(parents=True, exist_ok=True)
        meta = {}
        try:                                               # keep the map place learned earlier (and the last picture)
            meta = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
            if p.exists():
                p.replace(p.with_name(p.stem + "_prev.png"))
        except Exception:
            meta = {}
        cv2.imwrite(str(p), crop)
        meta.update({"dx": x - x0, "dy": y - y0, "at": time.strftime("%Y-%m-%d %H:%M")})
        try:                                               # PIN the building on the keyframe map: every successful open on the city
            cmap = city_map(dev)                           # (Activity route, learned picture, map) teaches its map place, so the
            if cmap is not None:                           # next visit is locate -> a few measured drags -> tap (owner 24 Oct: the
                loc = cmap.locate(shot)                    # Activity tile is a teacher, the map is the way)
                if loc:                                    # (a zoomed-in screen does not locate: the pixel check fails)
                    meta["map"] = [round(loc[0] + x), round(loc[1] + y)]
        except Exception:
            pass
        (p.with_suffix(".json")).write_text(json.dumps(meta), encoding="utf-8")
    except Exception:
        pass


def _learn_match(shot, tpl):
    """Best match of a learned picture on the city view, at a few sizes. -> (score, (x, y) top-left on screen, size)"""
    best = (0.0, None, 1.0)
    for f in (1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 0.8, 0.7, 0.6):   # the city may be zoomed in or out compared with the learning time
        t = tpl if f == 1.0 else cv2.resize(tpl, None, fx=f, fy=f, interpolation=cv2.INTER_LINEAR)
        if t.shape[0] >= 760 or t.shape[1] >= 460:
            continue
        r = cv2.matchTemplate(shot[90:860, 0:470], t, cv2.TM_CCOEFF_NORMED)
        _, v, _, l = cv2.minMaxLoc(r)
        if v > best[0]:
            best = (v, (l[0], l[1] + 90), f)
        if v >= 0.85:
            break
    return best


def find_learned_building(dev, cfg, log, stop, bname):
    """No Go route today: find the building's learned picture on the city.
    The city reopens wherever the camera was left (often far from the building; Kiravik 23 Oct 17:50: 17 spiral views
    all 0.3), so: look here -> top-left corner -> the spot where it was found before -> sweep the whole city row by row
    (each move measured from the pictures). The spot found is saved for next time. -> True when its ring menu is open."""
    p = _learn_path(dev, bname)
    pin = culture_pin(dev, bname)
    if not p.exists():
        if pin is None:
            return False
        log(f"[i]   {bname}: culture pin ({city_culture(dev)['culture'].title()}) at {round(pin[0])},{round(pin[1])}")
        return bool(find_on_map(dev, cfg, log, stop, bname, None, {"map": pin, "dx": 0, "dy": 0}, p))
    try:
        tpl = cv2.imread(str(p))
        meta = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
    except Exception:
        return False
    if not meta.get("map") and pin is not None:
        meta["map"] = pin
    tag = "learn_" + re.sub(r"[^a-z]+", "_", bname.lower())
    r = find_on_map(dev, cfg, log, stop, bname, tpl, meta, p)   # the own city map: a few measured drags (owner 23 Oct)
    if r:
        return True
    cmap = city_map(dev)
    scores = []
    nav = None

    def look(where):
        shot = dev.screenshot()
        if view_of(shot, cfg) != "city":
            leave_to_city(dev, cfg, stop, log)
            shot = dev.screenshot()
        if find_detail_icon(shot):                         # a drag read as a tap opened some building's ring: close it first
            close_ring(dev, stop, (270, 490))
            shot = dev.screenshot()
        mv, ml, f = _learn_match(shot, tpl)
        cam = f"@{round(nav.cx)},{round(nav.cy)}" if nav is not None else ""
        scores.append(f"{where}{cam}:{mv:.2f}")
        rd_snap(shot, f"{tag}_v{len(scores):02d}")         # every view (debug for the sweep, 23 Oct)
        if mv < 0.78:
            return False
        x, y = ml[0] + int(meta["dx"] * f), ml[1] + int(meta["dy"] * f)
        if not (60 <= x <= 470 and 150 <= y <= 820):
            return False
        loc = cmap.locate(shot) if cmap is not None else None
        if loc:                                            # remember the spot on the own map (reused by find_on_map)
            meta["map"] = [round(loc[0] + x), round(loc[1] + y)]
        else:
            meta.pop("map", None)
        if open_building_here(dev, cfg, log, stop, bname, spots=[(x, y)], how=f"its learned picture (match {mv:.2f}, {where})"):
            try:                                           # open_building_here saved the fresh picture; add the map place
                m2 = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
                if meta.get("map"):
                    m2["map"] = meta["map"]
                p.with_suffix(".json").write_text(json.dumps(m2), encoding="utf-8")
            except Exception:
                pass
            return True
        return False

    if look("here"):
        return True
    rd_snap(dev.screenshot(), tag + "_0")
    nav = CityNav(dev, stop, log)
    moved = []
    for _ in range(30):                                    # to the top-left corner (like to_origin, but each move is logged)
        if stop.is_set():
            return False
        dx, dy, _ = nav._drag(210, 300, 330, 540)
        moved.append(f"{round(dx)},{round(dy)}")
        if abs(dx) < 4 and abs(dy) < 4:
            break
    nav.cx = nav.cy = 0.0
    log(f"[i]   {bname}: to the corner, map moved per drag: {' '.join(moved)}")
    # (A saved spot from an earlier run is not used yet: "to the corner" does not end at the same place every time on a
    #  diamond-shaped city, so corner-relative coordinates differ between runs. Needs the instance's own stitched map.)
    # The city is a diamond, not a rectangle: a row sweep stops at its slanted edges (Kiravik 18:32 saw a third of it).
    # Explore it cell by cell instead (flood fill): try every neighbouring cell, keep those the camera really reaches.
    SX, SY = 300, 420                                      # one cell = a bit less than one view (CITY_VIEW 360 x 690)
    seen, todo, dead = {(0, 0)}, [(1, 0), (0, 1), (0, -1), (-1, 0)], set()
    if look("c0,0"):
        return True
    while todo and len(seen) < 40:
        if stop.is_set():
            return False
        cur = (round(nav.cx / SX), round(nav.cy / SY))
        todo.sort(key=lambda c: abs(c[0] - cur[0]) + abs(c[1] - cur[1]))   # nearest cell first: fewer drags
        c = todo.pop(0)
        if c in seen or c in dead:
            continue
        nav.pan(c[0] * SX - nav.cx, c[1] * SY - nav.cy)
        got = (round(nav.cx / SX), round(nav.cy / SY))
        if got != c:                                       # the camera could not get there: outside the city
            dead.add(c)
            if got not in seen:                            # it ended in another new cell: look there instead
                c = got
            else:
                continue
        seen.add(c)
        if look(f"c{c[0]},{c[1]}"):
            return True
        for n in ((c[0] + 1, c[1]), (c[0], c[1] + 1), (c[0] - 1, c[1]), (c[0], c[1] - 1)):
            if n not in seen and n not in dead and n not in todo:
                todo.append(n)
    rd_snap(dev.screenshot(), tag + "_last")
    log(f"[i]   {bname}: its learned picture was not found on the city ({' '.join(scores)})")
    return False


# --- the instance's own city map (owner 23 Oct): a KEYFRAME map - every kept view is stored with its map place; after that
#     any city screenshot tells where the camera is, and a building is reached in a few measured drags ----------------------
#     Places are solved from ALL pairwise picture links at once (least squares), never from the finger: the stitched map.png
#     drifted and doubled the wall when views were placed by the drags (Kiravik 23 Oct, 60 views); the same 60 views solved
#     this way give one consistent map (56 placed, 4 countryside views dropped, residual < 2 px, locate 56/56 at < 1 px).
_CITY_MAPS = {}                                            # serial -> CityMap (features + index computed once)
KF_MIN_PTS = 60                                            # agreeing feature points for a link / a locate
KF_MIN_NCC = 0.5                                           # pixel correlation of the overlap: kills links between non-overlapping views
                                                           # made by the static HUD pixels inside CITY_VIEW (a confident 0,0 shift)
KF_MAP_MINUTES = 45                                        # hard cap only; the walk ends by itself when every view is explored


def _map_dir(dev):
    return LEARN_DIR / re.sub(r"[^\w.-]+", "_", str(getattr(dev, "serial", "default")))


def _kf_gray(img):
    x, y, w, h = CITY_VIEW
    return cv2.cvtColor(img[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY)


def _kf_feats(gray):
    if _ORB[0] is None:
        _ORB[0] = cv2.ORB_create(2500)
    return _ORB[0].detectAndCompute(gray, None)


def _kf_consensus(d, min_pts=KF_MIN_PTS, step=300):
    """The shift most of the match vectors `d` agree on (within 6 px). -> (dx, dy, n) or None."""
    if len(d) < min_pts:
        return None
    best_n, best = 0, None
    for i in range(0, len(d), max(1, len(d) // step)):
        n = int(np.sum(np.all(np.abs(d - d[i]) < 6, axis=1)))
        if n > best_n:
            best_n, best = n, d[i]
    if best_n < min_pts:
        return None
    inl = d[np.all(np.abs(d - best) < 6, axis=1)]
    return float(inl[:, 0].mean()), float(inl[:, 1].mean()), best_n


def _kf_ncc(ga, gb, dx, dy):
    """Pixel correlation of the overlap when the content of `ga` sits (dx, dy) further in `gb`. 0 = no / tiny overlap."""
    dx, dy = int(round(dx)), int(round(dy))
    h, w = ga.shape
    ow, oh = w - abs(dx), h - abs(dy)
    if ow < 60 or oh < 60 or ow * oh < 30000:
        return 0.0
    a = ga[max(0, -dy):max(0, -dy) + oh, max(0, -dx):max(0, -dx) + ow].astype(np.float32)
    b = gb[max(0, dy):max(0, dy) + oh, max(0, dx):max(0, dx) + ow].astype(np.float32)
    a -= a.mean()
    b -= b.mean()
    den = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / den) if den > 0 else 0.0


def _kf_link(fa, fb, ga, gb):
    """Picture link between two views: content of a found (dx, dy) further in b. -> (dx, dy, points) or None."""
    (ka, da), (kb, db) = fa, fb
    if da is None or db is None or len(ka) < 30 or len(kb) < 30:
        return None
    m = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(da, db)
    if len(m) < 30:
        return None
    d = np.float32([kb[q.trainIdx].pt for q in m]) - np.float32([ka[q.queryIdx].pt for q in m])
    s = _kf_consensus(d)
    if not s or abs(s[0]) >= ga.shape[1] * 0.95 or abs(s[1]) >= ga.shape[0] * 0.95:
        return None
    if _kf_ncc(ga, gb, s[0], s[1]) < KF_MIN_NCC:
        return None
    return s


def _kf_links_all(views, w, h, min_pts=KF_MIN_PTS):
    """All picture links between the views at once: ONE feature index over every view, one search per view, consensus
    per other view, winner checked by the pixels (the way locate() works). -> [(i, j, dx, dy, points)] with i < j.
    The pairwise BFMatcher took 583 s for 48 views (Kiravik 24 Oct 04:55); this is the same search locate() does in
    about 1 s per view."""
    descs, labels, pts = [], [], []
    for n, (g, (k, d)) in enumerate(views):
        if d is None or len(k) < 30:
            continue
        descs.append(d)
        labels.append(np.full(len(k), n))
        pts.append(np.float32([q.pt for q in k]))
    if len(descs) < 2:
        return []
    labels = np.concatenate(labels)
    allpts = np.vstack(pts)
    index = cv2.FlannBasedMatcher(dict(algorithm=6, table_number=6, key_size=12, multi_probe_level=1), dict(checks=50))
    index.add([np.vstack(descs)])
    index.train()
    edges = []
    for i, (gi, (ki, di)) in enumerate(views):
        if di is None or len(ki) < 30:
            continue
        pi = np.float32([q.pt for q in ki])
        ms = index.knnMatch(di, k=4)
        qi = np.array([m.queryIdx for mm in ms for m in mm], dtype=np.int64)
        ti = np.array([m.trainIdx for mm in ms for m in mm], dtype=np.int64)
        if len(qi) < min_pts:
            continue
        lab = labels[ti]
        for j in np.unique(lab):
            j = int(j)
            if j <= i:                                     # each pair once (i < j); never a view with itself
                continue
            sel = lab == j
            if sel.sum() < min_pts:
                continue
            s = _kf_consensus(allpts[ti[sel]] - pi[qi[sel]], min_pts)   # content of i found (dx, dy) further in j
            if not s or abs(s[0]) >= w * 0.95 or abs(s[1]) >= h * 0.95:
                continue
            if _kf_ncc(gi, views[j][0], s[0], s[1]) < KF_MIN_NCC:
                continue
            edges.append((i, j, s[0], s[1], s[2]))
    return edges


def _kf_solve(n, edges):
    """Camera places from pairwise links (edge i->j with shift (dx, dy) means place_j = place_i - (dx, dy)). Weighted least
    squares with view 0 fixed; links whose residual is over 6 px (repeating art) are dropped and it is solved again;
    views left without a link to the main group are dropped. -> (kept view indices, xs, ys)."""
    kept = list(range(n))
    E = list(edges)

    def groups(k, es):
        parent = {v: v for v in k}

        def f(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a
        for i, j, *_ in es:
            parent[f(i)] = f(j)
        g = {}
        for v in k:
            g.setdefault(f(v), []).append(v)
        return sorted(g.values(), key=len, reverse=True)

    def solve(k, es):
        idx = {v: q for q, v in enumerate(k)}
        A, bx, by, ws = [], [], [], []
        for i, j, dx, dy, w in es:
            r = np.zeros(len(k))
            r[idx[j]] = 1
            r[idx[i]] = -1
            A.append(r)
            bx.append(-dx)
            by.append(-dy)
            ws.append(np.sqrt(w))
        r = np.zeros(len(k))
        r[0] = 1
        A.append(r)
        bx.append(0.0)
        by.append(0.0)
        ws.append(100.0)
        A = np.array(A) * np.array(ws)[:, None]
        px = np.linalg.lstsq(A, np.array(bx) * ws, rcond=None)[0]
        py = np.linalg.lstsq(A, np.array(by) * ws, rcond=None)[0]
        return idx, px, py

    for _ in range(8):
        kept = groups(kept, E)[0] if E else kept[:1]
        E = [e for e in E if e[0] in kept and e[1] in kept]
        if not E:
            break
        idx, px, py = solve(kept, E)
        res = np.array([max(abs(px[idx[j]] - px[idx[i]] + dx), abs(py[idx[j]] - py[idx[i]] + dy)) for i, j, dx, dy, w in E])
        if res.max() <= 6:
            break
        worst = set(np.argsort(-res)[:max(1, int((res > 6).sum() * 0.3))].tolist())
        E = [e for q, e in enumerate(E) if q not in worst]
    if not E:
        return kept[:1], np.zeros(1), np.zeros(1)
    idx, px, py = solve(kept, E)
    return kept, px - px.min(), py - py.min()


class CityMap:
    """The keyframe map of one city: city_learn/<serial>/kf.json (place per keyframe) + kf_NN.png (CITY_VIEW crops).
    A 'camera place' is the map coordinate of screen point (0, 0), so screen (sx, sy) = map (cx + sx, cy + sy)."""

    def __init__(self, path):
        self.path = path                                   # .../map.png (the stitched picture, for looking at)
        try:
            self.meta = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        except Exception:
            self.meta = {}
        self.kf = []                                       # [(x, y, gray, keypoints)]
        self.index = None
        self.labels = self.pts = None
        self.half = bool(self.meta.get("half"))            # shipped culture map: half-size pictures + precomputed features
        try:
            for v in self.meta.get("keyframes", []):
                im = cv2.imread(str(path.parent / v["file"]), cv2.IMREAD_GRAYSCALE)
                if im is not None:
                    if self.half:                          # back to the view size for the pixel check (the shot is blurred the same way)
                        im = cv2.resize(im, (CITY_VIEW[2], CITY_VIEW[3]), interpolation=cv2.INTER_LINEAR)
                    self.kf.append((float(v["x"]), float(v["y"]), im, None))
            if self.meta.get("feats"):
                self._load_feats(path.parent / self.meta["feats"])
        except Exception:
            self.kf = []

    def _load_feats(self, p):
        """Features computed at full size when the culture map was packed (cultures/<c>/feats.npz: pts, desc, label)."""
        with np.load(str(p), allow_pickle=False) as z:
            pts, desc, lab = z["pts"].astype(np.float32), z["desc"].astype(np.uint8), z["label"].astype(np.int64)
        if len(desc) != len(pts) or len(desc) != len(lab) or not len(desc) or lab.max() >= len(self.kf):
            raise ValueError("feats.npz does not fit map.json")
        self.labels, self.pts = lab, pts
        self.index = cv2.FlannBasedMatcher(dict(algorithm=6, table_number=6, key_size=12, multi_probe_level=1), dict(checks=50))
        self.index.add([desc])
        self.index.train()

    def ok(self):
        return len(self.kf) >= 2

    def _build(self):
        """Feature index over ALL keyframes at once (one search per locate instead of one per keyframe: 1.0 s instead of
        5.7 s for 56 keyframes)."""
        if self.index is not None:
            return
        descs, labels, pts = [], [], []
        for n, (x, y, g, _) in enumerate(self.kf):
            k, d = _kf_feats(g)
            if d is None:
                continue
            descs.append(d)
            labels.append(np.full(len(k), n))
            pts.append(np.float32([q.pt for q in k]))
        self.labels = np.concatenate(labels)
        self.pts = np.vstack(pts)
        self.index = cv2.FlannBasedMatcher(dict(algorithm=6, table_number=6, key_size=12, multi_probe_level=1), dict(checks=50))
        self.index.add([np.vstack(descs)])
        self.index.train()

    def locate(self, shot, min_pts=KF_MIN_PTS):
        """Camera place from any city screenshot: its feature points searched in all keyframes at once, the keyframe
        whose points agree on one shift wins once the pixels of the overlap agree too. -> (cx, cy, points) or None."""
        try:
            self._build()
            g = _kf_gray(shot)
            if float(g.std()) < 12:
                return None
            k, d = _kf_feats(g)
            if d is None or len(k) < 30:
                return None
            pts = np.float32([q.pt for q in k])
            ms = self.index.knnMatch(d, k=3)
            qi = np.array([m.queryIdx for mm in ms for m in mm], dtype=np.int64)
            ti = np.array([m.trainIdx for mm in ms for m in mm], dtype=np.int64)
            if len(qi) < min_pts:
                return None
            lab = self.labels[ti]
            cands = []
            for n in np.unique(lab):
                sel = lab == n
                if sel.sum() < min_pts:
                    continue
                s = _kf_consensus(pts[qi[sel]] - self.pts[ti[sel]], min_pts)
                if s:
                    cands.append((s[2], int(n), s[0], s[1]))
            cands.sort(reverse=True)
            if self.half:                                  # same blur as the half-size keyframes
                g = cv2.resize(cv2.resize(g, (g.shape[1] // 2, g.shape[0] // 2), interpolation=cv2.INTER_AREA),
                               (g.shape[1], g.shape[0]), interpolation=cv2.INTER_LINEAR)
            for npts, n, dx, dy in cands[:4]:
                if _kf_ncc(self.kf[n][2], g, dx, dy) >= KF_MIN_NCC:
                    return self.kf[n][0] - dx, self.kf[n][1] - dy, npts
            return None
        except Exception:
            return None


def city_map(dev):
    """The instance's own map, or None when 'Map my city' has not been run yet (or the old stitched-only map is there)."""
    key = str(getattr(dev, "serial", "default"))
    cult = city_culture(dev)
    if cult:                                               # matched to a shipped culture map (owner 25 Oct: no 20-min walk)
        cm = culture_map(cult["culture"])
        if cm is not None:
            return cm
    m = _CITY_MAPS.get(key)
    p = _map_dir(dev) / "map.png"
    if m is None or (p.exists() and m.path.stat().st_mtime != m.meta.get("_mtime")):
        if not p.exists():
            return None
        m = CityMap(p)
        m.meta["_mtime"] = p.stat().st_mtime
        _CITY_MAPS[key] = m
    return m if m.ok() else None


# --- culture reference maps (24 Oct): one keyframe map per city culture, packed from the owner's own accounts and shipped
#     with the app (cultures/<culture>/: map.json + feats.npz + kf_NN.jpg half-size grey + map.jpg). Read-only. -----------
CULTURES = ("europe", "russia", "china", "korea", "japan", "arabia", "america")
_CULTURE_MAPS = {}                                         # culture -> CityMap or None


def culture_dir(name):
    """The newest copy of a culture map: the one the update downloaded (USER) wins over the one in the installer (RES)."""
    for root in (USER / "cultures", RES / "cultures"):
        if (root / name / "map.json").exists():
            return root / name
    return None


def culture_map(name):
    """The shipped reference map of one culture (cached), or None when it is not on this PC."""
    name = str(name or "").lower()
    if name not in CULTURES:
        return None
    if name not in _CULTURE_MAPS:
        d = culture_dir(name)
        m = CityMap(d / "map.png") if d else None          # CityMap reads map.json next to it; map.png itself is not needed
        _CULTURE_MAPS[name] = m if m is not None and m.ok() and m.index is not None else None
    return _CULTURE_MAPS[name]


CULTURE_MARGIN = 1.5                                       # the best culture needs this many times the 2nd best's points
                                                           # (false hits on another culture share walls/terrain - 24 Oct test)


def match_culture(shot):
    """Which shipped culture map does this widest-zoom city screenshot belong to? Located on ALL cultures, the one with
    the most agreeing points wins only with a clear margin. -> (culture, cx, cy, points, second_best) or None."""
    hits = []
    for c in CULTURES:
        m = culture_map(c)
        loc = m.locate(shot) if m is not None else None
        if loc:
            hits.append((loc[2], c, loc[0], loc[1]))
    if not hits:
        return None
    hits.sort(reverse=True)
    second = hits[1][0] if len(hits) > 1 else 0
    if hits[0][0] < KF_MIN_PTS or hits[0][0] < second * CULTURE_MARGIN:
        return None
    return hits[0][1], hits[0][2], hits[0][3], hits[0][0], second


def city_culture(dev):
    """The culture this instance's city was matched to (city_learn/<serial>/culture.json), or None."""
    try:
        c = json.loads((_map_dir(dev) / "culture.json").read_text(encoding="utf-8"))
        return c if c.get("culture") in CULTURES else None
    except Exception:
        return None


def culture_pins(name):
    """Fixed-building places on a culture map: cultures/<c>/pins.json {building: [x, y]} (map coordinates)."""
    d = culture_dir(name) if name else None
    try:
        return {str(k): [float(v[0]), float(v[1])] for k, v in json.loads((d / "pins.json").read_text(encoding="utf-8")).items()}
    except Exception:
        return {}


def culture_pin(dev, bname):
    """The shipped pin of a building for this instance's matched culture, or None."""
    cult = city_culture(dev)
    if not cult:
        return None
    pins = culture_pins(cult["culture"])
    want = re.sub(r"[^a-z]+", "", str(bname).lower())
    return next((v for k, v in pins.items() if re.sub(r"[^a-z]+", "", k.lower()) == want), None)


def _drop_own_places(dev):
    """Learned buildings' map places belong to one map: forget them when the instance changes map."""
    for p in _map_dir(dev).glob("*.json"):
        if p.stem not in ("map", "culture") and not p.stem.endswith("_prev"):
            try:
                m = json.loads(p.read_text(encoding="utf-8"))
                if "map" in m:
                    m.pop("map", None)
                    p.write_text(json.dumps(m), encoding="utf-8")
            except Exception:
                pass


def try_culture_match(dev, cfg, log, stop, nav=None):
    """Map my city, culture first: up to 3 widest-zoom screenshots (moved between) located on every culture map.
    A clear winner is saved as culture.json and used as the instance's city map. -> culture name or None."""
    if not any(culture_map(c) is not None for c in CULTURES):
        log("[i]   Map my city: no culture maps on this PC - walking the city")
        return None
    seen = []
    for i, move in enumerate(((0, 0), (300, 0), (-300, 400))):
        if stop.is_set():
            return None
        if i and nav is not None:
            nav.pan(*move)
        shot = dev.screenshot()
        if view_of(shot, cfg) != "city":
            leave_to_city(dev, cfg, stop, log)
            shot = dev.screenshot()
        r = match_culture(shot)
        if r:
            name, cx, cy, pts, second = r
            d = _map_dir(dev)
            d.mkdir(parents=True, exist_ok=True)
            was = city_culture(dev)
            if not was or was.get("culture") != name or (d / "map.json").exists():
                _drop_own_places(dev)                      # places learned on another map do not fit this one
            (d / "culture.json").write_text(json.dumps({"culture": name, "score": pts, "second": second,
                                                        "at": time.strftime("%Y-%m-%d %H:%M")}), encoding="utf-8")
            _CITY_MAPS.pop(str(getattr(dev, "serial", "default")), None)
            log(f"[+]  Map my city: the city matches the {name.title()} culture map ({pts} points, next best {second}) - no walk needed")
            return name
        seen.append("no clear match")
    log(f"[i]   Map my city: no culture map matched clearly ({len(seen)} views) - walking the city")
    return None


def zoom_out_full(dev, cfg, stop, log):
    """All the way out: Ctrl+wheel until the picture stops getting smaller (Kiravik 23 Oct: the two-notch zoom of the duties
    left the city 1.8x bigger than the game's own widest view, so the map needed 56 views instead of ~8)."""
    if cfg.get("zoom_out", False):
        zoom_out_city(dev, stop, log)
    name = (load_config().get("device_names") or {}).get(dev.serial)
    for i in range(6):
        if stop.is_set():
            return
        before = dev.screenshot()
        if not wheel_zoom_out(dev, log, name):
            return
        _nap(stop, 1.0, 1.3)
        sc = _view_scale(before, dev.screenshot())
        if not sc or sc >= 0.97:
            log(f"[i]   zoom: fully out after {i} more notch(es)")
            return
    dev.zoomed_out = True


def run_map_city(dev, cfg, log, stop):
    """Duty 'Map my city': at the widest zoom, explore the city from the current view (each direction once from every
    view, one measured drag per step), keep every view that shows buildings and is new ground, then solve all the
    views' places from their picture links at once and save them as keyframes. The finger only chooses the next drag."""
    leave_to_city(dev, cfg, stop, log)
    zoom_out_full(dev, cfg, stop, log)
    nav = CityNav(dev, stop, log, pause=0.6)
    if try_culture_match(dev, cfg, log, stop, nav):
        return
    x, y, w, h = CITY_VIEW
    STEPX, STEPY = int(w * 0.5), int(h * 0.6)          # one step in GAME px: views overlap half sideways, 40 % up/down. The
    views = []                                             # finger distance comes from the measured gain (a fixed 234 finger px
    t0 = time.time()                                       # moved up to 340 game px = 20 px overlap, no picture link); a third
    # of a view sideways (120 px) was not enough on Kiravik's east side (cliff, few features): 03:28 "no picture link".
    drags = [0]
    gain = [1.3, 1.3]                                      # game px per finger px, per axis (measured as we go)

    def known(px_, py_):
        """Is this camera place within one third of a view of ground we already keep?"""
        return any(abs(ex - px_) < 120 and abs(ey - py_) < 120 for _, _, _, ex, ey in views)

    def anchor(k, d, g):
        """Where the pictures say the camera is: median of the places implied by picture links to the kept views nearby
        (by the running estimate). -> (x, y) or None when no nearby view links. The drag estimates drift (24 Oct 02:44:
        164 moves put 3 'new' views on one edge spot and lost the west half as a separate group), the pictures do not."""
        order = sorted(range(len(views)), key=lambda i: abs(views[i][3] - nav.cx) / w + abs(views[i][4] - nav.cy) / h)
        for rng in (1.5, None):                            # the 4 nearest first; then ALL views (a drag read as 0 by the static
            px_, py_ = [], []                              # HUD pixels leaves the estimate a whole step off - 24 Oct 03:28)
            for i in (order[:4] if rng else order):
                _, g2, f2, ex, ey = views[i]
                if rng and not (abs(ex - nav.cx) < w * rng and abs(ey - nav.cy) < h * rng):
                    continue
                s = _kf_link(f2, (k, d), g2, g)
                if s:
                    px_.append(ex - s[0])
                    py_.append(ey - s[1])
            if px_:
                ax, ay = float(np.median(px_)), float(np.median(py_))
                if rng is None or abs(ax - nav.cx) > 60 or abs(ay - nav.cy) > 60:
                    log(f"[i]   view at {round(nav.cx)},{round(nav.cy)}: the pictures put it at {round(ax)},{round(ay)} ({len(px_)} links{'' if rng else ', found far from the estimate'})")
                return ax, ay
        return None

    nolink = [0]                                           # views in a row that linked to nothing (a window over the city?)
    last = [None]                                          # the settled picture the camera is on now (reused: one
                                                           # screenshot round per move instead of three)

    def clean_shot(shot=None):
        shot = dev.screenshot() if shot is None else shot
        if view_of(shot, cfg) != "city":
            if find_text(shot, ["disconnected", "someone login", "logged in elsewhere", "another device"], region=(60, 250, 360, 300), cutoff=0.8):
                raise RuntimeError("the game was opened on another device - mapping stopped (the bot waits and reclaims it, then maps again)")
            leave_to_city(dev, cfg, stop, log)
            shot = nav._settled()
        if find_detail_icon(shot):
            close_ring(dev, stop, (270, 490))
            shot = nav._settled()
        last[0] = shot
        return shot

    def keep(shot):
        """Keep this view when it shows buildings (the countryside / water has few feature points and no links)."""
        g = _kf_gray(shot)
        k, d = _kf_feats(g)
        if d is None or len(k) < 700:
            log(f"[i]   view at {round(nav.cx)},{round(nav.cy)}: {0 if d is None else len(k)} points - countryside, not kept")
            return False
        if views:
            a = anchor(k, d, g)
            if a is None:
                log(f"[i]   view at {round(nav.cx)},{round(nav.cy)}: no picture link to any kept view - not kept")
                rd_snap(shot, f"map_x{drags[0]:03d}")       # debug: the view that did not link
                nolink[0] += 1
                if nolink[0] >= 2:                         # Stalds 24 Oct 05:14: a "Bounty Cave" sale window over the city (HUD
                    nolink[0] = 0                          # still visible = looks like the city) - 7 unlinked views in 10 min
                    log("[!]   two views in a row link to nothing - a window may be covering the city, closing pop-ups")
                    clear_popups(dev, cfg, log, stop, rounds=3)
                    last[0] = None
                return False
            nolink[0] = 0
            nav.cx, nav.cy = a                             # the estimate follows the pictures from here on
        if known(nav.cx, nav.cy):                          # the same ground again (the edge slid the camera back)
            return False
        views.append((shot[y:y + h, x:x + w].copy(), g, (k, d), nav.cx, nav.cy))
        rd_snap(shot, f"map_v{len(views):02d}")
        log(f"[i]   map view {len(views)} at about {round(nav.cx)},{round(nav.cy)} ({len(k)} points)")
        log("@act " + json.dumps({"k": "step", "text": f"Duty: Mapping {len(views)} views"}))   # progress in the instance chip
        if len(views) % 5 == 0:
            log(f"[i]   Map my city: {len(views)} views, {drags[0]} moves, {round((time.time() - t0) / 60)} min")
        return True

    def move(dx, dy):
        """ONE measured drag per step (pan() tops a step up with extra drags: 20 s per step on Kiravik 24 Oct 00:08).
        Wherever the camera lands is fine - the places come from the pictures, not from the steps."""
        fx = max(-300.0, min(300.0, dx / gain[0]))         # finger px for the wanted game px (kept inside the map area)
        fy = max(-560.0, min(560.0, dy / gain[1]))
        ddx, ddy, after = nav._drag(270 + fx / 2, 440 + fy / 2, 270 - fx / 2, 440 - fy / 2, before=last[0])
        last[0] = after
        drags[0] += 1
        nav.cx -= ddx
        nav.cy -= ddy
        for a, f, m in ((0, fx, ddx), (1, fy, ddy)):       # learn the gain from full drags (not the edge's short ones)
            if abs(f) >= 100 and abs(m) >= abs(f) / 2:
                gain[a] = max(0.8, min(2.0, 0.7 * gain[a] + 0.3 * abs(m) / abs(f)))
        return -ddx, -ddy

    def timed_out():
        if stop.is_set() or time.time() - t0 > KF_MAP_MINUTES * 60:
            log(f"[!]   Map my city: stopping after {round((time.time() - t0) / 60)} min - saving what there is")
            return True
        return False

    keep(clean_shot())
    # The frontier is the VIEWS, not rows: the city is a diamond and its slanted edges slide the camera sideways, so a
    # row sweep ends early (00:22 run: 9 views) while from every view each of the 4 directions is tried once and
    # wherever the camera lands is a new view when it is new ground (the 21:45 run reached all 60 views this way).
    STEP = ((STEPX, 0), (0, STEPY), (-STEPX, 0), (0, -STEPY))
    tried = [set()]                                        # per view: directions already tried from it
    at = 0                                                 # the view the camera is on (or nearest to)

    def nearest():
        return min(range(len(views)), key=lambda i: abs(views[i][3] - nav.cx) + abs(views[i][4] - nav.cy))

    # No "idle" stop: Kiravik 24 Oct 01:45 ended after 8 old-ground moves in a row at the diamond's edge with half the
    # city unexplored (18 views, 13 min) while Stalds got the whole city (51 views, 28 min) only because its edge runs were
    # shorter. The walk ends when every kept view has had its 4 directions; the time cap is the safety.
    while len(views) < 80 and not timed_out():            # ~45 views with half-view steps; Stalds needed 51 at a third
        if len(tried[at]) == 4:                            # this view is done: go to the nearest view with directions left
            cand = [i for i in range(len(views)) if len(tried[i]) < 4]
            if not cand:
                break
            at = min(cand, key=lambda i: abs(views[i][3] - nav.cx) + abs(views[i][4] - nav.cy))
            for _ in range(4):
                rx, ry = views[at][3] - nav.cx, views[at][4] - nav.cy
                if abs(rx) < 80 and abs(ry) < 80 or timed_out():
                    break
                move(max(-STEPX, min(STEPX, rx)), max(-STEPY, min(STEPY, ry)))
            continue
        # Directions whose landing (by the measured gain) is ground we already keep - the way we came, a neighbour's
        # view - are marked tried without a drag: half of all moves landed on old ground before (Stalds: 137 moves, 56 views).
        for d in STEP:
            if d not in tried[at] and known(nav.cx + d[0], nav.cy + d[1]):
                tried[at].add(d)
        left = [d for d in STEP if d not in tried[at]]
        if not left:
            continue
        # Outward first (away from the centre of what is mapped) so the walk sweeps to the edge instead of circling
        # the start; sideways before up/down (the diamond is wider than tall).
        cx0 = sum(v[3] for v in views) / len(views)
        cy0 = sum(v[4] for v in views) / len(views)
        sx, sy = max(left, key=lambda d: (d[0] / STEPX * (nav.cx - cx0) + d[1] / STEPY * (nav.cy - cy0), abs(d[0]) > 0))
        tried[at].add((sx, sy))
        move(sx, sy)
        if keep(clean_shot(last[0])):
            tried.append(set())
            at = len(views) - 1
        else:                                              # old ground (the edge: did not move, slid, or sprang back)
            at = nearest()
    if len(views) < 2:
        log("[!]  Map my city: fewer than 2 views with buildings - nothing saved")
        return
    # solve the places from the pictures
    t1 = time.time()
    edges = _kf_links_all([(v[1], v[2]) for v in views], w, h)
    kept, px, py = _kf_solve(len(views), edges)
    d = _map_dir(dev)
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob("kf_*.png"):
        old.unlink()
    kfs = []
    for q, i in enumerate(kept):
        cv2.imwrite(str(d / f"kf_{q + 1:02d}.png"), views[i][0])
        kfs.append({"file": f"kf_{q + 1:02d}.png", "x": float(px[q]), "y": float(py[q])})
    W, H = int(px.max()) + w + 1, int(py.max()) + h + 1   # the stitched picture, for the user
    canvas = np.zeros((H, W, 3), np.uint8)
    for q, i in enumerate(kept):
        X, Y = int(round(px[q])), int(round(py[q]))
        canvas[Y:Y + h, X:X + w] = views[i][0]
    cv2.imwrite(str(d / "map.png"), canvas)
    meta = {"at": time.strftime("%Y-%m-%d %H:%M"), "views": len(kept), "size": [W, H], "keyframes": kfs,
            "links": len(edges), "zoomed_out": True}
    (d / "map.json").write_text(json.dumps(meta), encoding="utf-8")
    try:
        (d / "culture.json").unlink()                      # the own walked map is used from now on
    except Exception:
        pass
    _CITY_MAPS.pop(str(getattr(dev, "serial", "default")), None)
    for p in d.glob("*.json"):                             # learned buildings' map places belonged to the old map
        if p.stem != "map" and not p.stem.endswith("_prev"):
            try:
                m = json.loads(p.read_text(encoding="utf-8"))
                if "map" in m:
                    m.pop("map", None)
                    p.write_text(json.dumps(m), encoding="utf-8")
            except Exception:
                pass
    dropped = len(views) - len(kept)
    log(f"[+]  City map built: {len(kept)} keyframes ({dropped} views without a picture link dropped), {len(edges)} links, "
        f"{W}x{H} px, {drags[0]} moves, {round(time.time() - t0)} s (solve {round(time.time() - t1)} s)")


def goto_on_map(dev, cfg, log, stop, nav, cmap, mapx, mapy, tol=60):
    """Closed loop on the own map: locate -> drag the difference -> locate again, until map point (mapx, mapy) is near
    the middle of the view. -> the point's screen place (sx, sy), or None."""
    x, y, w, h = CITY_VIEW
    mid_x, mid_y = x + w / 2, y + h / 2
    lost = 0
    for i in range(8):
        if stop.is_set():
            return None
        shot = nav._settled() if i else dev.screenshot()
        if find_detail_icon(shot):
            close_ring(dev, stop, (270, 490))
            shot = nav._settled()
        r = cmap.locate(shot)
        if not r:
            lost += 1
            if lost >= 3:
                log("[i]   the screen is not found on the city map (zoomed differently, window in the way, or the city changed?)")
                return None
            leave_to_city(dev, cfg, stop, log)
            continue
        lost = 0
        nav.cx, nav.cy = r[0], r[1]
        sx, sy = mapx - nav.cx, mapy - nav.cy
        if abs(sx - mid_x) <= tol and abs(sy - mid_y) <= tol:
            log(f"[i]   camera at {round(nav.cx)},{round(nav.cy)} on the map ({r[2]} points), target on screen at {round(sx)},{round(sy)}")
            return sx, sy
        nav.pan(sx - mid_x, sy - mid_y)                    # measured drags (the diamond's edge stops them safely)
    return None


def find_on_map(dev, cfg, log, stop, bname, tpl, meta, p):
    """Building with a known map place: go there on the own map, check its learned picture near the expected spot, tap.
    -> True (ring open), False (went there but not it), None (no map / place / camera lost: explore instead)."""
    cmap = city_map(dev)
    if cmap is None or not meta.get("map"):
        return None
    nav = CityNav(dev, stop, log)
    at = goto_on_map(dev, cfg, log, stop, nav, cmap, float(meta["map"][0]), float(meta["map"][1]))
    if at is None:
        return None
    shot = dev.screenshot()
    mv, ml, f = _learn_match(shot, tpl) if tpl is not None else (0.0, None, 1.0)
    ex, ey = at
    if ml:
        x, y = ml[0] + int(meta["dx"] * f), ml[1] + int(meta["dy"] * f)
    if ml and mv >= 0.6 and abs(x - ex) < 90 and abs(y - ey) < 90:
        spot = (x, y)
    else:
        spot = (int(ex), int(ey))                          # trust the map place (the picture may have changed: upgrade, decoration)
        if tpl is not None:
            log(f"[i]   {bname}: picture match {mv:.2f} near the map place - tapping the map place")
    if not (60 <= spot[0] <= 470 and 150 <= spot[1] <= 820):
        return None
    if open_building_here(dev, cfg, log, stop, bname, spots=[spot], how=f"the city map (match {mv:.2f})"):
        try:                                               # refresh the place and the picture (open_building_here learned it)
            m2 = json.loads(p.with_suffix(".json").read_text(encoding="utf-8")) if p.with_suffix(".json").exists() else {"dx": 0, "dy": 0}
            m2["map"] = [round(nav.cx + spot[0]), round(nav.cy + spot[1])]
            p.with_suffix(".json").write_text(json.dumps(m2), encoding="utf-8")
        except Exception:
            pass
        return True
    return False

# --- Alliance page ------------------------------------------------------------------------------------------------
def run_alliance_gift(dev, cfg, log, stop):
    """Alliance > Alliance Gift > Open All. Gifts keep coming all day: this runs every hour, and 'nothing to open'
    counts only when the gift page itself was seen (owner 23 Oct: never Done without checking the screen)."""
    rd_alliance_page(dev, cfg, stop)
    if not rd_alliance_row(dev, stop, ["Alliance Gift"]):
        raise RuntimeError("no Alliance Gift row on the Alliance page (screen not found)")
    shot = dev.screenshot()
    if not rd_tap(dev, stop, ["Open All"], region=(130, 860, 280, 100), wait=(2.2, 2.8), shot=shot):
        rd_snap(shot, "agift_1")
        if not rd_seen(shot, ["Alliance Gift", "Gift"], region=(0, 0, 540, 160)):
            raise RuntimeError("the Alliance Gift page did not open (screen not found)")
        log("[+]  Alliance Gift: gift page checked - nothing to open right now")
        return
    rd_close_reward(dev, stop, spot=(270, 120))
    log("[+]  Alliance Gift: all gifts opened")


def run_alliance_help(dev, cfg, log, stop):
    """Alliance > Help > Help All. Help requests come and go all day: runs every hour; 'nothing to help' counts only when
    the help page itself was seen."""
    rd_alliance_page(dev, cfg, stop)
    if not rd_alliance_row(dev, stop, ["Alliance Help", "Help"]):
        raise RuntimeError("no Help row on the Alliance page (screen not found)")
    shot = dev.screenshot()
    rd_snap(shot, "ahelp_1")
    if rd_tap(dev, stop, ["Help All", "Help all"], region=(0, 760, 540, 200), wait=(1.8, 2.3), shot=shot):
        log("[+]  Alliance Help: Help All pressed")
        return
    if not rd_seen(shot, ["Help"], region=(0, 0, 540, 160)):
        raise RuntimeError("the Alliance Help page did not open (screen not found)")
    log("[+]  Alliance Help: help page checked - nobody needs help right now")


def run_alliance_treasure(dev, cfg, log, stop):
    """Alliance > Alliance Treasure > Open All (chests still on a timer stay closed)."""
    rd_alliance_page(dev, cfg, stop)
    if not rd_alliance_row(dev, stop, ["Alliance Treasure"]):
        raise RuntimeError("no Alliance Treasure row on the Alliance page")
    shot = dev.screenshot()
    rd_snap(shot, "atreasure_1")
    if not rd_tap(dev, stop, ["Open All"], region=(240, 860, 300, 100), wait=(2.2, 2.8), shot=shot):
        log("[+]  Alliance Treasure: nothing to open")
        rd_nothing()
        return
    rd_close_reward(dev, stop, spot=(270, 120))
    log("[+]  Alliance Treasure: Open All pressed")


# --- Event Center -------------------------------------------------------------------------------------------------
def run_ranger_knight(dev, cfg, log, stop):
    """Event Center is open: Limited tab -> Ranger Knight Order -> Quick Dispatch -> Send. Refresh never."""
    shot = dev.screenshot()
    if rd_tap(dev, stop, ["Limited"], region=(0, 60, 540, 140), shot=shot):
        shot = dev.screenshot()
    for i in range(5):
        p = rd_find(shot, ["Ranger Knight Order", "Knight Order"], region=(0, 150, 540, 720), cutoff=0.75)
        if p:
            dev.tap(p[0], p[1])
            _nap(stop, 2.2, 2.8)
            break
        if i == 4 or stop.is_set():
            rd_snap(shot, "rko_0")
            raise RuntimeError("no Ranger Knight Order in the Event Center")
        dev.swipe(270, 780, 270, 420, 600)
        _nap(stop, 1.0, 1.3)
        shot = dev.screenshot()
    shot = dev.screenshot()
    rd_snap(shot, "rko_1")
    if not rd_tap(dev, stop, ["Quick Dispatch"], region=(300, 830, 240, 110), wait=(1.8, 2.3), shot=shot):
        log("[+]  Ranger Knight Order: nothing to dispatch")
        rd_nothing()
        return
    if not rd_tap(dev, stop, ["Send"], region=(140, 700, 280, 110), whole=True, wait=(1.8, 2.3)):
        rd_snap(dev.screenshot(), "rko_2")
        dev.back()
        log("[+]  Ranger Knight Order: no task to send right now")
        return
    log("[+]  Ranger Knight Order: tasks sent")


# --- training with speed-ups ----------------------------------------------------------------------------------------
def rd_finish_with_speedups(dev, stop, log, what):
    """The busy button (Speed Up / Training Speedup) -> Finish All with speed-up ITEMS. Instant (gems) never."""
    if not rd_tap(dev, stop, ["Training Speedup", "Speed Up", "Speedup"], region=(300, 860, 240, 100), wait=(1.8, 2.3)):
        log(f"[i]   {what}: no Speed Up button")
        return False
    shot = dev.screenshot()
    rd_snap(shot, "speedup")
    if not rd_tap(dev, stop, RD_SPEEDUP_WORDS, region=(0, 860, 340, 100), wait=(2.0, 2.6), shot=shot):
        log(f"[i]   {what}: no Finish All button (no speed-up items?)")
        dev.back()
        return False
    log(f"[i]   {what}: finished with speed-up items")
    return True


def rd_clear_field(dev, stop, n=12):
    """Empty the text box that has the keyboard: cursor to the end, then delete backwards (and forwards, in case
    the cursor stayed at the start)."""
    dev._run("shell", "input", "keyevent", "123", *(["67"] * n), *(["112"] * n), timeout=15)
    _nap(stop, 0.4, 0.6)


def rd_train(dev, cfg, log, stop, duty_id, ring_word, qty_xy, what):
    """Barracks / Trap Factory ring menu is open: type the amount, check it was taken, press Train."""
    amount = max(1, min(int(float(rd_opt(cfg, duty_id, "amount", 500) or 500)), 9999999))
    if not rd_ring(dev, stop, [ring_word]):
        raise RuntimeError(f"no {ring_word} button in the menu")
    shot = dev.screenshot()
    rd_snap(shot, duty_id + "_1")
    if not rd_find(shot, ["Train"], region=(300, 860, 240, 100), whole=True):
        if rd_find(shot, ["Speed Up", "Speedup"], region=(300, 860, 240, 100)):
            log(f"[+]  {what}: already busy")
            if rd_opt(cfg, duty_id, "speedups", False) in (True, "true", "1", 1):
                rd_finish_with_speedups(dev, stop, log, what)
            return
        raise RuntimeError("the training page did not open")
    dev.tap(*qty_xy)
    _nap(stop, 1.4, 1.8)
    rd_clear_field(dev, stop)                                # the box already holds a number: typing would add to it
    dev.text(str(amount))
    _nap(stop, 0.6, 0.9)
    rd_keyboard_ok(dev, stop)
    shot = dev.screenshot()
    rd_snap(shot, duty_id + "_2")
    got = rd_numbers(" ".join(t for t, _ in rd_lines(shot, (qty_xy[0] - 90, qty_xy[1] - 28, 180, 56))))
    if amount not in got:                                    # never train the full (max) amount by mistake
        raise RuntimeError(f"the amount box does not show {amount} (read {got[:3]}) - not trained")
    if not rd_tap(dev, stop, ["Train"], region=(300, 860, 240, 100), whole=True, wait=(2.0, 2.5), shot=shot):
        raise RuntimeError("no Train button")
    log(f"[+]  {what}: {amount:,} queued")
    if rd_opt(cfg, duty_id, "speedups", False) in (True, "true", "1", 1):
        rd_finish_with_speedups(dev, stop, log, what)


def run_train_troops(dev, cfg, log, stop):
    rd_train(dev, cfg, log, stop, "train_troops", "Train", (418, 797), "Train troops")


def run_build_traps(dev, cfg, log, stop):
    rd_train(dev, cfg, log, stop, "build_traps", "Build", (438, 797), "Build traps")


def run_troop_healing(dev, cfg, log, stop):
    """Hospital's ring menu is open: Heal -> Heal (resources). Instant Heal (gems) never; paid offers are closed."""
    if not rd_ring(dev, stop, ["Heal"]):
        raise RuntimeError("no Heal button in the Hospital menu")
    shot = dev.screenshot()
    rd_snap(shot, "heal_1")
    if not rd_seen(shot, ["Wounded", "Hospital"], region=(0, 0, 540, 400)):
        raise RuntimeError("the Hospital page did not open")
    if rd_tap(dev, stop, ["Heal"], region=(300, 860, 240, 100), whole=True, wait=(2.0, 2.5), shot=shot):
        log("[+]  Troop healing: healing started")
    elif rd_find(shot, ["Speed Up", "Speedup"], region=(300, 860, 240, 100)):
        log("[+]  Troop healing: already healing")
    else:
        log("[+]  Troop healing: no wounded troops")
        return
    if rd_opt(cfg, "troop_healing", "speedups", False) in (True, "true", "1", 1):
        rd_finish_with_speedups(dev, stop, log, "Troop healing")


# --- Tavern -------------------------------------------------------------------------------------------------------
ACTIVITY_CARD_CHEST = (100, 445)                          # the first task card's chest (claimed cards move to the end)
ACTIVITY_MILESTONES = [(195, 194), (252, 186), (321, 186), (394, 192), (458, 186)]


def run_activity_chests(dev, cfg, log, stop):
    """Tavern's ring menu is open: Activity -> open every task chest that is ready, then the milestone chests."""
    if not rd_ring(dev, stop, ["Activity"]):
        raise RuntimeError("no Activity button in the Tavern menu")
    shot = dev.screenshot()
    rd_snap(shot, "activity_1")
    if not rd_seen(shot, ["Activity"], region=(0, 0, 540, 120)):
        raise RuntimeError("the Activity page did not open")
    n = 0
    for _ in range(40):
        if stop.is_set():
            break
        dev.tap(*ACTIVITY_CARD_CHEST)
        _nap(stop, 1.2, 1.6)
        if not wheel_reward_panel(dev.screenshot()):
            break
        n += 1
        rd_close_reward(dev, stop, spot=(270, 240))
    m = 0
    for xy in ACTIVITY_MILESTONES:
        if stop.is_set():
            break
        dev.tap(*xy)
        _nap(stop, 1.2, 1.6)
        if wheel_reward_panel(dev.screenshot()):
            m += 1
        dev.tap(*xy)                                        # second tap closes the reward / the preview
        _nap(stop, 0.9, 1.2)
        rd_close_reward(dev, stop, spot=(270, 240))
    rd_snap(dev.screenshot(), "activity_2")
    log(f"[+]  Activity: {n} task chest(s) and {m} milestone chest(s) opened")


# --- loops --------------------------------------------------------------------------------------------------------
GATE_VISITOR_BUBBLE = (236, 498)


def run_gate_visitors(dev, cfg, log, stop):
    """From the city: while the 'visitor is waiting at the city gate' banner shows, tap the '?' over the gate,
    skip the envoy's text, Claim, close the reward."""
    n = 0
    for _ in range(8):
        if stop.is_set():
            break
        shot = dev.screenshot()
        if not rd_seen(shot, ["visitor is waiting", "city gate", "visitor"], region=(0, 85, 540, 160)):
            break
        dev.tap(*GATE_VISITOR_BUBBLE)
        _nap(stop, 2.0, 2.5)
        shot = dev.screenshot()
        if not rd_seen(shot, ["Visiting Envoy", "Envoy", "Visitor"], region=(0, 300, 540, 600)):
            rd_snap(shot, "visitor_miss")
            log("[!]  Gate visitors: the visitor did not open (city view moved?) - stopped")
            break
        dev.tap(275, 805)                                   # skip the envoy's text
        _nap(stop, 1.0, 1.3)
        if not rd_tap(dev, stop, ["Claim"], region=(140, 840, 260, 100), whole=True, wait=(1.8, 2.3)):
            rd_snap(dev.screenshot(), "visitor_noclaim")
            dev.back()
            break
        n += 1
        if wheel_reward_panel(dev.screenshot()):
            dev.tap(245, 630)
            _nap(stop, 1.0, 1.3)
    log(f"[+]  Gate visitors: {n} visitor(s) claimed")


CARRIAGE_SLOTS = [(218, 772), (290, 583), (424, 435), (128, 491)]


def carriage_left(img):
    for t, _ in rd_lines(img, (0, 60, 540, 260)):
        if "transport" in t.lower() and "today" in t.lower():
            nums = rd_numbers(t)
            return nums[-1] if nums else None
    return None


def run_trade_carriage(dev, cfg, log, stop):
    """From the city: Trade Carriage (found by its name) -> each empty slot: take the first carriage, Select Formation,
    March with the preset already shown, until Transports Left Today is 0. Refresh Goods is never pressed (owner)."""
    if not rd_city_label(dev, stop, ["Trade Carriage", "Carriage"]):
        raise RuntimeError("the Trade Carriage is not on the city screen")
    shot = dev.screenshot()
    rd_snap(shot, "carriage_1")
    left = carriage_left(shot)
    if left is None:
        raise RuntimeError("the Trade Carriage page did not open")
    sent = 0
    for xy in CARRIAGE_SLOTS:
        if left <= 0 or stop.is_set():
            break
        dev.tap(*xy)
        _nap(stop, 1.8, 2.3)
        shot = dev.screenshot()
        if not rd_tap(dev, stop, ["Select Formation"], region=(270, 870, 270, 100), wait=(2.2, 2.8), shot=shot):
            if carriage_left(shot) is None:                 # a carriage that is already on the road opened: close it
                dev.back()
                _nap(stop, 1.2, 1.6)
            continue
        if not rd_tap(dev, stop, ["March"], region=(270, 870, 270, 100), whole=True, wait=(2.2, 2.8)):
            rd_snap(dev.screenshot(), "carriage_nomarch")
            dev.back()
            _nap(stop, 1.2, 1.6)
            raise RuntimeError("no March button on the formation screen")
        sent += 1
        shot = dev.screenshot()
        new = carriage_left(shot)
        left = new if new is not None else left - 1
    log(f"[+]  Trade Carriage: {sent} transport(s) sent, {left} left today")


# --- Shrine -------------------------------------------------------------------------------------------------------
def run_offering_tributes(dev, cfg, log, stop):
    """Shrine's ring menu is open: Offer -> Offer Tribute -> slider to the most (50) -> Offer. Uses Tribute items;
    Offer Gems is never pressed and the popup must not mention gems."""
    times = max(1, min(int(float(rd_opt(cfg, "offering_tributes", "times", 1) or 1)), 20))
    if not rd_ring(dev, stop, ["Offer"]):
        raise RuntimeError("no Offer button in the Shrine menu")
    done = 0
    for _ in range(times):
        if stop.is_set():
            break
        shot = dev.screenshot()
        rd_snap(shot, "offer_1")
        if not rd_tap(dev, stop, ["Offer Tribute"], region=(260, 860, 280, 100), wait=(1.6, 2.0), shot=shot):
            log("[+]  Offering: no tributes left")
            if rd_opt(cfg, "offering_tributes", "gems", False) in (True, "true", "1", 1):
                offer_with_gems(dev, cfg, log, stop)
            break
        for _ in range(55):                                 # + to the most allowed (50 per offer)
            if stop.is_set():
                break
            dev.tap(435, 473)
            time.sleep(0.08)
        _nap(stop, 0.8, 1.1)
        shot = dev.screenshot()
        rd_snap(shot, "offer_2")
        if any("gem" in t.lower() for t, _ in rd_lines(shot, (30, 330, 480, 330))):
            dev.back()
            raise RuntimeError("the offer popup mentions gems - not pressed")
        if not rd_tap(dev, stop, ["Offer"], region=(170, 575, 220, 70), whole=True, wait=(2.2, 2.8), shot=shot):
            dev.back()
            raise RuntimeError("no Offer button in the popup")
        done += 1
        rd_close_reward(dev, stop, spot=(270, 120))
    log(f"[+]  Offering: {done} tribute offer(s) made")


# --- gem duties (owner 22 Oct: the user switches them on knowing they spend gems; no extra gem block) ----------------
def run_wisdom_dome_tokens(dev, cfg, log, stop):
    """Wisdom Dome's ring menu is open: Rare Books -> + under the Goddess Honor Token -> Purchase by Gems (500 gems)
    and Buy in the confirm popup, as many times as set (game limit 5 a day). SPENDS GEMS."""
    want = max(1, min(int(float(rd_opt(cfg, "wisdom_dome_tokens", "times", 5) or 5)), 5))
    if not rd_ring(dev, stop, ["Rare Books"]):
        raise RuntimeError("no Rare Books button in the Wisdom Dome menu")
    shot = dev.screenshot()
    rd_snap(shot, "dome_1")
    if not rd_seen(shot, ["Rare Books", "Honor"], region=(0, 0, 540, 300)):
        raise RuntimeError("the Rare Books page did not open")
    dev.tap(222, 819)                                       # + under the Goddess Honor Token
    _nap(stop, 1.8, 2.3)
    bought = 0
    for _ in range(want):
        if stop.is_set():
            break
        shot = dev.screenshot()
        rd_snap(shot, "dome_2")
        for t, _ in rd_lines(shot, (0, 80, 540, 300)):
            if "daily chance" in t.lower():
                n = rd_numbers(t)
                if len(n) >= 2 and n[-2] >= n[-1]:
                    log(f"[+]  Wisdom Dome: today's {n[-1]} gem purchases are used")
                    rd_nothing()
                    want = bought
                break
        if bought >= want:
            break
        if not rd_tap(dev, stop, ["Purchase by Gems", "Purchase"], region=(300, 100, 240, 120), wait=(1.4, 1.8), shot=shot):
            if bought == 0:
                raise RuntimeError("no Purchase by Gems button on the Goddess Honor Token page")
            break
        if not rd_tap(dev, stop, ["Buy"], region=(240, 500, 280, 110), whole=True, wait=(1.8, 2.3)):
            rd_snap(dev.screenshot(), "dome_3")
            dev.back()
            raise RuntimeError("no Buy button in the confirm popup")
        bought += 1
        rd_close_reward(dev, stop, spot=(270, 120))
    log(f"[+]  Wisdom Dome: {bought} Goddess Honor Token(s) bought with gems")


def offer_with_gems(dev, cfg, log, stop):
    """The Shrine's Offer page is open and no tributes are left: Offer Gems -> + to the amount set -> Offer.
    SPENDS GEMS (25 for the first, more for each next one). Only when the user ticked it on the Shrine offering duty."""
    count = max(1, min(int(float(rd_opt(cfg, "offering_tributes", "gem_count", 3) or 3)), 50))
    shot = dev.screenshot()
    rd_snap(shot, "offergems_1")
    if not rd_tap(dev, stop, ["Offer Gems"], region=(20, 860, 300, 110), wait=(1.6, 2.0), shot=shot):
        log("[!]  Offering: no Offer Gems button on the Offer page")
        return
    for _ in range(count - 1):                              # the slider starts at 1
        if stop.is_set():
            break
        dev.tap(439, 468)
        _nap(stop, 0.35, 0.5)
    shot = dev.screenshot()
    rd_snap(shot, "offergems_2")
    if not rd_tap(dev, stop, ["Offer"], region=(170, 560, 230, 75), whole=True, wait=(2.2, 2.8), shot=shot):
        dev.back()
        log("[!]  Offering: no Offer button in the gem popup")
        return
    rd_close_reward(dev, stop, spot=(270, 120))
    log(f"[+]  Offering: {count} gem offer(s) made (no tributes left)")


# ------------------------------------------------------------ Royal Thief (owner's recording 22 Oct 21:38, project doc claude/flow_royal_thief.md)
# A thief only appears when an alliance member invites you (Invite icon on the left of the city / map).
# Invite > Royal Thief row > Go > tap a thief that is not killed > map flies there > tap it > Attack > Attack >
# troops (Power First or a preset) > March. No free march: wait for the soonest march to come back, then try again.
RT_POLL_MIN = 15                         # look for an Invite icon this often
RT_RECHECK_MIN = 5                       # after marching / when something got in the way: look again this soon
RT_THIEF_SPOTS = {"left": (120, 600), "mid": (288, 392), "right": (410, 600)}
RT_PRESETS = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII"]


def rt_save(serial, **kw):
    with _DUTY_LOCK:
        data = load_duty_state()
        row = data.setdefault(serial or "default", {}).setdefault("royal_thief", {})
        row.update(kw)
        save_duty_state(data)


def rt_row(serial):
    return (load_duty_state().get(serial or "default") or {}).get("royal_thief") or {}


def rt_col(x):
    return "left" if x < 190 else ("mid" if x < 350 else "right")


def rt_marches(img):
    """World map March panel (top left): -> (busy, total, soonest_seconds or None), or None when not read."""
    busy = total = None
    for t, _ in rd_lines(img, (0, 125, 300, 45)):
        m = re.search(r"(\d)\s*/\s*(\d)", t)
        if "arch" in t.lower() and m:
            busy, total = int(m.group(1)), int(m.group(2))
            break
    if busy is None:
        return None
    waits = []
    txt = " ".join(t for t, _ in rd_lines(img, (40, 165, 240, 60 * max(1, busy)))).replace(";", ":")
    for h, mi, s in re.findall(r"(\d{1,2})\s*[:.]\s*(\d{2})\s*[:.]\s*(\d{2})", txt):
        waits.append(int(h) * 3600 + int(mi) * 60 + int(s))
    return busy, total, (min(waits) if waits else None)


def run_royal_thief(dev, cfg, log, stop):
    serial = getattr(dev, "serial", "")
    day = _game_day()
    row = rt_row(serial)
    done = row.get("done", []) if row.get("day") == day else []
    rt_save(serial, day=day, done=done, next_at=time.time() + RT_POLL_MIN * 60)

    def later(minutes, why):
        rt_save(serial, next_at=time.time() + minutes * 60)
        log(f"[*]  Royal Thief: {why} - again at {time.strftime('%H:%M', time.localtime(time.time() + minutes * 60))}")

    shot = dev.screenshot()
    p = rd_find(shot, ["Invite"], region=(0, 220, 130, 560), cutoff=0.75, whole=True)
    if not p:
        return                                              # no invite = no thief to kill
    dev.tap(p[0], max(100, p[1] - 18))
    _nap(stop, 2.0, 2.5)
    shot = dev.screenshot()
    rd_snap(shot, "thief_1")
    if not rd_seen(shot, ["Invitation"], region=(0, 0, 540, 80)):
        dev.back()
        return later(RT_RECHECK_MIN, "the Invitation list did not open")
    rows = []
    for t, ws in rd_lines(shot, (0, 90, 540, 760)):
        if "thief" in t.lower():
            rows.append(int(sum(s[2] for s in ws) / len(ws)))
    target = None
    for y in rows:                                          # the inviter's name sits on the line above "Royal Thief"
        who = " ".join(t for t, _ in rd_lines(shot, (60, max(0, y - 40), 300, 30))).strip() or f"row{y}"
        if who not in done:
            target = (y, who)
            break
    if not target:
        dev.back()
        return
    y, who = target
    dev.tap(438, max(100, y - 8))                           # green Go on that row
    _nap(stop, 2.5, 3.0)
    shot = dev.screenshot()
    rd_snap(shot, "thief_2")
    if not rd_seen(shot, ["Royal Thief", "Help kill"], region=(0, 0, 540, 160)):
        leave_to_city(dev, cfg, stop, log)
        return later(RT_RECHECK_MIN, "the Royal Thief page did not open")
    alive, killed = [], set()
    for t, ws in rd_lines(shot, (0, 200, 540, 300)):
        x = int(sum(s[1] for s in ws) / len(ws))
        if "killed" in t.lower():
            killed.add(rt_col(x))
        elif re.search(r"x\s*[:.]?\s*\d{3}", t.lower()):
            alive.append(rt_col(x))
    if row.get("who") == who and row.get("col") in killed:   # the thief we attacked is dead: this invite is done
        done.append(who)
        rt_save(serial, done=done, who="", col="")
        leave_to_city(dev, cfg, stop, log)
        log(f"[+]  Royal Thief: {who}'s thief killed")
        return
    cols = [c for c in ("mid", "left", "right") if c not in killed and (c in alive or not alive)]
    if row.get("who") == who and row.get("col") in cols:     # keep on the same thief until it is killed
        cols.remove(row["col"])
        cols.insert(0, row["col"])
    if not cols:
        done.append(who)
        rt_save(serial, done=done)
        leave_to_city(dev, cfg, stop, log)
        log(f"[+]  Royal Thief: all 3 of {who}'s thieves are killed")
        return
    col = cols[0]
    dev.tap(*RT_THIEF_SPOTS[col])
    shot = _wait_view(dev, cfg, stop, "landscape", 60)     # the map flies to the thief
    if shot is None:
        leave_to_city(dev, cfg, stop, log)
        return later(RT_RECHECK_MIN, "the map did not open")
    _nap(stop, 2.0, 2.5)
    shot = dev.screenshot()
    rd_snap(shot, "thief_3")
    mk = rt_marches(shot)
    if mk and mk[0] >= mk[1]:
        wait = (mk[2] or 120) + 20
        leave_to_city(dev, cfg, stop, log)
        return later(max(1, round(wait / 60)), f"all {mk[1]} marches are out")
    dev.tap(274, 477)                                      # the thief sits in the middle of the screen
    _nap(stop, 1.5, 2.0)
    if not rd_ring(dev, stop, ["Attack"]):
        leave_to_city(dev, cfg, stop, log)
        return later(RT_RECHECK_MIN, "no Attack on the thief")
    shot = dev.screenshot()
    rd_snap(shot, "thief_4")
    if not rd_tap(dev, stop, ["Attack"], region=(150, 680, 260, 130), whole=True, wait=(2.0, 2.5), shot=shot):
        leave_to_city(dev, cfg, stop, log)
        return later(RT_RECHECK_MIN, "no Attack button on the thief's page")
    shot = dev.screenshot()
    if not rd_seen(shot, ["Select a Preset", "March"], region=(0, 0, 540, 180)):
        rd_snap(shot, "thief_5")
        leave_to_city(dev, cfg, stop, log)
        return later(RT_RECHECK_MIN, "the March page did not open (no free march?)")
    troops = str(rd_opt(cfg, "royal_thief", "troops", "Power First"))
    if troops.startswith("Preset"):
        n = RT_PRESETS.index(troops.split()[-1]) if troops.split()[-1] in RT_PRESETS else 0
        for _ in range(2):                                  # the first tap on a preset is sometimes lost
            dev.tap(62 + n * 61, 115)
            _nap(stop, 1.4, 1.8)
    else:
        dev.tap(57, 928)                                    # small button bottom left -> Power First
        _nap(stop, 1.2, 1.5)
        if not rd_tap(dev, stop, ["Power First"], region=(90, 885, 230, 75), wait=(1.4, 1.8)):
            dev.tap(57, 928)
            _nap(stop, 1.2, 1.5)
            rd_tap(dev, stop, ["Power First"], region=(90, 885, 230, 75), wait=(1.4, 1.8))
    shot = dev.screenshot()
    rd_snap(shot, "thief_6")
    if not rd_tap(dev, stop, ["March"], region=(330, 880, 210, 75), wait=(2.0, 2.5), shot=shot):
        leave_to_city(dev, cfg, stop, log)
        return later(RT_RECHECK_MIN, "no March button")
    shot = dev.screenshot()
    if rd_seen(shot, ["Select a Preset"], region=(0, 120, 540, 60)):
        rd_snap(shot, "thief_7")
        leave_to_city(dev, cfg, stop, log)
        return later(RT_RECHECK_MIN, "the march did not leave (no troops?)")
    rt_save(serial, who=who, col=col)
    later(RT_RECHECK_MIN, f"marched on {who}'s thief ({troops})")
    leave_to_city(dev, cfg, stop, log)


# ------------------------------------------------------------ Monster rally starter (owner's recording 23 Oct 07:44, project doc claude/flow_rally_monsters.md)
# Coordinates come from the Telegram section (live feed + boss finder). Map > coordinate search (Server / X / Y / Go) >
# tap the monster in the middle > Attack > monster page (name, level) > Alliance War > rally time > preset > March.
# No free march: stay on the monster until it is rallied (owner 23 Oct 08:20) - only a march line to it or the monster
# disappearing makes the bot move on. 'max_wait' minutes > 0 sets a limit (0 = no limit, the default).
# Skipped: a monster that is gone (no Attack / other name on its page) or that a march line is already going to.
MR_POLL_MIN = 1                          # look for a new target this often (monsters die fast - owner 23 Oct)
MR_FORGET_MIN = 30                       # a target that was started / skipped is not tried again for this long
MR_CENTER = (272, 480)                   # the searched tile sits in the middle of the map
MR_ALLIANCE_WAR = (384, 768)              # Alliance War button on a monster page (Attack sits at 170)
MR_MARCH = (404, 910)                    # March button on the Alliance War troop page
MR_EMPTY = (330, 250)                     # a map spot with no button: closes a ring (40,640 was the Quests button)
MR_NEAR = [(0, 0), (0, -30), (-30, 10), (30, 10), (0, 35)]   # the monster's picture can sit a little off the tile centre
MR_TIMES = {"5": (190, 512), "10": (395, 512), "30": (190, 590), "60": (395, 590)}   # Alliance War Rally! buttons (fallback spots)


def mr_save(serial, **kw):
    with _DUTY_LOCK:
        data = load_duty_state()
        row = data.setdefault(serial or "default", {}).setdefault("monster_rally", {})
        row.update(kw)
        save_duty_state(data)


def mr_row(serial):
    return (load_duty_state().get(serial or "default") or {}).get("monster_rally") or {}


MR_DEFAULT_RULES = [{"name": "Ymir", "min": 1, "max": 99, "preset": "I", "time": "5", "on": True}]


def mr_rules(cfg):
    """The Monster rallies tab: which monster (and levels) each preset attacks, and that preset's rally time."""
    rules = rd_opt(cfg, "monster_rally", "rules", None)
    out = []
    for r in (rules if isinstance(rules, list) else MR_DEFAULT_RULES):
        if not isinstance(r, dict) or not str(r.get("name") or "").strip() or r.get("on") is False:
            continue
        out.append({"name": str(r["name"]).strip(), "min": int(r.get("min") or 1), "max": int(r.get("max") or 99),
                    "preset": r.get("preset") if r.get("preset") in RT_PRESETS else "I",
                    "time": str(r.get("time")) if str(r.get("time")) in MR_TIMES else "5"})
    return out


def mr_rule_for(rules, name, level):
    """-> (index, rule) of the first row that fits this monster, or (None, None)."""
    low = str(name or "").lower().strip()
    if not low:
        return None, None
    for i, r in enumerate(rules):
        rn = r["name"].lower()
        if (rn in low or low in rn) and (not level or r["min"] <= level <= r["max"]):
            return i, r
    return None, None


def mr_targets(cfg, tried):
    """Telegram coordinates of monsters that sit on a preset (Monster rallies tab), best first (newest, then nearest).
    -> [{name, level, x, y, km, t, rule, rank}]"""
    rules = mr_rules(cfg)
    age = float(rd_opt(cfg, "monster_rally", "max_age", 30) or 30) * 60
    now = time.time()
    feed, _ = feed_get(_feed_keep[0] if _feed_keep[0] is not None else int(cfg.get("tg_keep_min", 60) or 0))
    try:
        upd = json.loads(BOSS_FILE.read_text(encoding="utf-8")).get("updated", 0) if BOSS_FILE.exists() else 0
    except Exception:
        upd = 0
    pool = list(feed) + [dict(b, t=upd) for b in get_bosses()]
    out, seen = [], set()
    for it in pool:
        key = f"{it.get('x')},{it.get('y')}"
        if key in seen or now - float(tried.get(key, 0)) < MR_FORGET_MIN * 60:
            continue
        seen.add(key)
        if now - float(it.get("t") or 0) > age:
            continue
        if str(it.get("name") or "").lower().strip() in NEVER_JOIN:      # world bosses: nobody can rally them
            continue
        i, r = mr_rule_for(rules, it.get("name"), it.get("level") or 0)
        if r is None:
            continue
        out.append(dict(it, rule=r, rank=i))
    km = lambda it: it.get("km") if it.get("km") is not None else 1e9
    out.sort(key=lambda it: (-round(float(it.get("t") or 0) / 60), km(it)))     # newest minute first, then nearest
    return out


def mr_march_line(img, center=MR_CENTER):
    """True when a march line (white = another player, green = an alliance march) ends at the monster in the middle.
    First guess, not checked on a real line yet: long straight bright strokes whose line passes the centre."""
    try:
        h, w = img.shape[:2]
        x0, y0, x1, y1 = 0, 200, w, 800                     # below the march panel, above the bottom buttons
        roi = img[y0:y1, x0:x1]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        white = cv2.inRange(hsv, (0, 0, 215), (180, 45, 255))
        green = cv2.inRange(hsv, (45, 120, 120), (80, 255, 255))
        cx, cy = center[0] - x0, center[1] - y0
        for name, mask in (("white", white), ("green", green)):
            m = mask.copy()
            cv2.circle(m, (cx, cy), 45, 0, -1)              # the monster itself (Ymir is white / ice blue) never counts
            segs = cv2.HoughLinesP(m, 1, np.pi / 180, 40, minLineLength=70, maxLineGap=12)
            for s in (segs.reshape(-1, 4) if segs is not None else []):    # (N,1,4) or (N,4) depending on the OpenCV build
                ax, ay, bx, by = [int(v) for v in s]
                dx, dy = bx - ax, by - ay
                ln = (dx * dx + dy * dy) ** 0.5
                if ln < 70 or abs(dy) < 6:                  # text and flat map edges are level - a march line to the middle is not
                    continue
                dist = abs(dy * (cx - ax) - dx * (cy - ay)) / ln
                near_end = min(((ax - cx) ** 2 + (ay - cy) ** 2) ** 0.5, ((bx - cx) ** 2 + (by - cy) ** 2) ** 0.5)
                if dist < 22 and near_end < 150:
                    return name
    except Exception:
        return None
    return None


def mr_box_number(shot, box):
    """One number box of the coordinate search (digits only, enlarged) -> int or None. The whole-line read missed
    'Y: 622' on Kiravik (23 Oct)."""
    try:
        x, y, w, h = box
        g = cv2.resize(cv2.cvtColor(shot[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY), None, fx=3, fy=3)
        t = pytesseract.image_to_string(g, config="--psm 7 -c tessedit_char_whitelist=0123456789").strip()
        return int(t) if t.isdigit() else None
    except Exception:
        return None


def mr_search(dev, stop, x, y):
    """Map shown: open the coordinate search, type X and Y, check them, Go. -> True when the map flew there."""
    if cancel_quit_dialog(dev, dev.screenshot(), lambda *_: None):   # an 'exit the game?' box left open would eat the tap
        _nap(stop, 0.8, 1.2)
    dev.tap(300, 845)                                       # the X: Y: box at the bottom of the map
    _nap(stop, 1.4, 1.8)
    shot = dev.screenshot()
    if not rd_seen(shot, ["Search", "Server"], region=(0, 280, 540, 280)):
        rd_snap(shot, "mrally_search")
        return False
    for spot, val in (((206, 535), x), ((428, 545), y)):
        dev.tap(*spot)
        _nap(stop, 1.0, 1.4)
        rd_clear_field(dev, stop, 6)
        dev.text(str(val))
        _nap(stop, 0.5, 0.8)
        rd_keyboard_ok(dev, stop)
    shot = dev.screenshot()
    got = [mr_box_number(shot, (110, 500, 150, 45)), mr_box_number(shot, (315, 500, 150, 45))]   # the X and Y boxes
    if None in got:                                          # digits-only read missed: the old whole-line read
        got = rd_numbers(" ".join(t for t, _ in rd_lines(shot, (30, 505, 480, 70))))
    if x not in got and y not in got:                       # neither number read back: the typing went wrong
        rd_snap(shot, "mrally_xy")
        dev.back()
        _nap(stop, 0.8, 1.1)
        raise RuntimeError(f"the search box does not show X {x} Y {y} (read {got[:4]})")
    # one number misread (Y 444 read as 40404): go anyway - the monster check on the tile catches a wrong spot
    if not rd_tap(dev, stop, ["Go"], region=(180, 575, 200, 75), whole=True, wait=(1.6, 1.9), shot=shot):
        dev.tap(333, 610)
        _nap(stop, 1.6, 1.9)
    return True


MR_EMPTY_RING = [False]                    # set by mr_attack_spot: the last ring was the empty-land one (Occupy / Teleport)


def mr_attack_spot(shot):
    """The monster's ring menu: -> (x, y) of its 'Attack' label, or None. Plain OCR only reads 'Att' on the ring's
    dark label (Kiravik 23 Oct) - read the ring area enlarged and bright. Empty land (Occupy / Teleport ring) sets
    MR_EMPTY_RING[0] so the caller can skip the monster at once."""
    MR_EMPTY_RING[0] = False
    try:
        for t, ws in ocr_lines(shot[250:700, 0:540], 2.5, bright=True):
            for w in ws:
                word = str(w[0]).lower().strip("|.,:;!() ")
                if word in ("attack", "attac", "atack"):
                    return int(w[1]), int(w[2]) + 250
                if word in ("occupy", "teleport"):
                    MR_EMPTY_RING[0] = True
    except Exception as _e:
        quiet_err("mr_attack_spot", _e)
    if MR_EMPTY_RING[0]:
        return None
    p = rd_find(shot, ["Attack"], region=(0, 250, 540, 450), cutoff=0.75)
    return (p[0], p[1]) if p else None


def mr_still_there(dev, stop):
    """While waiting for a march: tap the monster in the middle; True when its ring shows Attack (the ring is closed again)."""
    for dx, dy in MR_NEAR[:3]:
        if stop.is_set():
            return True
        dev.tap(MR_CENTER[0] + dx, MR_CENTER[1] + dy)
        _nap(stop, 1.2, 1.5)
        hit = mr_attack_spot(dev.screenshot())
        dev.tap(*MR_EMPTY)                                  # close the ring on empty map
        _nap(stop, 0.7, 0.9)
        if hit:
            return True
        if MR_EMPTY_RING[0]:                                # empty land on the tile: the monster is dead
            return False
    return False


def mr_open_monster(dev, stop, want, level=None):
    """Tap the monster in the middle; its ring menu must have Attack, its page must show the wanted name.
    -> ('ok', page_shot) | ('gone', None) | ('other', name_read)"""
    for dx, dy in MR_NEAR:
        if stop.is_set():
            return "gone", None
        dev.tap(MR_CENTER[0] + dx, MR_CENTER[1] + dy)
        _nap(stop, 1.3, 1.7)
        shot = dev.screenshot()
        rd_snap(shot, f"mrally_ring{MR_NEAR.index((dx, dy))}")   # what the tap opened (test pictures)
        p = mr_attack_spot(shot)
        if not p:
            dev.tap(*MR_EMPTY)                              # empty map: close whatever ring opened
            _nap(stop, 0.8, 1.0)
            if MR_EMPTY_RING[0]:                            # Occupy / Teleport = empty land: gone, no more taps
                return "gone", None
            continue
        dev.tap(p[0], max(100, p[1] - 30))
        _nap(stop, 1.8, 2.3)
        page = dev.screenshot()
        rd_snap(page, "mrally_page")
        if not rd_seen(page, ["Alliance War", "Winning Odds"], region=(0, 650, 540, 220)):
            dev.back()
            _nap(stop, 0.8, 1.1)
            continue
        head = " ".join(t for t, _ in ocr_lines(page[120:300, 0:540], 2.5, bright=True))   # plain OCR garbled the name
        import difflib
        toks = re.findall(r"[a-z]+", head.lower())
        words = [w for w in want.lower().split() if len(w) > 2]
        missing = [w for w in words if not any(w == t or difflib.SequenceMatcher(None, w, t).ratio() >= (0.75 if len(w) <= 5 else 0.8) for t in toks)]
        lv = re.search(r"(?:lv|level)\.?\s*(\d{1,2})\b", head.lower())
        wrong_lv = bool(level and lv and int(lv.group(1)) != int(level))
        if missing or wrong_lv:                               # every word of the name (Epic Cerberus is not Legendary Cerberus) and the level must fit
            dev.back()
            _nap(stop, 0.8, 1.1)
            return "other", head[:40]
        return "ok", page
    return "gone", None


def run_monster_rally(dev, cfg, log, stop):
    """The universal pop-up closer is held off meanwhile: it closed the rally time window by its X (Kiravik 23 Oct)."""
    was = getattr(dev, "_guard_busy", False)
    dev._guard_busy = True
    try:
        return _run_monster_rally(dev, cfg, log, stop)
    finally:
        dev._guard_busy = was


def _run_monster_rally(dev, cfg, log, stop):
    serial = getattr(dev, "serial", "")
    row = mr_row(serial)
    now = time.time()
    tried = {k: v for k, v in (row.get("tried") or {}).items() if now - float(v) < MR_FORGET_MIN * 60}
    mr_save(serial, tried=tried, next_at=now + MR_POLL_MIN * 60)

    def later(minutes, why):
        mr_save(serial, tried=tried, next_at=time.time() + minutes * 60)
        log(f"[*]  Monster rally: {why} - again at {time.strftime('%H:%M', time.localtime(time.time() + minutes * 60))}")

    targets = mr_targets(cfg, tried)
    if not targets:
        return later(MR_POLL_MIN, "no Telegram coordinate matches the monster list")
    keep_free = int(float(rd_opt(cfg, "monster_rally", "keep_free", 0) or 0))
    max_wait = float(rd_opt(cfg, "monster_rally", "max_wait", 0) or 0) * 60          # 0 = stay until it is rallied
    keep_sta = int(float(rd_opt(cfg, "monster_rally", "keep_stamina", 100) or 0))
    skip_lines = rd_opt(cfg, "monster_rally", "skip_busy", True) in (True, "true", "1", 1)
    if not ensure_view(dev, cfg, stop, log, "world"):       # the world map ("landscape" here = the rally list view)
        return later(MR_POLL_MIN, "the map did not open")
    shot = _wait_view(dev, cfg, stop, "landscape", 30)
    if shot is None:
        return later(MR_POLL_MIN, "the map did not open")
    for tgt in targets[:10]:                                # a gone / busy monster: try the next coordinate
        if stop.is_set():
            return
        mode, preset = tgt["rule"]["time"], tgt["rule"]["preset"]   # this monster's own preset and rally time
        key, label = f"{tgt['x']},{tgt['y']}", f"{('Lv' + str(tgt['level']) + ' ') if tgt.get('level') else ''}{tgt.get('name') or 'monster'} ({tgt['x']},{tgt['y']})"
        tried[key] = time.time()
        mr_save(serial, tried=tried)
        if not mr_search(dev, stop, tgt["x"], tgt["y"]):
            return later(MR_POLL_MIN, "the coordinate search did not open")
        _nap(stop, 1.0, 1.4)
        t0 = time.time()
        gone, checked, said = False, time.time(), False
        while True:                                          # stay on this monster until a march is free
            if stop.is_set():
                return
            shot = dev.screenshot()
            rd_snap(shot, "mrally_map")
            if view_of(shot, cfg) not in ("landscape", "world"):        # a popup / restart took the map away
                tried.pop(key, None)
                return later(1, "the map was closed while waiting")
            line = mr_march_line(shot) if skip_lines else None
            if line:
                log(f"[*]  Monster rally: {label} - a {line} march line is already going to it, skipped")
                break
            mk = None if getattr(dev, "mr_no_march_count", False) else rt_marches(shot)
            if not mk:
                dev.mr_no_march_count = True                 # the March panel is folded on the map: do not spend time reading it again
            free = (mk[1] - mk[0]) if mk else None
            if free is None or free > keep_free:
                break
            if not said:
                log(f"[*]  Monster rally: {label} - all marches busy, waiting on the monster")
                said = True
            if time.time() - checked > 60:                   # still there? (tap it: the ring must have Attack, then close it)
                checked = time.time()
                if not mr_still_there(dev, stop):
                    gone = True
                    log(f"[*]  Monster rally: {label} - the monster is not there any more, skipped")
                    break
            if max_wait and time.time() - t0 >= max_wait:
                log(f"[*]  Monster rally: {label} - no free march for {round(max_wait / 60)} min")
                tried.pop(key, None)                         # not the monster's fault: it may be tried again next time
                return later(max(1, round(((mk[2] if mk else None) or 120) / 60)), "all marches are busy")
            wait = min(20, max(5, (mk[2] or 20) + 2))
            _nap(stop, wait, wait + 1)
        if line or gone:
            continue
        while True:                                          # the march count was not read / was wrong: keep trying here
            got, page = mr_open_monster(dev, stop, tgt.get("name") or "", tgt.get("level"))
            if got != "ok":
                break
            dev.tap(*MR_ALLIANCE_WAR)                        # fixed spot (the white-on-brown label is rarely read); window checked below
            _nap(stop, 1.0, 1.2)
            shot = None
            for _ in range(5):                               # the rally time window can be slow to open
                shot = dev.screenshot()
                if rd_seen(shot, ["Alliance War Rally", "rally time"], region=(0, 300, 540, 160)):
                    break
                _nap(stop, 0.7, 0.9)
            else:
                rd_snap(shot, "mrally_time")
                if view_of(shot, cfg) not in ("landscape", "world"):
                    dev.back()
                    _nap(stop, 1.0, 1.3)
                if not max_wait or time.time() - t0 < max_wait:   # back on the map = no free march (or no stamina): wait here
                    log(f"[*]  Monster rally: {label} - no rally window (all marches busy?), trying again in 30 s")
                    _nap(stop, 30, 32)
                    continue
                tried.pop(key, None)
                return later(2, "no rally window (all marches busy or no stamina)")
            dev.tap(*MR_TIMES.get(mode, MR_TIMES["5"]))      # fixed spot of the 5/10/30/60 min button; the troop page is checked below
            _nap(stop, 1.0, 1.2)
            for _ in range(12):                              # the troop page can take several seconds to open (owner 23 Oct)
                shot = dev.screenshot()
                if rd_seen(shot, ["Select a Preset"], region=(0, 0, 540, 180)) and not rd_seen(shot, ["rally time"], region=(0, 380, 540, 80)):
                    break
                _nap(stop, 0.6, 0.8)
            rd_snap(shot, "mrally_preset_page")
            if not rd_seen(shot, ["Select a Preset", "Alliance War"], region=(0, 0, 540, 180)) or rd_seen(shot, ["rally time"], region=(0, 380, 540, 80)):
                rd_snap(shot, "mrally_nomarch")
                dev.tap(504, 352)                                # X of the rally time popup
                _nap(stop, 1.0, 1.3)
                if (not max_wait or time.time() - t0 < max_wait) and not (skip_lines and mr_march_line(dev.screenshot())):
                    log(f"[*]  Monster rally: {label} - no free march yet, waiting on the monster")
                    _nap(stop, 20, 22)
                    continue
                tried.pop(key, None)
                return later(2, "the troop page did not open (no free march?)")
            break
        if got == "gone":
            log(f"[*]  Monster rally: {label} - the monster is not there any more, skipped")
            continue
        if got == "other":
            log(f"[*]  Monster rally: {label} - another monster stands there ({page}), skipped")
            continue
        n = RT_PRESETS.index(preset) if preset in RT_PRESETS else 0
        for _ in range(2):                                   # the first tap on a preset is sometimes lost
            dev.tap(62 + n * 61, 115)
            _nap(stop, 0.6, 0.8)
        shot = dev.screenshot()
        rd_snap(shot, "mrally_troops")
        sta = rd_numbers(" ".join(t for t, _ in rd_lines(shot, (280, 820, 260, 55))))
        if keep_sta and len(sta) >= 2 and sta[-2] - sta[-1] < keep_sta:
            dev.back()
            _nap(stop, 1.0, 1.3)
            return later(60, f"stamina {sta[-2]} would drop under {keep_sta}")
        if not rd_seen(shot, ["Select a Preset", "Alliance War"], region=(0, 0, 540, 180)):
            dev.back()
            raise RuntimeError("no March button (the troop page is gone)")
        st_ = read_stamina(shot) if cfg.get("stamina_auto", True) else None
        if st_ and st_[0] < st_[1]:                          # not enough stamina for the rally: use stamina items like the joiner (owner 23 Oct)
            log(f"[!]  Monster rally: not enough stamina ({st_[0]:,} of {st_[1]:,}) - using stamina items")
            if not refill_stamina(dev, cfg, log, stop, st_[1]):
                dev.back()
                return later(30, "not enough stamina (and no stamina items)")
            shot = dev.screenshot()
        dev.tap(*MR_MARCH)                                   # fixed spot (the gold label is rarely read, PC 23 Oct); checked below
        _nap(stop, 1.5, 1.8)
        shot = dev.screenshot()
        if rd_seen(shot, ["Select a Preset"], region=(0, 120, 540, 60)):
            rd_snap(shot, "mrally_stuck")
            dev.back()
            raise RuntimeError("the march did not leave (no troops?)")
        log(f"[+]  Monster rally: started on {label} - {mode} min, preset {preset}")
        return later(1, "rally started, looking for the next one")
    later(MR_POLL_MIN, "every coordinate tried was gone or taken")

RD_STEPS = {"gold_levy": run_gold_levy, "resource_tax": run_resource_tax, "labor": run_labor,
            "general_chest": run_general_chest, "vip_claim": run_vip_claim, "recruit_citizens": run_recruit_citizens,
            "civilization_card": run_civilization_card, "empire_depot": run_empire_depot, "lucky_raffle": run_lucky_raffle,
            "refining_card": run_refining_card, "alliance_gift": run_alliance_gift, "alliance_help": run_alliance_help, "alliance_treasure": run_alliance_treasure,
            "ranger_knight": run_ranger_knight, "train_troops": run_train_troops, "build_traps": run_build_traps,
            "troop_healing": run_troop_healing, "activity_chests": run_activity_chests, "gate_visitors": run_gate_visitors,
            "trade_carriage": run_trade_carriage, "offering_tributes": run_offering_tributes,
            "wisdom_dome_tokens": run_wisdom_dome_tokens, "royal_thief": run_royal_thief,
            "monster_rally": run_monster_rally}


# ------------------------------------------------------------ duties (ready-made, scheduled, tracked)
DUTY_FILE = USER / "duties_state.json"
_DUTY_LOCK = threading.Lock()
_DUTY_READ_LOCK = threading.Lock()
DUTY_MAX_TRIES = 3                      # failures in a game day before the alert + report (the duty never gives up)
DUTY_RETRY_MIN = 60                     # minutes between two tries of a duty that failed (rallies are joined meanwhile)
# group, id, title, what it does, schedule text, every_min (0 = once per game day), target, steps.
# verified=False means the steps are a first guess from the game's menu text and have not been watched on a real
# emulator yet - the interface shows them as "untested" until they are recorded.
DUTY_LIBRARY = [
    {"id": "login_gifts", "group": "Reward claims", "title": "Login gifts", "every_min": 0, "verified": False,
     "note": "Opens Login Gifts and claims what is free.",
     "target": "icon:Login Gifts", "steps": ["wait 1.5", "tap_text Claim|Collect|Free", "wait 1"]},
    {"id": "mail", "group": "Reward claims", "title": "Mail rewards", "every_min": 180, "verified": False,
     "note": "Opens Mail and taps Claim all.",
     "target": "icon:Mail", "steps": ["wait 1.5", "tap_text Claim All|Read All|Claim"]},
    {"id": "alliance_gift", "group": "Reward claims", "title": "Alliance gift", "every_min": 60, "verified": False,
     "note": "Alliance > Alliance Gift > Open All.",
     "target": "self:", "steps": ["alliance_gift"]},
    {"id": "alliance_help", "group": "Daily activities", "title": "Alliance help", "every_min": 60, "verified": False,
     "note": "Alliance > Help > Help all.",
     "target": "self:", "steps": ["alliance_help"]},
    {"id": "champions", "group": "Daily activities", "title": "Champions", "every_min": 0, "verified": False,
     "note": "Keep > Champions: 15 taps and 10 greetings a day on the champions you pick (only active ones). Needs the city map (Keep).",
     "target": "go:Greet Champion|Keep", "tap_dy": 0, "steps": ["wait 1.2", "champions"]},
    {"id": "wheel", "group": "Daily activities", "title": "Wheel of Fortune", "every_min": 0, "verified": False,
     "note": "Tavern > Wheel of Fortune: presses the Spin button you pick (once / x10 / x100) the number of times you set, or until the coins are used up. Needs the city map (Tavern).",
     "target": "go:Wheel of Fortune|Tavern", "tap_dy": 0, "no_tile": True, "go_page": "wheel", "steps": ["wait 1.5", "ring_name Tavern", "wheel"]},
    {"id": "crazy_eggs", "group": "Common tasks", "title": "Crazy Eggs", "every_min": 1, "verified": False,
     "note": "Smashes the ready eggs, closes the reward panel, comes back when each timer ends; Lucky Hammer once a day on the egg you pick.",
     "target": "tpl:event_center_icon|Center",
     "steps": ["wait 2", "crazy_eggs"]},
    {"id": "collect_resources", "group": "Common tasks", "title": "Collect resources", "every_min": 180, "verified": False,
     "note": "Taps every mapped farm / sawmill / quarry / mine. Needs the city mapper.",
     "target": "building:Farm", "count": 6, "steps": ["wait 1", "tap_text Collect|Claim"]},
    {"id": "reach_keep_tavern", "group": "Common tasks", "title": "Test: Keep, then Tavern", "every_min": 60, "verified": False,
     "note": "Walks to the Keep, opens its menu, then walks to the Tavern and opens its menu (nothing is pressed).",
     "target": "building:Keep+Tavern", "tap_dy": 0, "steps": ["wait 1.5", "ring_name"]},
    {"id": "market_capture", "group": "Common tasks", "title": "Test: Black Market pictures", "every_min": 60, "verified": False,
     "note": "Walks to the Market, opens the Black Market and saves a picture of every item on sale into the item library (nothing is bought or refreshed).",
     "target": "building:Market", "tap_dy": 0, "steps": ["wait 1.5", "ring_name Market", "market_capture"]},
    {"id": "tavern_catalog", "group": "Common tasks", "title": "General library (Tavern portraits)", "every_min": 1440, "verified": False,
     "note": "Tavern > Recruit > Portraits: reads every general (picture + name) and adds the new ones to the general library. Nothing is recruited.",
     "target": "building:Tavern", "tap_dy": 0, "steps": ["wait 1.5", "ring_name Tavern", "tavern_catalog"]},
    {"id": "reach_tavern", "group": "Common tasks", "title": "Test: go to Tavern", "every_min": 60, "verified": False,
     "note": "Only checks that the bot walks to the Tavern and opens its menu (nothing is pressed).",
     "target": "building:Tavern", "tap_dy": 0, "steps": ["wait 1.5", "ring_name Tavern"]},
    {"id": "reach_market", "group": "Common tasks", "title": "Test: go to Market", "every_min": 60, "verified": False,
     "note": "Only checks that the bot walks to the Market and opens its menu (nothing is pressed).",
     "target": "building:Market", "tap_dy": 0, "steps": ["wait 1.5", "ring_name Market"]},
    {"id": "alliance_science", "group": "Daily activities", "title": "Alliance Science", "verified": False,
     "note": "Alliance > Alliance Science: uses the day's free donations on the recommended science. Never spends gems.",
     "target": "self:", "every_min": 240, "steps": ["alliance_science"]},
    {"id": "black_market", "group": "Daily activities", "title": "Black Market", "every_min": 0, "verified": False,
     "note": "Market > Black Market: buys the goods on the Market tab, refreshes as set there, keeps the gem and gold floors. Finds the Market by trying the building plots once, then remembers it.",
     "target": "go:Black Market|Market", "tap_dy": 0, "steps": ["wait 1.5", "ring_name Market", "market"]},
    {"id": "view_reset_test", "group": "Common tasks", "title": "Test: city camera reset (globe)", "every_min": 1440, "verified": False,
     "note": "Moves the camera, goes to the world map and back three times and saves the city picture each time - checks the city always opens at the same place. Nothing is pressed in the city.",
     "target": "self:", "steps": ["view_reset_test"]},
    {"id": "reach_embassy", "group": "Common tasks", "title": "Test: go to Embassy", "every_min": 60, "verified": False,
     "note": "Only checks that the bot walks to the Embassy and opens its menu (nothing is pressed).",
     "target": "building:Embassy", "tap_dy": 0, "steps": ["wait 1.5", "ring_name Embassy"]},
    {"id": "map_city", "group": "Common tasks", "title": "Map my city", "every_min": 0, "verified": False,
     "note": "Explores the whole city once (about 5 minutes) and stitches its own map, so buildings are reached in seconds afterwards. Run it again after moving buildings. Nothing is pressed in the city.",
     "target": "self:", "steps": ["map_city"]},
    {"id": "test_learned_keep", "group": "Common tasks", "title": "Test: find the Keep without its tile", "every_min": 60, "verified": False,
     "note": "Test only: Gold Levy tile (gone once done) -> learned Keep picture -> Keep menu. Nothing else is pressed.",
     "target": "learned:Keep", "tap_dy": 0, "steps": ["wait 1.2", "ring_name Keep"]},
    {"id": "test_activity_go", "group": "Common tasks", "title": "Test: Activity Go (Greet Champion)", "every_min": 60, "verified": False,
     "note": "Test only: '...' > Activity > Greet Champion > Go, saves a picture at every step. Nothing else is pressed.",
     "target": "self:", "steps": ["activity_go Greet Champion"]},
    # recorded duties (tester flows 22 Oct 2026, project docs claude/flow_*.md). "opts" = the duty's own settings on the Duties page.
    {"id": "gold_levy", "group": "Daily activities", "title": "Gold Levy", "every_min": 0, "verified": False,
     "note": "Keep > Levy > Free Levy All. Gems Levy is never pressed. Needs the city map (Keep).",
     "target": "go:Gold Levy|Keep", "tap_dy": 0, "steps": ["wait 1.2", "gold_levy"]},
    {"id": "resource_tax", "group": "Daily activities", "title": "Resource Tax", "every_min": 0, "verified": False,
     "note": "Market > Tax: uses all free taxes on the resource you pick. Stops when the button shows a gem price. Needs the city map (Market).",
     "target": "go:Resource Tax|Market", "tap_dy": 0, "steps": ["wait 1.2", "resource_tax"],
     "opts": [{"k": "resource", "label": "Resource", "type": "select", "choices": ["Food", "Lumber", "Stone", "Ore"], "def": "Lumber"}]},
    {"id": "labor", "group": "Daily activities", "title": "Labor (Prison)", "every_min": 360, "verified": False,
     "note": "Prison > Captives > Labor: 4 hours of production from your captives, every 6 hours. Clear Labor CD is never pressed. Needs the city map (Prison).",
     "target": "go:Labor|Prison", "tap_dy": 0, "steps": ["wait 1.2", "labor"]},
    {"id": "general_chest", "group": "Reward claims", "title": "Great General Chest (free)", "every_min": 0, "verified": False,
     "note": "Tavern > Great General Chest: opens the free chest when it is ready. Needs the city map (Tavern).",
     "target": "via:Wheel of Fortune|Tavern", "tap_dy": 0, "steps": ["wait 1.2", "general_chest"]},
    {"id": "vip_claim", "group": "Reward claims", "title": "VIP daily chest", "every_min": 0, "verified": False,
     "note": "VIP badge (top left) > Claim. VIP packages are never bought.",
     "target": "self:", "steps": ["vip_claim"]},
    {"id": "recruit_citizens", "group": "Reward claims", "title": "Recruit Citizens (free)", "every_min": 0, "verified": False,
     "note": "Citizens (top bar) > Citizen List > Recruit Citizens > the free Recruit Once. Recruit x10 is never pressed.",
     "target": "self:", "steps": ["recruit_citizens"]},
    {"id": "civilization_card", "group": "Reward claims", "title": "Civilization card pack (free)", "every_min": 0, "verified": False,
     "note": "World Civilizations > the free card pack. Finds the building by its name on the city screen.",
     "target": "self:", "steps": ["civilization_card"]},
    {"id": "empire_depot", "group": "Reward claims", "title": "Empire Depot", "every_min": 0, "verified": False,
     "note": "Empire Depot icon (bottom left) > Empire Depot tab > the Claimable chest. Nothing is bought.",
     "target": "self:", "steps": ["empire_depot"]},
    {"id": "lucky_raffle", "group": "Reward claims", "title": "Lucky Raffle", "every_min": 0, "verified": False,
     "note": "Empire Depot icon > Lucky Raffle tab > the Claimable chest. The Enter buttons (gems / VIP) are never pressed.",
     "target": "self:", "steps": ["lucky_raffle"]},
    {"id": "refining_card", "group": "Reward claims", "title": "Refining Weekly Card", "every_min": 240, "verified": False,
     "note": "Empire Depot icon > Refining Weekly Card tab: claims every reward your Daily Activity has reached.",
     "target": "self:", "steps": ["refining_card"]},
    {"id": "alliance_treasure", "group": "Reward claims", "title": "Alliance Treasure", "every_min": 240, "verified": False,
     "note": "Alliance > Alliance Treasure > Open All (chests still on a timer stay closed).",
     "target": "self:", "steps": ["alliance_treasure"]},
    {"id": "ranger_knight", "group": "Daily activities", "title": "Ranger Knight Order", "every_min": 70, "verified": False,
     "note": "Event Center > Limited > Ranger Knight Order > Quick Dispatch > Send. Refresh is never pressed.",
     "target": "tpl:event_center_icon|Center", "steps": ["wait 2", "ranger_knight"]},
    {"id": "troop_healing", "group": "Daily activities", "title": "Heal troops", "every_min": 120, "verified": False,
     "note": "Hospital > Heal > Heal (costs resources). Instant Heal (gems) is never pressed. Needs the city map (Hospital).",
     "target": "go:Troop Healing|Hospital", "tap_dy": 0, "steps": ["wait 1.2", "troop_healing"],
     "opts": [{"k": "speedups", "label": "Finish with speed-up items", "type": "bool", "def": False}]},
    {"id": "train_troops", "group": "Daily activities", "title": "Train troops", "every_min": 240, "verified": False,
     "note": "Barracks > Train: trains the amount you set of the troop the page has selected. Instant Train (gems) is never pressed. Needs the city map (Barracks).",
     "target": "go:Troop Training|Barracks", "tap_dy": 0, "steps": ["wait 1.2", "train_troops"],
     "opts": [{"k": "amount", "label": "Troops per run", "type": "number", "min": 1, "max": 9999999, "def": 500},
              {"k": "speedups", "label": "Finish with speed-up items", "type": "bool", "def": False}]},
    {"id": "build_traps", "group": "Daily activities", "title": "Build traps", "every_min": 240, "verified": False,
     "note": "Trap Factory > Build: builds the amount you set of the trap the page has selected. Instant Train (gems) is never pressed. Needs the city map (Trap Factory).",
     "target": "go:Trap Building|Trap Factory", "tap_dy": 0, "steps": ["wait 1.2", "build_traps"],
     "opts": [{"k": "amount", "label": "Traps per run", "type": "number", "min": 1, "max": 9999999, "def": 500},
              {"k": "speedups", "label": "Finish with speed-up items", "type": "bool", "def": False}]},
    {"id": "gate_visitors", "group": "Reward claims", "title": "Gate visitors", "every_min": 120, "verified": False,
     "note": "While 'A visitor is waiting at the city gate' shows: opens each visitor and claims the gift.",
     "target": "self:", "steps": ["gate_visitors"]},
    {"id": "trade_carriage", "group": "Daily activities", "title": "Trade Carriage", "every_min": 0, "verified": False,
     "note": "Trade Carriage: sends all 4 daily transports with the first carriage offered and the march preset already shown. Never refreshes goods.",
     "target": "self:", "steps": ["trade_carriage"]},
    {"id": "offering_tributes", "group": "Daily activities", "title": "Shrine offering", "every_min": 0, "verified": False,
     "note": "Shrine > Offer > Offer Tribute at the most allowed (50), using Tribute items. When no tributes are left it can offer with gems instead (your choice below, off by default). Needs the city map (Shrine).",
     "target": "go:Offering|Shrine", "tap_dy": 0, "steps": ["wait 1.2", "offering_tributes"],
     "opts": [{"k": "times", "label": "Tribute offers per day", "type": "number", "min": 1, "max": 20, "def": 1},
              {"k": "gems", "label": "No tributes left: offer with gems", "type": "bool", "def": False},
              {"k": "gem_count", "label": "Gem offers (25 gems first, then more)", "type": "number", "min": 1, "max": 50, "def": 3}]},
    {"id": "wisdom_dome_tokens", "group": "Daily activities", "title": "Wisdom Dome tokens (gems)", "every_min": 0, "verified": False, "gems": True,
     "note": "SPENDS GEMS: Wisdom Dome > Rare Books > buys Goddess Honor Tokens for 500 gems each, as many as you set (game limit 5 a day). Needs the city map (Wisdom Dome).",
     "target": "building:Wisdom Dome", "tap_dy": 0, "steps": ["wait 1.2", "wisdom_dome_tokens"],
     "opts": [{"k": "times", "label": "Tokens to buy per day (500 gems each)", "type": "number", "min": 1, "max": 5, "def": 5}]},
    {"id": "activity_chests", "group": "Reward claims", "title": "Activity chests", "every_min": 180, "verified": False,
     "note": "Tavern > Activity: opens every ready task chest and milestone chest. Runs every 3 hours so the later chests are caught too. Needs the city map (Tavern).",
     "target": "via:Wheel of Fortune|Tavern", "tap_dy": 0, "steps": ["wait 1.2", "activity_chests"]},
    {"id": "royal_thief", "group": "Daily activities", "title": "Royal Thief", "every_min": 15, "verified": False,
     "note": "When an alliance member invites you (Invite icon): opens the invite, picks a thief that is not killed and attacks it. Keeps trying until it is killed; when all marches are out it waits for one to come back.",
     "target": "self:", "steps": ["royal_thief"],
     "opts": [{"k": "troops", "label": "Troops", "type": "select", "def": "Power First",
               "choices": ["Power First"] + ["Preset " + n for n in ("I", "II", "III", "IV", "V", "VI", "VII", "VIII")]}]},
    {"id": "monster_rally", "group": "Daily activities", "title": "Start monster rallies", "every_min": 5, "verified": False,
     "note": "Takes monster coordinates from the Telegram section, flies there with the map search and starts an Alliance War rally with the preset set for that monster. "
             "No free march: waits on the monster. Skips a monster that is gone or that a march is already going to.",
     "target": "self:", "steps": ["monster_rally"],
     "opts": [{"k": "rules", "label": "Monsters", "type": "rules", "def": None},
              {"k": "max_wait", "label": "Give up on a monster after (min, 0 = wait until it is rallied)", "type": "number", "min": 0, "max": 120, "def": 0},
              {"k": "keep_free", "label": "Marches to keep free for joining", "type": "number", "min": 0, "max": 5, "def": 0},
              {"k": "keep_stamina", "label": "Keep at least this much stamina", "type": "number", "min": 0, "max": 9999, "def": 100},
              {"k": "max_age", "label": "Ignore coordinates older than (min)", "type": "number", "min": 1, "max": 600, "def": 30},
              {"k": "skip_busy", "label": "Skip a monster a march is already going to", "type": "bool", "def": True}]},
]
DUTY_BY_ID = {d["id"]: d for d in DUTY_LIBRARY}


def _game_day():
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() - reset_min() * 60))     # the game day changes at Evony's reset (midnight US Pacific) unless a time is set


_DUTY_CACHE = [None]                                     # duties_state.json kept in memory: read from disk once, written on change


def load_duty_state():
    """A private copy of the duty state (callers may change it; writers then pass it to save_duty_state)."""
    with _DUTY_READ_LOCK:
        if _DUTY_CACHE[0] is None:
            try:
                _DUTY_CACHE[0] = json.loads(DUTY_FILE.read_text(encoding="utf-8"))
            except Exception:
                _DUTY_CACHE[0] = {}
        return json.loads(json.dumps(_DUTY_CACHE[0]))


def save_duty_state(data):
    """Keep 'data' as the new duty state and write it to disk (atomic). Call with _DUTY_LOCK held."""
    with _DUTY_READ_LOCK:
        _DUTY_CACHE[0] = json.loads(json.dumps(data))
    try:
        save_json(DUTY_FILE, data, indent=1)
    except OSError as _e:
        quiet_err("save_duty_state", _e)


def duty_record(serial, duty_id, ok, msg="", nothing=False):
    with _DUTY_LOCK:
        data = load_duty_state()
        row = data.setdefault(serial or "default", {}).setdefault(duty_id, {})
        day = _game_day()
        if row.get("day") != day:
            row["day"], row["tries"] = day, 0
        row["tries"] = int(row.get("tries", 0)) + 1
        row["last_try"] = time.time()
        row["msg"] = msg
        hist = row.setdefault("hist", {})                        # per game day: [done, failed] - kept 7 days (task success stats)
        hd = hist.setdefault(str(day), [0, 0])
        hd[0 if ok else 1] += 1
        for k in sorted(hist)[:-7]:
            hist.pop(k, None)
        if not ok:
            row["last_error"], row["last_error_at"] = msg, time.time()
        if ok:
            row["last_ok"], row["status"] = time.time(), "done"
            row["tries"] = 0                                     # fulfilled: failures start counting again from 0
            if nothing:                                          # the game had nothing left: once more to be sure, then done until the reset
                row["empty"] = int(row.get("empty", 0)) + 1 if row.get("empty_day") == day else 1
                row["empty_day"] = day
                if row["empty"] >= 2:
                    row["done_day"] = day
            else:
                row["empty"] = 0
        else:
            row["status"] = "retry"
            if row["tries"] == DUTY_MAX_TRIES:                   # the 3rd failure today: alert + report once (it keeps trying every hour)
                row["status"] = "gaveup"
        save_duty_state(data)
        return row.get("status")


def duty_retry(serial, duty_id):
    """'Retry' after a duty failed for the day: clears today's tries so it runs again at once."""
    with _DUTY_LOCK:
        data = load_duty_state()
        row = data.setdefault(serial or "default", {}).setdefault(duty_id, {})
        row["tries"], row["status"], row["next_at"] = 0, "retry", 0
        row["last_try"] = 0
        save_duty_state(data)


def run_now_list(serial):
    """Tasks the user asked to run first on this emulator ('Run a task now' on the Duties page)."""
    return [str(x) for x in ((load_duty_state().get("_run_now") or {}).get(serial or "default") or [])]


def run_now_set(serial, ids):
    with _DUTY_LOCK:
        data = load_duty_state()
        rn = data.setdefault("_run_now", {})
        if ids:
            rn[serial or "default"] = list(dict.fromkeys(ids))
        else:
            rn.pop(serial or "default", None)
        save_duty_state(data)


def run_now_done(serial, tid):
    run_now_set(serial, [x for x in run_now_list(serial) if x != tid])


def tasks_active(cfg, serial):
    if cfg.get("city_enabled") or run_now_list(serial):
        return True
    return any((v or {}).get("on") for v in (cfg.get("duties") or {}).values())   # a duty that is switched on runs by itself, tasks on or off


def duty_status(serial, duty_id, cfg):
    """-> (status, text): status is off | waiting | due | done | retry | gaveup (failed 3+ times today, still retried hourly)."""
    conf = (cfg.get("duties") or {}).get(duty_id) or {}
    d = DUTY_BY_ID.get(duty_id)
    if not d or not conf.get("on"):
        return "off", "Off"
    row = (load_duty_state().get(serial or "default") or {}).get(duty_id) or {}
    day, now = _game_day(), time.time()
    tries = int(row.get("tries", 0)) if row.get("day") == day else 0
    if duty_id == "black_market" and bm_keep_going(cfg):     # owner 24 Oct: runs all day, never 'done' - the gem floor only pauses it
        fs = _duty_fail_state(row, tries, now)               # (checked before done_day: the Activity tile says nothing about the gems)
        if fs:
            return fs
        sp = bm_spend_text(row)
        if row.get("wait_gems") and row.get("next_at", 0) > now:
            g = row.get("end_gems")
            return "waiting", "Waiting for gems" + (f" (read {g:,})" if isinstance(g, int) else "") + " - next look " + \
                time.strftime("%H:%M", time.localtime(row["next_at"])) + (" · " + sp if sp else "")
        if row.get("next_at", 0) > now:
            return "waiting", "Next " + time.strftime("%H:%M", time.localtime(row["next_at"])) + (" · " + sp if sp else "")
        return "due", "Due" + (" · " + sp if sp else "")
    if row.get("done_day") == day:                          # checked twice: nothing left in the game until the reset
        return "done", "Done for today"
    if duty_id in ("crazy_eggs", "royal_thief", "monster_rally"):           # timer driven: comes back when the next egg is ready
        if row.get("all_done_day") == day:
            return "done", "All eggs done"
        fs = _duty_fail_state(row, tries, now)
        if fs:
            return fs
        if row.get("next_at", 0) > now:
            return "waiting", "Next " + time.strftime("%H:%M", time.localtime(row["next_at"]))
        return "due", "Due"
    every = int(conf.get("every_min", d["every_min"]) or 0)
    last_ok = row.get("last_ok", 0)
    if every == 0:                                          # once per game day
        if last_ok and time.strftime("%Y-%m-%d", time.gmtime(last_ok)) == day:
            return "done", "Done " + time.strftime("%H:%M", time.localtime(last_ok))
    elif last_ok and now - last_ok < every * 60:
        return "done", "Done " + time.strftime("%H:%M", time.localtime(last_ok))
    fs = _duty_fail_state(row, tries, now)
    if fs:
        return fs
    return "due", "Due"


def _duty_fail_state(row, tries, now):
    """A duty that failed: tried again every DUTY_RETRY_MIN minutes, all day (rallies are joined in between).
    From the 3rd failure the status is 'gaveup' (red, with a Retry button) - it still keeps trying."""
    if not tries or row.get("status") == "done" or now - row.get("last_try", 0) >= DUTY_RETRY_MIN * 60:
        return None
    nxt = time.strftime("%H:%M", time.localtime(row.get("last_try", 0) + DUTY_RETRY_MIN * 60))
    if tries >= DUTY_MAX_TRIES:
        return "gaveup", f"Failed {tries} times - next try {nxt}"
    return "retry", "Retrying " + nxt


def run_now_tasks(cfg, serial):
    out = []
    for tid in run_now_list(serial):
        if tid.startswith("duty:") and tid[5:] in DUTY_BY_ID:
            d = DUTY_BY_ID[tid[5:]]
            t = {k: d[k] for k in ("target", "steps", "no_tile", "go_page") if k in d}
            t["name"], t["count"] = tid, d.get("count", 1)
            if tid == "duty:black_market" and bm_keep_going(cfg):
                t["no_tile"] = True
            out.append(t)
        elif tid.startswith("task:"):
            for c in cfg.get("city_tasks") or []:
                if c.get("name", "") == tid[5:]:
                    out.append(dict(c))
                    break
            else:
                run_now_done(serial, tid)
        else:
            run_now_done(serial, tid)
    return out


def duty_tasks_due(cfg, serial):
    out = []
    for d in DUTY_LIBRARY:
        if duty_status(serial, d["id"], cfg)[0] in ("due",):
            t = {k: d[k] for k in ("target", "steps", "no_tile", "go_page") if k in d}
            t["name"] = "duty:" + d["id"]
            t["count"] = d.get("count", 1)
            if d["id"] == "black_market" and bm_keep_going(cfg):
                t["no_tile"] = True                          # straight to the Market building, not the Activity tile (gone once the daily is done)
            out.append(t)
    return out


def city_tasks_due(cfg, last_run, serial=""):
    now = time.time()
    first = run_now_tasks(cfg, serial)
    if first:                                                    # asked for by the user: these go before everything else
        return first
    if not cfg.get("city_enabled"):
        return duty_tasks_due(cfg, serial)                       # duties do not need the task switch
    out = []
    for t in cfg.get("city_tasks") or []:
        if not t.get("enabled", True):
            continue
        if any(str(x).strip() == "market" for x in t.get("steps") or []):
            continue                                             # old 'Black Market' city task: the Black Market duty replaced it
        if str(t.get("at") or "").strip():                       # fixed times of the day (PC clock)
            lt = time.localtime(now)
            for hm in re.split(r"[ ,;]+", str(t["at"]).strip()):
                m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", hm)
                if not m:
                    continue
                slot = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, int(m.group(1)), int(m.group(2)), 0, 0, 0, -1))
                if slot <= now < slot + 3600 and last_run.get(t.get("name", ""), 0) < slot:
                    out.append(t)
                    break
            continue
        every = float(t.get("every_min", 60) or 60) * 60
        if now - last_run.get(t.get("name", ""), 0) >= every:
            out.append(t)
    return out + duty_tasks_due(cfg, serial)


CITY_MAP_HELP = "The city map for this emulator is missing. Open the City page and run the City Mapper first - every task that uses a building (Black Market, Tavern, troops, resources...) needs it."


# Buildings that sit at the same place in every city. The pixel position on the map drifts a little between runs
# (and between accounts), so it is only a first guess: calibrate_keep() finds the Tavern (a fixed building) and goes
# from there to the Keep, then saves the real Keep position into this emulator's city map once, for good.
FIXED_BUILDINGS = {"Keep#1": [1141, 609]}
ANCHOR_TO_KEEP = {"Tavern": (90, -135), "Shrine": (313, -139)}   # Keep label minus anchor label (emulator pixels; measured on Kiravik's city picture and cross-checked on the PC client view)
# Fixed buildings: label position minus the Shrine label (emulator pixels). The Shrine, Tavern and Keep never move, so any one of them gives the rest.
FIXED_OFFSETS_FROM_SHRINE = {"Victory Column": [-681, -363], "Council of State": [74, -439], "Ideal Land": [324, -449], "Dragon Cliff": [651, -447], "Arsenal": [-91, -352], "Triumphal Arch": [219, -357], "Academy": [65, -272], "Military Academy": [492, -267], "Research Factory": [-451, -182], "Barracks": [-205, -178], "Wisdom Dome": [681, -159], "Warehouse": [-19, -133], "Keep": [314, -139], "Art Hall": [-628, -101], "Prison": [-381, -88], "Holy Palace": [528, -73], "Tavern": [223, -3], "Trap Factory": [-203, -6], "Shrine": [0, 0], "Archer Camp": [657, 27], "Pasture": [-808, -10], "Stables": [-577, 51], "Market": [-425, 132], "Workshop": [89, 141], "War Hall": [405, 126], "Embassy": [231, 211], "Hospital": [-72, 311], "Rally Spot": [644, 234], "Walls": [-406, 291], "Battlefield": [-701, 357], "Bounty Cave": [-471, 455]}
# Squares the bot does not need for now (owner, 20 Oct): Bounty Cave, Wonder, Watchtower, Archer Tower 1 and 2. Parked for a future build; ask for them with numbers (square:N).
PARKED_SQUARES = {31, 32, 34, 35, 36}
UNIQUE_BUILDINGS = {k.lower() for k in FIXED_OFFSETS_FROM_SHRINE}                # one of each in the city: a second read of the same name means the camera was somewhere else

# Numbered squares of the default city map (city_map/city_map_default.png): number -> [name, centre of the square minus the Shrine label, emulator pixels]. 1 = Keep.
CITY_SQUARES = {int(k): v for k, v in {"1": ["Keep", 319, -218], "2": ["Tavern", 232, -27], "3": ["Shrine", 30, -29], "4": ["Victory Column", -684, -334], "5": ["Council of State", 73, -452], "6": ["Ideal Land", 291, -487], "7": ["Dragon Cliff", 704, -430], "8": ["Arsenal", -85, -379], "9": ["Triumphal Arch", 222, -388], "10": ["Academy", 65, -302], "11": ["Military Academy", 502, -280], "12": ["Research Factory", -443, -204], "13": ["Barracks", -206, -199], "14": ["Wisdom Dome", 682, -177], "15": ["Warehouse", -21, -172], "16": ["Art Hall", -655, -123], "17": ["Prison", -378, -118], "18": ["Holy Palace", 515, -103], "19": ["Trap Factory", -210, -27], "20": ["Archer Camp", 658, 13], "21": ["Pasture", -810, -29], "22": ["Stables", -581, 52], "23": ["Market", -432, 116], "24": ["Workshop", 80, 111], "25": ["War Hall", 424, 103], "26": ["Embassy", 240, 190], "27": ["Hospital", -68, 280], "28": ["Rally Spot", 653, 221], "29": ["Walls", -473, 290], "30": ["Battlefield", -704, 359], "31": ["Bounty Cave", -473, 475], "32": ["Wonder", -999, -208], "33": ["Subordinate City", -1058, -523], "34": ["Watchtower", -1160, -88], "35": ["Archer Tower 1", -1085, 673], "36": ["Archer Tower 2", -990, 731], "37": ["Army Camp", 535, 421]}.items()}

CALIB_STOPS = [(1141, 609), (1312, 588), (1000, 800), (1400, 800), (700, 650), (1000, 450), (1400, 450), (600, 900), (1450, 700)]


DEFAULT_PICTURE_BUILDINGS = {"keep", "tavern", "shrine", "victory column", "council of state", "ideal land", "dragon cliff", "triumphal arch", "academy", "military academy", "research factory", "wisdom dome", "art hall", "holy palace", "pasture", "rally spot", "walls", "battlefield", "subordinate city"}   # the owner's list of fixed buildings (20 Oct): same place in every city, used without any mapping. Not fixed: Arsenal, Barracks, Warehouse, Prison, Trap Factory, Archer Camp, Stables, Market, Workshop, War Hall, Embassy, Hospital, Bounty Cave.


def picture_items(dev):
    """Building positions in the whole-city picture frame: what the square mapper saved for this emulator, and for every
    building it did not save the built-in default (the numbered squares). -> {"Keep#1": [x, y], ...} or None when the
    city picture is not available."""
    if city_picture() is None:
        return None
    ent = load_city().get(getattr(dev, "serial", "default")) or {}
    items = {}
    if ent.get("frame") == "picture":
        items.update({k: list(v) for k, v in ent.get("items", {}).items()})
    have = {k.split("#")[0].lower() for k in items}
    for n, (nm, ox, oy) in CITY_SQUARES.items():
        if n in PARKED_SQUARES:
            continue
        base = re.sub(r"\s*\d+$", "", nm)
        if base.lower() in have or base.lower() not in DEFAULT_PICTURE_BUILDINGS:
            continue
        items[f"{base}#1"] = [round(SHRINE_LABEL_IN_PICTURE[0] + ox), round(SHRINE_LABEL_IN_PICTURE[1] + oy)]
    for nm, n in (ent.get("plots") or {}).items():         # changeable buildings found by trying the plots (find_plot)
        if nm.lower() not in have and int(n) in CITY_SQUARES:
            _nm, ox, oy = CITY_SQUARES[int(n)]
            items[f"{nm}#1"] = [round(SHRINE_LABEL_IN_PICTURE[0] + ox), round(SHRINE_LABEL_IN_PICTURE[1] + oy)]
    return items


def fill_from_squares(ent):
    """Map by hand: the buildings placed by hand pin where the numbered squares sit, so every other fixed building is
    filled in from the squares. Hand-placed buildings always win; filled ones are redone on every pick."""
    items, manual = ent.setdefault("items", {}), set(ent.get("manual", []))
    for k in ent.get("auto", []):
        if k not in manual:
            items.pop(k, None)
    sq = {}
    for n, (nm, ox, oy) in CITY_SQUARES.items():
        base = re.sub(r"\s*\d+$", "", nm)
        if n not in PARKED_SQUARES and base.lower() in DEFAULT_PICTURE_BUILDINGS:
            sq.setdefault(base.lower(), (base, ox, oy))
    pins = [(items[k][0] - sq[k.split("#")[0].lower()][1], items[k][1] - sq[k.split("#")[0].lower()][2])
            for k in manual if k in items and k.split("#")[0].lower() in sq]
    if not pins:
        ent["auto"] = []
        return 0
    ax, ay = sum(p[0] for p in pins) / len(pins), sum(p[1] for p in pins) / len(pins)
    have = {k.split("#")[0].lower() for k in items}
    auto = []
    for low, (base, ox, oy) in sq.items():
        if low not in have:
            items[f"{base}#1"] = [round(ax + ox), round(ay + oy)]
            auto.append(f"{base}#1")
    ent["auto"] = auto
    return len(auto)


CHANGEABLE_PLOTS = [n for n, (nm, _x, _y) in CITY_SQUARES.items()
                    if n not in PARKED_SQUARES and re.sub(r"\s*\d+$", "", nm).lower() not in DEFAULT_PICTURE_BUILDINGS]


def save_plot(dev, name, number):
    serial = getattr(dev, "serial", "default")
    with _CITY_LOCK:
        data = load_city()
        ent = data.setdefault(serial, {})
        ent.setdefault("plots", {})[name] = int(number)
        save_city(data)


def forget_plot(dev, name):
    serial = getattr(dev, "serial", "default")
    with _CITY_LOCK:
        data = load_city()
        if (data.get(serial) or {}).get("plots", {}).pop(name, None) is not None:
            save_city(data)


def find_plot(dev, cfg, log, stop, nav, name):
    """A changeable building (Market, Embassy ...) has no known place on this account: walk the changeable plots of the
    numbered city map, tap each, read the name under the ring menu and save the plot that holds `name` (other changeable
    buildings read on the way are saved too). Only on a city that matches the stored picture. -> plot number or None."""
    x0, y0, w, h = SAFE_TAP_AREA
    want = BUILDING_ALIASES.get(name, name).lower()
    order = sorted(CHANGEABLE_PLOTS, key=lambda n: (re.sub(r"\s*\d+$", "", CITY_SQUARES[n][0]).lower() != want, n))
    known = {k.lower() for k in ((load_city().get(getattr(dev, "serial", "default")) or {}).get("plots") or {})}
    log(f"[*] {name} is not on this account's map yet - trying the building plots ({len(order)} plots)")
    first = True
    for n in order:
        if stop.is_set():
            return None
        nm0, ox, oy = CITY_SQUARES[n]
        tx, ty = SHRINE_LABEL_IN_PICTURE[0] + ox, SHRINE_LABEL_IN_PICTURE[1] + oy
        nav.seen_city = False
        leave_to_city(dev, cfg, stop)
        nav.goto(tx, ty)
        if first and not nav.seen_city:
            log(f"[i] {name}: this city does not match the stored city picture - counting the plots from the Shrine instead")
            return "nomatch"
        first = False
        px, py = tx - nav.cx, ty - nav.cy
        if not (x0 <= px <= x0 + w and y0 <= py <= y0 + h):
            nav.goto(tx, ty)                               # a long move can fall short: one more, shorter move
            px, py = tx - nav.cx, ty - nav.cy
        if not (x0 <= px <= x0 + w and y0 <= py <= y0 + h):
            log(f"[i]   plot {n}: could not be brought into view")
            continue
        close_reward_panel(dev, stop, log)
        dev.expect_city = False
        dev.tap(int(px), int(py + int(cfg.get("city_tap_dy", 22) or 0)))
        _nap(stop, 1.1, 1.5)
        shot = dev.screenshot()
        if not find_detail_icon(shot):                     # missed the building: once more, a little higher
            dev.tap(int(px), int(py) - 25)
            _nap(stop, 1.1, 1.5)
            shot = dev.screenshot()
        if not find_detail_icon(shot):
            log(f"[i]   plot {n}: no building menu opened")
            leave_to_city(dev, cfg, stop)
            continue
        got = ring_building_name(shot)
        read = (got[0] if got else "") or ""
        log(f"[i]   plot {n}: {read or 'name not read'}")
        close_ring(dev, stop, (px, py + 35))
        for b in [fix_building_name(x) for x in read.split(",") if x.strip()]:
            bn = BUILDING_ALIASES.get(b, b)
            if bn.lower() == want:
                save_plot(dev, name, n)
                log(f"[+] {name} found on plot {n} - saved for this account")
                return n
            if bn.lower() not in DEFAULT_PICTURE_BUILDINGS and bn.lower() not in known and bn in BUILDING_NAMES and bn.lower() not in RESOURCE_LOWER:
                save_plot(dev, bn, n)                     # another changeable building: remember it too
                known.add(bn.lower())
    log(f"[!] {name} was not found on any plot")
    return None


SQUARE_BASES = {}
for _n, (_nm, _x, _y) in CITY_SQUARES.items():
    SQUARE_BASES.setdefault(re.sub(r"\s*\d+$", "", _nm).lower(), _n)


def lock_shrine(dev, cfg, log, stop, nav_state):
    """THE first step of every building visit, in every city style: find the Shrine (from the name of any fixed building),
    go there and lock the camera on it by reading the fixed names around it. -> (nav, (shrine map x, y)) or (None, None)."""
    if nav_state.get("shrine") and nav_state.get("snav"):
        return nav_state["snav"], nav_state["shrine"]
    nav = CityNav(dev, stop, log)
    nav_state["snav"] = nav
    log("[*] Locking the camera on the Shrine")
    shrine = None
    nav.cx = nav.cy = 0.0                                  # first look where the city view already is (it opens near the Keep): no corner drag needed
    for i, (dx, dy) in enumerate([(0, 0), (0, 300), (300, 0), (0, -300), (0, -300), (-300, 0), (-300, 0), (0, 300), (0, 300)]):
        if stop.is_set():
            return None, None
        if i:
            nav.pan(dx, dy)
        labels, _, _ = look_with_names(dev, stop, nav)
        log(f"[i]   Shrine search {i + 1}/9: names read: {', '.join(n for n, _, _ in labels) or 'none'}")
        for n, mx, my in labels:
            off = FIXED_OFFSETS_FROM_SHRINE.get(BUILDING_ALIASES.get(n, n))
            if off:
                shrine = (mx - off[0], my - off[1])
                break
        if shrine:
            break
    if not shrine:
        log("[i]   no fixed building near the start - searching from the city corner")
        shrine = locate_shrine(dev, stop, log, nav)
    if not shrine:
        log("[!] The Shrine could not be found - no fixed building name was read")
        return None, None
    nav.goto(*shrine)
    labels, _, _ = look_with_names(dev, stop, nav)
    est = [(mx - off[0], my - off[1]) for n, mx, my in labels
           for off in [FIXED_OFFSETS_FROM_SHRINE.get(BUILDING_ALIASES.get(n, n))] if off]
    if est:
        shrine = (sum(e[0] for e in est) / len(est), sum(e[1] for e in est) / len(est))
    log(f"[+] Camera locked on the Shrine ({len(est)} fixed name(s) in view: {', '.join(n for n, _, _ in labels) or 'none'})")
    nav_state["shrine"] = shrine
    return nav, shrine


def shrine_plot_task(dev, cfg, log, stop, task, nav_state, base):
    """Another city style (the stored picture does not fit): find the Shrine by reading the name of any fixed building,
    then count from it to each changeable plot, tap it and read the name under the ring menu. The plot holding `base` is
    saved for this account and the task's steps run on its open menu. -> None when done, False when not found."""
    nav, shrine = lock_shrine(dev, cfg, log, stop, nav_state)
    if not shrine:
        return "noshrine"
    want = BUILDING_ALIASES.get(base, base).lower()
    saved = ((load_city().get(getattr(dev, "serial", "default")) or {}).get("plots") or {}).get(base)
    if base.lower() in DEFAULT_PICTURE_BUILDINGS and base.lower() in SQUARE_BASES:
        order = [SQUARE_BASES[base.lower()]]              # a fixed building: its plot is the same in every city
    else:
        order = sorted(CHANGEABLE_PLOTS, key=lambda n: (re.sub(r"\s*\d+$", "", CITY_SQUARES[n][0]).lower() != want, n))
        if saved in order:
            order.remove(saved)
            order.insert(0, saved)
    log(f"[*] Going to {base}" + ("" if len(order) == 1 else f" (trying {len(order)} plots)"))
    x0, y0, w, h = SAFE_TAP_AREA
    for n in order:
        if stop.is_set():
            return False
        nm0, ox, oy = CITY_SQUARES[n]
        tx, ty = shrine[0] + ox, shrine[1] + oy
        leave_to_city(dev, cfg, stop)
        dev.expect_city = True
        nav.goto(tx, ty)
        px, py = tx - nav.cx, ty - nav.cy
        if not (x0 <= px <= x0 + w and y0 <= py <= y0 + h):
            nav.goto(tx, ty)                               # a long move can fall short: one more, shorter move
            px, py = tx - nav.cx, ty - nav.cy
        if not (x0 <= px <= x0 + w and y0 <= py <= y0 + h):
            log(f"[i]   plot {n}: could not be brought into view")
            continue
        close_reward_panel(dev, stop, log)
        dev.expect_city = False
        dev.tap(int(px), int(py))
        _nap(stop, 1.1, 1.5)
        shot = dev.screenshot()
        if not find_detail_icon(shot):                     # missed the building: once more, a little higher
            dev.tap(int(px), int(py) - 25)
            _nap(stop, 1.1, 1.5)
            shot = dev.screenshot()
        if not find_detail_icon(shot):
            log(f"[i]   plot {n}: no building menu opened")
            leave_to_city(dev, cfg, stop)
            continue
        got = ring_building_name(shot)
        read = (got[0] if got else "") or ""
        log(f"[i]   plot {n}: {read or 'name not read'}")
        lab = ring_label(shot, nm0, (px, py))
        if lab:                                            # the label shows where the plot really is: correct the camera drift
            nav.cx, nav.cy = tx - lab[1], ty - (lab[2] - 35)
        names = [BUILDING_ALIASES.get(fix_building_name(b), fix_building_name(b)).lower() for b in read.split(",") if b.strip()]
        if want in names:
            if saved != n:
                save_plot(dev, base, n)
                log(f"[+] {base} found on plot {n} - saved for this account")
            for st in task.get("steps", []):
                run_step(dev, cfg, log, stop, st)
            leave_to_city(dev, cfg, stop, log)
            if find_detail_icon(dev.screenshot()):
                close_ring(dev, stop, (px, py + 35))
            return None
        close_ring(dev, stop, (px, py + 35))
    log(f"[!] {base} was not found on any plot")
    return False


RESOURCE_LOWER = {"quarry", "sawmill", "mine", "farm"}
PLOT_NOMATCH = {}                                          # serial -> True: the city picture does not fit, changeable buildings are found by their names


def own_city_map(dev):
    """True when the City Mapper saved buildings for this emulator (the built-in Keep guess does not count): without one,
    a city that does not fit the stored picture is walked by reading the building names."""
    return bool((load_city().get(getattr(dev, "serial", "default")) or {}).get("items"))


def city_map_items(dev):
    items = dict((load_city().get(getattr(dev, "serial", "default")) or {}).get("items", {}))
    for k, v in FIXED_BUILDINGS.items():
        items.setdefault(k, list(v))
    return items


def calibrate_keep(dev, stop, log, nav):
    """The Keep is not where the map says: sweep the city for the Keep or a fixed neighbour (Tavern), walk from it to
    the Keep, confirm the Keep's name label there and save its position. -> (screen x, screen y) or None."""
    serial = dev.serial if hasattr(dev, "serial") else "default"
    log("[*] Keep: calibrating the city map from the Tavern (one time)")
    nav.to_origin()
    for sx, sy in CALIB_STOPS:
        if stop.is_set():
            return None
        nav.goto(sx, sy)
        labels, _, _ = look_with_names(dev, stop, nav)
        seen = ", ".join(f"{n}({mx},{my})" for n, mx, my in labels)
        log(f"[i] Keep calibration: stop {sx},{sy} camera {round(nav.cx)},{round(nav.cy)} - names read: {seen or 'none'}")
        est = None
        for n, mx, my in labels:
            if n == "Keep":
                est = (mx, my)
                break
        if est is None:
            for n, mx, my in labels:
                if n in ANCHOR_TO_KEEP:
                    est = (mx + ANCHOR_TO_KEEP[n][0], my + ANCHOR_TO_KEEP[n][1])
                    break
        if est is None:
            continue
        nav.goto(*est)
        labels, _, _ = look_with_names(dev, stop, nav)
        keeps = [(mx, my) for n, mx, my in labels if n == "Keep" and abs(mx - est[0]) < 150 and abs(my - est[1]) < 150]
        if not keeps:
            log("[i] Keep calibration: no Keep label at the expected place yet - looking on")
            continue
        mx, my = min(keeps, key=lambda c: (c[0] - est[0]) ** 2 + (c[1] - est[1]) ** 2)
        data = load_city()
        data.setdefault(serial, {}).setdefault("items", {})["Keep#1"] = [round(mx), round(my)]
        save_city(data)
        log(f"[+] Keep found and saved on the city map at {round(mx)},{round(my)}")
        return mx - nav.cx, my - nav.cy
    log("[!] Keep calibration: could not find the Keep or the Tavern")
    return None


def locate_shrine(dev, stop, log, nav):
    """Find the Shrine's place on the city map by reading the name label of ANY fixed building (they all sit at a fixed
    distance from the Shrine). -> (map x, map y) of the Shrine label, or None."""
    nav.to_origin()
    for sx, sy in CALIB_STOPS:
        if stop.is_set():
            return None
        nav.goto(sx, sy)
        labels, _, _ = look_with_names(dev, stop, nav)
        for n, mx, my in labels:
            off = FIXED_OFFSETS_FROM_SHRINE.get(BUILDING_ALIASES.get(n, n))
            if off:
                log(f"[i] Squares: anchor '{n}' read at {mx},{my}")
                return mx - off[0], my - off[1]
    log("[!] Squares: no fixed building name could be read - cannot place the squares")
    return None


_DETAIL_TPL = {}
# The yellow "i" (Detail) icon of the building ring menu, cut from Kiravik (48x48 px, emulator size). It looks the same on every building.
DETAIL_ICON_B64 = "iVBORw0KGgoAAAANSUhEUgAAADAAAAAwCAIAAADYYG7QAAAR/klEQVRYCU3BaXddR3Ye4HfvqjrDHXAB3AuAADhLJDVLbcdxslbyw5MPWe4P7tVxuzVZUloSxZYoEhww3fHcc05V7TeUbPXy88g/fnxCOFWFiNFyzl2MRhNV/yuKGgSmAlFxYLLcZTZApy4Xg7IeD/YqOZyUezvjsh61Vlxcri9fna/Or7pVK84oRohRk+VkkqCmCufF+TIMgysBGEhaTkl+9+5UXemcE6cAUk59TEYTVe99CCGbmgkoQhEIrQNb9d1gqKNRsX84uHFjeDTIt3bDwe64Hu11bvfFPD17dv7i2dnVy/PVKi7W2+tVmm+4TXDOl0VZllURvHPig6pTkAKAICkfPdx1vvLeq3MQmFkfYwZV1AfnfQAL0tFgmcy95VY1DgY4Oh4fHk1Ob9a3TvW47I+qdhgAX2/9dMXdRYPlomnmq+1Wnr28fvLz1Y8vllcLUx0EX5XOexpyZIj0GUaYCURV5e/eO/CuUBVVAWDZupiyGUR88UZlyWhOJGR4MBa+nYz7kwPevxtOj8rZJEwqPypkUEjwXnzIWkWEaIx9n7oU2+pyMX/y/OLrx8uvH7vWZihK0Q5xPg4hgdGyECQFkDf+/sOj0pWECQCCxj6llElRH4pQlDlGAE69ejcoMdvFzUPcPZFbB2l/lAaFlL4MZV0NRkU9cCFAmFIf+ybF1lK2OGm6+dOXl5//Zf3Pn7nL9iiFAn5j3fm0HiZzMRNvGAW/kN+9d1BVtRlBAkLCzJKRVFXnQiDpJJUhjep8MvP3Twb3jkcn+9XIRYlbV7Laq+rhaLy7NxiPQ1kY0DbrZrPYNsu+7TVNmu767OLqq8fr3/8pP73a6TW4kJBWY1dRXFYIAUIAEPLRO/t1NSQJiIgCkg0xJzMC6t4IHA/scDffPMi3Z7g1DceTwbTeqaSWFHTHFcdFUVXVaOSrSr0nGLtN1zZ9t41dzGu/2b5+Pb/+9unmn/5v981fsUzeFeppRQouqAT8OyEEkA8f7RehVKfinIiSiH1MMQIWvKvKYjbD0UxPZ+7Ooczq7TjEcfCjcmc8PKoHe2FS656Kd/AeTk1AZssZZsJs2eI158ufL1eXT1/FP3ya/vjV5tUyU1yhznelBkphAnlDIb/48NG+U++8U+9FJOUY2wYW66CTncHezuj+XTs50IOJP9jRoXalxiK4ajAaTmb1aC/UlQQHr3BiChOSBlIIAcVgS724eny9vnw1x5+/0t//69WPr7ddQqlliEO6ni6qiP7CORH54OEeKC5451TEcm5z3NTODibV6dHuyeH+23e3s10bhlAXoVQJhYQ6FOPajyspvUcRciFBNTh4gdJIGmEGI6jalOcX3883l1cr/2/fVf/nj+ffP1+uuxykLDnO3CbpVNQ5deqcOnn/wS4zRSFKleQ11j4d7ODO4fDtm7PbJ9O90cu6ikUYFmEYQu2r0g8LN3S5SFl6b35gtTqVUsULlBTCwEwQktWa+uLqh/lmMV8V33w7+Kd/vfz+bLFsOxVfcdTlNrIXEe+cU3Wq8uju0DLJ7H0e1DLd8Sez4vYs3Dkobs2qg0kpXIQg1WAY6gGdM1WoOu99UFXxXosgFNIZ1UyyCZWqcE4czK/nfrk+Wzebiyv/6Zfhn79Y/Hy13TKpUKOPZokZEAFVfnX/ZoEEYyoLTvfCndOdd+9O7t8IJxObll2tXQg7vqy0VJTsGbMZ6AqpahkGKVnEXG+ghAddphpBGEAVOmZdLsq+v2623YuX+od/sX/5pjnf5hwM0uetZaiJ8g0jSJBy/yQofYzdsLRbh9UHb+9/9GDv9oHuDvrKt17TeLCvPiTJ9Nb2fe6yRhdy5VNVuAGHsZ+sfKlaAD6bJtJgYIIl5Ih2WZjacmuPn+bf/2H1/57kVVQGJjSx7SgFxfMNI0ga5cGtOvhRt5pPQnx4o/xv70zev13t7GQ/gI6H5f5s11/ldrXtEardtEBegGuiw2q7rUaj8rB0N1CPgh+YuS5hS0ZHRZLUMjXJLcwG+882xZ8fr37/p5evX/m+DWbWplWUraAQFPgbUu7dLDMrjc3BgO8cl//1rdE7R7Jbb4uB+VFd709DittNs2r6LvvcgI2xoyX0sDAa7JyM9m8Ni6FqmUzbzNaYFMIoqbW4SbJE68Z/nePPP6z/+MXV9WWIbTCzzjZJo9PSaSEiAEgCkDunZc8qoL8xwqPD4u9v1+8c2mzQVmXSyrvhKG2q9SbPm2bVbAN8pb503nkXdio3Kgf7xc608KXRdVm2mR1oCpejxK11m4R1uGr9d+fx0x/WX36/WS+L1AezHLmlS04r1QK/IglA7twss1aecb/mvX334XH1/g09GucypEzrIdfz0arBetssV5v9cTjcHezt1MNxOTnZk5HzFaoiwyfDNrPN6AVUhNRLt7VuY2zqp1fxq2fN539tfnie+ra0pGaWrYM3kQAE/AcCkHs3Sw2BKVYuH4zkwax896S+MfFBbdN18017dhk2LWPqt+v+rWP/9snOrRs701l9cHdmNanJsSe6bNvMjkgqKgixl7Zh21hqx395tv78yerfnrYv5socmMGcjVlUKAoofkNS7p2GILlPBDgocDDW29N6d1SI6LrNl+vu1Tr22QLM9/jwln58e+ftk/HhrJje2omhp+agQuuzbc16AVUdGfpOt41tGm678Zc/zD99vPz2LM77SumQE1MkAfWiChH8hoDcOylC7qOCouqkdBwGCV5NXJvdJuk6t6pp7LHv+btb5d/dnjw6Hh1O3XDmO9eamFdP68kOTAKIhJRc22rTcNPY1br67Pv55082T87ZYqTIYj1yFDpjIQ4Q/CeUe6dliF32PrtgKrQkKTIzQ5P46OqYm6BpWuPOrn5yXH9yc/zoZHjjwGHYb11rCi8BFoVRYAoFfdtps8Vmw1Vjz+fu0+8WX//UPVsEK8YOndhWLCmLnEuqiWb8DSH3b1Yhxd4VyYUsSuvZb61nJkwVYci+qyQdjOTdk+r9w+L9k8HD0/rwyDd+1RW9iUhyyqQwJ1Cqmds0XK+5brjY2A8X9vnjzbfP7XVTu+FY2ag1asmhjLGiJtGE35CUezcHwXRr6ESyiNNc5K1kY4ZB6EKZ3EDscCwf3t17e5+PboT7p/X0OFzjqqtSNlhjCgtCL6KUFHW1tvnKVhtbbuzb1/GLJ/HxK73qq3K4A1mpbT3No+j7CprEJfyGpNw59T4xEgkwgKCAAoCEQUSQZa/g/Yl+dFK9e1rcPimmB6Ga+BwIJ0zGLlGti9ugrtSiXcezl+t18lupLlv3vz7rzy77ZZO2CRIKUQFob2TmnAuRoIJ/RxKQW8fqEzOYAQMMICCAAGp4IygOan2wHz4+Lt65WZ4ch/Gec7WYF0CQTFI2Rdc3Xl1g2Cz6s5erZS5WLM7W+X9/hvmq72JKRqiKc4Rko1kmGKBOFABJ/EpOj8QZCBqRARO8IYQASggwKnA8Dg+n5cen9YObxeGhG+yIFIywnE0TPWHCmHqlMOr6ur+47uepeN3iyfnmj9+F9TannAmICgXGXwAQFcARijdI4g3KjRkcBABBAwy/EEIIBRywP5Bbk/DwcPDJzeG9m2E61XJo5nOfc+6zZnrCBNmyRfaNra7iui+uU/l00X/z8/XXz6q2N9IgVIVZNqMRonBeEnyG4lckAcjRFEqFkAIDCNIggBJO4IkbQ9zZLx4dDT64Nbx9Evb2UQzNnMVouctI5swMNErf5e0ybeYW/d5lLL+/aD578urJ+SgmA0xhKjCmbJaNEKh3JmoQ/A0hR1MRE4hQSAFBGoVwgAe84O4O7s+KBzeG790c3jiS8S5DTSpzgvVkn5iSOBXxbZNWi7hZSu/3n2/kqxfLz55cni2LnKlCr/AqhGVjMpqoOC9CgG/gN3I0VZqKCAQUEsZsSjggCLzi/ak8OCrfujF6cDqYzliPspYZKkiKiNzllJIPXl3YbtLyul+v3cpG315uP/95+dWzZtWpAsGh8PBOzdBn9lkyPFxwkoQJAEn8Sg6nzrK+IQoIyWyW1eCBIAgq/3Ai750M7t0Y3Tupx7vZVz1CgorLXnpN0VI2XxSqrln3i+tusy1ebvSzZ4tPn63/OkcBVB51kLJQB9fGvGm5iRJRiC+VvTACBEH8Qg72POnEiToRgEyWk5IeKAVe5X/cdh+eDu4cj++c1PW4k7Izlwnx2WvvLEsW8SHkzMW8mc/bLtY/LfKfflp/fb5dCk4LTGodVqEMRY44v25ez/N8g9ZUiiGRyIhfkQQg010PeOdUnIiQlixHJQNQKoLT/3kTH54O7p7s3DqpqlHHsjeXAfUpuF5JZ96pc822u7pczudd5ujpyv78bPOkMbdbv1t102E5Ggy8Fpt1/+Pzq5/O+pfXaLJIUZqawQCQxK9kNi2I4JUCAwnACDUrYAOPSe3/y8w+uDO6d2dyeMBQbbXIojATZ06SeNUQHLS4vNpeXLbLNSPLF/Pm2Wq7VhnsD28ETgqtvUeWi3n8y1nz3cv0/BpNpDo1NQPxH4SEzA5LogjIyBGWKV60RM4l4qTg8U713o598PbkrQeTncnWh00IUEjqoVAQhbPKwTB8ftZcLdD0RWfuan6ZpC8HOhxXquVAGXLqmvjTRfzyFb67wosVt70JM4UQAiICQEjI9KAgCs8sFmGZ8OJK5Fwh7hU8nlTv7+Ojh3tvP9qf7HXBr70aMmKfHbwC2XLKOabi7Gy9XGtmLa5cNSu6VFQ6GBZVUdTeIXG+6L75efn58/Rkzost+miMpp7iRSAASCEp01kw+iBZLMFIOLhSc64lTQue7FbvzuzDR3sPHk0nu73TtYMxWWqzg1Mgq7bm2gYvzpabDUXqshz0fQuffMEQpAw+hHrby/PL9tMf5l++6M5WaJIgZ+upARpEoPwFzCizmct0QSiWYTBxooWzPNQ4K3Frr3rvBB883Lt/fzIctSIbD0NiarOjCklfJD/YrtOr5/PtJjuURVGl1KqnukxJVNezvGz0yXn/2dP14/M07yQDHiYR8AIHUPgLGCGzmWa6QilmNJh40cIzjV06qnl7r/rkXvH+w8nNm8NQrMBNEKhp7k2zWE7qSylH7bq/eLHom6z0TlzK0XmIWp/7RdSLVn9e4PuL/O15erXiNkMFhdIBBhBCggQJkjKbCsUFIWikGAp1RcE4cel4yHv71T++N3z3weTgMBBXljaF90ECkyAxx17EOVd1Tbe63OSWYo6JKWdfBCjWXffjCj9ep8dX9vhaXmzcJiIZVSwog0M2mIEQGkmQkIMpIOpVQBolIzgXSqR9F09HeGtW//f3hw/fGu3tu5jOU2rKoix9DVNmS32LDG8+tbFddYhO6GOPaPBlbapXTfvFq/j9eff4Mv+0lGUqCNISLBLQIDDSAIIEiTfkYApSghMAmWIIzvmKaRri7RHeOhj8w0P34N5gd9/16TLmriyrMtSgp1mMnST4FOK26zYJ2TOHprFtFoa6M3m52HzxIv604LM1XjW6zeqRHHsyRSA5p0algXiDlF8c7MOI4ARAphq8c6FCnBXx7hgPZoOP7+YHd6q9g9Dn62SpKKsQKtBTmFOPqK4v2qZr171Fl6Kfr9Kyl06KZYtnF5tvL+x1769imEfXx1SiKyRC2Yk0CJ7JM4P4lb4hh7tqJsEZQRM1eOfLEmka+jtjvj0rPzjt37pVzQ4KuLUx+1B4X0ECBcl661xuymbVNau277jt5WIRLxpdRHe5sedX7bOVNFJspWzNp9hVaEuX1UsrbpO9R/JIoAjgnHcuyPFgIFvvw5Y+mSeDE78bTEZue1Rvbk3xyYnePQwH+35/Bx5JIeoKDXUSbHPcbLC6Kpq5bpbNptuuUnfV4fmyfr52511a5HbeOsILnAhVk2hSEIQZaKCDAUzwgp3hpCyGclA778W5nMFIdJQs3hM73o5Hdnvf367z8a7cmLmjqR9VUnqvzmf6ddcvN9vFJq/WbnVdbDZxm/qGednjbBFeN1hk6zSbCqmkwghkFapAKIAHPTWpo6q3xLocj4d7MhugVRQeqsgiPTTSJHMg2Pd6WBeHVZyNONvV2UTGtdaFOqeZstqkxTouurzqZTn3m4YtrYVtEi7XYdWxlcyCzgEmZoCBpBMIRMUJPOhUzRda1pWIH9Q7e5PZ/wdEP5yqCKh9eQAAAABJRU5ErkJggg=="


def detail_template():
    """The i icon picture: the one learned on this PC (templates/detail_icon.png) wins over the built-in one."""
    if "t" not in _DETAIL_TPL:
        import base64
        t = None
        p = template_path("detail_icon")
        if p:
            t = cv2.imread(str(p))
        if t is None:
            t = cv2.imdecode(np.frombuffer(base64.b64decode(DETAIL_ICON_B64), np.uint8), cv2.IMREAD_COLOR)
        _DETAIL_TPL["t"] = t
    return _DETAIL_TPL["t"]


def find_detail_icon(shot, thr=0.8):
    """Look for the i (Detail) icon of an open ring menu by its PICTURE. -> (x, y, score) or None."""
    t = detail_template()
    th, tw = t.shape[:2]
    area = shot[100:800, 0:540]
    if area.shape[0] < th or area.shape[1] < tw:
        return None
    res = cv2.matchTemplate(area, t, cv2.TM_CCOEFF_NORMED)
    _, mv, _, ml = cv2.minMaxLoc(res)
    if mv < thr:
        return None
    return ml[0] + tw // 2, ml[1] + 100 + th // 2, float(mv)


def ring_label(shot, expected, pos):
    """The building name the game prints under a tapped building (ring menu open). -> (name, x, y) of the best match or None.
    Prefers the expected building; otherwise the name nearest to where we tapped."""
    cands = []
    for text, ws in ocr_lines(shot[120:760, 40:500]):
        for n in (1, 2, 3):
            for i in range(len(ws) - n + 1):
                seg = ws[i:i + n]
                nm = match_building(" ".join(w[0] for w in seg))
                if nm:
                    cands.append((nm, int(sum(w[1] for w in seg) / n) + 40, int(sum(w[2] for w in seg) / n) + 120))
    for text, ws in ocr_lines(shot[120:760, 40:500]):          # a long name read only in part ("Arch" of "Triumphal Arch") counts for the expected building
        for w in ws:
            if len(w[0]) >= 4 and str(expected).count(" ") and w[0].lower().strip(".,:;*") in str(expected).lower().split():
                cands.append((str(expected), int(w[1]) + 40, int(w[2]) + 120))
    if not cands:
        return None
    norm = lambda t: BUILDING_ALIASES.get(t, t).lower().replace(" ", "")
    exp = [c for c in cands if norm(c[0]) == norm(expected) or str(expected).lower().startswith(c[0].lower())]
    pool = exp or cands
    return min(pool, key=lambda c: (c[1] - pos[0]) ** 2 + (c[2] - pos[1]) ** 2)


def close_ring(dev, stop, label_pos):
    """Close the open ring menu by tapping the same building again (the game closes it). Never Back: on the city screen
    Back asks to quit the game. -> True when the ring is gone."""
    lx = min(max(label_pos[0], 110), 400)                    # never aim at the icon columns on the screen edges (Login Gifts, Quests ...)
    spots = [(lx, label_pos[1] - 35), (270, 430), (lx, label_pos[1] - 60)]
    for sp in spots:
        if not find_detail_icon(dev.screenshot()):
            return True
        dev.tap(int(sp[0]), int(sp[1]))
        for _ in range(3):                                   # the game zooms back out after the ring closes: give it time before tapping again
            _nap(stop, 0.5, 0.7)
            if not find_detail_icon(dev.screenshot()):
                return True
    return not find_detail_icon(dev.screenshot())


def read_square(dev, cfg, log, stop, pos, expected="", levels=False):
    """Tap the building at pos and read its name from the label the game prints under it (the ring menu is open; its i
    icon, found by picture, is the proof that the menu opened). With levels=True also press the i icon, More Information
    and read "Name Level N". Every step first checks that the screen it expects is really there.
    -> (name, level, (label x, label y)) or None. In the quick mode the ring menu is left open (see close_ring)."""
    nap = lambda lo, hi=None: _nap(stop, lo, hi)
    ic = ring = lab = None
    for attempt in (1, 2):                                  # a bubble over the building can eat the first tap
        dev.tap(int(pos[0]), int(pos[1]))
        for _ in range(6):                                  # the camera glides to the building first: wait for the ring menu (up to ~4 s)
            nap(0.6, 0.8)
            ring = dev.screenshot()
            ic = find_detail_icon(ring)
            if ic or stop.is_set():
                break
        if ic:
            time.sleep(0.4)                                 # let the name label finish drawing
            ring = dev.screenshot()
            ic = find_detail_icon(ring) or ic
            break
    if not ic:
        log("[!]   no ring menu after tapping - nothing opened")
        return None
    known_names = {n.lower() for n in BUILDING_NAMES} | {BUILDING_ALIASES.get(n, n).lower() for n in BUILDING_NAMES}

    def _read(img):                                         # a building name the game prints, spelled the way the bot knows it
        r = ring_label(img, "", pos)
        if r:
            return r
        r = ring_label_free(img)
        if r and r[0].lower() in known_names:
            return r
        if r:
            log(f"[i]   text on the ring that is not a building name: '{r[0]}' - left blank (map this square by hand)")
        return None
    lab = _read(ring)
    for _ in range(3):                                      # the label may still be drawing: look again
        if lab or stop.is_set():
            break
        nap(0.5, 0.7)
        ring = dev.screenshot()
        lab = _read(ring)
    if not levels and lab:
        return lab[0], 0, (lab[1], lab[2])
    if not lab:
        log("[i]   name on the ring not clear - pressing the i icon to read it on the Detail page")
    dev.tap(ic[0], ic[1])
    nap(1.1, 1.5)
    shot = dev.screenshot()
    result = None
    more = find_text(shot, ["More Information"], region=(60, 780, 420, 150))
    if more:
        dev.tap(*more)
        nap(0.9, 1.3)
        shot = dev.screenshot()
        for text, _ in ocr_lines(shot[130:210, 60:480]):
            m = re.search(r"([A-Za-z' ]+?)\s+Level\s*(\d+)", text)
            if m:
                result = (m.group(1).strip(), int(m.group(2)))
                break
            nm = match_building(text)                       # no level in the header (Triumphal Arch...)
            if nm:
                result = (nm, 0)
                break
    else:                                                    # Detail page without More Information: read the name on the picture
        for text, _ in ocr_lines(shot[430:520, 100:440]):
            nm = match_building(text)
            if nm:
                result = (nm, 0)
                break
    try:                                                     # learn this PC's real i icon once
        if result and not template_path("detail_icon"):
            save_template("detail_icon", ring[ic[1] - 24:ic[1] + 24, ic[0] - 24:ic[0] + 24])
    except Exception as _e:
        quiet_err("read_square", _e)
    for _ in range(4):                                       # close the panel / Detail page, back to the city
        if screen_state(dev.screenshot(), cfg) == "city":
            break
        dev.back()
        nap(0.7, 1.0)
    return (result[0], result[1], None) if result else None


def open_square(dev, cfg, log, stop, nav, shrine, number):
    """Go to numbered square N of the default city map (by coordinates), tap it and read the building.
    -> (expected name, read name, level, map x, map y) or None."""
    sq = CITY_SQUARES.get(int(number))
    if not sq:
        log(f"[!] Square {number} does not exist (1-{len(CITY_SQUARES)})")
        return None
    name, ox, oy = sq
    tx, ty = shrine[0] + ox, shrine[1] + oy
    nav.goto(tx, ty)
    px, py = tx - nav.cx, ty - nav.cy
    x0, y0, w, h = CITY_VIEW
    if nav.absolute:                                         # only tap where nothing but the city is: not the top bar, bubbles or the side icon columns
        x0, y0, w, h = SAFE_TAP_AREA
    if not (x0 <= px <= x0 + w and y0 <= py <= y0 + h):
        log(f"[!] Square {number} ({name}): cannot be brought into the safe tap area (it is at {round(px)},{round(py)} on screen, city edge?) - left blank, map it by hand")
        return None
    log(f"[*] Square {number} ({name}): tapping {round(px)},{round(py)}")
    levels = bool(cfg.get("squares_levels", False))
    res = read_square(dev, cfg, log, stop, (px, py), name, levels)
    if res and res[2] and nav.absolute:                      # the camera place is read from the picture each time: only close the ring
        if not close_ring(dev, stop, res[2]):
            log("[!]   the ring menu would not close - restarting Evony so nothing is pressed by mistake")
            force_restart_game(dev, cfg, log, stop, must=True)
            return None
    elif res and res[2]:                                     # the label shows where the building really is on the screen: re-anchor the camera
        lx, ly = res[2]
        fo = FIXED_OFFSETS_FROM_SHRINE.get(name)
        if fo:                                               # the label's own place is known exactly (offset from the Shrine label)
            nav.cx, nav.cy = shrine[0] + fo[0] - lx, shrine[1] + fo[1] - ly
        else:
            nav.cx, nav.cy = tx - lx, ty - (ly - 35)
        if not close_ring(dev, stop, res[2]):
            log("[!]   the ring menu would not close - restarting Evony so nothing is pressed by mistake")
            force_restart_game(dev, cfg, log, stop, must=True)
            return None
    elif levels or res is not None:                          # read on the Detail page (i icon): back out to the city and make sure the ring is gone
        leave_to_city(dev, cfg, stop)
        _nap(stop, 0.6, 0.9)
        if find_detail_icon(dev.screenshot()):
            close_ring(dev, stop, (270, 465))
    elif res is None and not levels:
        leave_to_city(dev, cfg, stop)
    if not res:
        log(f"[!] Square {number} ({name}): nothing could be read")
        return None
    got = BUILDING_ALIASES.get(res[0], res[0])
    like = got.lower().replace(" ", "") == BUILDING_ALIASES.get(name, name).lower().replace(" ", "") or name.lower().startswith(got.lower())
    log(f"[+] Square {number} -> '{res[0]}'" + (f" level {res[1]}" if res[1] else "") + ("" if like else f"  (the default map says {name})"))
    return name, res[0], res[1], tx, ty, True                # the map is filled with what the game says, not with what we expect


def task_needs_city_map(t):
    tgt = str(t.get("target", "")).lower()
    steps = [str(x).strip().lower() for x in t.get("steps", [])]
    if tgt.startswith(("go:", "via:")):                    # reached through Activity > Go: no city map needed
        return False
    return tgt.startswith("building:") or any(x.startswith("market") for x in steps)


TASK_LOG = USER / "tasks_log.jsonl"
_TASKLOG_LOCK = threading.Lock()


def task_event(serial, task, ok, msg=""):
    """One line per finished / failed task, so the app can show what the bot did (Duties tab)."""
    try:
        with _TASKLOG_LOCK:
            with open(TASK_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": int(time.time()), "s": serial, "task": task, "ok": bool(ok), "msg": str(msg)[:160]}) + "\n")
            if TASK_LOG.stat().st_size > 400000:
                lines = TASK_LOG.read_text(encoding="utf-8").splitlines()[-1500:]
                TASK_LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as _e:
        quiet_err("task_event", _e)


def read_task_events(n=150):
    try:
        lines = TASK_LOG.read_text(encoding="utf-8").splitlines()[-n:]
    except Exception:
        return []
    out = []
    for l in reversed(lines):
        try:
            out.append(json.loads(l))
        except Exception as _e:
            quiet_err("read_task_events", _e)
    return out


DUTY_BUDGET_MIN = 8      # owner 25 Oct: a duty that cannot finish is left after 8 min (was 20 - Rob missed rallies for 18 min on
                         # a stuck Wheel panel); it is retried later and the instance goes back to joining rallies


def run_city_tasks(dev, cfg, log, stop, tasks, last_run):
    """From the city screen: run each due task (open it, run its steps, go back)."""
    nav_state = {}
    _TL.city_last = last_run                                 # the Black Market keep-going run looks at what else is due
    dev.arm_guard(cfg, log, stop)
    for t in tasks:
        if stop.is_set():
            break
        name = t.get("name", "task")
        duty_id = name[5:] if name.startswith("duty:") else ""
        shown = DUTY_BY_ID[duty_id]["title"] if duty_id in DUTY_BY_ID else name
        log(f"[*] {'Duty' if duty_id else 'City task'}: {shown}")
        log("@act " + json.dumps({"k": "step", "text": f"{'Duty' if duty_id else 'City task'}: {shown}"}))     # shown under 'Now executing'
        last_run[name] = time.time()
        for rid in (name, "task:" + name):                      # a 'run now' request is used up once the task is started
            run_now_done(getattr(dev, "serial", ""), rid)
        result = None
        if task_needs_city_map(t) and not city_map_items(dev):
            log(f"[!] '{shown}' needs the city map. {CITY_MAP_HELP}")
            if duty_id and not stop.is_set():
                duty_record(getattr(dev, "serial", ""), duty_id, False, "run the City Mapper first")
            task_event(getattr(dev, 'serial', ''), shown, False, 'needs the city map')
            continue
        RESTARTS = 1                                         # universal protocol: a stray window / failure = back to the city, then the whole operation starts again
        for attempt in range(RESTARTS + 1):
            try:
                if attempt:
                    log(f"[*] Restarting '{shown}' from the city view (attempt {attempt + 1})")
                    nav_state.clear()
                # time budget per duty (Kiravik 24 Oct 15:14: Champions sat on a purchase page for 10+ min with no log line):
                # 20 min, 50 for the city map; over it, the next tap aborts the duty and the attempt-2 path takes over
                _TL.duty_deadline = time.time() + (50 if duty_id == "map_city" else DUTY_BUDGET_MIN) * 60
                if not ensure_view(dev, cfg, stop, log, "city") and not stop.is_set():
                    log("[!] Could not get back to the city view")
                clear_popups(dev, cfg, log, stop)            # close offers / pop-ups first, then do the task
                ok = city_task_target(dev, cfg, log, stop, t, nav_state)
                if not ok and clear_popups(dev, cfg, log, stop):  # a pop-up was in the way: try the same task again
                    ok = city_task_target(dev, cfg, log, stop, t, nav_state)
                if ok:
                    _nap(stop, 1.2, 1.8)
                    _TL.rd_nothing = False
                    was_busy = getattr(dev, "_guard_busy", False)
                    dev._guard_busy = True                   # the pop-up closer must not shut the duty's own windows (Levy window, 23 Oct)
                    try:
                        for st in t.get("steps", []):
                            run_step(dev, cfg, log, stop, st)
                    finally:
                        dev._guard_busy = was_busy
                    leave_to_city(dev, cfg, stop, log)
                result = (ok is not False, "" if ok is not False else "could not open it")
            except Exception as e:
                log(f"[!] {'Duty' if duty_id else 'City task'} '{shown}' failed: {e}")
                result = (False, str(e)[:120])
            finally:
                _TL.duty_deadline = None
            if stop.is_set() or (result and result[0]):
                break
            dev.expect_city = False
            leave_to_city(dev, cfg, stop, log)               # failed or a wrong window opened: close everything, then (once) start over
        dev.expect_city = False
        if result is not None and not stop.is_set():
            task_event(getattr(dev, "serial", ""), shown, *result)
        if duty_id and result is not None and not stop.is_set():
            if duty_record(getattr(dev, "serial", ""), duty_id, *result, nothing=bool(result[0] and getattr(_TL, "rd_nothing", False))) == "gaveup":
                duty_failed_report(dev, cfg, log, shown, result[1])
        leave_to_city(dev, cfg, stop, log)
    log("@act " + json.dumps({"k": "step", "text": "Idle - tasks done, back to the rallies"}))   # the instance list stops showing the task


def duty_failed_report(dev, cfg, log, shown, why):
    """A duty failed 3 times today: save a report (log + screenshots) and send an alert, so it can be looked at."""
    serial = getattr(dev, "serial", "")
    path = ""
    try:
        d = Path.home() / "Desktop" / "RallyForge reports"
        d.mkdir(parents=True, exist_ok=True)
        who = re.sub(r"[^\w\-]+", "_", str(shown))[:30].strip("_")
        p = d / f"rallyforge-report-{who}-failed-{time.strftime('%Y%m%d-%H%M')}.zip"
        build_report(str(p), include_debug=True, description=f"Automatic report: '{shown}' failed 3 times on {serial} ({why})")
        path = str(p)
    except Exception as e:
        log(f"[!] Could not save the failure report: {e}")
    log(f"[!] {shown} failed 3 times today ({why}) - it keeps trying every {DUTY_RETRY_MIN} min; Retry on the Duties page runs it now."
        + (f" Report saved: {path}" if path else ""))
    notify(cfg, serial, f"{shown} failed 3 times today ({why}). The bot keeps trying every {DUTY_RETRY_MIN} min."
           + (f" Report: {path}" if path else ""))


def run_device(serial, adb, cfg, log, stop, on_frame=None, ctl=None):
    cfg = merge_device_cfg(cfg, serial)                              # per-instance overrides
    set_pacing(cfg)
    try:
        cfg_seen = [CONFIG.stat().st_mtime]                          # settings saved after this are taken over while running
    except OSError:
        cfg_seen = [None]
    _TL.ctl, _TL.stop = ctl, stop

    def nap(lo, hi=None):
        stop.wait(pace(random.uniform(lo, hi if hi is not None else lo)))

    dev = Device(serial, adb)
    dev.mode = cfg.get("screenshot_mode", "auto")
    dev.arm_guard(cfg, log, stop)
    auto = cfg["auto_launch_emulator"] or cfg["auto_launch_game"]
    try:
        if auto:
            if not bring_online(dev, cfg, log, stop, check_ready=True):
                if not stop.is_set():
                    notify(cfg, serial, "Could not get the emulator and the game ready - restarting the emulator.")
                return log("[+] Stopped.") if stop.is_set() else \
                    log("[!] Could not get the emulator and the game ready - restarting the emulator.")
        else:
            dev.connect()
        dev.screenshot()
        try:                                              # warn only: the bot keeps running on another screen size, but it can fail there
            ok_screen, msg_screen = dev.ensure_screen(log)
            log(("[+] " if ok_screen else "[!] ") + msg_screen + ("" if ok_screen else " The bot can fail on a different screen size."))
        except Exception as _e:
            quiet_err("run_device", _e)
    except Exception as e:
        notify(cfg, serial, f"Could not start: {e}")
        return log(f"[!] {e}")
    rw, rh = dev.real
    log(f"[+] Connected to {serial} (screen {rw}x{rh}, screenshots: {dev.used}, "
        f"click delay {cfg['click_delay_ms']} ms, CPU level {cfg['cpu_level']})")
    if rw > rh:
        return log("[!] Emulator is in landscape. Set it to portrait (540x960 recommended).")
    if abs(rw / rh - EXPECTED_SIZE[0] / EXPECTED_SIZE[1]) > 0.02:
        log("[!] Unusual aspect ratio; templates may not match. 540x960 portrait is best.")
    elif (rw, rh) != EXPECTED_SIZE:
        log(f"[i] Screen is {rw}x{rh}; scaling to 540x960 automatically.")

    rot = PresetRotator(active_presets(cfg), cfg["preset_mode"])
    at_bottom = [False]                   # True while a bottom-to-top sweep of the rally list is under way
    sweep_no = [0]                        # every new pass over the list: rallies that did not open are tried again
    back_at = {}                          # preset -> time its troops should be home again (rally left + march there and back + fight)
    mon_presets = {k.lower(): v for k, v in cfg.get("monster_presets", {}).items() if v}
    rots = {}

    def rotator_for(mon):
        """Presets for this monster if you set some, otherwise the normal presets."""
        if mon and mon.lower() in mon_presets:
            key = mon.lower()
            if key not in rots:
                rots[key] = PresetRotator(mon_presets[key], cfg["preset_mode"])
            return rots[key]
        return rot
    steps = [s for s in cfg["after_join_steps"] if has_template(s)]
    joins = scrolls = unknown = errors = 0
    warned_verify = False
    no_open = []                          # [monster, end time, "joined" | "failed", failures, last failure time]

    def remember_rally(name, end, kind):
        now_t = time.time()
        no_open[:] = [x for x in no_open if x[1] > now_t]
        for x in no_open:
            if x[0] == name and abs(x[1] - end) <= 6:
                if kind == "joined":
                    x[2] = "joined"
                else:
                    x[3] += 1
                    x[4] = now_t
                    x[5] = sweep_no[0]
                return
        no_open.append([name, end, kind, 1 if kind == "failed" else 0, now_t, sweep_no[0]])
    lat_pick = [None]                     # extra seconds a preset needs when the bot has to pick generals
    last_march = [None]                   # march time of the rally find_target just chose
    latency = [None]                      # seconds the bot itself needs between reading a rally and pressing March
    last_ok = [time.time()]               # last time ADB gave us a screenshot
    t_started = [time.time()]             # this run of the instance began (no chat check in the first minutes)
    still = {"sig": None, "since": None}  # screen that should be moving but is not
    stale_hits = [0]                      # empty-list checks in a row while other instances saw rallies
    empty_list = {"checks": 0, "reopens": 0, "last": 0.0, "stale": 0, "restarts": []}   # empty / out-of-date rally list: looks, reopens, last reopen, Evony restarts

    def list_restart(why):
        """Evony restart for a rally list that reopening did not fix - at most once per 30 min per instance."""
        t = time.time()
        empty_list["restarts"] = [r for r in empty_list["restarts"] if t - r < 1800]
        if empty_list["restarts"]:
            return False
        empty_list["restarts"].append(t)
        log(f"[!] {why} - restarting Evony")
        force_restart_game(dev, cfg, log, stop)
        return True
    lost_since = [None]                   # since when we have been off the rally list (kept across game restarts)
    restarts = []                         # when the emulator was restarted (to stop restart loops)
    lost_restarts = []                    # when "lost for too long" restarted Evony (2 in 30 min -> restart the emulator)
    freezes = []                          # when a frozen screen made us restart Evony

    def slower(reason):
        f = slow_down()
        if f:
            log(f"[i] {reason} - click delay raised to {cfg['click_delay_ms'] * f:.0f} ms for a while")

    def emu_restart(why):
        """Restart a stuck emulator. -> False if this instance should give up."""
        now = time.time()
        restarts[:] = [t for t in restarts if now - t < 3600]
        if len(restarts) >= int(cfg.get("max_emulator_restarts", 3)):
            wait_s = max(60, 3600 - (now - min(restarts)) + 5)
            log(f"[!] The emulator was already restarted several times this hour - cooling down {wait_s/60:.0f} min, then trying again.")
            notify(cfg, serial, f"Stuck again ({why}) after several restarts - waiting {wait_s/60:.0f} min before another try.")
            QUIET_UNTIL[serial] = time.time() + wait_s                    # planned wait: the watchdog leaves it alone
            if stop.wait(wait_s):
                return False
            now = time.time()
            restarts[:] = [t for t in restarts if now - t < 3600]
        restarts.append(now)
        notify(cfg, serial, f"Emulator stuck ({why}) - restarting it.")
        try:
            ok = restart_emulator(dev, cfg, log, stop, why)
        except Exception as e:
            log(f"[!] Could not restart the emulator: {e}")
            notify(cfg, serial, f"Could not restart the emulator: {e}")
            return False
        still.update(sig=None, since=None)
        lost_since[0] = off_list[0] = None
        last_ok[0] = time.time()
        if ok:
            notify(cfg, serial, "Emulator restarted and Evony is loaded again.")
        return ok

    seen = {}

    def fmt(v):
        return "?" if v is None else f"{v}s"

    def say(note):
        if time.time() - seen.get(note, 0) > 10:
            seen[note] = time.time()
            log("[?] " + note)

    act_seen = {}

    def act(kind, **kw):
        """Structured activity line for the Activity screen (the GUI hides these from the log)."""
        try:
            log("@act " + json.dumps({"k": kind, **kw}))
        except Exception as _e:
            quiet_err("run_device.act", _e)

    def act_skip(mon, why, rally=None, march=None):
        key = f"{mon}|{why}"
        if time.time() - act_seen.get(key, 0) > 12:
            act_seen[key] = time.time()
            act("skip", mon=mon or "?", why=why, rally=rally, march=march)

    last_dn = [None]                      # monster name with its level, e.g. "Witch 6", of the rally find_target chose
    last_base = [None]

    name_cache = {}

    def show_name(screen, hit, mon):
        """A monster name for the Activity screen and the log, also when the filters did not need one: the saved
        picture first, then the name printed under the portrait (each different label is read only once).
        Only for showing - whether to join is still decided by the filters alone."""
        if mon and mon != "hidden":
            return mon
        try:
            if mon is None:
                m0, _ = identify_monster(screen, hit, cfg)
                if m0 and m0 != "hidden":
                    return m0
            nx, ny = int(hit[0] - 21), int(hit[1] - 113)
            st = screen[max(ny - 18, 0):ny + 18, max(nx - 150, 0):nx + 150]
            if not st.size:
                return "?"
            g = cv2.cvtColor(cv2.resize(st, (48, 8), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
            key = (g // 32).tobytes()
            if key not in name_cache:
                lab = read_monster_label(st)
                if len(name_cache) > 300:
                    name_cache.clear()
                name_cache[key] = lab if lab in MONSTER_NAMES else "?"
            return name_cache[key]
        except Exception:
            return "?"

    def level_name(screen, hit, mon, mlv):
        """'Witch 6' for the Activity screen (reads the level from the card's power when it is not known yet)."""
        if not mon or mon == "hidden":
            return show_name(screen, hit, mon)
        if mlv is None:
            try:
                pw, _ = read_monster_power(screen, hit)
                mlv = level_from_power(mon, pw) if pw else None
            except Exception:
                mlv = None
        return f"{mon} {mlv}" if mlv else mon

    def mtxt(march):
        return f", march {fmt(march)}" if cfg.get("march_timer_source") else ""

    power_mem = []                       # [(monster, rally end time, power)]: a rally keeps its power, read it once
    nudge_t = [0.0]

    def power_of(screen, hit, mon, hard):
        """The monster power of this rally: remembered from an earlier scan of the same rally (same monster, same end
        time), else read now. -> (power | None, raw text)."""
        now = time.time()
        power_mem[:] = [m for m in power_mem if m[1] > now - 30]
        left = read_seconds(screen, hit, cfg["rally_timer_source"])
        end = now + left if left is not None else None
        if end is not None:
            for m, e, pw in power_mem:
                if m == mon and abs(e - end) <= 6:
                    return pw, "remembered"
        pw, raw = read_monster_power(screen, hit, hard)
        if pw and end is not None:
            power_mem.append((mon, end, pw))
        return pw, raw

    def cut_off(hit):
        """True when the power box of this card is hidden under the top bar of the list."""
        return int(hit[1]) - 258 < 125

    def find_target(screen, nudged=False):
        hits = find_all(screen, "join_button", cfg["threshold"], cfg["search_region"])
        hits = [h0 for h0 in hits if not joined_button(screen, h0)]   # grey "Joined" = this account is already in it
        hits = sorted(hits, key=lambda h0: -h0[1])                    # bottom of the list first: those rallies have the least time left
        deferred = []                                                 # rallies whose power could not be read at the first try
        picks = []                                                    # every rally on screen the bot could join: the one closing soonest wins

        def order():                                                  # every readable rally first, then the unreadable ones read harder
            for h0 in hits:
                yield h0, False
            for h0 in list(deferred):
                yield h0, True
        for hit, hard in order():
            label, mon, mlv = "", None, None
            if cfg.get("save_unknown_monsters"):
                m0, _ = identify_monster(screen, hit, cfg)
                if m0 is None:
                    collect_unknown_monster(screen, hit, cfg, log)
            c_ok, c_why = cerberus_allowed(screen, hit, cfg)
            if not c_ok:
                act_skip("Cerberus", c_why)
                if cfg["log_timers"]:
                    say(f"skip ({c_why})")
                continue
            if cfg["monster_mode"] != "off" or mon_presets or cfg.get("monster_skip") or never_join_present():
                mon, _ = identify_monster(screen, hit, cfg)
                if mon is None and cfg.get("monster_skip"):        # no picture yet: read the name under the portrait instead
                    try:
                        nx0, ny0 = int(hit[0] - 21), int(hit[1] - 113)
                        lab = read_monster_label(screen[max(ny0 - 18, 0):ny0 + 18, max(nx0 - 150, 0):nx0 + 150])
                        if lab in MONSTER_NAMES:
                            mon = lab
                            # first picture of a monster known only by name (e.g. Ymir): keep its portrait so the next
                            # rally is recognised from the picture. Only a fully visible card - never a cut-off one.
                            if lab != "Viking" and not cut_off(hit) and not any(_base_monster(n) == lab for n in _raw_monsters()):
                                mdx, mdy = cfg["monster_offset"]
                                mcx, mcy, mh = int(hit[0] + mdx), int(hit[1] + mdy), MON_SIZE // 2
                                if mcy - mh >= MON_TOP + 10 and mcx - mh >= 0 and mcx + mh <= screen.shape[1] and mcy + mh <= screen.shape[0]:
                                    learn_monster(lab, screen[mcy - mh:mcy + mh, mcx - mh:mcx + mh].copy(), cfg, log)
                    except Exception as _e:
                        quiet_err("run_device.find_target", _e)
                if mon and _base_monster(mon) in WORLD_BOSS_LOOKALIKE:   # Behemoth or Behemoth King? the picture alone cannot tell
                    try:
                        nx0, ny0 = int(hit[0] - 21), int(hit[1] - 113)
                        lab = read_monster_label(screen[max(ny0 - 18, 0):ny0 + 18, max(nx0 - 150, 0):nx0 + 150])
                        if str(lab).lower() in NEVER_JOIN or "king" in str(lab).lower().split():
                            say(f"[{mon}] name reads '{lab}' - world boss, never joined")
                            mon = lab if str(lab).lower() in NEVER_JOIN else f"{mon} King"
                    except Exception as _e:
                        quiet_err("run_device.find_target.label", _e)
                if mon == "Viking":                                   # which set is it? level on the card + power decide
                    nx, ny = int(hit[0] - 21), int(hit[1] - 113)
                    st = screen[max(ny - 18, 0):ny + 18, max(nx - 150, 0):nx + 150]
                    pw, praw = power_of(screen, hit, "Viking", hard)
                    if pw is None and cut_off(hit) and not nudged and time.time() - nudge_t[0] > 4:
                        nudge_t[0] = time.time()                       # unreadable because the card is cut off at the top:
                        say(f"[{mon}] card is cut off at the top - scrolling it into view")      # scroll it into view, look again
                        dev.swipe(270, 380, 270, 560, 400)
                        nap(0.7, 1.0)
                        return find_target(dev.screenshot(), nudged=True)
                    lv0 = read_monster_level(st) if st.size else None
                    vname, vlv = viking_resolve(pw, lv0)
                    if vname is None and lv0 is not None:
                        vname, vlv = viking_resolve(pw, None)
                    if vname is None and not hard:                    # come back to this one after the other rallies
                        say(f"[Viking] power read '{praw}' - not clear, trying the other rallies first")
                        deferred.append(hit)
                        continue
                    say(f"[Viking] power read '{praw}', level {lv0 if lv0 else '?'} -> {vname or 'set not recognised'} Lv{vlv if vlv else '?'}")
                    if vname:
                        mon, mlv = vname, vlv
                    else:                                             # set unknown: join only if every Viking group would be allowed
                        bad = [vn for vn in VIKING_SETS if not monster_allowed(vn, cfg, lv0)[0]]
                        if len(bad) >= len(VIKING_SETS):              # every group is off: nothing to join. Some on: join rather than miss it
                            act_skip("Viking", "Viking set not recognised and some groups are switched off")
                            if cfg["log_timers"]:
                                say("[Viking] skip (set not recognised and %s is switched off)" % ", ".join(bad))
                            continue
                elif monster_needs_level(mon, cfg):
                    nx, ny = int(hit[0] - 21), int(hit[1] - 113)
                    st = screen[max(ny - 18, 0):ny + 18, max(nx - 150, 0):nx + 150]
                    pw, praw = power_of(screen, hit, mon, hard)
                    if pw is None and cut_off(hit) and not nudged and time.time() - nudge_t[0] > 4:
                        nudge_t[0] = time.time()                       # unreadable because the card is cut off at the top:
                        say(f"[{mon}] card is cut off at the top - scrolling it into view")      # scroll it into view, look again
                        dev.swipe(270, 380, 270, 560, 400)
                        nap(0.7, 1.0)
                        return find_target(dev.screenshot(), nudged=True)
                    mlv = level_from_power(mon, pw) if pw else None
                    if mlv is None and st.size:
                        mlv = read_monster_level(st)
                    say(f"[{mon}] power read '{praw}' -> level {mlv if mlv else 'not readable'}")
                    if mlv is None:
                        if not hard:                                  # come back to this one after the other rallies
                            say(f"[{mon}] level not clear - trying the other rallies first")
                            deferred.append(hit)
                            continue
                        say(f"[{mon}] level still not readable - joining anyway (a rally is never left unjoined just because of a blurry number)")
                m_ok, m_why = monster_allowed(mon, cfg, mlv)
                if mon == "hidden" and mon_presets:
                    m_ok, m_why = False, "monster picture not fully on screen"
                label = f"[{mon or '?'}] "
                if not m_ok:
                    act_skip(level_name(screen, hit, mon, mlv), m_why)
                    if cfg["log_timers"]:
                        say(f"{label}skip ({m_why})")
                    continue
            need = latency[0] if latency[0] is not None else 3.0 * pace(1.0)
            try:                                              # only slow presets left: count their general search too
                if not any(not preset_picks(cfg, n) for n in active_presets(cfg)):
                    need += lat_pick[0] if lat_pick[0] is not None else 30.0
            except Exception as _e:
                quiet_err("run_device.find_target", _e)
            rally, march, ok, why = judge(screen, hit, cfg, need)
            last_march[0] = march
            if mon and mon != "hidden" and rally:
                note_rally(serial, mon, rally, mlv)
            if not label:
                label = f"[{show_name(screen, hit, mon)}] "
            if ok and not rotator_for(mon).available():
                r0 = rotator_for(mon)
                wait = r0.seconds_until_free()
                why_b = f"preset {'/'.join(str(p) for p in r0.presets)} still out - free in about {max(1, round(wait / 60))} min" if wait > 60 \
                    else "every preset for this monster is resting"
                act_skip(level_name(screen, hit, mon, mlv), why_b, rally, march)
                if cfg["log_timers"]:
                    say(f"{label}rally {fmt(rally)}{mtxt(march)} -> skip ({why_b})")
                continue
            if cfg["log_timers"]:
                say(f"{label}rally {fmt(rally)}{mtxt(march)}, bot needs ~{need:.0f}s -> "
                    f"{'JOIN' if ok else 'skip'} ({why})")
            if not ok:
                act_skip(level_name(screen, hit, mon, mlv), why, rally, march)
            now_t = time.time()
            same = [x for x in no_open if x[1] > now_t and rally is not None and abs(x[1] - (now_t + rally)) <= 6
                    and x[0] == (mon if mon and mon != "hidden" else show_name(screen, hit, mon))] if ok else []
            # the game itself says what is joined (grey "Joined" button, filtered out above). A rally that did not open is left
            # alone only for the rest of this pass over the list - the next pass tries it again while it still shows "Join".
            seen_before = [x[2] for x in same if x[2] == "joined" or (x[2] == "failed" and x[5] == sweep_no[0])]
            if seen_before:
                if "joined" in seen_before:                   # our own join: already shown as JOINED - not a skip
                    say(f"{label}already joined by this emulator")
                else:
                    why_o = "did not open just now - tried again on the next pass over the list"
                    act_skip(level_name(screen, hit, mon, mlv), why_o, rally, march)
                    say(f"{label}skip ({why_o})")
                continue
            if ok:
                picks.append((rally if rally is not None else 10 ** 6, hit, mon, mlv, march))
        if not picks:
            return None
        rally, hit, mon, mlv, march = min(picks, key=lambda x: x[0])   # least time left that can still be reached
        last_march[0] = march
        if len(picks) > 1 and cfg["log_timers"]:
            say(f"{len(picks)} rallies can be joined - taking the one closing first ({fmt(rally)} left)")
        last_dn[0] = level_name(screen, hit, mon, mlv)
        last_base[0] = mon if mon and mon != "hidden" else show_name(screen, hit, mon)
        return hit, rally, mon

    last_frame = [0.0]

    def emit_frame(img):
        if on_frame and time.time() - last_frame[0] >= 1.0:
            last_frame[0] = time.time()
            try:
                on_frame(serial, img)
            except Exception as _e:
                quiet_err("run_device.emit_frame", _e)

    def wait_state(want, timeout):
        end = time.time() + timeout
        while time.time() < end and not stop.is_set():
            if screen_state(dev.screenshot(), cfg) == want:
                return True
            nap(0.4, 0.6)
        return False

    def reach_city():
        """From the rally list (or wherever we are) to the city: the arrow top left, then Back a few times, always looking first."""
        for i in range(5):
            if stop.is_set():
                return False
            scr = dev.screenshot()
            if screen_state(scr, cfg) == "city":
                return True
            if cancel_quit_dialog(dev, scr, log):
                # the Exit box only opens on the city or the world map, so we are on one of them even if it was not
                # recognised: switch view (world map -> city), and once more if that was the city (city -> world -> city)
                for _t in range(2):
                    dev.tap(*VIEW_TOGGLE)
                    log("[*] Exit box means city or world map - tapped the city/landscape switch")
                    if wait_state("city", 6):
                        return True
                continue
            if i > 0 and world_map(scr):                       # the arrow led to the world map: Back there asks to quit - use the city button
                dev.tap(*VIEW_TOGGLE)
                log("[*] On the world map - tapped the city button")
                if wait_state("city", 8):
                    return True
                continue
            if i == 0:
                dev.tap(*cfg["city_to_tap"])
            elif i == 3:
                dev.tap(*VIEW_TOGGLE)                          # maybe the game shows the landscape view
                log("[*] Not in the city yet - tapped the city/landscape switch")
            else:
                dev.back()
            if wait_state("city", 4):
                return True
        last = dev.screenshot()
        save_debug(serial, last)
        try:                                                   # one picture per instance of the screen it took for "not the city"
            (USER / "debug").mkdir(exist_ok=True)
            cv2.imwrite(str(USER / "debug" / f"city_unknown_{re.sub(r'[^0-9A-Za-z]', '_', serial)}.png"), last)
        except Exception as _e:
            quiet_err("reach_city.picture", _e)
        return False

    city_fails = [0]

    def city_trip_failed(msg):
        """reach_city() gave up. Once: pause city tasks 10 min. Twice in a row: the bot is lost - restart the game
        (closing and reopening Evony fixed it on Renner, 23 Oct) instead of pausing again and again."""
        city_fails[0] += 1
        task_event(serial, "Tasks", False, "could not reach the city screen")
        if city_fails[0] >= 2:
            log(f"[!] Could not reach the city screen {city_fails[0]} times in a row - restarting the game")
            city_fails[0] = 0
            force_restart_game(dev, cfg, log, stop)
            return
        log(msg)
        city_block[0] = time.time() + 600

    def dismiss_popup(screen, x_ok=False):
        if cancel_quit_dialog(dev, screen, log):
            return True
        for name in list_templates("popup_"):
            h = find(screen, name, 0.85)
            if h:
                tap_hit(dev, h)
                log(f"[*] Closed popup ({name})")
                return True
        if x_ok and tap_alliance_war_row(dev, screen, log):
            return True
        if x_ok:                                          # a shop / package page (24 Oct: 'Ariadne's Journey - Purchase Packages',
            try:                                          # opened by a shifted button): leave it with Back, never tap inside it
                if find_text(screen, ["Purchase Packages", "Purchase Package"], region=(0, 300, 540, 200), cutoff=0.8) or \
                        find_text(screen, ["Buy"], region=(120, 860, 300, 90), cutoff=0.9):
                    log("[!] A purchase page is open - leaving it with Back (nothing tapped inside it)")
                    rd_snap(screen, "shop_page")
                    dev.back()
                    return True
            except Exception as _e:
                quiet_err("dismiss_popup", _e)
        if x_ok:                                          # no saved picture: look for the round X button of any pop-up
            cx = find_close_x(screen)
            if cx:
                dev.tap(cx[0][0], cx[0][1])
                log(f"[*] Closed a pop-up with its X button ({cx[0][0]},{cx[0][1]})")
                return True
        return False

    war_streak = [0]                          # 'Alliance War' taps since the rally list was last reached (across recover() calls)
    frozen = {"shot": None, "n": 0, "last": 0.0}   # unchanged picture after our close/tap rounds in a row (frozen game guard)

    def recover():
        """Get back to the rally list: close pop-ups, follow the navigation steps, use Back where it is safe."""
        if getattr(dev, "lag_streak", 0) >= 1:       # the game lagged 2+ min non-stop: restart it instead of carrying on
            log("[!] The game kept lagging for over 2 min")
            dev.lag_streak = 0
            war_streak[0] = 0
            force_restart_game(dev, cfg, log, stop)
            return False
        often = [t for t in getattr(dev, "lag_times", []) if time.time() - t < 600]
        if len(often) >= 8 and time.time() - getattr(dev, "lag_restart_at", 0) > 1800:   # lags again and again (max 1 restart / 30 min)
            log(f"[!] The game lagged {len(often)} times in 10 minutes - restarting Evony")
            dev.lag_times, dev.lag_streak, dev.lag_restart_at = [], 0, time.time()
            war_streak[0] = 0
            force_restart_game(dev, cfg, log, stop)
            return False
        at_bottom[0] = False                  # the list reopens at the top: the next sweep jumps to the bottom first
        outside_taps = [0]
        back_taps = [0]
        mail_backs = [0]
        hidden_taps = [0]
        war_row_taps = [0]
        view_toggles = [0]
        last_toggle = [0.0]
        for _ in range(8):
            if stop.is_set():
                return False
            screen = dev.screenshot()
            st = screen_state(screen, cfg)
            if st == "rally_list":
                war_streak[0] = 0
                return True
            if reclaim_if_kicked(dev, cfg, log, stop, screen):
                return False
            if dismiss_popup(screen, st in ("unknown", "city")):
                nap(1.0, 1.5)
                continue
            if st == "unknown" and 0 < view_toggles[0] < 4 and time.time() - last_toggle[0] < 25:
                view_toggles[0] += 1                           # we just flipped the view and landed on an unknown screen: flip it back (Back would open the quit dialog)
                last_toggle[0] = time.time()
                dev.tap(*VIEW_TOGGLE)
                log("[*] Unknown screen after switching view - tapped the city/landscape switch again")
                nap(2.5, 3.5)
                continue
            if st == "unknown" and war_row_taps[0] < 3:       # the Alliance main page (list: Alliance War / Gift / Treasure ...): tap the 'Alliance War' row
                _row = None
                try:                                           # the words first: the war_button picture can match the wrong spot when the page layout shifts
                    _t = find_text(screen, ["Alliance War"], region=(60, 330, 420, 440), cutoff=0.8)
                    if _t and find_text(screen, ["Alliance Gift", "Alliance Treasure", "Alliance Help"], region=(60, 330, 420, 440), cutoff=0.75):
                        _row = _t
                    if not _row and has_template("war_button"):
                        _row = find(screen, "war_button", 0.7)
                        if _row:
                            _row = (int(_row[0]), int(_row[1]))
                except Exception:
                    _row = None
                if _row and war_streak[0] >= 6:                # tapping the row keeps landing back on the Alliance page: restart the game
                    log(f"[!] Tapped 'Alliance War' {war_streak[0]} times without reaching the rally list")
                    war_streak[0] = 0
                    force_restart_game(dev, cfg, log, stop)
                    return False
                if _row:
                    war_streak[0] += 1
                    war_row_taps[0] += 1
                    dev.tap(int(_row[0]), int(_row[1]))
                    log(f"[*] On the Alliance page - tapped the 'Alliance War' row ({int(_row[0])},{int(_row[1])})")
                    nap(1.5, 2.2)
                    continue
            if st == "unknown" and mail_backs[0] < 4:
                try:
                    in_mail = find_text(screen, ["Mail", "Inbox", "System", "Reports"], region=(60, 0, 420, 130), cutoff=0.8)
                except Exception:
                    in_mail = None
                if in_mail:                                   # Mail (or a mail opened from it): device Back, not taps that open more mails (owner 23 Oct)
                    mail_backs[0] += 1
                    dev.back()
                    log("[*] Recovery: on the Mail page - pressed Back")
                    nap(1.0, 1.5)
                    continue
            if st == "unknown" and back_taps[0] < 2:          # a full page (event, Royal Thief, ...) has the gold Back arrow top left: use it first
                try:
                    hsv = cv2.cvtColor(screen[8:80, 5:75], cv2.COLOR_BGR2HSV)
                    gold = cv2.countNonZero(cv2.inRange(hsv, (12, 110, 150), (32, 255, 255)))
                except Exception:
                    gold = 0
                if gold > 250:
                    back_taps[0] += 1
                    dev.tap(*cfg.get("city_to_tap", (35, 35)))
                    log("[*] Unknown page with a Back arrow - tapped the Back arrow")
                    nap(1.2, 1.8)
                    continue
            if st == "unknown" and hidden_taps[0] < 2 and buttons_hidden(screen):   # bare city/map: the game's buttons are hidden
                hidden_taps[0] += 1
                dev.tap(*GROUND_TAP)
                log("[*] The game's buttons were hidden - tapped the ground to show them")
                nap(1.0, 1.4)
                continue
            if st == "unknown" and outside_taps[0] < 3:       # a dialog we have no picture of: wait until it is steady, then tap beside it
                outside_taps[0] += 1
                prev = screen
                for _w in range(6):
                    nap(0.6, 0.8)
                    cur = dev.screenshot()
                    if float(np.mean(cv2.absdiff(prev[40:920:8, ::8], cur[40:920:8, ::8]))) < 1.5:
                        break
                    prev = cur
                if outside_taps[0] >= 3:                   # third try: the usual X spot (some offer pop-ups ignore taps outside)
                    ox, oy = cfg.get("popup_x_tap") or (477, 141)
                    dev.tap(int(ox), int(oy))
                    log(f"[*] Unknown pop-up - tapping outside did not close it, tapped the usual X spot ({ox},{oy})")
                    nap(1.0, 1.5)
                    continue
                ox, oy = cfg.get("popup_outside_tap") or (8, 480)
                if outside_taps[0] >= 2:                   # second try: the opposite edge
                    ox = 540 - int(ox)
                dev.tap(int(ox), int(oy))
                log(f"[*] Unknown pop-up - waited until it was steady, tapped outside it ({ox},{oy})")
                nap(1.0, 1.5)
                continue
            nav = None
            for step in cfg["rally_nav_steps"]:
                if has_template(step):
                    nav = find(screen, step, 0.85)
                    if nav:
                        break
            if not nav and st in ("city", "unknown"):
                for step in cfg["rally_nav_steps"]:                  # the picture may look a little different (badge, glow): accept a weaker match
                    if has_template(step):
                        nav = find(screen, step, 0.7)
                        if nav:
                            log(f"[i] '{step}' found with a weaker match")
                            break
                if not nav:                                          # last try: read the word 'Alliance' under the button on the right side
                    try:
                        ab = find_text(screen, ["Alliance"], region=(270, 200, 270, 740), cutoff=0.7)
                    except Exception:
                        ab = None
                    if ab:
                        dev.tap(int(ab[0]), max(int(ab[1]) - 38, 0))
                        log(f"[*] Read the word 'Alliance' and tapped the button above it ({int(ab[0])},{int(ab[1]) - 38})")
                        nap(1.5, 2.2)
                        continue
                if not nav:
                    save_debug(serial, screen)
            if st in ("march", "general"):
                dev.back()
                log(f"[*] Stuck on the {st} screen - pressed Back")
            elif nav:
                tap_hit(dev, nav)
                log(f"[*] Tapped '{step}' to reach the rally list")
            elif st == "city" and cfg.get("rally_nav_tap"):
                dev.tap(*cfg["rally_nav_tap"])
                log("[*] Tapped the saved spot to reach the rally list")
            elif st == "city" and outside_taps[0] < 3:       # the navigation button is hidden: probably a card-style pop-up (no X) over the city
                outside_taps[0] += 1
                prev = screen
                for _w in range(6):
                    nap(0.6, 0.8)
                    cur = dev.screenshot()
                    if float(np.mean(cv2.absdiff(prev[40:920:8, ::8], cur[40:920:8, ::8]))) < 1.5:
                        break
                    prev = cur
                if outside_taps[0] >= 3:                   # third try: the usual X spot (some offer pop-ups ignore taps outside)
                    ox, oy = cfg.get("popup_x_tap") or (477, 141)
                    dev.tap(int(ox), int(oy))
                    log(f"[*] Navigation button hidden - tapping outside did not help, tapped the usual X spot ({ox},{oy})")
                    nap(1.0, 1.5)
                    continue
                ox, oy = cfg.get("popup_outside_tap") or (8, 480)
                if outside_taps[0] >= 2:                   # second try: the opposite edge
                    ox = 540 - int(ox)
                dev.tap(int(ox), int(oy))
                log(f"[*] Navigation button hidden - waited until the screen was steady, tapped outside the pop-up ({ox},{oy})")
                nap(1.0, 1.5)
                continue
            elif False and st == "city" and view_toggles[0] < 2:       # (off: in a recognised city this flipped the game to the landscape view and left it there) maybe the game shows the landscape (world map): the round button bottom right switches it
                view_toggles[0] += 1
                last_toggle[0] = time.time()
                dev.tap(*VIEW_TOGGLE)
                log("[*] Navigation button not visible - tapped the city/landscape switch (bottom right)")
                nap(2.5, 3.5)
                continue
            elif st == "city":
                log("[!] In the city but no navigation button is visible - waiting (add the rally_icon picture or "
                    "set the tap position on the Tasks tab; Back could open the quit dialog)")
                nap(5)
                return False
            elif has_template("rally_screen") or has_template("popup_exit_cancel"):
                save_debug(serial, screen)
                dev.back()
                nap(0.8, 1.2)
                again = dev.screenshot()
                if has_template("popup_exit_cancel") and find(again, "popup_exit_cancel", 0.85):
                    tap_hit(dev, find(again, "popup_exit_cancel", 0.85))
                    log("[*] Back opened the quit dialog - cancelled it")
                else:
                    log("[*] Unknown screen - pressed Back (debug screenshot saved)")
            else:
                log("[*] Screen not recognised - waiting (add a rally_screen template to enable auto-Back)")
            nap(1.5, 2.5)
        return False

    def back_to_march():
        for _ in range(3):
            st = screen_state(dev.screenshot(), cfg)
            if st == "march":
                return True
            if st == "general":
                dev.back()
                nap(1.0, 1.5)
            else:
                return False
        return False

    tab_check, tab_logged = [0.0], [False]
    aj_next = [0.0]
    aj_scan = [0.0]                       # last time the rally list was read for monster pictures while the game's Auto-Join joins
    city_last, city_block = {}, [0.0]
    city_why = [0.0]
    city_day = [_game_day()]
    last_watch = time.time()
    off_list = [None]                                         # since when we have not been on the rally list
    perf_at = [time.time()]
    kick_check = [0.0]

    def lost_too_long(on_list):
        if on_list:
            off_list[0] = None
            return False
        if off_list[0] is None:
            off_list[0] = time.time()
        limit = cfg.get("lost_seconds", 0)
        return bool(cfg["auto_launch_game"] and limit and time.time() - off_list[0] > limit)

    log(f"[+] Running: join when rally time left minus march time ({cfg.get('march_guess', 10)}s unless read) "
        f"and the bot's own time is at least {cfg['safety_margin']}s, "
        f"presets {rot.presets} ({cfg['preset_mode']}).")
    if cfg["monster_mode"] != "off":
        log(f"[+] Monster filter: {cfg['monster_mode']} {cfg['monster_list']} "
            f"(unknown monsters: {cfg['monster_unknown']})")

    duty_log_at = [0.0]
    while not stop.is_set():
        if live_settings(cfg, serial, cfg_seen):
            log("[i] Settings changed - using the new ones from now on")
        if time.time() - duty_log_at[0] > 900:                # every 15 min: what the duties are waiting for, so a silent duty is visible
            duty_log_at[0] = time.time()
            try:
                rows = [f"{d['title']}: {duty_status(serial, d['id'], cfg)[1]}" for d in DUTY_LIBRARY if (cfg.get('duties') or {}).get(d['id'], {}).get('on')]
                if rows:
                    log("[i] Duties - " + "; ".join(rows) + ("" if tasks_active(cfg, serial) else "  (not running: tasks off)"))
            except Exception as _e:
                quiet_err("run_device", _e)
        if ctl is not None and not ctl.run.is_set():          # paused from the GUI (only between actions)
            log("[i] Paused")
            while not ctl.run.is_set() and not stop.is_set():
                stop.wait(0.3)
            if stop.is_set():
                break
            log("[i] Resumed")
            continue
        try:
            if auto and time.time() - last_watch > cfg["watchdog_seconds"]:
                last_watch = time.time()
                bring_online(dev, cfg, log, stop)                 # restarts the emulator / game if it died and waits
            screen = dev.screenshot()
            errors = 0
            last_ok[0] = time.time()
            _TL.perf["loops"] += 1
            if cfg["perf_log_seconds"] and time.time() - perf_at[0] > cfg["perf_log_seconds"]:
                perf_at[0] = time.time()
                rep_txt = perf_report()
                if rep_txt:
                    log(rep_txt)
            emit_frame(screen)
            if serial in EMU_RESTART_REQ:                        # Restart emulator asked from the interface
                EMU_RESTART_REQ.discard(serial)
                log("[*] Restart emulator asked - closing and starting the emulator")
                restart_emulator(dev, cfg, log, stop, "asked from the interface")
                continue
            if serial in GAME_RESTART_REQ:                       # Restart game asked from the interface
                GAME_RESTART_REQ.discard(serial)
                log("[*] Restart game asked - closing and opening Evony")
                force_restart_game(dev, cfg, log, stop, must=True)
                continue
            st = screen_state(screen, cfg)
            if st == "rally_list":
                _ON_LIST[serial] = time.time()
            if st != "rally_list" and time.time() - kick_check[0] > 4:
                kick_check[0] = time.time()
                if reclaim_if_kicked(dev, cfg, log, stop, screen):
                    off_list[0] = None
                    still["since"] = None
                    lost_since[0] = None
                    continue
            if st != "rally_list" and dismiss_popup(screen, st in ("unknown", "city")):
                # The game does not react: the same close/tap on a picture that does not change, again and again (Rory
                # 24 Oct 05:05-05:44: 'Alliance War' row tapped 229 times, the game frozen after a kick/kick-back clash).
                # 20 such rounds (~3 min) = restart Evony; a second freeze within 15 min = leave it 10 min, then the emulator.
                same = frozen["shot"] is not None and float(cv2.absdiff(frozen["shot"], screen).mean()) < 2.0
                frozen["shot"] = screen
                frozen["n"] = frozen["n"] + 1 if same else 0
                if frozen["n"] >= 20:
                    frozen["n"] = 0
                    frozen["shot"] = None
                    save_debug(serial, screen)
                    if time.time() - frozen["last"] < 900:
                        log("[!] The game froze again within 15 min - leaving it alone for 10 min, then restarting the emulator")
                        _nap(stop, 600)
                        if stop.is_set():
                            break
                        restart_emulator(dev, cfg, log, stop, "the game froze twice (does not react to taps)")
                    else:
                        log("[!] The game does not react (same screen after 20 taps, ~3 min) - restarting Evony")
                        force_restart_game(dev, cfg, log, stop, must=True)
                    frozen["last"] = time.time()
                    continue
                nap(1.0, 1.5)
                continue
            frozen["n"] = 0

            # Owner 25 Oct: Kiravik/Stalds ran 2 h with the game's Auto-Join expired - their bot joining was off
            # (rally_enabled False) and the renew check only ran with it on. Auto-Join is its own switch: it always
            # needs the rally list (the game joins, the bot only renews), whatever the bot-joining switch says.
            rallies_on = cfg.get("rally_enabled", True) or bool(cfg.get("game_auto_join"))
            if getattr(dev, "acted", None) is None:
                dev.acted = time.time()
            if st == "unknown" and has_template("rally_screen") and time.time() - dev.acted > 90:   # no tap for 90 s on a screen it does not know
                try:
                    (USER / "debug").mkdir(exist_ok=True)
                    cv2.imwrite(str(USER / "debug" / ("idle_" + re.sub(r"\W", "_", serial) + ".png")), screen)
                except Exception as _e:
                    quiet_err("run_device", _e)
                log(f"[!] Nothing to do on this screen for 90s ({st}) - pressing Back")
                dev.acted = time.time()
                dev.back()
                nap(1.5, 2.0)
                continue
            need_recover = st in ("march", "city", "general") and not (st == "city" and not rallies_on)
            if st == "unknown":
                if has_template("rally_screen"):
                    need_recover = True          # we know what the list looks like, so this is wrong
                elif has_template("rally_icon") and find(screen, "rally_icon", 0.85):
                    need_recover = True          # city-style HUD is visible
                else:                            # most likely just an empty rally list
                    unknown += 1
                    if unknown == 10:
                        save_debug(serial, screen)
                        log("[i] Nothing recognised for a while. Saved a screenshot to the 'debug' "
                            "folder. Check it and add a rally_screen template if the bot is lost.")
            else:
                unknown = 0
            now = time.time()
            # --- is the screen frozen? (only where something should be moving: Join buttons / not on the list)
            moving = st in ("march", "city", "general") or need_recover or (st == "rally_list" and _TL.joins_seen)
            if st == "rally_list":                                   # note when this list shows rallies, so a stale (empty) list on another instance can be spotted
                try:
                    _g = cv2.inRange(cv2.cvtColor(screen[150:800:3, ::3], cv2.COLOR_BGR2HSV), (40, 140, 90), (85, 255, 255))
                    if cv2.countNonZero(_g) > 150:
                        _RALLY_SEEN[serial] = now
                        stale_hits[0] = 0
                except Exception as _e:
                    quiet_err("run_device", _e)
            ck = CHAT_STATE.get(serial) or {}
            ck_every = int(cfg.get("chat_check_minutes", 10) or 0) * 60
            if ck_every and st == "rally_list" and not ck.get("off") and now - _RALLY_SEEN.get(serial, 0) > ck_every \
                    and now - ck.get("last", 0) > ck_every and now - t_started[0] > ck_every:
                verdict = chat_war_check(dev, cfg, log, stop, serial)   # no rally on this list for 10 min: is the game really quiet?
                ck = CHAT_STATE[serial]
                if verdict == "broken":
                    ck["broken"] = ck.get("broken", 0) + 1
                    if ck["broken"] > 5:                                # owner: after 5 tries stop restarting for the same case
                        if ck["broken"] == 6:
                            notify(cfg, serial, "Chat check: still broken after 5 restarts (" +
                                   ("not in an alliance" if ck.get("noally") else "rallies in chat never reach the list") + ") - please check this instance.")
                            log("[!] Chat check: 5 restarts did not help - not restarting for this any more, alert sent")
                    else:
                        log(f"[!] Chat check: restarting Evony (try {ck['broken']} of 5)")
                        force_restart_game(dev, cfg, log, stop, must=True)
                else:
                    ck["broken"] = 0
                continue
            if not moving and st in ("rally_list", "unknown"):      # rally countdown bars (green) on the list: their numbers tick every second
                try:
                    hsv = cv2.cvtColor(screen[150:800:3, ::3], cv2.COLOR_BGR2HSV)
                    green = cv2.inRange(hsv, (40, 140, 90), (85, 255, 255))
                    moving = cv2.countNonZero(green) > 150
                except Exception as _e:
                    quiet_err("run_device", _e)
            sig = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
            changed = 9999 if still["sig"] is None else \
                cv2.countNonZero(cv2.threshold(cv2.absdiff(sig, still["sig"]), 10, 255, cv2.THRESH_BINARY)[1])
            # pixel-identical for 5 minutes on any screen (even an empty rally list) = the emulator is not drawing frames
            if changed >= 8 or st not in ("rally_list", "unknown", "march", "city", "general"):
                still["any"] = None
            elif still.get("any") is None:
                still["any"] = now
            if not moving and still.get("any") is not None and now - still["any"] > 200:
                # an empty rally list can stay pixel-identical for many minutes. Before calling it frozen, prove it:
                # tap the Event War tab - a live game redraws the tabs, a frozen emulator does not.
                alive = None
                if st in ("rally_list", "unknown"):
                    try:
                        war_tab_is_event(screen)
                        lt, rt = war_tab_is_event.last
                        if lt > rt + 25:                                   # we really are on Alliance War (PvP tab lit)
                            dev.tap(400, 105)
                            nap(1.2, 1.6)
                            probe = cv2.cvtColor(dev.screenshot(), cv2.COLOR_BGR2GRAY)
                            diff = cv2.countNonZero(cv2.threshold(cv2.absdiff(probe, sig), 10, 255, cv2.THRESH_BINARY)[1])
                            alive = diff > 500
                            if alive:
                                dev.tap(140, 105)                          # back to PvP War
                                nap(1.0, 1.4)
                        if not alive:                                      # not sure where we are (or the tab did not react): press Back
                            dev.back()
                            nap(1.5, 2.0)
                            probe = cv2.cvtColor(dev.screenshot(), cv2.COLOR_BGR2GRAY)
                            diff = cv2.countNonZero(cv2.threshold(cv2.absdiff(probe, sig), 10, 255, cv2.THRESH_BINARY)[1])
                            alive = diff > 500
                            if alive:
                                recover()                                  # the game answered: go back to the rally list
                    except Exception:
                        alive = None
                if alive and st == "rally_list" and others_see_rallies(serial):   # the game answers but its list stays empty while the other instances see rallies: the list is stale
                    stale_hits[0] += 1
                    still["any"], still["since"], still["sig"] = None, None, None
                    if stale_hits[0] >= 2:
                        stale_hits[0] = 0
                        log("[!] The rally list is still empty here while instances in the same alliance see rallies - restarting Evony")
                        force_restart_game(dev, cfg, log, stop)
                    else:
                        log("[!] The rally list is empty here while instances in the same alliance see rallies - reopening it")
                        dev.back()
                        nap(1.5, 2.0)
                        recover()
                    continue
                if alive:
                    log(f"[i] The rally list has been quiet for {(now - still['any']) / 60:.0f} min - checked: the game is responding")
                    still["any"], still["since"], still["sig"] = None, None, None
                    lost_since[0] = None
                    continue
                moving = True
            if not moving or changed >= 8:            # a ticking countdown changes far more than 8 pixels
                still["since"] = None
            elif still["since"] is None:
                still["since"] = now
            still["sig"] = sig
            frozen_for = 0 if still["since"] is None else now - still["since"]
            _lag_s, dev.lag_spent = getattr(dev, "lag_spent", 0.0), 0.0
            if lost_since[0] and _lag_s:
                lost_since[0] += _lag_s                  # time the game lagged does not count as lost (owner 25 Oct)
            lost_since[0] = (lost_since[0] or now) if need_recover else None
            stuck = int(cfg.get("stuck_seconds", 0) or 0)
            if (frozen_for > 20 or (lost_since[0] and now - lost_since[0] > 20)) and now - kick_check[0] > 4:
                kick_check[0] = now                  # a frozen / lost screen may just be the "opened elsewhere" dialog
                if reclaim_if_kicked(dev, cfg, log, stop, screen):
                    off_list[0] = None
                    still["since"] = None
                    lost_since[0] = None
                    continue
            if still.get("any") is not None and now - still["any"] > (900 if st in ("rally_list", "unknown") else 240):   # a quiet, empty rally list is pixel-identical for minutes - not a freeze
                still["any"] = None
                still["since"] = None
                if not emu_restart(f"the screen has not drawn a new frame for {(900 if st in ('rally_list', 'unknown') else 240) // 60} minutes"):
                    return
                continue
            if stuck and frozen_for > stuck:
                if not emu_restart(f"screen frozen for {frozen_for:.0f}s"):
                    return
                continue
            if stuck and lost_since[0] and now - lost_since[0] > stuck:
                if not emu_restart(f"could not get back to the rally list for {now - lost_since[0]:.0f}s"):
                    return
                continue
            fz = int(cfg.get("frozen_seconds", 0) or 0)
            if fz and frozen_for > fz and cfg["auto_launch_game"]:
                freezes[:] = [t for t in freezes if now - t < max(stuck, 600)] + [now]
                if stuck and len(freezes) >= 2:       # restarting Evony did not help
                    freezes.clear()
                    if not emu_restart("the screen froze again after restarting Evony"):
                        return
                    continue
                log(f"[!] The screen has not changed for {frozen_for:.0f}s - restarting Evony")
                notify(cfg, serial, f"Screen frozen for {frozen_for:.0f}s - restarting Evony.")
                still["since"] = None
                force_restart_game(dev, cfg, log, stop, must=True)
                continue
            if lost_too_long(not need_recover):
                if reclaim_if_kicked(dev, cfg, log, stop, dev.screenshot()):      # not lost: kicked by another device
                    off_list[0] = None
                    still["since"] = None
                    lost_since[0] = None
                    continue
                off_list[0] = None
                nowt = time.time()
                lost_restarts[:] = [t for t in lost_restarts if nowt - t < 1800]
                if len(lost_restarts) >= 2:        # restarting Evony twice did not help -> restart the emulator
                    lost_restarts.clear()
                    if not emu_restart("lost for too long, restarting Evony did not help"):
                        return
                    continue
                lost_restarts.append(nowt)
                notify(cfg, serial, "Lost for too long - restarting Evony.")
                force_restart_game(dev, cfg, log, stop)
                continue
            if need_recover:
                unknown = 0
                slower("Landed on the wrong screen")
                recover()
                continue

            if st in ("rally_list", "unknown") and rallies_on and time.time() - tab_check[0] > 5:
                tab_check[0] = time.time()
                is_ev = war_tab_is_event(screen)
                if not tab_logged[0]:
                    tab_logged[0] = True
                    log("[i] War tab brightness (left/right): %.0f / %.0f" % war_tab_is_event.last)
                if is_ev:                      # the Event War tab got opened by mistake
                    log("[*] The Event War tab is open - switching back to PvP War")
                    dev.tap(140, 105)
                    nap(1.0, 1.5)
                    continue
            if cfg.get("game_auto_join") and rallies_on:          # the game joins by itself; we only keep it switched on
                if st == "rally_list":
                    if cfg.get("save_unknown_monsters") and now - aj_scan[0] >= 12:       # picture collector: look at the list, never join
                        aj_scan[0] = now
                        try:
                            for hit in find_all(screen, "join_button", cfg["threshold"], cfg["search_region"]):
                                if identify_monster(screen, hit, cfg)[0] is None:
                                    collect_unknown_monster(screen, hit, cfg, log)
                        except Exception as e:
                            log(f"[i] Picture collector: {e}")
                    if now >= aj_next[0] or AJ_ASK.pop(serial, False):
                        aj_next[0] = now + auto_join_renew(dev, cfg, log, stop, act)
                    elif tasks_active(cfg, serial) and now >= city_block[0] and (due := city_tasks_due(cfg, city_last, serial)):
                        log(f"[*] Auto-Join is running - going to the city for {len(due)} task(s)")      # the game joins rallies by itself meanwhile
                        if reach_city():
                            city_fails[0] = 0
                            run_city_tasks(dev, cfg, log, stop, due, city_last)
                        else:
                            city_trip_failed("[!] Could not reach the city screen - city tasks paused for 10 minutes")
                        recover()
                    else:
                        act("step", text="Auto-Join is on - next check in %d min" % max(1, int((aj_next[0] - now) // 60)))
                        if now - city_why[0] > 300:                       # say why no city trip is made (tasks off / nothing due / blocked)
                            city_why[0] = now
                            nd = len(city_tasks_due(cfg, city_last, serial))
                            log(f"[i] No city trip now: tasks {'on' if cfg.get('city_enabled') else 'OFF'}, {nd} due, blocked for {max(0, int(city_block[0] - now))} s")
                        nap(5, 8)
                    continue
            if not rallies_on:                                    # tasks only: stay in the city and work through them
                due = city_tasks_due(cfg, city_last, serial)
                if st == "city":
                    if due:
                        log(f"[*] Running {len(due)} task(s)")
                        run_city_tasks(dev, cfg, log, stop, due, city_last)
                    else:
                        nap(5, 8)
                    continue
                if st == "rally_list" and due and now >= city_block[0]:
                    if not reach_city():
                        city_trip_failed("[!] Could not reach the city screen - tasks paused for 10 minutes")
                    else:
                        city_fails[0] = 0
                    continue
                nap(5, 8)                                         # rallies are off: never fall through to joining
                continue

            target = find_target(screen)
            if _game_day() != city_day[0]:                       # the game reset: every task starts again
                city_day[0] = _game_day()
                city_last.clear()
                log("[*] The game day reset - all tasks start again")
            bm_first = bool(target) and bm_priority_due(cfg, serial)   # Black Market 'until the gem floor' goes before joining (owner 24 Oct)
            if (not target or run_now_list(serial) or bm_first) and tasks_active(cfg, serial) and st == "rally_list" and now >= city_block[0]:
                due = city_tasks_due(cfg, city_last, serial)
                if due:
                    log(f"[*] {'No rally waiting' if not target else 'Black Market comes first' if bm_first and not run_now_list(serial) else 'A task was started by hand'} - going to the city for {len(due)} task(s)")
                    if reach_city():
                        city_fails[0] = 0
                        run_city_tasks(dev, cfg, log, stop, due, city_last)
                    else:
                        city_trip_failed("[!] Could not reach the city screen - city tasks paused for 10 minutes")
                    recover()
                    continue
            if not target and st == "rally_list":
                # the game sometimes shows Alliance War with an EMPTY list that never refreshes, while rallies are running.
                # Scrolling an empty list does nothing: reopen the page (allies busy -> fast, otherwise every 5 min).
                try:
                    _g = cv2.inRange(cv2.cvtColor(screen[150:800:3, ::3], cv2.COLOR_BGR2HSV), (40, 140, 90), (85, 255, 255))
                    is_empty = cv2.countNonZero(_g) <= 150
                except Exception:
                    is_empty = False
                if not is_empty:
                    empty_list["checks"] = empty_list["reopens"] = 0
                else:
                    empty_list["checks"] += 1
                    t_now = time.time()
                    allies_busy = others_see_rallies(serial)
                    recent_restart = any(t_now - r < 1800 for r in empty_list["restarts"])
                    gap = 45 if allies_busy and not (recent_restart and empty_list["reopens"] >= 3) else 300
                    if empty_list["checks"] >= 3 and t_now - empty_list["last"] >= gap:
                        empty_list["checks"] = 0
                        empty_list["last"] = t_now
                        empty_list["reopens"] += 1
                        at_bottom[0], scrolls = False, 0
                        if allies_busy and empty_list["reopens"] >= 3 and \
                                list_restart("The rally list stays empty after reopening it while other instances see rallies"):
                            empty_list["reopens"] = 0
                        else:
                            log("[!] The rally list is empty" + (" while other instances see rallies" if allies_busy else "")
                                + " - reopening Alliance War to refresh it")
                            dev.back()
                            nap(1.5, 2.0)
                            recover()
                        continue
            if not target:
                # the list is read from the BOTTOM up: the lowest rallies close first, the top ones were just started.
                # One sweep = jump to the bottom, then one page up at a time until the top.
                max_sc = cfg["max_scrolls"]
                x1, y1, x2, y2, ms = cfg["swipe"]
                if not max_sc:
                    sweep_no[0] += 1                           # no scrolling: every look at the list is a new pass
                if max_sc and not at_bottom[0]:
                    for _ in range(max_sc):                    # down to the end of the list (the oldest rallies)
                        dev.swipe(x1, y1, x2, y2, ms)
                        nap(0.3, 0.5)
                    at_bottom[0] = True
                    sweep_no[0] += 1
                    scrolls = 0
                    act("step", text="Looking for a rally to join (from the bottom)")
                    nap(0.6, 0.9)
                    continue
                if (not max_sc or scrolls >= max_sc) and st == "rally_list":
                    # a full look at the list is done: does it miss rallies that other instances in the alliance see?
                    # The game sometimes stops updating the list (old rallies keep counting down, new ones never appear).
                    t_now = time.time()
                    miss = missed_rallies(serial, cfg)
                    if not miss:
                        empty_list["stale"] = 0
                    elif t_now - empty_list["last"] >= (300 if any(t_now - r < 1800 for r in empty_list["restarts"]) else 60):
                        empty_list["last"] = t_now
                        empty_list["stale"] += 1
                        at_bottom[0], scrolls = False, 0
                        names = ", ".join(sorted({m for m, _ in miss}))
                        if empty_list["stale"] >= 3 and list_restart(f"The rally list still misses {names} after reopening it"):
                            empty_list["stale"] = 0
                        else:
                            log(f"[!] The rally list does not show {len(miss)} rally(s) other instances see ({names}) - reopening Alliance War")
                            dev.back()
                            nap(1.5, 2.0)
                            recover()
                        continue
                if max_sc and scrolls >= max_sc:
                    log("[*] Top of the list - every rally checked")
                    at_bottom[0] = False                       # next sweep starts at the bottom again
                    scrolls = 0
                    act("step", text="Idle - nothing to join, waiting for a new rally")
                    nap(3, 5)
                else:
                    act("step", text="Looking for a rally to join")
                    log("[*] No rally to join here, scrolling up")
                    dev.swipe(x1, y2, x2, y1, ms)
                    scrolls += 1
                lo, hi = cfg["poll_seconds"]
                nap(lo * _TL.cpu, hi * _TL.cpu)                # CPU level: longer pause = lighter
                continue
            scrolls = 0
            at_bottom[0] = False                              # after joining, the next look starts at the bottom again
            hit, left, mon = target

            used = rotator_for(mon)
            if not used.available():
                wait = used.seconds_until_free()
                log(f"[!] All presets are unavailable, retrying in ~{min(max(wait, 1), 5):.0f}s")
                nap(min(max(wait, 1), 5))                  # rallies last ~5 min: keep looking, never sleep long
                continue

            shown = last_base[0] if last_base[0] and last_base[0] != "?" else (mon if mon and mon != "hidden" else "")
            vs = f" vs {shown}" if shown else ""
            log(f"[*] Rally{vs} with {left}s left -> joining")
            t_join = time.time()
            t_march = None
            walk = last_march[0] if last_march[0] is not None else int(cfg.get("march_guess", 10) or 0)
            t_deadline = t_join + left - walk - 2                     # latest moment to press March and still arrive in time
            mon_name = last_dn[0] or (mon if mon and mon != "hidden" else "?")
            base_name = last_base[0] or "?"

            # a rally closing soon has no second chance: stay on it (try again straight away) until it is too late.
            # One with plenty of time can wait: after 30 s without a march the others go first, back on the next pass.
            priority = left <= 90

            def fail(why):
                if priority and time.time() < t_deadline:
                    act("fail", mon=mon_name, rally=left, took=round(time.time() - t_join, 1),
                        why=why + " - closing soon, trying again straight away")
                    return                                     # not set aside: the next look picks it again first
                act("fail", mon=mon_name, rally=left, took=round(time.time() - t_join, 1),
                    why=why + " - the other rallies first, this one is tried again on the next pass")
                remember_rally(base_name, t_join + left, "failed")      # never given up: retried every pass while it shows "Join"
            act("step", text=f"Joining {mon_name} rally ({left}s left)")
            tap_hit(dev, hit)
            nap(1.0, 1.5)

            if has_template("march_screen") and not wait_state("march", 3):
                try:                                           # one more try at once when the same green Join is still there (a tap that did not register)
                    sh2 = dev.screenshot()
                    again = [h2 for h2 in find_all(sh2, "join_button", cfg["threshold"], cfg["search_region"])
                             if abs(h2[1] - hit[1]) <= 30 and not joined_button(sh2, h2)]
                except Exception:
                    again = []
                if again:
                    log("[i] The rally is still shown with Join - tapping it once more")
                    tap_hit(dev, min(again, key=lambda h2: abs(h2[1] - hit[1])))
                    nap(1.0, 1.5)
                    if wait_state("march", 3):
                        again = "ok"
                if again != "ok":
                    log("[!] March screen did not open (rally ended?) - skipping")
                    fail("March screen did not open (rally ended?)")
                    slower("The march screen did not open")
                    continue

            t_open = time.time()                               # join speed: the march screen is open
            # Take the next preset in the rotation. A preset that has no general (or does not march) is
            # skipped and the next preset is tried straight away, on the same march screen.
            preset, plain_fails, gave_up = None, [], False
            tried, time_skips = set(), 0
            for _ in range(len(used.presets) * 2):
                cand = used.next()
                if cand is None or len(tried) >= len(used.presets):
                    break
                if cand in tried:                              # 'fixed' mode hands out the first preset again: take the next unused one
                    rest = [p for p in used.presets if p not in tried and used.bad.get(p, 0) <= time.time()]
                    if not rest:
                        break
                    cand = rest[0]
                if not priority and time.time() - t_join > 30:  # plenty of time left: let the other rallies go first
                    log("[*] 30 s on this rally without marching - moving on to the other rallies, back to this one on the next pass")
                    break
                tried.add(cand)
                log(f"[*] Trying preset {cand}")
                act("step", text=f"Trying preset {cand}")
                t_preset = time.time()
                slow = preset_picks(cfg, cand)
                est = (lat_pick[0] if lat_pick[0] is not None else 35.0) if slow else 0.0
                if slow and time.time() + est > t_deadline:        # the general search would not finish in time
                    log(f"[!] Preset {cand} needs ~{est:.0f}s to pick generals but only {max(t_deadline - time.time(), 0):.0f}s are left - skipped")
                    time_skips += 1
                    continue
                dev.tap(*preset_xy(cfg, cand))
                nap(0.6, 1.0)
                pcfg, skip_empty = preset_cfg(cfg, cand)
                picks_generals = bool(pcfg["general_main"] or pcfg["general_assistant"])
                if pcfg.get("use_preset_generals"):        # march with whatever generals the game preset already holds
                    picks_generals = False                     # (the preset's own 'skip if no general' choice still applies)
                # 'Skip if no general': an empty general slot means this preset is passed for THIS rally and the next
                # one is tried at once (no resting - it is tried again on the next rally). A 'Listed' preset is never
                # skipped for an empty slot: the bot puts a general from its list in.
                if skip_empty and not picks_generals and not slot_has_general(dev.screenshot(), "main", pcfg):
                    out = back_at.get(cand, 0) > time.time()
                    log(f"[!] Preset {cand} has no general" + (" (its general is probably still out on the last march)" if out else "")
                        + " - trying the next preset")
                    continue
                if pcfg.get("max_troops"):
                    tr = troops_in_march(dev.screenshot())
                    for _again in range(2):                    # the number can still be the old one while the preset loads: look again before judging
                        if tr is None or tr <= pcfg["max_troops"]:
                            break
                        nap(0.8, 1.1)
                        tr = troops_in_march(dev.screenshot())
                    if tr is not None and tr > pcfg["max_troops"]:
                        log(f"[!] Preset {cand} already has {tr:,} troops (limit {pcfg['max_troops']}) - skipped")
                        used.mark_bad(cand, cfg["bad_preset_seconds"])
                        continue
                if picks_generals:
                    act("step", text="Choosing generals")
                    if not set_generals(dev, pcfg, log, stop):
                        # the listed general could not be set: never drop the whole rally for that.
                        if back_to_march():
                            if slot_has_general(dev.screenshot(), "main", pcfg):
                                log(f"[!] Could not set the listed general - marching with the general already in preset {cand}")
                            elif cfg["general_on_fail"] == "march":
                                log(f"[!] Could not set the listed general and preset {cand} is empty - marching anyway (setting: march)")
                            else:
                                log(f"[!] Could not set the listed general and preset {cand} is empty - trying the next preset")
                                continue
                        else:
                            log("[!] General selection failed and the march screen is gone - skipping this rally")
                            fail("General selection failed")
                            recover()
                            gave_up = True
                            break
                if cfg.get("stamina_auto", True):
                    act("step", text="Checking stamina")
                    sta = read_stamina(dev.screenshot())
                    if sta and sta[0] < sta[1]:
                        log(f"[!] Not enough stamina ({sta[0]:,} of {sta[1]:,}) - using stamina items")
                        if refill_stamina(dev, cfg, log, stop, sta[1]):
                            if ctl is not None:
                                ctl.no_stamina = 0           # refilled: the "no stamina" chip must go away at once
                        else:
                            log("[!] Could not get enough stamina - skipping this rally")
                            if ctl is not None:
                                ctl.no_stamina += 1
                                if ctl.no_stamina == 3:
                                    log("[!] NO STAMINA - this emulator is out of stamina (and stamina items). It keeps trying, but it cannot join until it has stamina.")
                                    notify(cfg, serial, "Out of stamina - cannot join rallies until it recovers (add stamina or items).")
                            fail("Not enough stamina")
                            slower("Not enough stamina")
                            recover()
                            gave_up = True
                            break
                if time.time() > t_deadline + 2:
                    log("[!] Out of time - the troops would arrive after the rally starts - going back")
                    fail("Ran out of time")
                    gave_up = True
                    if screen_state(dev.screenshot(), cfg) == "march":
                        dev.back()
                        nap(1, 2)
                    break
                act("step", text=f"Marching with preset {cand}")
                dev.tap(*cfg["march_tap"])
                t_march = time.time()
                nap(1.5, 2.0)
                if cfg["verify_march"]:
                    if has_template("march_screen"):
                        if screen_state(dev.screenshot(), cfg) == "march":
                            log(f"[!] Preset {cand} did not march (empty or busy) - trying the next preset")
                            slower(f"Preset {cand} did not march")
                            used.mark_bad(cand, cfg["bad_preset_seconds"])
                            plain_fails.append(cand)
                            continue
                    elif not warned_verify:
                        log("[i] templates/march_screen.png missing - cannot verify that the march started")
                        warned_verify = True
                preset = cand
                break
            if gave_up:
                continue
            if preset is None:
                for p in plain_fails:                      # every preset failed: the rally is probably
                    used.bad.pop(p, None)                  # full or over, so do not bench them
                log("[!] No preset could march - going back")
                fail("No preset could march")
                if screen_state(dev.screenshot(), cfg) == "march":
                    dev.back()
                    nap(1, 2)
                continue


            log(f"[+] Preset {preset} marched")
            if t_march:                                        # join speed: where the seconds went (to find the slow step)
                _o, _m = t_open - t_join, t_march - t_open
                log(f"[i] Join timing: march screen {_o:.1f}s + preset/checks {_m:.1f}s = {t_march - t_join:.1f}s")
            mt = last_march[0] or 0
            back_at[preset] = time.time() + max(left, 0) + 2 * (mt if mt else 90) + 60   # march time unknown: count 90 s each way
            if t_march:
                lat = t_march - t_join
                if preset is not None and preset_picks(cfg, preset):      # keep the slow general search apart from the base time
                    pk = max(t_march - t_preset, 0.0)
                    lat_pick[0] = pk if lat_pick[0] is None else 0.6 * lat_pick[0] + 0.4 * pk
                    lat = max(lat - pk, 1.0)
                latency[0] = lat if latency[0] is None else 0.6 * latency[0] + 0.4 * lat
            nap(1.5, 2.5)
            recover()

            ok = True
            for name in steps:
                end, h2 = time.time() + cfg["step_timeout"], None
                while time.time() < end and not stop.is_set():
                    nap(0.4, 0.8)
                    h2 = find(dev.screenshot(), name, cfg["threshold"])
                    if h2:
                        break
                if not h2:
                    log(f"[!] '{name}' not found, going back")
                    fail(f"'{name}' not found after marching")
                    dev.back()
                    ok = False
                    break
                tap_hit(dev, h2)
                nap(0.5, 1.0)

            if ok:
                joins += 1
                if ctl is not None:
                    ctl.no_stamina = 0
                remember_rally(base_name, t_join + left, "joined")        # joined: never try this same rally again
                speed_up()
                log(f"[+] Joined rally #{joins}")
                act("join", mon=mon_name, rally=left, took=round(time.time() - t_join, 1), preset=preset, n=joins)
                notify(cfg, serial, f"Joined rally #{joins}", "join")
                if cfg["max_joins"] and joins >= cfg["max_joins"]:
                    log("[+] Reached max joins, stopping this instance.")
                    stop.set()                               # a wanted stop: the instance must not start again by itself
                    return
                nap(*cfg["cooldown_after_join"])
            else:
                nap(1, 2)
        except Exception as e:
            errors += 1
            log(f"[!] {e}")
            dead = time.time() - last_ok[0]
            if cfg.get("stuck_seconds") and dead > cfg["stuck_seconds"] and auto:
                if not emu_restart(f"no answer from the emulator for {dead:.0f}s"):
                    return
                errors = 0
                continue
            if auto and errors in (3, 8):                      # the emulator or the game probably closed
                try:
                    bring_online(dev, cfg, log, stop)
                except Exception as e3:
                    log(f"[!] {e3}")
                continue
            if errors >= 15:
                log("[!] Too many errors in a row - restarting the emulator.")
                notify(cfg, serial, "Too many errors in a row - restarting the emulator.")
                return
            if errors % 5 == 0:
                try:
                    dev.connect()
                    log("[*] Reconnected to ADB")
                except Exception as e2:
                    log(f"[!] Reconnect failed: {e2}")
            else:
                try:
                    recover()
                except Exception as _e:
                    quiet_err("run_device", _e)
            nap(3)

    log("[+] Stopped.")


# ---------------------------------------------------------------------- bot
def prepare(cfg, log):
    """Check ADB, OCR and the Join template. -> adb path or None (the problem is logged)."""
    adb = find_adb(cfg["adb_path"])
    if not adb:
        log("[!] ADB not found. Install your emulator or set the ADB path in settings.")
        return None
    if not setup_tesseract():
        log("[!] Tesseract OCR not found (it should be bundled with the app).")
        return None
    if not has_template("join_button"):
        log("[!] Join button image missing (templates/join_button.png).")
        return None
    return adb


INSTANCE_GUARD = None        # set by the sold edition: callable(serial) -> bool


HEARTBEAT = {}            # serial -> last time the instance wrote a log line
QUIET_UNTIL = {}          # serial -> end of a planned long wait (cool-down) the watchdog must not count as stuck


def start_instance(serial, adb, cfg, log, ctl, on_frame=None, on_end=None):
    """Start (or restart) one emulator's bot in its own thread. ctl = DeviceControl for pause/stop."""
    if INSTANCE_GUARD is not None and not INSTANCE_GUARD(serial):
        log("[!] License does not allow this emulator.")
        return None
    ctl.reset()
    _log0 = log

    def log(msg, *a, **k):                                   # every log line = this instance is alive (see watchdog below)
        HEARTBEAT[serial] = time.time()
        return _log0(msg, *a, **k)
    HEARTBEAT[serial] = time.time()

    def watchdog():
        """Owner 25 Oct: an instance must never sit stuck with no emulator restart. No log line for no_progress_seconds
        (default 5 min) while running and not in a planned wait -> close its emulator by force from outside. The stuck
        bot thread then gets errors from ADB and its normal recovery restarts the emulator and the game."""
        last_force = 0.0
        while not ctl.stop.wait(20):
            now = time.time()
            if not ctl.run.is_set():                          # paused: not stuck
                HEARTBEAT[serial] = now
                continue
            limit = int(merge_device_cfg(cfg, serial).get("no_progress_seconds", 300) or 0)
            idle = now - max(HEARTBEAT.get(serial, now), QUIET_UNTIL.get(serial, 0))
            if not limit or idle < limit or now - last_force < 600:
                continue
            last_force = now
            log(f"[!] No progress for {idle / 60:.0f} min - force restarting the emulator")
            try:
                mcfg = merge_device_cfg(cfg, serial)
                notify(mcfg, serial, f"Stuck with no progress for {idle / 60:.0f} min - force restarting the emulator.")
                cmd = emulator_stop_command(serial, adb, mcfg)
                if cmd:
                    subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, timeout=40, creationflags=NOWIN)
                kill_listener(serial, log)
            except Exception as e:
                log(f"[!] Force restart did not work: {e}")
    threading.Thread(target=watchdog, daemon=True).start()

    def work():
        try:
            fails = 0
            while not ctl.stop.is_set():
                # Owner rule (24 Oct): an instance never stays stopped with "Problem - see the log". Whenever the bot loop
                # ends by itself (could not connect, gave up, crashed), restart the emulator and start again - waiting a
                # little longer each time (1, 2, 3 ... 10 min) so a broken emulator cannot hammer the PC.
                t_run = time.time()
                try:
                    run_device(serial, adb, cfg, log, ctl.stop, on_frame, ctl)
                    if ctl.stop.is_set():
                        break
                    why = "the bot stopped by itself"
                except Exception as e:                       # e.g. the PC ran out of memory for a moment: do not die silently
                    log(f"[!] {e}")
                    if ctl.stop.is_set():
                        break
                    why = f"it crashed ({type(e).__name__})"
                fails = 1 if time.time() - t_run > 1800 else fails + 1     # a long good run starts the count again
                import gc
                gc.collect()
                log(f"[!] This instance stopped ({why}) - restarting the emulator and starting again (try {fails})")
                try:
                    restart_emulator(Device(serial, adb), merge_device_cfg(cfg, serial), log, ctl.stop, why)
                except Exception as e2:
                    log(f"[!] Emulator restart did not work ({e2}) - trying again later")
                QUIET_UNTIL[serial] = time.time() + min(600, 60 * fails)      # planned wait: not stuck
                if ctl.stop.wait(min(600, 60 * fails)):
                    break
            merged = merge_device_cfg(cfg, serial)
            if merged.get("close_emulator_on_stop") and ctl.stop.is_set():      # stopped on purpose: free its RAM
                close_emulator(Device(serial, adb), merged, log)
        except Exception as e:
            log(f"[!] {e}")
        finally:
            if on_end:
                on_end(serial)

    t = threading.Thread(target=work, daemon=True)
    t.start()
    return t


def run_bot(cfg, log=print, stop=None, on_frame=None, device_log=None, controls=None):
    """Runs one thread per device. Blocks until all finish or `stop` is set."""
    stop = stop or threading.Event()
    controls = controls if controls is not None else {}
    log_lock = threading.Lock()

    def safe_log(msg):
        with log_lock:
            log(msg)

    adb = prepare(cfg, safe_log)
    if not adb:
        return
    serials = resolve_devices(cfg, adb, safe_log)
    if not serials:
        return safe_log("[!] No emulator found. Start it, enable ADB, or set device IDs in settings.")

    def make_log(serial):
        if device_log:
            return lambda m: device_log(serial, m)
        if len(serials) == 1:
            return safe_log
        tag = cfg.get("device_names", {}).get(serial) or (serial.split(":")[-1] if ":" in serial else serial)
        return lambda m: safe_log(f"[{tag}] {m}")

    threads = []
    for s in serials:
        threads.append(start_instance(s, adb, cfg, make_log(s), controls.setdefault(s, DeviceControl()), on_frame))
        stop.wait(random.uniform(0.5, 1.5))          # stagger so instances don't act in lockstep
    try:
        while not stop.is_set() and any(t.is_alive() for t in threads):
            stop.wait(0.5)
    finally:
        stop.set()
        for c in controls.values():
            c.stop.set()
        for t in threads:
            t.join(5)
    safe_log("[+] All instances stopped.")


if __name__ == "__main__":
    cfg = load_config()
    setup_tesseract()
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "devices":
        adb = find_adb(cfg["adb_path"])
        print("ADB:", adb)
        print("Devices:", detect_devices(adb) or "none found")
    elif cmd == "test":
        img = cv2.imread(sys.argv[2] if len(sys.argv) > 2 else str(RES / "templates" / "_screen.png"))
        if img is None:
            sys.exit("Could not read image.")
        if img.shape[1::-1] != EXPECTED_SIZE:
            img = cv2.resize(img, EXPECTED_SIZE, interpolation=cv2.INTER_AREA)
        print("Screen state:", screen_state(img, cfg))
        dbg = USER / "debug"
        dbg.mkdir(exist_ok=True)
        hits = find_all(img, "join_button", cfg["threshold"], cfg["search_region"])
        for n, hit in enumerate(hits, 1):
            for src in TIMER_BOXES:                      # what the OCR actually looks at
                crop = timer_crop(img, hit, src)
                if crop.size:
                    cv2.imwrite(str(dbg / f"crop_{n}_{src}.png"), crop)
        for x, y, sc, rally, march, ok, why, mon in analyze(img, cfg):
            print(f"Join at ({x},{y}) score={sc:.2f} monster={mon} rally={rally}s march={march}s -> "
                  f"{'WOULD JOIN' if ok else 'skip'} ({why})")
        if hits:
            print(f"Timer crops saved in {dbg}  (crop_N_progress_bar.png / crop_N_join_button.png)")
    elif cmd == "generals":
        print("General pictures:", ", ".join(list_generals(refresh=True)) or "none yet")
    elif cmd == "tabs" and len(sys.argv) == 3:
        shot = read_screenshot(sys.argv[2])
        k = selected_tab(shot)
        print("Selected tab:", k, "| tab centres:", tab_centres(k) if k else "unknown")
        print("Favourites filter on:", favourite_on(shot))
    elif cmd == "monsters":
        print("Monster pictures:", ", ".join(list_monsters(refresh=True)) or "none yet")
    elif cmd == "addmonster" and len(sys.argv) == 5:
        try:
            print("Saved", add_monster(sys.argv[2], sys.argv[3], int(sys.argv[4]), cfg))
        except ValueError as e:
            sys.exit(str(e))
    elif cmd == "crop" and len(sys.argv) == 8:
        name, src = sys.argv[2], sys.argv[3]
        x, y, w, h = map(int, sys.argv[4:8])
        img = cv2.imread(src)
        if img is None:
            sys.exit("Could not read image.")
        if img.shape[1::-1] != EXPECTED_SIZE:
            img = cv2.resize(img, EXPECTED_SIZE, interpolation=cv2.INTER_AREA)
        save_template(name, img[y:y + h, x:x + w])
        print(f"Saved {USER / 'templates' / (name + '.png')}")
    elif cmd == "run":
        try:
            run_bot(cfg)
        except KeyboardInterrupt:
            pass
    else:
        print(__doc__)
