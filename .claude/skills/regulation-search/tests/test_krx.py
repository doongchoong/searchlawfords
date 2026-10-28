"""KRX 어댑터 테스트.

- KrxParseTest: 실제 화면 구조를 줄인 HTML로 본문 파싱을 오프라인 확인(셀렉터 회귀용)
- KrxLiveTest: 실제 사이트 스모크 테스트. REGSEARCH_LIVE=1일 때만 돈다(요청 약 30회, 2초 간격이라 1~2분).
  실패하면 화면이 바뀐 것이니 references/selectors.krx.json부터 확인한다.

    python -m unittest discover -s tests                # 오프라인만
    REGSEARCH_LIVE=1 python -m unittest discover -s tests -p test_krx.py  # 실제 사이트 포함
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
from adapters.base import Regulation, find_pair, js_call  # noqa: E402
from adapters.krx import VIEW_JS, KrxAdapter  # noqa: E402

LIVE = os.environ.get("REGSEARCH_LIVE") == "1"

# regulationViewPop.do 구조를 줄인 것(2026-10-27 기준)
VIEW_FIXTURE = """<title>KRX 법무포털</title>
<form name="revi"><p class="title">테스트규정</p>
<p class="jang">[일부개정 2026. 1. 29 규정 제2392호 &lt;시행일 : 2026. 1. 29&gt;]</p></form>
<div class="layerViewW"><div class="lv_contents">
  <div onclick=" goRegulationView('210162282','N')"><div><b>테스트규정</b></div><div>제17차 일부개정 2026. 01. 29. 규정 제2392호</div></div>
  <div onclick=" goRegulationView('210138546','N')"><div><b>테스트규정</b></div><div>제16차 일부개정 2025. 10. 28. 규정 제2371호</div></div>
