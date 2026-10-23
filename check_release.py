"""Release gate: run it BEFORE and AFTER pushing. Every file listed in version.json must be byte-identical (sha256)
to what git holds for that ref - what GitHub serves is exactly that. Exit code 1 = do NOT announce the update.
  python check_release.py            -> checks the working tree against version.json
  python check_release.py origin/main -> checks what is pushed (run git fetch first)
First it runs the logic tests in tests/ (working tree); a failing test also gives exit code 1. --no-tests skips them."""
import hashlib, json, subprocess, sys, urllib.parse
from pathlib import Path

args = [a for a in sys.argv[1:] if not a.startswith("--")]
ref = args[0] if args else None
if "--no-tests" not in sys.argv and (Path(__file__).parent / "tests").is_dir():
    t = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
                       cwd=Path(__file__).parent, capture_output=True, text=True)
    last = (t.stderr.strip().splitlines() or ["?"])[-1]
    print(f"tests: {'OK' if t.returncode == 0 else 'FAILED'} - {last}")
    if t.returncode:
        print(t.stderr[-3000:])
        sys.exit(1)


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
