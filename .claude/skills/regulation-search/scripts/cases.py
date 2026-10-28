"""사람이 확정한 사례집(<데이터>/cases.jsonl)

- 한 줄이 사례 하나. 사람이 승인하거나 고친 경로만 저장한다(거절하면 아무것도 쓰지 않는다).
- 추가만 한다(append-only). 같은 주제·같은 경로를 다시 확정하면 원 사례를 고치지 않고 재확정 줄
  {"재확정": "c-…", "확정일": "…"}을 덧붙인다. 개정으로 조문 번호가 바뀐 걸 확인하고 다시 확정하면 재확정 줄에
  "경로갱신"을 함께 적는다. 확인횟수·최근확정일·현재 경로는 읽을 때 계산한다.
- "개정 이후 미확인": 사례 경로의 개정판·개정일이 카탈로그(또는 사이트)의 현재 것과 다르면 표시하고,
  다음에 열 때 조문 제목으로 위치를 다시 찾는다(relocate).

    python cases.py draft "주제" --path "KOFIA|매매거래계좌설정약관|6" --path "법제처|자본시장법|70" [--first "KOFIA:…"] [--law-query 검색어]
        → 초안을 <데이터>/drafts/에 저장하고 {"file": …, "draft": …}를 출력
    python cases.py confirm --latest               (가장 최근 초안을 확정. 고친 경우 그 파일을 고친 뒤)
    python cases.py confirm --file 초안.json        (또는 --file - 로 표준입력)
    python cases.py list [--stale]
    python cases.py relocate c-202610-001 [--confirm]
"""
from __future__ import annotations

import copy
import json
import re
import sys
from datetime import date
from pathlib import Path

import store
from adapters.base import RegulationText, normalize_name

SITE_LABEL = {"KOFIA": "금투협", "KRX": "거래소", "법제처": "법제처"}
RESULTS = ("근거확인", "해당없음")


def path() -> Path:
    return store.data_dir() / "cases.jsonl"


def drafts_dir() -> Path:
    return store.data_dir() / "drafts"


def save_draft(draft: dict) -> Path:
    """초안을 파일로 남긴다(사람이 고칠 수 있게). 확정 전에는 사례집에 아무것도 쓰지 않는다."""
    from datetime import datetime

    p = drafts_dir() / f"{datetime.now():%Y%m%d-%H%M%S-%f}.json"
    store.write_json(p, draft)
    return p


def latest_draft() -> Path:
    files = sorted(drafts_dir().glob("*.json")) if drafts_dir().exists() else []
    if not files:
        raise SystemExit("저장된 확정 초안이 없습니다. 먼저 cases.py draft를 실행하세요.")
    return files[-1]


# ---------------------------------------------------------------------------
# 읽기
# ---------------------------------------------------------------------------

def load() -> list[dict]:
    """사례 목록. 각 사례에 확인횟수·최근확정일을 계산해 붙이고, 경로갱신이 있으면 최신 경로로 바꾼다.
    매번 파일을 다시 읽는다(다른 세션이 방금 확정한 사례를 놓치지 않게)."""
    cases: dict[str, dict] = {}
    reconfirm: list[dict] = []
    for row in store.read_jsonl(path()):
        if "재확정" in row:
            reconfirm.append(row)
        elif row.get("id"):
            cases[row["id"]] = {**row, "확인횟수": 1, "최근확정일": row.get("확정일")}
    for row in sorted(reconfirm, key=lambda r: r.get("확정일") or ""):
        c = cases.get(row["재확정"])
        if not c:
            continue
        c["확인횟수"] += 1
        if (row.get("확정일") or "") >= (c["최근확정일"] or ""):
            c["최근확정일"] = row["확정일"]
        if row.get("경로갱신"):
            c["경로"] = row["경로갱신"]
    return list(cases.values())


def _reg_name(step: dict) -> str:
    return step.get("규정") or step.get("법령") or ""


def path_key(steps: list[dict]) -> frozenset:
    return frozenset((s.get("사이트"), normalize_name(_reg_name(s)), normalize_name(s.get("조문제목") or ""))
                     for s in steps)


# ---------------------------------------------------------------------------
# 개정 이후 미확인
# ---------------------------------------------------------------------------

