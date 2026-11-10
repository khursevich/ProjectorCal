"""UI smoke: going through the wizard on the meter simulation from start to report (Playwright, Chromium).

Run:  python3 tests/e2e_ui.py [screenshots_dir]
Needs the playwright package with chromium installed; without it the test is skipped.
"""
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIP: playwright is not installed")
    sys.exit(0)

SHOTS = sys.argv[1] if len(sys.argv) > 1 else None


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main():
    data = tempfile.mkdtemp(prefix="pcal-e2e-")
    port = free_port()
    env = dict(os.environ, PROJECTORCAL_MOCK_DELAY="0.05", PROJECTORCAL_MOCK_SPEED="10")
    srv = subprocess.Popen([sys.executable, "-m", "projectorcal", "--mock", "--no-browser", "--port", str(port),
                            "--data-dir", data], cwd=ROOT, env=env)
    errors = []
    try:
        time.sleep(1.5)
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            pg = b.new_page(viewport={"width": 1500, "height": 950})
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            pg.goto("http://127.0.0.1:%d/" % port)
            pg.wait_for_selector("text=Meter ready", timeout=15000)

            def shot(name):
                if SHOTS:
                    os.makedirs(SHOTS, exist_ok=True)
                    pg.screenshot(path=os.path.join(SHOTS, name + ".png"), full_page=False)

            def step_done():
                pg.click("[data-act=stepDone]")
                time.sleep(0.8)

            # 0. preparation
            pg.wait_for_selector("text=Test reading")
            pg.click("button[data-act=measureTest]")
            pg.wait_for_selector("text=Test reading: Y", timeout=10000)
            pg.click("input[data-key=warm]")
            time.sleep(0.5)
            shot("0_prep")
            step_done()
            # 1. full check: settings → SDR auto run → plan → "set it" → second run → links; HDR
            import json
            import urllib.request

            def post(path, body):
                rq = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=json.dumps(body).encode(),
                                            headers={"Content-Type": "application/json"})
                urllib.request.urlopen(rq).read()
            pg.wait_for_selector("text=Full check — all patches in one file")
            pg.click("button[data-act=psToggle]")
            pg.wait_for_selector("input[data-ps='sdr.blackLevel']")
            pg.fill("input[data-ps='sdr.blackLevel']", "53")
            pg.press("input[data-ps='sdr.blackLevel']", "Tab")
            pg.fill("input[data-ps='sdr.gammaPoints.1']", "-14")
            pg.press("input[data-ps='sdr.gammaPoints.1']", "Tab")
            time.sleep(0.8)
            shot("1_full_settings")
            pg.click("button[data-act=psToggle]")
            pg.click("button[data-act=fullStart][data-arg=sdr]")
            pg.wait_for_selector("text=waiting for the start mark", timeout=5000)
            shot("1_full_arming")
            pg.wait_for_selector("text=do not touch the remote", timeout=30000)
            time.sleep(2)
            shot("1_full_running")
            pg.wait_for_selector("text=Adjustment plan", timeout=120000)
            time.sleep(0.8)
            shot("1_full_result")
            btn = pg.query_selector("button[data-act=planApply]")
            assert btn, "no concrete values in the plan"
            fv = json.loads(urllib.request.urlopen("http://127.0.0.1:%d/api/session" % port).read())
            first = [i for i in fv["analysis"]["full"]["latest"]["sdr"]["plan"] if i.get("set")][0]
            gain = list(json.loads(urllib.request.urlopen("http://127.0.0.1:%d/api/state" % port).read())["mock"]["gain"])
            for x in first["set"]:
                if x["path"].startswith("sdr.gain."):
                    gain["RGB".index(x["path"][-1])] = x["to"]
            post("/api/mock", {"gain": gain})
            btn.click()
            time.sleep(0.8)
            assert "Repeat the check" in pg.inner_text("#center")
            pg.click("button[data-act=fullStart][data-arg=sdr]")
            pg.wait_for_selector("text=do not touch the remote", timeout=30000)
            pg.wait_for_selector("text=What changed since the previous run", timeout=120000)
            time.sleep(0.8)
            assert "Setting links (learned" in pg.inner_text("#center")
            shot("1_full_compare")
            pg.click("button[data-act=fullStart][data-arg=hdr]")
            pg.wait_for_selector("text=do not touch the remote", timeout=30000)
            pg.wait_for_selector("text=Reference white (203 nits)", timeout=120000)
            shot("1_full_hdr")
            pg.click("button[data-act=fullTab][data-arg=sdr]")
            time.sleep(0.4)
            step_done()
            # 1. black: meter check 100/95 manually
            pg.wait_for_selector("text=PLUGE — Black Level")
            for _ in range(2):
                time.sleep(1.7)  # the "user" switches the patch
                pg.click("button[data-act=passMeasure]")
                pg.wait_for_function("() => !document.querySelector('[data-act=passMeasure]').textContent.includes('Measuring')",
                                     timeout=10000)
                time.sleep(0.8)
            pg.wait_for_selector("text=normal (target ≈ 0.88", timeout=5000)
            shot("1_black")
            step_done()
            # 2. laser
            pg.wait_for_selector("#laserLevel")
            pg.fill("#laserLevel", "10")
            time.sleep(2.0)
            pg.click("button[data-act=fixWhite]")
            pg.wait_for_selector("text=Fixed:", timeout=10000)
            hdr = pg.inner_text("#hdr")
            assert "10" in hdr and "nits" in hdr, hdr
            shot("2_laser")
            step_done()
            # 3. gray scale: circle → "Gain" plan → live → "menu" change → new circle
            pg.wait_for_selector("text=Next action")
            pg.wait_for_selector("text=Pass complete", timeout=90000)
            time.sleep(1)
            plan = pg.inner_text(".now")
            assert "Next action".upper() in plan.upper(), plan
            shot("3_gray_plan")
            pg.click("button[data-act=grayLive]") if pg.query_selector("button[data-act=grayLive]") else \
                pg.evaluate("() => fetch('/api/graylive', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({patchId:'SDR_01'})})")
            pg.wait_for_selector("#wb-R-m")
            time.sleep(2.5)
            assert pg.inner_text("#wbHints").strip(), "no live hints"
            shot("3_gray_live")
            pg.click("button[data-act=grayLiveOff]")
            pg.wait_for_selector("text=Next action")
            pg.on("dialog", lambda d: d.accept())
            pg.click("button[data-act=passNew]")
            pg.wait_for_selector("text=Pass complete", timeout=90000)
            time.sleep(1)
            txt = pg.inner_text("#center")
            assert "Circles" in txt and "#2" in txt, txt[:500]
            shot("3_gray_circle2")
            step_done()
            # 4. gamma by points: pass → corrections → set → check → done
            pg.wait_for_selector("text=fine-tune the current points or start from scratch")
            pg.click("button[data-act=gpStart]")
            gp = [0] * 20
            for rnd in range(6):
                pg.wait_for_selector("button[data-act=gpPlan]", timeout=150000)
                pg.click("button[data-act=gpPlan]")
                pg.wait_for_selector("text=Corrections, round", timeout=5000)
                time.sleep(0.5)
                if rnd == 0:
                    shot("4_gp_plan")
                if not pg.query_selector("button[data-act=gpApplied]"):
                    break
                plan = json.loads(urllib.request.urlopen("http://127.0.0.1:%d/api/session" % port).read())
                for r in plan["session"]["steps"]["gpoints"]["plan"]:
                    gp[r["point"] - 1] = r["new"]
                post("/api/mock", {"gp": gp})
                pg.click("button[data-act=gpApplied]")
            pg.click("button[data-act=gpFinish]")
            pg.wait_for_selector("text=Gamma is set", timeout=5000)
            assert "1:-" in pg.inner_text("#center")
            shot("4_gp_done")
            step_done()
            # 5. colors
            pg.wait_for_selector("text=xy diagram")
            pg.wait_for_selector("text=Pass complete", timeout=60000)
            time.sleep(1)
            shot("5_color")
            step_done()
            # 6. verification (quick)
            pg.wait_for_selector("text=without changes")
            pg.click("input[data-opt=quick]")
            pg.wait_for_selector("text=Pass complete", timeout=60000)
            shot("6_verify")
            step_done()
            # 7. eyes
            step_done()
            # 8. HDR
            pg.wait_for_selector("text=Before the pass")
            t0 = time.time()
            while time.time() - t0 < 120:
                txt = pg.inner_text("#center")
                if "Pass complete" in txt:
                    break
                if "The readings have not changed" in txt:  # steps near the peak are equal → manual reading
                    pg.click("button[data-act=passMeasure]")
                    time.sleep(1.0)
                time.sleep(0.5)
            pg.wait_for_selector("text=Pass complete", timeout=1000)
            time.sleep(1)
            txt = pg.inner_text("#center")
            assert "PQ" in txt and re.search(r"Projector peak", txt), txt[:300]
            shot("8_hdr")
            step_done()
            # 9. DV
            step_done()
            # 10. summary and report
            pg.wait_for_selector("text=Summary")
            pg.fill("input[data-menu=sdr][data-key='Gain R']", "-10")
            pg.press("input[data-menu=sdr][data-key='Gain R']", "Tab")
            time.sleep(0.8)
            shot("10_summary")
            rp = b.new_page(viewport={"width": 900, "height": 1200})
            rp.on("pageerror", lambda e: errors.append(str(e)))
            rp.goto("http://127.0.0.1:%d/report.html" % port)
            rp.wait_for_selector("text=Adjustment plan")
            txt = rp.inner_text("body")
            assert "Setting links" in txt and "Run history" in txt and "HDR10: full check" in txt, txt[:400]
            if SHOTS:
                rp.emulate_media(media="print")
                rp.screenshot(path=os.path.join(SHOTS, "11_report_print.png"), full_page=True)
                rp.pdf(path=os.path.join(SHOTS, "report.pdf"), format="A4", print_background=True)
            # layout check: no element exceeds the page width
            over = rp.evaluate("""() => { const W = document.querySelector('.wrap').getBoundingClientRect().right + 1;
                return [...document.querySelectorAll('.wrap *')].filter(e => e.getBoundingClientRect().right > W)
                       .slice(0, 5).map(e => e.tagName + ' ' + (e.textContent || '').slice(0, 40)); }""")
            assert not over, over
            rp.close()
            # export
            for path in ("/export/measurements.csv", "/export/session.json"):
                r = pg.request.get("http://127.0.0.1:%d%s" % (port, path))
                assert r.status == 200 and len(r.body()) > 200, path
            # meter errors: switching to a non-existent spotread → a clear message
            post("/api/settings", {"meterMode": "argyll", "spotreadPath": "/nonexistent/spotread"})
            ok = False
            for _ in range(40):
                time.sleep(0.25)
                t = pg.inner_text("#meter")
                if "spotread not found" in t or "Meter not found" in t:
                    ok = True
                    break
            assert ok, pg.inner_text("#meter")
            shot("12_meter_error")
            b.close()
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=10)
        except subprocess.TimeoutExpired:
            srv.kill()
        shutil.rmtree(data, ignore_errors=True)
    if errors:
        print("JS ERRORS:", errors)
        sys.exit(1)
    print("UI-smoke OK")


if __name__ == "__main__":
    main()
