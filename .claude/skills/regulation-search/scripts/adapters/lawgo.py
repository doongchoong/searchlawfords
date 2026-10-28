"""법제처 국가법령정보센터 어댑터.

법령이 수천 건이라 전체 카탈로그를 받지 않는다:
① references/scope.json 화이트리스트가 이 사이트의 작은 카탈로그, ② 조문내용 검색이 기본 진입로
(화이트리스트 밖 결과는 extra.in_scope=False), ③ 한 번 연 법령은 Adapter.open_cached로 캐시해 재사용.

화면 구조(2026-10-27 확인, 셀렉터는 references/selectors.lawgo.json):
- /법령/{공백 없는 법령명}, /행정규칙/{규칙명} 은 1KB짜리 바깥 페이지이고 본문은 lawService iframe
  (lsInfoP.do?lsiSeq=…&efYd=… / admRulInfoP.do?admRulSeq=…)에 있다. 바깥 페이지를 브라우저로 열면 iframe 본문
  (자본시장법 약 3MB)까지 받으므로, 개정판 확인은 같은 출처 페이지에서 fetch로 바깥 HTML만 받아 iframe 주소를 읽는다.
  lsiSeq·admRulSeq는 개정판 ID, efYd는 시행일.
- /법령/{법령명}/제9조, /행정규칙/{규칙명}/제4-20조 가 그 조문을 바로 연다 → 조문 단위 원문 링크.
- 본문: div.lawcon(조문, span.bl에 제목, p마다 항·호·목), p.gtit(편장절관), #arDivArea(부칙).
- 검색: 법령(lsSc.do)·행정규칙(admRulSc.do) 검색 화면에서 "조문내용" 탭을 누르면 AJAX로 조문 단위 결과가 뜬다.
  같은 법령의 시행예정 개정판도 따로 나오고 "앞으로 시행될 법령" 아이콘이 붙는다.
- 행정규칙은 바깥 페이지에 시행일이 없어 본문을 열 때 머리말에서 채운다.

직접 실행(결과는 JSON):
    python lawgo.py catalog [--no-dates]
    python lawgo.py article <법령명> [조문번호] [--title 조문제목] [--no-cache]
    python lawgo.py revision <법령명>
    python lawgo.py search <검색어> [--target law|admrul|both] [--field content|title] [--in-scope]
    python lawgo.py recent [--days 30]
"""
from __future__ import annotations

import html as html_lib
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cdp  # noqa: E402
import store  # noqa: E402
from adapters.base import ( # noqa: E402
    REFERENCES, Adapter, Article, Change, Regulation, RegulationText, RevisionInfo, SearchHit,
    js_call, normalize_name, parse_date, pick_regulation, split_article_heading,
)

HOST = "www.law.go.kr"
SITE = "법제처"
ADMRUL_TYPE = "행정규칙"

FETCH_JS = r"""
((path) => fetch(path, {credentials: "same-origin"}).then(r => r.text()))
"""

CONTENT_JS = r"""
((cfg) => {
  const clean = s => (s || "").replace(/ /g, " ").replace(/\s+/g, " ").trim();
  const levelOf = t => /^제\s*\d+\s*편/.test(t) ? 0 : /^제\s*\d+\s*장/.test(t) ? 1 : /^제\s*\d+\s*절/.test(t) ? 2 : /^제\s*\d+\s*관/.test(t) ? 3 : 1;
  const m = (document.body.innerText || "").match(/\[시행[^\]]*\]\s*\[[^\]]*\]/);
  const out = {header: m ? clean(m[0]) : "", articles: []};
  const sel = [cfg.heading, cfg.article_block, cfg.addenda_label, cfg.addenda_para].join(",");
  const path = [];
  let cur = null;
  for (const el of document.querySelectorAll(sel)) {
    if (el.matches(cfg.heading)) {
      const t = clean(el.textContent);
      if (!t) continue;
      const level = levelOf(t);
      path.length = level;
      path[level] = t;
      continue;
    }
    if (el.matches(cfg.article_block)) {
      if (el.closest(cfg.addenda_area)) continue;
      const h = el.querySelector(cfg.article_heading);
      const heading = clean(h ? h.textContent : "");
      const lines = [...el.querySelectorAll("p")].map(p => clean(p.textContent)).filter(Boolean);
      if (heading && lines.length && lines[0].startsWith(heading)) {
        lines[0] = clean(lines[0].slice(heading.length));
        if (!lines[0]) lines.shift();
      }
      const group = el.closest(".pgroup");
      const anchor = group && group.previousElementSibling && group.previousElementSibling.getAttribute("name");
      out.articles.push({heading, lines, path: path.filter(Boolean), addenda: null, anchor: anchor || null});
      cur = null;
      continue;
    }
    if (el.matches(cfg.addenda_label)) {
      cur = {heading: "", lines: [], path: [], addenda: clean(el.textContent), anchor: null};
      out.articles.push(cur);
      continue;
    }
    if (cur) {
      const t = clean(el.textContent);
      if (t) cur.lines.push(t);
    }
  }
  return out;
})
"""

