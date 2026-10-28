"""KOFIA 어댑터 테스트.

- KofiaParseTest: 실제 화면 구조를 줄인 HTML로 본문 파싱을 오프라인 확인(셀렉터 회귀용)
- KofiaLiveTest: 실제 사이트 스모크 테스트. REGSEARCH_LIVE=1일 때만 돈다(요청 약 10회).
  실패하면 화면이 바뀐 것이니 references/selectors.kofia.json부터 확인한다.

    python -m unittest discover -s tests                  # 오프라인만
    REGSEARCH_LIVE=1 python -m unittest discover -s tests -p test_kofia.py  # 실제 사이트 포함
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
from adapters.base import Regulation  # noqa: E402
from adapters.kofia import CONTENT_JS, KofiaAdapter, _call  # noqa: E402

LIVE = os.environ.get("REGSEARCH_LIVE") == "1"

# lawFullScreenContent.do 구조를 줄인 것(2026-10-27 기준)
CONTENT_FIXTURE = """<title>규정내용</title><div id="lawcontent">
<div class="lawname" align="center"><center>테스트 규정</center></div>
<div class="history">제정 2008. 12. 30</div>
<div class="history">개정 2026. 10. 15</div>
<div class="PT"><div class="part">제1편 총칙</div></div>
<a name="000001"></a><div class="JO" id="317126">
  <div class="article"><table><tr><td>&nbsp;제1-1조(목적)</td><td><a href="#"><img alt="조항 인쇄"></a></td></tr></table></div>
  <div class="none"><span>이 규정은 목적을 정한다.</span></div>
</div>
<div class="PT"><div class="part">제2편 영업행위</div></div>
<div class="CT"><div class="chapter">제1장 투자권유 등</div></div>
<div class="CT"><div class="chapter"></div></div>
<div class="CT"><div class="none"><span>&lt;본장제목개정 2010.10.15&gt;</span></div></div>
<div class="ST"><div class="section">제1절 투자권유</div></div>
<div class="JO" id="317133">
  <div class="article"><table><tr><td>&nbsp;제2-1조(적합성(일반))</td></tr></table></div>
  <div class="hang"><table><tr><td>① </td><td><span>첫째 항</span></td></tr></table></div>
  <div class="ho"><table><tr><td>1. </td><td><span>첫째 호</span></td></tr></table></div>
  <div class="dann"><table><tr><td>가. </td><td><span>첫째 목</span></td></tr></table></div>
</div>
<div class="JO" id="317134"><div class="article"><table><tr><td>제2-2조 삭제 &lt;2010.1.1&gt;</td></tr></table></div></div>
<a name="000317"></a><div class="addenda">부 칙 (2008. 12. 30)</div>
<div id="317508"><div class="article">제1조(시행일)</div><div class="none"><span>이 규정은 2009년 2월 4일부터 시행한다.</span></div></div>
<div class="addenda">부 칙 (2009. 2. 26)</div>
<div id="317509"><div class="addenda_none" level="1">이 규정은 2009년 3월 9일부터 시행한다.</div></div>
</div>"""


# 표준투자권유준칙(모범규준) 구조를 줄인 것: "8. 제목" 번호, 조문 안의 .partition, 끝의 별표/서식 목록
GUIDELINE_FIXTURE = """<title>규정내용</title><div id="lawcontent">
<div class="lawname"><center>표준투자권유준칙</center></div>
<div class="PT"><div class="part">Ⅰ. 총 칙</div></div>
<div class="JO" id="305481"><div class="article"><table><tr><td>&nbsp;1. 목 적</td></tr></table></div>
  <div class="none"><span>이 준칙은 목적을 정한다.</span></div></div>
<div class="CT"><div class="chapter">Ⅳ-1. 투자자정보</div></div>
<div class="JO" id="305492"><div class="article"><table><tr><td><a><img alt="연혁"></a></td><td>&nbsp;8. 투자자정보 파악 및 투자자성향 분석 등</td></tr></table></div>
  <div class="hang"><table><tr><td>1) </td><td><span>투자권유 전에 투자자정보를 파악한다.</span></td></tr></table></div>
  <div class="none"><table><tr><td><span class="partition"> ※ 회사참고사항 8-1</span></td></tr><tr><td><span>▶ 투자자정보 확인서 양식</span></td></tr></table></div>
  <div class="hang"><table><tr><td>2) </td><td><span>파악한 정보는 유지·관리한다.</span></td></tr></table></div></div>
