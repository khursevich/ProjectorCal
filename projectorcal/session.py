"""Sessions and settings: readable JSON, atomic writes, schemaVersion."""
from __future__ import annotations

import datetime
import json
import os
import time
import sys
import uuid

SCHEMA_VERSION = 1

STEP_IDS = ["prep", "full", "black", "laser", "gray", "gpoints", "color", "verify", "eyes", "hdr", "dv", "summary"]
HIDDEN_STEPS = ["auto", "autohdr"]     # measurements of auto runs (pass = run number)
OLD_STEPS = {"wb": "gray", "gamma": "gray"}   # schema v1.0: balance and gamma were separate steps

DEFAULT_SETTINGS = {
    "schemaVersion": SCHEMA_VERSION,
    "meterMode": "argyll",        # argyll | oneshot | mock
    "spotreadPath": "",
    "flag": "-e",
    "refresh": "",                # "" (auto) | "n" | "r"
    "ccmx": "",
    "extraArgs": "",
    "displayType": "",            # spotread -y (display type / installed .ccss of the instrument); "" = default
    "meterProfile": "",           # profiles.METERS id; "" = suggested from the detected instrument
    "autoSpeed": "",              # auto-run video: "" (from the meter profile) | "std" | "slow"
    "avg": 3,
    "stabilityPct": 0.7,
    "autoDefault": True,
    "theme": "dark",
}

MENU_TEMPLATE = {
    "sdr": {
        "Image Mode": "Filmmaker", "Brightness level (laser)": "", "Black Level": "", "Contrast": "",
        "Color Temp": "", "Gain R": "0", "Gain G": "0", "Gain B": "0",
        "Offset R": "0", "Offset G": "0", "Offset B": "0", "Gamma": "", "Gamma Control Points": "",
        "Color Space": "", "CMS Red": "", "CMS Yellow": "", "CMS Green": "", "CMS Cyan": "",
        "CMS Blue": "", "CMS Magenta/Pink": "", "Sharpness": "",
    },
    "hdr10": {
        "Image Mode": "", "Brightness level (laser)": "", "Gain R": "0", "Gain G": "0", "Gain B": "0",
        "Offset R": "0", "Offset G": "0", "Offset B": "0", "Dynamic tone mapping": "",
    },
}


def data_dir():
    env = os.environ.get("PROJECTORCAL_HOME")
    if env:
        return env
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/ProjectorCal")
    if os.name == "nt":
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "ProjectorCal")
    xdg = os.environ.get("XDG_DATA_HOME")
    return os.path.join(xdg, "ProjectorCal") if xdg else os.path.expanduser("~/.local/share/ProjectorCal")


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def atomic_write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    for attempt in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:          # Windows: antivirus / indexer briefly holds the file
            if attempt == 9:
                raise
            time.sleep(0.1)


def gp_fresh():
    """State of the "Gamma by points" wizard: the program keeps the point values itself (no need to type them)."""
    return {"stage": "prep", "round": 1, "queue": [], "values": {str(k): 0 for k in range(1, 21)},
            "preset": "", "started": False, "plan": [], "perStep": None, "applied": None, "probe": None}


