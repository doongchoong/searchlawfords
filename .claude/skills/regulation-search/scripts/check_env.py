"""0단계 환경 점검. 현업 PC에 스킬을 설치한 직후 한 번 실행한다.

    python check_env.py            # 사이트 접속까지 확인
    python check_env.py --offline  # 사이트 접속은 건너뜀

확인 항목: Python 버전, 데이터 폴더 쓰기, Chrome 설치, 원격 디버깅 정책,
헤드리스 기동과 페이지 제목 읽기, 디버깅 포트가 루프백에만 열리는지,
종료 후 Chrome 프로세스와 임시 프로필이 남지 않는지, (온라인) 세 사이트 접속.
"""
from __future__ import annotations

import argparse
import contextlib
import os
import socket
import subprocess
import sys
import time

MIN_PYTHON = (3, 10)
SITES = [
    ("KOFIA", "https://law.kofia.or.kr/"),
    ("KRX", "https://rule.krx.co.kr/out/index.do"),
    ("법제처", "https://www.law.go.kr/"),
]

results: list[tuple[str, bool, str]] = []


def report(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"[{'통과' if ok else '실패'}] {name}" + (f" - {detail}" if detail else ""), flush=True)
    return ok


def chrome_policy() -> str | None:
    """그룹 정책 RemoteDebuggingAllowed 값. 정책이 없으면 None."""
    if os.name != "nt":
        return None
    import winreg
    for hive, label in ((winreg.HKEY_LOCAL_MACHINE, "HKLM"), (winreg.HKEY_CURRENT_USER, "HKCU")):
        with contextlib.suppress(OSError):
            with winreg.OpenKey(hive, r"SOFTWARE\Policies\Google\Chrome") as key:
                value, _ = winreg.QueryValueEx(key, "RemoteDebuggingAllowed")
                return f"{label}={value}"
    return None


def lan_address() -> str | None:
    # 실제로 패킷을 보내지 않고 바깥으로 나가는 인터페이스 주소만 알아낸다
    with contextlib.suppress(OSError):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            addr = s.getsockname()[0]
            if not addr.startswith("127."):
                return addr
    return None


def port_reachable(host: str, port: int) -> bool:
    with contextlib.suppress(OSError):
        with socket.create_connection((host, port), timeout=1.0):
            return True
    return False


def processes_using(profile_name: str) -> int:
    """명령줄에 임시 프로필 이름이 들어간 프로세스 수(Windows만)."""
    if os.name != "nt":
        return 0
    # 이 PowerShell 자신의 명령줄에도 프로필 이름이 들어가므로 제외한다
    cmd = ("(Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and "
           "$_.CommandLine -like '*%s*' } | Measure-Object).Count" % profile_name)
    out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True)
    with contextlib.suppress(ValueError):
        return int(out.stdout.strip() or 0)
    return -1


def main() -> int:
    parser = argparse.ArgumentParser(description="법규 검색 스킬 환경 점검")
    parser.add_argument("--offline", action="store_true", help="사이트 접속 확인을 건너뜀")
    args = parser.parse_args()

    bundled = "runtime" in os.path.normcase(sys.executable).split(os.sep)
    if not report("Python 버전", sys.version_info >= MIN_PYTHON,
                  f"{sys.version.split()[0]} (필요: {'.'.join(map(str, MIN_PYTHON))} 이상) — "
                  f"{'스킬 동봉 Python' if bundled else '이 PC에 설치된 Python'}: {sys.executable}"):
        return 1

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cdp
    import store

    try:
        d = store.data_dir()
        store.write_text(d / ".write-test", "ok")
        (d / ".write-test").unlink()
        report("데이터 폴더 쓰기", True, str(d))
    except OSError as e:
        report("데이터 폴더 쓰기", False, str(e))
        return 1

    try:
        chrome = cdp.find_chrome()
        report("Chrome 설치", True, chrome)
    except FileNotFoundError as e:
        report("Chrome 설치", False, str(e))
        return 1

    policy = chrome_policy()
    blocked_by_policy = policy is not None and policy.endswith("=0")
    report("원격 디버깅 정책", not blocked_by_policy, policy or "정책 없음(허용)")

    try:
        browser = cdp.Browser.launch(headless=True)
    except cdp.CDPError as e:
        report("헤드리스 Chrome 기동", False, str(e))
        return 1
    profile, pid, port = browser.profile, browser.proc.pid, browser.port
    try:
        report("헤드리스 Chrome 기동", True, f"포트 {port}")
        page = browser.new_page()
        page.goto("data:text/html;charset=utf-8,<title>regsearch-check</title><p>ok</p>")
        title = page.title()
        report("페이지 제목 읽기", title == "regsearch-check", repr(title))

        lan = lan_address()
        if lan:
            exposed = port_reachable(lan, port)
            report("디버깅 포트 루프백 전용", not exposed,
                   f"{lan}:{port}에서 접속 {'됨(위험)' if exposed else '안 됨'}")
        else:
            report("디버깅 포트 루프백 전용", True, "LAN 주소가 없어 외부 노출 확인 생략")

        if not args.offline:
            for name, url in SITES:
                try:
                    status = page.goto(url, timeout=30)
                    ok = status is None or status < 400
                    report(f"{name} 접속", ok, f"HTTP {status} / 제목 {page.title()!r}")
                except cdp.BlockedError as e:
                    report(f"{name} 접속", False, str(e))
                except cdp.CDPError as e:
                    report(f"{name} 접속", False, str(e))
    finally:
        browser.close()

    time.sleep(0.5)
    leftover = processes_using(profile.name)
    report("종료 후 Chrome 프로세스 정리",
           not cdp.pid_alive(pid) and leftover == 0, f"남은 프로세스 {leftover}개")
    report("종료 후 임시 프로필 삭제", not profile.exists(), str(profile))

    failed = [r for r in results if not r[1]]
    print()
    print("모든 항목 통과" if not failed else f"실패 {len(failed)}건: " + ", ".join(r[0] for r in failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
