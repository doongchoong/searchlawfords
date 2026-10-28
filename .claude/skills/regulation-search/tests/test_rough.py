"""규칙 기반 러프 파싱 오프라인 테스트."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cache  # noqa: E402
from adapters.base import Article, Regulation, RegulationText  # noqa: E402
from adapters.rough import ROUGH_VERSION, refine_text  # noqa: E402


def reg():
    return Regulation(site="KOFIA", reg_id="294", name="특정금전신탁 업무처리 모범규준", category=["모범규준"],
                      revision_id="1", revision_date="2026-01-01", url="https://law.kofia.or.kr/x")


def text(*articles):
    return RegulationText(reg(), list(articles))


def blob(body, **kw):
    return Article(number="", title="", text=body.strip("\n"), **kw)


GUIDELINE = """
Ⅰ. 총 칙
1. 목적
이 규준은 신탁업자가 준수할 사항을 정함을 목적으로 한다.
2. 정의
이 규준에서 사용하는 용어의 정의는 다음과 같다.
1. 국가
2. 한국은행
3. 그 밖에 금융위원회가 정하는 자
3. 적용범위
이 규준은 신탁업자의 임직원에게 적용한다.
Ⅱ. 투자권유에 관한 사항
Ⅱ-1. 일반원칙
4. 적정성의 원칙
신탁업자의 임직원은 일반투자자의 재산상황을 파악하여야 한다.
"""


class RoughTest(unittest.TestCase):
    def test_guideline_blob(self):
        r = refine_text(text(blob(GUIDELINE)))
        heads = [a.heading for a in r.articles]
        self.assertEqual(heads, ["1. 목적", "2. 정의", "3. 적용범위", "4. 적정성의 원칙"])
        self.assertIn("1. 국가\n2. 한국은행", r.articles[1].text)  # 조문 안 목록은 조문으로 보지 않는다
        self.assertEqual(r.articles[0].path, ["Ⅰ. 총 칙"])
        self.assertEqual(r.articles[3].path, ["Ⅱ. 투자권유에 관한 사항", "Ⅱ-1. 일반원칙"])
        self.assertTrue(all(a.extra["parse"] == "rough" for a in r.articles))
        self.assertEqual(r.regulation.extra["rough"], ROUGH_VERSION)
        self.assertEqual(r.find("4").title, "적정성의 원칙")

    def test_law_style_blob(self):
        body = """
제1조(목적) 이 법은 금융소비자를 보호함을 목적으로 한다.
제2조(정의) 이 법에서 사용하는 용어의 뜻은 다음과 같다.
1. 국가
2. 지방자치단체
제2조의2(적용 배제) 다음 각 호에는 적용하지 않는다.
"""
        r = refine_text(text(blob(body)))
        self.assertEqual([a.number for a in r.articles], ["제1조", "제2조", "제2조의2"])
        self.assertEqual(r.articles[1].title, "정의")
        self.assertIn("1. 국가", r.articles[1].text)  # 법령식이면 "N."은 호다

    def test_annex_inside_addenda(self):
        r = refine_text(text(
            Article("1.", "목적", "…"),
            Article("", "", "이 준칙은 2026년 1월 1일부터 시행한다.", addenda="부 칙 (2025. 12. 1)"),
            Article("", "", "[참고1] 점검 체크리스트\n1) 상품 위험도 확인", addenda="부 칙 (2025. 12. 1)"),
            Article("", "", "2) 판매 후 점검", addenda="부 칙 (2025. 12. 1)"),
            Article("", "", "이 준칙은 2027년 1월 1일부터 시행한다.", addenda="부 칙 (2026. 6. 1)"),
        ))
        annex = next(a for a in r.articles if a.number == "[참고1]")
        self.assertIsNone(annex.addenda)
        self.assertEqual(annex.heading, "[참고1] 점검 체크리스트")
        self.assertIn("2) 판매 후 점검", annex.text)  # 같은 부칙 표시로 이어진 덩어리는 첨부에 붙는다
        later = [a for a in r.articles if a.addenda == "부 칙 (2026. 6. 1)"]
        self.assertEqual(len(later), 1)  # 다른 부칙은 첨부에 섞이지 않는다
        self.assertEqual(len([a for a in r.articles if a.addenda == "부 칙 (2025. 12. 1)"]), 1)

    def test_block_fallback(self):
        para = ("이 설명서는 신용거래의 위험을 설명합니다. " * 20).strip()
        r = refine_text(text(blob("\n".join([para] * 4))))
        self.assertGreaterEqual(len(r.articles), 2)
        self.assertEqual(r.articles[0].number, "블록1")
        self.assertTrue(all(a.extra["parse"] == "block" for a in r.articles))
        self.assertEqual("".join(a.text.replace("\n", "") for a in r.articles), para * 4)

    def test_site_articles_untouched_and_idempotent(self):
        site = Article("제6조", "임의매매의 금지", "…", path=["제2장"])
        once = refine_text(text(site, blob(GUIDELINE)))
        self.assertEqual(once.articles[0].to_dict(), site.to_dict())
        twice = refine_text(once)
        self.assertEqual([a.to_dict() for a in twice.articles], [a.to_dict() for a in once.articles])

    def test_sentence_lines_are_not_headings(self):
        body = "1. 목적\n이 규준의 목적을 정한다.\n2. 신탁업자는 고객에게 설명하여야 한다.\n3. 적용범위\n모든 임직원"
        r = refine_text(text(blob(body)))
        self.assertEqual([a.heading for a in r.articles], ["1. 목적"])  # 2.는 문장이라 제목이 아니고, 3은 2를 건너뜀
        self.assertIn("3. 적용범위", r.articles[0].text)

    def test_chapter_text_without_number_stays_in_place(self):
        body = """
