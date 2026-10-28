"""법제처 어댑터와 조회 캐시 테스트.

- CacheTest: open_cached가 개정판이 같으면 캐시를, 다르면 새로 받는지(가짜 어댑터, 오프라인)
- LawgoParseTest: 실제 화면 구조를 줄인 HTML로 본문·검색결과 파싱을 오프라인 확인(셀렉터 회귀용)
- LawgoLiveTest: 실제 사이트 스모크 테스트. REGSEARCH_LIVE=1일 때만 돈다(자본시장법 본문 약 3MB 포함).

    python -m unittest discover -s tests                                  # 오프라인만
    REGSEARCH_LIVE=1 python -m unittest discover -s tests -p test_lawgo.py  # 실제 사이트 포함
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cache  # noqa: E402
import cdp  # noqa: E402
from adapters.base import Adapter, Article, Regulation, RegulationText, RevisionInfo, js_call  # noqa: E402
from adapters.lawgo import CONTENT_JS, SEARCH_JS, LawgoAdapter, load_scope  # noqa: E402

LIVE = os.environ.get("REGSEARCH_LIVE") == "1"


class FakeAdapter(Adapter):
    site = "테스트"
    selectors_key = "kofia"  # 셀렉터는 쓰지 않는다

    def __init__(self):
        super().__init__(browser=None)
        self.current = "r1"
        self.opened = 0

    def crawl_catalog(self, previous=None, with_dates=True):
        return []

    def open_regulation(self, reg):
        self.opened += 1
        return RegulationText(Regulation.from_dict(reg.to_dict()), [Article("제1조", "목적", f"판 {reg.revision_id}")])

    def current_revision_date(self, reg):
        return RevisionInfo(self.current, "2026-10-01")

    def search(self, query, scope=None):
        return []

    def recent_changes(self, days=30):
        return []


class CacheTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["REGSEARCH_DATA_DIR"] = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_open_cached_uses_cache_until_revision_changes(self):
        site = FakeAdapter()
        reg = Regulation(site="테스트", reg_id="법령/테스트법", name="테스트법", category=[])
        first = site.open_cached(reg)
        self.assertEqual((site.opened, first.find("1").text), (1, "판 r1"))
        self.assertTrue(cache.path("테스트", reg.reg_id).exists())

        again = site.open_cached(reg)  # 개정판 그대로 → 캐시
        self.assertEqual(site.opened, 1)
        self.assertTrue(again.regulation.extra["from_cache"])
        self.assertEqual(again.collected_at, first.collected_at)

        site.current = "r2"  # 사이트에서 개정됨 → 다시 받는다
        fresh = site.open_cached(reg)
        self.assertEqual((site.opened, fresh.find("1").text), (2, "판 r2"))
        self.assertNotIn("from_cache", fresh.regulation.extra)

    def test_parser_version_change_refetches(self):
        site = FakeAdapter()
        reg = Regulation(site="테스트", reg_id="법령/테스트법", name="테스트법", category=[])
        site.open_cached(reg)
        site.parser_version = 2  # 파싱 방식이 바뀌면 개정판이 같아도 다시 받는다
        again = site.open_cached(reg)
        self.assertEqual(site.opened, 2)
        self.assertEqual(again.regulation.extra["parser"], 2)
        self.assertTrue(site.open_cached(reg).regulation.extra["from_cache"])

    def test_scope_has_requested_laws(self):
        names = {l["name"] for laws in load_scope().values() for l in laws}
        self.assertIn("조세특례제한법", names)
        self.assertIn("금융투자업규정", names)


# lsInfoP.do 본문과 lsSc.do 조문내용 검색결과 구조를 줄인 것(2026-10-27 기준)
CONTENT_FIXTURE = """<title>법령</title><div><span class="tx2">[시행 2026. 10. 2.] [법률 제21857호, 2026. 8. 4., 타법개정]</span></div>
<div class="pgroup"><p class="gtit">제1편 총칙</p></div>
<a name="J1:0" id="J1:0"></a><div class="pgroup"><ul class="lawico01"></ul><div class="lawcon">
  <p class="pty1_p4"><input type="checkbox"><span class="bl"><label>제1조(목적)</label></span> 이 법은 목적을 정한다. <span class="sfon">&lt;개정 2013. 5. 28.&gt;</span></p>
