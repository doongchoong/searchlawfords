"""cdp.py 오프라인 스모크 테스트. 실제 Chrome을 띄우지만 외부 사이트에는 접속하지 않는다.

    python -m unittest discover -s tests
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cdp  # noqa: E402

PAGE = """<title>스모크</title>
<p id="msg">본문</p>
<button id="btn" onclick="document.getElementById('msg').textContent='눌림'">버튼</button>
<iframe name="inner" srcdoc="<p id='in'>프레임 안 조문</p>"></iframe>"""


def data_url(html: str) -> str:
    return "data:text/html;charset=utf-8," + quote(html)


class CDPSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        os.environ["REGSEARCH_DATA_DIR"] = cls._tmp.name
        cls.browser = cdp.Browser.launch(headless=True)
        cls.page = cls.browser.new_page()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._tmp.cleanup()

    def test_navigate_evaluate_click(self):
        self.page.goto(data_url(PAGE))
        self.assertEqual(self.page.title(), "스모크")
        self.page.click("#btn")
        self.assertEqual(self.page.evaluate("document.getElementById('msg').textContent"), "눌림")

    def test_iframe_evaluate(self):
        self.page.goto(data_url(PAGE))
        frame_id = next(f["id"] for f in self.page.frames() if f["name"] == "inner")
        self.page.wait_for("document.getElementById('in')", frame_id=frame_id)
        self.assertEqual(self.page.evaluate("document.getElementById('in').textContent", frame_id=frame_id),
                         "프레임 안 조문")

    def test_js_error_is_raised(self):
        with self.assertRaises(cdp.JSError):
            self.page.evaluate("undefinedFunction()")

    def test_screenshot_is_png(self):
        self.page.goto(data_url(PAGE))
        shot = self.page.screenshot(name="smoke")
        self.assertTrue(shot.read_bytes().startswith(b"\x89PNG"))

    def test_large_message_roundtrip(self):
        # 64KB를 넘는 응답(127 길이 프레임)과 송신 마스킹 확인
        value = self.page.evaluate("'가'.repeat(200000)")
        self.assertEqual(len(value), 200000)
        echoed = self.page.evaluate("'" + "a" * 100000 + "'.length")
        self.assertEqual(echoed, 100000)

    def test_blocked_page_raises(self):
        with self.assertRaises(cdp.BlockedError) as ctx:
            self.page.goto(data_url("<title>보안문자 입력</title><p>자동입력 방지</p>"))
        self.assertTrue(ctx.exception.screenshot.exists())

    def test_long_regulation_text_is_not_blocked(self):
        # 규정 본문에 차단 문구가 들어 있어도 긴 페이지면 차단으로 보지 않는다
        body = "<p>비정상적인 접근을 차단하기 위한 조치</p>" + "<p>조문 본문</p>" * 1000
        self.page.goto(data_url("<title>전자금융 규정</title>" + body))
        self.assertEqual(self.page.title(), "전자금융 규정")


class CDPCleanupTest(unittest.TestCase):
    def test_close_removes_process_and_profile(self):
        browser = cdp.Browser.launch(headless=True)
        pid, profile = browser.proc.pid, browser.profile
        self.assertTrue(profile.exists())
        browser.close()
        self.assertFalse(cdp.pid_alive(pid))
        self.assertFalse(profile.exists())

    def test_stale_profile_without_live_owner_is_removed(self):
        stale = Path(tempfile.mkdtemp(prefix=cdp.PROFILE_PREFIX))
        (stale / cdp.OWNER_FILE).write_text('{"chrome_pid": 999999}', encoding="utf-8")
        cdp.cleanup_stale_profiles()
        self.assertFalse(stale.exists())


if __name__ == "__main__":
    unittest.main()
