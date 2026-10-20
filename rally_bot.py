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
    pass
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
VERSION = "2.4.60"                       # shown in the window; the updater compares this with the published version
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
    "settle_seconds": 8,                    # after the game looks loaded: wait this long (and until the screen is calm) before any task
    "watchdog_seconds": 60,
    # generals: names = pictures in the generals folder, "" = keep whatever the preset set
    "general_main": [],                     # a list, best first: the bot tries the next one if a general is not available
    "popup_outside_tap": [8, 480],          # where to tap to close an unknown dialog (outside it, left edge)
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
    "market_refresh_mode": "none",          # none | count | all
    "market_refresh_n": 5,
    "market_paid_refresh": False,           # refresh with gems when the free refreshes are used up
    "market_max_gems": 0,                   # most gems to spend in one visit (0 = no limit; required for paid refreshes)
    "market_refresh_cost": 50,              # gems a paid refresh costs if it cannot be read
    "market_match": 0.78,
    "tg_api_id": "",                        # from https://my.telegram.org -> API development tools
    "tg_api_hash": "",
    "tg_phone": "",                         # your number with country code, e.g. +911234567890
    "tg_boss_bot": "@EvonyBossBot",         # the bot whose answers hold the boss coordinates
    "tg_boss_command": "",                  # text sent to it to ask for bosses (as you type it in the chat)
    "tg_auto_min": 0,                       # ask again every N minutes (0 = only when you press the button)
    "tg_min_level": 0,                      # ignore bosses below this level when a task uses {boss_x} {boss_y}
    "tg_pick": "nearest",                   # nearest | highest
    "sidebar_open": True,                   # emulator list on the left (toggle with the header button, Ctrl+B or F9)
    "ui_pal": "amber",                      # amber | sage
    "ui_mode": "dark",                      # dark | light | auto
    "ui_accent": "",                        # "" = the theme's own colour, else #rrggbb
    "ui_btn": "raised", "ui_border": "subtle", "ui_round": "normal",   # raised|tinted, subtle|accent, sharp|normal|round
    "ui_text": 100,                         # whole-window text size in percent (85-150)
    "ui_name": 24,                          # emulator name heading size in px (18-40)
    "rally_enabled": True,                  # join rallies (first priority); off = the bot only does the tasks
    "game_reset_utc": "00:00",               # when the game's daily reset happens (UTC, HH:MM); daily duties start again then
    "tasks_first": True,                    # a task/duty that is due (e.g. Crazy Eggs timer ended) goes before rallies; the bot comes back afterwards
    "city_enabled": False,                  # run the tasks (Tasks tab) while no rally is waiting
    "city_tasks": [],                       # [{name, enabled, target, steps, every_min}], see run_step()
    "duties": {},                           # {duty_id: {on, every_min}} - ready-made duties, see DUTY_LIBRARY
    "wheel": {"button": 1, "spins": 10, "reserve": 0},   # Tavern > Wheel of Fortune: biggest button (1/10/100), spins to do (0 = until the coins are used up), coins to keep
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


# ------------------------------------------------------------------ log files
LOG_DIR = USER / "logs"
_log_lock = threading.Lock()
_SECRET_KEYS = ("telegram_token", "telegram_chat", "discord_webhook", "update_key", "tg_api_id", "tg_api_hash",
                "tg_phone")


def log_to_file(line):
    """Append a line to today's log file (kept: the last 5 days)."""
    try:
        with _log_lock:
            LOG_DIR.mkdir(exist_ok=True)
            f = LOG_DIR / f"bot_{time.strftime('%Y%m%d')}.log"
            new = not f.exists()
            with open(f, "a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%H:%M:%S')} {line}\n")
            if new:
                for old in sorted(LOG_DIR.glob("bot_*.log"))[:-5]:
                    old.unlink(missing_ok=True)
    except Exception:
        pass


def recent_log(max_lines=2000):
    try:
        files = sorted(LOG_DIR.glob("bot_*.log"))
        lines = []
        for f in files[-3:]:
            lines += f.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-max_lines:])
    except Exception:
        return ""


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
    except Exception:
        pass
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
    except Exception:
        pass
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
    except Exception:
        pass
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
            except Exception:
                pass
    set_game_reset(cfg)
    return migrate_cfg(cfg)


def save_config(cfg):
    CONFIG.write_text(json.dumps(cfg, indent=2))
    set_game_reset(cfg)


_RESET_MIN = [0]                          # minutes after 00:00 UTC at which the game day changes


def set_game_reset(cfg):
    m = re.fullmatch(r"\s*([01]?\d|2[0-3]):([0-5]\d)\s*", str((cfg or {}).get("game_reset_utc") or "00:00"))
    _RESET_MIN[0] = int(m.group(1)) * 60 + int(m.group(2)) if m else 0


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
    except Exception:
        pass
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
                            except OSError:
                                pass
                except OSError:
                    pass
    except Exception:
        pass
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
    except Exception:
        pass
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
        except Exception:
            pass
    ports |= {5555, 5565, 5575, 5585, 5595}               # BlueStacks / generic
    ports |= {62001, 62025, 62026, 62027}                 # Nox
    ports |= {21503 + 10 * i for i in range(6)}           # MEmu
    ports |= {7555} | {16384 + 32 * i for i in range(8)}  # MuMu
    # LDPlayer registers itself as emulator-5554, 5556... and shows up in `adb devices` by itself.
    return sorted(ports)


def _try_connect(adb, port):
    try:
        subprocess.run([adb, "connect", f"127.0.0.1:{port}"], capture_output=True,
                       creationflags=NOWIN, timeout=4)
    except Exception:
        pass


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
            else:
                self._guard_unknown = 0
        except Exception:
            pass
        finally:
            self._guard_busy = False
        return img

    def _run(self, *a, timeout=20):
        if len(a) > 1 and a[0] == "shell" and a[1] == "input":      # a tap / swipe / text: never while the instance is paused
            pause_gate()
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
        return img

    def tap(self, x, y):
        self._run("shell", "input", "tap", str(int(x * self.sx)), str(int(y * self.sy)))

    def swipe(self, x1, y1, x2, y2, ms=300):
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
MONSTER_CATALOG = [["Normal monsters",[["Zombie",[[1,"36.5K"]]],["Redcap",[[2,"219K"]]],["Centaur",[[3,"328.5K"]]],["Skeleton Dragon",[[4,"787.2K"]]],["Werewolf",[[5,"1.3M"]]],["Manticore",[[6,"2.2M"]]],["Yasha",[[7,"3.4M"]]],["Peryton",[[8,"6.5M"]]],["Minotaur",[[9,"9.9M"]]],["Griffin",[[10,"17.9M"]]],["Ifrit",[[11,"59.6M"]]],["Kamaitachi",[[12,"89.4M"]]],["Fafnir",[[13,"134.1M"]]],["Behemoth",[[14,"187.7M"]]],["Phoenix",[[15,"262.7M"]]],["Jormungandr",[[16,"394.1M"]]],["Typhon",[[17,"551.8M"]]],["Ammit",[[18,"800.1M"]]],["Stymphalian Bird",[[19,"1.1B"]]],["Kraken",[[20,"1.6B"]]],["Azazel",[[21,"2.6B"]]],["Leviathan",[[22,"3.3B"]]],["Garmr",[[23,"4.7B"]]]]],["Event monsters",[["Lava Turtle",[[1,"12.4M"],[2,"22.3M"],[3,"74.5M"],[4,"147.5M"],[5,"225.2M"],[6,"668.1M"],[7,"1.5B"]]],["Golem",[[1,"12.4M"],[2,"22.3M"],[3,"74.5M"],[4,"147.5M"],[5,"225.2M"],[6,"668.1M"],[7,"1.5B"]]],["Witch",[[1,"13M"],[2,"24.6M"],[3,"85.6M"],[4,"154.8M"],[5,"225.2M"],[6,"668.1M"],[7,"1.5B"]]],["Warlord",[[1,"13M"],[2,"24.6M"],[3,"85.6M"],[4,"154.8M"],[5,"225.2M"],[6,"668.1M"],[7,"1.5B"]]],["Ymir",[[1,"23.4M"],[2,"70.8M"],[3,"120.2M"],[4,"206.4M"],[5,"361.3M"],[6,"722.6M"],[7,"1.8B"]]],["Sphinx",[[1,"12.4M"],[2,"22.3M"],[3,"74.5M"],[4,"147.5M"],[5,"225.2M"],[6,"472.9M"],[7,"914.4M"],[8,"2B"]]],["Pan",[[1,"22.3M"],[2,"42.8M"],[3,"122.9M"],[4,"206.4M"],[5,"361.3M"],[6,"711.7M"],[7,"1.8B"]]],["Hydra",[[1,"84.9M"],[2,"120.6M"],[3,"198.9M"],[4,"328.4M"],[5,"678.8M"],[6,"1.5B"]]],["Knight Bayard",[[1,"65.5M"],[2,"147.5M"],[3,"225.2M"],[4,"668.1M"],[5,"1.5B"]]],["Cerberus",[[1,"68.2M"],[2,"153.4M"],[3,"234.2M"],[4,"694.9M"],[5,"1.5B"]]],["Aglaope",[[1,"120.2M"]]]]],["Limited-time event",[["Pumpkin Monster",[[1,"708.5K"],[2,"1.9M"],[3,"5.9M"],[4,"16.1M"],[5,"53.6M"],[6,"80.4M"],[7,"134.1M"],[8,"268.1M"]]],["Nian",[[1,"59.6M"],[2,"71.5M"],[3,"89.4M"],[4,"134.1M"],[5,"236.5M"],[6,"472.9M"]]],["Cranky Snowman",[[4,"1.6M"],[5,"2.7M"],[6,"3.5M"],[7,"11M"]]]]]]   # [category, [[name, [[level, power], ...]], ...]] from evonyguidewiki.com
_SUMMONED = [["Fenrir",[[1,"27.4K"],[2,"186.1K"],[3,"492.7K"]]],["Garuda",[[1,"1.3M"],[2,"1.7M"],[3,"125M"]]],["Achelois",[[1,"17.9M"],[2,"89.4M"],[3,"187.7M"]]],["Basilisk",[[1,"130M"]]],["Draugr",[[1,"130M"]]],["Taotie",[[1,"124.6M"],[2,"398.1M"],[3,"805.1M"]]]]
MONSTER_CATALOG.append(["Summoned", _SUMMONED + [[_n, [[1, ""]]] for _n in (
    "Savage Tiger Gladiator", "Arachne", "Carcinus", "Harpy", "Serpopard", "Gugler Knight", "Nasu",
    "Barbary Pirate", "Outlander Elites", "Temple Guard")]])     # summoned / auto-join monsters; no level data yet for the plain ones
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
    log(f"[*] Auto-Join: {fmt_hms(left)} left (renew under {limit // 60} min)")
    if left <= limit:
        act("step", text="Renewing the game's Auto-Join")
        dev.tap(*cfg["auto_join_start_tap"])
        time.sleep(2.0)
        now_left = read_auto_join_time(dev.screenshot())
        if now_left is not None and now_left > left + 600:
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
            for nm in MONSTER_NAMES + list(_MONSTER_ALIASES):
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
                            except OSError:
                                pass
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
    except Exception:
        pass


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
    except Exception:
        pass
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