def stale_details(case: dict, regs) -> list[str]:
    """사례 경로 중 현재 개정판이 사례를 확정할 때와 다른 것. regs는 카탈로그(Regulation 목록)."""
    current = {(r.site, normalize_name(r.name)): r for r in regs}
    out = []
    for s in case.get("경로", []):
        r = current.get((s.get("사이트"), normalize_name(_reg_name(s))))
        if r is None:
            continue
        old_rev = s.get("개정판")
        old_date = s.get("규정개정일") or s.get("법령시행일")
        if old_rev and r.revision_id and str(old_rev) != str(r.revision_id):
            out.append(f"{s['사이트']} {r.name}: 확정 때 개정판 {old_rev} → 현재 {r.revision_id}({r.revision_date})")
        elif not old_rev and old_date and r.revision_date and r.revision_date > old_date:
            out.append(f"{s['사이트']} {r.name}: 확정 때 {old_date} → 현재 {r.revision_date}")
    return out


def annotate_stale(case_list: list[dict], regs) -> list[dict]:
    for c in case_list:
        c["개정이후미확인"] = stale_details(c, regs)
    return case_list


def relocate_step(step: dict, text: RegulationText) -> dict:
    """현재 본문에서 조문 제목으로 위치를 다시 찾아 갱신한 경로 한 칸을 돌려준다(found 표시 포함)."""
    new = copy.deepcopy(step)
    title = step.get("조문제목")
    art = text.find(title=title) if title else None
    new["found"] = bool(art)
    if art:
        key = "조항" if step.get("사이트") == "법제처" else "조문"
        old = step.get(key) or ""
        # "제17조 제2항"처럼 항까지 적혀 있으면 조 번호만 바꾼다
        new[key] = re.sub(r"^제[\d\-]+조(?:의\d+)?", art.number, old) if old.startswith("제") else art.number
        new["링크"] = art.url or new.get("링크")
    reg = text.regulation
    new["개정판"] = reg.revision_id
    new["법령시행일" if step.get("사이트") == "법제처" else "규정개정일"] = reg.revision_date
    return new


# ---------------------------------------------------------------------------
# 초안과 확정
# ---------------------------------------------------------------------------

def _eul(word: str) -> str:
    """목적격 조사: 마지막 한글 글자에 받침이 있으면 '을', 없으면 '를'."""
    for ch in reversed(word):
        if "가" <= ch <= "힣":
            return "을" if (ord(ch) - 0xAC00) % 28 else "를"
    return "을(를)"


def describe(case: dict) -> str:
    """사람이 읽는 한 줄 서술."""
    d = case.get("확정일") or date.today().isoformat()
    parts = []
    for s in case.get("경로", []):
        if s.get("사이트") == "법제처":
            kw = f" 키워드 '{s['검색어']}'로" if s.get("검색어") else ""
            target = " ".join(x for x in (s.get("법령"), s.get("조항")) if x)
            parts.append(f"법제처{kw} {target}{_eul(target)} 검색")
        else:
            where = "-".join([SITE_LABEL.get(s.get("사이트"), s.get("사이트", ""))] + list(s.get("카테고리", [])))
            target = s.get("규정", "") + (f" {s.get('조문')}({s.get('조문제목')})" if s.get("조문") else "")
            parts.append(f"{where} 카테고리에서 {target}{_eul(target)} 참고")
    result = "이력 확정" if case.get("결과") == "근거확인" else "해당 규정 없음으로 확정"
    body = ", ".join(parts) if parts else "확인한 범위에서 근거를 찾지 못해"
    return f"{d[2:4]}년 {int(d[5:7])}월에 '{case.get('주제')}'에 대해 {body}해 {result}."


def _find_reg(regs, site: str, key: str):
    from adapters.base import pick_regulation
    from match import load_json

    site_regs = [r for r in regs if r.site == site]
    names = load_json("aliases.json").get("regulations", {}).get(key)
    if names:
        key = names[0]
    try:
        return pick_regulation(site_regs, key)
    except SystemExit as e:
        raise ValueError(f"{site}에서 '{key}'을(를) 하나로 정하지 못했습니다: {e}")


