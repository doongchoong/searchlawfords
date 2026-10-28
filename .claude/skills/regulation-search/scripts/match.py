"""질문 → 후보 규정·조문 (6단계 매칭).

질문을 이해하는 일은 이 스크립트를 부르는 Claude가 한다(SKILL.md "질문 해석"). Claude가 규정 용어로 바꾼
핵심어·짚은 규정·사이트 검색어·업무영역을 힌트(--terms/--regs/--search/--areas)로 넘기면 그 값으로 찾고,
힌트가 없을 때만 아래 1단계의 규칙 기반 키워드 추출(조사·어미 떼기)을 쓴다. 이 스크립트는 찾기와 근거 제시를 맡는다.

1. 질의 정규화: 키워드를 뽑고(또는 힌트를 받고) aliases.json으로 약칭을 정식 규정명·검색어로 바꾼다.
2. 유사 사례: cases.jsonl에서 키워드·주제가 비슷한 확정 사례를 꺼내 그 경로를 후보로 올린다.
3. 카탈로그 퍼지 매칭: 규정명 유사도로 후보를 뽑고 본규정·시행세칙(법제처는 법·시행령·시행규칙)으로 넓힌다.
4. 법제처 범위 좁히기: 업무영역을 가려 scope.json 화이트리스트에서 그 영역 법령만 후보로 올린다.
5. 조문 검색: 후보의 본문 캐시에서 로컬 검색(조문 제목 가중, 흔한 말은 IDF로 낮춤). 캐시 전체도 훑어 이름은
   안 맞아도 본문이 맞는 규정을 찾는다. site_search면 세 사이트 통합검색을 가장 구체적인 검색어로 돌려 걸린
   규정·조문을 후보로 올리고, fetch>0이면 캐시가 없는 상위 후보를 사이트에서 받아 온다.
6. 탐색 슬롯: 최종 후보에 사례와 무관하게 뽑힌 후보를 항상 하나 이상 넣는다.

모든 후보에는 "왜 이 후보인지" 이유가 붙는다. 외부 라이브러리 없이 문자 2-gram과 키워드 겹침만 쓴다.
가중치·임계값은 references/match.json.

    python match.py "고객 주문 없이 매매하면 안 되는 근거" [--fetch 3] [--verify] [--json]
    python match.py "투자자정보 만료되면 뭘 챙겨야 해" --terms "투자자정보,유효기간,재파악" \
        --regs "표준투자권유준칙" --search "투자자정보 유효기간" --online --json
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, field

import cache as cache_mod
import cases as cases_mod
import catalog as catalog_mod
from adapters.base import REFERENCES, Regulation, RegulationText, find_pair, normalize_name

# 조사·어미. 길이가 긴 것부터 떼어 보고, 떼고 남은 말이 두 글자 이상일 때만 떼며, 원형도 함께 남긴다.
SUFFIXES = sorted(["으로부터", "에서는", "에게서", "으로서", "으로써", "이라는", "에서", "에게", "한테", "까지", "부터",
                   "으로", "라는", "이란", "에는", "와의", "과의", "은", "는", "이", "가", "을", "를", "에", "의", "로",
                   "와", "과", "도", "만", "란",
                   # 서술격 조사(이다) 꼴: "관리종목인 종목", "고객이면"
                   "인지", "인데", "이고", "이면", "이라", "이다", "이며", "인",
                   "하려면", "하면", "하는", "하여", "해야", "하고", "하지", "한다", "된다", "되는", "되면", "했을",
                   "하게", "하기", "해서", "할때", "할", "한", "된",
                   # 되다 꼴: "제한돼", "금지되나요"
                   "되나요", "되는지", "돼요", "되나", "돼"], key=len, reverse=True)
STOP = {"관련", "관한", "대한", "대해", "대하여", "규정", "조항", "조문", "근거", "내용", "경우", "때", "것", "등",
        "및", "또는", "어떻게", "무엇", "뭐", "알려줘", "알려주세요", "찾아줘", "있나", "있는지", "있나요", "해야",
        "하는", "하면", "되는", "안", "되나요", "되는지", "여부", "확인", "증권사", "회사", "준수", "사항", "어디",
        "무슨", "어떤", "해당", "하나요", "하는지", "필요", "기준", "방법", "의무", "뭐야", "뭔가요", "먼저",
        "한다", "한다는", "하라는", "된다는", "회사가", "규정이", "조문을", "있어", "있다는", "없는", "종류",
        "정리", "정리해줘", "신경써야", "챙겨야", "알려줄래", "궁금"}


def load_json(name: str) -> dict:
    return json.loads((REFERENCES / name).read_text(encoding="utf-8"))


def nospace(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def bigrams(s: str) -> set[str]:
    s = nospace(s).lower()
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else ({s} if s else set())


def coverage(query_bg: set[str], target: str) -> float:
    """target의 2-gram 중 질문에 들어 있는 비율(긴 질문이 짧은 이름을 덮는 정도)."""
    t = bigrams(target)
    return len(query_bg & t) / len(t) if t else 0.0


def dice(a: str, b: str) -> float:
    A, B = bigrams(a), bigrams(b)
    return 2 * len(A & B) / (len(A) + len(B)) if A and B else 0.0


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    for tok in re.findall(r"[가-힣A-Za-z0-9·\-]+", text):
        forms = [tok]
        for j in SUFFIXES:
            if tok.endswith(j) and len(tok) - len(j) >= 2:
                forms.append(tok[:-len(j)])
                break
        if len(forms) > 1 and forms[1] in STOP:  # "규정이", "확인해야"처럼 떼고 나면 불용어인 말은 통째로 버린다
            continue
        for f in forms:
            if len(f) >= 2 and f not in STOP and f not in out:
                out.append(f)
    return out


def core_terms(tokens: list[str]) -> list[str]:
    """tokenize 결과에서 조사·어미를 뗀 형태만 남긴다("매매거래에서" → "매매거래"만). 사례 키워드 저장용."""
    out = []
    for t in tokens:
        if any(u != t and t.startswith(u) and t[len(u):] in SUFFIXES for u in tokens):
            continue
        out.append(t)
    return out


def snippet(text: str, words: list[str], width: int = 60) -> str:
    flat = re.sub(r"\s+", " ", text)
    pos = min((p for p in (flat.find(w) for w in words) if p >= 0), default=0)
    start = max(0, pos - width // 3)
    return ("…" if start else "") + flat[start:start + width] + ("…" if start + width < len(flat) else "")


# ---------------------------------------------------------------------------
# 1. 질의 정규화
# ---------------------------------------------------------------------------

@dataclass
class Query:
    text: str
    keywords: list[str]                       # 질문에서 뽑은 말(힌트가 있으면 힌트 핵심어가 앞)
    terms: list[str]                          # 조문·캐시 검색에 쓰는 말(힌트가 있으면 힌트 기준)
    aliases: list[tuple[str, list[str]]]      # (약칭, 정식 규정명들)
    bigrams: set[str] = field(repr=False, default_factory=set)
    hints: "Hints | None" = None
    name_terms: list[str] = field(default_factory=list)  # 규정명 매칭용: 힌트 + 질문 낱말("유가증권시장" 등)


@dataclass
class Hints:
    """Claude가 질문을 읽고 정한 해석. 모두 '어디를 찾을지'일 뿐 답이 아니다(답은 라이브 본문으로)."""
    terms: list[str] = field(default_factory=list)        # 규정에 실제로 쓰이는 용어로 바꾼 핵심어
    regulations: list[str] = field(default_factory=list)  # 짚은 규정: 정식명·약칭, "사이트:이름"도 됨
    search: list[str] = field(default_factory=list)       # 사이트 통합검색에 넣을 말
    areas: list[str] = field(default_factory=list)        # scope.json 업무영역

    def empty(self) -> bool:
        return not (self.terms or self.regulations or self.search or self.areas)

    def to_dict(self) -> dict:
        return {"핵심어": self.terms, "규정": self.regulations, "검색어": self.search, "업무영역": self.areas}

    @classmethod
    def from_dict(cls, d: dict | None) -> "Hints | None":
        if not d:
            return None
        h = cls(list(d.get("핵심어") or d.get("terms") or []), list(d.get("규정") or d.get("regulations") or []),
                list(d.get("검색어") or d.get("search") or []), list(d.get("업무영역") or d.get("areas") or []))
        return None if h.empty() else h


def split_list(values: list[str] | None) -> list[str]:
    """명령줄 값: 여러 번 주거나 쉼표로 이어 줄 수 있다."""
    out = []
    for v in values or []:
        out += [x.strip() for x in v.split(",") if x.strip()]
    return out


def normalize_query(text: str, aliases: dict | None = None, hints: Hints | None = None) -> Query:
    aliases = aliases if aliases is not None else load_json("aliases.json")
    hint_text = " ".join(hints.terms + hints.regulations) if hints else ""
    ntext = nospace(text + " " + hint_text)
    found, rest = [], ntext
    for key in sorted(aliases.get("regulations", {}), key=lambda k: -len(nospace(k))):
        nk = nospace(key)
        if nk and nk in rest:
            found.append((key, aliases["regulations"][key]))
            rest = rest.replace(nk, " ")
    auto = tokenize(text)
    if hints and hints.terms:
        # 힌트 핵심어를 주로 쓴다. 질문에서 기계적으로 뽑은 말("만료되는" 등)은 조문 검색에 넣지 않는다.
        hint_words = core_terms(tokenize(" ".join(hints.terms)))
        terms = list(dict.fromkeys(hints.terms + hint_words))
        keywords = list(dict.fromkeys(hint_words + core_terms(auto)))
    else:
        keywords, terms = auto, list(auto)
    for key, vals in aliases.get("terms", {}).items():
        if nospace(key).lower() in ntext.lower():
            terms += [v for v in vals if v not in terms]
    # 조문 검색은 힌트 위주로 하되, 규정명에는 질문의 낱말("유가증권시장 업무규정"의 "유가증권시장")이 결정적이다
    name_terms = list(dict.fromkeys(terms + core_terms(auto)))
    return Query(text, keywords, terms, found, bigrams(text), hints, name_terms)


# ---------------------------------------------------------------------------
# 후보
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    site: str
    name: str
    reg_id: str | None
    kind: str = ""
    reg_score: float = 0.0
    article_norm: float = 0.0
    score: float = 0.0
    sources: list[str] = field(default_factory=list)   # case | catalog | pair | area | cache | explore
    reasons: list[str] = field(default_factory=list)
    articles: list[dict] = field(default_factory=list)
    preferred_titles: list[str] = field(default_factory=list)  # 사례가 가리킨 조문 제목
    text_from: str | None = None                                # cache | fetched
    regulation: dict | None = None

    @property
    def key(self) -> tuple[str, str]:
        return self.site, self.reg_id or normalize_name(self.name)

    @property
    def label(self) -> str:
        return f"{self.site}:{self.name}"


class Pool:
    def __init__(self):
        self.items: dict[tuple[str, str], Candidate] = {}

    def add(self, c: Candidate) -> Candidate:
        cur = self.items.get(c.key)
        if cur is None:
            self.items[c.key] = c
            return c
        cur.reg_score = max(cur.reg_score, c.reg_score)
        for s in c.sources:
            if s not in cur.sources:
                cur.sources.append(s)
        cur.reasons += [r for r in c.reasons if r not in cur.reasons]
        cur.preferred_titles += [t for t in c.preferred_titles if t not in cur.preferred_titles]
        cur.regulation = cur.regulation or c.regulation
        return cur

    def values(self) -> list[Candidate]:
        return list(self.items.values())


def _from_reg(reg: Regulation, score: float, source: str, reason: str) -> Candidate:
    return Candidate(site=reg.site, name=reg.name, reg_id=reg.reg_id, kind=reg.kind, reg_score=round(score, 3),
                     sources=[source], reasons=[reason], regulation=reg.to_dict())


# ---------------------------------------------------------------------------
# 2. 유사 사례
# ---------------------------------------------------------------------------

def similar_cases(q: Query, case_list: list[dict], cfg: dict) -> list[dict]:
    qn = nospace(q.text)
    qterms = {nospace(t) for t in q.terms}
    out = []
    for c in case_list:
        kws = [k for k in c.get("키워드", []) if nospace(k)]
        hits = [k for k in kws if nospace(k) in qn or nospace(k) in qterms]
        # 사례 키워드가 "호가입력의 제한"처럼 붙어 있으면 통째로는 안 맞으니 낱말("호가", "입력", "제한")로도 비교한다
        words = set(core_terms(tokenize(" ".join(kws + [c.get("주제", "")]))))
        hits += [w for w in core_terms(q.keywords) if w in words and w not in hits
                 and not any(w in nospace(h) for h in hits)]
        kw_score = min(1.0, len(hits) / 3)
        text_score = dice(q.text, c.get("주제", ""))
        score = cfg["w_keyword"] * kw_score + cfg["w_text"] * text_score
        if score < cfg["min_score"]:
            continue
        reasons = []
        if hits:
            reasons.append(f"확정 사례 {c.get('id')}와 키워드 {len(hits)}개 일치({', '.join(hits[:3])})")
        if text_score >= 0.3:
            reasons.append(f"사례 주제와 문장 유사도 {text_score:.2f}")
        if c.get("확인횟수", 1) > 1:
            reasons.append(f"{c['확인횟수']}번 확정됨(최근 {c.get('최근확정일')})")
        if c.get("개정이후미확인"):
            reasons.append("개정 이후 미확인: " + "; ".join(c["개정이후미확인"]))
        out.append({"case": c, "score": round(score, 3), "reasons": reasons})
    return sorted(out, key=lambda x: -x["score"])[: cfg["top"]]


def case_candidates(case_hits: list[dict], regs: list[Regulation], cfg: dict) -> list[Candidate]:
    by_name = {(r.site, normalize_name(r.name)): r for r in regs}
    out = []
    for hit in case_hits:
        c = hit["case"]
        for step in c.get("경로", []):
            site = step.get("사이트")
            name = step.get("규정") or step.get("법령")
            if not site or not name:
                continue
            title = step.get("조문제목")
            reason = f"확정 사례 {c.get('id')} 경로: {site} {name}" + (f" '{title}'" if title else "")
            stale = [d for d in c.get("개정이후미확인", []) if name in d]
            if stale:
                reason += " — 개정 이후 미확인, 조문 제목으로 위치를 다시 확인할 것"
            reg = by_name.get((site, normalize_name(name)))
            cand = (_from_reg(reg, cfg["candidate_base"] * hit["score"] / max(cfg["w_keyword"] + cfg["w_text"], 1e-9) + 0.3, "case", reason)
                    if reg else Candidate(site=site, name=name, reg_id=None, reg_score=0.3, sources=["case"],
                                          reasons=[reason + " (카탈로그에 없음)"]))
            if title:
                cand.preferred_titles.append(title)
            out.append(cand)
    return out


# ---------------------------------------------------------------------------
# 3. 카탈로그 퍼지 매칭
# ---------------------------------------------------------------------------

def match_catalog(q: Query, regs: list[Regulation], cfg: dict) -> list[Candidate]:
    alias_of = {normalize_name(n): key for key, names in q.aliases for n in names}
    qname = normalize_name(q.text)
    terms = [t for t in (q.name_terms or q.terms) if len(nospace(t)) >= 2]
    out = []
    for r in regs:
        nn = normalize_name(r.name)
        score, reasons = 0.0, []
        if nn in alias_of:
            score += cfg["w_alias"]
            reasons.append(f"약칭 '{alias_of[nn]}' → 정식명 일치")
        elif len(nn) >= 4 and nn in qname:
            score += cfg["w_alias"]
            reasons.append("질문에 규정명이 그대로 있음")
        kw_hits = [t for t in terms if nospace(t) in nn]
        if kw_hits:
            score += cfg["w_keyword"] * min(1.0, len(kw_hits) / 2)
            reasons.append(f"규정명에 '{', '.join(kw_hits[:3])}' 포함")
        cov = coverage(q.bigrams, r.name)
        if cov >= 0.3:
            score += cfg["w_ngram"] * cov
            reasons.append(f"규정명 글자 유사도 {cov:.2f}")
        if score >= cfg["min_score"]:
            c = _from_reg(r, score, "catalog", reasons[0])
            c.reasons = reasons
            out.append(c)
    return sorted(out, key=lambda c: -c.reg_score)[: cfg["top"]]


def law_family(reg: Regulation, regs: list[Regulation]) -> list[Regulation]:
    base = re.sub(r"(시행령|시행규칙)$", "", normalize_name(reg.name))
    return [r for r in regs if r.site == "법제처" and r.reg_id != reg.reg_id and r.kind != "행정규칙"
            and re.sub(r"(시행령|시행규칙)$", "", normalize_name(r.name)) == base]


def expand_pairs(cands: list[Candidate], regs: list[Regulation], cfg: dict) -> list[Candidate]:
    out = []
    for c in cands:
        if not c.regulation:
            continue
        reg = Regulation.from_dict(c.regulation)
        partners = law_family(reg, regs) if reg.site == "법제처" and reg.kind != "행정규칙" else \
            [p for p in [find_pair(reg, regs)] if p]
        for p in partners:
            out.append(_from_reg(p, c.reg_score * cfg["pair_ratio"], "pair", f"'{reg.name}'의 짝({p.kind or '시행세칙'})"))
    return out


# ---------------------------------------------------------------------------
# 4. 법제처 범위 좁히기
# ---------------------------------------------------------------------------

def classify_areas(q: Query, case_hits: list[dict], cfg: dict) -> list[dict]:
    qn = nospace(q.text)
    qterms = {nospace(t) for t in q.terms}
    scores: dict[str, dict] = {}
    for area, kws in cfg["area_keywords"].items():
        hits = [k for k in kws if nospace(k) in qn or nospace(k) in qterms]
        if hits:
            scores[area] = {"area": area, "score": len(hits), "reasons": [f"키워드 '{', '.join(hits[:3])}'"]}
    for hit in case_hits:
        for area in hit["case"].get("업무영역", []):
            e = scores.setdefault(area, {"area": area, "score": 0, "reasons": []})
            e["score"] += 1
            e["reasons"].append(f"확정 사례 {hit['case'].get('id')}의 업무영역")
    return sorted(scores.values(), key=lambda e: -e["score"])


def area_candidates(areas: list[dict], regs: list[Regulation], cfg: dict) -> list[Candidate]:
    out = []
    for a in areas:
        # 영역 키워드가 여럿 맞을수록 그 영역 법령을 조금 더 올린다
        score = min(cfg["area_max"], cfg["area_base"] + cfg["area_per_extra_hit"] * (a["score"] - 1))
        laws = [r for r in regs if r.site == "법제처" and a["area"] in r.category][: cfg["max_per_area"]]
        out += [_from_reg(r, score, "area", f"업무영역 '{a['area']}'({'; '.join(a['reasons'])}) → 법제처 화이트리스트")
                for r in laws]
    return out


# ---------------------------------------------------------------------------
# 5. 조문 검색
# ---------------------------------------------------------------------------

def term_weights(texts: list[RegulationText], terms: list[str], floor: float = 0.15) -> dict[str, float]:
    """조문 전체에서 흔한 말일수록 낮은 가중치(IDF를 0~1로). 본문이 없으면 모두 1."""
    bodies = [nospace(a.title + a.text) for t in texts for a in t.articles if a.addenda is None and a.number]
    n = len(bodies)
    if n < 20:
        return {}
    import math
    out = {}
    for term in terms:
        nt = nospace(term)
        df = sum(1 for b in bodies if nt in b)
        out[term] = round(max(floor, math.log((n + 1) / (df + 1)) / math.log(n + 1)), 3)
    return out


def search_articles(text: RegulationText, q: Query, cfg: dict, preferred: list[str] | None = None,
                    weights: dict[str, float] | None = None) -> list[dict]:
    terms = [t for t in q.terms if len(nospace(t)) >= 2]
    weights = weights or {}
    cap = cfg["body_hit_cap"]
    # "~의 정의/뜻"을 묻는 질문이면 정의 조항 꼴(“투자권유”란)이 있는 조문을 올린다
    wants_definition = bool(re.search(r"정의|뜻|의미|란\s*무엇", q.text))
    hits = []
    for a in text.articles:
        if a.addenda is not None or not a.number:
            continue
        tn, bn = nospace(a.title), nospace(a.text)
        score, matched = 0.0, []
        for t in terms:
            nt = nospace(t)
            w = weights.get(t, 1.0)
            in_title = nt in tn
            count = bn.count(nt)
            if in_title:
                score += cfg["w_title"] * w
            if count:
                score += cfg["w_body"] * w * min(count, cap) / cap
            if (in_title or count) and t not in matched:
                matched.append(t)
            if wants_definition and count and re.search(rf"[“\"']{re.escape(nt)}[”\"']\s*(?:이)?란", bn):
                score += cfg["w_title"] * w
        if len(tn) >= 4:  # "목적" 같은 짧은 제목은 질문 글자 조각("투자목적")에 우연히 덮인다
            score += cfg["w_ngram_title"] * coverage(q.bigrams, a.title)
        pref = any(p and nospace(p) == tn for p in preferred or [])
        if pref:
            score += cfg["w_title"] * 2
        if a.extra.get("parse") == "block":  # 글자 수로 자른 블록은 여러 주제가 섞여 말이 잘 걸리므로 덜 친다
            score *= cfg.get("w_block", 1.0)
        if score >= cfg["min_score"] or pref:
            hits.append({"number": a.number, "title": a.title, "heading": a.heading, "score": round(score, 2),
                         "matched": matched, "snippet": snippet(a.text, matched), "url": a.url,
                         "path": a.path, "from_case": pref, **({"parse": a.extra["parse"]} if a.extra.get("parse") else {})})
    return sorted(hits, key=lambda h: -h["score"])[: cfg["top"]]


def article_norm(hits: list[dict], cfg: dict) -> float:
    if not hits:
        return 0.0
    return round(min(1.0, hits[0]["score"] / (cfg["w_title"] + cfg["w_body"] + cfg["w_ngram_title"])), 3)


# ---------------------------------------------------------------------------
# 전체 흐름
# ---------------------------------------------------------------------------

@dataclass
class MatchResult:
    query: Query
    cases: list[dict]
    areas: list[dict]
    candidates: list[Candidate]
    notes: list[str]
    considered: list[dict] = field(default_factory=list)  # 점수가 낮아도 Claude가 읽고 판단할 사례

    def first_candidates(self) -> list[str]:
        """사례집의 '처음후보' 필드용(hit@k 평가)."""
        return [c.label for c in self.candidates]

    def to_dict(self) -> dict:
        q = self.query
        return {
            "query": {"text": q.text, "keywords": q.keywords, "terms": q.terms,
                      "aliases": [{"alias": a, "names": n} for a, n in q.aliases],
                      "hints": q.hints.to_dict() if q.hints else None},
            "cases": [{"id": h["case"].get("id"), "주제": h["case"].get("주제"), "score": h["score"],
                       "reasons": h["reasons"], "서술": h["case"].get("서술"),
                       "개정이후미확인": h["case"].get("개정이후미확인", [])} for h in self.cases],
            # 점수 문턱 아래까지 넓게: 같은 주제인지는 Claude가 주제·서술을 읽고 판단한다
            "cases_considered": [{"id": h["case"].get("id"), "주제": h["case"].get("주제"), "score": h["score"],
                                  "서술": h["case"].get("서술")} for h in self.considered],
            "areas": self.areas,
            "candidates": [{k: v for k, v in asdict(c).items() if k != "regulation"} |
                           {"url": (c.regulation or {}).get("url"),
                            "revision_date": (c.regulation or {}).get("revision_date")}
                           for c in self.candidates],
            "notes": self.notes,
        }


def find_candidates(question: str, *, regs: list[Regulation] | None = None, case_list: list[dict] | None = None,
                    texts: list[RegulationText] | None = None, fetch: int = 0, verify: bool = False,
                    site_search: bool = False, browser=None, cfg: dict | None = None,
                    aliases: dict | None = None, hints: Hints | None = None) -> MatchResult:
    """질문으로 최종 후보를 고른다. regs/case_list/texts를 주지 않으면 카탈로그·사례집·캐시에서 읽는다.
    hints는 Claude가 질문을 해석한 결과(핵심어·규정·검색어·업무영역)로, 주면 자동 키워드 추출보다 우선한다.
    site_search면 사이트 통합검색을 돌리고, fetch>0이면 본문이 없는 상위 fetch개 후보를 사이트에서 받는다
    (verify면 현재 개정판도 확인). 둘 다 브라우저를 쓴다."""
    cfg = cfg or load_json("match.json")
    aliases = aliases if aliases is not None else load_json("aliases.json")
    regs = catalog_mod.load() if regs is None else regs
    case_list = cases_mod.load() if case_list is None else case_list
    cases_mod.annotate_stale(case_list, regs)
    texts = list(cache_mod.iter_all()) if texts is None else texts
    notes: list[str] = []
    hints = hints if hints and not hints.empty() else None

    q = normalize_query(question, aliases, hints)
    case_hits = similar_cases(q, case_list, cfg["cases"])
    considered = similar_cases(q, case_list, {**cfg["cases"], "min_score": cfg["cases"]["list_min"],
                                              "top": cfg["cases"]["list_top"]})
    pool = Pool()
    for c in case_candidates(case_hits, regs, cfg["cases"]):
        pool.add(c)
    catalog_hits = match_catalog(q, regs, cfg["catalog"])
    hinted = hint_candidates(hints, regs, aliases, cfg["hints"], notes)
    for c in catalog_hits + hinted:
        pool.add(c)
    for c in expand_pairs(catalog_hits + hinted + [c for c in pool.values() if "case" in c.sources],
                          regs, cfg["catalog"]):
        pool.add(c)
    areas = classify_areas(q, case_hits, cfg)
    for area in hints.areas if hints else []:
        if area not in [a["area"] for a in areas]:
            areas.append({"area": area, "score": 2, "reasons": ["질문 해석에서 정한 업무영역"]})
    for c in area_candidates(areas, regs, cfg["laws"]):
        pool.add(c)

    # 5. 본문 캐시: 후보에 붙이고, 이름은 안 맞아도 본문이 맞는 규정을 새 후보로
    acfg = cfg["articles"]
    weights = term_weights(texts, q.terms)
    for t in texts:
        key = (t.regulation.site, t.regulation.reg_id)
        cand = pool.items.get(key)
        hits = search_articles(t, q, acfg, cand.preferred_titles if cand else None, weights)
        norm = article_norm(hits, acfg)
        if cand is None:
            if norm < cfg["cache_search"]["min_norm"]:
                continue
            cand = pool.add(_from_reg(t.regulation, 0.0, "cache",
                                      f"캐시 본문 {hits[0]['heading']}에 '{', '.join(hits[0]['matched'][:3])}' 일치"))
        cand.articles, cand.article_norm, cand.text_from = hits, norm, "cache"

    if site_search or fetch:
        import cdp

        own = browser is None
        browser = browser or cdp.Browser.launch()
        try:
            if site_search:
                _site_search(pool, q, regs, weights, cfg["site_search"], browser, notes)
            if fetch:
                _fetch_texts(pool, q, acfg, fetch, verify, browser, notes, weights)
        finally:
            if own:
                browser.close()

    for c in pool.values():
        c.score = round(cfg["final"]["w_reg"] * c.reg_score + cfg["final"]["w_article"] * c.article_norm, 3)
    ranked = sorted(pool.values(), key=lambda c: -c.score)
    final = _with_explore_slot(ranked, cfg["final"])

    no_text = [c for c in final if c.text_from is None and c.regulation]
    if no_text:
        notes.append(f"본문을 아직 보지 않은 후보 {len(no_text)}건: " + ", ".join(c.name for c in no_text)
                     + " (fetch로 받아 조문까지 확인)")
    notes.append("검색 범위: KOFIA·KRX 현행 규정 카탈로그, 법제처는 scope.json 화이트리스트와 캐시된 본문. "
                 "법제처 전체 조문 검색은 lawgo.search로 따로 한다.")
    if not hints:
        notes.append("질문 해석 힌트 없이 규칙 기반 키워드로 찾았다(--terms/--search를 주면 더 정확하다).")
    return MatchResult(q, case_hits, areas, final, notes, considered)


def hint_candidates(hints: Hints | None, regs: list[Regulation], aliases: dict, cfg: dict,
                    notes: list[str]) -> list[Candidate]:
    """질문 해석에서 짚은 규정을 카탈로그에서 찾아 후보로 올린다. "사이트:이름"이면 그 사이트에서만 찾는다."""
    from adapters import SITES

    out = []
    for raw in hints.regulations if hints else []:
        site, _, name = raw.partition(":")
        if site not in SITES:
            site, name = "", raw
        name = name.strip()
        pool_regs = [r for r in regs if not site or r.site == site]
        found: list[Regulation] = []
        for n in aliases.get("regulations", {}).get(name, [name]):
            nn = normalize_name(n)
            found += [r for r in pool_regs if normalize_name(r.name) == nn] or \
                     [r for r in pool_regs if nn in normalize_name(r.name)][:3]
        if not found:
            notes.append(f"질문 해석에서 짚은 규정 '{raw}'을(를) 카탈로그에서 찾지 못함")
        out += [_from_reg(r, cfg["w_reg"], "hint", f"질문 해석에서 짚은 규정('{raw}')") for r in found]
    return out


def search_terms(q: Query, weights: dict[str, float], n: int) -> list[str]:
    """사이트 검색에 쓸 말. 질문 해석의 검색어가 있으면 그것을, 없으면
    용어 확장 → 흔하지 않은 키워드 → 긴 말 순으로 고른다."""
    if q.hints and q.hints.search:
        return q.hints.search[:max(n, len(q.hints.search))]
    expanded = [t for t in q.terms if t not in q.keywords]
    rest = sorted((t for t in q.keywords if len(t) >= 2 and t not in expanded),
                  key=lambda t: (-weights.get(t, 0.5), -len(t)))
    out: list[str] = []
    for t in expanded + rest:
        if t not in out and not any(nospace(t) in nospace(o) for o in out):
            out.append(t)
    return out[:n]


def _site_search(pool: Pool, q: Query, regs: list[Regulation], weights: dict[str, float], cfg: dict,
                 browser, notes: list[str]) -> None:
    import cdp
    from adapters import get_adapter

    by_name = {(r.site, normalize_name(r.name)): r for r in regs}
    by_rev = {(r.site, r.revision_id): r for r in regs if r.revision_id}
    terms = search_terms(q, weights, cfg["terms"])
    found: dict[tuple[str, str], dict] = {}
    for site_name in cfg["sites"]:
        site = get_adapter(site_name, browser)
        for term in terms:
            try:
                hits = site.search(term)
            except cdp.CDPError as e:
                notes.append(f"{site_name} 검색 '{term}' 실패: {e}")
                continue
            for h in hits:
                reg = by_name.get((h.site, normalize_name(h.name))) or by_rev.get((h.site, h.revision_id))
                if h.extra.get("status") == "시행예정":
                    continue
                key = (h.site, reg.reg_id if reg else normalize_name(h.name))
                e = found.setdefault(key, {"reg": reg, "hit": h, "terms": [], "headings": [], "count": 0})
                e["count"] += 1
                if term not in e["terms"]:
                    e["terms"].append(term)
                if h.article_heading and h.article_heading not in e["headings"]:
                    e["headings"].append(h.article_heading)
    for e in found.values():
        score = cfg["base"] + cfg["per_hit"] * min(e["count"], cfg["max_hits"])
        h = e["hit"]
        where = f": {', '.join(e['headings'][:2])}" if e["headings"] else ""
        reason = f"{h.site} 사이트 검색 '{', '.join(e['terms'])}'에 걸림{where}"
        if e["reg"]:
            cand = _from_reg(e["reg"], score, "site_search", reason)
        else:  # 법제처 화이트리스트 밖
            cand = Candidate(site=h.site, name=h.name, reg_id=h.reg_id, reg_score=round(score * 0.5, 3),
                             sources=["site_search"], reasons=[reason + " (화이트리스트 밖, 근거로 쓰기 전 확인 필요)"],
                             articles=[{"number": "", "title": "", "heading": hd, "score": 0, "matched": e["terms"],
                                        "snippet": "", "url": h.url, "path": [], "from_case": False}
                                       for hd in e["headings"][:3]])
        cand.preferred_titles = [split_heading_title(hd) for hd in e["headings"][:3]]
        pool.add(cand)


def split_heading_title(heading: str) -> str:
    m = re.match(r"제[\d\-]+조(?:의\d+)?\((.*)\)$", heading.strip())
    return m[1] if m else heading


def _fetch_texts(pool: Pool, q: Query, acfg: dict, n: int, verify: bool, browser, notes: list[str],
                 weights: dict[str, float]) -> None:
    import cdp
    from adapters import get_adapter

    todo = [c for c in sorted(pool.values(), key=lambda c: -c.reg_score) if c.text_from is None and c.regulation][:n]
    adapters: dict[str, object] = {}
    for c in todo:
        site = adapters.setdefault(c.site, get_adapter(c.site, browser))
        try:
            text = site.open_cached(Regulation.from_dict(c.regulation), verify=verify)
        except cdp.CDPError as e:
            notes.append(f"{c.label} 본문을 받지 못함: {e}")
            continue
        c.articles = search_articles(text, q, acfg, c.preferred_titles, weights)
        c.article_norm, c.text_from = article_norm(c.articles, acfg), "fetched"


def _with_explore_slot(ranked: list[Candidate], cfg: dict) -> list[Candidate]:
    k, slots = cfg["k"], cfg["explore_slots"]
    top = ranked[:k]
    free = [c for c in top if "case" not in c.sources]
    if len(free) >= slots:
        return top
    extra = [c for c in ranked[k:] if "case" not in c.sources][: slots - len(free)]
    for c in extra:
        c.sources.append("explore")
        c.reasons.append("탐색 후보: 사례와 무관하게 뽑힘(과거 경로만 따라가지 않도록)")
    return top[: k - len(extra)] + extra


def format_result(r: MatchResult) -> str:
    lines = [f"질문: {r.query.text}", f"키워드: {', '.join(r.query.keywords)}"]
    if r.query.aliases:
        lines.append("약칭: " + "; ".join(f"{a} → {', '.join(n)}" for a, n in r.query.aliases))
    if r.cases:
        lines.append("유사 사례: " + "; ".join(f"{h['case'].get('id')}({h['score']})" for h in r.cases))
    if r.areas:
        lines.append("업무영역: " + ", ".join(a["area"] for a in r.areas))
    for i, c in enumerate(r.candidates, 1):
        lines.append(f"{i}. [{c.site}] {c.name}  점수 {c.score} ({'+'.join(c.sources)})")
        for reason in c.reasons[:3]:
            lines.append(f"     - {reason}")
        for a in c.articles[:2]:
            lines.append(f"     · {a['heading']} {a['score']}  {a['snippet']}")
    lines += [f"참고: {n}" for n in r.notes]
    return "\n".join(lines)


def add_hint_args(ap) -> None:
    """질문 해석 힌트 옵션(match.py·cases.py draft가 같이 쓴다). 여러 번 주거나 쉼표로 이어 준다."""
    ap.add_argument("--terms", action="append", help="규정 용어로 바꾼 핵심어. 예: 투자자정보,유효기간")
    ap.add_argument("--regs", action="append", help="짚은 규정(정식명·약칭, '사이트:이름'). 예: 표준투자권유준칙")
    ap.add_argument("--search", action="append", help="사이트 검색어(공백 포함 가능, 여러 번). 예: --search \"투자자정보 유효기간\"")
    ap.add_argument("--areas", action="append", help="scope.json 업무영역. 예: 금융소비자보호")


def hints_from_args(args) -> Hints | None:
    search = [s.strip() for s in args.search or [] if s.strip()]
    h = Hints(split_list(args.terms), split_list(args.regs), search, split_list(args.areas))
    return None if h.empty() else h


def main(argv: list[str] | None = None) -> int:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="질문 → 후보 규정·조문")
    ap.add_argument("question")
    ap.add_argument("--fetch", type=int, default=0, help="본문 캐시가 없는 상위 N개 후보를 사이트에서 받음")
    ap.add_argument("--verify", action="store_true", help="받을 때 사이트의 현재 개정판도 확인")
    ap.add_argument("--site-search", action="store_true", help="세 사이트 통합검색도 돌림")
    ap.add_argument("--online", action="store_true", help="--site-search --fetch 3 --verify 와 같음")
    ap.add_argument("--json", action="store_true")
    add_hint_args(ap)
    args = ap.parse_args(argv)
    if args.online:
        args.site_search, args.fetch, args.verify = True, max(args.fetch, 3), True
    result = find_candidates(args.question, fetch=args.fetch, verify=args.verify, site_search=args.site_search,
                             hints=hints_from_args(args))
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2) if args.json else format_result(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
