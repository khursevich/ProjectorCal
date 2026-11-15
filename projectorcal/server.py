"""UI HTTP server (127.0.0.1 only) and the application entry point."""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__
from . import analysis as A
from .catalog import Catalog
from .controller import Controller, meter_check_info
from .session import Store, atomic_write_json

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
APP_ID = "ProjectorCal"
IDLE_SHUTDOWN = 3 * 3600  # s without browser requests → exit (frees the meter)

MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
        ".json": "application/json; charset=utf-8", ".ico": "image/x-icon"}


def find_assets():
    cands = [os.environ.get("PROJECTORCAL_ASSETS"),
             os.path.join(os.path.dirname(HERE), "assets", "patches"),
             os.path.join(HERE, "..", "..", "assets", "patches")]
    for c in cands:
        if c and os.path.exists(os.path.join(c, "patches.json")):
            return os.path.abspath(c)
    raise SystemExit("Patch catalog not found (assets/patches/patches.json).")


def is_mac():
    return sys.platform == "darwin"


def reveal(path):
    """Show a file or folder in Finder / Explorer / the Linux file manager."""
    try:
        if is_mac():
            subprocess.Popen(["open", "-R", path] if os.path.isfile(path) else ["open", path],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif os.name == "nt":
            if os.path.isfile(path):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                os.startfile(os.path.normpath(path))  # noqa: S606 — a local folder
        else:
            subprocess.Popen(["xdg-open", path if os.path.isdir(path) else os.path.dirname(path)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


from .sequence import VERSION as SEQ_VERSION  # noqa: E402

PLEX_README = """Calibration patches for Vidda projectors (ProjectorCal)

Plex: copy this whole folder to your Plex server, add a library of type "Other Videos" pointing to it, Scan.
USB stick: copy the folder to an exFAT stick and open the files in the projector's own media player.
Play the file that ProjectorCal shows. The number at the start of the name is the order.
Each file is a still frame lasting 10 minutes:
   just play it (no need to pause).
   If the player shows its control bar over the picture, wait for it to disappear.

SDR_01…21  gray 100% → 0% (5% steps)      SDR_22…27  R Y G C B M 100%
SDR_28…51  saturation 25/50/75/100%    SDR_52 PLUGE    SDR_53 white clipping
HDR_01…21  HDR10 PQ gray 100% → 0%
AUTO/ProjectorCal_AUTO_SDR_v3.mp4    full SDR check (14 min): all patches in a row — for the automatic run
AUTO/ProjectorCal_AUTO_HDR10_v3.mp4  full HDR10 check (6.5 min)
AUTO/*_v3_slow.mp4                   the same for slower meters (ColorMunki Display, Spyder, i1Pro…): 27 / 13 min
                                     ProjectorCal shows which one to play (Settings → Meter model).

All files 3840×2160, 24p, HEVC Main10, limited range. HDR10: PQ / BT.2020, mastering P3-D65 1000 nits.
patches.json holds the targets for the program; the player does not need it (but it does no harm).
"""


def measurements_csv(session, catalog):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["session", "step", "pass", "patch", "patch_name", "timestamp", "X", "Y", "Z", "x", "y", "CCT",
                "duv_d65", "bal_R", "bal_G", "bal_B", "samples", "sdY_%", "auto", "flags"])
    for m in session["measurements"]:
        d = A.derive((m["X"], m["Y"], m["Z"]))
        p = catalog.by_id.get(m["patchId"], {})
        w.writerow([session["id"], m["stepId"], m.get("pass", 1), m["patchId"], p.get("name", ""), m["timestamp"],
                    "%.5f" % m["X"], "%.5f" % m["Y"], "%.5f" % m["Z"], d["x"], d["y"], d["cct"], d["duv"],
                    d["bal"][0], d["bal"][1], d["bal"][2], m.get("samples"), m.get("sdYpercent"),
                    int(bool(m.get("auto"))), " ".join(m.get("flags", []))])
    return buf.getvalue()


class App:
    def __init__(self, store, catalog, mock_delay=0.35):
        self.store = store
        self.catalog = catalog
        self.ctl = Controller(store, catalog, mock_delay=mock_delay)
        self.httpd = None
        self.last_request = time.monotonic()
        self.stopping = False

    def export_patches(self, dest):
        src = self.catalog.dir
        if not any(f.endswith(".mp4") for _, _, files in os.walk(src) for f in files):
            raise OSError("This build has no video patches. Download ProjectorCal-patches-v%d.zip from the project's "
                          "GitHub Releases page and copy it to Plex or a USB stick." % SEQ_VERSION)
        dest = os.path.expanduser(dest or "~/Desktop/ProjectorCal Patches")
        os.makedirs(dest, exist_ok=True)
        for sub in ("SDR", "HDR10", "AUTO"):
            if os.path.isdir(os.path.join(src, sub)):
                shutil.copytree(os.path.join(src, sub), os.path.join(dest, sub), dirs_exist_ok=True)
        shutil.copy2(os.path.join(src, "patches.json"), os.path.join(dest, "patches.json"))
        with open(os.path.join(dest, "README_patches.txt"), "w", encoding="utf-8") as f:
            f.write(PLEX_README)
        n = sum(len(files) for _, _, files in os.walk(dest))
        reveal(dest)
        return {"dest": dest, "files": n}

    def shutdown(self):
        if self.stopping:
            return
        self.stopping = True
        self.ctl.shutdown()
        if self.httpd:
            threading.Thread(target=self.httpd.shutdown, daemon=True).start()


def make_handler(app):
    class H(BaseHTTPRequestHandler):
        server_version = "ProjectorCal"

        def log_message(self, fmt, *args):  # no console noise
            pass

        # ---------------------------------------------------------- responses
        def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            elif isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                return {}
            try:
                return json.loads(self.rfile.read(n).decode("utf-8"))
            except ValueError:
                return {}

        def _host_ok(self):
            # local names only (DNS-rebinding protection)
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
            return host in ("127.0.0.1", "localhost", "::1")

        def _origin_ok(self):
            # protection against requests from third-party browser pages
            o = self.headers.get("Origin")
            if not o:
                return True
            host = self.headers.get("Host", "")
            return o in ("http://" + host,)

        # ---------------------------------------------------------- GET
        def do_GET(self):
            app.last_request = time.monotonic()
            if not self._host_ok():
                return self._send(403, {"error": "host"})
            u = urlparse(self.path)
            path = u.path
            ctl = app.ctl
            try:
                if path == "/api/ping":
                    return self._send(200, {"app": APP_ID, "version": __version__, "pid": os.getpid()})
                if path == "/api/state":
                    return self._send(200, ctl.state())
                if path == "/api/session":
                    return self._send(200, ctl.session_view())
                if path == "/api/catalog":
                    return self._send(200, {"patches": app.catalog.public(), "dir": app.catalog.dir})
                if path == "/api/log":
                    return self._send(200, {"lines": ctl.log_lines(),
                                            "file": app.store.log_path(ctl.session["id"])})
                if path == "/api/meter/check":
                    return self._send(200, meter_check_info(ctl.settings))
                if path == "/report.html":
                    from . import report
                    return self._send(200, report.build(ctl), "text/html; charset=utf-8")
                if path.startswith("/export/"):
                    return self._export(path, parse_qs(u.query))
                return self._static(path)
            except Exception as e:  # noqa: BLE001
                return self._send(500, {"error": str(e)})

        def _export(self, path, q):
            ctl = app.ctl
            with ctl.lock:
                s = json.loads(json.dumps(ctl.session))
            runs = [r for r in s.get("runs", []) if r.get("startedAt")]
            if runs:     # named after the actual last run: ProjectorCal_2026-11-05_21-08_SDR-5
                last = max(runs, key=lambda r: r["startedAt"])
                base = "ProjectorCal_%s_%s" % (last["startedAt"][:16].replace("T", "_").replace(":", "-"), last["id"])
            else:
                base = "ProjectorCal_%s" % s["id"]
            if path == "/export/session.json":
                return self._send(200, json.dumps(s, ensure_ascii=False, indent=1), "application/json; charset=utf-8",
                                  {"Content-Disposition": 'attachment; filename="%s.json"' % base})
            if path == "/export/measurements.csv":
                return self._send(200, "﻿" + measurements_csv(s, app.catalog), "text/csv; charset=utf-8",
                                  {"Content-Disposition": 'attachment; filename="%s.csv"' % base})
            if path == "/export/spotread.log":
                try:
                    with open(app.store.log_path(s["id"]), encoding="utf-8", errors="replace") as f:
                        txt = f.read()
                except OSError:
                    txt = ""
                return self._send(200, txt, "text/plain; charset=utf-8",
                                  {"Content-Disposition": 'attachment; filename="%s_spotread.log"' % base})
            return self._send(404, {"error": "not found"})

        def _static(self, path):
            if path in ("/", "/index.html", "/report"):
                path = "/index.html"
            full = os.path.normpath(os.path.join(STATIC, path.lstrip("/")))
            if not full.startswith(STATIC + os.sep) or not os.path.isfile(full):
                return self._send(404, "not found", "text/plain; charset=utf-8")
            with open(full, "rb") as f:
                data = f.read()
            return self._send(200, data, MIME.get(os.path.splitext(full)[1], "application/octet-stream"))

        # ---------------------------------------------------------- POST
        def do_POST(self):
            app.last_request = time.monotonic()
            if not self._host_ok() or not self._origin_ok():
                return self._send(403, {"error": "origin"})
            path = urlparse(self.path).path
            d = self._json()
            ctl = app.ctl
            try:
                if path == "/api/goto":
                    ctl.goto(d.get("stepId", "prep"))
                elif path == "/api/step":
                    ctl.set_step(d["stepId"], d)
                elif path == "/api/fields":
                    ctl.set_fields(d)
                elif path == "/api/pass":
                    ctl.pass_action(d["stepId"], d["action"], d.get("arg"))
                elif path == "/api/measure":
                    ctl.measure(d.get("purpose", "test"), d)
                elif path == "/api/full/start":
                    ctl.full_start(d.get("mode", "sdr"))
                elif path == "/api/full/stop":
                    ctl.full_stop(bool(d.get("save", True)))
                elif path == "/api/full/reanalyze":
                    ctl.full_reanalyze(str(d.get("run", "")))
                elif path == "/api/psettings":
                    ctl.set_psettings(d.get("changes") or {})
                elif path == "/api/gp":
                    ctl.gp_action(d.get("action"), d)
                elif path == "/api/graylive":
                    ctl.gray_live(d.get("patchId"))
                elif path == "/api/live":
                    ctl.free_live(bool(d.get("on")))
                elif path == "/api/meter/retry":
                    ctl.meter_retry()
                elif path == "/api/meter/continue":
                    ctl.meter_calibration_continue()
                elif path == "/api/dismiss":
                    ctl.dismiss(d.get("what"), d.get("id"))
                elif path == "/api/settings":
                    ctl.update_settings(d)
                elif path == "/api/mock":
                    ctl.mock_update(d)
                elif path == "/api/session/new":
                    ctl.new_session(d.get("name"))
                elif path == "/api/session/open":
                    ctl.open_session(d["id"])
                elif path == "/api/measurement/delete":
                    ctl.delete_measurement(d["id"])
                elif path == "/api/patches/export":
                    return self._send(200, app.export_patches(d.get("dest")))
                elif path == "/api/reveal":
                    what = d.get("what")
                    target = {"data": app.store.root, "sessions": app.store.sessions_dir,
                              "patches": app.catalog.dir}.get(what)
                    if target:
                        reveal(target)
                elif path == "/api/quit":
                    self._send(200, {"ok": True})
                    threading.Thread(target=app.shutdown, daemon=True).start()
                    return None
                else:
                    return self._send(404, {"error": "not found"})
                return self._send(200, {"ok": True})
            except (KeyError, ValueError, TypeError) as e:
                return self._send(400, {"error": "Bad request: %s" % e})
            except OSError as e:
                return self._send(500, {"error": "File system error: %s" % e})
            except Exception as e:  # noqa: BLE001
                return self._send(500, {"error": str(e)})

    return H


def running_instance(store):
    """If the program is already running — its address."""
    p = os.path.join(store.root, "instance.json")
    try:
        with open(p, encoding="utf-8") as f:
            info = json.load(f)
        url = "http://127.0.0.1:%d" % info["port"]
        with urllib.request.urlopen(url + "/api/ping", timeout=1.5) as r:
            if json.loads(r.read().decode()).get("app") == APP_ID:
                return url
    except Exception:  # noqa: BLE001
        return None
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="projectorcal", description="ProjectorCal — Vidda / Hisense projector calibration")
    ap.add_argument("--port", type=int, default=0, help="port (default 8765 or the next free one)")
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    ap.add_argument("--data-dir", help="data folder (default: macOS ~/Library/Application Support/ProjectorCal, "
                                       "Windows %%APPDATA%%\\ProjectorCal, Linux ~/.local/share/ProjectorCal)")
    ap.add_argument("--mock", action="store_true", help="enable the meter simulation")
    args = ap.parse_args(argv)

    store = Store(args.data_dir)
    url = running_instance(store)
    if url:
        print("ProjectorCal is already running:", url)
        if not args.no_browser:
            webbrowser.open(url)
        return 0

    catalog = Catalog(find_assets())
    if args.mock:
        st = store.load_settings()
        st["meterMode"] = "mock"
        store.save_settings(st)
    app = App(store, catalog, mock_delay=float(os.environ.get("PROJECTORCAL_MOCK_DELAY", "0.35")))

    ports = [args.port] if args.port else list(range(8765, 8786))
    httpd = None
    for port in ports:
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
            break
        except OSError:
            continue
    if httpd is None:
        app.ctl.shutdown()
        print("No free port", file=sys.stderr)
        return 1
    httpd.daemon_threads = True
    app.httpd = httpd
    port = httpd.server_address[1]
    url = "http://127.0.0.1:%d/" % port
    inst = os.path.join(store.root, "instance.json")
    atomic_write_json(inst, {"port": port, "pid": os.getpid()})

    def on_signal(signum, frame):
        threading.Thread(target=app.shutdown, daemon=True).start()

    for sig in [getattr(signal, n) for n in ("SIGTERM", "SIGINT", "SIGHUP", "SIGBREAK") if hasattr(signal, n)]:
        try:
            signal.signal(sig, on_signal)
        except (ValueError, OSError):
            pass

    def idle_watch():
        while not app.stopping:
            time.sleep(30)
            if time.monotonic() - app.last_request > IDLE_SHUTDOWN:
                app.shutdown()

    threading.Thread(target=idle_watch, daemon=True).start()
    print("ProjectorCal %s: %s" % (__version__, url), flush=True)
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever(poll_interval=0.5)
    finally:
        app.shutdown()
        try:
            os.remove(inst)
        except OSError:
            pass
        httpd.server_close()
    return 0
