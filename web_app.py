# Copyright (c) 2026 Kiravik. All rights reserved. RallyForge Bot was created by Kiravik.
"""
RallyForge Bot - the app.

The bot engine (rally_bot.py) is unchanged. This file starts a tiny web server that only listens on this PC
(127.0.0.1) and shows the interface in the ui/ folder inside a small window (Edge WebView2 through pywebview,
or an Edge / Chrome "app" window as a fallback). Closing the window does not stop the bots: they keep running
quietly (about 100 MB) and starting the app again brings the window back.

Run from source with:  python web_app.py
"""
import base64
import collections
import datetime
import json
import mimetypes
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np

import rally_bot as rb
import rf_license

UI_DIR = (Path(os.environ["RF_APP_DIR"]) / "ui") if os.environ.get("RF_APP_DIR") and (Path(os.environ["RF_APP_DIR"]) / "ui" / "index.html").exists() else rb.RES / "ui"
STATE_FILE = rb.USER / "ui.json"                 # port + key of the running app, so a second start can reopen the window
NONE = "(none)"
EXAMPLE_TASKS = [
    {"name": "Claim login gifts", "enabled": False, "target": "icon:Login Gifts", "every_min": 720, "count": 1,
     "steps": ["wait 1.5", "tap_text Claim|Collect|Free", "wait 1"]},
    {"name": "Collect resources", "enabled": False, "target": "building:Sawmill", "every_min": 60, "count": 6,
     "steps": ["wait 1", "tap_text Collect|Claim"]},
    {"name": "Check mail", "enabled": False, "target": "icon:Mail", "every_min": 180, "count": 1,
     "steps": ["wait 1.5", "tap_text Claim All|Read All|Claim"]},
]


def png_bytes(img, size=None):
    if size:
        img = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes()


def jpg_bytes(img, quality=80):
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes()


def short(serial):
    return serial.split(":")[-1] if ":" in serial else serial


class ApiError(Exception):
    pass


