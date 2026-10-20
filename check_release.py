"""Release gate: run it BEFORE and AFTER pushing. Every file listed in version.json must be byte-identical (sha256)
to what git holds for that ref - what GitHub serves is exactly that. Exit code 1 = do NOT announce the update.
  python check_release.py            -> checks the working tree against version.json
  python check_release.py origin/main -> checks what is pushed (run git fetch first)"""
import hashlib, json, subprocess, sys, urllib.parse

ref = sys.argv[1] if len(sys.argv) > 1 else None


def read(name):
    if ref:
        return subprocess.run(["git", "show", f"{ref}:{name}"], capture_output=True).stdout
    try:
        return open(name, "rb").read()
    except OSError:
        return b""


man = json.loads(read("version.json") or b"{}")
bad = []
for group in ("files", "templates", "monsters", "catalog"):
    for name, meta in (man.get(group) or {}).items():
        data = read(urllib.parse.unquote(meta.get("url", name) if isinstance(meta, dict) else name))
        if not data or hashlib.sha256(data).hexdigest() != (meta or {}).get("sha256"):
            bad.append(f"{group}/{name}")
print(f"version {man.get('version')}: {'OK - every file matches' if not bad else 'MISMATCH: ' + ', '.join(bad[:8])}")
sys.exit(1 if bad else 0)