def new_session(settings, name=None):
    created = now_iso()
    sid = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    steps = {}
    for s in STEP_IDS + HIDDEN_STEPS:
        steps[s] = {"status": "todo", "notes": "", "pass": 1, "checklist": {}}
    steps["gray"]["full"] = False
    steps["gpoints"].update(gp_fresh())
    steps["verify"]["quick"] = False
    steps["color"]["sweeps"] = False
    return migrate({
        "schemaVersion": SCHEMA_VERSION,
        "id": sid,
        "name": name or ("Calibration " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M")),
        "createdAt": created,
        "updatedAt": created,
        "projectorProfile": {"model": "vidda-c3s", "name": "Vidda C3S", "gpCount": 20, "screenSizeInches": None,
                             "screenGain": None, "notes": ""},
        "meter": {"flag": settings.get("flag", "-e"), "refresh": settings.get("refresh") or None,
                  "ccmx": settings.get("ccmx") or None, "displayType": settings.get("displayType") or None,
                  "profile": settings.get("meterProfile") or None},
        "laserLevel": None,
        "whiteY": None,
        "whiteFixedAt": None,
        "gamma": "2.4",
        "currentStep": "prep",
        "steps": steps,
        "measurements": [],
        "menuValues": json.loads(json.dumps(MENU_TEMPLATE)),
        "events": [],
        "runs": [],
        "psettings": None,
    })


# Flags of sessions recorded before v1.6 (Russian UI) → English
LEGACY_FLAGS = {
    "\u043d\u0435\u0442 \u043e\u0442\u0441\u0447\u0451\u0442\u043e\u0432": "no readings",
    "\u043d\u0435 \u043f\u043e\u0445\u043e\u0436\u0435": "implausible",
    "\u043d\u0435\u0441\u0442\u0430\u0431\u0438\u043b\u044c\u043d\u043e": "unstable",
    "\u043d\u0435\u043c\u043e\u043d\u043e\u0442\u043e\u043d\u043d\u043e": "non-monotonic",
    "1 \u043e\u0442\u0441\u0447\u0451\u0442": "single reading",
    "\u043f\u0435\u0440\u0435\u0434 \u0441\u0431\u043e\u0435\u043c": "before failure",
    "\u0441\u0431\u043e\u0439 \u0432 \u043a\u043e\u043d\u0446\u0435": "failure at end",
    "\u0432\u0437\u044f\u0442 \u0438\u0437 \u043c\u0435\u0442\u043a\u0438": "taken from mark",
    "\u0432\u0437\u044f\u0442 \u0438\u0437 \u043a\u043e\u043d\u0442\u0440\u043e\u043b\u044f \u0431\u0435\u043b\u043e\u0433\u043e": "taken from drift check",
    "\u0432\u0437\u044f\u0442 \u0438\u0437 \u0444\u0438\u043d\u0438\u0448\u0430": "taken from finish",
    "\u0438\u0437 \u043c\u0435\u0442\u043a\u0438": "from mark",
    "\u0438\u0437 \u043a\u043e\u043d\u0442\u0440\u043e\u043b\u044f \u0431\u0435\u043b\u043e\u0433\u043e": "from drift check",
    "\u0438\u0437 \u0444\u0438\u043d\u0438\u0448\u0430": "from finish",
    "\u0432\u043e\u0441\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u043e \u043f\u043e \u0436\u0443\u0440\u043d\u0430\u043b\u0443": "reconstructed from the log",
}


def migrate(s):
    """Fills old/incomplete sessions with missing fields (without losing data)."""
    s.setdefault("schemaVersion", SCHEMA_VERSION)
    s.setdefault("steps", {})
    for sid in STEP_IDS + HIDDEN_STEPS:
        st = s["steps"].setdefault(sid, {})
        st.setdefault("status", "todo")
        st.setdefault("notes", "")
        st.setdefault("pass", 1)
        st.setdefault("checklist", {})
    s["steps"]["gray"].setdefault("full", False)
    for k, v in gp_fresh().items():
        s["steps"]["gpoints"].setdefault(k, v)
    for k in range(1, 21):
        s["steps"]["gpoints"]["values"].setdefault(str(k), 0)
    if s["steps"]["gpoints"].get("stage") == "point":   # v1.2: live point mode was replaced by calculation
        s["steps"]["gpoints"]["stage"] = "check"
    s.setdefault("ambientY", None)
    s.setdefault("runs", [])
    from .psettings import migrate as ps_migrate
    s["psettings"] = ps_migrate(s.get("psettings"), s.get("menuValues"), s["steps"]["gpoints"].get("values"))
    s["steps"]["verify"].setdefault("quick", False)
    if s.get("currentStep") in OLD_STEPS:
        s["currentStep"] = OLD_STEPS[s["currentStep"]]
    s["steps"]["color"].setdefault("sweeps", False)
    s.setdefault("measurements", [])
    s.setdefault("menuValues", json.loads(json.dumps(MENU_TEMPLATE)))
    for mode, tpl in MENU_TEMPLATE.items():
        mv = s["menuValues"].setdefault(mode, {})
        for k, v in tpl.items():
            mv.setdefault(k, v)
    s.setdefault("gamma", "2.4")
    s.setdefault("currentStep", "prep")
    s.setdefault("events", [])
    s.setdefault("projectorProfile", {"name": "Vidda C3S", "screenSizeInches": None, "screenGain": None,
                                      "notes": ""})
    pp = s["projectorProfile"]
    if "model" not in pp:          # sessions before v1.7 were all recorded on the Vidda C3S layout
        pp["model"] = "vidda-c3s" if "c3s" in str(pp.get("name") or "").lower() else "vidda-other"
    pp.setdefault("gpCount", 20)
    for k in ("laserLevel", "whiteY", "whiteFixedAt"):
        s.setdefault(k, None)
    _migrate_language(s)
    return s


def _migrate_language(s):
    """Sessions recorded with the Russian UI (before v1.6): English flags and menu keys."""
    from .psettings import OLD_LASER_KEY
    tr = lambda fl: [LEGACY_FLAGS.get(f, f) for f in (fl or [])]  # noqa: E731
    for m in s.get("measurements", []):
        m["flags"] = tr(m.get("flags"))
    for r in s.get("runs", []):
        for sg in r.get("segments", []):
            sg["flags"] = tr(sg.get("flags"))
    for mv in s.get("menuValues", {}).values():
        if OLD_LASER_KEY in mv:
            v = mv.pop(OLD_LASER_KEY)
            if not mv.get("Brightness level (laser)"):
                mv["Brightness level (laser)"] = v


class Store:
    def __init__(self, root=None):
        self.root = root or data_dir()
        self.sessions_dir = os.path.join(self.root, "sessions")
        os.makedirs(self.sessions_dir, exist_ok=True)
        self.settings_path = os.path.join(self.root, "settings.json")

    # ---------------------------------------------------------------- settings
    def load_settings(self):
        st = dict(DEFAULT_SETTINGS)
        try:
            with open(self.settings_path, encoding="utf-8") as f:
                st.update(json.load(f))
        except (OSError, ValueError):
            pass
        return st

    def save_settings(self, st):
        atomic_write_json(self.settings_path, st)

    # ---------------------------------------------------------------- sessions
    def path(self, sid):
        return os.path.join(self.sessions_dir, sid + ".json")

    def log_path(self, sid):
        return os.path.join(self.sessions_dir, sid + ".spotread.log")

    def save(self, s):
        s["updatedAt"] = now_iso()
        atomic_write_json(self.path(s["id"]), s)

    def load(self, sid):
        with open(self.path(sid), encoding="utf-8") as f:
            return migrate(json.load(f))

    def list(self):
        out = []
        for fn in os.listdir(self.sessions_dir):
            if not fn.endswith(".json"):
                continue
            p = os.path.join(self.sessions_dir, fn)
            try:
                with open(p, encoding="utf-8") as f:
                    s = json.load(f)
                out.append({"id": s["id"], "name": s.get("name", s["id"]), "createdAt": s.get("createdAt"),
                            "updatedAt": s.get("updatedAt"), "count": len(s.get("measurements", []))})
            except (OSError, ValueError, KeyError):
                continue
        out.sort(key=lambda r: r.get("updatedAt") or "", reverse=True)
        return out

    def latest(self):
        for row in self.list():
            try:
                return self.load(row["id"])
            except (OSError, ValueError):
                continue
        return None