def monster_allowed(name, cfg, level=None):
    """Apply the skip / only rule. -> (allowed, reason)."""
    why = monster_rule_skip(name, cfg, level)
    if why:
        return False, why
    mode = cfg["monster_mode"]
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
    if cfg.get("cerberus", "skip") == "join":
        return True, ""
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
    march = read_seconds(screen, hit, cfg["march_timer_source"]) if cfg["march_timer_source"] else 0
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
    spare = rally - (march or 0) - int(extra)
    note = "" if march is not None else ", march time unreadable (treated as 0)"
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
    opened = False
    for xy in (tuple(cfg["stamina_xy"]), (478, 853), (345, 853)):      # the saved spot, then the round "+" button, then the number
        dev.tap(*xy)
        nap(1.0, 1.5)
        shot = dev.screenshot()
        if find_text(shot, ["Use Item"], region=(120, 20, 300, 90)):
            opened = True
            break
        if screen_state(shot, cfg) != "march":                 # something else opened: back to the march screen, then try the next spot
            dev.back()
            nap(0.8, 1.2)
    if not opened:
        log("[!] Stamina: the Use Item screen did not open")
        return False
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
        except Exception:
            pass
        return None if seen_rows else ROW_Y.get(size)

    def _dialog_open(shot):
        """The amount window (item picture, 'Own: n', 'You can receive', slider, Max / Use). Either text line is enough:
        the OCR sometimes cannot read 'Own: 7,218' at all (seen on Oogly), and then the open window was taken as closed."""
        if find_text(shot, ["You can receive", "can receive"], region=(40, 410, 440, 100), cutoff=0.72):
            return True
        return bool(find_text(shot, ["Own:", "Own"], region=(60, 310, 300, 80), cutoff=0.6)) and \
            bool(find_text(shot, ["Max", "Minimum"], region=(60, 650, 420, 70), cutoff=0.8))

    for size in sizes:
        if stop.is_set():
            break
        shot = dev.screenshot()
        ry = _row_y(shot, size)
        if not ry:
            continue                                        # the account has none of this size / not a size we know
        use = find_text(shot, ["Use"], region=(330, ry + 10, 200, 80))
        dev.tap(*(use if use else (426, ry + 49)))
        nap(1.0, 1.4)
        shot = dev.screenshot()
        if not _dialog_open(shot):                          # slow emulator: look again, then tap Use once more before giving up
            nap(1.0, 1.4)
            shot = dev.screenshot()
        if not _dialog_open(shot) and screen_state(shot, cfg) != "march" and _row_y(shot, size):
            dev.tap(*(use if use else (426, ry + 49)))
            nap(1.2, 1.6)
            shot = dev.screenshot()
        if not _dialog_open(shot):
            _stamina_snap(shot, "no_dialog")
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
            except Exception:
                pass
            return None
        if mode.startswith("full"):                          # slide the bar all the way to the right end. NOT the game's Max button: it stops at 100 stamina
            for _sw in range(4):
                _shot_max = dev.screenshot()
                cnt = _count(_shot_max)
                if cnt and cnt[0] >= cnt[1]:
                    break
                dev.swipe(137, 503, 440, 503, 700 if _sw % 2 == 0 else 1300)   # a slower drag when a quick one did not move the thumb
                nap(0.6, 0.9)
                _shot_max = dev.screenshot()
                cnt = _count(_shot_max)
                if cnt and cnt[0] >= cnt[1]:
                    break
            _stamina_snap(_shot_max, "after_slide")
            log(f"[i] Stamina: slid to the end - using {cnt[0] if cnt else '?'} of {cnt[1] if cnt else '?'} item(s)")
        elif mode.startswith("exact"):
            want = max(1, int(cfg.get("stamina_count", 2) or 1))
            def _count(sh):
                try:
                    for _, ws in ocr_lines(sh[540:590, 130:410]):
                        m = re.search(r"(\d+)\s*/\s*(\d+)", " ".join(w[0] for w in ws))
                        if m:
                            return int(m.group(1)), int(m.group(2))
                except Exception:
                    pass
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
        for attempt in range(3):                            # the slider already sits at 2 items; press the green Use inside the window
            dev.tap(383 + random.randint(-12, 12), 685 + random.randint(-6, 6))
            nap(1.2, 1.7)
            shot = dev.screenshot()
            if not _dialog_open(shot):
                used_any = True
                break
            log(f"[i] Stamina: the amount window is still open - pressing Use again ({attempt + 2})")
        _stamina_snap(shot, "after_use")
        if used_any:
            log(f"[+] Stamina: used {size}-stamina items")
        else:
            log("[!] Stamina: the Use button did not close the window")
            dev.tap(502, 273)                               # the red X of the amount window
            nap(0.8, 1.2)
        break
    for _ in range(3):                                      # back to the march screen
        if screen_state(dev.screenshot(), cfg) == "march":
            break
        dev.back()
        nap(0.8, 1.2)
    nap(0.8, 1.2)
    shot_end = dev.screenshot()
    st = read_stamina(shot_end)
    log(f"[i] Stamina after using items: {st[0]:,} (needs {needed:,})" if st else "[i] Stamina after using items: not readable")
    _stamina_snap(shot_end, "march_after")
    if used_any and st and st[0] >= needed:
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
    except Exception:
        pass


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
        except Exception:
            pass
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
            except OSError:
                pass
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
    except Exception:
        pass
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
    except Exception:
        pass


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
    except Exception:
        pass
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
"Askia Muhammed I",
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
"Euiji Mundeok",
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
"Scipio Africanus"
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
    _gen_meta_file().write_text(json.dumps(meta, indent=2), encoding="utf-8")


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


