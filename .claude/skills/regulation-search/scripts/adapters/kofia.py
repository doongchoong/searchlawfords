"""금융투자협회(KOFIA) 법규정보시스템 어댑터.

화면 구조(2026-10-27 확인, 셀렉터는 references/selectors.kofia.json):
- 카탈로그: 현행 분야별 트리(lawCurrentPartTree.do)의 WebFXTree JS 객체를 따라가 분류 경로와
  규정 ID(lawSeq)·현재 개정판 ID(historySeq)를 한 번에 얻는다.
- 본문: lawFullScreenContent.do?seq=&historySeq= 가 조문 전체를 서버 렌더링한다(historySeq=0은 빈 화면).
- 현재 개정일: lawFullScreen.do?seq=&historySeq=0 의 연혁 select(#p_sel)에서 선택된 항목. 페이지가 가볍다.
- 검색: 메인의 통합검색 폼, 최근 변경: 최신제ㆍ개정정보 목록과 규정 제ㆍ개정 예고 목록.
- HWP 원문은 파싱하지 않는다.

직접 실행(결과는 JSON):
    python kofia.py catalog [--no-dates] [--out FILE]
    python kofia.py article <규정명|규정ID> [조문번호] [--title 조문제목]
    python kofia.py revision <규정명|규정ID>
    python kofia.py search <검색어> [--field content|title|attach] [--history] [--pages N]
    python kofia.py recent [--days 30]
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cdp  # noqa: E402
from adapters.base import (  # noqa: E402
    KIND_MAIN, KIND_RULE, Adapter, Article, Change, NotSupported, Regulation, RegulationText,
    RevisionInfo, SearchHit, js_call, normalize_name, parse_date, pick_regulation, split_article_heading,
)

TREE_JS = r"""
((cfg) => {
  const root = window[cfg.root_var];
  if (!root) return null;
  const re = new RegExp(cfg.action_function + "\\((.*)\\)");
  const idx = cfg.arg_index;
  const out = [];
  const walk = (node, path) => {
    for (const c of node.childNodes || []) {
      const m = (c.action || "").match(re);
      const args = m ? [...m[1].matchAll(/'([^']*)'/g)].map(x => x[1]) : [];
      const name = args[idx.name] || c.text;
      if (args[idx.folder] === "1") {
        walk(c, path.concat([name]));
        continue;
      }
      if (args.length) out.push({path, args});
      if (c.childNodes && c.childNodes.length) walk(c, path.concat([name]));
    }
  };
  walk(root, []);
  return out;
})
"""

CONTENT_JS = r"""
((cfg) => {
  const root = document.querySelector(cfg.root);
  if (!root) return null;
  const clean = s => (s || "").replace(/ /g, " ").replace(/\s+/g, " ").trim();
  const headingSel = Object.keys(cfg.headings);
  const sel = [cfg.article_title, cfg.paragraph, cfg.addenda, cfg.addenda_text, ...headingSel].join(",");
  const out = {
    name: clean((root.querySelector(cfg.law_name) || {}).textContent),
    history: [...root.querySelectorAll(cfg.history)].map(e => clean(e.textContent)),
    articles: [],
    attachments: [...root.querySelectorAll(cfg.attachment_link)].map(a => ({name: clean(a.textContent), url: a.href})),
  };
  const path = [];
  let cur = null, addenda = null;
  for (const el of root.querySelectorAll(sel)) {
    // 본문 끝의 "[별표/서식 파일]" 목록도 .article 모양이라 조문에서 뺀다
    if (el.closest(cfg.attachment_area)) continue;
    const text = clean(el.textContent);
    const h = headingSel.find(s => el.matches(s));
    // .partition은 약관에선 장 제목이지만 모범규준에선 조문 안 "※ 회사참고사항" 강조라, 조문 안이면 제목이 아니다
    if (h !== undefined && el.closest(cfg.article_block)) continue;
    if (h !== undefined) {
      if (!text) continue;
      const level = cfg.headings[h];
      path.length = level;
      path[level] = text;
      cur = null;
      continue;
    }
    if (el.matches(cfg.addenda)) { addenda = text; path.length = 0; cur = null; continue; }
    if (el.matches(cfg.addenda_text)) {
      out.articles.push({heading: "", lines: [text], path: [], addenda,
                         content_seq: el.parentElement ? el.parentElement.id : null});
      cur = null;
      continue;
    }
    if (el.matches(cfg.article_title)) {
      const block = el.closest(cfg.article_block);
      cur = {heading: text, lines: [], path: path.filter(Boolean), addenda, content_seq: block ? block.id : null};
      out.articles.push(cur);
      continue;
    }
    if (el.closest(cfg.heading_blocks)) continue;  // <본장제목개정 ...> 같은 제목 주석
    if (cur && text) cur.lines.push(text);
  }
  return out;
})
"""

SELECT_JS = r"""
((sel) => {
  const s = document.querySelector(sel);
  return s ? [...s.options].map(o => [o.value, o.textContent.trim(), o.selected]) : null;
})
"""

ROWS_JS = r"""
((sel) => [...document.querySelectorAll(sel)].map(tr => ({
  cells: [...tr.children].map(td => td.innerText.replace(/\s+/g, " ").trim()),
  paras: [...tr.querySelectorAll("td p")].map(p => p.innerText.replace(/\s+/g, " ").trim()),
  links: [...tr.querySelectorAll("a")].map(a => a.getAttribute("href") || ""),
})))
"""

FULLSCREEN_ARGS = re.compile(r"popfullscreenEx\d?\('(\d+)',\s*'(\d+)'")
NOTICE_ARGS = re.compile(r"goRevisionInfoDetail\('(\d+)'\)")


_call = js_call


def _strip_tags(text: str) -> str:
    # 검색 결과의 조문 제목에 HTML 조각이 글자 그대로 섞여 나오는 경우가 있다
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>?", "", text)).strip()


class KofiaAdapter(Adapter):
    site = "KOFIA"
    selectors_key = "kofia"
    parser_version = 2  # 2: 모범규준식 번호("8."), 조문 안 .partition, 별표/서식 목록 제외

    # --- 카탈로그 ---

    def crawl_catalog(self, previous: list[Regulation] | None = None, with_dates: bool = True) -> list[Regulation]:
        tcfg = self.sel["tree"]
        self.page.goto(self.url("tree"))
        nodes = self.page.evaluate(_call(TREE_JS, tcfg))
        if not nodes:
            shot = self.page.screenshot(name="kofia-tree")
            raise cdp.CDPError(f"KOFIA 규정 트리를 읽지 못했습니다(스크린샷: {shot}). selectors.kofia.json의 tree 항목을 확인하세요.")
        idx = tcfg["arg_index"]
        prev = {r.reg_id: r for r in previous or [] if r.site == self.site}
        regs: dict[str, Regulation] = {}
        for node in nodes:
            a = node["args"]
            reg_id, name = a[idx["reg_id"]], a[idx["name"]]
            if reg_id in regs:  # 여러 분류에 걸린 규정
                regs[reg_id].extra.setdefault("other_categories", []).append(node["path"])
                continue
            rev = a[idx["revision_id"]] if len(a) > idx["revision_id"] else ""
            law_type = a[idx["law_type"]] if len(a) > idx["law_type"] else "1"
            reg = Regulation(
                site=self.site, reg_id=reg_id, name=name, category=node["path"],
                kind=KIND_RULE if normalize_name(name).endswith("시행세칙") else KIND_MAIN,
                revision_id=rev if rev not in ("", "0", "null") else None,
                url=self.url("view", reg_id=reg_id),
                extra={"law_type": tcfg["law_types"].get(law_type, law_type)},
            )
            if law_type == "3" and len(a) > idx["url"]:
                reg.url = a[idx["url"]] or reg.url
            regs[reg_id] = reg

        result = list(regs.values())
        if with_dates:
            todo = []
            for reg in result:
                old = prev.get(reg.reg_id)
                if old and old.revision_id == reg.revision_id and old.revision_date:
                    reg.revision_date = old.revision_date
                elif reg.extra["law_type"] == "일반":
                    todo.append(reg)
            for i, reg in enumerate(todo, 1):
                if i == 1 or i % 10 == 0 or i == len(todo):
                    print(f"[kofia] 개정일 조회 {i}/{len(todo)}", file=sys.stderr, flush=True)
                info = self.current_revision_date(reg)
                reg.revision_date = info.revision_date
                reg.revision_id = info.revision_id or reg.revision_id
        return result

    # --- 본문 ---

    def open_regulation(self, reg: Regulation) -> RegulationText:
        if reg.extra.get("law_type", "일반") != "일반":
            raise NotSupported(f"'{reg.name}'은(는) KOFIA에서 {reg.extra['law_type']} 형태라 조문을 읽을 수 없습니다: {reg.url}")
        rev = reg.revision_id or self.current_revision_date(reg).revision_id
        self.page.goto(self.url("content", reg_id=reg.reg_id, revision_id=rev))
        data = self.page.evaluate(_call(CONTENT_JS, self.sel["content"]))
        if not data or not data["articles"]:
            shot = self.page.screenshot(name=f"kofia-content-{reg.reg_id}")
            raise cdp.CDPError(f"'{reg.name}' 본문을 읽지 못했습니다(스크린샷: {shot}). selectors.kofia.json의 content 항목을 확인하세요.")

        fullscreen = self.url("fullscreen", reg_id=reg.reg_id, revision_id=rev)
        articles = []
        for a in data["articles"]:
            number, title, rest = split_article_heading(a["heading"]) if a["heading"] else ("", "", "")
            lines = ([rest] if rest else []) + a["lines"]
            url = (self.url("article_print", reg_id=reg.reg_id, revision_id=rev, content_seq=a["content_seq"])
                   if a["content_seq"] and a["addenda"] is None else fullscreen)
            articles.append(Article(number=number, title=title, text="\n".join(lines), path=a["path"],
                                    addenda=a["addenda"], url=url, extra={"content_seq": a["content_seq"]}))
        # 본문 연혁의 마지막 날짜와 사이트 연혁 목록(#p_sel)의 날짜가 하루 다를 때가 있다
        # (예: 매매거래계좌설정약관 2026-03-18 / 2026-03-17). 개정일은 연혁 목록 기준으로 통일하고
        # 본문 날짜는 따로 남긴다. 개정 여부 판단은 날짜가 아니라 revision_id로 한다.
        text_date = parse_date(data["history"][-1]) if data["history"] else None
        same_rev = reg.revision_id == rev and reg.revision_date
        meta = Regulation.from_dict({**reg.to_dict(), "revision_id": rev,
                                     "revision_date": reg.revision_date if same_rev else text_date})
        meta.extra = {**reg.extra, "fullscreen_url": fullscreen, "text_history_date": text_date,
                      "attachments": data.get("attachments", [])}
        return RegulationText(regulation=meta, articles=articles)

    def current_revision_date(self, reg: Regulation) -> RevisionInfo:
        if reg.extra.get("law_type", "일반") != "일반":
            return RevisionInfo(reg.revision_id, reg.revision_date)
        self.page.goto(self.url("fullscreen", reg_id=reg.reg_id, revision_id=0))
        options = self.page.evaluate(_call(SELECT_JS, self.sel["revision_select"]))
        if not options:
            shot = self.page.screenshot(name=f"kofia-revision-{reg.reg_id}")
            raise cdp.CDPError(f"'{reg.name}' 연혁 목록을 읽지 못했습니다(스크린샷: {shot}).")
        history = [{"revision_id": v, "revision_date": parse_date(t)} for v, t, _ in options]
        current = next((h for h, (_, _, selected) in zip(history, options) if selected), history[0])
        return RevisionInfo(current["revision_id"], current["revision_date"], history)

    # --- 목록 화면 공통 ---

    def _rows(self) -> list[dict]:
        return self.page.evaluate(_call(ROWS_JS, self.sel["list_rows"])) or []

    def _goto_list_page(self, n: int) -> bool:
        find = f"[...document.querySelectorAll({json.dumps(self.sel['pager_link'])})].find(a => a.textContent.trim() === '{n}')"
        if not self.page.evaluate(f"!!{find}"):
            return False
        self.page.navigate_by_js(f"{find}.click()")
        return True

    # --- 검색 ---

    def search(self, query: str, scope: dict | None = None) -> list[SearchHit]:
        """scope: field(content|title|attach, 기본 content), history(연혁 포함, 기본 False), max_pages(기본 1)"""
        scope = scope or {}
        s = self.sel["search"]
        field = scope.get("field", "content")
        if field not in s["fields"]:
            raise ValueError(f"field는 {list(s['fields'])} 중 하나: {field}")
        time_value = s["time_values"]["history" if scope.get("history") else "current"]
        self.page.goto(self.url("search_start"))
        self.page.navigate_by_js(
            "(() => {"
            f" document.querySelector({json.dumps(s['value'])}).value = {json.dumps(query, ensure_ascii=False)};"
            f" document.querySelector({json.dumps(s['field'])}).value = {json.dumps(field)};"
            f" document.querySelectorAll({json.dumps(s['time_radio'])}).forEach(r => r.checked = r.value === {json.dumps(time_value)});"
            f" {s['submit']}; }})()"
        )
        hits: list[SearchHit] = []
        for page_no in range(1, int(scope.get("max_pages", 1)) + 1):
            if page_no > 1 and not self._goto_list_page(page_no):
                break
            for row in self._rows():
                if len(row["cells"]) < 3 or not row["paras"]:
                    continue  # "검색 결과가 없습니다" 같은 안내 줄
                m = next((FULLSCREEN_ARGS.search(l) for l in row["links"] if FULLSCREEN_ARGS.search(l)), None)
                reg_id, rev = (m[1], m[2]) if m else (None, None)
                paras = [_strip_tags(p) for p in row["paras"]]
                hits.append(SearchHit(
                    site=self.site, name=re.sub(r"\s*\[[^\]]*\]$", "", paras[0]),  # 규정명 검색은 "이름 [분류]"
                    article_heading=paras[1] if len(paras) > 2 else "",
                    snippet=paras[-1] if len(paras) > 1 else "",
                    revision_date=parse_date(row["cells"][2]), reg_id=reg_id, revision_id=rev,
                    url=self.url("fullscreen", reg_id=reg_id, revision_id=rev) if m else self.url("search_start"),
                ))
        return hits

    # --- 최근 변경 ---

    def recent_changes(self, days: int = 30, max_pages: int = 20) -> list[Change]:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        today = date.today().isoformat()
        changes: list[Change] = []

        # 최신제ㆍ개정정보: 번호, 제목, 개정구분, 제ㆍ개정일, 다운로드, 전체화면
        self.page.goto(self.url("revision_list"))
        for page_no in range(1, max_pages + 1):
            if page_no > 1 and not self._goto_list_page(page_no):
                break
            done = False
            for row in self._rows():
                c = row["cells"]
                if len(c) < 4:
                    continue
                d = parse_date(c[3])
                if d and d < cutoff:
                    done = True
                    break
                m = next((FULLSCREEN_ARGS.search(l) for l in row["links"] if FULLSCREEN_ARGS.search(l)), None)
                changes.append(Change(
                    site=self.site, kind="제개정", name=c[1], change_type=c[2], date=d,
                    reg_id=m[1] if m else None, revision_id=m[2] if m else None,
                    url=self.url("fullscreen", reg_id=m[1], revision_id=m[2]) if m else self.url("revision_list"),
                ))
            if done:
                break

        # 규정 제ㆍ개정 예고: 번호, 제목, 구분, 예고시작일, 예고종료일, 다운로드
        self.page.goto(self.url("notice_list"))
        for page_no in range(1, max_pages + 1):
            if page_no > 1 and not self._goto_list_page(page_no):
                break
            done = False
            for row in self._rows():
                c = row["cells"]
                if len(c) < 5:
                    continue
                start, end = parse_date(c[3]), parse_date(c[4])
                recent = (start and start >= cutoff) or (end and end >= today)
                if not recent:
                    done = True
                    break
                m = next((NOTICE_ARGS.search(l) for l in row["links"] if NOTICE_ARGS.search(l)), None)
                changes.append(Change(
                    site=self.site, kind="예고", name=c[1], change_type=c[2], date=start, end_date=end,
                    url=self.url("notice_download", notice_seq=m[1]) if m else self.url("notice_list"),
                ))
            if done:
                break
        return changes


# ---------------------------------------------------------------------------
# 명령줄
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse

    import store

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="KOFIA 법규정보시스템 조회")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("catalog")
    p.add_argument("--no-dates", action="store_true", help="규정별 개정일 조회를 건너뜀(빠름)")
    p.add_argument("--out", help="결과를 이 파일에 JSON으로 저장")
    p = sub.add_parser("article")
    p.add_argument("regulation")
    p.add_argument("number", nargs="?")
    p.add_argument("--title")
    p = sub.add_parser("revision")
    p.add_argument("regulation")
    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--field", default="content")
    p.add_argument("--history", action="store_true")
    p.add_argument("--pages", type=int, default=1)
    p = sub.add_parser("recent")
    p.add_argument("--days", type=int, default=30)
    args = ap.parse_args(argv)

    def dump(obj) -> None:
        print(json.dumps(obj, ensure_ascii=False, indent=2))

    with KofiaAdapter() as site:
        if args.cmd == "catalog":
            catalog = site.crawl_catalog(with_dates=not args.no_dates)
            data = [r.to_dict() for r in catalog]
            if args.out:
                store.write_json(args.out, data)
                print(f"{len(data)}건 저장: {args.out}")
            else:
                dump(data)
        elif args.cmd in ("article", "revision"):
            reg = pick_regulation(site.crawl_catalog(with_dates=False), args.regulation)
            if args.cmd == "revision":
                dump(site.current_revision_date(reg).to_dict())
            elif args.number or args.title:
                text = site.open_regulation(reg)
                art = text.find(args.number, args.title)
                dump({"regulation": text.regulation.to_dict(), "article": art.to_dict() if art else None})
            else:
                text = site.open_regulation(reg)
                dump({"regulation": text.regulation.to_dict(),
                      "articles": [{"number": a.number, "title": a.title, "path": a.path}
                                   for a in text.articles if a.addenda is None]})
        elif args.cmd == "search":
            dump([h.to_dict() for h in site.search(args.query, {"field": args.field, "history": args.history,
                                                                 "max_pages": args.pages})])
        elif args.cmd == "recent":
            dump([c.to_dict() for c in site.recent_changes(args.days)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