</div></div>
<div class="pgroup"><p class="gtit">제2편 금융투자업</p></div>
<div class="pgroup"><p class="gtit">제1장 인가</p></div>
<a name="J9:2" id="J9:2"></a><div class="pgroup"><div class="lawcon">
  <p class="pty1_p4"><span class="bl"><label>제9조의2(용어의 정의)</label></span> ① 첫째 항</p>
  <p class="pty1_de2_1">② 둘째 항</p><p class="pty1_de2h">1. 첫째 호</p><p class="pty1_de3">가. 첫째 목</p><p></p>
</div></div>
<div id="arDivArea"><div class="pgroup"><p class="pty3"><span class="bl">부&nbsp;&nbsp;칙</span> <span class="sfon">&lt;법률 제8635호, 2007. 8. 3.&gt;</span></p>
<p class="pty3_dep1"><span class="bl">제1조 (시행일)</span>&nbsp;이 법은 공포 후 1년 6개월이 경과한 날부터 시행한다.</p>
<p class="pty3_dep2">1. 「증권거래법」</p></div></div>"""

SEARCH_FIXTURE = """<table><tr><th>번호</th><th>법령명</th><th>조문명</th></tr></table><ul>
<li class="left_li" id="liBgcolor2"><div><a onclick="fLsListView('liBgcolor2', '283193', '2', '20270204', '0');" title="자본시장과 금융투자업에 관한 법률 [시행 2027. 2. 4.] [법률 제21324호, 2026. 2. 3., 일부개정]">
  <img alt="앞으로 시행될 법령"><span class="tx">자본시장과 금융투자업에 관한 법률</span></a></div>
  <dl class="dep02"><dd><a onclick="fJoView('jo', 'liBgcolor2a', '99611353', '283193', '0393', '00', '자본시장과 금융투자업에 관한 법률', '2', '제393조', '0', '20270204', '1');return false;">3.&nbsp;제393조 업무규정</a></dd></dl></li>
<li id="liBgcolor9"><div><a onclick="fLsListView('liBgcolor9','2100000285020','1');" title="금융투자업규정&nbsp;[시행 2026. 9. 15.] [금융위원회고시 제2026-38호, 2026. 9. 9., 일부개정]"><span class="tx">금융투자업규정</span></a></div>
  <dl class="dep02"><dd><a onclick="fJoView('jo','liBgcolor9a','18357779','2100000285020','0002','0023','00','금융투자업규정','1');return false;">10.&nbsp;제2-23조 지점에 관한 내부통제</a></dd></dl></li>