def make_draft(question: str, path_specs: list[str], first_candidates: list[str] | None = None,
               law_query: str | None = None, result: str = "근거확인", regs=None, hints=None) -> dict:
    """사람에게 보여 줄 확정 초안. path_specs는 "사이트|규정명·약칭·ID|조문번호(생략 가능)".
    본문 캐시가 있으면 조문 제목·링크·개정판을 본문 기준으로 채운다.
    hints(match.Hints)를 주면 '해석' 필드로 남겨 다음 매칭과 평가에서 재현에 쓰고, 키워드도 힌트 핵심어를 앞에 둔다."""
    import cache
    import catalog
    from match import classify_areas, core_terms, load_json, normalize_query

    regs = catalog.load() if regs is None else regs
    steps = []
    for spec in path_specs:
        site, key, *rest = [p.strip() for p in spec.split("|")]
        number = rest[0] if rest and rest[0] else None
        reg = _find_reg(regs, site, key)
        text = cache.get(site, reg.reg_id)
        meta = text.regulation if text else reg
        art = text.find(number) if (text and number) else None
        num = art.number if art else (number and (number if number.startswith("제") else f"제{number}조"))
        if site == "법제처":
            step = {"사이트": site, "검색어": law_query or "", "법령": reg.name, "조항": num or "",
                    "조문제목": art.title if art else "", "법령시행일": meta.revision_date, "개정판": meta.revision_id}
        else:
            step = {"사이트": site, "카테고리": reg.category, "규정": reg.name, "조문": num or "",
                    "조문제목": art.title if art else "", "규정개정일": meta.revision_date, "개정판": meta.revision_id}
        step["링크"] = (art.url if art and art.url else None) or meta.url or reg.url
        if number and not art:
            step["확인필요"] = "본문 캐시에 없어 조문 제목을 채우지 못함"
        steps.append(step)

    q = normalize_query(question, hints=hints)
    core = core_terms(q.keywords)  # "매매거래에서"·"입력이" 같은 조사 붙은 형태는 빼고 저장
    keywords = list(dict.fromkeys((hints.terms if hints else []) + core[:6]))
    keywords += [s["조문제목"] for s in steps if s.get("조문제목") and s["조문제목"] not in keywords]
    areas = [a["area"] for a in classify_areas(q, [], load_json("match.json"))]
    areas += [a for a in (hints.areas if hints else []) if a not in areas]
    areas += [c for s in steps if s["사이트"] == "법제처" for r in regs
              if r.site == "법제처" and r.name == s["법령"] for c in r.category if c not in areas]
    draft = {"주제": question, "키워드": keywords, "업무영역": areas, "결과": result, "경로": steps,
             "처음후보": first_candidates or []}
    if hints:
        draft["해석"] = hints.to_dict()
    draft["서술"] = describe({**draft, "확정일": date.today().isoformat()})
    return draft


def _validate(draft: dict) -> None:
    if not (draft.get("주제") or "").strip():
        raise ValueError("주제가 비어 있습니다")
    if draft.get("결과") not in RESULTS:
        raise ValueError(f"결과는 {RESULTS} 중 하나여야 합니다: {draft.get('결과')}")
    if draft["결과"] == "근거확인" and not draft.get("경로"):
        raise ValueError("근거확인 사례에는 경로가 하나 이상 있어야 합니다")
    for s in draft.get("경로", []):
        if s.get("사이트") not in SITE_LABEL or not _reg_name(s):
            raise ValueError(f"경로 항목에 사이트·규정이 없습니다: {s}")


def find_same(draft: dict, existing: list[dict]) -> dict | None:
    """같은 경로이고 주제가 비슷한(또는 '재확정대상'으로 지정한) 기존 사례."""
    from match import dice

    if draft.get("재확정대상"):
        return next((c for c in existing if c["id"] == draft["재확정대상"]), None)
    key = path_key(draft.get("경로", []))
    for c in existing:
        if path_key(c.get("경로", [])) == key and (
                normalize_name(c.get("주제", "")) == normalize_name(draft["주제"]) or dice(c.get("주제", ""), draft["주제"]) >= 0.6):
            return c
    return None


def _new_id(existing: list[dict], today: str) -> str:
    prefix = f"c-{today[:4]}{today[5:7]}-"
    nums = [int(c["id"][len(prefix):]) for c in existing if c.get("id", "").startswith(prefix) and c["id"][len(prefix):].isdigit()]
    return f"{prefix}{max(nums, default=0) + 1:03d}"


def confirm(draft: dict, today: str | None = None) -> dict:
    """사람이 승인한(고친) 초안을 저장한다. 같은 사례가 있으면 재확정 줄만 덧붙인다."""
    _validate(draft)
    today = today or date.today().isoformat()
    clean_steps = [{k: v for k, v in s.items() if k not in ("found", "확인필요")} for s in draft.get("경로", [])]
    with store.lock("cases-confirm", timeout=30):  # id 매기기와 쓰기를 한 번에
        existing = load()
        same = find_same(draft, existing)
        if same:
            row = {"재확정": same["id"], "확정일": today}
            old = {json.dumps(s, ensure_ascii=False, sort_keys=True) for s in same.get("경로", [])}
            new = {json.dumps(s, ensure_ascii=False, sort_keys=True) for s in clean_steps}
            if old != new:
                row["경로갱신"] = clean_steps
            store.append_jsonl(path(), row)
            return {"action": "재확정", "id": same["id"], "확인횟수": same["확인횟수"] + 1,
                    "경로갱신": "경로갱신" in row}
        record = {"id": _new_id(existing, today), "확정일": today, "주제": draft["주제"],
                  "키워드": draft.get("키워드", []), "업무영역": draft.get("업무영역", []), "결과": draft["결과"],
                  "경로": clean_steps, "처음후보": draft.get("처음후보", [])}
        if draft.get("해석"):  # Claude의 질문 해석(핵심어·규정·검색어) — 평가 때 그대로 재현한다
            record["해석"] = draft["해석"]
        record["서술"] = describe(record)
        store.append_jsonl(path(), record)
        return {"action": "추가", "id": record["id"], "서술": record["서술"]}


