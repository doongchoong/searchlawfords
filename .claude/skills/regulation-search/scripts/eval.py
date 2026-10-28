"""매칭 회귀 평가.

질의마다 처음 후보 상위 k개에 정답 규정이 있었는지(hit@k)와, 그 후보의 조문 검색 결과에 정답 조문이
있었는지(조문 적중)를 잰다. 정답은 references/eval_queries.json(시드 질의) 또는 확정 사례(cases.jsonl)의 경로.
매칭 방식이나 match.json을 바꿀 때마다 돌려 나빠진 곳이 없는지 본다. 결과는 <데이터>/eval/에 쌓인다.

두 가지 모드를 잰다.
- auto: 질문만 넣는다(규칙 기반 키워드 추출). 이 스크립트 혼자 돈다.
- hints: Claude의 질문 해석(시드 질의의 "hints", 확정 사례의 "해석")을 함께 넣는다. 실제 사용(SKILL.md)과 같은 경로를
  재현한다. 해석이 없는 항목은 auto와 같다.

    python eval.py                     # 시드 질의, 오프라인(카탈로그+캐시만), 두 모드
    python eval.py --mode hints        # 한 모드만
    python eval.py --online            # 사이트 검색과 본문 받기까지(느림, 캐시가 쌓인다)
    python eval.py --cases             # 확정 사례로 평가
"""
from __future__ import annotations

import json
import sys
from datetime import datetime

import store
from adapters.base import REFERENCES, normalize_name
from match import Hints, MatchResult, find_candidates, nospace

KS = (1, 3, 5)
MODES = ("auto", "hints")


def load_queries() -> list[dict]:
    return json.loads((REFERENCES / "eval_queries.json").read_text(encoding="utf-8"))["queries"]


def queries_from_cases() -> list[dict]:
    import cases

    out = []
    for c in cases.load():
        if c.get("결과") != "근거확인":
            continue
        expect = [{"site": s.get("사이트"), "name": s.get("규정") or s.get("법령"), "article_title": s.get("조문제목")}
                  for s in c.get("경로", []) if s.get("사이트") and (s.get("규정") or s.get("법령"))]
        if expect:
            out.append({"q": c.get("주제", ""), "expect": expect, "case_id": c.get("id"), "hints": c.get("해석")})
    return out


def judge(result: MatchResult, expect: list[dict]) -> dict:
    """정답 규정의 첫 순위(없으면 None)와 조문 적중 여부."""
    rank, article_hit, matched = None, False, None
    for i, c in enumerate(result.candidates, 1):
        for e in expect:
            if c.site != e["site"] or normalize_name(c.name) != normalize_name(e["name"]):
                continue
            title = nospace(e.get("article_title") or "")
            hit = bool(title) and any(title in nospace(a.get("title") or a.get("heading") or "") for a in c.articles)
            if rank is None:
                rank, matched = i, f"{c.site}:{c.name}"
            article_hit = article_hit or hit
    needs_article = any(e.get("article_title") for e in expect)
    return {"rank": rank, "article_hit": article_hit if needs_article else None, "matched": matched}


def evaluate(items: list[dict], mode: str = "auto", **find_kwargs) -> dict:
    """mode가 hints면 항목의 해석(hints/해석)을 find_candidates에 넘긴다."""
    rows = []
    for item in items:
        hints = Hints.from_dict(item.get("hints")) if mode == "hints" else None
        result = find_candidates(item["q"], hints=hints, **find_kwargs)
        j = judge(result, item["expect"])
        rows.append({"q": item["q"], **j, "case_id": item.get("case_id"), "hinted": bool(hints),
                     "candidates": result.first_candidates(),
                     "top_articles": [a["heading"] for c in result.candidates[:1] for a in c.articles[:2]]})
    n = len(rows) or 1
    summary = {f"hit@{k}": round(sum(1 for r in rows if r["rank"] and r["rank"] <= k) / n, 3) for k in KS}
    art = [r for r in rows if r["article_hit"] is not None]
    summary["article_hit"] = round(sum(1 for r in art if r["article_hit"]) / (len(art) or 1), 3)
    summary["n"] = len(rows)
    summary["hinted"] = sum(1 for r in rows if r["hinted"])
    return {"summary": summary, "rows": rows}


def main(argv: list[str] | None = None) -> int:
    import argparse

    import cdp

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="매칭 회귀 평가")
    ap.add_argument("--cases", action="store_true", help="시드 질의 대신 확정 사례로 평가")
    ap.add_argument("--online", action="store_true", help="사이트 검색 + 상위 3개 본문 받기")
    ap.add_argument("--mode", choices=(*MODES, "both"), default="both")
    args = ap.parse_args(argv)

    items = queries_from_cases() if args.cases else load_queries()
    kwargs: dict = {}
    browser = None
    if args.online:
        browser = cdp.Browser.launch()
        kwargs = {"site_search": True, "fetch": 3, "browser": browser}
    modes = MODES if args.mode == "both" else (args.mode,)
    try:
        reports = {m: evaluate(items, mode=m, **kwargs) for m in modes}
    finally:
        if browser:
            browser.close()
    stamp = datetime.now()
    out = store.data_dir() / "eval" / f"{stamp:%Y%m%d-%H%M%S}-{'cases' if args.cases else 'seed'}-{'online' if args.online else 'offline'}.json"
    store.write_json(out, {"modes": reports, "source": "cases" if args.cases else "eval_queries",
                           "online": args.online, "at": stamp.isoformat(timespec="seconds")})

    for m, report in reports.items():
        print(f"== {m}")
        for r in report["rows"]:
            mark = f"{r['rank']}위" if r["rank"] else "없음"
            art = "" if r["article_hit"] is None else (" 조문O" if r["article_hit"] else " 조문X")
            print(f"[{mark:>3}{art}] {r['q']}  → 1위 {r['candidates'][0] if r['candidates'] else '-'}")
        print(json.dumps(report["summary"], ensure_ascii=False))
    print(f"저장: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
