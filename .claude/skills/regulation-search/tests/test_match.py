"""매칭·카탈로그·사례 읽기·평가 오프라인 테스트(사이트에 접속하지 않음)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import store  # noqa: E402
from adapters.base import Article, Regulation, RegulationText  # noqa: E402

ALIASES = {
    "regulations": {
        "자본시장법": ["자본시장과 금융투자업에 관한 법률"],
        "자본시장법 시행령": ["자본시장과 금융투자업에 관한 법률 시행령"],
        "금소법": ["금융소비자 보호에 관한 법률"],
    },
    "terms": {"ELS": ["파생결합증권"], "임의매매": ["임의매매", "주문을 받지 아니하고"]},
}


def reg(site, reg_id, name, category=(), kind="본규정"):
    return Regulation(site=site, reg_id=reg_id, name=name, category=list(category), kind=kind, revision_id="1",
                      revision_date="2026-10-01", url=f"https://example/{reg_id}")


REGS = [
    reg("KOFIA", "120", "매매거래계좌설정약관", ["표준약관"]),
    reg("KOFIA", "136", "금융투자회사의 영업 및 업무에 관한 규정", ["협회규정"]),
    reg("KOFIA", "137", "금융투자회사의 영업 및 업무에 관한 규정 시행세칙", ["협회규정"], "시행세칙"),
    reg("KRX", "4", "시장감시규정", ["업무규정", "시장감시규정"]),
    reg("법제처", "법령/자본시장과금융투자업에관한법률", "자본시장과 금융투자업에 관한 법률", ["자본시장 일반"], "법률"),
    reg("법제처", "법령/자본시장과금융투자업에관한법률시행령", "자본시장과 금융투자업에 관한 법률 시행령", ["자본시장 일반"], "대통령령"),
    reg("법제처", "법령/금융소비자보호에관한법률", "금융소비자 보호에 관한 법률", ["금융소비자보호"], "법률"),
]

# 흔한 말("고객")이 들어간 조문을 많이 넣어 IDF가 작동하게 한다
FILLER = [Article(f"제{i}조", f"일반 사항 {i}", "회사는 고객에게 통지한다.") for i in range(10, 40)]
TEXTS = [
    RegulationText(REGS[0], [
        Article("제6조", "임의매매의 금지", "회사는 고객 또는 그 대리인으로부터 주문을 받지 아니하고는 예탁받은 재산으로 매매를 하여서는 아니 된다.",
                url="https://example/120/6"),
        Article("제7조", "매매거래의 통지", "회사는 고객에게 매매거래 내용을 통지한다."),
    ] + FILLER),
    RegulationText(REGS[4], [
        Article("제9조", "그 밖의 용어의 정의", "④ 이 법에서 “투자권유”란 특정 투자자를 상대로 금융투자상품의 매매를 권유하는 것을 말한다."),
        Article("제50조", "투자권유준칙", "금융투자업자는 투자권유를 함에 있어서 임직원이 준수하여야 할 투자권유준칙을 정하여야 한다."),
        Article("제51조", "투자권유대행인의 등록", "투자권유대행인은 투자권유를 대행한다."),
    ] + FILLER),
]

CFG = None


def cfg():
    global CFG
    if CFG is None:
        from match import load_json
        CFG = load_json("match.json")
    return CFG


class TextTest(unittest.TestCase):
    def test_tokenize_strips_josa_and_endings(self):
        from match import tokenize
        toks = tokenize("고객 주문 없이 매매하면 투자권유를 어떻게 해야 하나요")
        for t in ("매매", "매매하면", "투자권유", "고객", "주문"):
            self.assertIn(t, toks)
        for t in ("어떻게", "해야", "하나요"):
            self.assertNotIn(t, toks)
        self.assertIn("고난도", tokenize("고난도 상품"))  # 짧은 말은 원형을 남긴다

    def test_copula_and_doeda_endings(self):
        # fork 세션 보고(2026-10-27): "관리종목인"의 "인", "제한돼"의 "돼"를 못 떼서 사례가 안 잡혔다
        from match import core_terms, tokenize
        toks = tokenize("관리종목인 종목은 어떤 호가 입력이 제한돼? 금지되나요")
        for t in ("관리종목", "제한", "금지", "입력", "호가"):
            self.assertIn(t, toks)
        self.assertEqual(core_terms(toks), ["관리종목", "종목", "호가", "입력", "제한", "금지"])
        self.assertIn("대리인", tokenize("대리인 확인"))  # "인"으로 끝나는 명사는 원형을 남긴다

    def test_aliases_longest_first_and_term_expansion(self):
        from match import normalize_query
        q = normalize_query("자본시장법 시행령에서 ELS 임의매매", ALIASES)
        self.assertEqual([a for a, _ in q.aliases], ["자본시장법 시행령"])
        self.assertIn("파생결합증권", q.terms)
        self.assertIn("주문을 받지 아니하고", q.terms)


class MatchTest(unittest.TestCase):
    def find(self, question, **kw):
        from match import find_candidates
        return find_candidates(question, regs=REGS, case_list=kw.pop("cases", []), texts=kw.pop("texts", TEXTS),
                               aliases=ALIASES, cfg=cfg(), **kw)

    def test_alias_puts_law_first_with_reason(self):
        r = self.find("금소법 적합성 원칙", texts=[])
        top = r.candidates[0]
        self.assertEqual(top.name, "금융소비자 보호에 관한 법률")
        self.assertIn("약칭 '금소법' → 정식명 일치", top.reasons)
        self.assertIn("금융소비자보호", [a["area"] for a in r.areas])

    def test_pair_expansion(self):
        r = self.find("영업 및 업무에 관한 규정", texts=[])
        names = [c.name for c in r.candidates]
        self.assertIn("금융투자회사의 영업 및 업무에 관한 규정 시행세칙", names)
        rule = next(c for c in r.candidates if c.name.endswith("시행세칙"))
        self.assertIn("pair", rule.sources)

    def test_cache_search_finds_article_by_body(self):
        r = self.find("고객 주문 없이 예탁재산으로 매매하면 안 되는 근거")
        kofia = next(c for c in r.candidates if c.name == "매매거래계좌설정약관")
        self.assertEqual(kofia.articles[0]["title"], "임의매매의 금지")
        self.assertEqual(kofia.text_from, "cache")

    def test_definition_question_prefers_definition_article(self):
        r = self.find("자본시장법상 투자권유의 정의")
        law = next(c for c in r.candidates if c.name == "자본시장과 금융투자업에 관한 법률")
        self.assertEqual(law.articles[0]["title"], "그 밖의 용어의 정의")

    def test_idf_lowers_common_words(self):
        from match import normalize_query, term_weights
        q = normalize_query("고객 임의매매", ALIASES)
        w = term_weights(TEXTS, q.terms)
        self.assertLess(w["고객"], w["임의매매"])

    def test_case_path_is_candidate_with_reason_and_preferred_article(self):
        case = {"id": "c-202610-001", "확정일": "2026-10-27", "주제": "고객 동의 없는 매매 금지 근거",
                "키워드": ["임의매매", "동의 없는 매매"], "업무영역": ["자본시장 일반"], "결과": "근거확인",
                "경로": [{"사이트": "KOFIA", "규정": "매매거래계좌설정약관", "조문제목": "임의매매의 금지"}]}
        r = self.find("임의매매 금지 근거", cases=[case])
        self.assertEqual(r.cases[0]["case"]["id"], "c-202610-001")
        kofia = next(c for c in r.candidates if c.name == "매매거래계좌설정약관")
        self.assertIn("case", kofia.sources)
        self.assertTrue(any("c-202610-001" in x for x in kofia.reasons))
        self.assertTrue(kofia.articles[0]["from_case"])

    def test_case_keywords_compared_word_by_word(self):
        # 사례 키워드가 "호가입력의 제한"처럼 붙어 있어도 다른 표현의 질문에서 사례를 찾아야 한다
        from match import load_json, similar_cases, normalize_query
        case = {"id": "c-202610-001", "주제": "주식 매매거래에서 중간가호가 입력이 제한되는 경우의 근거",
                "키워드": ["중간가호가", "호가입력의 제한", "호가의 효력 등"],
                "경로": [{"사이트": "KRX", "규정": "시장감시규정", "조문제목": "회원의 금지행위"}]}
        hits = similar_cases(normalize_query("관리종목인 종목에 대해 입력할 수 없는 호가 종류는?", ALIASES),
                             [case], load_json("match.json")["cases"])
        self.assertEqual([h["case"]["id"] for h in hits], ["c-202610-001"])
        self.assertIn("호가", hits[0]["reasons"][0])

    def test_hints_replace_noisy_auto_terms(self):
        # 질문 해석 힌트가 있으면 조문 검색어는 힌트 위주("만료되는" 같은 자동 추출 말은 빠진다)
        from match import Hints, normalize_query, search_terms
        h = Hints(terms=["투자자정보", "유효기간"], search=["투자자정보 유효기간"])
        q = normalize_query("투자자정보 만료되는 경우 신경써야 하는 점", ALIASES, h)
        self.assertNotIn("만료되는", q.terms)
        self.assertIn("유효기간", q.terms)
        self.assertEqual(search_terms(q, {}, 2), ["투자자정보 유효기간"])
        # 규정명 매칭에는 질문 낱말도 남는다
        q2 = normalize_query("유가증권시장 위탁증거금 징수기준", ALIASES, Hints(terms=["위탁증거금"]))
        self.assertIn("유가증권시장", q2.name_terms)

    def test_hint_regulation_becomes_candidate(self):
        from match import Hints
        r = self.find("이거 근거가 뭐야", texts=[], hints=Hints(regulations=["KOFIA:매매거래계좌설정약관", "자본시장법"]))
        top = {c.name: c for c in r.candidates}
        self.assertIn("hint", top["매매거래계좌설정약관"].sources)
        self.assertIn("hint", top["자본시장과 금융투자업에 관한 법률"].sources)  # 약칭도 풀린다
        r2 = self.find("이거 근거가 뭐야", texts=[], hints=Hints(regulations=["없는규정"]))
        self.assertTrue(any("없는규정" in n for n in r2.notes))

    def test_hints_round_trip_and_cli_parsing(self):
        import argparse
        from match import Hints, add_hint_args, hints_from_args
        ap = argparse.ArgumentParser()
        add_hint_args(ap)
        args = ap.parse_args(["--terms", "투자자정보,유효기간", "--terms", "재파악", "--search", "투자자정보 유효기간",
                              "--regs", "KOFIA:표준투자권유준칙"])
        h = hints_from_args(args)
        self.assertEqual(h.terms, ["투자자정보", "유효기간", "재파악"])
        self.assertEqual(h.search, ["투자자정보 유효기간"])
        self.assertEqual(Hints.from_dict(h.to_dict()), h)
        self.assertIsNone(hints_from_args(ap.parse_args([])))

    def test_cases_considered_lists_weaker_cases_for_claude(self):
        case = {"id": "c-9", "주제": "호가 관련 사례", "키워드": ["호가"],
                "경로": [{"사이트": "KRX", "규정": "시장감시규정", "조문제목": "회원의 금지행위"}]}
        r = self.find("호가 정정 시간", cases=[case], texts=[])
        self.assertIn("c-9", [h["case"]["id"] for h in r.considered])

    def test_explore_slot_keeps_a_non_case_candidate(self):
        from match import Candidate, _with_explore_slot
        ranked = [Candidate("KOFIA", f"r{i}", str(i), score=1 - i / 10, sources=["case"]) for i in range(6)]
        ranked.append(Candidate("KRX", "탐색", "x", score=0.1, sources=["catalog"]))
        top = _with_explore_slot(ranked, {"k": 5, "explore_slots": 1})
        self.assertEqual(len(top), 5)
        self.assertEqual(top[-1].name, "탐색")
        self.assertIn("explore", top[-1].sources)

    def test_suitability_question_without_the_word_finds_consumer_law(self):
        # 실사용 시험(2026-10-27)에서 "적합성"이란 말 없이 물으면 금소법이 후보에 없던 문제
        from match import load_json, tokenize
        q = "증권사가 고객에게 투자권유를 할 때 고객의 투자성향(투자목적, 재산상황, 투자경험)을 먼저 확인해야 한다는 근거 규정이 뭐야?"
        toks = tokenize(q)
        for junk in ("규정이", "뭐야", "한다는", "먼저", "확인해야"):
            self.assertNotIn(junk, toks)
        law = RegulationText(REGS[6], [Article("제17조", "적합성원칙", "투자성 상품의 경우 취득 목적, 재산상황, 취득 경험을 파악한다.")] + FILLER)
        from match import find_candidates
        r = find_candidates(q, regs=REGS, case_list=[], texts=[law], aliases=load_json("aliases.json"), cfg=cfg())
        self.assertIn("금융소비자보호", [a["area"] for a in r.areas])
        cand = next(c for c in r.candidates if c.name == "금융소비자 보호에 관한 법률")
        self.assertEqual(cand.articles[0]["title"], "적합성원칙")

    def test_every_candidate_has_a_reason(self):
        r = self.find("시장감시규정 회원의 금지행위")
        self.assertTrue(r.candidates)
        self.assertTrue(all(c.reasons for c in r.candidates))


class DataFilesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["REGSEARCH_DATA_DIR"] = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_cases_load_counts_reconfirmations(self):
        import cases
        store.append_jsonl(cases.path(), {"id": "c-1", "확정일": "2026-10-01", "주제": "a", "키워드": []})
        store.append_jsonl(cases.path(), {"재확정": "c-1", "확정일": "2026-10-20"})
        store.append_jsonl(cases.path(), {"재확정": "c-1", "확정일": "2026-10-10"})
        [c] = cases.load()
        self.assertEqual((c["확인횟수"], c["최근확정일"]), (3, "2026-10-20"))

    def test_catalog_falls_back_to_seed(self):
        import catalog
        self.assertFalse(catalog.path().exists())
        regs = catalog.load()
        if catalog.SEED.exists():
            self.assertGreater(len(regs), 100)
            self.assertTrue(catalog.path().exists())  # 첫 사용에 사용자 데이터 폴더로 복사
            self.assertEqual({r.site for r in regs}, {"KOFIA", "KRX", "법제처"})

    def test_aliases_point_to_catalog_names(self):
        import catalog
        from match import load_json
        if not catalog.SEED.exists():
            self.skipTest("seed 없음")
        names = {r.name for r in catalog.load()}
        missing = [n for vals in load_json("aliases.json")["regulations"].values() for n in vals if n not in names]
        self.assertEqual(missing, [])


class EvalTest(unittest.TestCase):
    def test_judge_rank_and_article(self):
        from eval import judge
        from match import find_candidates
        r = find_candidates("고객 주문 없이 예탁재산으로 매매하면 안 되는 근거", regs=REGS, case_list=[], texts=TEXTS,
                            aliases=ALIASES, cfg=cfg())
        j = judge(r, [{"site": "KOFIA", "name": "매매거래계좌설정약관", "article_title": "임의매매의 금지"}])
        self.assertIsNotNone(j["rank"])
        self.assertTrue(j["article_hit"])
        miss = judge(r, [{"site": "KRX", "name": "없는 규정"}])
        self.assertEqual((miss["rank"], miss["article_hit"]), (None, None))


if __name__ == "__main__":
    unittest.main()
