"""한국거래소(KRX) 법무포털 어댑터.

화면 구조(2026-10-27 확인, 셀렉터는 references/selectors.krx.json):
- 직접 URL 진입이 막혀 있어(세션·CSRF 토큰) 항상 /out/index.do에서 시작한다. 토큰과 쿠키는 브라우저 세션에 맡긴다.
- 카탈로그: "KRX규정" 메뉴를 누르면 좌측에 ExtJS 비동기 트리(분류), 오른쪽 iframe(workPage)에 ExtJS 그리드
  (현행 규정 목록, 한 쪽 20건)가 뜬다. 그리드의 gridStore로 전 쪽을 읽고, 트리를 폴더별로 펼쳐 분류 경로를 붙인다.
  OBOOKID는 규정 고유 ID(개정돼도 같음), BOOKID는 개정판 ID로 쓴다.
- 본문: 첫 화면에서 사이트의 검색결과 팝업과 같은 폼(regulationViewPop.do, POST bookid)을 제출해 메인 프레임에 연다.
  연혁 레이어(.layerViewW)에 개정판별 BOOKID와 차수·날짜가 있다.
- 조문 단위 링크는 세션 없이 열리지 않아(printJoPop.do 직접 GET → 500) 원문 링크는 첫 화면 URL과 메뉴 경로로 안내한다.
- 검색: 첫 화면 통합검색. 결과는 규정 단위(조문 단위 아님)이고 첨부파일 결과가 섞인다.
- 최근 변경: "최근 제·개정규정" 메뉴(같은 그리드)와 "규정 제·개정예고" 게시판. 예고 종료일은 사이트에 없다.

직접 실행(결과는 JSON):
    python krx.py catalog [--out FILE]
    python krx.py article <규정명|규정ID> [조문번호] [--title 조문제목]
    python krx.py revision <규정명|규정ID>
    python krx.py search <검색어> [--pages N]
    python krx.py recent [--days 30]
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
import store  # noqa: E402
from adapters.base import (  # noqa: E402
    KIND_MAIN, KIND_RULE, Adapter, Article, Change, Regulation, RegulationText, RevisionInfo, SearchHit,
    js_call, normalize_name, parse_date, pick_regulation, split_article_heading,
)

HOST = "rule.krx.co.kr"

# 트리에서 아직 안 펼친 폴더 id 목록
UNLOADED_FOLDERS_JS = r"""
((cfg) => {
  const ids = [];
  Ext.getCmp(cfg.component_id).getRootNode().cascade(n => {
    if (n.attributes.catcd === cfg.folder_type && !n.isLoaded()) ids.push(n.id);
  });
  return ids;
})
"""

EXPAND_JS = r"""
((cfg, id) => new Promise((res, rej) => {
  const n = Ext.getCmp(cfg.component_id).getNodeById(id);
  if (!n) return rej("트리 노드 없음: " + id);
  n.expand(false, false, () => res(true));
}))
"""

TREE_DOCS_JS = r"""
((cfg) => {
  const out = [];
  const walk = (n, path) => n.eachChild(c => {
    const a = c.attributes;
    if (cfg.doc_types.includes(a.catcd)) out.push({bookid: String(a.bookid), obookid: String(a.obookid), path});
    else walk(c, path.concat([a.text]));
  });
  walk(Ext.getCmp(cfg.component_id).getRootNode(), []);
  return out;
})
"""

GRID_PAGE_JS = r"""
((frame, gridVar, page) => new Promise((res, rej) => {
  const s = document.getElementById(frame).contentWindow[gridVar];
  const size = parseInt(s.baseParams.pagesize || 20, 10);
  const params = Object.assign({}, s.lastOptions ? s.lastOptions.params : {}, {start: (page - 1) * size, limit: size});
  s.load({params, callback: (recs, opt, ok) => ok
    ? res({total: s.getTotalCount(), size, rows: recs.map(r => r.data)})
    : rej("목록을 불러오지 못함")});
}))
"""

POST_VIEW_JS = r"""
((action, csrfSel, bookid, noformyn) => {
  const f = document.createElement("form");
  f.method = "post";
  f.action = action;
  const add = (n, v) => { const i = document.createElement("input"); i.type = "hidden"; i.name = n; i.value = v; f.appendChild(i); };
  add("bookid", bookid);
  add("noformyn", noformyn || "N");
  const csrf = document.querySelector(csrfSel);
  if (csrf) add("_csrf", csrf.value);
  document.body.appendChild(f);
  f.submit();
})
"""

VIEW_JS = r"""
((cfg) => {
  const clean = s => (s || "").replace(/ /g, " ").replace(/[ \t\r\f\v]+/g, " ").trim();
  const blockLines = el => {
    const parts = [];
    const walk = n => {
      for (const c of n.childNodes) {
        if (c.nodeType === 3) { parts.push(c.nodeValue); continue; }
        if (c.nodeType !== 1) continue;
        if (c.tagName === "BR") { parts.push("\n"); continue; }
        const block = c.tagName === "DIV" || c.tagName === "P";
        if (block) parts.push("\n");
        walk(c);
        if (block) parts.push("\n");
      }
    };
    walk(el);
    return parts.join("").split("\n").map(clean).filter(Boolean);
  };
  const root = document.querySelector(cfg.body);
  if (!root) return null;
  const out = {
    name: clean((document.querySelector(cfg.title) || {}).textContent),
    header: clean((document.querySelector(cfg.header) || {}).textContent),
    history: [...document.querySelectorAll(cfg.history_item)].map(d => {
      const m = (d.getAttribute("onclick") || "").match(/goRegulationView\('(\d+)'/);
      // 첫 줄은 규정명, 마지막 줄이 "제17차 일부개정 2026. 01. 29. 규정 제2392호"
      return {bookid: m ? m[1] : null, label: clean((d.lastElementChild || d).textContent)};
    }),
    articles: [],
  };
  const headingSel = Object.keys(cfg.headings);
  const sel = [cfg.article, cfg.article_note, cfg.addenda, cfg.addenda_body, ...headingSel].join(",");
  const path = [];
  let cur = null, addenda = null;
  for (const el of root.querySelectorAll(sel)) {
    const h = headingSel.find(s => el.matches(s));
    if (h !== undefined) {
      const t = clean(el.textContent);
      if (!t) continue;
      const level = cfg.headings[h];
      path.length = level;
      path[level] = t;
      cur = null;
      continue;
    }
    if (el.matches(cfg.addenda)) { addenda = clean(el.textContent); path.length = 0; cur = null; continue; }
    if (el.matches(cfg.article)) {
      const strong = el.querySelector(cfg.article_heading);
      const heading = clean(strong ? strong.textContent : "");
      const lines = blockLines(el);
      if (heading && lines.length && lines[0].startsWith(heading)) {
        lines[0] = clean(lines[0].slice(heading.length));
        if (!lines[0]) lines.shift();
      }
      const wrap = el.parentElement;
      cur = {heading, lines, path: path.filter(Boolean), addenda,
             content_id: wrap && /^\d+$/.test(wrap.id) ? wrap.id : null,
             anchor: (el.querySelector("a[name]") || {}).name || null};
      out.articles.push(cur);
      continue;
    }
    if (el.matches(cfg.article_note)) { if (cur) cur.lines.push(clean(el.textContent)); continue; }
    if (el.matches(cfg.addenda_body)) {
      out.articles.push({heading: "", lines: blockLines(el), path: [], addenda, content_id: null, anchor: null});
      cur = null;
    }
  }
  return out;
})
"""

SEARCH_ITEMS_JS = r"""
((frame, itemSel) => {
  const doc = document.getElementById(frame).contentWindow.document;
  return [...doc.querySelectorAll(itemSel)].map(dl => {
    const dt = dl.querySelector("dt");
    const m = ((dt && dt.getAttribute("onclick")) || "").match(/goToView\('[^']*',\s*'(\d+)'/);
    const p = dl.querySelector("dd:not(.attachDd) p");
    return {
      name: dt ? dt.textContent.replace(/\s+/g, " ").trim() : "",
      bookid: m ? m[1] : null,
      spans: [...dl.querySelectorAll("dd:not(.attachDd) span")].map(s => s.textContent.trim()),
      snippet: p ? p.textContent.replace(/\s+/g, " ").trim() : "",
      files: [...dl.querySelectorAll("dd.attachDd p.attach")].map(x => x.textContent.trim()),
    };
  });
})
"""

NOTICE_TYPE = re.compile(r"(전부개정|일부개정|제정|개정|폐지)")


class KrxAdapter(Adapter):
    site = "KRX"
    selectors_key = "krx"

    def __init__(self, browser: cdp.Browser | None = None):
        super().__init__(browser)
        self._tree_docs: dict[str, list[str]] | None = None

    # --- 화면 이동 ---

    def _work(self) -> str:
        return f"document.getElementById({json.dumps(self.sel['work_frame'])}).contentWindow"

    def _start(self) -> None:
        self.page.goto(self.url("index"))

    def _open_menu(self, key: str) -> None:
        """첫 화면에서 메뉴를 눌러 들어가고 목록 그리드가 뜰 때까지 기다린다."""
        self._start()
        name = self.sel["menus"][key]
        self.page.navigate_by_js(
            f"[...document.querySelectorAll('a')].find(a => a.textContent.trim() === {json.dumps(name)}).click()")
        self.page.wait_for(f"{self._work()}.{self.sel['grid_var']} && {self._work()}.{self.sel['grid_var']}.getTotalCount() > 0",
                           timeout=30)

    def _grid_pages(self, max_pages: int = 50):
        """현재 메뉴 그리드를 1쪽부터 차례로 내준다(쪽마다 요청 1회)."""
        for page in range(1, max_pages + 1):
            store.throttle(HOST)
            data = self.page.evaluate(js_call(GRID_PAGE_JS, self.sel["work_frame"], self.sel["grid_var"], page))
            yield data["rows"]
            if not data["rows"] or page * data["size"] >= data["total"]:
                return

    def _load_tree(self) -> dict[str, list[str]]:
        """KRX규정 메뉴가 열린 상태에서 트리를 폴더별로 펼쳐 BOOKID → 분류 경로를 만든다."""
        tcfg = self.sel["tree"]
        self.page.wait_for(f"!!Ext.getCmp({json.dumps(tcfg['component_id'])})", timeout=20)
        for _ in range(10):  # 깊이만큼 반복
            pending = self.page.evaluate(js_call(UNLOADED_FOLDERS_JS, tcfg))
            if not pending:
                break
            for node_id in pending:
                store.throttle(HOST)
                self.page.evaluate(js_call(EXPAND_JS, tcfg, node_id))
        docs = self.page.evaluate(js_call(TREE_DOCS_JS, tcfg))
        return {d["bookid"]: d["path"] for d in docs}

    def _post_view(self, bookid: str, noformyn: str = "N") -> None:
        self._start()
        self.page.navigate_by_js(js_call(POST_VIEW_JS, self.sel["urls"]["view_post"], self.sel["csrf_input"],
                                         str(bookid), noformyn))

    def _navigation(self, category: list[str], name: str) -> str:
        return " > ".join([self.sel["menus"]["rules"], *category, name])

    def _to_regulation(self, row: dict, category: list[str]) -> Regulation:
        name = row["TITLE"].strip()
        return Regulation(
            site=self.site, reg_id=str(row["OBOOKID"]), name=name, category=category,
            kind=KIND_RULE if normalize_name(name).endswith("시행세칙") else KIND_MAIN,
            revision_id=str(row["BOOKID"]), revision_date=parse_date(row.get("PROMULDT")),
            url=self.url("index"),
            extra={
                "navigation": self._navigation(category, name),
                "bookcd": row.get("BOOKCD"), "rev_type": row.get("REVCD"), "rev_count": row.get("REVCHA"),
                "effective_date": parse_date(row.get("STARTDT")), "promul_no": row.get("PROMULNO"),
                "dept": row.get("DEPTNAME"), "noformyn": row.get("NOFORMYN") or "N",
            },
        )

    # --- 카탈로그 ---

    def crawl_catalog(self, previous: list[Regulation] | None = None, with_dates: bool = True) -> list[Regulation]:
        # 그리드에 개정일이 함께 오므로 previous·with_dates와 상관없이 요청 수가 같다
        self._open_menu("rules")
        rows = [r for page in self._grid_pages() for r in page]
        tree = self._load_tree()
        regs: dict[str, Regulation] = {}
        for row in rows:
            reg = self._to_regulation(row, tree.get(str(row["BOOKID"]), []))
            regs.setdefault(reg.reg_id, reg)
        if not regs:
            shot = self.page.screenshot(name="krx-catalog")
            raise cdp.CDPError(f"KRX 규정 목록을 읽지 못했습니다(스크린샷: {shot}). selectors.krx.json을 확인하세요.")
        return list(regs.values())

    # --- 본문 ---

    def open_regulation(self, reg: Regulation) -> RegulationText:
        if not reg.revision_id:
            raise ValueError(f"KRX 규정은 개정판 ID(BOOKID)가 있어야 열 수 있습니다. 카탈로그에서 가져오세요: {reg.name}")
        self._post_view(reg.revision_id, reg.extra.get("noformyn", "N"))
        data = self.page.evaluate(js_call(VIEW_JS, self.sel["view"]))
        if not data or not data["articles"]:
            shot = self.page.screenshot(name=f"krx-view-{reg.reg_id}")
            raise cdp.CDPError(f"'{reg.name}' 본문을 읽지 못했습니다(스크린샷: {shot}). selectors.krx.json의 view 항목을 확인하세요.")

        articles = []
        for a in data["articles"]:
            number, title, rest = split_article_heading(a["heading"]) if a["heading"] else ("", "", "")
            if not number and a["lines"]:  # 부칙 본문이 "제1조(시행일)…"으로 시작하는 경우
                number, title, rest0 = split_article_heading(a["lines"][0])
                if number:
                    a["lines"] = ([rest0] if rest0 else []) + a["lines"][1:]
            lines = ([rest] if rest else []) + a["lines"]
            articles.append(Article(number=number, title=title, text="\n".join(lines), path=a["path"],
                                    addenda=a["addenda"], url=None,
                                    extra={"content_id": a["content_id"], "anchor": a["anchor"]}))
        history = self._history(data["history"])
        meta = Regulation.from_dict(reg.to_dict())
        meta.extra = {**reg.extra, "header": data["header"], "history": history,
                      "navigation": reg.extra.get("navigation") or self._navigation(reg.category, reg.name)}
        if not meta.revision_date:
            meta.revision_date = parse_date(data["header"])
        return RegulationText(regulation=meta, articles=articles)

    @staticmethod
    def _history(items: list[dict]) -> list[dict]:
        # 최신순. 이 날짜는 시행일 기준으로 보인다(시장감시규정 시행세칙: 연혁 2026-10-02 / 목록 공포일
        # 2026-10-01). revision_date는 목록 공포일을 쓴다.
        return [{"revision_id": h["bookid"], "revision_date": parse_date(h["label"]), "label": h["label"]}
                for h in items if h.get("bookid")]

    def current_revision_date(self, reg: Regulation) -> RevisionInfo:
        """목록 그리드를 앞쪽부터 넘기며 같은 규정(OBOOKID)의 현재 개정판을 찾는다(보통 1~5회 요청)."""
        self._open_menu("rules")
        for rows in self._grid_pages():
            for row in rows:
                if str(row["OBOOKID"]) == reg.reg_id:
                    return RevisionInfo(str(row["BOOKID"]), parse_date(row.get("PROMULDT")))
        return RevisionInfo(None, None)

    # --- 검색 ---

    def search(self, query: str, scope: dict | None = None) -> list[SearchHit]:
        """scope: max_pages(기본 1). 결과는 규정 단위라 article_heading은 비어 있다."""
        scope = scope or {}
        s = self.sel["search"]
        work = self._work()
        self._start()
        self.page.navigate_by_js(
            f"(() => {{ const el = document.querySelector({json.dumps(s['input'])});"
            f" el.value = {json.dumps(query, ensure_ascii=False)};"
            f" const e = $.Event('keydown'); e.keyCode = 13; e.which = 13; $(el).trigger(e); }})()")
        hits: list[SearchHit] = []
        for page in range(1, int(scope.get("max_pages", 1)) + 1):
            if page > 1:
                origin = self.page.evaluate(f"{work}.performance.timeOrigin")
                store.throttle(HOST)
                self.page.evaluate(f"{work}.{s['next_page_fn']}({page}); true")
                self.page.wait_for(f"{work}.performance.timeOrigin !== {origin} && {work}.document.readyState === 'complete'",
                                   timeout=30)
            self.page.wait_for(f"{work}.document.readyState === 'complete' && !!{work}.document.querySelector({json.dumps(s['results_ready'])})",
                               timeout=30)
            items = self.page.evaluate(js_call(SEARCH_ITEMS_JS, self.sel["work_frame"], s["item"]))
            if not items:
                break
            for it in items:
                snippet = it["snippet"]
                if it["files"]:
                    snippet += f" [첨부파일: {', '.join(it['files'])}]"
                spans = it["spans"]  # [구분, YYYYMMDD, 부서]
                hits.append(SearchHit(
                    site=self.site, name=it["name"], article_heading="", snippet=snippet.strip(),
                    revision_date=parse_date(f"{spans[1][:4]}-{spans[1][4:6]}-{spans[1][6:8]}") if len(spans) > 1 and len(spans[1]) == 8 else None,
                    reg_id=None, revision_id=it["bookid"], url=self.url("index"),
                ))
        return hits

    # --- 최근 변경 ---

    def recent_changes(self, days: int = 30) -> list[Change]:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        changes: list[Change] = []

        self._open_menu("recent")
        for rows in self._grid_pages():
            done = False
            for row in rows:
                d = parse_date(row.get("PROMULDT"))
                if d and d < cutoff:
                    done = True
                    break
                changes.append(Change(site=self.site, kind="제개정", name=row["TITLE"].strip(),
                                      change_type=row.get("REVCD") or "", date=d,
                                      reg_id=str(row["OBOOKID"]), revision_id=str(row["BOOKID"]),
                                      url=self.url("index")))
            if done:
                break

        # 예고는 게시판이라 등록일만 있다(종료일 없음)
        self._open_menu("notices")
        for rows in self._grid_pages():
            done = False
            for row in rows:
                d = parse_date(row.get("UPDT"))
                if d and d < cutoff:
                    done = True
                    break
                title = re.sub(r"\s+", " ", row.get("TITLE") or "").strip()
                m = NOTICE_TYPE.search(title)
                changes.append(Change(site=self.site, kind="예고", name=title,
                                      change_type=m[1] if m else "", date=d, url=self.url("index")))
            if done:
                break
        return changes


# ---------------------------------------------------------------------------
# 명령줄
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="KRX 법무포털 조회")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("catalog")
    p.add_argument("--out", help="결과를 이 파일에 JSON으로 저장")
    p = sub.add_parser("article")
    p.add_argument("regulation")
    p.add_argument("number", nargs="?")
    p.add_argument("--title")
    p = sub.add_parser("revision")
    p.add_argument("regulation")
    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--pages", type=int, default=1)
    p = sub.add_parser("recent")
    p.add_argument("--days", type=int, default=30)
    args = ap.parse_args(argv)

    def dump(obj) -> None:
        print(json.dumps(obj, ensure_ascii=False, indent=2))

    with KrxAdapter() as site:
        if args.cmd == "catalog":
            data = [r.to_dict() for r in site.crawl_catalog()]
            if args.out:
                store.write_json(args.out, data)
                print(f"{len(data)}건 저장: {args.out}")
            else:
                dump(data)
        elif args.cmd in ("article", "revision"):
            reg = pick_regulation(site.crawl_catalog(), args.regulation)
            if args.cmd == "revision":
                dump(site.current_revision_date(reg).to_dict())
            else:
                text = site.open_regulation(reg)
                if args.number or args.title:
                    art = text.find(args.number, args.title)
                    dump({"regulation": text.regulation.to_dict(), "article": art.to_dict() if art else None})
                else:
                    dump({"regulation": text.regulation.to_dict(),
                          "articles": [{"number": a.number, "title": a.title, "path": a.path}
                                       for a in text.articles if a.addenda is None]})
        elif args.cmd == "search":
            dump([h.to_dict() for h in site.search(args.query, {"max_pages": args.pages})])
        elif args.cmd == "recent":
            dump([c.to_dict() for c in site.recent_changes(args.days)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
