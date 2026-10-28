"""조문 본문 캐시.

<데이터>/cache/<사이트>/<규정ID>.json 에 RegulationText를 통째로 둔다(규정마다 최신 한 판).
수집일(collected_at)이 함께 저장되고, 쓰기 전 검증은 Adapter.open_cached가 맡는다.
지워도 다시 만들어지는 파생 데이터다.

꺼낼 때 러프 파싱(adapters/rough.py) 판이 다르면 그 자리에서 다시 나눈다. 규칙만 바뀐 것이라 사이트에 다시
접속하지 않는다.
"""
from __future__ import annotations

import re
from pathlib import Path

import store
from adapters.base import RegulationText
from adapters.rough import ROUGH_VERSION, refine_text


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]', "_", name).strip("._") or "_"


def path(site: str, reg_id: str) -> Path:
    return store.data_dir() / "cache" / _safe(site) / f"{_safe(reg_id)}.json"


def _load(data: dict) -> RegulationText:
    text = RegulationText.from_dict(data)
    return text if text.regulation.extra.get("rough") == ROUGH_VERSION else refine_text(text)


def get(site: str, reg_id: str) -> RegulationText | None:
    data = store.read_json(path(site, reg_id))
    return _load(data) if data else None


def iter_all():
    """캐시에 있는 모든 본문(사이트 구분 없이). 깨진 파일은 건너뛴다."""
    root = store.data_dir() / "cache"
    for p in sorted(root.glob("*/*.json")) if root.exists() else []:
        try:
            data = store.read_json(p)
        except ValueError:
            continue
        if data:
            yield _load(data)


def put(text: RegulationText) -> Path:
    p = path(text.regulation.site, text.regulation.reg_id)
    store.write_json(p, text.to_dict())
    return p