1. 목적
이 준칙의 목적을 정한다.
2. 일반원칙
제조회사는 투자자 이익을 우선한다.
Ⅳ. 제조 및 판매에 관한 금지행위
임직원은 다음의 어느 하나에 해당하는 행위를 하여서는 아니 된다.
1) 목표시장을 설정하지 않고 제조하는 행위
2) 시나리오 분석을 하지 않는 행위
Ⅴ. 부칙
1) 이 준칙은 2020년 7월 19일부터 시행한다.
다만, 6. 목표시장 및 판매전략 설정, 7. 금융투자상품 테스트는 2021년부터 시행한다.
"""
        r = refine_text(text(blob(body)))
        self.assertEqual([a.heading for a in r.articles if a.addenda is None],
                         ["1. 목적", "2. 일반원칙", "블록1(Ⅳ. 제조 및 판매에 관한 금지행위)"])
        ban = r.articles[2]
        self.assertEqual(ban.path, ["Ⅳ. 제조 및 판매에 관한 금지행위"])
        self.assertNotIn("시행한다", ban.text)  # 장 아래 블록이 부칙을 넘지 않는다
        [add] = [a for a in r.articles if a.addenda is not None]
        self.assertEqual((add.addenda, add.path, add.number), ("Ⅴ. 부칙", [], ""))
        self.assertIn("다만, 6. 목표시장", add.text)  # 부칙 단서가 잘리지 않는다

    def test_blocks_rank_below_articles(self):
        from match import load_json, normalize_query, search_articles
        words = "목표시장 테스트 " * 3
        r = text(Article("7.", "금융투자상품 테스트", "제조회사는 목표시장 시나리오 분석을 하여야 한다."),
                 Article("블록1", "참고", words, extra={"parse": "block"}))
        cfg = load_json("match.json")["articles"] | {"top": 5, "min_score": 0.1}
        hits = search_articles(r, normalize_query("목표시장 테스트"), cfg)
        self.assertEqual(hits[0]["number"], "7.")
        self.assertEqual(hits[1].get("parse"), "block")

    def test_cache_refines_old_entries_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["REGSEARCH_DATA_DIR"] = tmp
            try:
                cache.put(text(blob(GUIDELINE)))  # 러프 파싱 전에 저장된 캐시라고 본다
                got = cache.get("KOFIA", "294")
                self.assertEqual(len(got.articles), 4)
                self.assertEqual([len(t.articles) for t in cache.iter_all()], [4])
            finally:
                del os.environ["REGSEARCH_DATA_DIR"]


if __name__ == "__main__":
    unittest.main()