<a name="inclosure"><div class="article">[별표/서식 파일]</div>
<div class="article"><table><tr><td><a href="/download.do?gubun=102&amp;seq=27611"> 1. 0000_[참고 1].hwp </a></td></tr></table></div></a>
</div>"""


class KofiaParseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        os.environ["REGSEARCH_DATA_DIR"] = cls._tmp.name
        cls.browser = cdp.Browser.launch()
        cls.adapter = KofiaAdapter(browser=cls.browser)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._tmp.cleanup()

    def test_content_structure(self):
        page = self.adapter.page
        page.goto("data:text/html;charset=utf-8," + quote(CONTENT_FIXTURE))
        data = page.evaluate(_call(CONTENT_JS, self.adapter.sel["content"]))
        self.assertEqual(data["name"], "테스트 규정")
        self.assertEqual(data["history"][-1], "개정 2026. 10. 15")
        arts = data["articles"]
        self.assertEqual([a["heading"] for a in arts],
                         ["제1-1조(목적)", "제2-1조(적합성(일반))", "제2-2조 삭제 <2010.1.1>", "제1조(시행일)", ""])
        self.assertEqual(arts[0]["path"], ["제1편 총칙"])
        self.assertEqual(arts[1]["path"], ["제2편 영업행위", "제1장 투자권유 등", "제1절 투자권유"])
        self.assertEqual(arts[1]["lines"], ["① 첫째 항", "1. 첫째 호", "가. 첫째 목"])
        self.assertEqual(arts[1]["content_seq"], "317133")
        self.assertIsNone(arts[0]["addenda"])
        self.assertEqual(arts[3]["addenda"], "부 칙 (2008. 12. 30)")
        self.assertEqual(arts[3]["path"], [])
        self.assertEqual(arts[4]["lines"], ["이 규정은 2009년 3월 9일부터 시행한다."])

    def test_guideline_structure(self):
        page = self.adapter.page
        page.goto("data:text/html;charset=utf-8," + quote(GUIDELINE_FIXTURE))
        data = page.evaluate(_call(CONTENT_JS, self.adapter.sel["content"]))
        arts = data["articles"]
        # 별표/서식 목록은 조문이 아니다
        self.assertEqual([a["heading"] for a in arts], ["1. 목 적", "8. 투자자정보 파악 및 투자자성향 분석 등"])
        self.assertEqual(arts[1]["path"], ["Ⅰ. 총 칙", "Ⅳ-1. 투자자정보"])
        # 조문 안의 .partition("※ 회사참고사항")을 제목으로 보고 뒤 본문을 버리면 안 된다
        self.assertEqual(len(arts[1]["lines"]), 3)
        self.assertIn("※ 회사참고사항 8-1", arts[1]["lines"][1])
        self.assertEqual(arts[1]["lines"][2], "2) 파악한 정보는 유지·관리한다.")
        self.assertEqual(data["attachments"][0]["name"], "1. 0000_[참고 1].hwp")


@unittest.skipUnless(LIVE, "REGSEARCH_LIVE=1일 때만 실제 사이트에 접속")
class KofiaLiveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = KofiaAdapter()
        cls.catalog = cls.site.crawl_catalog(with_dates=False)

    @classmethod
    def tearDownClass(cls):
        cls.site.close()

    def reg(self, name: str) -> Regulation:
        return next(r for r in self.catalog if r.name == name)

    def test_catalog(self):
        self.assertGreater(len(self.catalog), 100)
        cats = {r.category[0] for r in self.catalog if r.category}
        self.assertTrue({"협회규정", "모범규준", "표준약관"} <= cats, cats)
        self.assertTrue(all(r.revision_id for r in self.catalog))

    def test_open_regulation_and_article(self):
        text = self.site.open_regulation(self.reg("금융투자회사의 영업 및 업무에 관한 규정"))
        self.assertGreater(len([a for a in text.articles if a.addenda is None]), 100)
        art = text.find("2-1")
        self.assertEqual(art.title, "목적")
        self.assertIn("detailArticlePrint.do", art.url)
        self.assertTrue(art.path)
        self.assertIsNotNone(text.regulation.revision_date)

    def test_current_revision_date(self):
        reg = self.reg("매매거래계좌설정약관")
        info = self.site.current_revision_date(reg)
        self.assertEqual(info.revision_id, reg.revision_id)  # 트리와 연혁 목록의 현재 개정판이 같아야 한다
        self.assertRegex(info.revision_date, r"^\d{4}-\d{2}-\d{2}$")
        self.assertGreater(len(info.history), 1)

    def test_recent_changes(self):
        changes = self.site.recent_changes(days=365)
        kinds = {c.kind for c in changes}
        self.assertIn("제개정", kinds)
        self.assertTrue(all(c.date for c in changes if c.kind == "제개정"))

    def test_search(self):
        hits = self.site.search("투자권유")
        self.assertTrue(hits)
        self.assertTrue(any(h.article_heading.startswith("제") for h in hits))
        self.assertTrue(all(h.reg_id for h in hits))


if __name__ == "__main__":
    unittest.main()
