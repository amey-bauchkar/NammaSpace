"""Minimal Chrome DevTools Protocol client for automated browser tests (no extra packages beyond
websocket-client, which is already installed).

Chrome is started with a NEW temporary profile (never the user's profile), on 127.0.0.1, and is
closed at the end. Use as a context manager:

    with Chrome(headless=True) as c:
        c.navigate("http://127.0.0.1:8781/studio.html")
        c.eval("document.title")
"""
import base64
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket

CHROME_CANDIDATES = [
    os.environ.get("NAMMA_CHROME", ""),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]


def find_chrome():
    for c in CHROME_CANDIDATES:
        if c and Path(c).exists():
            return c
    for name in ("chrome", "google-chrome", "chromium", "msedge"):
        p = shutil.which(name)
        if p:
            return p
    raise FileNotFoundError("Chrome / Edge not found; set NAMMA_CHROME")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class CDPError(RuntimeError):
    pass


class Chrome:
    def __init__(self, headless=True, width=1440, height=900, extra_args=()):
        self.headless, self.width, self.height, self.extra = headless, width, height, list(extra_args)
        self.console, self.exceptions, self.events = [], [], []
        self._id = 0

    def __enter__(self):
        self.profile = tempfile.mkdtemp(prefix="namma-cdp-")
        self.port = free_port()
        args = [find_chrome(), f"--remote-debugging-port={self.port}", "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={self.profile}", "--no-first-run", "--no-default-browser-check",
                "--disable-extensions", "--disable-sync", "--disable-background-networking",
                "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                "--disable-backgrounding-occluded-windows", f"--window-size={self.width},{self.height}",
                "--enable-unsafe-swiftshader", *self.extra]
        if self.headless:
            args.append("--headless=new")
        self.proc = subprocess.Popen(args + ["about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json", timeout=2).read())
                page = next(t for t in tabs if t.get("type") == "page")
                self.ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=120, suppress_origin=True)
                break
            except (OSError, StopIteration, ValueError):
                time.sleep(0.3)
        else:
            raise CDPError("Chrome did not expose a debugging endpoint")
        for dom in ("Page", "Runtime", "Log", "Network", "DOM"):
            self.send(f"{dom}.enable")
        self.send("Emulation.setDeviceMetricsOverride", width=self.width, height=self.height, deviceScaleFactor=1, mobile=False)
        return self

    def __exit__(self, *exc):
        try:
            self.send("Browser.close")
        except Exception:  # noqa: BLE001 - closing is best effort
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)

    # ------------------------------------------------------------------ protocol
    def _record(self, msg):
        m = msg.get("method")
        if m == "Runtime.consoleAPICalled":
            p = msg["params"]
            text = " ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args", []))
            self.console.append({"type": p.get("type"), "text": text[:500]})
        elif m == "Runtime.exceptionThrown":
            d = msg["params"]["exceptionDetails"]
            self.exceptions.append((d.get("exception") or {}).get("description", d.get("text", ""))[:800])
        elif m == "Log.entryAdded":
            e = msg["params"]["entry"]
            self.console.append({"type": f"log.{e.get('level')}", "text": e.get("text", "")[:500], "url": e.get("url")})
        elif m:
            self.events.append(m)

    def send(self, method, **params):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise CDPError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            self._record(msg)

    def pump(self, seconds):
        """Process events for a while (keeps console capture flowing)."""
        end = time.time() + seconds
        self.ws.settimeout(0.2)
        try:
            while time.time() < end:
                try:
                    self._record(json.loads(self.ws.recv()))
                except websocket.WebSocketTimeoutException:
                    pass
        finally:
            self.ws.settimeout(120)

    def navigate(self, url, wait=1.0):
        self.send("Page.navigate", url=url)
        self.pump(wait)

    def eval(self, expr, await_promise=True, timeout_ms=600_000):
        r = self.send("Runtime.evaluate", expression=expr, awaitPromise=await_promise, returnByValue=True,
                      timeout=timeout_ms)
        if "exceptionDetails" in r:
            raise CDPError(f"JS error: {r['exceptionDetails'].get('exception', {}).get('description', r['exceptionDetails'])}")
        return r.get("result", {}).get("value")

    def wait_for(self, expr, timeout=60, interval=0.5):
        end = time.time() + timeout
        while time.time() < end:
            v = self.eval(expr)
            if v:
                return v
            self.pump(interval)
        raise CDPError(f"timeout waiting for: {expr}")

    def screenshot(self, path):
        data = self.send("Page.captureScreenshot", format="png")["data"]
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(base64.b64decode(data))
        return str(path)

    # ------------------------------------------------------------------ real input events
    def key(self, code, key=None, down_ms=0):
        key = key or (code[3:].lower() if code.startswith("Key") else code)
        vk = {"KeyW": 87, "KeyA": 65, "KeyS": 83, "KeyD": 68, "KeyF": 70, "KeyR": 82, "KeyE": 69, "KeyQ": 81,
              "ShiftLeft": 16, "Escape": 27, "BracketRight": 221, "BracketLeft": 219}.get(code, 0)
        self.send("Input.dispatchKeyEvent", type="keyDown", code=code, key=key, windowsVirtualKeyCode=vk)
        if down_ms:
            self.pump(down_ms / 1000)
        self.send("Input.dispatchKeyEvent", type="keyUp", code=code, key=key, windowsVirtualKeyCode=vk)

    def mouse_drag(self, x0, y0, dx, dy, steps=12):
        self.send("Input.dispatchMouseEvent", type="mouseMoved", x=x0, y=y0)
        self.send("Input.dispatchMouseEvent", type="mousePressed", x=x0, y=y0, button="left", buttons=1, clickCount=1)
        for i in range(1, steps + 1):
            self.send("Input.dispatchMouseEvent", type="mouseMoved", x=x0 + dx * i / steps, y=y0 + dy * i / steps,
                      button="left", buttons=1)
            self.pump(0.02)
        self.send("Input.dispatchMouseEvent", type="mouseReleased", x=x0 + dx, y=y0 + dy, button="left", buttons=0, clickCount=1)

    def click(self, x, y):
        self.send("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        self.send("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", buttons=1, clickCount=1)
        self.send("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button="left", buttons=0, clickCount=1)

    def set_files(self, selector, paths):
        doc = self.send("DOM.getDocument")
        node = self.send("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector=selector)["nodeId"]
        if not node:
            raise CDPError(f"no element {selector}")
        self.send("DOM.setFileInputFiles", files=[str(Path(p).resolve()) for p in paths], nodeId=node)
