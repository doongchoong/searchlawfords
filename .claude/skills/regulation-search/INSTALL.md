# 법규 검색 스킬 설치 안내

증권사 공통 준수사항의 근거 조문을 금융투자협회(KOFIA)·한국거래소(KRX)·법제처에서 찾아 주는 Claude Code 스킬입니다.

## 필요한 것

- **Claude Code** (데스크톱 앱의 Code 탭 또는 터미널의 `claude`). claude.ai 채팅에 올리는 방식으로는 동작하지 않습니다.
- **Google Chrome** (보통 이미 설치되어 있습니다).
- Python은 스킬에 들어 있어 따로 설치하지 않아도 됩니다(`runtime\python`). 회사 보안 프로그램이 막을 때만 `references\python-install.md`를 보세요.

## 설치

1. 받은 zip 파일을 엽니다. 안에 `regulation-search` 폴더가 하나 있습니다.
2. 그 폴더를 아래 위치에 통째로 넣습니다(없는 폴더는 만들어 주세요).

   ```
   %USERPROFILE%\.claude\skills\
   ```

   다 넣으면 `%USERPROFILE%\.claude\skills\regulation-search\SKILL.md` 가 있어야 합니다.
   (`%USERPROFILE%`은 보통 `C:\Users\사번` 입니다. 탐색기 주소창에 그대로 입력하면 열립니다.)
3. Claude Code를 새로 시작합니다(이미 열려 있었다면 새 세션을 엽니다).

## 처음 써 보기

Claude Code에 평소처럼 물어보면 됩니다. 예:

- "고객 주문 없이 매매하면 안 되는 근거 규정 찾아줘"
- "자본시장법 제9조 보여줘"
- "최근 한 달 사이 바뀐 KRX 규정 알려줘"

처음 한 번은 스킬이 환경 점검을 먼저 합니다. Claude Code가 명령 실행을 허락할지 물으면 내용을 확인하고 허용해 주세요.
답변 끝에 "이 경로를 이력으로 확정할까요?"라고 물으면, 맞는 경로일 때만 승인하세요. 승인한 경로가 쌓일수록 비슷한 질문에 더 빨리 답합니다.

## 업데이트

새 버전 zip을 받으면 `%USERPROFILE%\.claude\skills\regulation-search` 폴더를 지우거나 덮어쓰고 같은 방법으로 넣습니다.
**확정한 사례·캐시·카탈로그는 스킬 폴더가 아니라 `%LOCALAPPDATA%\regulation-search\`에 있어 업데이트해도 지워지지 않습니다.**

## 문제가 생기면

- "Python을 찾지 못함", "액세스가 거부되었습니다" → `references\python-install.md`
- "차단·캡차 화면으로 보여 중단합니다" → 사이트가 자동 조회를 막은 것입니다. 잠시 뒤 다시 시도하고, 계속되면 담당 개발자에게 알려 주세요.
- 조회가 계속 실패 → 사이트 화면이 바뀌었을 수 있습니다. 오류 메시지와 스크린샷 경로를 담당 개발자에게 전달해 주세요.

버전 정보는 `VERSION.txt`에 있습니다.
