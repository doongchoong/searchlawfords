"""Chrome DevTools Protocol 러너.

헤드리스 Chrome을 빈 임시 프로필로 띄우고, 표준 라이브러리만으로 구현한
WebSocket 클라이언트로 붙는다. 어댑터는 Browser.new_page()가 주는 Page만 쓴다.

    with Browser.launch() as browser:
        page = browser.new_page()
        page.goto("https://law.kofia.or.kr/")
        print(page.title())

안전 규칙:
- 평소 프로필을 쓰지 않고 실행마다 새 임시 프로필과 임의 포트(--remote-debugging-port=0)를 쓴다.
- 디버깅 포트는 Chrome 기본값인 루프백(127.0.0.1)에만 열린다. check_env.py가 확인한다.
- 정상·비정상 종료 모두에서 Chrome 프로세스와 임시 프로필을 정리한다.
- 캡차·차단 화면이 보이면 우회하지 않고 BlockedError로 멈춘다.
- 사이트 요청은 store.throttle로 모든 세션이 공유하는 최소 간격을 지킨다.
"""
from __future__ import annotations

import atexit
import base64
import contextlib
import hashlib
import json
import os
import select
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import store

PROFILE_PREFIX = "regsearch-chrome-"
OWNER_FILE = "regsearch-owner.json"
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# 차단·캡차 화면 판별 문구. 규정 본문에 같은 낱말이 나올 수 있어 짧은 페이지에만 본문 검사를 한다.
BLOCK_PATTERNS = [
    "captcha", "recaptcha", "i'm not a robot", "access denied", "too many requests",
    "보안문자", "자동입력 방지", "자동입력방지", "로봇이 아닙니다",
    "비정상적인 접근", "비정상적인 요청", "접근이 차단", "요청이 차단", "로그인이 필요",
]
BLOCK_BODY_MAX_CHARS = 3000


class CDPError(RuntimeError):
    pass


class CDPTimeout(CDPError, TimeoutError):
    pass


class NavigationError(CDPError):
    pass


class JSError(CDPError):
    pass


class BlockedError(CDPError):
    """캡차·봇 차단·로그인 요구 화면. 우회하지 않고 사람에게 넘긴다."""

    def __init__(self, message: str, screenshot: Path | None = None):
        super().__init__(message if screenshot is None else f"{message} (스크린샷: {screenshot})")
        self.screenshot = screenshot


class ConnectionClosed(CDPError):
    pass


# ---------------------------------------------------------------------------
# WebSocket (RFC 6455) 클라이언트: CDP에 필요한 만큼만 구현한다
# ---------------------------------------------------------------------------

def _mask(data: bytes, key: bytes) -> bytes:
    n = len(data)
    if n == 0:
        return data
    k = (key * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "little") ^ int.from_bytes(k, "little")).to_bytes(n, "little")


