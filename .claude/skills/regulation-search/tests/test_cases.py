"""사례집 확정 절차·개정 이후 미확인 오프라인 테스트."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cache  # noqa: E402
import cases  # noqa: E402
from adapters.base import Article, Regulation, RegulationText  # noqa: E402

TODAY = "2026-10-27"


def kofia_reg(rev="1785", date="2026-03-17"):
    return Regulation(site="KOFIA", reg_id="120", name="매매거래계좌설정약관", category=["표준약관"],
                      revision_id=rev, revision_date=date, url="https://law.kofia.or.kr/x")


def law_reg(rev="288615", date="2026-10-02"):
    return Regulation(site="법제처", reg_id="법령/자본시장과금융투자업에관한법률", name="자본시장과 금융투자업에 관한 법률",
                      category=["자본시장 일반"], kind="법률", revision_id=rev, revision_date=date,
                      url="https://www.law.go.kr/법령/자본시장과금융투자업에관한법률")


def draft(topic="고객 주문 없이 매매 금지 근거", title="임의매매의 금지", number="제6조"):
    return {"주제": topic, "키워드": ["임의매매"], "업무영역": ["자본시장 일반"], "결과": "근거확인",
            "경로": [{"사이트": "KOFIA", "카테고리": ["표준약관"], "규정": "매매거래계좌설정약관", "조문": number,
                     "조문제목": title, "규정개정일": "2026-03-17", "개정판": "1785"}],
            "처음후보": ["KOFIA:매매거래계좌설정약관"]}


class CasesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["REGSEARCH_DATA_DIR"] = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_confirm_new_then_reconfirm_same(self):
        r1 = cases.confirm(draft(), today=TODAY)
        self.assertEqual((r1["action"], r1["id"]), ("추가", "c-202610-001"))
        self.assertIn("금투협-표준약관 카테고리에서 매매거래계좌설정약관 제6조(임의매매의 금지)를 참고", r1["서술"])
        self.assertTrue(r1["서술"].startswith("26년 10월에 '고객 주문 없이 매매 금지 근거'에 대해"))

        r2 = cases.confirm(draft(topic="고객 주문 없이 매매 금지 근거는?"), today="2026-10-28")
        self.assertEqual((r2["action"], r2["id"], r2["확인횟수"]), ("재확정", "c-202610-001", 2))
        [c] = cases.load()
        self.assertEqual((c["확인횟수"], c["최근확정일"]), (2, "2026-10-28"))
        # 원 줄은 고치지 않는다(append-only)
        lines = cases.path().read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn('"재확정": "c-202610-001"', lines[1])

    def test_different_topic_same_path_is_new_case(self):
        cases.confirm(draft(), today=TODAY)
        r = cases.confirm(draft(topic="랩어카운트 일임 범위 밖 매매"), today=TODAY)
        self.assertEqual((r["action"], r["id"]), ("추가", "c-202610-002"))

    def test_reconfirm_with_moved_article_updates_path(self):
        cases.confirm(draft(), today=TODAY)
        moved = draft(number="제7조")
        moved["경로"][0]["개정판"] = "1900"
        moved["재확정대상"] = "c-202610-001"
        r = cases.confirm(moved, today="2026-11-30")
        self.assertTrue(r["경로갱신"])
        [c] = cases.load()
        self.assertEqual(c["경로"][0]["조문"], "제7조")
        self.assertEqual(c["경로"][0]["개정판"], "1900")

    def test_validation(self):
        with self.assertRaises(ValueError):
            cases.confirm({**draft(), "결과": "아마도"})
        with self.assertRaises(ValueError):
            cases.confirm({**draft(), "경로": []})
        r = cases.confirm({**draft(), "결과": "해당없음", "경로": []}, today=TODAY)  # 사람이 명시적으로 확정한 해당없음
        self.assertEqual(r["action"], "추가")
        self.assertIn("해당 규정 없음으로 확정", r["서술"])

    def test_concurrent_confirms_get_unique_ids(self):
        code = (f"import sys; sys.path.insert(0, {str(SCRIPTS)!r}); import cases\n"
                "for j in range(5):\n"
                "    cases.confirm({'주제': f'주제 W-{j} 고유번호', '결과': '근거확인', '경로': [{'사이트': 'KRX', '규정': f'규정 W-{j}'}]}, today='2026-10-27')\n")
        env = dict(os.environ)
        procs = [subprocess.Popen([sys.executable, "-c", code.replace("W", str(i))], env=env) for i in range(4)]
        for p in procs:
            self.assertEqual(p.wait(timeout=120), 0)
        ids = [c["id"] for c in cases.load()]
        self.assertEqual(len(ids), 20)
        self.assertEqual(len(set(ids)), 20)

    def test_stale_detection(self):
        cases.confirm(draft(), today=TODAY)
        [c] = cases.load()
        self.assertEqual(cases.stale_details(c, [kofia_reg()]), [])
        details = cases.stale_details(c, [kofia_reg(rev="1900", date="2026-11-20")])
        self.assertEqual(len(details), 1)
        self.assertIn("1785 → 현재 1900", details[0])

    def test_relocate_step_by_title(self):
        text = RegulationText(kofia_reg(rev="1900", date="2026-11-20"), [
            Article("제6조", "다른 조문", "…"), Article("제7조", "임의매매의 금지", "…", url="https://x/7")])
        step = cases.relocate_step(draft()["경로"][0], text)
        self.assertTrue(step["found"])
        self.assertEqual((step["조문"], step["개정판"], step["규정개정일"], step["링크"]),
                         ("제7조", "1900", "2026-11-20", "https://x/7"))
        law_step = {"사이트": "법제처", "법령": "자본시장과 금융투자업에 관한 법률", "조항": "제17조 제2항", "조문제목": "적합성원칙"}
        law_text = RegulationText(law_reg(), [Article("제18조", "적합성원칙", "…")])
        self.assertEqual(cases.relocate_step(law_step, law_text)["조항"], "제18조 제2항")
        missing = cases.relocate_step(law_step, RegulationText(law_reg(), [Article("제1조", "목적", "…")]))
        self.assertFalse(missing["found"])

    def test_make_draft_fills_from_cache_and_alias(self):
        cache.put(RegulationText(kofia_reg(), [Article("제6조", "임의매매의 금지", "…", url="https://k/6")]))
        cache.put(RegulationText(law_reg(), [Article("제70조", "임의매매의 금지", "…", url="https://l/70")]))
        d = cases.make_draft("고객 주문 없이 매매 금지 근거", ["KOFIA|매매거래계좌설정약관|6", "법제처|자본시장법|70"],
                             ["KOFIA:매매거래계좌설정약관"], law_query="임의매매", regs=[kofia_reg(), law_reg()])
        k, law = d["경로"]
        self.assertEqual((k["조문"], k["조문제목"], k["개정판"], k["링크"]), ("제6조", "임의매매의 금지", "1785", "https://k/6"))
        self.assertEqual((law["조항"], law["검색어"], law["법령시행일"]), ("제70조", "임의매매", "2026-10-02"))
        self.assertIn("임의매매의 금지", d["키워드"])
        self.assertNotIn("주문", [k for k in d["키워드"] if k.endswith(("에서", "이", "을"))])
        self.assertFalse(any(k.endswith("에서") for k in d["키워드"]))  # "매매거래에서" 같은 조사 붙은 형태는 저장하지 않는다
        self.assertIn("자본시장 일반", d["업무영역"])
        self.assertIn("법제처 키워드 '임의매매'로", d["서술"])
        r = cases.confirm(d, today=TODAY)  # 초안 그대로 확정할 수 있어야 한다
        self.assertEqual(r["action"], "추가")

    def test_draft_file_and_confirm_latest(self):
        import io
        from contextlib import redirect_stdout
        p = cases.save_draft(draft())
        self.assertEqual(cases.latest_draft(), p)
        self.assertEqual(cases.load(), [])  # 초안만으로는 사례집에 아무것도 쓰지 않는다
        out = io.StringIO()
        with redirect_stdout(out):
            cases.main(["confirm", "--latest"])
        self.assertIn('"action": "추가"', out.getvalue())
        self.assertEqual(len(cases.load()), 1)

    def test_draft_keeps_hints_and_eval_replays_them(self):
        from match import Hints
        cache.put(RegulationText(kofia_reg(), [Article("제6조", "임의매매의 금지", "…")]))
        h = Hints(terms=["임의매매", "주문을 받지 아니하고"], search=["임의매매"])
        d = cases.make_draft("고객 몰래 매매하면 안 되는 근거", ["KOFIA|매매거래계좌설정약관|6"], regs=[kofia_reg()], hints=h)
        self.assertEqual(d["해석"]["핵심어"], ["임의매매", "주문을 받지 아니하고"])
        self.assertEqual(d["키워드"][:2], ["임의매매", "주문을 받지 아니하고"])
        cases.confirm(d, today=TODAY)
        import eval as ev
        [item] = ev.queries_from_cases()
        self.assertEqual(Hints.from_dict(item["hints"]), h)

    def test_match_marks_stale_case(self):
        from match import find_candidates, load_json
        cases.confirm(draft(), today=TODAY)
        r = find_candidates("임의매매 금지 근거", regs=[kofia_reg(rev="1900", date="2026-11-20")], texts=[],
                            cfg=load_json("match.json"))
        self.assertTrue(r.cases[0]["case"]["개정이후미확인"])
        kofia = next(c for c in r.candidates if c.name == "매매거래계좌설정약관")
        self.assertTrue(any("개정 이후 미확인" in x for x in kofia.reasons))


if __name__ == "__main__":
    unittest.main()
