# Python 설치 안내 (법규 검색 스킬)

이 스킬은 보통 스킬 폴더에 함께 들어 있는 Python(`runtime\python\python.exe`)으로 돌아가므로 설치가 필요 없다.
이 안내는 **동봉 Python이 없거나, 회사 보안 프로그램이 동봉 Python 실행을 막을 때만** 쓴다.

## 1. 이미 설치돼 있는지 확인

명령 프롬프트(또는 PowerShell)에서:

```
py -3 --version
python --version
```

- `Python 3.10` 이상이 나오면 설치된 것이다. 더 할 일이 없다.
- 아무것도 안 나오거나 **Microsoft Store가 열리면** Python이 없는 것이다. Windows에 기본으로 들어 있는 "앱 실행 별칭"이 Store를 여는 것이고 진짜 Python이 아니다.

## 2. 설치하기 (관리자 권한 없이, 사용자 단위)

**방법 A — python.org 설치 프로그램 (권장)**

1. https://www.python.org/downloads/windows/ 에서 "Windows installer (64-bit)"를 받는다(3.12 이상 아무 버전).
2. 설치 첫 화면 아래의 **"Add python.exe to PATH"를 반드시 체크**한다.
3. "Install Now"를 누른다. 사용자 폴더에 설치되므로 보통 관리자 권한이 필요 없다.
4. 명령 프롬프트를 **새로 열어** `python --version`으로 확인한다.

**방법 B — winget (회사에서 허용된 경우)**

```
winget install -e --id Python.Python.3.12 --scope user
```

## 3. "python을 치면 Store가 열린다"가 계속될 때

Windows 설정 → 앱 → 고급 앱 설정 → **앱 실행 별칭**에서 `python.exe`, `python3.exe` 항목(앱 설치 관리자)을 끈다.
그다음 명령 프롬프트를 새로 열어 다시 확인한다.

## 4. 설치가 막힐 때

회사 PC에서 설치 프로그램 실행이나 winget이 막혀 있으면 사내 IT(헬프데스크)에 요청한다. 요청 문구 예:

> 업무용 Claude Code 스킬 실행을 위해 Python 3.12 이상(python.org 공식 배포판, 사용자 단위 설치)이 필요합니다.
> 또는 스킬에 동봉된 Python(Python Software Foundation 서명, `runtime\python\python.exe`) 실행을 허용해 주세요.

## 설치 뒤

Claude Code에서 다시 질문하면 된다. 처음 한 번은 스킬이 환경 점검(`check_env.py`)을 먼저 돌린다.
Chrome도 필요하다(대부분 이미 설치돼 있다).