def clear_popups(dev, cfg, log, stop, rounds=5):
    """Close whatever covers the game (offers, event pop-ups, full-screen sales, dialogs) so the bot can carry on with
    what it was doing. Uses your popup_* pictures first; on a screen it does not know it presses Back, but only when it
    can recognise the city (Back on the city would open the quit dialog). -> number of things closed."""
    th = min(cfg["threshold"], 0.85)
    closed = 0
    outside_done = False
    back_arrow_taps = 0
    for _ in range(rounds):
        if stop.is_set():
            break
        shot = dev.screenshot()
        if cancel_quit_dialog(dev, shot, log):
            closed += 1
            continue
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
        if st != "unknown" or not has_template("city_screen"):
            break
        if tap_alliance_war_row(dev, shot, log):          # the Alliance page is not a pop-up: go on into Alliance War
            _nap(stop, 1.5, 2.2)
            break
        try:                                               # a full page (Alliance Science, event, ...) has the gold Back arrow top left: it is not a pop-up
            _hsv = cv2.cvtColor(shot[8:80, 5:75], cv2.COLOR_BGR2HSV)
            _gold = cv2.countNonZero(cv2.inRange(_hsv, (12, 110, 150), (32, 255, 255)))
        except Exception:
            _gold = 0
        if _gold > 250 and back_arrow_taps < 6:
            back_arrow_taps += 1
            dev.tap(*cfg.get("city_to_tap", (35, 35)))
            log("[*] A full page is open (not a pop-up) - tapped the Back arrow")
            closed += 1
            _nap(stop, 1.2, 1.8)
            continue
        cx = find_close_x(shot)
        if cx:                                             # any pop-up with a round X button: tap it (works on every instance)
            dev.tap(cx[0][0], cx[0][1])
            log(f"[*] Closed a pop-up with its X button ({cx[0][0]},{cx[0][1]})")
            closed += 1
            _nap(stop, 0.8, 1.3)
            continue
        if not outside_done:                               # an unknown dialog: wait until it is steady, tap beside it first
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
            log(f"[*] Unknown pop-up - waited until it was steady, tapped outside it ({ox},{oy})")
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

    prev = None
    for _ in range(8):                                   # go to the top of the list
        shot = dev.screenshot()
        if prev is not None and same(prev, shot):
            break
        prev = shot
        dev.swipe(270, 330, 270, 820, 500)
        nap(0.7, 1.0)
    hit, prev, busy, best_all, scans = None, None, False, 0.0, 0
    log(f"[i] Looking for {role} general '{name}': tab {want}, favourites {fav or 'leave'}, {view} view")
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
    if not hit:
        if busy:
            log(f"[!] {role.capitalize()} general '{name}' is busy (not Idle)")
        else:
            log(f"[!] {role.capitalize()} general '{name}' was not found in the list "
                f"(check its tab / favourites setting; best picture match {best_all:.2f} over the whole list)")
        try:
            save_debug("general_" + name, dev.screenshot())
        except Exception:
            pass
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
            except Exception:
                pass
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
            continue
        cv2.imwrite(str(root / "art" / f"{safe}.png"), g["image"])
        if g.get("label") is not None:
            cv2.imwrite(str(root / "labels" / f"{safe}.png"), g["label"])
        have[safe] = {"name": safe, "region": g["region"], "troops": g["troops"], "art": f"{safe}.png"}
        added += 1
    (root / "catalog.json").write_text(json.dumps({"built": time.strftime("%Y-%m-%d"), "generals": sorted(have.values(), key=lambda g: g["name"].lower())}, indent=1), encoding="utf-8")
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
    (root / "catalog.json").write_text(json.dumps({"built": time.strftime("%Y-%m-%d"), "generals": gens}, indent=1), encoding="utf-8")
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
        except Exception:
            pass
        log(f"[+] {n} new monster picture(s) added to your library.")
    try:
        sync_catalog_pack(log)
    except Exception:
        pass
    return n


_NAME_CAT = re.compile(r"^(catalog\.json|art/[^/\\:*?\"<>|]{1,80}\.png)$")


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
                continue                                       # never replace a picture already on this PC
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
        except Exception:
            pass
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
        raise ValueError(f"Could not read {url}: {e}")
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
    except Exception:
        pass
    return info


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
        except Exception:
            pass


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
    except Exception:
        pass


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
    key = (serial, text)
    if time.time() - _notify_at.get(key, 0) < 30:
        return
    _notify_at[key] = time.time()
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
    (profiles_dir() / f"{name}.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
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
                except OSError:
                    pass
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
        except OSError:
            pass


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
        except Exception:
            pass
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


def start_app(adb, serial, pkg):
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
                except OSError:
                    pass
    except Exception:
        pass
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
        tmp = _LAUNCH_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(mem, indent=1), encoding="utf-8")
        os.replace(tmp, _LAUNCH_FILE)
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
    except Exception:
        pass
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


def settle_game(dev, cfg, log, stop):
    """After a start / restart / reconnect: give the game a few calm seconds (late pop-ups, icons still appearing,
    animations) before any task starts. Needs `settle_seconds` to have passed AND the screen to be a known one and
    unchanged for 3 checks in a row."""
    secs = cfg.get("settle_seconds", 8)
    if not secs or secs <= 0:
        return
    log(f"[*] Letting the game settle ({secs}s) before starting")
    t_end, limit = time.time() + secs, time.time() + secs + 45
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



KICKED = {}                       # serial -> time (epoch s) when the wait after being kicked ends (shown in the emulator list)
TAKEBACK = {}                     # serial -> Event: set from the interface to end the wait after being kicked


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
        except Exception:
            pass
        ev.wait(2)
    KICKED.pop(dev.serial, None)
    if stop.is_set():
        return True
    if ev.is_set():
        log("[*] 'Take game back now' pressed - not waiting any longer")
    try:
        log("@act " + json.dumps({"k": "step", "text": "Taking the game back..."}))
    except Exception:
        pass
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


def wait_stable(dev, cfg, log, stop, pkg=None, allow_emu_restart=True):
    """Wait until the game shows a known screen several times in a row (loading screens do not count)."""
    good, last_note, last_fg = 0, 0.0, time.time()
    not_ready_since, last_clear, last_kick = None, 0.0, 0.0
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
                return not stop.is_set()
        else:
            good = 0
            now = time.time()
            not_ready_since = not_ready_since or now
            if now - last_note > 30:
                last_note = now
                log("[*] Waiting for the game to finish loading...")
            if now - not_ready_since > 10 and now - last_clear > 10:   # not a loading screen any more: a pop-up / page is in the way
                last_clear = now
                if clear_popups(dev, cfg, log, stop, rounds=3):
                    not_ready_since = None
                    _nap(stop, 1.5)
                    continue
        if ready_screen(screen, cfg):
            not_ready_since = None
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
        log("[*] The emulator is off - starting it")
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
            raise RuntimeError("The emulator did not finish starting in time.")
        log("[+] The emulator is up")
        _nap(stop, 5)
    pkg = None
    if cfg["auto_launch_game"]:
        pkg = evony_package(adb, serial, cfg)
        if restarted or app_foreground(adb, serial, pkg) is False:
            log("[*] Starting Evony")
            start_app(adb, serial, pkg)
            restarted = True
            _nap(stop, 8)
    if wait and (restarted or (check_ready and cfg["auto_launch_game"])):
        return wait_stable(dev, cfg, log, stop, pkg)       # also when Evony was already open but still loading
    return not stop.is_set()


_HEAVY_LOCK = threading.RLock()            # only one emulator restart / game restart at a time: they are the PC's heaviest moments


def restart_emulator(dev, cfg, log, stop, why=""):
    """Close a stuck emulator (nicely first, then by force), start it again and wait until Evony is loaded."""
    pause_gate()                                                  # never restart an emulator while it is paused
    if not _HEAVY_LOCK.acquire(blocking=False):
        log("[*] Another emulator is restarting - waiting for my turn (keeps the PC from overloading)")
        while not _HEAVY_LOCK.acquire(timeout=2):
            if stop.is_set():
                return False
    try:
        return _restart_emulator(dev, cfg, log, stop, why)
    finally:
        _HEAVY_LOCK.release()


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
    except Exception:
        pass
    if stop.is_set():
        return False
    stop.wait(5)
    return bring_online(dev, cfg, log, stop, check_ready=True)


def force_restart_game(dev, cfg, log, stop):
    """Close Evony completely, open it again and wait until it is stable (used when the bot is lost)."""
    pkg = evony_package(dev.adb, dev.serial, cfg)
    log("[!] Lost for too long - restarting Evony")
    _adb(dev.adb, dev.serial, "shell", "am", "force-stop", pkg)
    _nap(stop, 3, 4)
    while not _HEAVY_LOCK.acquire(timeout=2):             # loading Evony is heavy: one at a time
        if stop.is_set():
            return False
    try:
        start_app(dev.adb, dev.serial, pkg)
        _nap(stop, 8)
        return wait_stable(dev, cfg, log, stop, pkg, allow_emu_restart=False)
    finally:
        _HEAVY_LOCK.release()


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
            BOSS_FILE.write_text(json.dumps({"updated": int(time.time()), "source": source, "items": items}, indent=1),
                                 encoding="utf-8")
        except Exception:
            pass


def get_bosses():
    with _boss_lock:
        if not _bosses and BOSS_FILE.exists():
            try:
                _bosses[:] = json.loads(BOSS_FILE.read_text(encoding="utf-8")).get("items", [])
            except Exception:
                pass
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
                except Exception:
                    pass
        try:
            self._call(go(), 20)
        except Exception:
            pass
        for f in USER.glob("tg_session*"):
            f.unlink(missing_ok=True)

    def close(self):
        async def go():
            if self.client is not None:
                await self.client.disconnect()
        try:
            self._call(go(), 10)
        except Exception:
            pass
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


def safe_shift(before, after, cmd_dx, cmd_dy):
    """city_shift, but never believe an impossible answer: the map cannot move further than the finger did (repeating
    pictures such as rows of troops fool the matcher). -> (dx, dy) of the map content between the two screenshots."""
    dx, dy, resp = city_shift(before, after)
    ok = (resp >= 0.05
          and min(0, cmd_dx) - 30 <= dx <= max(0, cmd_dx) + 30
          and min(0, cmd_dy) - 30 <= dy <= max(0, cmd_dy) + 30)
    if ok:
        return dx, dy
    x, y, w, h = CITY_VIEW
    diff = float(cv2.absdiff(before[y:y + h, x:x + w], after[y:y + h, x:x + w]).mean())
    if diff < 2.0:                                         # the picture did not change: the map did not move (edge)
        return 0.0, 0.0
    return cmd_dx * 0.9, cmd_dy * 0.9                      # it moved but the match is not trustworthy: use the finger


