"""규칙 기반 러프 파싱: 사이트 파서가 조문으로 나누지 못한 덩어리를 다시 나눈다(사이트 공통).

사이트 파서는 각 사이트가 조문마다 붙이는 제목 칸을 기준으로 자른다. 모범규준·준칙·설명서처럼 제목을 본문 안에
글자로만 쓴 문서는 통째로 한 덩어리(번호 없음)가 되어 조문 검색에서 빠진다. 여기서 그 덩어리만 줄 단위로 다시 본다.

1. 범용 조문 나누기 — 제목처럼 생긴 줄에서 자른다.
   - "제N조(제목)", "제N조의M"          → 법령식 조문. 이 꼴이 하나라도 나오면 아래 "N." 꼴은 조문으로 보지 않는다
                                          (법령의 호 목록 "1. 국가"를 조문으로 착각하지 않게).
   - "N. 제목"(짧은 줄, 문장 끝 아님)     → 모범규준식 조문. 번호가 1, 2, 3…으로 이어질 때만. 큰 제목(Ⅰ. 등) 바로 뒤의
                                          1은 새로 시작으로 본다. 바로 다음 줄도 이어지는 번호 줄이면 목록으로 보고, 목록이
                                          시작되면 이어지는 번호(2, 3…)는 제목이 아니라 목록으로 둔다.
   - "Ⅰ. 총칙", "Ⅳ-1. 투자자정보"        → 편·장 제목(조문의 위치 정보)
   - "[참고1]", "[별표 2]", "[별지 제3호]" → 첨부. 부칙에 섞여 있으면 떼어 낸다.
   - "Ⅴ. 부칙", "부 칙"                  → 그 뒤는 부칙(조문 검색에서 빠진다)
2. 제목 줄 사이에 번호 제목 없이 이어지는 글(예: "Ⅳ. 금지행위" 바로 아래 본문)과 제목을 하나도 못 찾은 덩어리는
   약 800자씩 잘라 "블록N"으로 둔다(검색에는 걸리게). 블록은 장 제목·부칙을 넘지 않고, 장 제목을 제목으로 단다.

나눈 조문에는 extra.parse = "rough"(1) 또는 "block"(2)을 단다. 사이트 구조로 나눈 조문(표시 없음)과 구분해
답변에서 "자동 분할"로 밝히기 위해서다. 본문 글자는 바꾸지 않는다(줄을 나누기만 한다).
"""
from __future__ import annotations

import re

from adapters.base import Article, RegulationText, normalize_article_number

# 이 파일의 규칙을 바꾸면 올린다. 캐시의 값과 다르면 캐시 본문을 다시 나눈다(사이트에 다시 접속하지 않음).
ROUGH_VERSION = 2  # 2: 장 제목 아래 블록을 제자리에, "Ⅴ. 부칙" 뒤는 부칙

BLOCK_CHARS = 800
HEADING_MAX = 40

ROMAN = "ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ"
RE_ROMAN = re.compile(rf"^([{ROMAN}]+|[IVX]{{1,5}})(-\d+)?\s*[.\)]\s*\S")
RE_ROMAN_SUB = re.compile(rf"^([{ROMAN}]+|[IVX]+)-\d+")
RE_LAW = re.compile(r"^(제\s*\d+(?:-\d+)?\s*조(?:\s*의\s*\d+)?)\s*(?:\(([^)]*)\))?\s*(.*)$")
RE_NUM = re.compile(r"^(\d{1,3})\.\s*(\S.*)$")
RE_ADDENDA = re.compile(rf"^(?:(?:[{ROMAN}]+|[IVX]{{1,5}})\s*[.\)]\s*)?부\s*칙(?:\s*[<(].*)?$")
RE_ANNEX = re.compile(r"^\[\s*(참고|별표|별지|서식|붙임|첨부)[^\]]*\]")
SENTENCE_END = re.compile(r"(다|함|음|것|임)\s*[.。]?\s*$")


def _short_title(line: str, title: str) -> bool:
    return len(line) <= HEADING_MAX and not SENTENCE_END.search(title) and not title.endswith((",", "·"))


def _is_num_heading(line: str, nxt: str, expected: int | None, after_roman: bool,
                    list_last: int | None) -> tuple[int, str] | None:
    m = RE_NUM.match(line)
    if not m:
        return None
    n, title = int(m[1]), m[2].strip()
    if not _short_title(line, title):
        return None
    if list_last is not None and n == list_last + 1:
        return None  # 조문 안 목록이 이어지는 중(애매하면 잘못 자르기보다 붙여 둔다)
    if expected is None:
        ok = n == 1
    else:
        ok = n == expected or (after_roman and n == 1)
    if not ok:
        return None
    nm = RE_NUM.match(nxt)
    if nm and int(nm[1]) == n + 1 and _short_title(nxt, nm[2].strip()):
        return None  # 다음 줄도 이어지는 짧은 번호 줄이면 "1. 국가 / 2. 한국은행" 같은 목록이다
    return n, title