class WebSocket:
    def __init__(self, url: str, timeout: float = 30.0):
        parts = urlsplit(url)
        if parts.scheme != "ws":
            raise ValueError(f"ws:// 주소만 지원: {url}")
        host, port = parts.hostname, parts.port or 80
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        self.io_timeout = timeout
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self._buf = b""
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode()
        )
        while b"\r\n\r\n" not in self._buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionClosed("WebSocket 핸드셰이크 중 연결 종료")
            self._buf += chunk
        head, self._buf = self._buf.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        if " 101 " not in lines[0] + " ":
            raise CDPError(f"WebSocket 업그레이드 실패: {lines[0]}")
        expected = base64.b64encode(hashlib.sha1((key + _WS_GUID).encode()).digest()).decode()
        headers = {k.strip().lower(): v.strip() for k, v in (l.split(":", 1) for l in lines[1:] if ":" in l)}
        if headers.get("sec-websocket-accept") != expected:
            raise CDPError("WebSocket accept 키 불일치")

    def _recv_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self._buf)))
            if not chunk:
                raise ConnectionClosed("WebSocket 연결 종료")
            self._buf += chunk
        data, self._buf = self._buf[:n], self._buf[n:]
        return data

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        header = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", n)
        key = os.urandom(4)
        self.sock.sendall(header + key + _mask(payload, key))

    def send(self, text: str) -> None:
        self._send_frame(0x1, text.encode("utf-8"))

    def recv(self, timeout: float) -> str | None:
        """메시지 하나를 받는다. timeout 동안 아무것도 오지 않으면 None.

        프레임 중간에서 시간 초과가 나면 스트림이 어긋나므로, 기다리는 것은
        첫 바이트까지만이고 프레임을 읽기 시작하면 끝까지 읽는다.
        """
        if not self._buf:
            ready, _, _ = select.select([self.sock], [], [], max(0.0, timeout))
            if not ready:
                return None
        self.sock.settimeout(self.io_timeout)
        parts: list[bytes] = []
        while True:
            b0, b1 = self._recv_exact(2)
            fin, opcode = b0 & 0x80, b0 & 0x0F
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._recv_exact(8))[0]
            key = self._recv_exact(4) if b1 & 0x80 else None
            payload = self._recv_exact(n)
            if key:
                payload = _mask(payload, key)
            if opcode == 0x8:
                raise ConnectionClosed("Chrome이 WebSocket을 닫음")
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            parts.append(payload)
            if fin:
                return b"".join(parts).decode("utf-8")

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._send_frame(0x8, struct.pack("!H", 1000))
        with contextlib.suppress(OSError):
            self.sock.close()


# ---------------------------------------------------------------------------
# CDP 연결: 브라우저 WebSocket 하나에 페이지 세션을 sessionId로 묶는다(flatten 모드)
# ---------------------------------------------------------------------------