def match_building(text, cutoff=0.82):
    import difflib
    t = " ".join(text.split()).strip(" .,:;|-_")
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
            dev.tap(int(row[0]), int(row[1]))
            log(f"[*] On the Alliance page - tapped the 'Alliance War' row ({int(row[0])},{int(row[1])})")
            return True
    except Exception:
        pass
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


def zoom_out_city(dev, stop, log, max_tries=3):
    """Zoom the city view out with a two-finger pinch (done once per emulator session). -> True when the view got wider."""
    if getattr(dev, "zoomed_out", False):
        return True
    ok_any = False
    lab = {}
    try:                                                  # developer aid: %APPDATA%\\RallyForgeBot\\pinch_lab.json
        lab = json.loads((USER / "pinch_lab.json").read_text(encoding="utf-8"))
    except Exception:
        lab = {}
    if lab.get("shell"):
        for c in ([lab["shell"]] if isinstance(lab["shell"], str) else lab["shell"]):
            r = dev._run("shell", "sh", "-c", "'" + c + "'", timeout=40)
            out = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "ignore")
            for i in range(0, min(len(out), 3000), 300):
                log(f"[i] lab> {out[i:i + 300]!r}")
    kw = lab.get("params") or {}
    for vi, var in enumerate(lab.get("variants") or []):
        b = dev.screenshot()
        ok = dev.pinch_in(**var)
        _nap(stop, 1.5, 1.8)
        a = dev.screenshot()
        sc = _view_scale(b, a)
        log(f"[i] lab variant {vi} {var}: sent={ok} scale={sc} {getattr(dev, 'last_pinch', '')}")
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


class CityNav:
    """Moves around the city map and remembers where the camera is (relative to the top-left corner)."""

    def __init__(self, dev, stop, log, pause=0.9):
        self.dev, self.stop, self.log, self.pause = dev, stop, log, pause
        self.cx = self.cy = 0.0
        self.seen_city = False                              # set once the whole-city picture recognised the screen
        self.absolute = False                                # True: the camera place is read from the whole-city picture (no drifting)

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
            if self.stop.is_set():
                return False
            sc = self.localize()
            if sc:
                self.last_score = sc
                self.seen_city = True
            if not sc:
                lost += 1
                if lost >= 7:
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

    def _drag(self, fx, fy, tx, ty):
        before = self.dev.screenshot()
        self.dev.swipe(int(fx), int(fy), int(tx), int(ty), 450)
        _nap(self.stop, self.pause, self.pause + 0.3)
        after = self.dev.screenshot()
        dx, dy = safe_shift(before, after, tx - fx, ty - fy)
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
        for _ in range(30):
            rx, ry = want_dx - mx, want_dy - my
            if abs(rx) < 25 and abs(ry) < 25:
                break
            sx = max(-120, min(120, rx))              # bigger drags leave too little overlap to measure
            sy = max(-240, min(240, ry))
            # dragging the finger left/up moves the camera right/down.
            # A short drag can be read by the game as a TAP on a building (that opened Prison, Rare Books ...), so a
            # short move is done as one long drag past the target and a second long drag back.
            ln = (sx * sx + sy * sy) ** 0.5
            if ln < 80:
                ux, uy = (sx / ln, sy / ln) if ln else (0.0, 1.0)
                ax, ay = sx + ux * 90, sy + uy * 90
                d1x, d1y, _ = self._drag(270 + ax / 2, 440 + ay / 2, 270 - ax / 2, 440 - ay / 2)
                d2x, d2y, _ = self._drag(270 - ux * 45, 440 - uy * 45, 270 + ux * 45, 440 + uy * 45)
                dx, dy = d1x + d2x, d1y + d2y
            else:
                dx, dy, _ = self._drag(270 + sx / 2, 440 + sy / 2, 270 - sx / 2, 440 - sy / 2)
            if abs(dx) < 3 and abs(dy) < 3:
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
            if abs(rx) < 40 and abs(ry) < 40:                # so do the other direction first, then come back
                break
            if abs(ry) >= 25:
                self.pan(0, ry)
            self.pan(want_x - self.cx, 0)
            self.pan(want_x - self.cx, want_y - self.cy)
        return mapx - self.cx, mapy - self.cy


_CITY_PIC = {}
SAFE_TAP_AREA = (95, 215, 335, 470)                        # x, y, w, h of the screen where a tap can only hit the city (emulator px)
SHRINE_LABEL_IN_PICTURE = (1372.2, 576.9)                    # the Shrine label in the whole-city picture (emulator px); the square offsets count from here


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
    except Exception:
        pass
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
    SLOT_FILE.write_text(json.dumps({"slots": slots}, indent=1), encoding="utf-8")


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


def scan_city_taps(dev, cfg, log, stop, progress=None):
    """Test run: the numbered squares of the default city map replace the old per-building taps."""
    return scan_squares(dev, cfg, log, stop, progress=progress)