# ---------------------------------------------------------------------------
# 명령줄
# ---------------------------------------------------------------------------

def relocate(case: dict, browser=None, verify: bool = True) -> list[dict]:
    """사례 경로의 규정을 (현재 개정판으로) 열어 조문 제목으로 위치를 다시 찾는다."""
    import catalog
    import cdp
    from adapters import get_adapter

    regs = catalog.load()
    own = browser is None
    browser = browser or cdp.Browser.launch()
    try:
        out = []
        for s in case.get("경로", []):
            reg = _find_reg(regs, s["사이트"], _reg_name(s))
            text = get_adapter(s["사이트"], browser).open_cached(reg, verify=verify)
            out.append(relocate_step(s, text))
        return out
    finally:
        if own:
            browser.close()


def main(argv: list[str] | None = None) -> int:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="확정 사례집")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("draft", help="확정 초안 만들기(저장하지 않음)")
    p.add_argument("question")
    p.add_argument("--path", action="append", default=[], help='"사이트|규정명·약칭·ID|조문번호"')
    p.add_argument("--first", action="append", default=[], help="처음 보여준 후보(사이트:규정명)")
    p.add_argument("--law-query", help="법제처에서 쓴 검색어")
    p.add_argument("--none", action="store_true", help="결과를 '해당없음'으로(사람이 명시적으로 확정할 때만)")
    from match import add_hint_args
    add_hint_args(p)  # 매칭에 넘긴 질문 해석 힌트를 그대로 넘기면 '해석'으로 저장된다
    p = sub.add_parser("confirm", help="승인한 초안 저장")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", help="초안 JSON 파일, '-'면 표준입력")
    src.add_argument("--latest", action="store_true", help="가장 최근에 만든 초안")
    p = sub.add_parser("list")
    p.add_argument("--stale", action="store_true", help="개정 이후 미확인 사례만")
    p = sub.add_parser("relocate", help="개정 이후 조문 위치 다시 찾기(사이트 접속)")
    p.add_argument("case_id")
    p.add_argument("--confirm", action="store_true", help="찾은 위치로 재확정 줄을 쓴다(사람이 확인한 뒤에만)")
    args = ap.parse_args(argv)

    def dump(obj) -> None:
        print(json.dumps(obj, ensure_ascii=False, indent=2))

    if args.cmd == "draft":
        from match import hints_from_args
        d = make_draft(args.question, args.path, args.first, args.law_query, "해당없음" if args.none else "근거확인",
                       hints=hints_from_args(args))
        dump({"file": str(save_draft(d)), "draft": d})
    elif args.cmd == "confirm":
        if args.latest:
            raw = latest_draft().read_text(encoding="utf-8")
        else:
            raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
        dump(confirm(json.loads(raw)))
    elif args.cmd == "list":
        import catalog

        rows = annotate_stale(load(), catalog.load())
        if args.stale:
            rows = [c for c in rows if c["개정이후미확인"]]
        dump([{k: c.get(k) for k in ("id", "주제", "확인횟수", "최근확정일", "결과", "개정이후미확인", "서술")} for c in rows])
    elif args.cmd == "relocate":
        case = next((c for c in load() if c["id"] == args.case_id), None)
        if not case:
            raise SystemExit(f"사례 없음: {args.case_id}")
        steps = relocate(case)
        out = {"id": case["id"], "이전경로": case["경로"], "현재경로": steps,
               "모두찾음": all(s["found"] for s in steps if s.get("조문제목"))}
        if args.confirm:
            if not out["모두찾음"]:
                raise SystemExit("조문 제목으로 찾지 못한 경로가 있어 재확정하지 않았습니다. 사람이 확인해 주세요.")
            out["저장"] = confirm({**case, "경로": steps, "재확정대상": case["id"]})
        dump(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