</ul>"""


class LawgoParseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        os.environ["REGSEARCH_DATA_DIR"] = cls._tmp.name
        cls.browser = cdp.Browser.launch()
        cls.adapter = LawgoAdapter(browser=cls.browser)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._tmp.cleanup()

    def test_content_structure(self):
        page = self.adapter.page
        page.goto("data:text/html;charset=utf-8," + quote(CONTENT_FIXTURE))
        data = page.evaluate(js_call(CONTENT_JS, self.adapter.sel["content"]))
        self.assertEqual(data["header"], "[시행 2026. 10. 2.] [법률 제21857호, 2026. 8. 4., 타법개정]")
        arts = data["articles"]
        self.assertEqual([a["heading"] for a in arts], ["제1조(목적)", "제9조의2(용어의 정의)", ""])
        self.assertEqual(arts[0]["lines"], ["이 법은 목적을 정한다. <개정 2013. 5. 28.>"])
        self.assertEqual(arts[0]["path"], ["제1편 총칙"])
        self.assertEqual(arts[1]["path"], ["제2편 금융투자업", "제1장 인가"])
        self.assertEqual(arts[1]["lines"], ["① 첫째 항", "② 둘째 항", "1. 첫째 호", "가. 첫째 목"])
        self.assertEqual(arts[1]["anchor"], "J9:2")
        self.assertEqual(arts[2]["addenda"], "부 칙 <법률 제8635호, 2007. 8. 3.>")
        self.assertEqual(arts[2]["lines"], ["제1조 (시행일) 이 법은 공포 후 1년 6개월이 경과한 날부터 시행한다.", "1. 「증권거래법」"])

    def test_search_structure(self):
        page = self.adapter.page
        page.goto("data:text/html;charset=utf-8," + quote(SEARCH_FIXTURE))
        items = page.evaluate(js_call(SEARCH_JS, self.adapter.sel["search"]))
        self.assertEqual(len(items), 2)
        self.assertTrue(items[0]["upcoming"])
        self.assertFalse(items[1]["upcoming"])
        self.assertTrue(items[0]["header"].startswith("자본시장과 금융투자업에 관한 법률 [시행 2027. 2. 4.]"))
        self.assertEqual(items[1]["articles"][0]["text"], "10. 제2-23조 지점에 관한 내부통제")

    def test_article_url(self):
        reg = Regulation(site="법제처", reg_id="행정규칙/금융투자업규정", name="금융투자업규정", category=[], kind="행정규칙")
        self.assertEqual(self.adapter.article_url(reg, "제4-20조"), "https://www.law.go.kr/행정규칙/금융투자업규정/제4-20조")
        law = Regulation(site="법제처", reg_id="법령/x", name="자본시장과 금융투자업에 관한 법률", category=[], kind="법률")
        self.assertEqual(self.adapter.article_url(law, "제8조의2"),
                         "https://www.law.go.kr/법령/자본시장과금융투자업에관한법률/제8조의2")


@unittest.skipUnless(LIVE, "REGSEARCH_LIVE=1일 때만 실제 사이트에 접속")
class LawgoLiveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()  # 실사용 캐시를 건드리지 않게
        os.environ["REGSEARCH_DATA_DIR"] = cls._tmp.name
        cls.site = LawgoAdapter()
        cls.catalog = cls.site.crawl_catalog(with_dates=False)

    @classmethod
    def tearDownClass(cls):
        cls.site.close()
        cls._tmp.cleanup()

    def reg(self, name: str) -> Regulation:
        return next(r for r in self.catalog if r.name == name)

    def test_current_revision_for_every_scope_law(self):
        for reg in self.catalog:
            info = self.site.current_revision_date(reg)
            self.assertTrue(info.revision_id, reg.name)  # 화이트리스트 법령명이 법제처와 정확히 맞아야 한다
            if reg.kind != "행정규칙":
                self.assertRegex(info.revision_date, r"^\d{4}-\d{2}-\d{2}$", reg.name)

    def test_open_cached_admrul_and_article(self):
        text = self.site.open_cached(self.reg("금융투자업규정"))
        art = text.find("4-20")
        self.assertEqual(art.title, "불건전 영업행위의 금지")
        self.assertEqual(art.url, "https://www.law.go.kr/행정규칙/금융투자업규정/제4-20조")
        self.assertRegex(text.regulation.revision_date, r"^\d{4}-\d{2}-\d{2}$")
        self.assertTrue(self.site.open_cached(self.reg("금융투자업규정")).regulation.extra.get("from_cache"))

    def test_open_pair_tax_law_family(self):
        texts = self.site.open_pair(self.reg("조세특례제한법"), self.catalog)
        self.assertEqual([t.regulation.kind for t in texts], ["법률", "대통령령", "총리령·부령"])
        self.assertTrue(all(len(t.articles) > 10 for t in texts))

    def test_search_marks_scope_and_status(self):
        hits = self.site.search("위탁증거금", {"target": "law"})
        names = {h.name for h in hits}
        self.assertIn("자본시장과 금융투자업에 관한 법률", names)
        statuses = {h.extra["status"] for h in hits if h.name == "자본시장과 금융투자업에 관한 법률"}
        self.assertIn("현행", statuses)
        self.assertTrue(hits[0].extra["in_scope"])  # 화이트리스트 안이 앞으로
        self.assertTrue(any(h.article_heading.startswith("제396조") for h in hits))


if __name__ == "__main__":
    unittest.main()