def scan_city_taps_old(dev, cfg, log, stop, progress=None):
    """(old) for every building on the saved city map go to it (saved picture), tap it and read its name and level."""
    serial = dev.serial if hasattr(dev, "serial") else "default"
    data = load_city()
    items = (data.get(serial) or {}).get("items", {})
    if not items:
        raise ValueError(CITY_MAP_HELP)
    dev.arm_guard(cfg, log, stop)
    dev.guard_city = True
    nav = CityNav(dev, stop, log)
    nav.to_origin()
    order = sorted(items, key=lambda k: (0 if k.split("#")[0] in ("Market", "Keep") else 1, items[k][1], items[k][0]))
    order = [k for k in order if k.split("#")[0] not in RESOURCE_BUILDINGS]
    t0, got, bad = time.time(), {}, []
    for i, k in enumerate(order):
        if stop.is_set():
            break
        name = k.split("#")[0]
        pos = find_building(dev, stop, log, nav, name, items[k], items, k)
        res = read_building_level(dev, cfg, log, stop, pos) if pos else None
        if res:
            got[k] = res
            same = BUILDING_ALIASES.get(res[0], res[0]).lower() == name.lower() or res[0].lower().replace(" ", "") == name.lower().replace(" ", "")
            log(f"[*] Tap test: {name} -> '{res[0]}'" + (f" level {res[1]}" if res[1] else " (no level)") + ("" if same else "  (NAME DIFFERS)"))
            if not same:
                bad.append(k)
        else:
            log(f"[!] Tap test: {name}: could not read it")
            bad.append(k)
        if progress:
            try:
                progress({"phase": "sweeping", "pct": int(100 * (i + 1) / len(order)), "left": int((time.time() - t0) / (i + 1) * (len(order) - i - 1)),
                          "text": f"Tap test: {name} done", "mapped": [x.split("#")[0] for x in got], "expected": [x.split("#")[0] for x in order],
                          "missing": [x.split("#")[0] for x in bad], "seconds": int(time.time() - t0), "stops": i + 1, "total": len(order), "row": 0})
            except Exception:
                pass
    dev.guard_city = False
    data = load_city()
    ent = data.setdefault(serial, {"items": items})
    ent["items"] = items
    ent["levels"] = {k: {"name": v[0], "level": v[1]} for k, v in got.items()}
    ent["tap_test"] = {"seconds": int(time.time() - t0), "read": len(got), "total": len(order), "bad": bad}
    save_city(data)
    log(f"[+] Tap test: read {len(got)} of {len(order)} buildings in {int(time.time() - t0)}s. Not read: {', '.join(bad) if bad else 'none'}")
    if progress:
        try:
            progress({"phase": "done", "pct": 100, "left": 0, "text": "Done", "mapped": [x.split("#")[0] for x in got],
                      "expected": [x.split("#")[0] for x in order], "missing": [x.split("#")[0] for x in bad],
                      "seconds": int(time.time() - t0), "stops": len(order), "total": len(order), "row": 0})
        except Exception:
            pass
    return got


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
        except Exception:
            pass

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
        out.append({"btn": (x, y, w, h), "gem": gem, "afford": red < 40,
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
    if not wanted:
        log("[!] Black Market: no wanted items with a picture - add some on the Market tab")
        return
    img = dev.screenshot()
    if not market_on_screen(img):
        p = find_text(img, ["Black Market"])
        if p:
            dev.tap(*p)
            _nap(stop, 1.5, 2.0)
            img = dev.screenshot()
    if not market_on_screen(img):
        log("[!] Black Market: the shop is not open")
        return
    mode = cfg.get("market_refresh_mode", "none")
    limit = int(cfg.get("market_refresh_n", 0) or 0) if mode == "count" else (float("inf") if mode == "all" else 0)
    paid_ok = bool(cfg.get("market_paid_refresh"))
    budget = int(cfg.get("market_max_gems", 0) or 0)
    th = float(cfg.get("market_match", 0.78))
    spent = refreshes = 0
    no_change = 0
    bought, failed = {}, set()                              # failed: slot numbers that did not sell (until the next refresh)
    log(f"[*] Black Market: looking for {', '.join(w[0] for w in wanted)}")
    while not stop.is_set():
        img = dev.screenshot()
        if not market_on_screen(img):
            log("[!] Black Market: the shop closed")
            break
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
                else:
                    bx, by, bw, bh = slot["btn"]
                    dev.tap(bx + bw / 2, by + bh / 2)
                    _nap(stop, 0.9, 1.3)
                    p = find_text(dev.screenshot(), ["Confirm", "Buy", "Purchase", "OK"])
                    if p:
                        dev.tap(*p)
                        _nap(stop, 0.9, 1.3)
                    after = dev.screenshot()
                    if market_on_screen(after) and market_match(after, slot, tname, th) >= th:
                        failed.add(si)                       # still there: the purchase did not go through
                        log(f"[!] {name} is still for sale after tapping it - not bought")
                    else:
                        bought[name] = bought.get(name, 0) + 1
                        if slot["gem"] and price:
                            spent += price
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
        free = free_refreshes(img, refresh)
        if not free:
            if not paid_ok:
                log("[i] Black Market: no free refreshes left")
                break
            cost = refresh_cost(img, refresh, cfg.get("market_refresh_cost", 50))
            if budget and spent + cost > budget:
                log("[i] Black Market: the gem limit is reached")
                break
        before = _market_sig(img)
        dev.tap(refresh[0] + refresh[2] / 2, refresh[1] + refresh[3] / 2)
        _nap(stop, 0.9, 1.3)
        shot = dev.screenshot()
        if not market_on_screen(shot):                       # a dialog (confirm / not enough gems)
            p = find_text(shot, ["Confirm", "OK"])
            if p and not find_text(shot, ["enough", "insufficient", "Not enough"]):
                dev.tap(*p)
                _nap(stop, 1.0, 1.5)
        _nap(stop, 0.8, 1.2)
        shot = dev.screenshot()
        if not market_on_screen(shot):
            log("[!] Black Market: a dialog opened instead of refreshing (out of gems?) - stopping")
            dev.back()
            _nap(stop, 0.8, 1.2)
            break
        if _market_sig(shot) is not None and before is not None and abs(_market_sig(shot) - before).mean() < 2.0:
            no_change += 1
            if no_change >= 2:
                log("[!] Black Market: the shop did not change after refreshing (out of gems?) - stopping")
                break
            continue
        no_change = 0
        if not free:
            spent += cost
        refreshes += 1
        failed.clear()
        log(f"[*] Black Market: refresh {refreshes}" + (f" of {limit}" if mode == "count" else ""))
    total = ", ".join(f"{n} x{c}" for n, c in bought.items()) or "nothing"
    log(f"[+] Black Market done: bought {total}; {refreshes} refresh(es)")


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
        except Exception:
            pass
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
    if kind == "building" and not nav_state.get("picture_bad") and picture_items(dev) is not None and str(name).lower().split("+")[0].split("#")[0] in {k.split("#")[0].lower() for k in picture_items(dev)}:
        pitems = picture_items(dev)                          # the camera is found by the whole-city picture; positions come from the mapper or the built-in default
        pkeys = []
        for nm in str(name).lower().split("+"):                # "Keep+Tavern" = visit the Keep, then the Tavern
            want = nm.strip()
            pkeys += sorted(k for k in pitems if k.split("#")[0].lower() == want.split("#")[0] and (("#" not in want) or k.lower() == want))[:int(task.get("count", 1) or 1)]
        nav = nav_state.get("anav")
        if nav is None:
            nav = CityNav(dev, stop, log)
            nav.absolute = True
            nav_state["anav"] = nav
        done_any = False
        for k in pkeys:
            tx, ty = pitems[k]
            log(f"[*] Going to {k} by the city picture")
            nav.seen_city = False
            nav.goto(tx, ty)
            if not nav.seen_city:                           # this city does not look like the picture: use the old way for this run
                log("[i] The whole-city picture does not match this screen - using the older way to find the building")
                nav_state["picture_bad"] = True
                break
            px, py = tx - nav.cx, ty - nav.cy
            x0, y0, w, h = SAFE_TAP_AREA
            if not (x0 <= px <= x0 + w and y0 <= py <= y0 + h):
                log(f"[!] Task '{task.get('name')}': {k} could not be brought into view (it is at {round(px)},{round(py)}) - skipped")
                continue
            opened = False
            for attempt in range(3):                        # tap the building; when no ring menu opens (a reward panel came up, the tap landed beside it) look again and retry
                close_reward_panel(dev, stop, log)
                if find_detail_icon(dev.screenshot()):      # a ring menu is still open from before: tapping the building would close it
                    close_ring(dev, stop, (px, py + 35))
                try:
                    pre = dev.screenshot()
                    cv2.circle(pre, (int(px), int(py + float(task.get("tap_dy", 0)))), 14, (0, 0, 255), 2)
                    cv2.imwrite(str(USER / "debug" / "before_tap.png"), pre)
                except Exception:
                    pass
                dev.tap(int(px), int(py + float(task.get("tap_dy", 0))))
                _nap(stop, 1.0, 1.4)
                shot = dev.screenshot()
                ring_task = any(str(t).split()[0] in ("ring_name", "champions", "tavern_catalog", "market_capture", "wheel") for t in task.get("steps", []) if str(t).strip())
                if find_detail_icon(shot) or not ring_task:
                    if ring_task:                           # a menu opened - but is it the one we wanted? (a camera that is a little off opens the neighbour: Wisdom Dome instead of the Keep)
                        got = ring_building_name(shot)
                        want_nm = k.split("#")[0].lower()
                        if got and got[0] and want_nm not in got[0].lower() and any(b.lower() in got[0].lower() for b in BUILDING_NAMES):
                            log(f"[!]   the menu that opened is {got[0]}, not {k} (try {attempt + 1}/3) - closing it and aiming again")
                            close_ring(dev, stop, (px, py + 35))
                            leave_to_city(dev, cfg, stop)
                            nav.goto(tx, ty)
                            px, py = tx - nav.cx, ty - nav.cy
                            if not (x0 <= px <= x0 + w and y0 <= py <= y0 + h):
                                break
                            continue
                    opened = True
                    break
                log(f"[i]   no building menu opened at {k} (try {attempt + 1}/3) - looking again")
                close_reward_panel(dev, stop, log)
                nav.goto(tx, ty)
                px, py = tx - nav.cx, ty - nav.cy
                if not (x0 <= px <= x0 + w and y0 <= py <= y0 + h):
                    break
            if not opened:
                log(f"[!] Task '{task.get('name')}': the menu of {k} did not open - skipped")
                if find_detail_icon(dev.screenshot()):
                    close_ring(dev, stop, (px, py + 35))
                leave_to_city(dev, cfg, stop)
                continue
            for st in task.get("steps", []):
                run_step(dev, cfg, log, stop, st)
            leave_to_city(dev, cfg, stop)
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


def leave_to_city(dev, cfg, stop):
    """Close whatever a task opened (a building's panel, stacked pages, a ring menu). Back is only pressed while we are not
    on the city screen (on the city screen it would open the quit dialog); a pop-up's round X is used when there is one."""
    for i in range(10):
        if stop.is_set():
            return False
        shot = dev.screenshot()
        if screen_state(shot, cfg) in ("city", "rally_list") and not find_detail_icon(shot):
            return True
        if cancel_quit_dialog(dev, shot, lambda *_: None):
            _nap(stop, 0.8, 1.2)
            continue
        if find_detail_icon(shot) and screen_state(shot, cfg) == "city":
            close_ring(dev, stop, (270, 430))
            continue
        cx = find_close_x(shot) if i % 2 == 0 else []
        if cx:
            dev.tap(cx[0][0], cx[0][1])
        else:
            dev.back()
        _nap(stop, 0.8, 1.2)
    return False


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
            "reserve": max(0, min(int(float(c.get("reserve", 0) or 0)), 999999999))}


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


def ce_save(serial, **kw):
    with _DUTY_LOCK:
        data = load_duty_state()
        row = data.setdefault(serial or "default", {}).setdefault("crazy_eggs", {})
        row.update(kw)
        try:
            DUTY_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")
        except OSError:
            pass


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
    except Exception:
        pass


def ce_on_screen(img):
    return bool(find_text(img, ["Reset Countdown", "Scout Cost", "Activate all"], cutoff=0.72))


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
            p = find_text(img, ["Crazy Eggs"], cutoff=0.72)
            if not p:                                      # the list may be longer than the screen
                dev.swipe(270, 700, 270, 350, 400)
                _nap(stop, 1.0, 1.4)
                img = dev.screenshot()
                p = find_text(img, ["Crazy Eggs"], cutoff=0.72)
            if p:
                dev.tap(*p)
                _nap(stop, 2.5, 3.2)
                if ce_on_screen(dev.screenshot()):
                    break
        else:
            ce_snap(dev.screenshot(), "2_failed")
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
        ce_save(serial, next_at=time.time() + 1800)


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
        try:
            DUTY_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")
        except OSError:
            pass


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


def ch_greet_label(img):
    """-> {"pack": bool, "n": greets used or None, "wait": seconds left on the timer}"""
    x, y, w, h = CH_GREET_BOX
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


def run_wheel(dev, cfg, log, stop):
    """Tavern's ring menu is open: open the Wheel of Fortune and spin with the button the user picked until the number
    of spins is done, the coins run out (or fall to the reserve to keep) or something unexpected is on the screen.
    The reward panel is closed by tapping its Continue / empty ground. Only the three Spin buttons are ever pressed."""
    conf = wheel_conf(cfg)
    big = int(conf["button"]) if int(conf["button"]) in WHEEL_BTN else 100
    target = int(conf["spins"] or 0)                       # 0 = until the coins are used up
    reserve = int(conf["reserve"] or 0)
    shot = dev.screenshot()
    if not wheel_on_page(shot):
        p = find_text(shot, ["Wheel of Fortune", "Wheel of", "Fortune"], region=(0, 250, 300, 300), cutoff=0.7)
        dev.tap(p[0], p[1] - 40) if p else dev.tap(*WHEEL_ICON)
        _nap(stop, 2.2, 2.8)
        shot = dev.screenshot()
        if not wheel_on_page(shot):
            raise RuntimeError("the Wheel of Fortune page did not open")
    done = 0
    fails = 0
    while not stop.is_set():
        shot = dev.screenshot()
        if wheel_reward_panel(shot):                       # a reward panel is still up
            dev.tap(270, 754)
            _nap(stop, 0.8, 1.1)
            continue
        if not wheel_on_page(shot):
            log("[!]  Wheel: not on the Wheel of Fortune page any more - stopped")
            break
        coins = wheel_coins(shot)
        if coins is None:
            log("[!]  Wheel: could not read the coins - stopped")
            break
        left = (target - done) if target else None
        if left is not None and left <= 0:
            log(f"[+]  Wheel: {done} spin(s) done")
            break
        pick = next((b for b in (100, 10, 1) if b <= big and (left is None or b <= left) and coins - WHEEL_COST[b] >= reserve), None)
        if pick is None:
            log(f"[+]  Wheel: stopped after {done} spin(s) - {coins:,} coins left (cost/keep-at-least {reserve:,})")
            break
        dev.tap(*WHEEL_BTN[pick])
        shown = False
        for _ in range(14):
            _nap(stop, 0.5, 0.7)
            if wheel_reward_panel(dev.screenshot()):
                shown = True
                break
        if not shown:
            fails += 1
            log(f"[i]   Wheel: no reward panel after the {pick}-spin button ({fails}/3)")
            if fails >= 3:
                break
            continue
        fails = 0
        done += pick
        log(f"[i]   Wheel: {done} spin(s) done ({coins - WHEEL_COST[pick]:,} coins left)")
        for _ in range(4):                                 # close the panel: Continue, else empty ground
            dev.tap(270, 754)
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
    except Exception:
        pass


def ch_open_page(dev, stop, log):
    """The Keep's ring menu is open: tap Champions. True when the Champion page shows."""
    for _ in range(2):
        shot = dev.screenshot()
        ch_snap(shot, "0_ring")
        if ch_on_page(shot) and ch_name(shot):
            return True
        p = find_text(shot, ["Champions", "Champion"], region=(0, 150, 540, 450), cutoff=0.7)
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
            dev.tap(*((box[0] - 150, box[1]) if box and box[0] > 200 else (216, 486)))           # the tick box is left of its text
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
        if find_text(dev.screenshot(), ["Greet"], region=CH_GREET_BOX, cutoff=0.7):
            return


def ch_do_greets(dev, stop, log, name, want, serial, day):
    """Greet the champion `want` times (stops at 10 a day in total). -> greets done."""
    done = 0
    for _ in range(want + 4):
        if done >= want or stop.is_set():
            break
        lab = ch_greet_label(dev.screenshot())
        if lab["n"] is not None and lab["n"] >= CH_GREETS:
            log("[i]   Champions: the 10 daily greetings are used up")
            break
        if lab["wait"] > 0:
            log(f"[i]   Champions: greet timer, waiting {lab['wait']} s")
            if stop.wait(min(lab["wait"], 75) + 3):
                break
        dev.tap(*CH_GREET)
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
    plan = ch_conf(cfg)["plan"]
    row = ch_row(serial)
    taps_used = row.get("taps") or {} if row.get("taps_day") == day else {}
    greets_used = row.get("greets") or {} if row.get("greets_day") == day else {}
    if not plan:                                           # default: everything on the champion shown, or the first active one
        cur = ch_name(dev.screenshot())
        plan = {cur: {"taps": CH_TAPS, "greets": CH_GREETS}} if cur else {}
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


# ------------------------------------------------------------ duties (ready-made, scheduled, tracked)
DUTY_FILE = USER / "duties_state.json"
_DUTY_LOCK = threading.Lock()
DUTY_MAX_TRIES = 3                      # tries per game day before a duty gives up until the next reset
DUTY_RETRY_MIN = 20                     # minutes between two tries of a duty that failed
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
    {"id": "alliance_gift", "group": "Reward claims", "title": "Alliance gift", "every_min": 0, "verified": False,
     "note": "Alliance > Gifts > Claim all.",
     "target": "icon:Alliance", "steps": ["wait 1.5", "tap_text Gifts", "wait 1.2", "tap_text Claim All|Claim", "wait 1"]},
    {"id": "alliance_help", "group": "Daily activities", "title": "Alliance help", "every_min": 60, "verified": False,
     "note": "Alliance > Help > Help all.",
     "target": "icon:Alliance", "steps": ["wait 1.5", "tap_text Help", "wait 1.2", "tap_text Help All|Help all", "wait 1"]},
    {"id": "champions", "group": "Daily activities", "title": "Champions", "every_min": 0, "verified": False,
     "note": "Keep > Champions: 15 taps and 10 greetings a day on the champions you pick (only active ones). Needs the city map (Keep).",
     "target": "building:Keep", "tap_dy": 0, "steps": ["wait 1.2", "champions"]},
    {"id": "wheel", "group": "Daily activities", "title": "Wheel of Fortune", "every_min": 0, "verified": False,
     "note": "Tavern > Wheel of Fortune: spins with the button you pick (1 / 10 / 100) for the number of spins you set, or until the coins are used up. Needs the city map (Tavern).",
     "target": "building:Tavern", "tap_dy": 0, "steps": ["wait 1.5", "ring_name Tavern", "wheel"]},
    {"id": "crazy_eggs", "group": "Common tasks", "title": "Crazy Eggs", "every_min": 1, "verified": False,
     "note": "Smashes the ready eggs, closes the reward panel, comes back when each timer ends; Lucky Hammer once a day on the egg you pick.",
     "target": "tpl:event_center_icon|Center",
     "steps": ["wait 2", "crazy_eggs"]},
    {"id": "collect_resources", "group": "Common tasks", "title": "Collect resources", "every_min": 180, "verified": False,
     "note": "Taps every mapped farm / sawmill / quarry / mine. Needs the city mapper.",
     "target": "building:Farm", "count": 6, "steps": ["wait 1", "tap_text Collect|Claim"]},
]
DUTY_BY_ID = {d["id"]: d for d in DUTY_LIBRARY}


