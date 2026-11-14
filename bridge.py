import fcntl
import hashlib
import ctypes as C
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from tempfile import TemporaryDirectory

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

def exchange(pull=False):
    manifest = root / "seen.json"
    seen = set(json.loads(manifest.read_text(encoding="utf-8"))) if manifest.exists() else set()
    received_path = root / "received.json"
    received = set(json.loads(received_path.read_text(encoding="utf-8"))) if received_path.exists() else set()
    rows = {}
    for line in sys.stdin.buffer.read().decode("utf-8").splitlines():
        if len(line.split("\t")) != 3:
            raise ValueError("Expected word, pinyin and weight in each row")
        rows[line.rsplit("\t", 1)[0]] = line
    new = sorted(rows.keys() - seen - received)
    if not new and not pull:
        print(json.dumps({"new": 0}))
        return
    result = {"applied": 0, "failed": 0}
    if new:
        body = "\n".join(rows[key] for key in new)
        request_id = "bridge-" + hashlib.sha256(body.encode()).hexdigest()[:32]
        result = request("settings_dictionary", dict(options=options, action=dict(
            operation="import", kind="pinyin", format="standard", text=body, request_id=request_id)))
    state = sync(dict(operation="run", trigger="manual"))
    status = state["status"]
    if (state.get("error") or status.get("last_error") or not status.get("approved")
            or status.get("pending_upload") or (pull and status.get("pending_apply"))):
        raise RuntimeError(state.get("error") or status.get("last_error") or "Publication pending")
    temp = manifest.with_suffix(".new")
    temp.write_text(json.dumps(sorted(seen | set(new)), ensure_ascii=False), encoding="utf-8")
    temp.replace(manifest)
    output = dict(new=len(new), imported=result["applied"], rejected=result["failed"],
                  published=state["report"]["published_ops"])
    if pull:
        # Never return previously sent Rime words, including words later removed locally.
        output["incoming"] = export_new_words(seen | rows.keys())
        received.update(f"{word}\t{code}" for word, code in output["incoming"])
        temp = received_path.with_suffix(".new")
        temp.write_text(json.dumps(sorted(received), ensure_ascii=False), encoding="utf-8")
        temp.replace(received_path)
    print(json.dumps(output, ensure_ascii=False))


def export_new_words(excluded):
    incoming = set()
    with TemporaryDirectory(dir=root) as work:
        destination = Path(work) / "dictionary.ndjson"
        request("dictionary", dict(options=options, action=dict(
            operation="export_snapshot", destination=str(destination))))
        with destination.open(encoding="utf-8") as stream:
            for record in map(json.loads, stream):
                if record["type"] != "entry" or record["data"]["kind"] != "pinyin":
                    continue
                data = record["data"]
                word, code = data["word"], data["code"]
                if not re.fullmatch(r"[\u4e00-\u9fff]+", word):
                    continue
                if not re.fullmatch(r"[a-z]+(?:'[a-z]+)*", code) or code.count("'") + 1 != len(word):
                    continue
                if f"{word}\t{code}" not in excluded:
                    incoming.add((word, code))
    return sorted(incoming)

if len(sys.argv) < 2 or sys.argv[1] not in ("configure", "run", "devices", "approve", "publish", "exchange"):
    raise SystemExit("Usage: bridge.py {configure|run|devices|approve DEVICE_ID|publish|exchange}")
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
elif operation in ("publish", "exchange"):
    exchange(pull=operation == "exchange")