</div></div>
<div class="innerbody" id="innerbody">
<p class="jang"><a name="1"> 제1장&nbsp;총칙</a></p>
<div id="205962721"><img class="histIcon"><div class="Mjo" id="jo"><a name="bon1"><strong>제1조(목적)</strong></a>이 규정은 목적을 정한다. <span class="jotag2"><a class="jotag2">&lt;개정 2015. 7. 8.&gt;</a></span><br></div><div class="jotag">[전문개정 2009. 1. 28.]</div><br></div>
<p class="jang"><a name="2"> 제2장&nbsp;시장</a></p>
<p class="jeol"><a name="3"> 제1절&nbsp;의무</a></p>
<div id="205962541"><div class="Mjo" id="jo"><a name="bon3"><strong>제3조(회원의 금지행위)</strong></a>다음 행위를 하여서는 아니된다.<br><div id="hang">① 첫째 항 <br></div><div id="ho">1. 첫째 호 <br></div></div><br></div>
<div id="205962603"><div class="Mjo" id="jo"><a name="bon5bu2"><strong>제5조의2(투자주의종목)</strong></a>본문<br></div></div>
<p class="buchik_title"><a name="9">부칙 &lt;제14호, 2005. 1. 21.&gt; </a></p>
<div id="jo">① (시행일) 이 규정은 설립일부터 시행한다. <br><div id="hang">② (경과조치) 종전 규정에 따른다. <br></div></div>
<p class="buchik_title"><a name="10">부칙 &lt;제2392호, 2026. 1. 29.&gt; </a></p>
<div id="jo">이 규정은 2026년 1월 29일부터 시행한다. <br></div>
</div>"""


class KrxParseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        os.environ["REGSEARCH_DATA_DIR"] = cls._tmp.name
        cls.browser = cdp.Browser.launch()
        cls.adapter = KrxAdapter(browser=cls.browser)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._tmp.cleanup()

    def test_view_structure(self):
        page = self.adapter.page
        page.goto("data:text/html;charset=utf-8," + quote(VIEW_FIXTURE))
        data = page.evaluate(js_call(VIEW_JS, self.adapter.sel["view"]))
        self.assertEqual(data["name"], "테스트규정")
        self.assertIn("규정 제2392호", data["header"])
        self.assertEqual([h["bookid"] for h in data["history"]], ["210162282", "210138546"])
        hist = KrxAdapter._history(data["history"])
        self.assertEqual(hist[0], {"revision_id": "210162282", "revision_date": "2026-01-29",
                                   "label": "제17차 일부개정 2026. 01. 29. 규정 제2392호"})
        arts = data["articles"]
        self.assertEqual([a["heading"] for a in arts], ["제1조(목적)", "제3조(회원의 금지행위)", "제5조의2(투자주의종목)", "", ""])
        self.assertEqual(arts[0]["lines"], ["이 규정은 목적을 정한다. <개정 2015. 7. 8.>", "[전문개정 2009. 1. 28.]"])
        self.assertEqual(arts[0]["path"], ["제1장 총칙"])
        self.assertEqual(arts[1]["path"], ["제2장 시장", "제1절 의무"])
        self.assertEqual(arts[1]["lines"], ["다음 행위를 하여서는 아니된다.", "① 첫째 항", "1. 첫째 호"])
        self.assertEqual(arts[1]["content_id"], "205962541")
        self.assertIsNone(arts[1]["addenda"])
        self.assertEqual(arts[3]["addenda"], "부칙 <제14호, 2005. 1. 21.>")
        self.assertEqual(arts[3]["lines"], ["① (시행일) 이 규정은 설립일부터 시행한다.", "② (경과조치) 종전 규정에 따른다."])
        self.assertEqual(arts[4]["addenda"], "부칙 <제2392호, 2026. 1. 29.>")


@unittest.skipUnless(LIVE, "REGSEARCH_LIVE=1일 때만 실제 사이트에 접속")
class KrxLiveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = KrxAdapter()
        cls.catalog = cls.site.crawl_catalog()

    @classmethod
    def tearDownClass(cls):
        cls.site.close()

    def reg(self, name: str) -> Regulation:
        return next(r for r in self.catalog if r.name == name)

    def test_catalog(self):
        self.assertGreater(len(self.catalog), 70)
        cats = {r.category[-1] for r in self.catalog if r.category}
        self.assertTrue({"유가증권시장규정", "코스닥시장규정", "파생상품시장규정", "회원관리규정"} <= cats, cats)
        self.assertTrue(all(r.revision_id and r.revision_date for r in self.catalog))
        self.assertEqual(len({r.reg_id for r in self.catalog}), len(self.catalog))

    def test_open_pair(self):
        reg = self.reg("시장감시규정")
        self.assertEqual(find_pair(reg, self.catalog).name, "시장감시규정 시행세칙")
        texts = self.site.open_pair(reg, self.catalog)
        self.assertEqual([t.regulation.name for t in texts], ["시장감시규정", "시장감시규정 시행세칙"])
        main = texts[0]
        self.assertGreater(len([a for a in main.articles if a.addenda is None]), 20)
        self.assertEqual(main.find("3").title, "회원의 금지행위")
        self.assertTrue(main.find("3").path)
        history = main.regulation.extra["history"]
        self.assertEqual(history[0]["revision_id"], reg.revision_id)  # 연혁 첫 항목이 현행이어야 한다

    def test_current_revision_date(self):
        reg = self.reg("회원관리규정")
        info = self.site.current_revision_date(reg)
        self.assertEqual(info.revision_id, reg.revision_id)
        self.assertEqual(info.revision_date, reg.revision_date)

    def test_recent_changes(self):
        changes = self.site.recent_changes(days=180)
        kinds = {c.kind for c in changes}
        self.assertEqual(kinds, {"제개정", "예고"})
        self.assertTrue(all(c.date for c in changes))

    def test_search(self):
        hits = self.site.search("위탁증거금")
        self.assertTrue(hits)
        self.assertTrue(all(h.revision_id for h in hits))
        self.assertIn("파생상품시장 업무규정", {h.name for h in hits})


if __name__ == "__main__":
    unittest.main()