SEARCH_JS = r"""
((cfg) => [...document.querySelectorAll(cfg.result_item)].map(li => {
  const head = li.querySelector("div") || li;
  const headLink = head.querySelector("a");
  return {
    header: (headLink && headLink.getAttribute("title")) || head.textContent.replace(/\s+/g, " ").trim(),
    upcoming: !!head.querySelector(`img[alt="${cfg.upcoming_icon_alt}"]`),
    articles: [...li.querySelectorAll(cfg.article_link)].map(a => ({
      text: a.textContent.replace(/\s+/g, " ").trim(),
      onclick: a.getAttribute("onclick") || "",
    })),
  };
}))
"""

JO_ARGS = re.compile(r"fJoView\(([^)]*)\)")


def _section(type_: str) -> str:
    return "admrul" if type_ == ADMRUL_TYPE else "law"


def load_scope() -> dict[str, list[dict]]:
    return json.loads((REFERENCES / "scope.json").read_text(encoding="utf-8"))["areas"]


class LawgoAdapter(Adapter):
    site = SITE
    selectors_key = "lawgo"

    def __init__(self, browser: cdp.Browser | None = None):
        super().__init__(browser)
        self._on_origin = False
        self._service: dict[str, tuple[str, str | None, str | None]] = {}  # reg_id → (service_url, rev, 시행일)

    # --- 도우미 ---

    def _name_path(self, reg: Regulation) -> str:
        return re.sub(r"\s", "", reg.name)

    def _section_name(self, reg: Regulation) -> str:
        return self.sel["sections"][_section(reg.kind)]

    def outer_url(self, reg: Regulation) -> str:
        return self.url("outer", section=self._section_name(reg), name=self._name_path(reg))

    def article_url(self, reg: Regulation, number: str) -> str:
        return self.url("article", section=self._section_name(reg), name=self._name_path(reg), number=number)

    def _fetch(self, path: str) -> str:
        """같은 출처 페이지에서 fetch로 HTML만 받는다(iframe·이미지 등은 받지 않음)."""
        if not self._on_origin or urlsplit(self.page.url()).hostname != HOST:
            self.page.goto(self.url("same_origin_stub"))
            self._on_origin = True
        store.throttle(HOST)
        return self.page.evaluate(js_call(FETCH_JS, path))

    def _lookup(self, reg: Regulation) -> tuple[str, str | None, str | None]:
        """바깥 페이지의 iframe 주소에서 (본문 URL, 개정판 ID, 시행일)을 읽는다."""
        outer = self._fetch(f"/{self._section_name(reg)}/{quote(self._name_path(reg))}")
        m = re.search(self.sel["outer_iframe_src"], outer)
        if not m:
            raise cdp.CDPError(f"법제처에서 '{reg.name}'을(를) 찾지 못했습니다. 법령명이 정확한지 scope.json을 확인하세요: {self.outer_url(reg)}")
        src = html_lib.unescape(m[1])
        q = parse_qs(urlsplit(src).query)
        rp = self.sel["revision_params"]
        rev = (q.get(rp[_section(reg.kind)]) or [None])[0]
        eff = (q.get(rp["effective_date"]) or [None])[0]
        eff = f"{eff[:4]}-{eff[4:6]}-{eff[6:8]}" if eff and len(eff) == 8 else None
        service = src if src.startswith("http") else self.sel["base_url"] + "/" + src.lstrip("/")
        self._service[reg.reg_id] = (service, rev, eff)
        return service, rev, eff

    # --- 카탈로그(화이트리스트) ---

    def crawl_catalog(self, previous: list[Regulation] | None = None, with_dates: bool = True) -> list[Regulation]:
        regs = []
        for area, laws in load_scope().items():
            for law in laws:
                reg = Regulation(site=self.site, reg_id=f"{self.sel['sections'][_section(law['type'])]}/{re.sub(r'\s', '', law['name'])}",
                                 name=law["name"], category=[area], kind=law["type"], extra={"in_scope": True})
                reg.url = self.outer_url(reg)
                regs.append(reg)
        if with_dates:
            import cache

            prev = {r.reg_id: r for r in previous or [] if r.site == self.site}
            for reg in regs:
                info = self.current_revision_date(reg)
                reg.revision_id, reg.revision_date = info.revision_id, info.revision_date
                if reg.revision_date:
                    continue
                # 행정규칙은 바깥 페이지에 시행일이 없다: 같은 개정판의 이전 카탈로그나 캐시 본문에서 가져온다
                old = prev.get(reg.reg_id)
                cached = cache.get(self.site, reg.reg_id)
                for src in (old, cached.regulation if cached else None):
                    if src and src.revision_id == reg.revision_id and src.revision_date:
                        reg.revision_date = src.revision_date
                        break
        return regs

    # --- 본문 ---

    def current_revision_date(self, reg: Regulation) -> RevisionInfo:
        _, rev, eff = self._lookup(reg)
        if not eff and reg.revision_id == rev:  # 행정규칙: 바깥 페이지에 시행일이 없다
            eff = reg.revision_date
        return RevisionInfo(rev, eff)

    def open_regulation(self, reg: Regulation) -> RegulationText:
        service, rev, eff = self._service.get(reg.reg_id) or self._lookup(reg)
        if reg.revision_id and rev and reg.revision_id != rev:
            raise cdp.CDPError(f"'{reg.name}'의 현행 개정판({rev})이 요청한 개정판({reg.revision_id})과 다릅니다. 과거 개정판 조회는 아직 지원하지 않습니다.")
        self.page.goto(service, timeout=60)
        self._on_origin = True
        # 본문은 load 뒤에 AJAX로 그려진다("Loading data..."). 조문이 나타나고 개수가 1초간 그대로일 때까지 기다린다.
        block = json.dumps(self.sel["content"]["article_block"])
        self.page.wait_for(f"document.querySelectorAll({block}).length > 0", timeout=60)
        self.page.evaluate(
            f"new Promise(res => {{ let last = -1; const t = setInterval(() => {{"
            f" const n = document.querySelectorAll({block}).length; if (n === last) {{ clearInterval(t); res(n); }} last = n; }}, 1000); }})",
            timeout=60)
        data = self.page.evaluate(js_call(CONTENT_JS, self.sel["content"]), timeout=60)
        if not data or not data["articles"]:
            shot = self.page.screenshot(name="lawgo-content")
            raise cdp.CDPError(f"'{reg.name}' 본문을 읽지 못했습니다(스크린샷: {shot}). selectors.lawgo.json의 content 항목을 확인하세요.")
        articles = []
        for a in data["articles"]:
            number, title, rest = split_article_heading(a["heading"]) if a["heading"] else ("", "", "")
            lines = ([rest] if rest else []) + a["lines"]
            articles.append(Article(number=number, title=title, text="\n".join(lines), path=a["path"],
                                    addenda=a["addenda"],
                                    url=self.article_url(reg, number) if number and a["addenda"] is None else self.outer_url(reg),
                                    extra={"anchor": a["anchor"]}))
        header = data["header"]  # "[시행 2026. 10. 2.] [법률 제21857호, 2026. 8. 4., 타법개정]"
        meta = Regulation.from_dict({**reg.to_dict(), "revision_id": rev,
                                     "revision_date": eff or parse_date(header) or reg.revision_date})
        promul = re.search(r"\]\s*\[([^\]]*)\]", header)
        meta.extra = {**reg.extra, "header": header, "status": "현행", "service_url": service,
                      "promulgation": promul[1] if promul else None,
                      "in_scope": reg.extra.get("in_scope", self._in_scope(reg.name))}
        meta.url = self.outer_url(reg)
        return RegulationText(regulation=meta, articles=articles)

    def open_pair(self, reg: Regulation, catalog: list[Regulation]) -> list[RegulationText]:
        """법률·시행령·시행규칙을 함께 연다(카탈로그에 있는 것만). 행정규칙은 혼자."""
        base = re.sub(r"(시행령|시행규칙)$", "", normalize_name(reg.name))
        family = [r for r in catalog if r.site == self.site and r.kind != ADMRUL_TYPE
                  and re.sub(r"(시행령|시행규칙)$", "", normalize_name(r.name)) == base]
        if reg.kind == ADMRUL_TYPE or not family:
            family = [reg]
        order = {"법률": 0, "대통령령": 1, "총리령·부령": 2}
        return [self.open_cached(r) for r in sorted(family, key=lambda r: order.get(r.kind, 9))]

    # --- 검색 ---

    def _in_scope(self, name: str) -> bool:
        n = normalize_name(name)
        return any(normalize_name(l["name"]) == n for laws in load_scope().values() for l in laws)

    def _search_one(self, query: str, target: str, field: str) -> list[SearchHit]:
        s = self.sel["search"]
        self.page.goto(self.url("law_search" if target == "law" else "admrul_search", query=quote(query)))
        self._on_origin = True
        tab = s["tabs"][field]
        store.throttle(HOST)
        self.page.evaluate(f"[...document.querySelectorAll({json.dumps(s['tab_link'])})].find(e => e.textContent.trim() === {json.dumps(tab)}).click(); true")
        # AJAX로 결과 표가 바뀌면 머리줄에 "조문명" 칸이 생긴다
        self.page.wait_for(f"[...document.querySelectorAll('th')].some(t => t.textContent.trim() === {json.dumps(s['ready_header'])})",
                           timeout=30)
        today = date.today().isoformat()
        hits = []
        for item in self.page.evaluate(js_call(SEARCH_JS, s)):
            header = item["header"]
            name = re.split(r"\s*\[", header, maxsplit=1)[0].strip()
            eff = parse_date(re.search(r"\[시행[^\]]*\]", header)[0]) if re.search(r"\[시행[^\]]*\]", header) else None
            upcoming = item["upcoming"] or bool(eff and eff > today)
            kind = ADMRUL_TYPE if target == "admrul" else ""
            reg = Regulation(site=self.site, reg_id="", name=name, category=[], kind=kind or "법률")
            for art in item["articles"]:
                text = re.sub(r"^\d+\.\s*", "", art["text"])  # "3. 제393조 업무규정"
                m = re.match(r"(제\s*[\d\-]+\s*조(?:\s*의\s*\d+)?)\s*(.*)$", text)
                number, title = (re.sub(r"\s", "", m[1]), m[2].strip()) if m else ("", text)
                args = [a.strip().strip("'") for a in JO_ARGS.search(art["onclick"])[1].split(",")] if JO_ARGS.search(art["onclick"]) else []
                hits.append(SearchHit(
                    site=self.site, name=name, article_heading=f"{number}({title})" if number and title else (number or title),
                    snippet="", revision_date=eff, reg_id=f"{self.sel['sections'][target]}/{re.sub(r'\s', '', name)}",
                    revision_id=args[3] if len(args) > 3 else None,
                    url=self.article_url(reg, number) if number and number != "제0조" else self.outer_url(reg),
                    extra={"status": "시행예정" if upcoming else "현행", "header": header,
                           "in_scope": self._in_scope(name), "type": "행정규칙" if target == "admrul" else "법령"},
                ))
        return hits

    def search(self, query: str, scope: dict | None = None) -> list[SearchHit]:
        """scope: target(law|admrul|both, 기본 both), field(content|title, 기본 content),
        in_scope_only(기본 False — 범위 밖도 돌려주되 extra.in_scope=False로 표시).
        시행예정 개정판의 조문도 따로 나오며 extra.status로 구분한다. 시행예정 조문의 url은 현행 조문을 연다."""
        scope = scope or {}
        target, field = scope.get("target", "both"), scope.get("field", "content")
        if field not in self.sel["search"]["tabs"]:
            raise ValueError(f"field는 {list(self.sel['search']['tabs'])} 중 하나: {field}")
        targets = ["law", "admrul"] if target == "both" else [target]
        hits = [h for t in targets for h in self._search_one(query, t, field)]
        if scope.get("in_scope_only"):
            hits = [h for h in hits if h.extra["in_scope"]]
        # 화이트리스트 안, 현행을 앞으로
        return sorted(hits, key=lambda h: (not h.extra["in_scope"], h.extra["status"] != "현행"))

    # --- 최근 변경(화이트리스트 법령만) ---

    def recent_changes(self, days: int = 30) -> list[Change]:
        """화이트리스트 법령의 현행 시행일이 최근 days일 안이면 돌려준다. 시행예정 개정판은 search 결과의
        extra.status로 확인한다(이 메서드는 아직 보지 않음)."""
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        changes = []
        for reg in self.crawl_catalog(with_dates=False):
            info = self.current_revision_date(reg)
            if info.revision_date and info.revision_date >= cutoff:
                changes.append(Change(site=self.site, kind="제개정", name=reg.name, change_type="시행",
                                      date=info.revision_date, reg_id=reg.reg_id, revision_id=info.revision_id,
                                      url=self.outer_url(reg)))
        return changes