class Controller:
    """Everything the old window did (emulator list, start / stop, schedules, Telegram, updates) without any
    drawing code: the interface asks for the state and sends commands through the web API."""

    def __init__(self):
        self.lock = threading.RLock()
        try:
            rb.backup_before_new_version()                     # a copy of your settings before this version changes anything
        except Exception as _e:
            rb.quiet_err("Controller.__init__", _e)
        self.cfg = rb.load_config()
        self.lic = rf_license.AccountLicense(rb.USER, rb.RES)
        self.lic.set_source(self.cfg.get("update_url"))
        devs = [d for d in (self.cfg.get("devices") or []) if d != "auto"]
        self.first_run = not devs and not rb.CONFIG.exists()
        if not devs and not self.first_run:
            devs = [self.cfg.get("device_id")] if self.cfg.get("device_id") else []
        names = self.cfg.get("device_names", {})
        self.devices = [{"id": d, "name": names.get(d, "")} for d in devs]
        self.removed = [str(x) for x in (self.cfg.get("removed_devices") or []) if x]   # emulators you removed: Detect never adds them again
        self.dev = {}                                   # serial -> runtime record
        self.start_queue = []
        self.start_eta = {}                             # serial -> when Start all will start it (instance list countdown)
        self.logs = collections.deque(maxlen=2500)      # (id, serial, name, text)
        self.acts = collections.deque(maxlen=6000)      # (id, serial, name, dict) - the Activity screen (this game day)
        self.seq = 0
        self._acts_day = rb._game_day()
        self._load_acts()
        threading.Thread(target=self._backfill_days, daemon=True).start()
        self.events = collections.deque(maxlen=60)     # (id, kind, data) the interface should react to once
        self.shots = collections.OrderedDict()          # token -> screenshot (540x960)
        self.scan_found = []
        self.city_jobs = {}                                   # serial -> {"running", "stop", "progress"}: every instance maps its own city, the others stay free
        self.jobs = {"city": {"running": False, "stop": None}, "generals": {"running": False, "stop": None, "count": 0}, "catalog": {"running": False, "stop": None, "count": 0}}
        self.tg = None
        self.tg_ready = False
        self.tg_last = 0.0
        self.tg_status = "Not connected"
        self.restart_pending = False
        self.quit_event = threading.Event()
        self.window_open = True
        self.last_poll = time.time()
        self._ram_warned = 0
        self._trim_n = 0
        self._mem_log_at = time.time()
        self.sync_devices()
        threading.Thread(target=self._maintenance, daemon=True).start()

    # ------------------------------------------------------------------ log + events
    def log(self, msg, serial=""):
        with self.lock:
            self.seq += 1
            name = ""
            if serial and serial in self.dev:
                name = self.dev[serial]["name"] or "Emulator " + str(self.dev[serial]["idx"])
            if msg.startswith("@act "):                  # structured activity: shown on the Activity screen, not in the log
                try:
                    d = json.loads(msg[5:])
                    rec = self.dev.get(serial)
                    if rec is not None:                  # in the middle of a join? (an update restart waits for it)
                        if d.get("k") == "step" and str(d.get("text", "")).startswith(("Joining", "Trying preset", "Choosing generals", "Checking stamina", "Marching")):
                            rec["busy_until"] = time.time() + 60
                        elif d.get("k") in ("join", "fail") or (d.get("k") == "step" and str(d.get("text", "")).startswith(("Looking", "Idle"))):
                            rec["busy_until"] = 0
                    d["t"] = time.strftime("%H:%M:%S")
                    gd = rb._game_day()
                    if gd != self._acts_day:
                        self._new_game_day(gd)
                    self.acts.append((self.seq, serial, name, d))
                    if d.get("k") in ("join", "skip", "fail"):
                        self._save_act(serial, name, d)
                    if serial and d.get("k") == "join":
                        self.day_count(serial, "joins", str(d.get("mon") or ""))
                    elif serial and d.get("k") == "skip" and str(d.get("why", "")).startswith("not enough time"):
                        self.day_count(serial, "late")
                except Exception as _e:
                    rb.quiet_err("Controller.log", _e)
                return
            self.logs.append((self.seq, serial, name, msg))
            if serial:
                rb.log_to_file(f"[{name}] {msg}")
            else:
                rb.log_to_file(msg)
            if serial and serial in self.dev:
                self._note_status(self.dev[serial], msg)
                low = msg.lower()
                if msg.startswith("[!]") and ("restarting evony" in low or "restarting the emulator" in low):
                    self.day_count(serial, "restarts")

    def event(self, kind, data=None):
        with self.lock:
            self.seq += 1
            self.events.append((self.seq, kind, data))

    def toast(self, kind, msg):
        self.event("toast", {"kind": kind, "msg": msg})

    DAY_FILE = rb.USER / "daily_stats.json"            # per instance, per game day: joins, late skips, monsters, restarts (40 days kept - Reports page)
    ACT_DIR = rb.USER / "activity"                     # acts_<game day>.jsonl: every join / skip / fail of that game day (40 days kept)
    KEEP_DAYS = 40

    def _act_file(self, day):
        return self.ACT_DIR / f"acts_{day}.jsonl"

    def _load_acts(self):
        """Today's monster joins come back after an app restart; they start from 0 only at the game's daily reset."""
        try:
            for line in self._act_file(self._acts_day).read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                    self.seq += 1
                    self.acts.append((self.seq, r.get("s", ""), r.get("n", ""), r.get("a") or {}))
                except Exception:
                    continue
        except Exception as _e:
            rb.quiet_err("Controller._load_acts", _e)

    def _save_act(self, serial, name, d):
        try:
            self.ACT_DIR.mkdir(parents=True, exist_ok=True)
            with open(self._act_file(self._acts_day), "a", encoding="utf-8") as f:
                f.write(json.dumps({"s": serial, "n": name, "a": d}) + "\n")
        except Exception as _e:
            rb.quiet_err("Controller._save_act", _e)

    def _new_game_day(self, day):
        """Daily reset: the Activity screen starts again from 0; files older than KEEP_DAYS go."""
        self._acts_day = day
        self.acts.clear()
        self.seq += 1
        self.events.append((self.seq, "acts_reset", {}))
        try:
            for f in sorted(self.ACT_DIR.glob("acts_*.jsonl"))[:-self.KEEP_DAYS]:
                f.unlink()
        except Exception as _e:
            rb.quiet_err("Controller._new_game_day", _e)

    def day_count(self, serial, key, mon=""):
        """Today card numbers that survive an app restart. Called with self.lock held."""
        try:
            if not hasattr(self, "_days"):
                try:
                    self._days = json.loads(self.DAY_FILE.read_text(encoding="utf-8"))
                except Exception:
                    self._days = {}
            day = rb._game_day()
            per = self._days.setdefault(serial, {})
            row = per.setdefault(day, {"joins": 0, "late": 0, "restarts": 0, "monsters": {}})
            row[key] = int(row.get(key, 0)) + 1
            if key == "joins" and mon:
                base = re.sub(r"\s+\d+$", "", mon).strip()
                row.setdefault("monsters", {})[base] = row.get("monsters", {}).get(base, 0) + 1
            for old in sorted(per)[:-self.KEEP_DAYS]:
                per.pop(old, None)
            rb.save_json(self.DAY_FILE, self._days, indent=None)
        except Exception as _e:
            rb.quiet_err("Controller.day_count", _e)

    BACKFILL_DONE = rb.USER / "daily_stats_backfill.done"

    def _backfill_days(self):
        """Once per PC: Reports gets the days from before it saved numbers (versions before 2.5.2),
        rebuilt from the bot log files (kept 7 days). Only log lines older than daily_stats.json are counted."""
        if self.BACKFILL_DONE.exists():
            return
        try:
            cutoff = self.DAY_FILE.stat().st_ctime if self.DAY_FILE.exists() else time.time()
            ids = {}
            for d in self.devices:
                ids[d["id"]] = d["id"]
                if d.get("name"):
                    ids[d["name"]] = d["id"]
            line_re = re.compile(r"^(\d\d:\d\d:\d\d) \[([^\]]+)\] (.*)$")
            add, last_mon = {}, {}
            for f in sorted(rb.LOG_DIR.glob("bot_*.log")):
                day = f.stem[4:]
                if not (len(day) == 8 and day.isdigit()):
                    continue
                with open(f, encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        m = line_re.match(line)
                        if not m or m.group(2) not in ids:
                            continue
                        hms, who, msg = m.groups()
                        t = time.mktime(time.strptime(day + hms, "%Y%m%d%H:%M:%S"))
                        if t >= cutoff:
                            continue
                        serial = ids[who]
                        if msg.startswith("[*] Rally vs "):
                            last_mon[serial] = msg[13:].split(" with ")[0].strip()
                            continue
                        if msg.startswith("[+] Joined rally #"):
                            key = "joins"
                        elif "-> skip (not enough time" in msg:
                            key = "late"
                        elif msg.startswith("[!]") and ("restarting evony" in msg.lower() or "restarting the emulator" in msg.lower()):
                            key = "restarts"
                        else:
                            continue
                        gday = time.strftime("%Y-%m-%d", time.gmtime(t - rb.reset_min(t) * 60))
                        row = add.setdefault(serial, {}).setdefault(gday, {"joins": 0, "late": 0, "restarts": 0, "monsters": {}})
                        row[key] += 1
                        if key == "joins":
                            mon = re.sub(r"\s+\d+$", "", last_mon.pop(serial, "") or "").strip()
                            if mon:
                                row["monsters"][mon] = row["monsters"].get(mon, 0) + 1
            with self.lock:
                self.day_rows("")
                for serial, per in add.items():
                    have = self._days.setdefault(serial, {})
                    for gday, row in per.items():
                        cur = have.setdefault(gday, {"joins": 0, "late": 0, "restarts": 0, "monsters": {}})
                        for k in ("joins", "late", "restarts"):
                            cur[k] = int(cur.get(k, 0) or 0) + row[k]
                        mons = cur.setdefault("monsters", {})
                        for mon, n in row["monsters"].items():
                            mons[mon] = mons.get(mon, 0) + n
                    for old in sorted(have)[:-self.KEEP_DAYS]:
                        have.pop(old, None)
                self.DAY_FILE.parent.mkdir(parents=True, exist_ok=True)
                rb.save_json(self.DAY_FILE, self._days, indent=None)
            self.BACKFILL_DONE.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
        except Exception as _e:
            rb.quiet_err("Controller._backfill_days", _e)

    def day_rows(self, serial):
        if not hasattr(self, "_days"):
            try:
                self._days = json.loads(self.DAY_FILE.read_text(encoding="utf-8"))
            except Exception:
                self._days = {}
        return dict(self._days.get(serial) or {})

    def _note_status(self, d, msg):
        if "[+] Connected" in msg:
            d["connected"] = True
        elif "Joined rally #" in msg:
            m = re.search(r"#(\d+)", msg)
            d["joins"] = int(m.group(1)) if m else d["joins"] + 1
        elif msg.startswith("[!]") and not d["connected"]:
            d["failed"] = True

    # ------------------------------------------------------------------ devices
    def sync_devices(self):
        with self.lock:
            serials = [d["id"] for d in self.devices]
            for s in list(self.dev):
                if s not in serials:
                    rec = self.dev.pop(s)
                    rec["ctl"].stop.set()
                    rec["ctl"].resume()
            for i, d in enumerate(self.devices, 1):
                s = d["id"]
                if s not in self.dev:
                    self.dev[s] = {"ctl": rb.DeviceControl(), "thread": None, "joins": 0, "state": "idle",
                                   "connected": False, "failed": False, "idx": i, "name": d["name"]}
                self.dev[s]["idx"], self.dev[s]["name"] = i, d["name"]

    def n_active(self):
        """Emulators running now or in the middle of starting (counted against the license)."""
        return sum(1 for s, d in self.dev.items() if self.running(s) or d.get("starting"))

    def allowed(self, serial):
        """License check: any number of emulators can be in the list, but only N of them may RUN at the same
        time (N = emulators of the license). One that is already running or starting is always allowed."""
        lim = self.lic.limit()
        if lim is None:
            return True
        d = self.dev.get(serial)
        if d is None:
            return False
        if self.running(serial) or d.get("starting"):
            return True
        return self.n_active() < lim

    def run_cap(self, cfg=None):
        """How many emulators may run at once: the 'at most N at once' setting and the license, whichever is lower (0 = no cap)."""
        cfg = cfg or self.cfg
        caps = [int(cfg.get("max_running_emulators", 0) or 0)]
        lim = self.lic.limit()
        if lim:
            caps.append(int(lim))
        caps = [c for c in caps if c > 0]
        return min(caps) if caps else 0

    def set_devices(self, rows):
        out, seen = [], set()
        for r in rows or []:
            i = str(r.get("id", "")).strip()
            if i and i not in seen:
                seen.add(i)
                out.append({"id": i, "name": str(r.get("name", "")).strip()})
        with self.lock:
            old = [d["id"] for d in self.devices]
            new = [d["id"] for d in out]
            gone = [i for i in old if i not in new]
            removed = [i for i in self.removed if i not in new] + [i for i in gone if i not in self.removed]   # typed back in = wanted again
            changed = removed != self.removed or old != new
            self.removed = removed
            self.devices = out
            self.sync_devices()
        if changed:
            self._persist_devices()

    def _persist_devices(self):
        """Write the emulator list and the removed list to the settings file at once, so a restart of the app or of
        the emulators never brings removed emulators back (even if Save was not pressed)."""
        try:
            with self.lock:
                cfg = rb.load_config()
                ids = [d["id"] for d in self.devices]
                cfg["devices"] = ids
                cfg["device_id"] = ids[0] if ids else ""
                cfg["device_names"] = {d["id"]: d["name"] for d in self.devices if d["name"]}
                cfg["removed_devices"] = list(self.removed)
                rb.save_config(cfg)
                for k in ("devices", "device_id", "device_names", "removed_devices"):
                    self.cfg[k] = cfg[k]
        except Exception as e:
            self.log(f"[!] Could not save the emulator list: {e}")

    def restore_removed(self):
        """Forget the removed list: the next Detect may add those emulators again."""
        with self.lock:
            n = len(self.removed)
            self.removed = []
        self._persist_devices()
        return n

    def running(self, serial):
        d = self.dev.get(serial)
        return bool(d and d["thread"] is not None and d["thread"].is_alive())

    def n_running(self):
        return sum(self.running(s) for s in self.dev)

    def status_list(self):
        out = []
        for dv in self.devices:
            s = dv["id"]
            d = self.dev.get(s)
            if not d:
                continue
            out.append({"id": s, "name": d["name"], "idx": d["idx"], "state": d["state"], "joins": d["joins"],
                        "queued": s in self.start_queue, "paused": d["ctl"].paused, "no_stamina": getattr(d["ctl"], "no_stamina", 0) >= 3,
                        "kicked_at": (int(rb.KICKED.get(s, 0)) if d["state"] == "run" and rb.KICKED.get(s, 0) > time.time() else 0),
                        "loading": (rb.LOADING[s][0] if d["state"] == "run" and s in rb.LOADING else ""),
                        "loading_since": (int(rb.LOADING[s][1]) if d["state"] == "run" and s in rb.LOADING else 0),
                        "start_at": (int(self.start_eta[s]) if d["state"] != "run" and self.start_eta.get(s, 0) > time.time() - 5 else 0),
                        "resume_in": (max(1, int((d["resume_at"] - time.time()) / 60) + 1) if d.get("resume_at") and d["ctl"].paused else 0),
                        "resume_at": (int(d["resume_at"]) if d.get("resume_at") and d["ctl"].paused else 0)})
        return out

    # ------------------------------------------------------------------ config
    def validate(self, cfg):
        def num(key, label, lo=0, hi=None, kind=int):
            try:
                v = kind(float(cfg.get(key)) if kind is int else cfg.get(key))
            except (TypeError, ValueError):
                raise ApiError(f"{label} must be a number.")
            if v < lo or (hi is not None and v > hi):
                raise ApiError(f"{label} must be between {lo} and {hi if hi is not None else 'any'}.")
            return v
        num("safety_margin", "Spare time")
        num("march_guess", "March time if not read", 0, 300)
        num("max_rally_seconds", "Skip rally longer than")
        num("max_joins", "Max joins")
        num("start_gap_seconds", "Gap between emulator starts", 0, 600)
        num("lost_seconds", "Restart Evony if lost for")
        num("click_delay_ms", "Click delay", 100, 6000)
        num("frozen_seconds", "Frozen seconds")
        num("stuck_seconds", "Stuck seconds")
        num("max_emulator_restarts", "Emulator restarts per hour")
        num("adaptive_max", "Slowest it goes", 1, 5, float)
        # general tabs are fixed by fix_general_tabs() before this check: a missing one never stops Start
        if not cfg.get("rally_timer_source"):
            raise ApiError("The rally countdown has to be read from the progress bar or the join button.")
        try:
            rb.parse_schedule(cfg.get("schedule", ""))
        except ValueError as e:
            raise ApiError(f"Default schedule: {e}")
        for s, ov in (cfg.get("per_device") or {}).items():
            if isinstance(ov, dict) and ov.get("schedule"):
                try:
                    rb.parse_schedule(ov["schedule"])
                except ValueError as e:
                    raise ApiError(f"{s}: schedule: {e}")

    @staticmethod
    def _tab_value(v):
        """A general tab as 0-5, or None when it is empty or unreadable ("", "All", null...)."""
        if v in (None, ""):
            return None
        if isinstance(v, str) and not re.fullmatch(r"\s*\d+(\.0+)?\s*", v):
            names = ["leave as is", "all", "military", "development", "duty", "subordinate city"]
            return names.index(v.strip().lower()) if v.strip().lower() in names else None
        try:
            return max(0, min(5, int(float(v))))
        except (TypeError, ValueError):
            return None

    def fix_general_tabs(self, cfg):
        """An empty or wrong general tab must not block Start: it falls back to the global one (or 'Leave as is'),
        and the log says which emulator it was."""
        names = {d["id"]: (d["name"] or d["id"]) for d in self.devices}
        for k in ("general_main_tab", "general_assistant_tab"):
            raw = cfg.get(k)
            v = self._tab_value(raw)
            if v is None and raw not in (None, ""):
                self.log(f"[i] {'Main' if 'main' in k else 'Assistant'} general tab '{raw}' is not valid - using the general tab setting.")
            cfg[k] = v
        for s, ov in (cfg.get("per_device") or {}).items():
            if not isinstance(ov, dict):
                continue
            for k in ("general_main_tab", "general_assistant_tab", "general_tab"):
                if k not in ov:
                    continue
                v = self._tab_value(ov[k])
                if v is None:
                    if ov[k] not in (None, ""):
                        self.log(f"[i] {names.get(s, s)}: {'main' if 'main' in k else 'assistant' if 'assistant' in k else ''} general tab '{ov[k]}' is not valid - using the global setting.")
                    ov.pop(k)
                else:
                    ov[k] = v
        return cfg

    @staticmethod
    def _enabled(bps):
        return [int(b.get("n", i + 1)) for i, b in enumerate(bps or []) if isinstance(b, dict) and b.get("enabled")]

    def sync_presets(self, cfg):
        """Each emulator (and the global settings) had two preset lists: the preset cards (bot_presets) and the
        plain 'Presets (1-8)' box. They now always agree: a box that was changed switches the cards, otherwise the
        box shows what the cards say. This stops 6 armed presets from falling back to 1."""
        try:
            disk = rb.load_config()
        except Exception:
            disk = {}
        boxes = [(cfg, disk)] + [(ov, (disk.get("per_device") or {}).get(s) or {}) for s, ov in (cfg.get("per_device") or {}).items() if isinstance(ov, dict)]
        for box, old in boxes:
            bps = box.get("bot_presets")
            if not isinstance(bps, list) or not bps:
                continue
            on = self._enabled(bps)
            plain = box.get("presets")
            try:
                plain = sorted({int(x) for x in plain}) if isinstance(plain, list) else None
            except (TypeError, ValueError):
                plain = None
            old_plain = old.get("presets") if isinstance(old, dict) else None
            changed_box = plain is not None and plain != on and plain != old_plain and self._enabled(old.get("bot_presets")) == on
            if changed_box and plain:                      # the user typed new numbers into the box: arm those cards
                want = set(plain)
                for i, b in enumerate(bps):
                    if isinstance(b, dict):
                        b["enabled"] = int(b.get("n", i + 1)) in want
            elif "presets" in box or box is cfg:
                box["presets"] = on or [1]                  # the box follows the cards

    OWN_API_KEYS = ("duties", "crazy_eggs", "champions", "wheel")      # changed through their own calls (switches on the Duties page), not by the page's autosave

    def apply_config(self, posted, devices=None, managed=False):
        """Merge what the interface sends into the saved settings (checked first), keep it for the bots.
        managed=True: the app itself changes the Duties / Crazy Eggs / Champions settings (the page's copy may be older)."""
        with self.lock:
            cfg = rb.load_config()
            disk_pd = dict(cfg.get("per_device") or {})
            disk_own = {k: cfg[k] for k in self.OWN_API_KEYS if k in cfg}
            cfg.update(posted or {})
            if not managed:                               # the page's autosave must never undo a switch made through its own call
                for k, v in disk_own.items():
                    cfg[k] = v
            # an emulator's own settings are never lost: what the page did not send stays as saved on disk
            pd = dict(disk_pd)
            pd.update((posted or {}).get("per_device") or {})
            if not managed:
                for ser, ov in list(pd.items()):
                    old = disk_pd.get(ser) or {}
                    ov = dict(ov)
                    for k in self.OWN_API_KEYS:
                        if k in old:
                            ov[k] = old[k]
                    pd[ser] = ov
            cfg["per_device"] = pd
            if devices is not None:
                self.set_devices(devices)
            ids = [d["id"] for d in self.devices]
            cfg["devices"] = ids
            cfg["device_id"] = ids[0] if ids else cfg.get("device_id", "")
            cfg["device_names"] = {d["id"]: d["name"] for d in self.devices if d["name"]}
            cfg["per_device"] = {s: ov for s, ov in (cfg.get("per_device") or {}).items() if ov}   # kept for removed emulators too: add one back and its settings return
            cfg["removed_devices"] = list(self.removed)       # kept by the app, not by the page
            self.fix_general_tabs(cfg)
            self.sync_presets(cfg)
            _mt = cfg.get("general_main_tab")
            cfg["general_tab"] = _mt if _mt not in (None, "") else (cfg.get("general_tab") or 0)
            cfg["general_favourites"] = cfg.get("general_main_fav", "leave")
            cfg["cpu_level"] = int(cfg.get("cpu_level", 2))
            if cfg.get("march_timer_source") == "none":
                cfg["march_timer_source"] = ""
            self.validate(cfg)
            rb.configure_resources(cfg)
            self.cfg = cfg
            return cfg

    def save(self, posted, devices=None, quiet=False, managed=False):
        cfg = self.apply_config(posted, devices, managed=managed)
        rb.save_config(cfg)
        if not quiet:
            self.log("[+] Settings saved.")
        return cfg

    # ------------------------------------------------------------------ start / stop
    def start_instance(self, serial, cfg=None):
        d = self.dev.get(serial)
        if d is not None and self.running(serial) and d["ctl"].paused:       # Start on a paused instance = carry on now (it used to do nothing)
            d["pause_tok"], d["resume_at"] = None, None
            d["ctl"].resume()
            d["state"] = "run"
            self.log("[i] Resumed", serial)
            return
        if d is None or self.running(serial) or d.get("starting"):
            return
        lim = self.lic.limit()
        if lim is not None and not lim:
            self.log("[!] No valid license - open the License tab.", serial)
            return
        cfg = cfg or self.cfg
        with self.lock:                                   # check and reserve the seat together (starts can overlap)
            cap = self.run_cap(cfg)
            if cap > 0 and self.n_active() >= cap:
                if serial not in self.start_queue:
                    self.start_queue.append(serial)
                    why = (f"your plan runs {lim} emulator(s) at once" if lim and cap == lim else f"at most {cap} emulators run at once")
                    self.log(f"[*] Waiting for a free slot ({why}) - it starts when another one stops", serial)
                return
            if serial in self.start_queue:
                self.start_queue.remove(serial)
            d["starting"] = True
        try:
            log = lambda m, s=serial: self.log(m, s)
            adb = rb.prepare(cfg, log)
            if not adb:
                d["failed"], d["state"] = True, "error"
                return
            d["joins"], d["connected"], d["failed"] = 0, False, False
            d["thread"] = rb.start_instance(serial, adb, cfg, log, d["ctl"], on_frame=None,
                                            on_end=lambda s: self._ended(s))
            d["state"] = "run" if d["thread"] is not None else "idle"
        finally:
            d["starting"] = False

    def _ended(self, serial):
        d = self.dev.get(serial)
        if d:
            d["state"] = "error" if d["failed"] else "stop"
        self._start_next_queued()

    def _start_next_queued(self):
        cap = self.run_cap()
        while self.start_queue and (cap <= 0 or self.n_active() < cap):
            s = self.start_queue.pop(0)
            if s in self.dev and not self.running(s):
                self.start_instance(s)

    def toggle_pause(self, serial):
        d = self.dev.get(serial)
        if not d or not self.running(serial):
            return
        if d["ctl"].paused:
            d["pause_tok"], d["resume_at"] = None, None
            d["ctl"].resume()
            d["state"] = "run"
        else:
            d["ctl"].pause()
            d["state"] = "pause"
            try:
                mins = float(rb.merge_device_cfg(self.cfg, serial).get("pause_minutes", 15) or 0)
            except (TypeError, ValueError):
                mins = 15.0
            tok = object()
            d["pause_tok"] = tok if mins > 0 else None
            d["resume_at"] = time.time() + mins * 60 if mins > 0 else None
            if mins > 0:
                self.log(f"[i] Paused for {mins:g} min - it runs again by itself after that", serial)
                t = threading.Timer(mins * 60, self._auto_resume, (serial, tok))
                t.daemon = True
                t.start()

    def _auto_resume(self, serial, tok):
        d = self.dev.get(serial)
        if d and d.get("pause_tok") is tok and d["ctl"].paused and self.running(serial):
            d["pause_tok"], d["resume_at"] = None, None
            d["ctl"].resume()
            d["state"] = "run"
            self.log("[+] The pause time is over - running again", serial)

    def stop_instance(self, serial):
        d = self.dev.get(serial)
        if not d:
            return
        d["pause_tok"], d["resume_at"] = None, None
        if serial in self.start_queue:
            self.start_queue.remove(serial)
        self.start_eta.pop(serial, None)                   # still waiting in Start all: do not start it later
        d["ctl"].stop.set()
        d["ctl"].resume()
        self.log("[*] Stopping...", serial)

    def start_all(self, auto=False):
        if not auto:
            self.resume_all()                           # Start all also carries on with paused instances
        todo = [d["id"] for d in self.devices if d["id"] and not self.running(d["id"])]
        if auto:      # app just opened: each emulator follows its own 'start by itself' switch (falls back to the global one)
            todo = [s for s in todo if rb.merge_device_cfg(self.cfg, s).get("auto_start")]
        lim = self.lic.limit()
        if lim is not None and not lim:
            self.log("[!] No valid license - open the License tab.")
            return
        cap = self.run_cap()
        free = max(0, cap - self.n_active()) if cap else len(todo)
        if cap and len(todo) > free:
            why = f"your plan runs {lim} at once" if lim and cap == lim else f"at most {cap} run at once"
            self.log(f"[+] Starting {min(free, len(todo))} emulator(s); {len(todo) - free} more wait for a free slot ({why}).")
        else:
            self.log(f"[+] Starting {len(todo)} emulator(s)...")
        cfg = self.cfg

        def launcher():
            # the gap only spreads out heavy starts (emulator boot / Evony load). An emulator that is already
            # running with Evony on screen is just picked up, so the next one starts right after it.
            gap = max(0, int(cfg.get("start_gap_seconds", 40) or 0))
            try:
                adb = rb.find_adb(cfg.get("adb_path") or "auto")
            except Exception:
                adb = None
            t0 = time.time()
            for j, s in enumerate(todo):
                self.start_eta[s] = t0 + 0.5 + j * gap         # worst case; refreshed after every start
            for i, s in enumerate(todo):
                if s not in self.start_eta:                     # stopped while it was waiting for its turn
                    continue
                warm = False
                if adb and gap:
                    try:
                        if rb.device_online(adb, s):
                            pkg = rb.evony_package(adb, s, rb.merge_device_cfg(cfg, s))
                            warm = rb.app_foreground(adb, s, pkg) is True
                    except Exception:
                        warm = False
                self.start_eta.pop(s, None)
                threading.Thread(target=self._safe, args=(self.start_instance, s, cfg), daemon=True).start()
                if i < len(todo) - 1:
                    wait_s = 2 if warm else gap
                    t1 = time.time()
                    for j, s2 in enumerate(todo[i + 1:]):
                        if s2 in self.start_eta:
                            self.start_eta[s2] = t1 + wait_s + j * gap
                    time.sleep(wait_s)

        threading.Thread(target=launcher, daemon=True).start()

    def _safe(self, fn, *a):
        try:
            fn(*a)
        except Exception as e:
            self.log(f"[!] {type(e).__name__}: {e}")

    def pause_all(self):
        def sweep():
            for s in list(self.dev):
                if self.running(s) and not self.dev[s]["ctl"].paused:
                    self.toggle_pause(s)
        sweep()
        t = threading.Timer(4, sweep)               # second pass: instances that were still starting when the button was pressed
        t.daemon = True
        t.start()

    def resume_all(self):
        for s in list(self.dev):
            if self.running(s) and self.dev[s]["ctl"].paused:
                self.toggle_pause(s)

    def stop_all(self):
        for s in list(self.dev):
            if self.running(s):
                self.stop_instance(s)
        self.log("[*] Stopping all...")

    def shutdown(self):
        for d in self.dev.values():
            d["ctl"].stop.set()
            d["ctl"].resume()
        self.quit_event.set()

    # ------------------------------------------------------------------ background upkeep
    def _maintenance(self):
        t0 = time.time()
        last = {"sched": 0, "tg": 0, "res": 0, "upd": 0}
        did_names = did_tg = False
        while not self.quit_event.is_set():
            time.sleep(2)
            now = time.time()
            try:
                if not did_names and now - t0 > 3:
                    did_names = True
                    self._auto_names()
                if not did_tg and now - t0 > 5:
                    did_tg = True
                    self._tg_autoconnect()
                if now - last["res"] > 5:
                    last["res"] = now
                    self._resource_tick()
                if now - last["sched"] > 20:
                    last["sched"] = now
                    self._schedule_tick()
                if now - last["tg"] > 30:
                    last["tg"] = now
                    self._tg_tick()
                if now - last.get("backup", 0) > 24 * 3600 and now - t0 > 120:    # one settings backup a day
                    last["backup"] = now
                    rb.auto_backup("daily")
                if now - last.get("clean", 0) > 6 * 3600 and now - t0 > 30:     # keep the data folder small
                    last["clean"] = now
                    rb.housekeeping(lambda m: self.log(m))
                if now - last["upd"] > 1800 or (last["upd"] == 0 and now - t0 > 12):
                    last["upd"] = now
                    if self.cfg.get("update_mode") != "off" and ((self.cfg.get("update_url") or "").strip() or rb.DEFAULT_UPDATE_URL):
                        self.check_updates(False)
            except Exception as e:
                self.log(f"[!] {type(e).__name__}: {e}")

    def _auto_names(self):
        try:
            adb = rb.find_adb(self.cfg.get("adb_path") or "auto")
            names = rb.emulator_names(adb) if adb else {}
        except Exception:
            names = {}
        changed = False
        with self.lock:
            for d in self.devices:
                if not d["name"] and names.get(d["id"]):
                    d["name"] = names[d["id"]]
                    changed = True
            if changed:
                self.sync_devices()
        if changed:
            self.log("[+] Emulator names read from the emulators.")
            self.event("devices")

    def _resource_tick(self):
        info = rb.memory_info()
        self.ram = info
        if info["total_mb"]:
            need = int(self.cfg.get("min_free_ram_mb", 0) or 0)
            if info["free_mb"] < max(need, 300) and time.time() - self._ram_warned > 300:
                self._ram_warned = time.time()
                self.log(f"[!] The PC is low on memory: {info['free_mb']:.0f} MB free.")
                rb.notify(self.cfg, "PC", f"Low on memory: {info['free_mb']:.0f} MB free.")
        self._trim_n += 1
        if self._trim_n % 24 == 0:                       # every ~2 min, for everyone (not only Low memory mode)
            rb.trim_memory()
        if time.time() - self._mem_log_at >= 600:        # one memory line every 10 min: a slow leak shows up over a long run
            self._mem_log_at = time.time()
            self.log(rb.memory_report(info))

    def _schedule_tick(self):
        self._maybe_restart()
        if not self.cfg.get("schedules_on"):
            for d in self.dev.values():
                d["sched_want"] = None
            return
        now = datetime.datetime.now()
        per = self.cfg.get("per_device") or {}
        for s, d in list(self.dev.items()):
            text = (per.get(s, {}).get("schedule") or self.cfg.get("schedule") or "").strip()
            if not text:
                d["sched_want"] = None
                continue
            try:
                want = rb.schedule_active(rb.parse_schedule(text), now)
            except ValueError:
                continue
            prev, d["sched_want"] = d.get("sched_want"), want
            running = self.running(s)
            if want and not running and not prev:
                self.log("[+] Schedule: time to start", s)
                self.start_instance(s)
            elif not want and running and prev:
                self.log("[+] Schedule: time to stop", s)
                self.stop_instance(s)

    # ------------------------------------------------------------------ detect
    def detect(self):
        self.log("[*] Detecting emulators...")
        adb = rb.find_adb(self.cfg.get("adb_path") or "auto")
        if not adb:
            self.log("[!] ADB not found. Set the ADB path in Settings.")
            return {"found": None, "devices": self.devices}
        found = rb.detect_devices(adb)
        names = rb.emulator_names(adb)
        if not found:
            self.log("[!] No emulators found. Start them and enable ADB.")
            return {"found": [], "devices": self.devices}
        with self.lock:
            listed = {d["id"] for d in self.devices}
            # the same emulator can answer as emulator-5556 and as 127.0.0.1:5557 - keep one of them
            same = {}
            for dev in found:
                m = re.fullmatch(r"emulator-(\d+)", dev)
                if m:
                    same[dev] = f"127.0.0.1:{int(m.group(1)) + 1}"
            dupes = [e for e, ip in same.items() if ip in found and e not in listed]
            dupes += [ip for e, ip in same.items() if ip in found and e in listed and ip not in listed]
            found = [f for f in found if f not in dupes]
            skipped = [f for f in found if f in self.removed and f not in listed]
            new = [f for f in found if f not in listed and f not in skipped]
            self.log(f"[+] Found {len(found)}: {', '.join(found)}")
            if dupes:
                self.log(f"[i] Same emulator under two names, kept one: {', '.join(dupes)}")
            # nothing is added by itself: the found emulators are offered with an Add button each
            def _label(f):                                # no name found for this emulator: a readable one from its address
                n = names.get(f, "")
                if n:
                    return n
                m = re.search(r"(\d+)$", f)
                return f"Emulator {m.group(1)}" if m else ""
            self.available = [{"id": f, "name": _label(f), "removed": f in skipped} for f in new + skipped]
            by_id = {d["id"]: d for d in self.devices}
            changed = False
            for dev in found:                                 # emulators already in the list: fill in a missing name
                if dev in by_id and not by_id[dev]["name"] and names.get(dev):
                    by_id[dev]["name"] = names[dev]
                    changed = True
            if changed:
                self.sync_devices()
        if changed:
            self._persist_devices()
        if self.available:
            self.log(f"[i] {len(self.available)} emulator(s) not in your list yet - press Add next to the ones you want")
        return {"found": found, "available": self.available, "dupes": dupes, "devices": self.devices}

    def add_found(self, ids):
        """Add emulators from the 'Found emulators' list (one click each, or all)."""
        with self.lock:
            rows = [d for d in self.devices if d["id"]]
            have = {d["id"] for d in rows}
            names = {a["id"]: a["name"] for a in getattr(self, "available", [])}
            for i in ids:
                if i and i not in have:
                    rows.append({"id": i, "name": names.get(i, "")})
                    have.add(i)
            self.available = [a for a in getattr(self, "available", []) if a["id"] not in have]
        self.set_devices(rows)                                # saves at once and takes them off the removed list
        return self.devices

    # ------------------------------------------------------------------ screenshots
    def keep_shot(self, img):
        token = secrets.token_hex(6)
        self.shots[token] = img
        while len(self.shots) > 4:
            self.shots.popitem(last=False)
        return {"token": token, "w": img.shape[1], "h": img.shape[0]}

    def shot_from_device(self, serial):
        adb = rb.find_adb(self.cfg.get("adb_path") or "auto")
        if not adb:
            raise ApiError("ADB not found. Set the ADB path in Settings.")
        try:
            dev = rb.Device(serial, adb)
            dev.connect()
            img = dev.screenshot()
        except Exception as e:
            raise ApiError(f"Could not take a screenshot from {serial}: {e}")
        return self.keep_shot(img)

    def shot_from_upload(self, b64):
        raw = base64.b64decode(b64.split(",")[-1])
        img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise ApiError("Could not read that image.")
        if img.shape[1::-1] != rb.EXPECTED_SIZE:
            img = cv2.resize(img, rb.EXPECTED_SIZE, interpolation=cv2.INTER_AREA)
        return self.keep_shot(img)

    def shot(self, token):
        img = self.shots.get(token)
        if img is None:
            raise ApiError("That screenshot is no longer available. Take it again.")
        return img

    # ------------------------------------------------------------------ generals
    def generals_info(self):
        out, cat = [], rb.load_catalog()
        key = lambda t: re.sub(r"[^a-z0-9]", "", str(t).lower())
        known = [key(k) for k in rb.KNOWN_GENERALS]

        def real(n):                                          # hide junk names left by poor scans ("a ae", "all tie")
            k = key(n)
            return len(k) >= 4 and (rb.is_known_general(rb.snap_name(n, [])) or any(x.startswith(k) or k in x for x in known))
        pictured = set()
        allpics = rb.list_generals(refresh=True)
        full = [key(m) for m in allpics if rb.is_known_general(m)]
        for n in allpics:
            if not real(n):
                continue
            if not rb.is_known_general(n) and any(k != key(n) and k.startswith(key(n)) for k in full):
                continue                                      # "Sanada" when "Sanada Yukimura" has a picture too: show the full name only
            pictured.add(key(n))
            m = rb.get_general_meta(n)
            c = cat.get(n) or {}
            out.append({"name": n, "tab": m.get("tab"), "fav": m.get("fav"), "copy": m.get("copy", "any"), "tabs": m.get("tabs", []),
                        "region": c.get("region", []), "troops": c.get("troops", []), "cat_tab": rb.catalog_tab(n) if c else 0, "cat": m.get("cat", ""), "pic": True})
        hidden = set(rb.load_general_meta().get("__hidden__", []))
        for n in rb.KNOWN_GENERALS:                           # every general of the game can be listed, also without a picture (found by name)
            if key(n) in pictured or n in hidden:
                continue
            m = rb.get_general_meta(n)
            out.append({"name": n, "tab": m.get("tab"), "fav": m.get("fav"), "copy": m.get("copy", "any"), "tabs": m.get("tabs", []),
                        "region": [], "troops": [], "cat_tab": 0, "cat": m.get("cat", ""), "pic": False})
        out.sort(key=lambda g: (not g["pic"], g["name"].lower()))
        return out

    def ready_device(self, serial, adb, stop, check_ready=True):
        """A Device that is ready to use: starts the emulator and Evony first when those settings are on."""
        dev = rb.Device(serial, adb)
        cfg = rb.merge_device_cfg(rb.load_config(), serial)
        if cfg.get("auto_launch_emulator") or cfg.get("auto_launch_game"):
            if not rb.bring_online(dev, cfg, lambda m: self.log(m), stop, check_ready=check_ready):
                raise ApiError("Stopped." if stop.is_set() else "Could not get the emulator and Evony ready.")
        else:
            dev.connect()
        return dev

    def scan_generals(self, serial, mode="all"):
        job = self.jobs["generals"]
        if job["running"]:
            raise ApiError("A scan is already running.")
        adb = rb.find_adb(self.cfg.get("adb_path") or "auto")
        if not adb:
            raise ApiError("ADB not found. Set the ADB path in Settings.")
        stop = threading.Event()
        job.update(running=True, stop=stop, count=0)
        try:                                                  # leftovers of an earlier scan whose names could not be read
            for p in (rb.USER / "generals").glob("General *.png"):
                if re.fullmatch(r"General \d+", p.stem):
                    p.unlink()
            rb.list_generals(refresh=True)
        except Exception as _e:
            rb.quiet_err("Controller.scan_generals", _e)

        def work():
            try:
                dev = self.ready_device(serial, adb, stop)
                prog = lambda n: job.update(count=n)
                if mode == "current":
                    found = rb.scan_generals(dev, rb.load_config(), lambda m: self.log(m), stop, progress=prog)
                    for f in found:
                        f["tabs"] = sorted(t for t in f.get("tabs", ()) if 1 <= t <= 5)
                else:
                    found = rb.scan_all_tabs(dev, rb.load_config(), lambda m: self.log(m), stop, progress=prog)
                self.scan_found = found or []
                self.event("scan_done", {"n": len(self.scan_found)})
            except Exception as e:
                self.event("scan_done", {"error": str(e)})
            finally:
                job["running"] = False
        threading.Thread(target=work, daemon=True).start()

    def crawl_catalog(self, serial, merge=False):
        """Developer tool: record every general of Tavern > Portraits (all regions x troop types)."""
        job = self.jobs["catalog"]
        if job["running"]:
            raise ApiError("The catalogue crawl is already running.")
        adb = rb.find_adb(self.cfg.get("adb_path") or "auto")
        if not adb:
            raise ApiError("ADB not found. Set the ADB path in Settings.")
        stop = threading.Event()
        job.update(running=True, stop=stop, count=0)

        def work():
            try:
                cfg_now = rb.load_config()
                dev = self.ready_device(serial, adb, stop, check_ready=False)     # the Portraits screen is not a screen the bot knows
                if not (rb.find_text(dev.screenshot(), ["Portraits"], region=(120, rb.black_top(dev.screenshot()), 300, 100))):
                    rb.open_tavern_portraits(dev, cfg_now, lambda m: self.log(m), stop)
                found = rb.crawl_portraits(dev, rb.load_config(), lambda m: self.log(m), stop,
                                           progress=lambda n: job.update(count=n))
                if merge:                                     # General Manager > Scan Tavern portraits: add to the library, never wipe it
                    added = rb.merge_catalog({g["name"]: g for g in found} if isinstance(found, list) else found, lambda m: self.log(m))
                    root = rb.USER / "catalog"
                else:
                    added = None
                    root = rb.save_catalog(found, lambda m: self.log(m))
                with rb._cache_lock:
                    rb._gen_cache.clear()
                rb.list_generals(refresh=True)
                self.event("catalog_done", {"n": len(found), "path": str(root), "added": added})
            except Exception as e:
                self.log(f"[!] Catalogue crawl: {e}")
                self.event("catalog_done", {"error": str(e)})
            finally:
                job["running"] = False
        threading.Thread(target=work, daemon=True).start()

    def capture_city(self, serial):
        """Developer tool: save screenshots of the whole city (see rb.capture_city_frames)."""
        job = self.jobs["city"]
        if job["running"]:
            raise ApiError("The city mapper is already running.")
        adb = rb.find_adb(self.cfg.get("adb_path") or "auto")
        if not adb:
            raise ApiError("ADB not found. Set the ADB path in Settings.")
        stop = threading.Event()
        job.update(running=True, stop=stop)
        cfg = dict(self.cfg)

        def work():
            try:
                dev = self.ready_device(serial, adb, stop)
                safe = "".join(c if c.isalnum() else "_" for c in serial)
                rb.capture_city_frames(dev, cfg, lambda m: self.log(m), stop, rb.USER / "citycap" / safe)
            except Exception as e:
                self.log(f"[!] City capture: {e}")
            finally:
                job["running"] = False
                self.event("city_done")
        threading.Thread(target=work, daemon=True).start()

    def city_job(self, serial, start=False):
        """The city-mapper job of one instance. start=True checks that this instance can be mapped now and claims it."""
        job = self.city_jobs.setdefault(serial, {"running": False, "stop": None, "progress": None})
        if start:
            if job["running"]:
                raise ApiError("This instance is already mapping its city.")
            d = self.dev.get(serial)
            if d and d.get("state") == "run":
                raise ApiError("Stop (or pause) this instance first - the mapper and the bot would both use its screen. The other instances can keep running.")
        return job

    def city_any_running(self):
        return any(j["running"] for j in self.city_jobs.values())

    def city_jobs_state(self):
        return {k: {"running": j["running"], "progress": j.get("progress")} for k, j in self.city_jobs.items() if j["running"]}

    def scan_city(self, serial):
        job = self.city_job(serial, start=True)
        adb = rb.find_adb(self.cfg.get("adb_path") or "auto")
        if not adb:
            raise ApiError("ADB not found. Set the ADB path in Settings.")
        stop = threading.Event()
        job.update(running=True, stop=stop, progress=None)
        cfg = rb.merge_device_cfg(rb.load_config(), serial)          # this instance's own settings

        def work():
            try:
                dev = self.ready_device(serial, adb, stop)
                rb.scan_city(dev, cfg, lambda m: self.log(m), stop, progress=lambda d: job.update(progress=d))
            except Exception as e:
                self.log(f"[!] City mapper: {e}")
            finally:
                job["running"] = False
                job["progress"] = None
                self.event("city_done", {"serial": serial})
        threading.Thread(target=work, daemon=True).start()

    # ---- map the city by hand: the user clicks each building in a live picture of the emulator
    def manual_open(self, serial):
        if self.city_job(serial)["running"]:
            raise ApiError("This instance is already mapping its city.")
        adb = rb.find_adb(self.cfg.get("adb_path") or "auto")
        if not adb:
            raise ApiError("ADB not found. Set the ADB path in Settings.")
        stop = threading.Event()
        m = {"serial": serial, "busy": True, "stop": stop, "last": None, "nav": None, "dev": None, "status": "Going to the top-left corner of the city..."}
        self.manual = m

        def work():
            try:
                dev = self.ready_device(serial, adb, stop)
                dev.arm_guard(dict(self.cfg), lambda t: self.log(t), stop)
                dev.guard_city = True
                nav = rb.CityNav(dev, stop, lambda t: self.log(t))
                nav.to_origin()
                m.update(dev=dev, nav=nav, status="Ready")
                m["last"] = dev.screenshot()
            except Exception as e:
                m["status"] = f"Error: {e}"
            finally:
                m["busy"] = False
        threading.Thread(target=work, daemon=True).start()

    def manual_state(self):
        m = getattr(self, "manual", None)
        if not m:
            return {"open": False}
        have = {}
        if m.get("dev") is not None:
            data = rb.load_city().get(m["serial"]) or {}
            for k in (data.get("items") or {}):
                have[k.split("#")[0]] = have.get(k.split("#")[0], 0) + 1
        return {"open": True, "busy": m["busy"], "status": m["status"], "have": have,
                "names": rb.EXPECTED_BUILDINGS, "optional": list(rb.OPTIONAL_BUILDINGS)}

    def manual_pan(self, dx, dy):
        m = getattr(self, "manual", None)
        if not m or m["busy"] or not m.get("nav"):
            raise ApiError("The camera is not ready yet.")
        m["busy"] = True
        try:
            m["nav"].pan(float(dx), float(dy))
            m["last"] = m["dev"].screenshot()
        finally:
            m["busy"] = False

    def manual_shot_image(self):
        m = getattr(self, "manual", None)
        if not m or m.get("dev") is None:
            return None
        if not m["busy"]:
            m["last"] = m["dev"].screenshot()
        return m.get("last")

    def manual_pick(self, name, x, y):
        m = getattr(self, "manual", None)
        if not m or m["busy"] or not m.get("nav"):
            raise ApiError("The camera is not ready yet.")
        nav, serial = m["nav"], m["serial"]
        shot = m.get("last")
        data = rb.load_city()
        ent = data.setdefault(serial, {"items": {}})
        items = ent.setdefault("items", {})
        k = 1
        while f"{name}#{k}" in items and k < 1:
            k += 1
        key = f"{name}#1"                                     # a hand-placed building replaces the first one of that name
        items[key] = [round(nav.cx + float(x)), round(nav.cy + float(y))]
        ent["missing"] = rb.city_report(items)[1]
        ent["manual"] = sorted(set(ent.get("manual", [])) | {key})
        filled = rb.fill_from_squares(ent)                    # the rest of the fixed buildings follow from the squares
        ent["missing"] = rb.city_report(items)[1]
        rb.save_city(data)
        if shot is not None:
            rb.save_building_picture(serial, key, shot, float(x), float(y))
        self.log(f"[+] City map by hand: {name} placed at {items[key][0]},{items[key][1]}"
                 + (f" - {filled} other building(s) filled in from the squares" if filled else ""))

    def slots_info(self, serial):
        """The numbered plots and which building of this emulator sits in which one."""
        data = rb.load_city().get(serial) or {}
        items = data.get("items") or {}
        slots = rb.load_slots()
        if not slots and items:
            slots = rb.build_slots(items)
            rb.save_slots(slots)
        sel, taken = {}, set()
        for k, (x, y) in sorted(items.items()):
            n = k.split("#")[0]
            if n in rb.RESOURCE_BUILDINGS or n in sel:
                continue
            sel[n] = rb.nearest_slot(slots, x, y, name=n, taken=taken)
            if sel[n]:
                taken.add(sel[n])
        return {"slots": slots, "assigned": sel, "names": rb.EXPECTED_BUILDINGS, "optional": list(rb.OPTIONAL_BUILDINGS)}

    def slot_assign(self, serial, building, slot):
        slot = int(slot)
        data = rb.load_city()
        ent = data.setdefault(serial, {"items": {}})
        items = ent.setdefault("items", {})
        for k in [k for k in items if k.split("#")[0] == building]:
            items.pop(k, None)
        if slot > 0:
            pos = next((p for p in rb.load_slots() if p["n"] == slot), None)
            if not pos:
                raise ApiError("There is no such slot.")
            items[f"{building}#1"] = [pos["x"], pos["y"]]
            ent["manual"] = sorted(set(ent.get("manual", [])) | {f"{building}#1"})
        ent["missing"] = rb.city_report(items)[1]
        rb.save_city(data)
        self.log(f"[+] City map: {building} -> slot {slot}" if slot > 0 else f"[+] City map: {building} cleared")

    def manual_goto(self, slot):
        m = getattr(self, "manual", None)
        if not m or m["busy"] or not m.get("nav"):
            raise ApiError("The camera is not ready yet.")
        pos = next((p for p in rb.load_slots() if p["n"] == int(slot)), None)
        if not pos:
            raise ApiError("There is no such slot.")
        m["busy"] = True
        try:
            m["nav"].goto(pos["x"], pos["y"])
            m["last"] = m["dev"].screenshot()
        finally:
            m["busy"] = False

    def manual_close(self):
        m = getattr(self, "manual", None)
        if m:
            m["stop"].set()
            if m.get("dev") is not None:
                m["dev"].guard_city = False
        self.manual = None

    # ---- developer tool: record a flow played by hand (taps + before/after pictures)
    def flow_start(self, serial, name):
        if getattr(self, "flow", None) and not self.flow.stop_ev.is_set():
            raise ApiError("A flow is already being recorded. Stop it first.")
        if self.running(serial):
            raise ApiError("Stop this instance first - its own taps would be recorded too.")
        adb = rb.find_adb(self.cfg.get("adb_path") or "auto")
        if not adb:
            raise ApiError("ADB not found. Set the ADB path in Settings.")
        rec = rb.FlowRecorder(serial, adb, name or "flow", lambda t: self.log(t, serial))
        try:
            rec.start()
        except Exception as e:
            raise ApiError(f"Could not start recording: {e}")
        self.flow = rec
        return {"path": str(rec.dir)}

    def flow_state(self):
        r = getattr(self, "flow", None)
        if not r:
            return {"active": False}
        return {"active": not r.stop_ev.is_set(), "steps": len(r.steps), "path": str(r.dir), "error": r.error,
                "last": r.steps[-1] if r.steps else None}

    def flow_stop(self):
        r = getattr(self, "flow", None)
        if not r or r.stop_ev.is_set():
            return {"steps": 0, "path": str(r.dir) if r else ""}
        return r.stop()

    def city_info(self):
        data = rb.load_city()
        return [f"{k}: {len(v.get('items', {}))} buildings" for k, v in data.items()]

    # ------------------------------------------------------------------ telegram
    def _tg_keys(self):
        try:
            import telethon  # noqa: F401
        except ImportError:
            raise ApiError("The Telegram library is missing. Run:  pip install telethon  (the downloadable app already includes it).")
        try:
            api_id = int(str(self.cfg.get("tg_api_id", "")).strip())
        except ValueError:
            raise ApiError("api_id must be a number (from my.telegram.org).")
        h = str(self.cfg.get("tg_api_hash", "")).strip()
        if not h:
            raise ApiError("Enter the api_hash from my.telegram.org.")
        return api_id, h

    def _tg_obj(self):
        if self.tg is None:
            api_id, h = self._tg_keys()
            self.tg = rb.TgLink(api_id, h)
        return self.tg

    def _tg_run(self, label, fn):
        tg = self._tg_obj()
        self.tg_status = label

        def work():
            try:
                self._tg_result(fn(tg))
            except Exception as e:
                self.tg_status = f"Problem: {type(e).__name__}: {e}"
                self.log(f"[!] Telegram: {e}")
        threading.Thread(target=work, daemon=True).start()

    def _tg_listen(self, tg):
        tg.listen(self.cfg.get("tg_boss_bot") or "@EvonyBossBot", on_update=lambda items: self.event("bosses"))
        self._tg_listen_feed(tg)

    def _tg_listen_feed(self, tg):
        chats = str(self.cfg.get("tg_feed_chats") or "").strip()
        rb._feed_keep[0] = int(self.cfg.get("tg_keep_min", 60) or 0)
        if chats:
            try:
                labels = tg.listen_feed(chats, on_update=lambda items: self.event("coords"), keep_min=int(self.cfg.get("tg_keep_min", 60) or 0))
                self.tg_status = "Logged in - watching " + ", ".join(labels or [chats])
            except Exception as e:
                self.tg_status = f"Problem: {e}"
                self.log(f"[!] Telegram: {e}")

    def tg_connect(self):
        def fn(tg):
            if tg.connect():
                self._tg_listen(tg)
                return "logged_in"
            return "need_login"
        self._tg_run("Connecting...", fn)

    def tg_send_code(self, phone):
        phone = (phone or "").strip()
        if not phone.startswith("+"):
            raise ApiError("Enter the phone number with the country code, like +911234567890.")
        self._tg_run("Asking Telegram to send a code...", lambda tg: (tg.send_code(phone), "code_sent")[1])

    def tg_login(self, code, pw):
        code = (code or "").strip()
        if not code and not pw:
            raise ApiError("Type the login code Telegram sent you first.")

        def fn(tg):
            res = tg.sign_in(password=pw) if (pw and not code) else tg.sign_in(code=code)
            if res == "password" and pw:
                res = tg.sign_in(password=pw)
            if res == "ok":
                self._tg_listen(tg)
                return "logged_in"
            return "need_password"
        self._tg_run("Logging in...", fn)

    def tg_logout(self):
        tg, self.tg, self.tg_ready = self.tg, None, False
        if tg is not None:
            threading.Thread(target=tg.log_out, daemon=True).start()
        else:
            for f in rb.USER.glob("tg_session*"):
                f.unlink(missing_ok=True)
        self.tg_status = "Logged out"

    def tg_fetch(self):
        cmd = (self.cfg.get("tg_boss_command") or "").strip()
        if not cmd:
            raise ApiError("Type the message to send to the bot first.")
        if not self.tg_ready:
            raise ApiError("Log in first.")
        self.tg_last = time.time()
        self._tg_run("Asking the bot...", lambda tg: ("fetched", tg.fetch(cmd)))

    def _tg_result(self, data):
        if data == "logged_in":
            self.tg_ready = True
            self.tg_status = "Logged in - reading " + (self.cfg.get("tg_boss_bot") or "")
        elif data == "need_login":
            self.tg_status = "Not logged in: enter your number, press 'Send code', then 'Log in'"
        elif data == "code_sent":
            self.tg_status = "Code sent - check your Telegram app, type it above and press 'Log in'"
        elif data == "need_password":
            self.tg_status = "Two-step password needed: type it in the password box and press 'Log in'"
        elif isinstance(data, tuple) and data[0] == "fetched":
            self.event("bosses")
            self.tg_status = (f"Logged in - {len(data[1])} bosses found" if data[1]
                              else "Logged in - the bot answered with no coordinates (check the message)")

    def _tg_autoconnect(self):
        if any(rb.USER.glob("tg_session*")) and str(self.cfg.get("tg_api_id", "")).strip() and self.cfg.get("tg_api_hash"):
            try:
                self.tg_connect()
            except ApiError as _e:
                rb.quiet_err("Controller._tg_autoconnect", _e)

    def _tg_tick(self):
        mins = int(self.cfg.get("tg_auto_min", 0) or 0)
        if mins > 0 and self.tg_ready and (self.cfg.get("tg_boss_command") or "").strip() \
                and time.time() - self.tg_last >= mins * 60:
            self.tg_fetch()

    # ------------------------------------------------------------------ updates
    def check_updates(self, manual):
        cfg = {"update_url": (self.cfg.get("update_url") or "").strip() or rb.DEFAULT_UPDATE_URL, "update_key": self.cfg.get("update_key", "")}
        if manual:
            self.log("[*] Checking for updates...")

        def work():
            try:
                info = rb.check_update(cfg)
                try:
                    rb.sync_monster_pack(lambda m: self.log(m))
                except Exception as _e:
                    rb.quiet_err("check_updates.work", _e)
                if info is None:
                    if manual:
                        self.event("update_none", {"version": rb.VERSION})
                    return
                if getattr(self, "restart_pending", False) and info.get("version") == getattr(self, "_installed_version", None):
                    self.log(f"[i] Version {info['version']} is already installed - the app restarts at the next quiet moment (no emulator in the middle of a join)")
                    if manual:
                        self._auto_restart = True
                    return
                self.log(f"[+] Version {info['version']} is available.")
                self._pending_update = info                 # shown in the status bar: "Update x available" (click = install + restart)
                rel = str((info.get("manifest") or {}).get("mode", "")).lower()
                if rel == "auto" and self.cfg.get("update_mode") == "auto" and not manual:
                    self.install_update(info, auto=True)    # only if the user chose fully automatic updates
                elif manual:
                    self.event("update_found", {"version": info["version"], "notes": info.get("notes", ""), "manual": True})
            except Exception as e:
                if manual:
                    self.toast("error", f"Update: {e}")
                self.log(f"[!] Update: {e}")
        threading.Thread(target=work, daemon=True).start()

    def install_update(self, info=None, auto=None):
        info = info or getattr(self, "_pending_update", None)
        if not info:
            raise ApiError("No update is waiting.")

        if getattr(self, "restart_pending", False) and info.get("version") == getattr(self, "_installed_version", None):
            self._auto_restart = True                     # already installed: just let it restart
            return
        self._installing = True

        def work():
            try:
                rb.install_update(info, lambda m: self.log(m))
                self._installing = False
                self._installed_version = info["version"]
                self.log(f"[+] Version {info['version']} is installed. Restart the app to use it.")
                self.restart_pending = True
                self._pending_since = time.time()
                self._auto_restart = bool(auto)
                self.event("update_installed", {"version": info["version"], "auto": bool(auto)})
                if auto:
                    self.log("[*] The app restarts by itself in a moment (when no emulator is joining) and carries on")
            except Exception as e:
                self._installing = False
                self.toast("error", f"Could not install {info['version']}: {e}")
                self.log(f"[!] Update: {e}")
        threading.Thread(target=work, daemon=True).start()

    RESUME_FILE = rb.USER / "resume.json"

    def _maybe_restart(self):
        """An update was installed: restart by itself at a quiet moment (no emulator in the middle of a join, or after
        30 s at the latest). The emulators that were running start again on their own after the restart."""
        if not self.restart_pending or not getattr(self, "_auto_restart", False):
            return
        now = time.time()
        since = getattr(self, "_pending_since", None) or now
        self._pending_since = since
        busy = [d["name"] or s for s, d in self.dev.items() if self.running(s) and d.get("busy_until", 0) > now]
        self._restart_wait = busy
        if busy and now - since < self.RESTART_MAX_WAIT:
            return
        self.restart_for_update()

    RESTART_MAX_WAIT = 30                               # seconds the update restart waits for running joins to finish

    def restart_info(self):
        """-> (seconds left before the update restart, names of the emulators it waits for)."""
        since = getattr(self, "_pending_since", None)
        left = max(0, int(self.RESTART_MAX_WAIT - (time.time() - since))) if since else self.RESTART_MAX_WAIT
        return left, list(getattr(self, "_restart_wait", []) or [])

    def restart_for_update(self):
        """Restart now for the installed update; the emulators that were running start again by themselves."""
        now = time.time()
        running = [s for s in self.dev if self.running(s)]
        try:
            rb.save_json(self.RESUME_FILE, {"serials": running, "t": now}, indent=None)
        except OSError as _e:
            rb.quiet_err("Controller.restart_for_update", _e)
        self.log(f"[+] Restarting to use the new version - {len(running)} emulator(s) start again by themselves")
        self.restart_pending = False
        self.restart_app()

    def take_resume(self):
        """Emulators to start again after an update restart (only if that restart was minutes ago)."""
        try:
            d = json.loads(self.RESUME_FILE.read_text(encoding="utf-8"))
            self.RESUME_FILE.unlink()
        except (OSError, ValueError):
            return []
        if time.time() - float(d.get("t", 0)) > 900:
            return []
        ids = {x["id"] for x in self.devices}
        return [s for s in d.get("serials", []) if s in ids]

    def restart_app(self):
        env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
        args = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable] + sys.argv
        self.shutdown()
        try:
            STATE_FILE.unlink()
        except OSError as _e:
            rb.quiet_err("Controller.restart_app", _e)
        subprocess.Popen(args, env=env)
        threading.Timer(0.6, lambda: os._exit(0)).start()