def _blocks(lines: list[str], base: Article, start: int = 1, title: str | None = None) -> list[Article]:
    out, buf = [], []
    for line in lines:
        buf.append(line)
        if sum(len(x) for x in buf) >= BLOCK_CHARS:
            out.append(buf)
            buf = []
    if buf:
        out.append(buf)
    return [Article(number="" if base.addenda is not None else f"블록{start + i}", title=title or chunk[0][:30],
                    text="\n".join(chunk), path=list(base.path),
                    addenda=base.addenda, url=base.url, extra={**base.extra, "parse": "block"})
            for i, chunk in enumerate(out)]


def split_lines(lines: list[str], base: Article) -> list[Article]:
    """번호 없는 덩어리 하나를 조문들로 나눈다. 제목을 하나도 못 찾으면 블록으로."""
    lines = [l.strip() for l in lines if l.strip()]
    out: list[Article] = []
    path = list(base.path)
    cur: Article | None = None
    pre: list[str] = []
    expected: int | None = None
    after_roman = False
    list_last: int | None = None
    addenda: str | None = None
    law_mode = any(RE_LAW.match(l) and RE_LAW.match(l)[2] is not None for l in lines)
    found = False

    def start(number: str, title: str, first: str = "", parse: str = "rough") -> Article:
        a = Article(number=number, title=title, text=first, path=list(path), addenda=addenda, url=base.url,
                    extra={**base.extra, "parse": parse})
        out.append(a)
        return a

    for i, line in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        m = RE_ANNEX.match(line)
        if m:
            found = True
            addenda = None  # 첨부는 부칙이 아니다
            cur = start(m[0], line[len(m[0]):].strip())
            cur.extra["kind"] = "첨부"
            continue
        if RE_ADDENDA.match(line):
            found = True
            addenda, cur, list_last = line, None, None  # 이 뒤는 부칙: 장 아래 글처럼 블록으로 두되 부칙 표시
            path = list(base.path)
            continue
        if RE_ROMAN.match(line) and _short_title(line, line):
            found = True
            level = 1 if RE_ROMAN_SUB.match(line) else 0
            path = path[: len(base.path) + level] + [line]
            addenda = None
            after_roman, cur, list_last = True, None, None
            continue
        law = RE_LAW.match(line)
        if law and (law[2] is not None or len(line) <= HEADING_MAX):
            found = True
            cur = start(normalize_article_number(law[1]), (law[2] or "").strip(), law[3].strip())
            after_roman, list_last = False, None
            continue
        num = None if law_mode else _is_num_heading(line, nxt, expected, after_roman, list_last)
        if num:
            found = True
            expected, after_roman, list_last = num[0] + 1, False, None
            cur = start(f"{num[0]}.", num[1])
            continue
        item = RE_NUM.match(line)
        if item and cur is not None:
            n = int(item[1])
            if n == 1 or (list_last is not None and n == list_last + 1):
                list_last = n
        if cur is None:
            if len(path) == len(base.path) and addenda is None:
                pre.append(line)  # 첫 제목 앞 머리말
                continue
            # 장 제목(또는 부칙) 바로 아래 번호 제목 없는 글: 그 자리에 블록으로 두고 장 제목을 단다
            cur = start("블록", addenda or path[-1], line, parse="block")
        else:
            cur.text = f"{cur.text}\n{line}" if cur.text else line
    if not found:
        return _blocks(lines, base)
    pieces: list[Article] = []
    for a in out:  # 장 아래 블록이 길면 그 장 안에서 800자씩 다시 자른다
        pieces += _blocks(a.text.split("\n"), a, title=a.title) if a.extra.get("parse") == "block" else [a]
    return (_blocks(pre, base) if pre else []) + pieces


def refine_text(text: RegulationText) -> RegulationText:
    """번호 없는 덩어리만 다시 나누고, 부칙에 섞인 첨부를 떼어 낸다. 이미 나뉜 조문은 그대로 둔다."""
    out: list[Article] = []
    annex: Article | None = None
    annex_label: str | None = None
    for a in text.articles:
        first = a.text.split("\n", 1)[0].strip()
        if a.addenda is not None:
            # 부칙 뒤에 붙은 "[참고1] …" 같은 첨부: 부칙이 아니므로 떼어 내고, 같은 부칙 표시의 다음 덩어리를 이어 붙인다
            if RE_ANNEX.match(first):
                annex, annex_label = None, a.addenda
                for piece in split_lines(a.text.split("\n"), Article("", "", "", a.path, None, a.url, dict(a.extra))):
                    out.append(piece)
                    if piece.extra.get("kind") == "첨부":
                        annex = piece
                continue
            if annex is not None and a.addenda == annex_label and not a.number:
                annex.text = f"{annex.text}\n{a.text}" if annex.text else a.text
                continue
            annex = annex_label = None
            out.append(a)
            continue
        annex = annex_label = None
        if a.number or a.extra.get("parse"):
            out.append(a)
            continue
        out += split_lines(a.text.split("\n"), a)
    blocks = [a for a in out if a.extra.get("parse") == "block" and a.addenda is None]
    for i, a in enumerate(blocks, 1):  # 덩어리마다 1부터 매기면 겹치므로 규정 전체에서 다시 매긴다
        a.number = f"블록{i}"
    refined = RegulationText.from_dict(text.to_dict())
    refined.articles = out
    refined.regulation.extra["rough"] = ROUGH_VERSION
    return refined
