"""사이트 어댑터 공통 인터페이스와 데이터 모델.

세 어댑터(kofia, krx, lawgo)는 Adapter를 상속해 같은 메서드를 구현한다. 사이트 차이는
어댑터 안과 references/selectors.<사이트>.json에 가두고, 상위 로직(매칭·사례·출력)은
여기 정의된 메서드와 데이터 모델만 쓴다. 모든 모델은 to_dict/from_dict로 JSON 저장이 된다.

    with KofiaAdapter() as site:
        catalog = site.crawl_catalog()
        text = site.open_regulation(catalog[0])
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any

import cdp

REFERENCES = Path(__file__).resolve().parents[2] / "references"

KIND_MAIN = "본규정"
KIND_RULE = "시행세칙"
KIND_ANNEX = "별표"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def parse_date(text: str | None) -> str | None:
    """'2026. 10. 15', '2026-10-15', '2026.10.15.' 등을 YYYY-MM-DD로 바꾼다."""
    if not text:
        return None
    m = re.search(r"(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})", text)
    return f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}" if m else None


def js_call(js_fn: str, *args) -> str:
    """'(cfg) => {...}' 꼴의 JS 함수 소스를 인자(JSON)로 호출하는 식을 만든다."""
    return f"{js_fn.strip()}({', '.join(json.dumps(a, ensure_ascii=False) for a in args)})"


def normalize_name(name: str) -> str:
    """규정명 비교용: 공백과 가운뎃점 차이를 없앤다."""
    return re.sub(r"[\s·ㆍ・]", "", name)


def normalize_article_number(number: str) -> str:
    """'2-1', '제 2-1 조', '제3조의 2' → '제2-1조', '제3조의2'"""
    s = re.sub(r"\s", "", number)
    m = re.fullmatch(r"제?([\d\-]+)조?(?:의(\d+))?", s)
    if not m:
        return s
    return f"제{m[1]}조" + (f"의{m[2]}" if m[2] else "")


def split_article_heading(heading: str) -> tuple[str, str, str]:
    """'제2-1조(목적)' → ('제2-1조', '목적', ''); '제5조 삭제 <2010.1.1>' → ('제5조', '', '삭제 <2010.1.1>');
    모범규준식 '8. 투자자정보 파악' → ('8.', '투자자정보 파악', '')"""
    h = re.sub(r"\s+", " ", heading).strip()
    m = re.match(r"(제\s*[\d\-]+\s*조(?:\s*의\s*\d+)?)\s*(.*)$", h)
    if not m:
        n = re.match(r"(\d+(?:-\d+)*)\.\s+(\S.*)$", h)
        return (f"{n[1]}.", n[2], "") if n else ("", "", h)
    number, rest = normalize_article_number(m[1]), m[2].strip()
    if rest.startswith("("):
        depth = 0
        for i, ch in enumerate(rest):
            depth += ch == "("
            depth -= ch == ")"
            if depth == 0:
                return number, rest[1:i].strip(), rest[i + 1:].strip()
    return number, "", rest


class _Model:
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict):
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


@dataclass
class Regulation(_Model):
    site: str                       # "KOFIA" | "KRX" | "법제처"
    reg_id: str                     # 사이트 안 규정 ID (KOFIA: lawSeq)
    name: str
    category: list[str]             # 분류 경로. 예: ["표준약관"]
    kind: str = KIND_MAIN           # 본규정 | 시행세칙 | 별표
    revision_id: str | None = None  # 사이트 안 개정판 ID (KOFIA: historySeq)
    revision_date: str | None = None
    url: str = ""                   # 사람이 열어 볼 원문 진입 URL
    collected_at: str = field(default_factory=now_iso)
    extra: dict = field(default_factory=dict)


@dataclass
class Article(_Model):
    number: str                     # "제2-1조", 부칙 단일 문단이면 ""
    title: str                      # 조문 제목. 번호가 밀려도 이걸로 다시 찾는다
    text: str                       # 항·호·목을 줄바꿈으로 이은 본문
    path: list[str] = field(default_factory=list)  # 편·장·절 제목
    addenda: str | None = None      # 부칙이면 "부 칙 (2008. 12. 30)"
    url: str | None = None          # 조문 단위 원문 링크(없으면 규정 링크)
    extra: dict = field(default_factory=dict)

    @property
    def heading(self) -> str:
        if self.number.endswith((".", "]")):  # 모범규준식 번호 "8.", 첨부 "[참고1]"
            return f"{self.number} {self.title}".strip()
        return f"{self.number}({self.title})" if self.title else self.number


@dataclass
class RegulationText(_Model):
    regulation: Regulation
    articles: list[Article]
    collected_at: str = field(default_factory=now_iso)

    @classmethod
    def from_dict(cls, d: dict) -> "RegulationText":
        return cls(Regulation.from_dict(d["regulation"]),
                   [Article.from_dict(a) for a in d["articles"]], d.get("collected_at", now_iso()))

    def find(self, number: str | None = None, title: str | None = None,
             include_addenda: bool = False) -> Article | None:
        """번호로 먼저 찾고, 없으면 제목(정확히 → 포함)으로 찾는다."""
        body = [a for a in self.articles if include_addenda or a.addenda is None]
        if number:
            n = normalize_article_number(number)
            plain = re.sub(r"^제|조$|\.$", "", number.strip())  # "8", "8.", "제8조" → "8"
            for a in body:
                if a.number == n or (a.number.endswith(".") and a.number[:-1] == plain):
                    return a
        if title:
            t = re.sub(r"\s", "", title)
            for exact in (True, False):
                for a in body:
                    at = re.sub(r"\s", "", a.title)
                    if at and (at == t if exact else t in at):
                        return a
        return None


@dataclass
class RevisionInfo(_Model):
    revision_id: str | None
    revision_date: str | None
    history: list[dict] = field(default_factory=list)  # [{"revision_id", "revision_date"}], 최신순


@dataclass
class SearchHit(_Model):
    site: str
    name: str                       # 규정명
    article_heading: str            # "제30조(영업의 일반원칙)", 규정명 검색이면 ""
    snippet: str
    revision_date: str | None
    reg_id: str | None
    revision_id: str | None
    url: str
    extra: dict = field(default_factory=dict)


@dataclass
class Change(_Model):
    site: str
    kind: str                       # "제개정" | "예고"
    name: str
    change_type: str                # 제정·개정·전문개정·폐지 등 사이트 표기 그대로
    date: str | None                # 제개정일 또는 예고 시작일
    end_date: str | None = None     # 예고 종료일
    reg_id: str | None = None
    revision_id: str | None = None
    url: str = ""


class NotSupported(cdp.CDPError):
    """이 규정은 사이트에서 조문 단위 본문을 제공하지 않는다(파일만 제공 등)."""


def load_selectors(site_key: str) -> dict:
    return json.loads((REFERENCES / f"selectors.{site_key}.json").read_text(encoding="utf-8"))


def find_pair(reg: Regulation, catalog: list[Regulation]) -> Regulation | None:
    """본규정이면 시행세칙을, 시행세칙이면 본규정을 이름으로 찾는다."""
    same_site = [r for r in catalog if r.site == reg.site and r.reg_id != reg.reg_id]
    base = normalize_name(reg.name)
    if reg.kind == KIND_RULE:
        target = base.removesuffix("시행세칙")
        return next((r for r in same_site if normalize_name(r.name) == target), None)
    return next((r for r in same_site if normalize_name(r.name) == base + "시행세칙"), None)


def pick_regulation(catalog: list[Regulation], key: str) -> Regulation:
    """명령줄용: 규정ID 또는 규정명(정확히 → 포함)으로 하나를 고른다."""
    found = [r for r in catalog if r.reg_id == key]
    if not found:
        k = normalize_name(key)
        found = [r for r in catalog if normalize_name(r.name) == k] or \
                [r for r in catalog if k in normalize_name(r.name)]
    if not found:
        raise SystemExit(f"규정을 찾지 못함: {key}")
    if len(found) > 1:
        names = ", ".join(f"{r.reg_id}:{r.name}" for r in found[:10])
        raise SystemExit(f"여러 규정이 맞음. 규정ID로 다시 지정하세요: {names}")
    return found[0]


class Adapter(ABC):
    site: str = ""
    selectors_key: str = ""
    # 본문 파싱 방식이 바뀌면 올린다. 캐시에 적힌 버전과 다르면 개정판이 같아도 다시 받는다.
    parser_version: int = 1

    def __init__(self, browser: cdp.Browser | None = None):
        self.sel = load_selectors(self.selectors_key)
        self._browser = browser
        self._owns_browser = browser is None
        self._page: cdp.Page | None = None

    @property
    def page(self) -> cdp.Page:
        if self._page is None:
            if self._browser is None:
                self._browser = cdp.Browser.launch()
            self._page = self._browser.new_page()
        return self._page

    def url(self, key: str, /, **kw: Any) -> str:
        return self.sel["base_url"] + self.sel["urls"][key].format(**kw)

    def close(self) -> None:
        if self._owns_browser and self._browser is not None:
            self._browser.close()
        self._browser = self._page = None

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- 공통 인터페이스 ---

    @abstractmethod
    def crawl_catalog(self, previous: list[Regulation] | None = None, with_dates: bool = True) -> list[Regulation]:
        """규정 목록. with_dates면 규정마다 개정일도 조회하는데, previous를 주면
        개정판이 그대로인 규정은 이전 개정일을 그대로 쓴다(증분 갱신)."""

    @abstractmethod
    def open_regulation(self, reg: Regulation) -> RegulationText:
        """규정 하나의 조문 전체."""

    def get_article(self, reg: Regulation, number: str | None = None, title: str | None = None,
                    include_addenda: bool = False) -> Article | None:
        """조문 하나. 번호가 개정으로 밀렸을 수 있으니 가능하면 제목도 함께 준다."""
        return self.open_regulation(reg).find(number, title, include_addenda)

    def open_cached(self, reg: Regulation, verify: bool = True) -> RegulationText:
        """캐시를 먼저 쓰되, verify면 사이트의 현재 개정판을 확인해 다를 때만 다시 받는다(설계 원칙 "캐시를 믿지 않는다").

        캐시에서 꺼낸 결과는 regulation.extra["from_cache"]가 True이고 collected_at은 처음 수집한 때다.
        """
        import cache

        cached = cache.get(self.site, reg.reg_id)
        if cached and cached.regulation.extra.get("parser", 1) != self.parser_version:
            cached = None  # 옛 파서로 읽은 본문
        target = reg
        if verify:
            info = self.current_revision_date(reg)
            if cached and info.revision_id and cached.regulation.revision_id == info.revision_id:
                cached.regulation.extra["from_cache"] = True
                return cached
            target = Regulation.from_dict({**reg.to_dict(),
                                           "revision_id": info.revision_id or reg.revision_id,
                                           "revision_date": info.revision_date or reg.revision_date})
        elif cached:
            cached.regulation.extra["from_cache"] = True
            return cached
        from adapters.rough import refine_text

        text = refine_text(self.open_regulation(target))  # 사이트 파서가 못 나눈 덩어리를 다시 나눈다
        text.regulation.extra["parser"] = self.parser_version
        cache.put(text)
        return text

    def open_pair(self, reg: Regulation, catalog: list[Regulation]) -> list[RegulationText]:
        """본규정과 시행세칙을 한 쌍으로 연다(짝이 없으면 하나만)."""
        texts = [self.open_regulation(reg)]
        pair = find_pair(reg, catalog)
        if pair is not None:
            texts.append(self.open_regulation(pair))
        return texts

    @abstractmethod
    def current_revision_date(self, reg: Regulation) -> RevisionInfo:
        """사이트상 현재 개정판과 개정일. 답변 전 라이브 검증용이라 가볍게 조회한다."""

    @abstractmethod
    def search(self, query: str, scope: dict | None = None) -> list[SearchHit]:
        """사이트 통합검색. scope의 키는 사이트마다 다르다."""

    @abstractmethod
    def recent_changes(self, days: int = 30) -> list[Change]:
        """최근 days일 안의 제·개정과 현재 진행 중이거나 최근 시작한 개정예고."""