def _game_day():
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() - _RESET_MIN[0] * 60))     # the game day changes at the reset time (default 00:00 UTC, server time)


def load_duty_state():
    try:
        return json.loads(DUTY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def duty_record(serial, duty_id, ok, msg=""):
    with _DUTY_LOCK:
        data = load_duty_state()
        row = data.setdefault(serial or "default", {}).setdefault(duty_id, {})
        day = _game_day()
        if row.get("day") != day:
            row["day"], row["tries"] = day, 0
        row["tries"] = int(row.get("tries", 0)) + 1
        row["last_try"] = time.time()
        row["msg"] = msg
        if ok:
            row["last_ok"], row["status"] = time.time(), "done"
            if duty_id == "crazy_eggs":
                row["tries"] = 0
        else:
            row["status"] = "retry" if row["tries"] < DUTY_MAX_TRIES else "gaveup"
        try:
            DUTY_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")
        except OSError:
            pass


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
        try:
            DUTY_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")
        except OSError:
            pass


def run_now_done(serial, tid):
    run_now_set(serial, [x for x in run_now_list(serial) if x != tid])


def tasks_active(cfg, serial):
    return bool(cfg.get("city_enabled")) or bool(run_now_list(serial))


def duty_status(serial, duty_id, cfg):
    """-> (status, text): status is off | waiting | due | done | retry | gaveup."""
    conf = (cfg.get("duties") or {}).get(duty_id) or {}
    d = DUTY_BY_ID.get(duty_id)
    if not d or not conf.get("on"):
        return "off", "Off"
    row = (load_duty_state().get(serial or "default") or {}).get(duty_id) or {}
    day, now = _game_day(), time.time()
    tries = int(row.get("tries", 0)) if row.get("day") == day else 0
    if duty_id == "crazy_eggs":                             # timer driven: comes back when the next egg is ready
        if row.get("all_done_day") == day:
            return "done", "All eggs done"
        if tries >= DUTY_MAX_TRIES:
            return "gaveup", "Gave up for today"
        if tries and now - row.get("last_try", 0) < DUTY_RETRY_MIN * 60:
            return "retry", "Retrying " + time.strftime("%H:%M", time.localtime(row.get("last_try", 0) + DUTY_RETRY_MIN * 60))
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
    if tries >= DUTY_MAX_TRIES:
        return "gaveup", "Gave up for today"
    if tries and now - row.get("last_try", 0) < DUTY_RETRY_MIN * 60:
        nxt = row.get("last_try", 0) + DUTY_RETRY_MIN * 60
        return "retry", "Retrying " + time.strftime("%H:%M", time.localtime(nxt))
    return "due", "Due"


def run_now_tasks(cfg, serial):
    out = []
    for tid in run_now_list(serial):
        if tid.startswith("duty:") and tid[5:] in DUTY_BY_ID:
            d = DUTY_BY_ID[tid[5:]]
            t = {k: d[k] for k in ("target", "steps") if k in d}
            t["name"], t["count"] = tid, d.get("count", 1)
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
            t = {k: d[k] for k in ("target", "steps") if k in d}
            t["name"] = "duty:" + d["id"]
            t["count"] = d.get("count", 1)
            out.append(t)
    return out


def city_tasks_due(cfg, last_run, serial=""):
    now = time.time()
    first = run_now_tasks(cfg, serial)
    if first:                                                    # asked for by the user: these go before everything else
        return first
    if not cfg.get("city_enabled"):
        return []
    out = []
    for t in cfg.get("city_tasks") or []:
        if not t.get("enabled", True):
            continue
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
    return items


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
    except Exception:
        pass
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
            log("[!]   the ring menu would not close - stopping so nothing is pressed by mistake")
            stop.set()
    elif res and res[2]:                                     # the label shows where the building really is on the screen: re-anchor the camera
        lx, ly = res[2]
        fo = FIXED_OFFSETS_FROM_SHRINE.get(name)
        if fo:                                               # the label's own place is known exactly (offset from the Shrine label)
            nav.cx, nav.cy = shrine[0] + fo[0] - lx, shrine[1] + fo[1] - ly
        else:
            nav.cx, nav.cy = tx - lx, ty - (ly - 35)
        if not close_ring(dev, stop, res[2]):
            log("[!]   the ring menu would not close - stopping so nothing is pressed by mistake")
            stop.set()
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


def scan_squares(dev, cfg, log, stop, numbers=None, progress=None):
    """Map the city by the numbered squares: go to each square, tap it, read its name and level. Squares that read
    correctly are saved on this emulator's city map. -> {number: result}"""
    serial = dev.serial if hasattr(dev, "serial") else "default"
    dev.arm_guard(cfg, log, stop)
    dev.guard_city = True
    t0 = time.time()
    nav = CityNav(dev, stop, log)
    order = list(numbers or [n for n in sorted(CITY_SQUARES) if n not in PARKED_SQUARES])
    got, bad, items, levels = {}, [], {}, {}
    prog_f = USER / "squares_progress.json"
    try:                                                     # an unfinished run (stopped, crashed, lost): carry on from where it stopped
        pst = json.loads(prog_f.read_text(encoding="utf-8")) if prog_f.exists() else {}
    except Exception:
        pst = {}
    mine = pst.get(serial) or {}
    if not numbers and mine and time.time() - mine.get("t", 0) < 6 * 3600 and mine.get("done"):
        done_before = set(mine["done"])
        order = [n for n in order if n not in done_before]
        try:                                                 # drop saved reads that are not in the done list (a read that was found wrong and cleared)
            _d = load_city()
            _e = _d.get(serial) or {}
            _lv = _e.get("levels", {})
            _drop = [k for k in list(_e.get("items", {})) if _lv.get(k, {}).get("square") not in done_before]
            if _drop:
                for k in _drop:
                    _e["items"].pop(k, None)
                    _lv.pop(k, None)
                save_city(_d)
        except Exception:
            pass
        if order:
            log(f"[*] Squares: carrying on from the last run - {len(done_before)} squares already read, {len(order)} to go")
        else:
            order = [n for n in sorted(CITY_SQUARES) if n not in PARKED_SQUARES]
            mine = {}
    done_list = list(mine.get("done", [])) if mine else []
    wipe = not done_list                                    # a fresh run starts from a blank map; a resumed run keeps what it already read
    if city_picture() is not None:                           # the camera place comes from the whole-city picture: no slow start, no drifting
        nav.absolute = True
        shrine = SHRINE_LABEL_IN_PICTURE
        log("[i] Squares: finding the camera place by the whole-city picture")
    else:
        shrine = locate_shrine(dev, stop, log, nav)
    if shrine:
        for i, n in enumerate(order):
            if stop.is_set():
                break
            r = open_square(dev, cfg, log, stop, nav, shrine, n)
            if not r and not stop.is_set():                  # an ad / pop-up may have covered the city: close it, find our place again, retry once
                try:
                    shut = clear_popups(dev, cfg, log, stop, rounds=4)
                except Exception:
                    shut = 0
                if shut:
                    log(f"[*] Square {n}: closed {shut} pop-up(s) - finding the way again and retrying")
                    if not nav.absolute:
                        nav = CityNav(dev, stop, log)
                        shrine = locate_shrine(dev, stop, log, nav) or shrine
                    r = open_square(dev, cfg, log, stop, nav, shrine, n)
            got[n] = r
            nm = CITY_SQUARES[n][0]
            if r and r[5] and r[1].lower() in UNIQUE_BUILDINGS and any(k.split("#")[0].lower() == r[1].lower() for k in items):
                log(f"[!] Square {n}: read '{r[1]}' but that building is already mapped on another square - the camera was probably not where it thought. Left blank.")
                r = None
            if r and r[5]:
                base = re.sub(r"\s*\d+$", "", r[1]).strip() or r[1]
                k_n = 1
                while f"{base}#{k_n}" in items:
                    k_n += 1
                key = f"{base}#{k_n}"
                items[key] = [round(r[3]), round(r[4])]
                levels[key] = {"name": r[1], "level": r[2], "square": n}
                done_list.append(n)
                try:
                    data = load_city()
                    ent = data.setdefault(serial, {"items": {}})
                    if nav.absolute and (wipe or ent.get("frame") != "picture"):
                        ent["items"], ent["levels"], ent["frame"] = {}, {}, "picture"
                        wipe = False
                    ent.setdefault("items", {})[key] = items[key]
                    ent.setdefault("levels", {})[key] = levels[key]
                    save_city(data)
                    pst[serial] = {"done": done_list, "t": time.time()}
                    prog_f.write_text(json.dumps(pst), encoding="utf-8")
                except Exception:
                    pass
            else:
                bad.append(nm)
            if progress:
                try:
                    progress({"phase": "sweeping", "pct": int(100 * (i + 1) / len(order)), "left": int((time.time() - t0) / (i + 1) * (len(order) - i - 1)),
                              "text": f"Square {n} ({nm}) done", "mapped": [k.split("#")[0] for k in items], "expected": [CITY_SQUARES[x][0] for x in order],
                              "missing": bad, "seconds": int(time.time() - t0), "stops": i + 1, "total": len(order), "row": 0})
                except Exception:
                    pass
    dev.guard_city = False
    if not bad and not stop.is_set():                        # finished: next run starts from square 1
        try:
            pst.pop(serial, None)
            prog_f.write_text(json.dumps(pst), encoding="utf-8")
        except Exception:
            pass
    if items:
        data = load_city()
        ent = data.setdefault(serial, {"items": {}})
        if nav.absolute and ent.get("frame") != "picture":
            ent["items"], ent["levels"], ent["frame"] = {}, {}, "picture"
        ent.setdefault("items", {}).update(items)
        ent.setdefault("levels", {}).update(levels)
        save_city(data)
    log(f"[+] Squares: read {len(items)} of {len(order)} in {int(time.time() - t0)}s. Not read: {', '.join(bad) if bad else 'none'}")
    if progress:
        try:
            progress({"phase": "done", "pct": 100, "left": 0, "text": "Done", "mapped": [k.split("#")[0] for k in items], "expected": [CITY_SQUARES[x][0] for x in order],
                      "missing": bad, "seconds": int(time.time() - t0), "stops": len(order), "total": len(order), "row": 0})
        except Exception:
            pass
    return got


def task_needs_city_map(t):
    tgt = str(t.get("target", "")).lower()
    steps = [str(x).strip().lower() for x in t.get("steps", [])]
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
    except Exception:
        pass


def read_task_events(n=150):
    try:
        lines = TASK_LOG.read_text(encoding="utf-8").splitlines()[-n:]
    except Exception:
        return []
    out = []
    for l in reversed(lines):
        try:
            out.append(json.loads(l))
        except Exception:
            pass
    return out


def run_city_tasks(dev, cfg, log, stop, tasks, last_run):
    """From the city screen: run each due task (open it, run its steps, go back)."""
    nav_state = {}
    dev.arm_guard(cfg, log, stop)
    for t in tasks:
        if stop.is_set():
            break
        name = t.get("name", "task")
        duty_id = name[5:] if name.startswith("duty:") else ""
        shown = DUTY_BY_ID[duty_id]["title"] if duty_id in DUTY_BY_ID else name
        log(f"[*] {'Duty' if duty_id else 'City task'}: {shown}")
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
        try:
            clear_popups(dev, cfg, log, stop)                # close offers / pop-ups first, then do the task
            ok = city_task_target(dev, cfg, log, stop, t, nav_state)
            if not ok and clear_popups(dev, cfg, log, stop):  # a pop-up was in the way: try the same task again
                ok = city_task_target(dev, cfg, log, stop, t, nav_state)
            if ok:
                _nap(stop, 1.2, 1.8)
                for st in t.get("steps", []):
                    run_step(dev, cfg, log, stop, st)
                leave_to_city(dev, cfg, stop)
            result = (ok is not False, "" if ok is not False else "could not open it")
        except Exception as e:
            log(f"[!] {'Duty' if duty_id else 'City task'} '{shown}' failed: {e}")
            result = (False, str(e)[:120])
        if result is not None and not stop.is_set():
            task_event(getattr(dev, "serial", ""), shown, *result)
        if duty_id and result is not None and not stop.is_set():
            duty_record(getattr(dev, "serial", ""), duty_id, *result)
        leave_to_city(dev, cfg, stop)


def run_device(serial, adb, cfg, log, stop, on_frame=None, ctl=None):
    cfg = merge_device_cfg(cfg, serial)                              # per-instance overrides
    set_pacing(cfg)
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
                    notify(cfg, serial, "Could not get the emulator and the game ready - this instance stopped.")
                return log("[+] Stopped.") if stop.is_set() else \
                    log("[!] Could not get the emulator and the game ready - stopping this instance.")
        else:
            dev.connect()
        dev.screenshot()
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
    still = {"sig": None, "since": None}  # screen that should be moving but is not
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
        except Exception:
            pass

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
            if cfg["monster_mode"] != "off" or mon_presets or cfg.get("monster_skip"):
                mon, _ = identify_monster(screen, hit, cfg)
                if mon is None and cfg.get("monster_skip"):        # no picture yet: read the name under the portrait instead
                    try:
                        nx0, ny0 = int(hit[0] - 21), int(hit[1] - 113)
                        lab = read_monster_label(screen[max(ny0 - 18, 0):ny0 + 18, max(nx0 - 150, 0):nx0 + 150])
                        if lab in MONSTER_NAMES:
                            mon = lab
                    except Exception:
                        pass
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
            except Exception:
                pass
            rally, march, ok, why = judge(screen, hit, cfg, need)
            last_march[0] = march
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
            except Exception:
                pass

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
        save_debug(serial, dev.screenshot())
        return False

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
        if x_ok:                                          # no saved picture: look for the round X button of any pop-up
            cx = find_close_x(screen)
            if cx:
                dev.tap(cx[0][0], cx[0][1])
                log(f"[*] Closed a pop-up with its X button ({cx[0][0]},{cx[0][1]})")
                return True
        return False

    def recover():
        """Get back to the rally list: close pop-ups, follow the navigation steps, use Back where it is safe."""
        at_bottom[0] = False                  # the list reopens at the top: the next sweep jumps to the bottom first
        outside_taps = [0]
        back_taps = [0]
        war_row_taps = [0]
        view_toggles = [0]
        last_toggle = [0.0]
        for _ in range(8):
            if stop.is_set():
                return False
            screen = dev.screenshot()
            st = screen_state(screen, cfg)
            if st == "rally_list":
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
                try:
                    if has_template("war_button"):
                        _row = find(screen, "war_button", 0.7)
                        if _row:
                            _row = (int(_row[0]), int(_row[1]))
                    if not _row:
                        _t = find_text(screen, ["Alliance War"], region=(60, 330, 420, 440), cutoff=0.8)
                        if _t and find_text(screen, ["Alliance Gift", "Alliance Treasure", "Alliance Help"], region=(60, 330, 420, 440), cutoff=0.75):
                            _row = _t
                except Exception:
                    _row = None
                if _row:
                    war_row_taps[0] += 1
                    dev.tap(int(_row[0]), int(_row[1]))
                    log(f"[*] On the Alliance page - tapped the 'Alliance War' row ({int(_row[0])},{int(_row[1])})")
                    nap(1.5, 2.2)
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
            if st == "unknown" and outside_taps[0] < 2:       # a dialog we have no picture of: wait until it is steady, then tap beside it
                outside_taps[0] += 1
                prev = screen
                for _w in range(6):
                    nap(0.6, 0.8)
                    cur = dev.screenshot()
                    if float(np.mean(cv2.absdiff(prev[40:920:8, ::8], cur[40:920:8, ::8]))) < 1.5:
                        break
                    prev = cur
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
            elif st == "city" and outside_taps[0] < 2:       # the navigation button is hidden: probably a card-style pop-up (no X) over the city
                outside_taps[0] += 1
                prev = screen
                for _w in range(6):
                    nap(0.6, 0.8)
                    cur = dev.screenshot()
                    if float(np.mean(cv2.absdiff(prev[40:920:8, ::8], cur[40:920:8, ::8]))) < 1.5:
                        break
                    prev = cur
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

    log(f"[+] Running: join when rally time left minus march time is at least {cfg['safety_margin']}s, "
        f"presets {rot.presets} ({cfg['preset_mode']}).")
    if cfg["monster_mode"] != "off":
        log(f"[+] Monster filter: {cfg['monster_mode']} {cfg['monster_list']} "
            f"(unknown monsters: {cfg['monster_unknown']})")

    while not stop.is_set():
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
            st = screen_state(screen, cfg)
            if st != "rally_list" and time.time() - kick_check[0] > 4:
                kick_check[0] = time.time()
                if reclaim_if_kicked(dev, cfg, log, stop, screen):
                    off_list[0] = None
                    still["since"] = None
                    lost_since[0] = None
                    continue
            if st != "rally_list" and dismiss_popup(screen, st in ("unknown", "city")):
                nap(1.0, 1.5)
                continue

            rallies_on = cfg.get("rally_enabled", True)
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
            if not moving and st in ("rally_list", "unknown"):      # rally countdown bars (green) on the list: their numbers tick every second
                try:
                    hsv = cv2.cvtColor(screen[150:800:3, ::3], cv2.COLOR_BGR2HSV)
                    green = cv2.inRange(hsv, (40, 140, 90), (85, 255, 255))
                    moving = cv2.countNonZero(green) > 150
                except Exception:
                    pass
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
                force_restart_game(dev, cfg, log, stop)
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
                    if now >= aj_next[0]:
                        aj_next[0] = now + auto_join_renew(dev, cfg, log, stop, act)
                    elif tasks_active(cfg, serial) and now >= city_block[0] and (due := city_tasks_due(cfg, city_last, serial)):
                        log(f"[*] Auto-Join is running - going to the city for {len(due)} task(s)")      # the game joins rallies by itself meanwhile
                        if reach_city():
                            run_city_tasks(dev, cfg, log, stop, due, city_last)
                        else:
                            log("[!] Could not reach the city screen - city tasks paused for 10 minutes")
                            task_event(serial, "Tasks", False, "could not reach the city screen")
                            city_block[0] = time.time() + 600
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
                        log("[!] Could not reach the city screen - tasks paused for 10 minutes")
                        task_event(serial, "Tasks", False, "could not reach the city screen")
                        city_block[0] = time.time() + 600
                    continue
                nap(5, 8)                                         # rallies are off: never fall through to joining
                continue

            target = find_target(screen)
            if _game_day() != city_day[0]:                       # the game reset: every task starts again
                city_day[0] = _game_day()
                city_last.clear()
                log("[*] The game day reset - all tasks start again")
            if (not target or cfg.get("tasks_first", True) or run_now_list(serial)) and tasks_active(cfg, serial) and st == "rally_list" and now >= city_block[0]:
                due = city_tasks_due(cfg, city_last, serial)
                if due:
                    log(f"[*] {'No rally waiting' if not target else 'A task is due, it goes before rallies'} - going to the city for {len(due)} task(s)")
                    if reach_city():
                        run_city_tasks(dev, cfg, log, stop, due, city_last)
                    else:
                        log("[!] Could not reach the city screen - city tasks paused for 10 minutes")
                        task_event(serial, "Tasks", False, "could not reach the city screen")
                        city_block[0] = time.time() + 600
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
            t_deadline = t_join + left - (last_march[0] or 0) - 2     # latest moment to press March and still arrive in time
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
                        if not refill_stamina(dev, cfg, log, stop, sta[1]):
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
                log("[!] Too many errors in a row - stopping this instance.")
                notify(cfg, serial, "Too many errors in a row - this instance stopped.")
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
                except Exception:
                    pass
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


def start_instance(serial, adb, cfg, log, ctl, on_frame=None, on_end=None):
    """Start (or restart) one emulator's bot in its own thread. ctl = DeviceControl for pause/stop."""
    if INSTANCE_GUARD is not None and not INSTANCE_GUARD(serial):
        log("[!] License does not allow this emulator.")
        return None
    ctl.reset()

    def work():
        try:
            crashes = 0
            while True:
                try:
                    run_device(serial, adb, cfg, log, ctl.stop, on_frame, ctl)
                    break
                except Exception as e:                       # e.g. the PC ran out of memory for a moment: do not die silently
                    log(f"[!] {e}")
                    crashes += 1
                    if ctl.stop.is_set() or crashes > 6:
                        if crashes > 6:
                            log("[!] This instance crashed too many times - stopped.")
                        break
                    import gc
                    gc.collect()
                    log(f"[*] This instance crashed - starting it again in 60 s ({crashes}/6)")
                    if ctl.stop.wait(60):
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
