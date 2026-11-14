import fcntl
import hashlib
import ctypes as C
import json
import os
from pathlib import Path
import sqlite3
import sys

os.umask(0o077)
root = Path(os.environ.get("RIME_MSIME_STATE_DIR", "~/.local/state/rime-msime-bridge")).expanduser().resolve()
root.mkdir(parents=True, exist_ok=True)
lib = C.CDLL(str(Path(os.environ.get("MSIME_HOST_LIBRARY", root / "libmsime_host_api.so")).expanduser().resolve()))
free = lib.msime_client_string_free
free.argtypes = [C.c_void_p]
free.restype = None

def invoke(name, *params):
    call = getattr(lib, "msime_client_" + name)
    call.argtypes = [C.c_uint64 if isinstance(p, int) else C.c_char_p for p in params]
    call.restype = C.c_void_p
    ptr = call(*params)
    try:
        result = json.loads(C.string_at(ptr))
    finally:
        free(ptr)
    if not result["ok"]:
        raise RuntimeError(result.get("error", name))
    return result["value"]

def encode(document):
    return json.dumps(document, ensure_ascii=False).encode()

def request(name, document):
    body = encode(document)
    return invoke(name, body, len(body))

prefs = invoke("default_preferences")
options = dict(api_version=1, preferences=prefs, preferences_directory=str(root))
for key, folder in (("resources", "resources"), ("dictionaries", "dictionaries"),
                    ("user_data", "user"), ("cache", "cache")):
    directory = root / folder
    directory.mkdir(exist_ok=True)
    options[key] = str(directory)
    if key in ("resources", "dictionaries"):
        with sqlite3.connect(directory / "msime-pinyin.db") as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tbl_2_n(key TEXT,jp TEXT,value TEXT,weight INTEGER);
                CREATE TABLE IF NOT EXISTS tbl_3_a(key TEXT,jp TEXT,value TEXT,weight INTEGER);
                CREATE TABLE IF NOT EXISTS wubi86(key TEXT,value TEXT,weight INTEGER);
                CREATE TABLE IF NOT EXISTS quick_parases(key TEXT,value TEXT,weight INTEGER);
                CREATE INDEX IF NOT EXISTS idx_quick_parases_key_weight ON quick_parases(key,weight DESC);
            """)

            for bucket in [*range(1, 8), "others"]:
                for initial in "abcdefghjklmnopqrstwxyz":
                    db.execute(f"CREATE TABLE IF NOT EXISTS tbl_{bucket}_{initial}(key TEXT,jp TEXT,value TEXT,weight INTEGER)")

def sync(action):
    return request("dictionary_sync", dict(options=options,
        directory=str(root / "sync-state"), device_name="Rime MSIME bridge", action=action))

def publish():
    manifest = root / "seen.json"
    seen = set(json.loads(manifest.read_text(encoding="utf-8"))) if manifest.exists() else set()
    rows = {}
    for line in sys.stdin.buffer.read().decode("utf-8").splitlines():
        if len(line.split("\t")) != 3:
            raise ValueError("Expected word, pinyin and weight in each row")
        rows[line.rsplit("\t", 1)[0]] = line
    new = sorted(rows.keys() - seen)
    if not new:
        print(json.dumps({"new": 0}))
        return
    body = "\n".join(rows[key] for key in new)
    request_id = "bridge-" + hashlib.sha256(body.encode()).hexdigest()[:32]
    result = request("settings_dictionary", dict(options=options, action=dict(
        operation="import", kind="pinyin", format="standard", text=body, request_id=request_id)))
    state = sync(dict(operation="run", trigger="manual"))
    status = state["status"]
    if state.get("error") or status.get("last_error") or not status.get("approved") or status.get("pending_upload"):
        raise RuntimeError(state.get("error") or status.get("last_error") or "Publication pending")
    temp = manifest.with_suffix(".new")
    temp.write_text(json.dumps(sorted(seen | set(new)), ensure_ascii=False), encoding="utf-8")
    temp.replace(manifest)
    print(json.dumps(dict(new=len(new), imported=result["applied"], rejected=result["failed"],
                          published=state["report"]["published_ops"])))

if len(sys.argv) < 2 or sys.argv[1] not in ("configure", "run", "devices", "approve", "publish"):
    raise SystemExit("Usage: bridge.py {configure|run|devices|approve DEVICE_ID|publish}")
operation = sys.argv[1]
lock = (root / "bridge.lock").open("a")
fcntl.flock(lock, fcntl.LOCK_EX)
if operation == "configure":
    connection = json.loads((root / "webdav-connection.json").read_text(encoding="utf-8"))
    sync(dict(operation="probe", **connection))
    path = os.fsencode(root)
    snapshot = invoke("load_preferences", path, len(path))
    snapshot["preferences"]["dictionary_sync_webdav"] = connection
    body = encode({k: snapshot[k] for k in ("format_version", "revision", "preferences")})
    invoke("save_preferences", path, len(path), snapshot["revision"], body, len(body))
    print("WebDAV connection verified and saved")
elif operation == "run":
    print(json.dumps(sync(dict(operation="run", trigger="manual")), ensure_ascii=False))
elif operation == "devices":
    print(json.dumps(sync(dict(operation="devices")), ensure_ascii=False))
elif operation == "approve":
    print(json.dumps(sync(dict(operation="approve", device_id=sys.argv[2])), ensure_ascii=False))
elif operation == "publish":
    publish()
