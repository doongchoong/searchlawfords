"""데이터 파일 쓰기의 단일 창구.

여러 Claude Code 세션이 같은 데이터 폴더를 동시에 써도 파일이 깨지지 않도록
모든 쓰기는 이 모듈을 거친다. 표준 라이브러리만 쓴다.

- data_dir(): 사용자 데이터 폴더 (스킬 폴더 밖. 스킬을 덮어써도 남는다)
- lock(name): 프로세스 간 파일 락
- write_text / write_json: 임시 파일에 쓴 뒤 교체(atomic write)
- update_json: 락을 잡은 채 읽고-고치고-쓰기
- append_jsonl: 락을 잡고 한 줄 추가 (기존 줄은 건드리지 않음)
- throttle(key): 모든 세션이 공유하는 최소 요청 간격
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterator

DEFAULT_MIN_INTERVAL = float(os.environ.get("REGSEARCH_MIN_INTERVAL", "2.0"))


class LockTimeout(TimeoutError):
    pass


def data_dir() -> Path:
    """사용자 데이터 폴더. REGSEARCH_DATA_DIR로 바꿀 수 있다."""
    env = os.environ.get("REGSEARCH_DATA_DIR")
    if env:
        base = Path(env)
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "regulation-search"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "regulation-search"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _locks_dir() -> Path:
    d = data_dir() / ".locks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]", "_", name)


def _lock_name(path: Path) -> str:
    # 데이터 폴더 안의 파일은 상대 경로로, 밖의 파일은 절대 경로로 락 이름을 만든다
    try:
        rel = path.resolve().relative_to(data_dir().resolve())
        return "file-" + _safe(rel.as_posix())
    except ValueError:
        return "file-" + _safe(str(path.resolve()))


if os.name == "nt":
    import msvcrt

    def _try_lock(fh) -> bool:
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fh) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(fh) -> bool:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fh) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def lock(name: str, timeout: float = 60.0) -> Iterator[None]:
    """이름 단위 프로세스 간 락. 같은 프로세스 안에서 같은 이름을 중첩해 잡지 않는다."""
    path = _locks_dir() / f"{_safe(name)}.lock"
    fh = open(path, "a+b")
    try:
        deadline = time.monotonic() + timeout
        while not _try_lock(fh):
            if time.monotonic() > deadline:
                raise LockTimeout(f"락 대기 시간 초과: {name}")
            time.sleep(0.05)
        try:
            yield
        finally:
            _unlock(fh)
    finally:
        fh.close()


def _replace(src: str, dst: Path) -> None:
    # Windows에서는 다른 프로세스가 대상 파일을 읽는 중이면 교체가 잠깐 실패한다
    for attempt in range(100):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == 99:
                raise
            time.sleep(0.05)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        _replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _read_bytes(path: Path) -> bytes:
    for attempt in range(100):
        try:
            return path.read_bytes()
        except PermissionError:
            if attempt == 99:
                raise
            time.sleep(0.05)
    raise AssertionError("unreachable")


def write_text(path: str | os.PathLike, text: str) -> None:
    path = Path(path)
    with lock(_lock_name(path)):
        _atomic_write(path, text.encode("utf-8"))


def write_json(path: str | os.PathLike, obj: Any) -> None:
    write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def read_json(path: str | os.PathLike, default: Any = None) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(_read_bytes(path).decode("utf-8"))


def update_json(path: str | os.PathLike, fn: Callable[[Any], Any], default: Any = None) -> Any:
    """락을 잡은 채 JSON을 읽어 fn(값)의 결과로 교체하고, 새 값을 돌려준다."""
    path = Path(path)
    with lock(_lock_name(path)):
        current = json.loads(_read_bytes(path).decode("utf-8")) if path.exists() else default
        new = fn(current)
        _atomic_write(path, (json.dumps(new, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        return new


def append_jsonl(path: str | os.PathLike, record: dict) -> None:
    """한 줄을 추가한다. 기존 줄은 그대로 두고 파일 전체를 원자적으로 교체한다."""
    path = Path(path)
    line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    with lock(_lock_name(path)):
        existing = _read_bytes(path) if path.exists() else b""
        if existing and not existing.endswith(b"\n"):
            existing += b"\n"
        _atomic_write(path, existing + line)


def read_jsonl(path: str | os.PathLike) -> list[dict]:
    """깨진 줄은 경고만 남기고 건너뛴다(사람이 직접 고친 파일일 수 있으므로)."""
    path = Path(path)
    if not path.exists():
        return []
    records = []
    for no, raw in enumerate(_read_bytes(path).decode("utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            records.append(json.loads(raw))
        except json.JSONDecodeError as e:
            print(f"[store] {path.name}:{no} 줄을 읽지 못해 건너뜀: {e}", file=sys.stderr)
    return records


def throttle(key: str = "global", min_interval: float | None = None) -> None:
    """모든 세션이 공유하는 최소 요청 간격을 지킨다. 필요하면 그만큼 잠든다."""
    interval = DEFAULT_MIN_INTERVAL if min_interval is None else min_interval
    name = f"throttle-{_safe(key)}"
    stamp = _locks_dir() / f"{name}.last"
    with lock(name, timeout=max(60.0, interval * 60)):
        try:
            last = float(_read_bytes(stamp).decode())
        except (OSError, ValueError):
            last = 0.0
        wait = last + interval - time.time()
        if wait > 0:
            time.sleep(wait)
        _atomic_write(stamp, repr(time.time()).encode())
