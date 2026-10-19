# Copyright (c) 2026 Kiravik. All rights reserved. RallyForge Bot was created by Kiravik.
"""License keys for RallyForge Bot (offline, signed with Ed25519).

A key says how many emulators (instances) the buyer may use. Only the seller's private key can create keys;
this file contains the matching PUBLIC key, so keys can be checked without a server and cannot be forged.
The seller creates the key pair once with:  python make_license.py --init
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

PUBLIC_KEY = "mv1uK_Aq-fIwjFfknxx4voa4lZXaE_2UxDYTV3AO02s"       # filled in by  make_license.py --init
PREFIX = "RFB1-"
BUY_URL = ""
DEFAULT_UPDATE_URL = "https://raw.githubusercontent.com/28vicky/rallyforge-bot/main/version.json"                                      # shown in the License tab, e.g. "https://yourshop.com/rallyforge"


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def required(res_dir):
    """The sold edition contains an edition.txt with the word 'pro'; the developer copy does not and is unlimited."""
    try:
        return (Path(res_dir) / "edition.txt").read_text().strip().lower() == "pro"
    except OSError:
        return False


def machine_id():
    raw = ""
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                raw = winreg.QueryValueEx(k, "MachineGuid")[0]
        except Exception:
            raw = ""
    if not raw:
        import uuid
        raw = str(uuid.getnode())
    h = hashlib.sha256(("rfb-" + raw).encode()).hexdigest()[:16].upper()
    return "-".join(h[i:i + 4] for i in range(0, 16, 4))


def verify(key):
    """-> payload dict, or raises ValueError with a readable reason."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    key = "".join(str(key or "").split())
    if not key.startswith(PREFIX) or "." not in key:
        raise ValueError("That is not a RallyForge license key.")
    body, sig = key[len(PREFIX):].split(".", 1)
    try:
        pub = Ed25519PublicKey.from_public_bytes(_b64d(PUBLIC_KEY))
    except Exception:
        raise ValueError("This build has no license public key (the seller must run make_license.py --init before building).")
    try:
        pub.verify(_b64d(sig), body.encode())
        p = json.loads(_b64d(body))
    except (InvalidSignature, ValueError, Exception):
        raise ValueError("The license key is not valid. Check that you copied all of it.")
    if int(p.get("n", 0)) < 1:
        raise ValueError("The license key has no instances.")
    if p.get("hw") and p["hw"].replace("-", "").upper() != machine_id().replace("-", ""):
        raise ValueError("This key is locked to a different PC.")
    if p.get("exp") and time.strftime("%Y-%m-%d") > p["exp"]:
        raise ValueError(f"The license expired on {p['exp']}.")
    return p


class License:
    def __init__(self, user_dir, res_dir):
        self.file = Path(user_dir) / "license.key"
        self.seen = Path(user_dir) / "license.seen"          # latest date the app ran: setting the PC clock back does not extend a key
        self.on = required(res_dir)
        self.payload, self.error = None, ""
        self.load()

    def load(self):
        self.payload, self.error = None, ""
        if not self.on:
            return
        try:
            p = verify(self.file.read_text(encoding="utf-8"))
            today = time.strftime("%Y-%m-%d")
            try:
                last = self.seen.read_text(encoding="utf-8").strip()
            except OSError:
                last = ""
            if last and today < last and p.get("exp"):
                raise ValueError("The PC clock was set back. Set the right date and time, then restart the app.")
            if today > last:
                try:
                    self.seen.write_text(today, encoding="utf-8")
                except OSError:
                    pass
            self.payload = p
        except OSError:
            self.error = "No license key entered yet."
        except ValueError as e:
            self.error = str(e)

    def set_key(self, key):
        p = verify(key)                               # raises ValueError when bad
        self.file.write_text("".join(key.split()), encoding="utf-8")
        self.load()
        return p

    def clear(self):
        try:
            self.file.unlink()
        except OSError:
            pass
        self.load()

    def limit(self):
        """How many emulators may be used: None = unlimited (developer copy), 0 = no valid license."""
        if not self.on:
            return None
        self.load() if self.payload and self.payload.get("exp") else None
        return int(self.payload["n"]) if self.payload else 0

    def info(self):
        p = self.payload or {}
        left = None
        if p.get("exp"):
            try:
                left = (time.mktime(time.strptime(p["exp"], "%Y-%m-%d")) - time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))) // 86400
                left = int(left)
            except ValueError:
                left = None
        return {"days_left": left, "required": self.on, "valid": bool(p) or not self.on, "instances": p.get("n"), "buyer": p.get("to", ""),
                "expires": p.get("exp", ""), "bound": bool(p.get("hw")), "error": self.error, "machine_id": machine_id(),
                "buy_url": BUY_URL}


# ------------------------------------------------------------------ accounts (ID + password, closable early)
# The seller publishes a signed list "accounts.json" next to version.json. Each entry has a salted PBKDF2 hash of the
# password, an expiry date, an instance limit and a revoked flag. The app refreshes the list every hour; when it cannot
# (offline) the last good copy is used for up to OFFLINE_HOURS.
OFFLINE_HOURS = 48
PBKDF_ROUNDS = 200000


def _canon(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":")).encode()


def pw_hash(password, salt_b64):
    return base64.urlsafe_b64encode(hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _b64d(salt_b64), PBKDF_ROUNDS)).decode().rstrip("=")


