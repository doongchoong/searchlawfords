"""어댑터 공통 모델·도우미 오프라인 테스트."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from adapters.base import (  # noqa: E402
    KIND_RULE, Article, Regulation, RegulationText, find_pair, normalize_article_number, parse_date,
    split_article_heading,
)


class HelperTest(unittest.TestCase):
    def test_parse_date(self):
        self.assertEqual(parse_date("개정 2009.  1. 23"), "2009-01-23")
        self.assertEqual(parse_date("2026-10-15"), "2026-10-15")
        self.assertEqual(parse_date("<개정 2021.4.15.>"), "2021-04-15")
        self.assertIsNone(parse_date("날짜 없음"))

    def test_article_number(self):
        self.assertEqual(normalize_article_number("2-1"), "제2-1조")
        self.assertEqual(normalize_article_number("제 3 조의 2"), "제3조의2")
        self.assertEqual(normalize_article_number("제2-11조의2"), "제2-11조의2")

    def test_split_heading(self):
        self.assertEqual(split_article_heading("제1-1조(목적)"), ("제1-1조", "목적", ""))
        self.assertEqual(split_article_heading("제2-11조의2(방문판매등)"), ("제2-11조의2", "방문판매등", ""))
        self.assertEqual(split_article_heading("제4조(유가증권시장 주식워런트증권 등의 기본예탁금 납부)")[1],
                         "유가증권시장 주식워런트증권 등의 기본예탁금 납부")
        self.assertEqual(split_article_heading("제9조(위험고지(서면))"), ("제9조", "위험고지(서면)", ""))
        self.assertEqual(split_article_heading("제5조 삭제 <2010.1.1>"), ("제5조", "", "삭제 <2010.1.1>"))
        # 모범규준식 번호(표준투자권유준칙)
        self.assertEqual(split_article_heading("8. 투자자정보 파악 및 투자자성향 분석 등"),
                         ("8.", "투자자정보 파악 및 투자자성향 분석 등", ""))
        self.assertEqual(split_article_heading("1. 목 적"), ("1.", "목 적", ""))

    def test_find_numbered_guideline_article(self):
        text = RegulationText(Regulation(site="KOFIA", reg_id="149", name="표준투자권유준칙", category=[]), [
            Article("1.", "목 적", "…"), Article("8.", "투자자정보 파악 및 투자자성향 분석 등", "…")])
        for key in ("8", "8.", "제8조"):
            self.assertEqual(text.find(key).title, "투자자정보 파악 및 투자자성향 분석 등")
        self.assertEqual(text.articles[1].heading, "8. 투자자정보 파악 및 투자자성향 분석 등")

    def test_find_hyphen_and_ui_numbers_interchange(self):
        krx = RegulationText(Regulation(site="KRX", reg_id="1", name="세칙", category=[]), [
            Article("제56조", "저유동성", "…"), Article("제56조의4", "종류주식", "…")])
        for key in ("56-4", "56의4", "제56조의4"):
            self.assertEqual(krx.find(key).title, "종류주식")
        fsc = RegulationText(Regulation(site="법제처", reg_id="2", name="금융투자업규정", category=[]), [
            Article("제4-20조", "불건전 영업행위", "…"), Article("제4조의2", "다른 조문", "…")])
        self.assertEqual(fsc.find("4-20").title, "불건전 영업행위")
        self.assertEqual(fsc.find("4-2").title, "다른 조문")  # "제4-2조"가 없을 때만 "제4조의2"로
        self.assertEqual(fsc.find("4조의20").title, "불건전 영업행위")


class ModelTest(unittest.TestCase):
    def setUp(self):
        reg = Regulation(site="KOFIA", reg_id="1", name="테스트 규정", category=["협회규정"])
        self.text = RegulationText(reg, [
            Article("제1조", "목적", "목적 본문"),
            Article("제2조", "임의매매의 금지", "본문"),
            Article("제1조", "시행일", "부칙 본문", addenda="부 칙 (2008. 12. 30)"),
        ])

    def test_find_by_number_then_title(self):
        self.assertEqual(self.text.find("1").title, "목적")
        # 번호가 밀려 틀려도 제목으로 찾는다
        self.assertEqual(self.text.find("제7조", title="임의매매").number, "제2조")
        self.assertIsNone(self.text.find(title="시행일"))
        self.assertEqual(self.text.find(title="시행일", include_addenda=True).addenda, "부 칙 (2008. 12. 30)")

    def test_round_trip(self):
        again = RegulationText.from_dict(self.text.to_dict())
        self.assertEqual(again, self.text)

    def test_find_pair_ignores_spacing(self):
        main = Regulation(site="KOFIA", reg_id="322", name="적격기관투자자대상증권의 등록 및 관리 등에 관한 규정", category=[])
        rule = Regulation(site="KOFIA", reg_id="323", name="적격기관투자자대상증권의 등록 및 관리등에 관한 규정 시행세칙",
                          category=[], kind=KIND_RULE)
        other = Regulation(site="KOFIA", reg_id="9", name="다른 규정", category=[])
        self.assertEqual(find_pair(main, [main, rule, other]).reg_id, "323")
        self.assertEqual(find_pair(rule, [main, rule, other]).reg_id, "322")
        self.assertIsNone(find_pair(other, [main, rule, other]))


if __name__ == "__main__":
    unittest.main()