# ====================================================================== web API
def downloads_dir():
    p = Path.home() / "Downloads"
    return p if p.is_dir() else rb.USER


def open_path(path):
    try:
        if os.name == "nt":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as _e:
        rb.quiet_err("open_path", _e)


class Api:
    """One method per button. Each takes the JSON the page sent and returns a dict (or raises ApiError)."""

    def __init__(self, ctl):
        self.c = ctl

    # ---- state
    def state(self, p):
        c = self.c
        cfg = dict(c.cfg)
        for k in ("telegram_token", "telegram_chat"):
            cfg.setdefault(k, "")
        return {
            "version": rb.VERSION, "author": rb.AUTHOR, "copyright": rb.COPYRIGHT, "cfg": cfg, "devices": c.devices, "first_run": c.first_run,
            "generals": c.generals_info(), "monsters": rb.list_monsters(refresh=True), "catalog": rb.MONSTER_CATALOG, "never_join": sorted(rb.NEVER_JOIN),
            "profiles": rb.list_profiles(), "city_info": c.city_info(),
            "bosses": rb.get_bosses(), "building_names": rb.BUILDING_NAMES,
            "icons": rb.DEFAULT_CITY_ICONS, "example_tasks": EXAMPLE_TASKS,
            "default_bot_presets": rb.default_bot_presets(),
            "frozen": bool(getattr(sys, "frozen", False)), "license": c.lic.info(), "dev_tools": (rb.USER / "admin.txt").exists(),        # builder tools (scan, capture, add pictures) only for the owner: an admin.txt file in the data folder
        }

    # ---- duties (ready-made scheduled tasks with a status per instance)
    # Settings live globally ("All emulators") and an emulator may carry its own copy that replaces the global one.
    def _scope(self, p):
        s = str(p.get("serial") or "")
        return s if s in [d["id"] for d in self.c.devices] else ""

    def _eff(self, scope):
        return rb.merge_device_cfg(self.c.cfg, scope) if scope else self.c.cfg

    def _save_scoped(self, scope, key, value):
        if scope:
            per = {s: dict(v) for s, v in (self.c.cfg.get("per_device") or {}).items()}
            per.setdefault(scope, {})[key] = value
            self.c.save({"per_device": per}, managed=True)
        else:
            self.c.save({key: value}, managed=True)

    def _duty_rows(self, scope):
        cfg = self._eff(scope)
        serial = scope or (self.c.devices[0]["id"] if self.c.devices else "default")
        rows = []
        for d in rb.DUTY_LIBRARY:
            conf = (cfg.get("duties") or {}).get(d["id"]) or {}
            st, txt = rb.duty_status(serial, d["id"], cfg)
            _r = (rb.load_duty_state().get(serial or "default") or {}).get(d["id"]) or {}
            _h = list((_r.get("hist") or {}).values())
            week = {"ok": sum(int(x[0]) for x in _h), "fail": sum(int(x[1]) for x in _h), "last_error": _r.get("last_error", "")}
            rows.append({"id": d["id"], "week": week, "group": d["group"], "title": d["title"], "note": d["note"],
                         "on": bool(conf.get("on")), "every_min": int(conf.get("every_min", d["every_min"]) or 0),
                         "default_every": d["every_min"], "verified": bool(d.get("verified")),
                         "status": st, "status_text": txt,
                         "opts": [dict(o, val=conf.get(o["k"], o.get("def"))) for o in (d.get("opts") or [])],
                         "gems": bool(d.get("gems")),
                         "schedule": ("When the next egg is ready" if d["id"] == "crazy_eggs" else "Once per game day" if not d["every_min"] else f"Every {d['every_min']} min")})
        return rows

    def _own(self, scope):
        ov = (self.c.cfg.get("per_device") or {}).get(scope, {}) if scope else {}
        return {"duties": "duties" in ov, "crazy_eggs": "crazy_eggs" in ov, "champions": "champions" in ov, "wheel": "wheel" in ov}

    def duties(self, p):
        scope = self._scope(p)
        return {"duties": self._duty_rows(scope), "serial": scope, "own": self._own(scope)}

    def today(self, p):
        """Today card for one instance: reset time, duty queue (Daily activities + Reward claims that are on),
        today's results and the last 7 days. Only data the bot already has - nothing is read from the game here."""
        serial = self._scope(p) or (self.c.devices[0]["id"] if self.c.devices else "default")
        cfg = self._eff(serial)
        now = time.time()
        rm = rb.reset_min() * 60
        reset_at = ((now - rm) // 86400 + 1) * 86400 + rm
        state = (rb.load_duty_state().get(serial) or {})
        day = rb._game_day()
        queue, done_n = [], 0
        for d in rb.DUTY_LIBRARY:
            if d["group"] not in ("Daily activities", "Reward claims"):
                continue
            conf = (cfg.get("duties") or {}).get(d["id"]) or {}
            if not conf.get("on"):
                continue
            row = state.get(d["id"]) or {}
            st, txt = rb.duty_status(serial, d["id"], cfg)
            last_ok = row.get("last_ok", 0)
            ok_today = bool(last_ok) and time.strftime("%Y-%m-%d", time.gmtime(last_ok - rb.reset_min() * 60)) == day
            done_n += ok_today
            queue.append({"id": d["id"], "title": d["title"], "group": d["group"], "status": st, "text": txt,
                          "last_ok": last_ok if ok_today else 0, "msg": row.get("msg", "") if row.get("day") == day else "",
                          "every": int(conf.get("every_min", d["every_min"]) or 0)})
        rows = self.c.day_rows(serial)
        t = rows.get(day) or {}
        week = [rows[k] for k in sorted(rows)[-7:]]
        name = next((x.get("name") for x in self.c.devices if x["id"] == serial), "") or serial
        return {"serial": serial, "name": name, "now": now, "reset_at": reset_at, "tasks_on": bool(cfg.get("city_enabled")) or any(q for q in queue),
                "queue": queue,
                "results": {"joins": int(t.get("joins", 0)), "late": int(t.get("late", 0)), "restarts": int(t.get("restarts", 0)),
                            "monsters": t.get("monsters") or {}, "duties_done": done_n},
                "week": {"days": len(week), "joins": sum(int(w.get("joins", 0)) for w in week),
                         "restarts": sum(int(w.get("restarts", 0)) for w in week)}}

    def today_all(self, p):
        """Dashboard (nothing selected): one row per instance from today() + which instances have Crazy Eggs off.
        Shield / buff are not read from the game yet, so they are not sent."""
        rows, ce_off = [], []
        for d in self.c.devices:
            if not d.get("id"):
                continue
            t = self.today({"serial": d["id"]})
            q = t["queue"]
            rows.append({"serial": d["id"], "name": t["name"], "duties_done": t["results"]["duties_done"], "duties_total": len(q),
                         "joins": t["results"]["joins"], "late": t["results"]["late"]})
            if not ((self._eff(d["id"]).get("duties") or {}).get("crazy_eggs") or {}).get("on"):
                ce_off.append(t["name"])
        now = time.time()
        rm = rb.reset_min() * 60
        return {"now": now, "reset_at": ((now - rm) // 86400 + 1) * 86400 + rm, "rows": rows,
                "crazy_eggs": {"total": len(rows), "off": ce_off}}

    def crazy_eggs_on_all(self, p):
        """'turn on' link of the Crazy Eggs alert: switch the crazy_eggs duty on in the universal list
        and in every instance that has its own duties copy. Nothing else in the settings changes."""
        self.duty_set({"id": "crazy_eggs", "on": True})
        own = [s for s, v in (self.c.cfg.get("per_device") or {}).items() if "duties" in (v or {})]
        for s in own:
            if s in [d["id"] for d in self.c.devices]:
                self.duty_set({"id": "crazy_eggs", "on": True, "serial": s})
        return self.today_all(p)
    def duty_retry(self, p):
        """'Retry' on a duty that failed 3 times today: run it again now (one instance, or every instance)."""
        did = str(p.get("id", ""))
        if did not in rb.DUTY_BY_ID:
            raise ApiError("Unknown duty.")
        scope = self._scope(p)
        for s in ([scope] if scope else [d["id"] for d in self.devices]):
            rb.duty_retry(s, did)
        return {"duties": self._duty_rows(scope), "serial": scope, "own": self._own(scope)}

    def duty_set(self, p):
        did = str(p.get("id", ""))
        if did not in rb.DUTY_BY_ID:
            raise ApiError("Unknown duty.")
        scope = self._scope(p)
        duties = {k: dict(v) for k, v in (self._eff(scope).get("duties") or {}).items()}
        row = duties.setdefault(did, {})
        if "on" in p:
            row["on"] = bool(p["on"])
        if "every_min" in p:
            row["every_min"] = max(0, min(int(float(p["every_min"])), 10080))
        for o in rb.DUTY_BY_ID[did].get("opts") or []:      # the duty's own settings (resource, amount, ...)
            if o["k"] not in (p.get("opt") or {}):
                continue
            v = p["opt"][o["k"]]
            if o["type"] == "bool":
                v = bool(v)
            elif o["type"] == "number":
                try:
                    v = max(o.get("min", 0), min(int(float(v)), o.get("max", 10 ** 9)))
                except (TypeError, ValueError):
                    raise ApiError(f"{o['label']}: enter a number.")
            elif o["type"] == "select" and v not in o["choices"]:
                raise ApiError(f"{o['label']}: pick one of the list.")
            elif o["type"] == "rules":                     # Monster rallies list: monster, levels, preset, rally time
                if not isinstance(v, list):
                    raise ApiError("Monster list: wrong format.")
                clean = []
                for r in v[:40]:
                    if not isinstance(r, dict):
                        continue
                    try:
                        lo, hi = max(1, min(int(r.get("min") or 1), 99)), max(1, min(int(r.get("max") or 99), 99))
                    except (TypeError, ValueError):
                        raise ApiError("Monster list: levels must be numbers.")
                    clean.append({"name": str(r.get("name") or "").strip()[:40], "min": min(lo, hi), "max": max(lo, hi),
                                  "preset": r.get("preset") if r.get("preset") in rb.RT_PRESETS else "I",
                                  "time": str(r.get("time")) if str(r.get("time")) in rb.MR_TIMES else "5",
                                  "on": r.get("on") is not False})
                v = clean
            row[o["k"]] = v
        self._save_scoped(scope, "duties", duties)
        return self.duties(p)

    def task_history(self, p):
        """What the bot did: today's state per emulator and duty, plus the latest task events."""
        names = {d["id"]: (d.get("name") or d["id"]) for d in self.c.devices}
        events = [{"t": e.get("t", 0), "emu": names.get(e.get("s"), e.get("s")), "task": e.get("task", ""), "ok": bool(e.get("ok")), "msg": e.get("msg", "")}
                  for e in rb.read_task_events(150)]
        matrix = []
        for d in self.c.devices:
            eff = rb.merge_device_cfg(self.c.cfg, d["id"])
            row = {"emu": names[d["id"]], "tasks": bool(eff.get("city_enabled")), "duties": []}
            for du in rb.DUTY_LIBRARY:
                st, txt = rb.duty_status(d["id"], du["id"], eff)
                if st != "off":
                    row["duties"].append({"title": du["title"], "status": st, "text": txt})
            matrix.append(row)
        return {"events": events, "matrix": matrix}

    def scope_reset(self, p):
        """Drop this emulator's own duties / Crazy Eggs copy so it follows the global setting again."""
        scope, key = self._scope(p), str(p.get("key", ""))
        if scope and key in ("duties", "crazy_eggs", "champions", "wheel"):
            per = {s: dict(v) for s, v in (self.c.cfg.get("per_device") or {}).items()}
            per.get(scope, {}).pop(key, None)
            self.c.save({"per_device": per}, managed=True)
        return self.duties(p)

    def crazy_eggs(self, p):
        scope = self._scope(p)
        c = rb.ce_conf(self._eff(scope))
        serial = scope or (self.c.devices[0]["id"] if self.c.devices else "default")
        row = rb.ce_row(serial)
        return {"conf": c, "costs": rb.CE_COSTS, "lucky_used": row.get("lucky_day") == rb._game_day(), "own": self._own(scope),
                "smashes": row.get("smashes") if row.get("smash_day") == rb._game_day() else {}}

    def crazy_eggs_set(self, p):
        scope = self._scope(p)
        c = rb.ce_conf(self._eff(scope))
        if "min_gap" in p:
            c["min_gap"] = max(0, min(int(float(p["min_gap"] or 0)), 600))
        if "min_hammers" in p:
            c["min_hammers"] = max(0, min(int(float(p["min_hammers"] or 0)), 10000000))
        if "lucky" in p:
            v = str(p["lucky"] or "")
            c["lucky"] = v if v in rb.CE_COSTS or v in ("max", "") else "max"
        if "egg" in p:
            e = c["eggs"].setdefault(str(p["egg"]), {"on": True, "daily": 0})
            if "on" in p:
                e["on"] = bool(p["on"])
            if "daily" in p:
                e["daily"] = max(0, min(int(float(p["daily"] or 0)), 99))
        self._save_scoped(scope, "crazy_eggs", c)
        return self.crazy_eggs(p)

    def _rn_targets(self, p):
        scope = self._scope(p)
        return [scope] if scope else [d["id"] for d in self.c.devices] or ["default"]

    def _rn_catalog(self):
        cat = [{"id": "duty:" + d["id"], "title": d["title"], "group": d["group"]} for d in rb.DUTY_LIBRARY]
        for t in self.c.cfg.get("city_tasks") or []:
            if t.get("name"):
                cat.append({"id": "task:" + t["name"], "title": t["name"], "group": "Your own tasks"})
        return cat

    def run_now(self, p):
        """'Run a task now': the list of every task and what is queued to run first on this emulator."""
        cat = self._rn_catalog()
        titles = {c["id"]: c["title"] for c in cat}
        scope = self._scope(p)
        serial = scope or (self.c.devices[0]["id"] if self.c.devices else "default")
        queue = [{"id": i, "title": titles.get(i, i)} for i in rb.run_now_list(serial)]
        return {"tasks": cat, "queue": queue, "serial": scope}

    def run_now_add(self, p):
        tid = str(p.get("id", ""))
        if tid not in {c["id"] for c in self._rn_catalog()}:
            raise ApiError("Unknown task.")
        for s in self._rn_targets(p):
            rb.run_now_set(s, rb.run_now_list(s) + [tid])
        return self.run_now(p)

    def run_now_remove(self, p):
        tid = str(p.get("id", ""))
        for s in self._rn_targets(p):
            rb.run_now_done(s, tid)
        return self.run_now(p)

    def wheel(self, p):
        scope = self._scope(p)
        return {"conf": rb.wheel_conf(self._eff(scope)), "own": self._own(scope)}

    def wheel_set(self, p):
        scope = self._scope(p)
        c = rb.wheel_conf(self._eff(scope))
        for k in ("button", "spins", "reserve", "chips"):
            if k in p:
                c[k] = p[k]
        self._save_scoped(scope, "wheel", rb.wheel_conf({"wheel": c}))
        return self.wheel(p)

    def champions(self, p):
        scope = self._scope(p)
        conf = rb.ch_conf(self._eff(scope))
        serial = scope or (self.c.devices[0]["id"] if self.c.devices else "default")
        row = rb.ch_row(serial)
        day = rb._game_day()
        return {"plan": conf["plan"], "names": rb.CHAMPIONS, "max_taps": rb.CH_TAPS, "max_greets": rb.CH_GREETS, "own": self._own(scope),
                "taps": row.get("taps") if row.get("taps_day") == day else {}, "greets": row.get("greets") if row.get("greets_day") == day else {}}

    def champions_set(self, p):
        scope = self._scope(p)
        plan = {k: dict(v) for k, v in rb.ch_conf(self._eff(scope))["plan"].items()}
        name = str(p.get("name", ""))
        if name in rb.CHAMPIONS and "main" in p:                # the switch on a champion: ON = this champion gets the whole day (all taps + all greetings), OFF = none
            if p["main"]:
                plan = {name: {"taps": rb.CH_TAPS, "greets": rb.CH_GREETS}}
            else:
                plan.pop(name, None)
        elif name in rb.CHAMPIONS:
            e = plan.setdefault(name, {"taps": 0, "greets": 0})
            if "taps" in p:
                e["taps"] = max(0, min(int(float(p["taps"] or 0)), rb.CH_TAPS))
            if "greets" in p:
                e["greets"] = max(0, min(int(float(p["greets"] or 0)), rb.CH_GREETS))
            if not e["taps"] and not e["greets"]:
                plan.pop(name, None)
        elif p.get("clear"):
            plan = {}
        self._save_scoped(scope, "champions", {"plan": plan})
        return self.champions(p)

    def login(self, p):
        try:
            self.c.lic.login(p.get("id", ""), str(p.get("pw", "")))
        except ValueError as e:
            raise ApiError(str(e))
        self.c.toast("ok", "Logged in.")
        return {"license": self.c.lic.info()}

    def logout(self, p):
        self.c.stop_all()
        self.c.lic.logout()
        return {"license": self.c.lic.info()}

    def license_set(self, p):
        try:
            self.c.lic.set_key(str(p.get("key", "")))
        except ValueError as e:
            raise ApiError(str(e))
        self.c.toast("ok", f"License accepted: {self.c.lic.payload['n']} emulator(s).")
        return {"license": self.c.lic.info()}

    def license_clear(self, p):
        self.c.lic.clear()
        return {"license": self.c.lic.info()}

    def joins_report(self, p):
        """Reports page: joins per instance and monster for the last N game days (from daily_stats.json)."""
        c = self.c
        days = max(1, min(int(p.get("days", 1) or 1), c.KEEP_DAYS))
        today = rb._game_day()
        import datetime
        d0 = datetime.date.fromisoformat(today)
        want = {str(d0 - datetime.timedelta(days=i)) for i in range(days)}
        with c.lock:
            if not hasattr(c, "_days"):
                c.day_rows("")
            names = {d["id"]: (d.get("name") or d["id"]) for d in c.devices}
            out = []
            for serial, per in (getattr(c, "_days", {}) or {}).items():
                tot = {"joins": 0, "late": 0, "restarts": 0, "monsters": {}, "days": 0}
                for day, row in per.items():
                    if day not in want:
                        continue
                    tot["days"] += 1
                    for k in ("joins", "late", "restarts"):
                        tot[k] += int(row.get(k, 0) or 0)
                    for m, n in (row.get("monsters") or {}).items():
                        tot["monsters"][m] = tot["monsters"].get(m, 0) + int(n)
                if tot["days"] or serial in names:
                    out.append({"id": serial, "name": names.get(serial, serial), **tot})
        out.sort(key=lambda r: (-r["joins"], r["name"].lower()))
        return {"days": days, "today": today, "rows": out}

    def poll(self, p):
        c = self.c
        c.last_poll = time.time()
        since = int(p.get("since", 0))
        with c.lock:
            logs = [{"id": i, "s": s, "n": n, "t": t} for (i, s, n, t) in c.logs if i > since]
            events = [{"id": i, "k": k, "d": d} for (i, k, d) in c.events if i > since]
            acts = [{"id": i, "s": s_, "n": n, "a": a} for (i, s_, n, a) in c.acts if i > since]
            last = c.seq
        ram = getattr(c, "ram", None) or rb.memory_info()
        return {"seq": last, "logs": logs, "acts": acts, "events": events, "devs": c.status_list(),
                "tg": {"status": c.tg_status, "ready": c.tg_ready},
                "ram": ram, "city": c.city_any_running() or c.jobs["city"]["running"], "city_progress": None, "city_jobs": c.city_jobs_state(),
                "gen_scan": {"running": c.jobs["generals"]["running"], "count": c.jobs["generals"].get("count", 0)},
                "catalog": {"running": c.jobs["catalog"]["running"], "count": c.jobs["catalog"].get("count", 0)},
                "lic": c.lic.info() if c.lic.on else None,
                "update": ({"version": c._pending_update["version"], "notes": c._pending_update.get("notes", ""),
                            "installing": bool(getattr(c, "_installing", False)),
                            "restarting": bool(c.restart_pending and getattr(c, "_auto_restart", False)),   # installed: restarts at the next quiet moment
                            "restart_in": c.restart_info()[0], "waiting_for": c.restart_info()[1],
                            "needs_restart": bool(c.restart_pending and not getattr(c, "_auto_restart", False))}
                           if getattr(c, "_pending_update", None) else None)}

    # ---- settings / devices
    def save(self, p):
        self.c.save(p.get("cfg"), p.get("devices"), quiet=bool(p.get("auto")))
        return {}

    def set_devices(self, p):
        self.c.set_devices(p.get("devices"))
        return {"devices": self.c.devices}

    def detect(self, p):
        return self.c.detect()

    def add_found(self, p):
        ids = [str(x) for x in (p.get("ids") or [])]
        devs = self.c.add_found(ids)
        return {"devices": devs, "available": getattr(self.c, "available", [])}

    def restore_removed(self, p):
        n = self.c.restore_removed()
        return {"restored": n, "devices": self.c.devices}

    def reorder_devices(self, p):
        """New order of the emulator list (dragged in the left sidebar). Saved at once."""
        order = [str(x) for x in (p.get("order") or [])]
        with self.c.lock:
            by_id = {d["id"]: d for d in self.c.devices}
            rows = [by_id[i] for i in order if i in by_id] + [d for d in self.c.devices if d["id"] not in order]
        self.c.set_devices(rows)
        self.c._persist_devices()
        return {"devices": self.c.devices}

    # ---- run control
    def start(self, p):
        c = self.c
        if p.get("cfg") is not None:
            c.save(p.get("cfg"), p.get("devices"), quiet=True)        # what you start with is also kept after a restart
        if not c.devices:
            raise ApiError("Add at least one device ID.")
        if p.get("serial") and p["serial"] != "all":
            c.start_instance(p["serial"])
        else:
            c.start_all()
        return {}

    def pause(self, p):
        s = p.get("serial")
        self.c.toggle_pause(s) if s and s != "all" else self.c.pause_all()
        return {}

    def aj_status(self, p):
        v = rb.AJ_LEFT.get(p.get("serial"))
        return {} if not v else {"left_s": int(v[0]), "age_s": int(time.time() - v[1])}

    def restart_game(self, p):
        """Close and open Evony on one instance (or all) at its next loop."""
        s = p.get("serial")
        for x in ([s] if s and s != "all" else [d.get("id") for d in self.c.devices if d.get("id")]):
            rb.GAME_RESTART_REQ.add(x)
        return {}

    def restart_emulator(self, p):
        """Close and start the emulator of one instance (or all) at its next loop."""
        s = p.get("serial")
        for x in ([s] if s and s != "all" else [d.get("id") for d in self.c.devices if d.get("id")]):
            rb.EMU_RESTART_REQ.add(x)
        return {}

    def takeback(self, p):
        s = p.get("serial")
        evs = [rb.TAKEBACK[s]] if s in rb.TAKEBACK else ([] if s and s != "all" else list(rb.TAKEBACK.values()))
        for e in evs:
            e.set()
        return {}

    def resume(self, p):
        self.c.resume_all()
        return {}

    def stop(self, p):
        s = p.get("serial")
        self.c.stop_instance(s) if s and s != "all" else self.c.stop_all()
        return {}

    def clear_log(self, p):
        with self.c.lock:
            self.c.logs.clear()
        return {}

    # ---- log helpers
    def log_text(self, p):
        return {"text": rb.recent_log(int(p.get("lines", 300)))}

    def log_names(self, p):
        return {"names": rb.log_names()}

    def save_log(self, p):
        """Save the log as a zip in Downloads: last `hours` hours (1 h .. 7 days), all instances or one."""
        try:
            hours = min(max(int(p.get("hours", 24)), 1), 168)
        except Exception:
            hours = 24
        name = str(p.get("name") or "").strip()
        who = re.sub(r"[^\w\-]+", "_", name)[:30].strip("_") or "all"
        span = f"{hours // 24}d" if hours >= 24 and hours % 24 == 0 else f"{hours}h"
        path = downloads_dir() / f"rallyforge-log-{who}-{span}-{time.strftime('%Y%m%d-%H%M')}.zip"
        n = rb.export_log(str(path), hours, name)
        return {"path": str(path), "lines": n}            # the folder opens only when the person clicks "Open folder"

    def save_report(self, p):
        d = Path.home() / "Desktop" / "RallyForge reports"
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception:
            d = downloads_dir()
        who = re.sub(r"[^\w\-]+", "_", str(p.get("tester") or "").strip())[:30].strip("_")
        path = d / f"rallyforge-report{'-' + who if who else ''}-{time.strftime('%Y%m%d-%H%M')}.zip"
        rb.build_report(str(path), include_debug=bool(p.get("debug")),
                        description=(f"From: {who}\n" if who else "") + str(p.get("description") or "")[:4000],
                        devices=self.c.devices)
        return {"path": str(path), "folder": str(d)}

    def reveal_file(self, p):
        """Open the folder with the file selected (Windows) so the person can attach it."""
        f = Path(str(p.get("path", "")))
        try:
            if os.name == "nt" and f.exists():
                subprocess.Popen(["explorer", "/select,", str(f)])
            else:
                open_path(f.parent)
        except Exception:
            open_path(f.parent)
        return {}

    def open_unknown(self, p):
        d = rb.USER / "unknown_monsters"
        d.mkdir(exist_ok=True)
        open_path(d)
        return {"path": str(d)}

    def open_logs(self, p):
        rb.LOG_DIR.mkdir(exist_ok=True)
        open_path(rb.LOG_DIR)
        return {"path": str(rb.LOG_DIR)}

    # ---- profiles
    def profile_save(self, p):
        cfg = self.c.apply_config(p.get("cfg"), p.get("devices"))
        try:
            name = rb.save_profile(p.get("name", ""), cfg)
        except ValueError as e:
            raise ApiError(str(e))
        return {"name": name, "profiles": rb.list_profiles()}

    def profile_load(self, p):
        if self.c.n_running():
            raise ApiError("Stop all emulators first.")
        try:
            rb.save_config(rb.load_profile(p["name"]))
            cfg = self.c.apply_config({})                # take it over now: no restart needed (your emulator list stays as it is)
            rb.save_config(cfg)
        except Exception as e:
            raise ApiError(f"{type(e).__name__}: {e}")
        return {}

    def profile_delete(self, p):
        rb.delete_profile(p["name"])
        return {"profiles": rb.list_profiles()}

    # ---- profiles of ONE emulator (its own settings, copied to any other emulator)
    def _inst_dir(self):
        d = rb.profiles_dir() / "instances"
        d.mkdir(exist_ok=True)
        return d

    def _inst_names(self):
        return sorted((f.stem for f in self._inst_dir().glob("*.json")), key=str.lower)

    def inst_profiles(self, p):
        return {"profiles": self._inst_names()}

    def inst_profile_save(self, p):
        name = re.sub(r'[\\/:*?"<>|]', "", str(p.get("name", ""))).strip()
        if not name:
            raise ApiError("Give the profile a name.")
        serial = str(p.get("serial", ""))
        with self.c.lock:
            disk = dict((rb.load_config().get("per_device") or {}).get(serial) or {})
        st = {k: v for k, v in dict(p.get("settings") or {}).items()}
        for k in self.c.OWN_API_KEYS:                       # Duties / Crazy Eggs / Champions: always as saved
            if k in disk:
                st[k] = disk[k]
        rb.save_json(self._inst_dir() / f"{name}.json", st, indent=2)
        return {"name": name, "profiles": self._inst_names()}

    def inst_profile_load(self, p):
        serial = str(p.get("serial", ""))
        f = self._inst_dir() / (re.sub(r'[\\/:*?"<>|]', "", str(p.get("name", ""))) + ".json")
        if not f.exists():
            raise ApiError("That profile does not exist.")
        st = json.loads(f.read_text(encoding="utf-8"))
        with self.c.lock:
            per = {s: dict(v) for s, v in (self.c.cfg.get("per_device") or {}).items()}
            per[serial] = st
        self.c.save({"per_device": per}, quiet=True, managed=True)
        return {"settings": st}

    def inst_profile_delete(self, p):
        f = self._inst_dir() / (re.sub(r'[\\/:*?"<>|]', "", str(p.get("name", ""))) + ".json")
        if f.exists():
            f.unlink()
        return {"profiles": self._inst_names()}

    # ---- screenshots and pictures
    def shot_take(self, p):
        return self.c.shot_from_device(p["serial"])

    def dev_find(self, p):
        """Owner tool: run the bot's own building finder on a PAUSED instance and leave the building's ring menu open
        (e.g. the Keep, to change the city culture from a PC-linked chat). {"serial", "name"} -> {"found"}"""
        adb = rb.find_adb(self.c.cfg.get("adb_path") or "auto")
        if not adb:
            raise ApiError("ADB not found. Set the ADB path in Settings.")
        serial = p["serial"]
        dev = rb.Device(serial, adb)
        dev.connect()
        cfg = rb.merge_device_cfg(self.c.cfg, serial)
        log = lambda m: self.c.log(m, serial)
        stop = threading.Event()
        rb.leave_to_city(dev, cfg, stop, log)
        found = bool(rb.find_learned_building(dev, cfg, log, stop, str(p.get("name", "Keep"))))
        return {"found": found}

    def dev_act(self, p):
        """Owner tools: one tap / swipe / Back on an instance (used from a PC-linked chat to drive the game, e.g. change the
        city culture, while the instance is paused). {"serial", "do": "tap"|"swipe"|"back", "x","y" [,"x2","y2","ms"]}"""
        adb = rb.find_adb(self.c.cfg.get("adb_path") or "auto")
        if not adb:
            raise ApiError("ADB not found. Set the ADB path in Settings.")
        dev = rb.Device(p["serial"], adb)
        dev.connect()
        do = p.get("do", "tap")
        if do == "tap":
            dev.tap(int(p["x"]), int(p["y"]))
        elif do == "swipe":
            dev.swipe(int(p["x"]), int(p["y"]), int(p["x2"]), int(p["y2"]), int(p.get("ms", 500)))
        elif do == "back":
            dev.back()
        elif do == "shot":                                 # save the screen to a file (for a chat that cannot see the window)
            d = rb.USER / "debug"
            d.mkdir(exist_ok=True)
            f = d / ("owner_" + re.sub(r"\W", "_", p["serial"]) + ".png")
            cv2.imwrite(str(f), dev.screenshot())
            return {"file": str(f)}
        else:
            raise ApiError("Unknown action.")
        return {}

    def shot_upload(self, p):
        return self.c.shot_from_upload(p["data"])

    def portrait_rect(self, p):
        img = self.c.shot(p["token"])
        r = rb.portrait_rect(float(p["x"]), float(p["y"]), img)
        return {"rect": list(r) if r else None}

    def monster_add(self, p):
        img = self.c.shot(p["token"])
        name = (p.get("name") or "").strip()
        if not name:
            raise ApiError("Give the monster a name.")
        saved = rb.save_monster(name, img, int(p["x"]), int(p["y"]))
        self.c.log(f"[+] Saved monster picture {saved}")
        return {"monsters": rb.list_monsters(refresh=True)}

    def template_add(self, p):
        img = self.c.shot(p["token"])
        name = re.sub(r"[^\w\-]", "_", (p.get("name") or "").strip())
        if not name:
            raise ApiError("Give the picture a name.")
        x, y, w, h = [int(v) for v in p["rect"]]
        rb.save_template(name, img[y:y + h, x:x + w])
        self.c.log(f"[+] Saved template '{name}'")
        return {"name": name}

    def market_item_add(self, p):
        img = self.c.shot(p["token"])
        name = re.sub(r"[^\w\- ]", "", p.get("name") or "").strip()
        if not name:
            raise ApiError("Give the item a name.")
        x, y, w, h = [int(v) for v in p["rect"]]
        rb.save_template("mk_" + re.sub(r"[^\w\-]", "_", name), img[y:y + h, x:x + w])
        return {"name": name}

    def market_pictures(self, p):
        """Every Black Market picture the bot can look for (shipped + captured), as item names for the picture picker."""
        seen = {}
        for base in (rb.USER / "templates", rb.RES / "templates", rb.RES):
            for f in sorted(base.glob("mk_*.png")) if base.exists() else []:
                seen.setdefault(f.stem, f.stem[3:].replace("_", " "))
        return {"names": sorted(seen.values())}

    def general_add(self, p):
        img = self.c.shot(p["token"])
        name = (p.get("name") or "").strip()
        if not name:
            raise ApiError("Give the general a name.")
        try:
            saved = rb.save_general_region(name, img, tuple(int(v) for v in p["rect"]))
        except ValueError as e:
            raise ApiError(str(e))
        self.c.log(f"[+] Saved general picture {saved}")
        return {"name": saved, "generals": self.c.generals_info()}

    def general_meta(self, p):
        old, new = p["name"], (p.get("new_name") or p["name"]).strip()
        try:
            if new != old:
                new = rb.rename_general(old, new)
            rb.set_general_meta(new, p.get("tab"), p.get("fav"), p.get("copy"), cat=(p["cat"] if "cat" in p else rb._KEEP))
        except ValueError as e:
            raise ApiError(str(e))
        return {"name": new, "renamed": new != old, "generals": self.c.generals_info()}

    def general_delete(self, p):
        rb.delete_general(p["name"])
        return {"generals": self.c.generals_info()}

    # ---- scans
    def scan_generals(self, p):
        self.c.scan_generals(p["serial"], p.get("mode", "all"))
        return {}

    def catalog_crawl(self, p):
        self.c.crawl_catalog(p["serial"], bool(p.get("merge")))
        return {}

    def catalog_stop(self, p):
        st = self.c.jobs["catalog"].get("stop")
        if st:
            st.set()
        return {}

    def catalog_open(self, p):
        root = rb.USER / "catalog"
        root.mkdir(parents=True, exist_ok=True)
        open_path(root)
        return {"path": str(root)}

    def scan_generals_stop(self, p):
        st = self.c.jobs["generals"].get("stop")
        if st:
            st.set()
        return {}

    def scan_found(self, p):
        names = {n.lower() for n in rb.list_generals(refresh=True)}
        return {"items": [{"i": i, "name": f["name"], "stars": f.get("stars") or "", "tabs": f.get("tabs", []),
                           "exists": f["name"].lower() in names} for i, f in enumerate(self.c.scan_found)]}

    def scan_add(self, p):
        tabp = p.get("tab", "auto")
        if tabp != "auto" and not 0 <= int(tabp) <= 5:
            raise ApiError("The tab number has to be 0 to 5.")
        added, skipped, problems = [], [], []
        have = {n.lower() for n in rb.list_generals(refresh=True)}
        for it in p.get("items", []):
            f = self.c.scan_found[int(it["i"])]
            name = (it.get("name") or f["name"]).strip()
            if name.lower() in have and not p.get("replace"):
                skipped.append(name)
                continue
            try:
                saved = rb.save_general_image(name, f["image"])
                tabs = [t for t in f.get("tabs", []) if 1 <= t <= 5]
                chosen = next((t for t in tabs if t > 1), 0) if tabp == "auto" else int(tabp)
                m = rb.get_general_meta(saved)
                if chosen or tabs:
                    rb.set_general_meta(saved, chosen or m.get("tab"), m.get("fav"), m.get("copy"), tabs=tabs or None)
                added.append(saved)
            except ValueError as e:
                problems.append(f"{name}: {e}")
        return {"added": added, "skipped": skipped, "problems": problems, "generals": self.c.generals_info()}

    def city_capture(self, p):
        self.c.capture_city(p["serial"])
        return {}

    def city_capture_open(self, p):
        root = rb.USER / "citycap"
        root.mkdir(parents=True, exist_ok=True)
        open_path(root)
        return {"path": str(root)}

    def scan_city(self, p):
        self.c.scan_city(p["serial"])
        return {}

    def flow_start(self, p):
        return self.c.flow_start(p["serial"], p.get("name", ""))

    def flow_state(self, p):
        return self.c.flow_state()

    def flow_stop(self, p):
        return self.c.flow_stop()

    def flow_open(self, p):
        root = rb.USER / "flows"
        root.mkdir(parents=True, exist_ok=True)
        open_path(root)
        return {"path": str(root)}

    def flow_zip(self, p):
        """All recorded flows in one zip in Downloads, so a tester can send them (beta)."""
        import zipfile
        root = rb.USER / "flows"
        dirs = sorted(d for d in root.iterdir() if d.is_dir()) if root.exists() else []
        if not dirs:
            raise ApiError("Nothing recorded yet - press 'Record a task' first.")
        path = downloads_dir() / f"rallyforge-recordings-{time.strftime('%Y%m%d-%H%M')}.zip"
        n = 0
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for d in dirs:
                for f in d.rglob("*"):
                    if f.is_file():
                        z.write(f, f.relative_to(root))
                        n += 1
        open_path(path.parent)
        return {"path": str(path), "flows": len(dirs), "files": n}

    def manual_open(self, p):
        self.c.manual_open(p["serial"])
        return {}

    def manual_state(self, p):
        return self.c.manual_state()

    def manual_pan(self, p):
        self.c.manual_pan(p.get("dx", 0), p.get("dy", 0))
        return {}

    def manual_pick(self, p):
        self.c.manual_pick(p["name"], p["x"], p["y"])
        return self.c.manual_state()

    def manual_close(self, p):
        self.c.manual_close()
        return {}

    def slots_info(self, p):
        return self.c.slots_info(p["serial"])

    def slot_assign(self, p):
        self.c.slot_assign(p["serial"], p["building"], p["slot"])
        return self.c.slots_info(p["serial"])

    def manual_goto(self, p):
        self.c.manual_goto(p["slot"])
        return {}

    def city_result(self, p):
        """The last city map of an emulator: every expected building with its position (or missing)."""
        data = rb.load_city().get(p.get("serial", "")) or {}
        items = data.get("items", {})
        have = {}
        for k, v in items.items():
            have.setdefault(k.split("#")[0], []).append(v)
        lv = {}
        for k, v in (data.get("levels") or {}).items():
            lv.setdefault(k.split("#")[0], v.get("level"))
        rows = [{"name": n, "found": n in have, "pos": have.get(n, [None])[0], "optional": n in rb.OPTIONAL_BUILDINGS, "level": lv.get(n)}
                for n in rb.EXPECTED_BUILDINGS]
        extra = sorted(n for n in have if n not in rb.EXPECTED_BUILDINGS)
        return {"rows": rows, "extra": extra, "scanned": data.get("scanned"), "seconds": data.get("seconds")}

    def scan_city_stop(self, p):
        serial = p.get("serial")
        jobs = [self.c.city_jobs.get(serial)] if serial else list(self.c.city_jobs.values())
        for j in jobs:
            if j and j.get("stop"):
                j["stop"].set()
        st = self.c.jobs["city"].get("stop")
        if st and not serial:
            st.set()
        return {}

    # ---- telegram
    def tg_connect(self, p):
        self.c.apply_config(p.get("cfg"))
        self.c.tg_connect()
        return {}

    def tg_send_code(self, p):
        self.c.apply_config(p.get("cfg"))
        self.c.tg_send_code(self.c.cfg.get("tg_phone"))
        return {}

    def tg_login(self, p):
        self.c.apply_config(p.get("cfg"))
        self.c.tg_login(p.get("code"), p.get("password"))
        return {}

    def tg_logout(self, p):
        self.c.tg_logout()
        return {}

    def coords(self, p):
        items, stats = rb.feed_get(int(self.c.cfg.get("tg_keep_min", 60) or 0))
        return {"items": items, "stats": stats, "now": time.time(), "chats": str(self.c.cfg.get("tg_feed_chats") or ""),
                "status": self.c.tg_status, "ready": bool(self.c.tg_ready)}

    def coords_watch(self, p):
        """Save the chats to watch and start watching them (the Telegram login must be done)."""
        self.c.apply_config(p.get("cfg"))
        if not self.c.tg_ready or self.c.tg is None:
            raise ApiError("Log in to Telegram first (above).")
        self.c._tg_listen_feed(self.c.tg)
        return self.coords(p)

    def coords_library(self, p):
        return {"items": rb.lib_get(), "now": time.time()}

    def coords_library_clear(self, p):
        rb.lib_clear()
        return self.coords_library(p)

    def coords_chats(self, p):
        """Remember the watched chats as soon as they are typed (kept in the settings for good)."""
        self.c.apply_config({"tg_feed_chats": str(p.get("chats") or "").strip()})
        return {}

    def coords_keep(self, p):
        """How long the live list keeps a coordinate (minutes, 0 = until cleared): saved and applied at once."""
        m = max(0, int(p.get("minutes") or 0))
        self.c.apply_config({"tg_keep_min": m})
        rb.feed_set_keep(m)
        return self.coords(p)

    def coords_clear(self, p):
        rb.feed_clear()
        return self.coords(p)

    def tg_fetch(self, p):
        self.c.apply_config(p.get("cfg"))
        self.c.tg_fetch()
        return {}

    # ---- updates / alerts / app
    def check_updates(self, p):
        self.c.apply_config(p.get("cfg"))
        self.c.check_updates(True)
        return {}

    def install_update(self, p):
        self.c.install_update(auto=bool(p.get("restart")))       # from the status bar: install, then restart by itself
        return {}

    def test_alert(self, p):
        cfg = p.get("cfg") or {}
        ok, msg = rb.send_alert({"telegram_token": cfg.get("telegram_token", ""),
                                 "telegram_chat": cfg.get("telegram_chat", ""),
                                 "discord_webhook": cfg.get("discord_webhook", "")},
                                "Test message from RallyForge Bot")
        return {"ok_send": bool(ok), "msg": msg}

    def restart(self, p):
        if self.c.restart_pending:                    # an installed update: the running instances start again too
            self.c.restart_for_update()
            return {}
        self.c.restart_app()
        return {}

    def restart_update_now(self, p):
        """Status bar clicked while the installed update waits for a quiet moment: restart at once (instances resume)."""
        self.c.restart_pending = False
        threading.Timer(0.3, lambda: self.c._safe(self.c.restart_for_update)).start()
        return {}

    def quit(self, p):
        self.c.shutdown()
        threading.Timer(0.5, lambda: os._exit(0)).start()
        return {}

    def hide_window(self, p):
        self.c.hide_requested = True                  # "keep running in the background" was chosen on purpose
        self.c.event("close_window")
        return {}

    def copy_text(self, p):
        return {}


class Handler(BaseHTTPRequestHandler):
    server_version = "RallyForge"
    protocol_version = "HTTP/1.1"
    ctl = None
    api = None
    key = ""

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if "Cache-Control" not in (extra or {}):
            self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _authed(self, q=None):
        k = self.headers.get("X-Key") or (q or {}).get("k", [""])[0]
        return secrets.compare_digest(k, self.key)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self._host_ok():
            return self._send(403, {"ok": False, "error": "forbidden"})
        path = u.path
        try:
            if path == "/":
                if not self._authed(q):
                    return self._send(403, "Open this app from its own window.", "text/plain")
                html = (UI_DIR / "index.html").read_text(encoding="utf-8").replace("__KEY__", self.key)
                return self._send(200, html, "text/html; charset=utf-8")
            if path == "/api/ping":
                return self._send(200, {"ok": True, "app": "rallyforge", "version": rb.VERSION})
            if path.startswith("/api/"):
                if not self._authed(q):
                    return self._send(403, {"ok": False, "error": "forbidden"})
                return self._get_api(path, q)
            f = None
            # an update only brings the code (js / css / html): pictures such as the logo stay in the installed copy,
            # so look there too instead of showing a broken image
            for base in (UI_DIR, rb.RES / "ui", rb.RES):
                c = (base / path.lstrip("/")).resolve()
                if base.resolve() in c.parents and c.is_file():
                    f = c
                    break
            if f is None and path.endswith("/logo.png") and (rb.RES / "assets" / "logo.png").is_file():
                f = (rb.RES / "assets" / "logo.png").resolve()
            if f is not None:
                ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
                if f.suffix in (".js", ".css", ".html", ".svg"):
                    ctype += "; charset=utf-8"
                code = f.suffix in (".js", ".css", ".html")          # code is never cached: an update must show at once
                return self._send(200, f.read_bytes(), ctype, None if code else {"Cache-Control": "max-age=3600"})
            return self._send(404, "not found", "text/plain")
        except Exception as e:
            return self._send(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    def _get_api(self, path, q):
        c = self.ctl
        if path == "/api/shot":
            img = c.shot(q["token"][0])
            return self._send(200, jpg_bytes(img, 85), "image/jpeg")
        if path == "/api/general_img":
            img = rb.general_image(q["name"][0])
            if img is None:
                return self._send(404, "no picture", "text/plain")
            return self._send(200, png_bytes(img), "image/png", {"Cache-Control": "max-age=30"})
        if path == "/api/market_img":
            stem = "mk_" + re.sub(r"[^\w\-]", "_", q["name"][0])
            for base in (rb.USER / "templates", rb.RES / "templates", rb.RES):
                f = base / (stem + ".png")
                if f.exists():
                    return self._send(200, f.read_bytes(), "image/png", {"Cache-Control": "max-age=30"})
            return self._send(404, "no picture", "text/plain")
        if path == "/api/scan_img":
            return self._send(200, png_bytes(c.scan_found[int(q["i"][0])]["image"]), "image/png")
        if path == "/api/manual_shot":
            img = c.manual_shot_image()
            if img is None:
                return self._send(404, "no picture", "text/plain")
            return self._send(200, jpg_bytes(img, 80), "image/jpeg", {"Cache-Control": "no-store"})
        if path == "/api/logo":
            return self._send(200, (rb.RES / "assets" / "logo.png").read_bytes(), "image/png")
        return self._send(404, {"ok": False, "error": "unknown"})

    def do_POST(self):
        if not self._host_ok() or not self._authed():
            return self._send(403, {"ok": False, "error": "forbidden"})
        name = urlparse(self.path).path.replace("/api/", "", 1).strip("/")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(n) or b"{}") if n else {}
            fn = getattr(self.api, name.replace("-", "_"), None)
            if fn is None or name.startswith("_"):
                return self._send(404, {"ok": False, "error": "unknown request"})
            out = fn(payload) or {}
            out["ok"] = True
            return self._send(200, out)
        except ApiError as e:
            return self._send(200, {"ok": False, "error": str(e)})
        except Exception as e:
            self.ctl.log(f"[!] {type(e).__name__}: {e}")
            return self._send(200, {"ok": False, "error": f"{type(e).__name__}: {e}"})


def start_server(ctl):
    key = secrets.token_urlsafe(18)
    Handler.ctl, Handler.api, Handler.key = ctl, Api(ctl), key
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        rb.save_json(STATE_FILE, {"port": port, "key": key, "pid": os.getpid()}, indent=None)
    except OSError as _e:
        rb.quiet_err("start_server", _e)
    return port, key


# ====================================================================== window
def browser_path():
    if os.name != "nt":
        return None
    roots = [os.environ.get(k) for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    for r in filter(None, roots):
        for rel in ("Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe"):
            p = Path(r) / rel
            if p.exists():
                return str(p)
    return None


def window_size(want_w=1260, want_h=840):
    """The window size that fits the screen's usable area (above the taskbar), in the units the window uses."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            rc = wintypes.RECT()
            ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rc), 0)      # SPI_GETWORKAREA
            try:
                scale = ctypes.windll.user32.GetDpiForSystem() / 96.0
            except Exception:
                scale = 1.0
            aw, ah = (rc.right - rc.left) / scale, (rc.bottom - rc.top) / scale
            w, h = int(min(want_w, aw - 40)), int(min(want_h, ah - 100))                  # room for the title bar and a margin
            return w, h, int(rc.left / scale + (aw - w) / 2), int(rc.top / scale + 12)
    except Exception as _e:
        rb.quiet_err("window_size", _e)
    return want_w, want_h, None, None


def open_window(url, ctl):
    """Show the interface. Returns when the window is closed."""
    closed = threading.Event()
    try:
        import webview
        ww, wh, wx, wy = window_size()
        win = webview.create_window("RallyForge Bot", url, width=ww, height=wh, x=wx, y=wy, min_size=(860, 520),
                                    background_color="#12141f", text_select=True)
        ctl_events = {"win": win}

        def watch():                                        # the page asks to be closed (hide-window button)
            seen = ctl.seq
            while not closed.is_set():
                time.sleep(0.5)
                with ctl.lock:
                    hit = any(k == "close_window" and i > seen for (i, k, d) in ctl.events)
                if hit:
                    try:
                        ctl_events["win"].destroy()
                    except Exception as _e:
                        rb.quiet_err("open_window.watch", _e)
                    return
        threading.Thread(target=watch, daemon=True).start()
        started = time.time()
        failed = {"v": False}

        def health():                                       # the page never loaded (blank window): use the browser
            while not closed.is_set() and time.time() - started < 25:
                time.sleep(1)
                if ctl.last_poll > started:
                    return
            if not closed.is_set() and ctl.last_poll <= started:
                failed["v"] = True
                try:
                    ctl_events["win"].destroy()
                except Exception as _e:
                    rb.quiet_err("open_window.health", _e)
        threading.Thread(target=health, daemon=True).start()
        try:
            sp = rb.USER / "webview"
            sp.mkdir(exist_ok=True)
            webview.start(storage_path=str(sp), private_mode=False)
        except TypeError:
            webview.start()
        closed.set()
        if not failed["v"]:
            return True
        ctl.log("[*] The built-in window stayed blank; using the browser instead.")
    except Exception as e:
        ctl.log(f"[*] Built-in window not available ({type(e).__name__}); using the browser instead.")
        try:
            import traceback
            (rb.USER / "startup.log").open("a", encoding="utf-8").write("webview failed:\n" + traceback.format_exc() + "\n")
        except Exception as _e:
            rb.quiet_err("open_window", _e)
    exe = browser_path()
    if exe:
        prof = rb.USER / "browser"
        prof.mkdir(exist_ok=True)
        p = subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={prof}", f"--window-size={window_size()[0]},{window_size()[1]}", f"--window-position={window_size()[2] or 40},{window_size()[3] or 12}",
                              "--no-first-run", "--disable-extensions"])
        p.wait()
        return True
    import webbrowser
    webbrowser.open(url)
    return False


def main():
    ctl = Controller()
    try:
        rb.setup_tesseract()                                  # the scans read general names with OCR
    except Exception as _e:
        rb.quiet_err("main", _e)
    if ctl.lic.on:
        rb.INSTANCE_GUARD = ctl.allowed

        def _lic_watch():                               # account check every hour: expired / closed accounts stop the bots
            if ctl.quit_event.wait(45):
                return
            while True:
                try:
                    if not ctl.lic.recheck():
                        ctl.log("[!] The account is no longer valid - stopping all bots. Log in again.")
                        ctl._safe(ctl.stop_all)
                except Exception as _e:
                    rb.quiet_err("main._lic_watch", _e)
                if ctl.quit_event.wait(3600):
                    return
        threading.Thread(target=_lic_watch, daemon=True).start()
    port, key = start_server(ctl)
    url = f"http://127.0.0.1:{port}/?k={key}"
    try:
        (rb.USER / "startup.log").open("a", encoding="utf-8").write(f"server up on port {port}\n")
    except Exception as _e:
        rb.quiet_err("main", _e)
    resume = ctl.take_resume()
    if resume:
        def _resume():                                  # restarted for an update: carry on where we were
            t0 = time.time()
            for i, s in enumerate(resume):              # instance list shows "start 0:12" while each one waits
                ctl.start_eta[s] = t0 + 4.5 + i * 5
            time.sleep(4)
            ctl.log(f"[+] Updated to version {rb.VERSION} - starting the {len(resume)} emulator(s) that were running")

            def _go(s):
                if ctl.start_eta.pop(s, None) is not None:      # not stopped meanwhile
                    ctl._safe(ctl.start_instance, s)
            for i, s in enumerate(resume):
                threading.Timer(i * 5 + 0.5, _go, args=(s,)).start()
        threading.Thread(target=_resume, daemon=True).start()
    elif ctl.cfg.get("auto_start") or any(v.get("auto_start") for v in (ctl.cfg.get("per_device") or {}).values()):
        def _auto():                                    # 'start by itself when the app opens' (global switch + per-emulator switches)
            time.sleep(6)
            ctl.log("[+] Auto-start is on - starting the chosen emulators, one after the other")
            ctl._safe(ctl.start_all, True)
        threading.Thread(target=_auto, daemon=True).start()
    if "--no-window" in sys.argv:
        print(url)
        ctl.quit_event.wait()
        return
    shown = open_window(url, ctl)
    if shown and not ctl.cfg.get("keep_running_on_close") and not getattr(ctl, "hide_requested", False):       # closing the window = stop everything and quit
        try:
            ctl.log("[*] Window closed - stopping all bots")
        except Exception as _e:
            rb.quiet_err("main", _e)
        ctl.shutdown()
        t1 = time.time()
        while ctl.n_running() and time.time() - t1 < 8:
            time.sleep(0.3)
        try:
            STATE_FILE.unlink()
        except OSError as _e:
            rb.quiet_err("main", _e)
        os._exit(0)
    # window closed: keep the bots running quietly until they all stop (or the user quits from the window)
    t0 = time.time()
    while not ctl.quit_event.is_set():
        if not shown and time.time() - ctl.last_poll > 60 and time.time() - t0 > 60:
            break
        if ctl.n_running() == 0 and time.time() - t0 > 3 and shown:
            break
        time.sleep(2)
    ctl.shutdown()
    try:
        STATE_FILE.unlink()
    except OSError as _e:
        rb.quiet_err("main", _e)
    os._exit(0)


if __name__ == "__main__":
    main()