def verify_accounts(doc):
    """-> the accounts dict of a signed list, or raises ValueError."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        pub = Ed25519PublicKey.from_public_bytes(_b64d(PUBLIC_KEY))
        body = {k: v for k, v in doc.items() if k != "sig"}
        pub.verify(_b64d(doc["sig"]), _canon(body))
    except Exception:
        raise ValueError("The account list could not be verified.")
    return body.get("accounts") or {}


class AccountLicense:
    """Same interface as License (limit / info / load), but driven by an ID + password login."""

    def __init__(self, user_dir, res_dir):
        self.dir = Path(user_dir)
        self.on = required(res_dir)
        self.cache = self.dir / "accounts_cache.json"
        self.login_file = self.dir / "login.json"
        self.seen = self.dir / "license.seen"
        self.url = ""
        self.payload, self.error = None, ""
        self.user = ""
        self.fetched = 0
        self.loaded = 0
        self.load()

    def set_source(self, update_url):
        """accounts.json lives next to version.json."""
        self.url = str(update_url or DEFAULT_UPDATE_URL).rsplit("/", 1)[0] + "/accounts.json"

    def _creds(self):
        try:
            d = json.loads(self.login_file.read_text(encoding="utf-8"))
            return d.get("id", ""), base64.b64decode(d.get("pw", "")).decode("utf-8")
        except Exception:
            return "", ""

    def _list(self):
        try:
            d = json.loads(self.cache.read_text(encoding="utf-8"))
            return verify_accounts(d["doc"]), float(d.get("fetched", 0))
        except Exception:
            return {}, 0

    def refresh(self, timeout=10):
        """Download the signed list. True when a fresh, correctly signed list was stored."""
        if not self.url:
            return False
        try:
            req = urllib.request.Request(self.url + "?t=" + str(int(time.time())), headers={"User-Agent": "RallyForgeBot"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                doc = json.loads(r.read().decode("utf-8"))
            verify_accounts(doc)
            self.cache.write_text(json.dumps({"doc": doc, "fetched": time.time()}), encoding="utf-8")
            return True
        except Exception:
            return False

    def load(self):
        """Re-evaluate the account. The result replaces the old state in one step, so other threads never see a half-empty state."""
        payload, error, user = None, "", ""
        if self.on:
            uid, pw = self._creds()
            if not uid:
                error = "Please log in."
            else:
                try:
                    payload = self._check(uid, pw)
                    user = uid
                except ValueError as e:
                    error = str(e)
        self.payload, self.error, self.user = payload, error, user
        self.loaded = time.time()

    def _check(self, uid, pw):
        accts, fetched = self._list()
        if not accts:
            raise ValueError("No account list yet - connect to the internet and log in.")
        a = accts.get(uid.strip().lower())
        if not a or not hmac.compare_digest(pw_hash(pw, a["s"]), a["h"]):
            raise ValueError("Wrong ID or password.")
        if a.get("rev"):
            raise ValueError("This account was closed. Contact the seller.")
        today = time.strftime("%Y-%m-%d")
        if a.get("exp") and today > a["exp"]:
            raise ValueError(f"This account expired on {a['exp']}. Contact the seller for a new one.")
        try:
            last = self.seen.read_text(encoding="utf-8").strip()
        except OSError:
            last = ""
        if last and today < last:
            raise ValueError("The PC clock was set back. Set the right date and time, then restart the app.")
        if today > last:
            try:
                self.seen.write_text(today, encoding="utf-8")
            except OSError:
                pass
        if time.time() - fetched > OFFLINE_HOURS * 3600:
            raise ValueError(f"Connect to the internet - the account has not been checked for {OFFLINE_HOURS} hours.")
        return {"n": int(a.get("n", 1)), "to": a.get("to") or uid, "exp": a.get("exp", ""), "id": uid}

    def login(self, uid, pw):
        uid = str(uid or "").strip().lower()
        if not uid or not pw:
            raise ValueError("Enter your ID and password.")
        if not self.refresh() and not self._list()[0]:
            raise ValueError("Could not reach the account server. Check the internet connection and try again.")
        p = self._check(uid, pw)
        self.login_file.write_text(json.dumps({"id": uid, "pw": base64.b64encode(pw.encode("utf-8")).decode()}), encoding="utf-8")
        self.payload, self.error, self.user = p, "", uid
        return p

    def logout(self):
        try:
            self.login_file.unlink()
        except OSError:
            pass
        self.load()

    def recheck(self):
        """Hourly: fetch the list again and re-evaluate. -> True when the account is still good."""
        if not self.on:
            return True
        self.refresh()
        self.load()
        return bool(self.payload)

    def limit(self):
        if not self.on:
            return None
        if self.payload and time.time() - self.loaded > 60:      # re-check at most once a minute (expiry still takes effect)
            self.load()
        p = self.payload
        return int(p["n"]) if p else 0

    def info(self):
        p, error, user = self.payload or {}, self.error, self.user
        left = None
        if p.get("exp"):
            try:
                left = int((time.mktime(time.strptime(p["exp"], "%Y-%m-%d")) - time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))) // 86400)
            except ValueError:
                left = None
        return {"mode": "account", "required": self.on, "valid": bool(p) or not self.on, "instances": p.get("n"), "buyer": p.get("to", ""),
                "user": user, "expires": p.get("exp", ""), "days_left": left, "bound": False, "error": error,
                "machine_id": machine_id(), "buy_url": BUY_URL}
