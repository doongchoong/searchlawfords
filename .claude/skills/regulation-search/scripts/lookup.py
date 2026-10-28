"""답변 직전에 쓰는 라이브 조회(사이트 구분 없이 한 진입점).

본문은 Adapter.open_cached로 가져온다: 사이트의 현재 개정판을 확인하고, 캐시와 같으면 캐시를, 다르면 새로
받는다(설계 원칙 "캐시를 믿지 않는다"). 결과에는 항상 "시점"(사이트상 개정일·본문 수집일·캐시 여부)이 붙는다.

    python lookup.py article --site KOFIA --reg 매매거래계좌설정약관 [--number 6] [--title 임의매매] [--no-verify]
    python lookup.py find    --site 법제처 --reg 자본시장법 --query "투자권유 정의" [--top 5]
    python lookup.py recent  [--days 30] [--site KRX]...
    python lookup.py search  "검색어" [--site 법제처]...

--reg는 규정 ID, 정식명, 정식명 일부, aliases.json의 약칭 중 아무거나.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date

import catalog
from adapters import SITES, get_adapter
from adapters.base import Article, Regulation, RegulationText, pick_regulation


def resolve(site: str, key: str, regs: list[Regulation] | None = None) -> Regulation:
    from match import load_json

    regs = [r for r in (catalog.load() if regs is None else regs) if r.site == site]
    names = load_json("aliases.json").get("regulations", {}).get(key)
    if names:
        hits = [r for r in regs if r.name in names]
        if len(hits) == 1:
            return hits[0]
    return pick_regulation(regs, key)


def timing(text: RegulationText, verified: bool) -> dict:
    reg = text.regulation
    return {
        "사이트상_개정일": reg.revision_date,
        "개정판": reg.revision_id,
        "본문_수집일": text.collected_at[:10],
        "캐시에서": bool(reg.extra.get("from_cache")),
        "라이브_확인": "현재 개정판과 같음을 확인함" if verified else "확인하지 않음(--no-verify)",
        "구분": reg.extra.get("status", "현행"),
        "조회일": date.today().isoformat(),
    }


def _parse_note(a: Article | None) -> dict:
    """사이트 구조가 아니라 글자 모양으로 나눈 조문이면 그렇다고 적는다(답변에 "자동 분할"로 밝힌다)."""
    kind = a and a.extra.get("parse")
    if kind == "rough":
        return {"분할": "자동 분할(본문 안 제목 줄로 나눔) — 조문 경계는 원문 링크로 확인"}
    if kind == "block":
        return {"분할": "자동 분할(제목을 못 찾아 글자 수로 자름) — 정식 조문 번호가 아님, 원문 링크로 확인"}
    return {}


def reg_info(text: RegulationText) -> dict:
    r = text.regulation
    info = {"site": r.site, "reg_id": r.reg_id, "name": r.name, "kind": r.kind, "category": r.category, "url": r.url}
    if r.extra.get("navigation"):
        info["찾아가는_경로"] = r.extra["navigation"]  # KRX: 조문 단위 링크가 없다
    if r.extra.get("header"):
        info["머리말"] = r.extra["header"]
    return info


def main(argv: list[str] | None = None) -> int:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="라이브 조회")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("article", help="조문 하나(번호 또는 제목)")
    p.add_argument("--site", required=True, choices=SITES)
    p.add_argument("--reg", required=True)
    p.add_argument("--number")
    p.add_argument("--title")
    p.add_argument("--addenda", action="store_true", help="부칙에서도 찾기")
    p.add_argument("--no-verify", action="store_true")
    p = sub.add_parser("find", help="규정 하나 안에서 질문과 맞는 조문")
    p.add_argument("--site", required=True, choices=SITES)
    p.add_argument("--reg", required=True)
    p.add_argument("--query", required=True)
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--no-verify", action="store_true")
    p = sub.add_parser("recent", help="최근 제·개정과 개정예고")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--site", action="append", choices=SITES)
    p = sub.add_parser("search", help="사이트 통합검색")
    p.add_argument("query")
    p.add_argument("--site", action="append", choices=SITES)
    args = ap.parse_args(argv)

    import cdp

    out: dict = {}
    with cdp.Browser.launch() as browser:
        if args.cmd in ("article", "find"):
            reg = resolve(args.site, args.reg)
            text = get_adapter(args.site, browser).open_cached(reg, verify=not args.no_verify)
            out = {"regulation": reg_info(text), "시점": timing(text, not args.no_verify)}
            if args.cmd == "article":
                art = text.find(args.number, args.title, include_addenda=args.addenda)
                out["article"] = {**art.to_dict(), **_parse_note(art)} if art else None
                if not art:
                    out["확인하지_못한_부분"] = f"'{args.number or ''} {args.title or ''}'에 맞는 조문이 현재 본문에 없음"
                elif args.title and re.sub(r"\s", "", args.title) not in re.sub(r"\s", "", art.title):
                    # 번호로 찾았는데 제목이 다르다: 번호가 밀렸거나 다른 조문(예: 같은 번호의 부칙)을 가리킨 것일 수 있다
                    out["확인하지_못한_부분"] = (f"번호 '{args.number}'로 찾은 조문의 제목이 '{art.title}'로, 준 제목 "
                                              f"'{args.title}'과 다름 — 번호 없이 --title로 다시 찾거나 부칙이면 --addenda")
            else:
                from match import load_json, normalize_query, search_articles
                cfg = load_json("match.json")["articles"] | {"top": args.top, "min_score": 0.5}
                hits = search_articles(text, normalize_query(args.query), cfg)
                # 러프 분할에서는 장마다 "1."이 다시 나올 수 있어 번호와 제목으로 짝짓는다
                full = {(a.number, a.title): a for a in text.articles if a.addenda is None}
                out["articles"] = []
                for h in hits:
                    a = full.get((h["number"], h["title"]))
                    out["articles"].append({**h, "text": a.text if a else "", **_parse_note(a)})
        elif args.cmd == "recent":
            out = {"days": args.days, "changes": [], "실패": []}
            for site in args.site or SITES:
                try:
                    out["changes"] += [c.to_dict() for c in get_adapter(site, browser).recent_changes(args.days)]
                except cdp.CDPError as e:
                    out["실패"].append(f"{site}: {e}")
        elif args.cmd == "search":
            out = {"query": args.query, "hits": [], "실패": []}
            for site in args.site or SITES:
                try:
                    out["hits"] += [h.to_dict() for h in get_adapter(site, browser).search(args.query)]
                except cdp.CDPError as e:
                    out["실패"].append(f"{site}: {e}")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