class CDPConnection:
    MAX_EVENTS = 2000

    def __init__(self, ws_url: str):
        self.ws = WebSocket(ws_url)
        self._next_id = 0
        self._events: list[dict] = []

    def _read(self, deadline: float) -> dict | None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        raw = self.ws.recv(remaining)
        if raw is None:
            return None
        msg = json.loads(raw)
        if "id" not in msg and "method" in msg:
            self._events.append(msg)
            if len(self._events) > self.MAX_EVENTS:
                del self._events[: len(self._events) - self.MAX_EVENTS]
        return msg

    def call(self, method: str, params: dict | None = None, session_id: str | None = None,
             timeout: float = 30.0) -> dict:
        self._next_id += 1
        mid = self._next_id
        msg: dict = {"id": mid, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        self.ws.send(json.dumps(msg))
        deadline = time.monotonic() + timeout
        while True:
            reply = self._read(deadline)
            if reply is None:
                raise CDPTimeout(f"{method} 응답 시간 초과({timeout}s)")
            if reply.get("id") == mid:
                if "error" in reply:
                    raise CDPError(f"{method} 실패: {reply['error'].get('message')}")
                return reply.get("result", {})

    def clear_events(self, session_id: str | None = None, method: str | None = None) -> None:
        self._events = [
            e for e in self._events
            if not ((session_id is None or e.get("sessionId") == session_id)
                    and (method is None or e.get("method") == method))
        ]

    def wait_event(self, method: str, session_id: str | None = None, timeout: float = 30.0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            for i, e in enumerate(self._events):
                if e.get("method") == method and (session_id is None or e.get("sessionId") == session_id):
                    del self._events[i]
                    return e.get("params", {})
            if self._read(deadline) is None:
                raise CDPTimeout(f"{method} 이벤트 대기 시간 초과({timeout}s)")

    def close(self) -> None:
        self.ws.close()


# ---------------------------------------------------------------------------
# Chrome 프로세스 관리
# ---------------------------------------------------------------------------

def find_chrome() -> str:
    env = os.environ.get("REGSEARCH_CHROME")
    if env:
        return env
    candidates: list[Path] = []
    if os.name == "nt":
        for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
            if base:
                candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    elif sys.platform == "darwin":
        candidates.append(Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
    for c in candidates:
        if c.exists():
            return str(c)
    for name in ("chrome", "google-chrome", "google-chrome-stable", "chromium"):
        found = shutil.which(name)
        if found:
            return found
    raise FileNotFoundError("Chrome을 찾지 못했습니다. 설치하거나 REGSEARCH_CHROME에 chrome.exe 경로를 지정하세요.")


def pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # 접근 거부면 살아 있는 것
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=10)


def _remove_profile(path: Path, timeout: float = 10.0) -> bool:
    # Chrome 자식 프로세스가 파일을 잠깐 더 잡고 있을 수 있어 몇 번 다시 시도한다
    deadline = time.monotonic() + timeout
    while True:
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return True
        if time.monotonic() > deadline:
            return False
        time.sleep(0.3)


def cleanup_stale_profiles() -> None:
    """이전 실행이 비정상 종료로 남긴 임시 프로필을 지운다. 주인 Chrome이 살아 있으면 건드리지 않는다."""
    tmp = Path(tempfile.gettempdir())
    for d in tmp.glob(PROFILE_PREFIX + "*"):
        if not d.is_dir():
            continue
        try:
            owner = json.loads((d / OWNER_FILE).read_text(encoding="utf-8"))
            if pid_alive(int(owner["chrome_pid"])):
                continue
        except (OSError, ValueError, KeyError):
            # 주인 기록이 없으면 만든 지 1시간이 지난 것만 지운다
            if time.time() - d.stat().st_mtime < 3600:
                continue
        _remove_profile(d, timeout=2.0)


def _screenshot_path(name: str) -> Path:
    d = store.data_dir() / "screenshots"
    d.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:60]
    return d / f"{datetime.now():%Y%m%d-%H%M%S}-{safe}.png"


class Browser:
    def __init__(self, proc: subprocess.Popen, profile: Path, conn: CDPConnection, port: int, headless: bool):
        self.proc = proc
        self.profile = profile
        self.conn = conn
        self.port = port
        self.headless = headless
        self._closed = False
        self.user_agent = conn.call("Browser.getVersion").get("userAgent", "")
        atexit.register(self.close)

    @classmethod
    def launch(cls, headless: bool | None = None, chrome_path: str | None = None, timeout: float = 30.0) -> "Browser":
        """Chrome을 띄운다. headless를 생략하면 REGSEARCH_HEADFUL=1일 때 창을 띄운다."""
        if headless is None:
            headless = os.environ.get("REGSEARCH_HEADFUL") != "1"
        cleanup_stale_profiles()
        chrome = chrome_path or find_chrome()
        profile = Path(tempfile.mkdtemp(prefix=PROFILE_PREFIX))
        args = [
            chrome,
            "--remote-debugging-port=0",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-sync",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-default-apps",
            "--mute-audio",
            "--lang=ko-KR",
            "about:blank",
        ]
        if headless:
            args.insert(1, "--headless=new")
        proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            (profile / OWNER_FILE).write_text(
                json.dumps({"chrome_pid": proc.pid, "python_pid": os.getpid()}), encoding="utf-8")
            # 포트 0으로 띄우면 Chrome이 실제 포트와 경로를 DevToolsActivePort 파일에 적는다
            port_file = profile / "DevToolsActivePort"
            deadline = time.monotonic() + timeout
            while True:
                if proc.poll() is not None:
                    raise CDPError(f"Chrome이 바로 종료됨(코드 {proc.returncode}). 원격 디버깅이 정책으로 막혔을 수 있습니다.")
                with contextlib.suppress(OSError, ValueError):
                    lines = port_file.read_text(encoding="utf-8").split()
                    if len(lines) >= 2:
                        port, path = int(lines[0]), lines[1]
                        break
                if time.monotonic() > deadline:
                    raise CDPTimeout("Chrome 디버깅 포트를 기다리다 시간 초과")
                time.sleep(0.1)
            conn = CDPConnection(f"ws://127.0.0.1:{port}{path}")
            return cls(proc, profile, conn, port, headless)
        except BaseException:
            _kill_tree(proc)
            _remove_profile(profile)
            raise

    def new_page(self) -> "Page":
        target_id = self.conn.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        session_id = self.conn.call("Target.attachToTarget", {"targetId": target_id, "flatten": True})["sessionId"]
        page = Page(self, target_id, session_id)
        page.call("Page.enable")
        if "HeadlessChrome" in self.user_agent:
            # 헤드리스 표식만 일반 UA로 바꾼다(헤드리스로 막힐 때의 최소 대응). 그 이상의 위장은 하지 않는다.
            page.call("Emulation.setUserAgentOverride", {
                "userAgent": self.user_agent.replace("HeadlessChrome", "Chrome"),
                "acceptLanguage": "ko-KR,ko;q=0.9",
            })
        return page

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            self.conn.call("Browser.close", timeout=5)
        with contextlib.suppress(Exception):
            self.conn.close()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _kill_tree(self.proc)
        if not _remove_profile(self.profile):
            print(f"[cdp] 임시 프로필을 지우지 못함(다음 실행 때 다시 정리): {self.profile}", file=sys.stderr)
        with contextlib.suppress(Exception):
            atexit.unregister(self.close)

    def __enter__(self) -> "Browser":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class Page:
    def __init__(self, browser: Browser, target_id: str, session_id: str):
        self.browser = browser
        self.target_id = target_id
        self.session_id = session_id

    def call(self, method: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        return self.browser.conn.call(method, params, session_id=self.session_id, timeout=timeout)

    def goto(self, url: str, timeout: float = 30.0, retries: int = 2) -> int | None:
        """페이지를 열고 load 이벤트까지 기다린다. HTTP 상태 코드를 돌려준다(알 수 없으면 None).

        실패하면 최대 retries번 다시 시도하고, 끝내 실패하면 스크린샷을 남기고 NavigationError.
        차단 화면이면 다시 시도하지 않고 바로 BlockedError.
        """
        host = urlsplit(url).hostname
        conn = self.browser.conn
        last: Exception | None = None
        for _ in range(retries + 1):
            if host:
                store.throttle(host)
            try:
                conn.clear_events(self.session_id, "Page.loadEventFired")
                res = self.call("Page.navigate", {"url": url}, timeout=timeout)
                if res.get("errorText"):
                    raise NavigationError(f"{url} 열기 실패: {res['errorText']}")
                if res.get("loaderId"):  # 같은 문서 안 이동(#해시)이면 load 이벤트가 없다
                    conn.wait_event("Page.loadEventFired", self.session_id, timeout=timeout)
                self.check_blocked()
                return self.status()
            except BlockedError:
                raise
            except CDPError as e:
                last = e
        shot = None
        with contextlib.suppress(CDPError):
            shot = self.screenshot(name=f"goto-fail-{host or 'page'}")
        raise NavigationError(f"{url} 열기 실패({retries + 1}회 시도): {last} (스크린샷: {shot})")

    def navigate_by_js(self, expression: str, timeout: float = 30.0) -> int | None:
        """폼 제출·페이지 이동 함수처럼 화면 전환을 일으키는 JS를 실행하고 새 페이지 load까지 기다린다."""
        host = urlsplit(self.url()).hostname
        if host:
            store.throttle(host)
        conn = self.browser.conn
        conn.clear_events(self.session_id, "Page.loadEventFired")
        self.evaluate(expression, timeout=timeout)
        conn.wait_event("Page.loadEventFired", self.session_id, timeout=timeout)
        self.check_blocked()
        return self.status()

    def status(self) -> int | None:
        with contextlib.suppress(CDPError):
            value = self.evaluate(
                "(performance.getEntriesByType('navigation')[0] || {}).responseStatus || null")
            return int(value) if value else None
        return None

    def evaluate(self, expression: str, frame_id: str | None = None, timeout: float = 30.0):
        """JS 식을 평가해 JSON으로 바꿀 수 있는 값을 돌려준다. frame_id를 주면 그 iframe 안에서 평가한다."""
        params: dict = {"expression": expression, "returnByValue": True, "awaitPromise": True}
        if frame_id:
            params["contextId"] = self._frame_context(frame_id)
        res = self.call("Runtime.evaluate", params, timeout=timeout)
        if "exceptionDetails" in res:
            d = res["exceptionDetails"]
            raise JSError((d.get("exception") or {}).get("description") or d.get("text", "JS 오류"))
        return res.get("result", {}).get("value")

    def _frame_context(self, frame_id: str) -> int:
        # 격리 월드는 DOM은 공유하고 페이지 JS 전역은 공유하지 않는다. DOM 추출에는 이걸로 충분하다.
        res = self.call("Page.createIsolatedWorld", {"frameId": frame_id, "worldName": "regsearch"})
        return res["executionContextId"]

    def frames(self) -> list[dict]:
        """프레임 목록(id, parent_id, name, url). 첫 항목이 최상위 프레임이다.

        교차 출처 iframe(별도 프로세스)은 목록에는 나와도 evaluate가 실패할 수 있다.
        """
        out: list[dict] = []

        def walk(node: dict, parent: str | None) -> None:
            f = node["frame"]
            out.append({"id": f["id"], "parent_id": parent, "name": f.get("name", ""), "url": f.get("url", "")})
            for child in node.get("childFrames", []):
                walk(child, f["id"])

        walk(self.call("Page.getFrameTree")["frameTree"], None)
        return out

    def wait_for(self, expression: str, timeout: float = 15.0, interval: float = 0.25, frame_id: str | None = None):
        """JS 식이 참이 될 때까지 기다리고 그 값을 돌려준다."""
        deadline = time.monotonic() + timeout
        while True:
            with contextlib.suppress(JSError):
                value = self.evaluate(expression, frame_id=frame_id)
                # JS 기준 참 거짓으로 본다. DOM 요소는 값으로 넘어오면 {}가 되지만 참이다.
                if value is not None and value is not False and value != "" and not (
                        isinstance(value, (int, float)) and value == 0):
                    return value
            if time.monotonic() > deadline:
                shot = self.screenshot(name="wait-timeout")
                raise CDPTimeout(f"조건을 기다리다 시간 초과({timeout}s): {expression[:80]} (스크린샷: {shot})")
            time.sleep(interval)

    def click(self, selector: str, frame_id: str | None = None) -> None:
        self.evaluate(
            f"(() => {{ const el = document.querySelector({json.dumps(selector)});"
            f" if (!el) throw new Error('요소 없음: ' + {json.dumps(selector)});"
            f" el.scrollIntoView({{block: 'center'}}); el.click(); return true; }})()",
            frame_id=frame_id,
        )

    def title(self) -> str:
        return self.evaluate("document.title") or ""

    def url(self) -> str:
        return self.evaluate("location.href") or ""

    def html(self, frame_id: str | None = None) -> str:
        return self.evaluate("document.documentElement.outerHTML", frame_id=frame_id) or ""

    def text(self, frame_id: str | None = None) -> str:
        return self.evaluate("document.body ? document.body.innerText : ''", frame_id=frame_id) or ""

    def screenshot(self, path: str | os.PathLike | None = None, name: str = "page", full_page: bool = False) -> Path:
        res = self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": full_page})
        out = Path(path) if path else _screenshot_path(name)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(base64.b64decode(res["data"]))
        return out

    def check_blocked(self) -> None:
        title = self.title().lower()
        body = self.text()
        haystack = title + ("\n" + body.lower() if len(body) <= BLOCK_BODY_MAX_CHARS else "")
        for pat in BLOCK_PATTERNS:
            if pat in haystack:
                shot = self.screenshot(name="blocked")
                raise BlockedError(f"차단·캡차 화면으로 보여 중단합니다(감지 문구: '{pat}'). 사람이 확인해 주세요.", shot)
