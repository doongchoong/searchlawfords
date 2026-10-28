"""법규 검색 스킬 배포 zip 만들기(개발자용 — 배포본에는 들어가지 않는다).

    python tools/package.py [--refresh-catalog] [--skip-tests] [--out dist]

1. 시드 카탈로그: 사용자 데이터의 catalog.json을 스킬 폴더 seed/catalog.json으로 복사한다.
   --refresh-catalog면 먼저 세 사이트에서 다시 수집한다(1~2분).
2. 동봉 Python(runtime/python)으로 오프라인 테스트를 돌린다. 실패하면 멈춘다.
3. 스킬 폴더를 dist/regulation-search-<버전>.zip으로 묶는다. __pycache__·tests는 빼고 VERSION.txt를 넣는다.
4. 검증: 임시 폴더에 풀어
   ① 다른 위치·다른 작업 폴더에서도 동봉 Python으로 모든 모듈이 import되고 환경 점검(오프라인)이 통과하는지,
   ② 사례를 하나 확정한 뒤 같은 zip으로 덮어써도 사례집·카탈로그가 그대로 남는지 확인한다.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".claude" / "skills" / "regulation-search"
PY = SKILL / "runtime" / "python" / "python.exe"
EXCLUDE_DIRS = {"__pycache__", "tests"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


def run(cmd: list, **kw) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", **kw.pop("env", {})}
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, encoding="utf-8", env=env, **kw)


def data_dir() -> Path:
    base = os.environ.get("REGSEARCH_DATA_DIR") or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "regulation-search"
    return Path(base)


def refresh_seed(refresh: bool) -> dict:
    if refresh:
        r = run([PY, SKILL / "scripts" / "catalog.py", "refresh"])
        if r.returncode:
            raise SystemExit(f"카탈로그 갱신 실패:\n{r.stdout}\n{r.stderr}")
    src = data_dir() / "catalog.json"
    if not src.exists():
        raise SystemExit(f"사용자 카탈로그가 없습니다: {src} (--refresh-catalog로 수집하세요)")
    data = json.loads(src.read_text(encoding="utf-8"))
    shutil.copyfile(src, SKILL / "seed" / "catalog.json")
    return {site: {"count": len(v["regulations"]), "updated_at": v.get("updated_at")} for site, v in data["sites"].items()}


def files_to_pack() -> list[Path]:
    out = []
    for p in sorted(SKILL.rglob("*")):
        rel = p.relative_to(SKILL)
        if p.is_dir() or any(part in EXCLUDE_DIRS for part in rel.parts) or p.suffix in EXCLUDE_SUFFIXES:
            continue
        out.append(p)
    return out


def build_zip(out_dir: Path, version: str, seed: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    zpath = out_dir / f"regulation-search-{version}.zip"
    py_version = run([PY, "-c", "import sys; print(sys.version.split()[0])"]).stdout.strip()
    version_txt = "\n".join([
        f"법규 검색 스킬 {version}",
        f"만든 시각: {datetime.now().isoformat(timespec='seconds')}",
        f"동봉 Python: {py_version}",
        "시드 카탈로그: " + ", ".join(f"{s} {v['count']}건({(v['updated_at'] or '')[:10]})" for s, v in seed.items()),
    ]) + "\n"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in files_to_pack():
            z.write(p, f"regulation-search/{p.relative_to(SKILL).as_posix()}")
        z.writestr("regulation-search/VERSION.txt", version_txt)
    return zpath


def verify(zpath: Path) -> list[str]:
    """임시 위치에 설치해 보고 확인한 항목을 돌려준다. 실패하면 SystemExit."""
    checks = []
    tmp = Path(tempfile.mkdtemp(prefix="regsearch-pkg-"))
    try:
        skills, data, elsewhere = tmp / "skills", tmp / "data", tmp / "cwd"
        elsewhere.mkdir()
        with zipfile.ZipFile(zpath) as z:
            z.extractall(skills)
        sk = skills / "regulation-search"
        py = sk / "runtime" / "python" / "python.exe"
        env = {"REGSEARCH_DATA_DIR": str(data)}

        names = [n for n in zipfile.ZipFile(zpath).namelist()]
        bad = [n for n in names if "__pycache__" in n or "/tests/" in n]
        if bad:
            raise SystemExit(f"빼야 할 파일이 들어감: {bad[:5]}")
        checks.append(f"zip 안 파일 {len(names)}개, __pycache__·tests 없음")

        r = run([py, "-c", "import store, cdp, cache, cases, catalog, lookup, match, eval; "
                           "from adapters import kofia, krx, lawgo; print('ok')"], cwd=elsewhere, env=env)
        if r.stdout.strip() != "ok":
            raise SystemExit(f"설치 위치에서 import 실패:\n{r.stdout}\n{r.stderr}")
        checks.append("다른 위치·다른 작업 폴더에서 동봉 Python으로 모든 모듈 import")

        r = run([py, sk / "scripts" / "check_env.py", "--offline"], cwd=elsewhere, env=env)
        if r.returncode:
            raise SystemExit(f"설치 위치에서 환경 점검 실패:\n{r.stdout}\n{r.stderr}")
        checks.append("설치 위치에서 환경 점검(오프라인) 통과")

        # 첫 사용: 시드 카탈로그가 사용자 데이터로 복사되고, 사례 하나 확정
        first = ("import catalog, cases\n"
                 "regs = catalog.load()\n"
                 "cases.confirm({'주제': '패키지 검증용 사례', '결과': '근거확인', "
                 "'경로': [{'사이트': 'KRX', '규정': '시장감시규정', '조문': '제3조', '조문제목': '회원의 금지행위'}]})\n"
                 "print(len(regs), len(cases.load()))")
        r = run([py, "-c", first], cwd=elsewhere, env=env)
        count_regs, count_cases = (r.stdout.split() + ["0", "0"])[:2]
        if r.returncode or int(count_cases) != 1 or int(count_regs) < 100:
            raise SystemExit(f"첫 사용 시뮬레이션 실패:\n{r.stdout}\n{r.stderr}")
        catalog_before = (data / "catalog.json").read_bytes()
        checks.append(f"첫 사용: 시드 카탈로그 {count_regs}건이 사용자 데이터로 복사, 사례 1건 확정")

        # 업데이트: 같은 위치에 덮어쓰기
        shutil.rmtree(sk)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(skills)
        r = run([py, "-c", "import cases; print(len(cases.load()))"], cwd=elsewhere, env=env)
        if r.stdout.strip() != "1" or (data / "catalog.json").read_bytes() != catalog_before:
            raise SystemExit(f"덮어쓰기 뒤 사용자 데이터가 바뀜:\n{r.stdout}\n{r.stderr}")
        checks.append("스킬 폴더를 지우고 다시 풀어도 사례집·카탈로그 그대로")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return checks


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="법규 검색 스킬 배포 zip")
    ap.add_argument("--refresh-catalog", action="store_true", help="시드로 넣기 전에 세 사이트 카탈로그를 다시 수집")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "dist"))
    args = ap.parse_args()

    if not PY.exists():
        raise SystemExit(f"동봉 Python이 없습니다: {PY}")
    print("1. 시드 카탈로그", flush=True)
    seed = refresh_seed(args.refresh_catalog)
    for site, v in seed.items():
        print(f"   {site}: {v['count']}건 (갱신 {v['updated_at']})")

    if not args.skip_tests:
        print("2. 오프라인 테스트(동봉 Python)", flush=True)
        r = run([PY, "-m", "unittest", "discover", "-s", "tests"], cwd=SKILL)
        last = (r.stderr or r.stdout).strip().splitlines()[-3:]
        print("   " + " / ".join(last))
        if r.returncode:
            raise SystemExit("테스트 실패로 중단합니다.\n" + r.stderr[-3000:])

    version = datetime.now().strftime("%Y.%m.%d-%H%M")
    print("3. zip 만들기", flush=True)
    zpath = build_zip(Path(args.out), version, seed)
    print(f"   {zpath} ({zpath.stat().st_size / 1024 / 1024:.1f} MB)")

    print("4. 설치 검증", flush=True)
    for c in verify(zpath):
        print(f"   ✓ {c}")
    print(f"\n완료: {zpath}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
