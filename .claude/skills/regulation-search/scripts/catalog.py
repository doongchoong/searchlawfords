"""세 사이트 통합 카탈로그(<데이터>/catalog.json).

    {"sites": {"KOFIA": {"updated_at": ..., "regulations": [Regulation...]}, "KRX": {...}, "법제처": {...}}}

법제처는 scope.json 화이트리스트 법령만 들어간다. 사용자 카탈로그가 없으면 스킬 폴더의 seed/catalog.json을
복사해 쓴다(첫 실행에 크롤링 없이 바로 매칭할 수 있게). 갱신은 사이트별로, 동시에 한 세션만 한다.

    python catalog.py stats
    python catalog.py refresh [--site KOFIA] [--site KRX] [--site 법제처]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import store
from adapters import SITES, get_adapter
from adapters.base import Regulation, now_iso

SEED = Path(__file__).resolve().parents[1] / "seed" / "catalog.json"


def path() -> Path:
    return store.data_dir() / "catalog.json"


def _raw() -> dict:
    data = store.read_json(path())
    if data is None and SEED.exists():
        data = json.loads(SEED.read_text(encoding="utf-8"))
        store.write_json(path(), data)
    return data or {"sites": {}}


def load(sites: list[str] | None = None) -> list[Regulation]:
    data = _raw()["sites"]
    return [Regulation.from_dict(r) for site, v in data.items() if not sites or site in sites
            for r in v.get("regulations", [])]


def updated_at() -> dict[str, str]:
    return {site: v.get("updated_at") for site, v in _raw()["sites"].items()}


def refresh(sites: list[str] | None = None, browser=None) -> dict[str, int]:
    """사이트별로 다시 수집해 저장한다. 이전 카탈로그를 넘겨 바뀐 규정만 다시 조회한다(KOFIA)."""
    import cdp

    sites = sites or SITES
    own = browser is None
    browser = browser or cdp.Browser.launch()
    counts = {}
    try:
        with store.lock("catalog-crawl", timeout=5):
            for site in sites:
                previous = load([site])
                regs = get_adapter(site, browser).crawl_catalog(previous=previous)
                entry = {"updated_at": now_iso(), "regulations": [r.to_dict() for r in regs]}
                store.update_json(path(), lambda d, s=site, e=entry: {"sites": {**(d or {"sites": {}})["sites"], s: e}},
                                  default={"sites": {}})
                counts[site] = len(regs)
    except store.LockTimeout:
        raise SystemExit("다른 세션이 카탈로그를 갱신하는 중입니다. 끝난 뒤 다시 시도하세요.")
    finally:
        if own:
            browser.close()
    return counts


def main(argv: list[str] | None = None) -> int:
    import argparse
    from collections import Counter

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="통합 카탈로그")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats")
    p = sub.add_parser("refresh")
    p.add_argument("--site", action="append", choices=SITES)
    args = ap.parse_args(argv)
    if args.cmd == "refresh":
        print(json.dumps(refresh(args.site), ensure_ascii=False))
    regs = load()
    print(f"카탈로그: {path()}")
    for site, n in Counter(r.site for r in regs).items():
        print(f"  {site}: {n}건 (갱신 {updated_at().get(site)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