# ---------------------------------------------------------------------------
# 명령줄
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="법제처 국가법령정보센터 조회")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("catalog")
    p.add_argument("--no-dates", action="store_true")
    p = sub.add_parser("article")
    p.add_argument("regulation")
    p.add_argument("number", nargs="?")
    p.add_argument("--title")
    p.add_argument("--no-cache", action="store_true")
    p = sub.add_parser("revision")
    p.add_argument("regulation")
    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--target", default="both")
    p.add_argument("--field", default="content")
    p.add_argument("--in-scope", action="store_true")
    p = sub.add_parser("recent")
    p.add_argument("--days", type=int, default=30)
    args = ap.parse_args(argv)

    def dump(obj) -> None:
        print(json.dumps(obj, ensure_ascii=False, indent=2))

    with LawgoAdapter() as site:
        if args.cmd == "catalog":
            dump([r.to_dict() for r in site.crawl_catalog(with_dates=not args.no_dates)])
        elif args.cmd in ("article", "revision"):
            reg = pick_regulation(site.crawl_catalog(with_dates=False), args.regulation)
            if args.cmd == "revision":
                dump(site.current_revision_date(reg).to_dict())
            else:
                text = site.open_regulation(reg) if args.no_cache else site.open_cached(reg)
                if args.number or args.title:
                    art = text.find(args.number, args.title)
                    dump({"regulation": text.regulation.to_dict(), "article": art.to_dict() if art else None})
                else:
                    dump({"regulation": text.regulation.to_dict(),
                          "articles": [{"number": a.number, "title": a.title, "path": a.path}
                                       for a in text.articles if a.addenda is None]})
        elif args.cmd == "search":
            dump([h.to_dict() for h in site.search(args.query, {"target": args.target, "field": args.field,
                                                                 "in_scope_only": args.in_scope})])
        elif args.cmd == "recent":
            dump([c.to_dict() for c in site.recent_changes(args.days)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
