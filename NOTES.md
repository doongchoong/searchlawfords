# NOTES — 설계 요약, 확인된 사실과 결정 로그

모든 세션은 시작할 때 이 파일을 읽고, 끝날 때 확인한 사실과 결정을 덧붙인다.
처음 설계는 `법규 검색 스킬 제작 계획서 (Claude Code 인계용).md`(2026-10-27 작성)였으나 구현이 끝나 사용자 요청으로
삭제했다(2026-10-27). 계속 유효한 내용은 아래 "설계 요약"으로 옮겼다. 아래 결정 로그의 "계획서"는 그 문서를 말한다.

## 설계 요약

**목적과 범위**: 증권사 현업이 KOFIA·KRX·법제처에서 모든 증권사 공통 준수사항의 근거 조문을 빨리 찾게 돕는 Claude Code 스킬.
사람이 확정한 조회 경로를 사례로 쌓아 비슷한 질문에 먼저 보여 준다. 하지 않을 것: 적법성 결론 단정(근거 제시까지만),
캡차·봇 차단 우회, 규정 원문의 외부 반출, 다인 공유(v1은 현업 1명 전용), 회사 고유 상품·내부 문서.

**원칙**
1. 세 사이트 모두 헤드리스 Chrome + CDP로 조회한다(법제처 Open API 미사용).
2. 사람이 확정한 사례만 저장한다(자동 로그·후보 상태 없음).
3. 길 안내만 기억하고 결론은 기억하지 않는다. 답변 때마다 본문·개정일을 라이브로 확인한다.
4. 사례는 정답이 아니라 출발점. 후보에 사례와 무관한 탐색 후보를 늘 하나 이상 넣는다.
5. 캐시를 믿지 않는다. 수집일을 저장하고 답변 전 사이트의 현재 개정판과 비교한다(`Adapter.open_cached`).
6. 번호가 아니라 제목으로 기억한다(개정으로 조·항 번호가 밀려도 따라가게).
7. 1명 전용으로 시작하되 파일은 JSONL 등 병합하기 쉬운 형식.
8. Python 표준 라이브러리만 쓴다(현업 PC에 pip 설치 없이). CDP WebSocket도 직접 구현.
9. 코드와 사용자 데이터 분리: 스킬 폴더엔 코드·지침·셀렉터·시드만, 사례집·카탈로그·캐시는 `%LOCALAPPDATA%\regulation-search\`.

**CDP 러너와 안전 규칙**(`cdp.py`): 평소 프로필 금지, 실행마다 빈 임시 프로필과 임의 포트(127.0.0.1만), 정상·비정상 종료 모두 프로세스·프로필 정리,
캡차·차단·로그인 요구 화면이면 우회하지 않고 스크린샷과 함께 중단, 요청 간격은 `store.throttle`로 모든 세션이 공유(기본 2초),
헤드리스 표식만 일반 UA로 바꾸고 그 이상의 위장은 하지 않는다.

**사이트 어댑터 사양**: 공통 인터페이스는 `scripts/adapters/base.py`가 기준(`crawl_catalog`, `open_regulation`, `get_article`, `open_pair`,
`current_revision_date`, `search`, `recent_changes`, `open_cached`). 사이트 차이는 어댑터와 `references/selectors.<사이트>.json`에 가둔다.

**매칭 방식**(`match.py`): ① 질의 정규화(약칭) ② 유사 사례 ③ 카탈로그 퍼지 매칭 + 본규정·시행세칙 짝 ④ 법제처 업무영역 → 화이트리스트
⑤ 조문 검색(캐시 로컬 검색, 사이트 검색, 필요 시 본문 받기) ⑥ 탐색 슬롯. 모든 후보에 이유. 가중치는 `references/match.json`.

**사례집**(`cases.py`): 사람이 승인·수정한 경로만, 추가만(append-only), 같은 주제·경로 재확정은 재확정 줄, 확인횟수·최근확정일은 읽을 때 계산,
개정판이 바뀌면 "개정 이후 미확인" 표시 후 조문 제목으로 위치 재확인. 확정 질문은 답변 끝에 한 번만, 초안(`cases.py draft`)을 보여 준 뒤.

**출력 형식·답변 규칙**: `SKILL.md`가 기준(결론 한 줄 → 근거 조문 발췌·구분 → 시점(사이트상 개정일/본문 수집일) → 원문 링크 → 참고 사례 →
확인하지 못한 부분 → 근거 자료 제시 고지 → 확정 초안 → 확정 질문). 0건이어도 "없음" 단정 금지, 현행·시행예정·개정예고 구분.

**테스트와 평가**: 어댑터 스모크 테스트(`REGSEARCH_LIVE=1`일 때 실사이트), 오프라인 파싱·매칭·사례 테스트, `eval.py`로 hit@k(시드 질의 또는 확정 사례).

**구현 단계**: 0 환경 점검 · 1 CDP+KOFIA+store · 2 KRX · 3 법제처 · 4 카탈로그 통합·매칭·약칭 · 5 사례집 · 6 SKILL.md·실사용 시험 · 7 패키징·배포.
0~5 완료, 6 실사용 시험 3건 통과(변경 확인 유형 남음), 7 진행 중(아래 "현재 상태").

**사람이 정할 것(열린 결정)**: 법제처 화이트리스트(`scope.json`, 초안 14개), 약칭 시드(`aliases.json`, 초안), 평가 질의(`eval_queries.json`, 초안),
초기 사례 10~20건, 각 사이트 이용약관상 자동화 조회 허용 범위(미확인).

## 현재 상태 (2026-10-27)

- 0~5단계 완료, 6단계 `SKILL.md` 초안 작성. 사이트 어댑터 셋 + `catalog.py`, `match.py`, `cases.py`(확정·재확정·개정 이후 미확인·위치 재확인), `lookup.py`(라이브 조회 단일 진입점), `eval.py`, `references/*.json`, `seed/catalog.json`, `SKILL.md`.
- **이제 이 프로젝트 폴더에서는 Claude Code가 스킬을 인식한다**(`.claude/skills/regulation-search/SKILL.md`). 새 세션부터 적용될 수 있다.
- **다음은 6단계 마무리**: 현업이 실제 질문으로 시험해 출력 형식·안전 규칙을 지키는지 확인. 그 전에 현업 검토가 필요한 것: `aliases.json`, `eval_queries.json`, `scope.json`.
- 테스트: `python -m unittest discover -s .claude/skills/regulation-search/tests` → 오프라인 56개 통과(실사이트 14개는 건너뜀). `REGSEARCH_LIVE=1`이면 실사이트 포함(2026-10-27 기준 실사이트 42개 통과 확인, 사례 테스트 추가 후 오프라인만 다시 돌림)
- 환경 점검: `python .claude/skills/regulation-search/scripts/check_env.py` → 전 항목 통과
- **Python 동봉(7단계 일부, 2026-10-27)**: 스킬 폴더 `runtime/python/`에 python.org 공식 Windows embeddable package 3.14.6(64-bit)을 넣었다. 압축 12.0MB / 풀면 23.3MB. SHA256이 python.org 릴리스 페이지 값과 일치, `python.exe` 서명은 Python Software Foundation(유효). `python314._pth`에 `../../scripts`를 더해 스킬 모듈을 import한다(embeddable은 스크립트 폴더를 sys.path에 넣지 않기 때문). 이 Python으로 오프라인 테스트 56개 통과, 환경 점검 통과, 실사이트 조회 확인. 출처·해시·업데이트 방법은 `runtime/README.md`.
  - SKILL.md "준비"에 **Python 고르기** 순서를 넣었다: ① 동봉 Python → ② 막히면 `py -3`/`python`(3.10+, WindowsApps 가짜 실행 파일 주의) → ③ 둘 다 없으면 `references/python-install.md` 안내를 보여 주고 중단. 명령은 모두 `<PY> "<스킬>/scripts/…"`.
  - `check_env.py`는 동봉/설치 Python 중 무엇을 썼는지와 경로를 보여 준다.
  - **현업 PC에서 확인할 것**: 사내 보안 프로그램(응용 프로그램 제어)이 동봉 `python.exe` 실행을 막는지. 막히면 ②·③으로 넘어간다.
- **패키징(7단계, 2026-10-27)**: `tools/package.py`(개발자용, 배포본 밖)로 `dist/regulation-search-<YYYY.MM.DD-HHMM>.zip`을 만든다.
  - 순서: 시드 카탈로그 복사(`--refresh-catalog`면 먼저 세 사이트 재수집) → 동봉 Python으로 오프라인 테스트(실패 시 중단) → zip(`__pycache__`·`tests` 제외, `VERSION.txt` 추가) → 임시 위치에 풀어 검증.
  - 검증 항목: 다른 위치·작업 폴더에서 동봉 Python으로 모든 모듈 import, 환경 점검(오프라인) 통과, 첫 사용 시 시드 카탈로그가 사용자 데이터로 복사되고 사례 확정 가능, **스킬 폴더를 지우고 다시 풀어도 사례집·카탈로그 그대로**.
  - 첫 빌드: `dist/regulation-search-2026.10.27-1521.zip` 11.8MB, 파일 63개(SKILL.md, INSTALL.md, VERSION.txt, scripts 14, references 8, seed 1, runtime 37). 시드 KOFIA 123·KRX 82·법제처 14(2026-10-27 15:20 갱신). 검증 전부 통과.
  - 현업용 설치 안내는 스킬 폴더 `INSTALL.md`(설치 위치 `%USERPROFILE%\.claude\skills\`, 업데이트는 폴더 덮어쓰기, 데이터는 `%LOCALAPPDATA%`라 안전).
  - **아직 안 한 것**: 현업 PC에서 실제 설치·`check_env.py`(동봉 Python이 보안 프로그램에 막히는지 포함).
- **질문 해석은 Claude, 찾기·검증은 스크립트(2026-10-27, 사용자 결정)**: 규칙 기반 키워드 추출(조사·어미 떼기)이 반복해서 틀려서("만료되는"을 검색어로, "관리종목인", 엉뚱한 "유효기간" 조항) 역할을 나눴다.
  - SKILL.md에 **0단계 "질문 해석"**: Claude가 규정 용어로 바꾼 핵심어(`--terms`), 짚이는 규정(`--regs`, "사이트:이름" 가능), 사이트 검색어(`--search`, 공백 포함·여러 번), 업무영역(`--areas`)을 정해 `match.py`에 넘긴다. 해석은 "어디를 찾을지"까지만이고 기억 속 조문 번호·내용을 답에 쓰지 않는다. 후보와 사례(`cases_considered`까지 넓게)는 Claude가 읽고 거른다.
  - `match.py`: `Hints` 추가. 힌트가 있으면 조문·캐시 검색어는 힌트 위주(자동 추출 말 제외), **규정명 매칭은 힌트 + 질문 낱말**(`Query.name_terms` — 처음엔 질문 낱말을 버렸다가 "유가증권시장 업무규정" 같은 이름 매칭이 1위→5위로 떨어져 되돌림), 사이트 검색은 힌트 검색어, 힌트 규정은 후보(source=hint, 점수 1.0). 힌트가 없으면 예전 자동 경로 그대로(예비 수단·평가 기준선).
  - `cases.py draft`가 같은 힌트 옵션을 받아 사례에 `해석`으로 저장, `eval.py`는 `--mode auto|hints|both`(기본 both)로 시드 질의의 `hints`·사례의 `해석`을 재현한다. 시드 질의 18개에 해석 예시를 붙였다(정답을 아는 상태라 규정명은 빼고 핵심어·검색어만).
  - 오프라인 평가(18개): auto 0.50/0.83/0.89/0.87, hints 0.50/0.78/0.89/0.87 — 오프라인에선 힌트 검색어가 안 쓰여 차이가 작다.
  - **온라인 평가(18개, hit@1/3/5/조문)**: auto 0.44/0.89/0.89/0.87, hints 0.44/0.83/**0.94**/**0.93**. 힌트를 주면 5위 안 적중과 조문 적중이 오르고(못 찾던 "전문투자자 일반 대우 서면 통지"를 찾음), 3위 안은 한 문항 내려간다. 둘 다 못 찾는 건 "ISA 비과세 과세특례"(조특법 본문이 커서 상위 3개 받기에 안 들어감). 이 수치에는 Claude가 후보를 읽고 거르고 다시 묻는 단계가 빠져 있어 실사용보다 보수적이다. 결과 파일 `<데이터>/eval/20261027-171626-seed-online.json`.
  - 실사용 확인(같은 날, 이 세션): 새 절차로 "전문투자자 투자자정보 만료 매매 허용", "파생형 ETF 통과 처리" 두 질문을 처리 — 무관한 "유효기간" 규정이 상위에 끼던 문제가 사라졌다.
- **실사용 사례 2건**(fork 세션에서 사용자가 확정, 2026-10-27): c-202610-001 중간가호가 입력 제한, c-202610-002 관리종목의 호가 입력 제한(경쟁대량매매호가). 경로는 둘 다 KRX 유가증권시장 업무규정 제12조·시행세칙 제14조, 코스닥시장 업무규정 제9조·시행세칙 제8조.
- **매칭 수정(2026-10-27, fork 보고)**: "관리종목인 종목에 대해 입력할 수 없는 호가 종류는?"에서 c-001이 유사 사례로 안 잡혔다.
  - 원인 ① 서술격 조사·되다 꼴을 못 뗌("관리종목인", "제한돼") → SUFFIXES에 인·인지·인데·이고·이면·이라·이다·이며, 돼·되나·돼요·되나요·되는지 추가(원형도 남기므로 "대리인" 같은 명사는 그대로 유지). STOP에 "없는", "종류".
  - 원인 ② 사례 키워드를 통째 문자열로만 비교("호가입력의 제한" ≠ "입력할…호가") → 사례 키워드·주제를 낱말로 쪼개(`core_terms(tokenize(…))`) 질문 낱말과 겹치는 수도 센다. 점수는 일치 수/3.
  - 확정 초안 키워드는 조사 붙은 형태를 빼고 저장(`core_terms`). 기존 사례 c-001·002 키워드엔 "매매거래에서"·"입력이"가 남아 있지만 읽을 때 낱말 비교를 하므로 문제없다.
  - 결과: 두 표현 모두 c-001이 유사 사례로 잡히고 정답 4개 규정이 1~4위. 시드 평가 변화 없음(0.53/0.76/0.88/0.86), 실사용 사례 평가(`eval.py --cases`, 2건) hit@1 1.0·조문 적중 1.0.
  - zip 재빌드: `dist/regulation-search-2026.10.27-1525.zip`(검증 전부 통과). 이전 빌드 `…-1521.zip`은 낡은 버전.
- **규칙 기반 러프 파싱(2026-10-28, 사용자 요청 "규칙기반부터")**: 사이트 파서가 조문으로 못 나눈 덩어리(번호 없음 → 조문 검색에서 빠짐)를 `scripts/adapters/rough.py`가 다시 나눈다. 사이트 공통, 사이트 파서 뒤에 한 겹.
  - 규칙: "제N조(제목)"(하나라도 있으면 법령식으로 보고 "N."은 호로 둠) / "N. 제목"(짧고 문장 끝 아님, 1·2·3 연속일 때만, 큰 제목 뒤 1은 재시작, 조문 안 "1. 국가 2. 한국은행" 같은 목록은 이어지는 번호를 목록으로) / "Ⅰ. 총칙"·"Ⅳ-1." → 경로 / "[참고1]·[별표]·[별지]" → 첨부(부칙에 섞여 있으면 떼어 내고 같은 부칙 표시의 다음 덩어리를 붙임, 다른 부칙은 그대로) / 그래도 없으면 약 800자 "블록N"(규정 전체에서 일련번호).
  - 나눈 조문은 `extra.parse` = rough | block. 사이트가 나눈 조문은 건드리지 않는다(번호 있는 조문·부칙). 본문 글자는 그대로, 줄만 나눈다.
  - 연결: `Adapter.open_cached`가 새로 받은 본문에 적용 후 캐시. `cache.get`/`iter_all`은 캐시의 `regulation.extra.rough`가 `ROUGH_VERSION`과 다르면 꺼낼 때 다시 나눈다(규칙만 바뀐 것이라 재접속 없음 — `parser_version`과 구분). `lookup.py`는 해당 조문에 `분할` 설명을, `match.py` 조문 히트는 `parse`를 붙인다. SKILL.md 규칙: 답변에 "(자동 분할)" 표시, 블록은 번호 대신 첫 문장으로 지칭.
  - 현재 캐시 적용 결과: 사이트 조문 6,260개 그대로, rough 62·block 33개 새로 생김. 특정금전신탁 업무처리 모범규준(KOFIA 294) 0 → 32개(1. 목적 … Ⅰ~Ⅳ 경로 포함), 고난도 금융투자상품 표준영업행위준칙 0 → 25개 + 머리말 블록 2, 설명서·위험고지서류는 블록(제목 줄이 없는 게 정상), KRX 업무규정 시행세칙의 별표가 첨부 조문으로.
  - 평가(오프라인 18개): 러프 끈 기준선 auto 0.44/0.78/0.89/0.87 · hints 0.44/0.78/0.89/0.87(어제 0.50에서 내려간 건 그사이 캐시가 늘어 ISA 질의가 2위가 된 탓) → 켬 auto 0.44/0.78/**0.94**/**0.93** · hints **0.39**/0.78/0.89/0.87. hints에서 "위탁증거금 징수기준은 회원이 정한다"가 1→2위(짝 규정인 유가증권시장 업무규정 시행세칙의 별표가 1위로 올라옴).
  - 테스트 `tests/test_rough.py` 7개(모범규준 덩어리·법령식·부칙 속 첨부·블록·멱등·문장 줄 제외·옛 캐시 재분할). 전체 84개 중 오프라인 70개 통과.
  - **실사용 시험(새 세션, 2026-10-28)**: 특정금전신탁 적정성·설명서, 고난도 상품 목표시장·테스트, "모범규준 13번" 세 질문 모두 분할 조문이 1순위, 답변에 "(자동 분할)" 표시, 대조한 조문 12개 경계 정확. 문제 둘: ① "Ⅳ. 금지행위"처럼 장 제목 바로 아래 번호 제목 없는 글이 머리말 자리로 빠져 장 이름 없는 맨 앞 블록이 되고 "Ⅴ. 부칙"까지 섞임(부칙 단서가 문장 중간에서 잘림) ② 그 부칙 블록(조문 제목이 나열됨)이 "목표시장" 검색 2위를 차지해 7.(테스트)이 5위 밖으로 밀림.
  - **수정(ROUGH_VERSION 2)**: 장 제목 아래 번호 없는 글은 그 자리에 블록으로 두고 장 제목을 제목·경로로 단다(장을 넘지 않음). "Ⅴ. 부칙"·"부 칙" 줄 뒤는 부칙(`addenda`, 번호 없음 → 조문 검색 제외). 조문 검색에서 블록 점수 × `match.json articles.w_block`(0.6). `lookup.py article`에서 번호로 찾은 조문 제목이 준 `--title`과 다르면 `확인하지_못한_부분`에 알림.
  - 덤으로 바로잡힌 것: 특정금전신탁 모범규준의 "Ⅵ. 부칙" 아래 "1. 부칙<2013.12.3>"…"16. 부칙<2024.11.7>" 16개가 조문으로 잡혀 있었는데(32개 중 16개) 이제 부칙으로 빠진다 → 실제 조문 16개.
  - 수정 후 고난도 준칙 "제조회사 목표시장 테스트" 조문 순위: 17·6·16·22·**7** (부칙 블록 없음). 평가(오프라인): auto 0.44/0.78/0.94/0.93, hints **0.44**/0.78/0.89/0.87(러프 1판의 0.39에서 회복), 사례 2건 1.0. 테스트 86개 중 오프라인 72개 통과.
  - 남은 것(하): 분할 조문의 원문 링크가 문서 전체 인쇄 URL, 조회 snippet 앞 글자 잘림("(1)"→"…1)"), 준칙 부칙의 2026-12-01 시행분을 "현행"으로만 표시.
  - 다음 후보(아직 요청 없음): 규칙으로도 블록밖에 안 나오는 문서에 한해 LLM이 경계만 표시하는 보조 단계.
- 이 폴더는 아직 git 저장소가 아니다. 2·3단계를 세션별로 병행하기 전에 `git init`이 필요하다.

## 결정

- 2026-10-27 · CDP 클라이언트는 표준 라이브러리로 WebSocket을 직접 구현한다(Playwright 미사용). 배포 대상 PC에 `pip install` 없이 Python과 Chrome만 있으면 되게 하려고.
- 2026-10-27 · 사용자 데이터(사례집·카탈로그·캐시·락·스크린샷)는 스킬 폴더 밖 `%LOCALAPPDATA%\regulation-search\`에 둔다. `REGSEARCH_DATA_DIR`로 바꿀 수 있다. 스킬을 덮어써 업데이트해도 사례가 남게 하려고.
- 2026-10-27 · 개발은 프로젝트 `.claude/skills/`, 배포는 현업의 `%USERPROFILE%\.claude\skills\`에 폴더째 복사.
- 2026-10-27 · 요청 간격(`store.throttle`)은 호스트별 키로 모든 세션이 공유한다. 기본 2초, `REGSEARCH_MIN_INTERVAL`로 조정. 서로 다른 사이트끼리는 기다리지 않는다.
- 2026-10-27 · `append_jsonl`은 락을 잡고 파일 전체를 원자적으로 교체한다(단순 append 아님). 사례집은 작으니 비용보다 손상 방지가 낫다.
- 2026-10-27 · **공통 인터페이스 확정** (`adapters/base.py`):
  - 모델: `Regulation`, `Article`, `RegulationText`, `RevisionInfo`, `SearchHit`, `Change`. 모두 `to_dict`/`from_dict`로 JSON 저장(캐시·카탈로그용).
  - 메서드: `crawl_catalog(previous=None, with_dates=True)`, `open_regulation(reg)`, `get_article(reg, number=None, title=None)`, `current_revision_date(reg)`, `search(query, scope=None)`, `recent_changes(days=30)`.
  - 계획서와 다른 점: 본규정·시행세칙 쌍은 `open_regulation`이 아니라 공통 `open_pair(reg, catalog)`가 이름으로 짝을 찾아 함께 연다(`find_pair`, 공백·가운뎃점 무시). KRX도 이걸 쓰면 된다. 짝 규칙이 이름으로 안 되는 사이트는 `open_pair`를 재정의한다.
  - `search`의 `scope`는 사이트별 dict. KOFIA: `field`(content|title|attach), `history`(bool), `max_pages`.
  - 조문 찾기(`RegulationText.find`)는 번호 → 제목 정확 일치 → 제목 포함 순. 부칙은 기본 제외.
  - 어댑터는 `Adapter(browser=None)`; 브라우저를 넘기면 여러 어댑터가 Chrome 하나를 같이 쓴다.
- 2026-10-27 · **개정 여부는 날짜가 아니라 `revision_id`로 비교한다.** KOFIA는 본문 연혁 마지막 날짜와 연혁 목록 날짜가 하루 다른 경우가 있다(매매거래계좌설정약관: 본문 2026-03-18 / 목록 2026-03-17). `revision_date`는 연혁 목록 기준, 본문 날짜는 `extra.text_history_date`.
- 2026-10-27 · 실사이트 스모크 테스트는 `REGSEARCH_LIVE=1`일 때만 돈다. 기본 테스트는 오프라인으로 빠르게.
- 2026-10-27 · KRX도 현행 규정 트리 전체를 수집한다(사용자 결정). 증권사와 거리가 먼 분류(일반상품시장 등)도 넣고 매칭에서 거른다.
- 2026-10-27 · KRX 본문은 iframe(workPage)을 거치지 않고, 첫 화면(/out/index.do)에서 사이트의 검색결과 팝업과 같은 폼(`regulationViewPop.do`, POST bookid + 페이지의 _csrf)을 메인 프레임으로 제출해 연다. 계획서의 "항상 index에서 시작"은 지킨다. 구조는 iframe 화면(`regulationView.do`)과 같다.
- 2026-10-27 · 공통 도우미 `js_call`, `pick_regulation`을 `base.py`로 옮겼다(어댑터끼리 import하지 않기 위해).
- 2026-10-27 · **법제처는 전체 카탈로그를 받지 않는다**(사용자와 합의): ① `references/scope.json` 화이트리스트가 작은 카탈로그, ② 조문내용 검색이 기본 진입로(범위 밖 결과는 `extra.in_scope=False`), ③ 연 법령은 캐시해 재사용. 화이트리스트에 조세특례제한법(법·시행령·시행규칙)을 사용자 요청으로 추가. 계획서 "사이트별 주의점"에 반영.
- 2026-10-27 · **조회 캐시** `scripts/cache.py`: `<데이터>/cache/<사이트>/<규정ID>.json`에 규정마다 최신 한 판. `Adapter.open_cached(reg, verify=True)`가 사이트의 현재 개정판(revision_id)을 확인해 같으면 캐시(`extra.from_cache=True`, `collected_at`은 처음 수집 시각), 다르면 다시 받는다. 세 사이트 공통이지만 확인 비용은 다르다(법제처 1KB fetch, KOFIA 55KB, KRX 메뉴+목록 1~5쪽).
- 2026-10-27 · **통합 카탈로그** `<데이터>/catalog.json` = `{"sites": {사이트: {"updated_at", "regulations"}}}`. 법제처는 화이트리스트만. 사용자 카탈로그가 없으면 스킬 폴더 `seed/catalog.json`을 복사해 쓴다(배포 직후 크롤링 없이 매칭 가능). 갱신은 `python catalog.py refresh [--site …]`, 락 `catalog-crawl`로 한 세션만. 2026-10-27 시드: KOFIA 123 · KRX 82 · 법제처 14.
- 2026-10-27 · **매칭(`match.py`)** — 계획서 6단계 그대로, 후보마다 이유. 결정 사항:
  - 형태소 분석기 없이 조사·어미 접미사를 떼되 원형도 남긴다("매매하면" → 매매하면, 매매).
  - 조문 점수는 제목 일치 3, 본문 1(최대 3회까지), 제목 2-gram 1에 **IDF 가중**(캐시 전체 조문에서 흔한 말은 낮춤, 하한 0.15). "고객·주문·원칙" 같은 말이 큰 법령을 끌어올리던 문제 때문.
  - 질문에 "정의/뜻/의미"가 있으면 정의 조항 꼴(“투자권유”란)이 있는 조문에 제목 일치만큼 가산.
  - **캐시 로컬 검색**: 캐시된 본문 전체를 훑어 이름이 안 맞아도 본문이 맞으면(정규화 점수 0.5 이상) 후보에 올린다(source=cache). 법제처 검색 50건 한계 보완.
  - **사이트 검색(site_search)**: 계획서의 "교차 확인용 보조"를 매칭 5단계에 넣었다. 가장 구체적인 말 2개(용어 확장 → IDF 높은 키워드)로 KOFIA·KRX·법제처 검색을 돌려 걸린 규정·조문 제목을 후보와 "우선 조문"으로 올린다. 시행예정 결과는 뺀다. 법제처 화이트리스트 밖 결과는 점수 절반 + "확인 필요" 표시.
  - 온라인 모드(`--online` = site_search + 상위 3개 본문 받기 + 개정판 확인)는 질의당 수십 초. 오프라인은 카탈로그+캐시만이라 1초 안.
  - 탐색 슬롯: 상위 k(5)개가 모두 사례 출신이면 마지막 자리를 사례와 무관한 최상위 후보로 바꾼다.
  - 사례 경로 후보는 사례 점수에 비례 + 0.3, 사례가 가리킨 조문 제목은 조문 검색에서 크게 가산(from_case).
- 2026-10-27 · **4단계 hit@k 측정**(시드 질의 16개, Claude 초안 정답, 결과 파일은 `<데이터>/eval/`):

  | 모드 | hit@1 | hit@3 | hit@5 | 조문 적중 |
  | --- | --- | --- | --- | --- |
  | 오프라인, 캐시 거의 없음 | 0.25 | 0.50 | 0.56 | 0.46 |
  | 온라인(사이트 검색 + 상위 3개 받기) | 0.31 | 0.69 | 0.88 | 0.85 |
  | 오프라인, 온라인 평가로 캐시 43개 쌓인 뒤 | 0.375 | 0.75 | 0.88 | 0.85 |

  - 관찰: 캐시가 차면 오프라인이 온라인보다 상위권이 낫다. **사이트 검색 점수(base 0.4 + 건당 0.1)가 상위권에 잡음을 넣는다** — 예: "자본시장법상 투자권유의 정의"가 온라인에서 3위(1위 금소법). 약칭 가중치(w_alias 1.0/1.5/2.0)는 오프라인 결과에 영향 없음.
  - 조정 후보(아직 안 함, 초안 16개에 맞추면 과적합 위험): 약칭이 잡힌 질문에서는 site_search 점수를 낮추기, site_search base 0.4 → 0.25.
  - 못 찾는 질문: "회원 직원이 위탁자 주문정보를 이용해 먼저 매매…"(KRX 시장감시규정 제3조) — KRX 검색이 규정 단위라 조문 단서가 없고 이름에도 단서 없음. "ISA 비과세 과세특례" — 조특법 본문이 커서 온라인 상위 3개 받기에 안 들어감.
- 2026-10-27 · **사례집(5단계)** `cases.py`:
  - 확정 초안 `draft "주제" --path "사이트|규정·약칭·ID|조번호" --first … --law-query …` → 저장하지 않고 JSON만. 본문 캐시가 있으면 조문 제목·링크·개정판을 본문 기준으로 채운다.
  - 경로 한 칸: KOFIA/KRX `{사이트, 카테고리, 규정, 조문, 조문제목, 규정개정일, 개정판, 링크}`, 법제처 `{사이트, 검색어, 법령, 조항, 조문제목, 법령시행일, 개정판, 링크}`. 계획서 예시에 `개정판`(revision_id)과 `링크`를 더했다 — 개정 여부를 날짜가 아니라 개정판으로 비교하기 때문.
  - `confirm --file`: 락 `cases-confirm` 안에서 id(`c-YYYYMM-NNN`)를 매기고 추가. **같은 경로(사이트·규정·조문제목 집합) + 주제 유사(2-gram dice ≥ 0.6)**면 재확정 줄만 쓴다. 경로가 바뀐 재확정(개정으로 조문 번호 이동)은 재확정 줄에 `경로갱신`을 담고, 읽을 때 최신 경로로 바꾼다. 원 줄은 절대 고치지 않는다.
  - "개정 이후 미확인"은 저장하지 않고 읽을 때 계산(`annotate_stale`: 사례 경로의 개정판 ≠ 카탈로그 현재 개정판, 개정판이 없으면 날짜 비교). 매칭 결과의 사례·후보 이유에 표시된다.
  - `relocate <id>`: 경로의 규정을 현재 개정판으로 열어 조문 제목으로 위치를 다시 찾는다. `--confirm`은 모두 찾았을 때만 재확정(경로갱신).
  - `해당없음`은 경로 없이도 저장 가능하지만 SKILL.md에서 사람이 명시적으로 확정할 때만 쓰게 했다.
- 2026-10-27 · **`lookup.py`**: 답변 직전 라이브 조회 한 진입점(`article`·`find`·`recent`·`search`). 본문은 `open_cached(verify=True)` — 현재 개정판 확인 후 캐시 또는 새로 받기. 결과에 항상 `시점`(사이트상 개정일·개정판·본문 수집일·캐시 여부·라이브 확인 여부·구분). `--reg`는 ID·정식명·일부·약칭 모두 받는다.
- 2026-10-27 · **`SKILL.md` 초안**: 질문 유형 3갈래(직접 지정 / 근거 탐색 / 변경 확인), 근거 탐색 절차(match --online → lookup find/article → 답변 → cases draft → 확정 질문 한 번 → confirm), 계획서의 출력 형식·규칙, 명령 요약. 명령은 `python "<스킬>/scripts/…"`로 쓰고 `<스킬>`은 SKILL.md가 있는 폴더라고 적었다.
- 2026-10-27 · **실사용 시험(새 세션, 스킬 이름 안 알려 주고 실제 질문만)**: "투자권유 전 투자성향(투자목적·재산상황·투자경험) 확인 근거" → 스킬 자동 호출 ○, 출력 형식·시점·조문 링크·확정 질문 한 번 ○, 적법성 단정 없음 ○. 발견된 문제 셋과 수정:
  1. **매칭이 금소법을 못 띄움**("적합성"이란 말이 없어서) → `aliases.json` 용어 확장(투자성향·투자목적·재산상황·투자경험·고객정보 파악 → 적합성·투자자정보), `match.json` 금융소비자보호 영역 키워드 추가, 영역 키워드가 여러 개 맞으면 영역 법령 가산(+0.1/개, 최대 0.6), 떼고 나면 불용어인 말("규정이", "확인해야") 버림, 4글자 미만 조문 제목엔 글자 유사도 가산 안 함("제1조(목적)" 잡음).
  2. **KOFIA 표준투자권유준칙 조문 0개** → 모범규준식 번호("8. 투자자정보 파악…")를 조문으로 인식(`number="8."`, `find("8")` 가능), 조문 블록 안 `.partition`("※ 회사참고사항")을 제목으로 보지 않음(그 뒤 본문을 버리던 버그 — 본문 2.4만→4.2만 자), 끝의 "[별표/서식 파일]" 목록(`a[name=inclosure]`)은 조문에서 빼고 `extra.attachments`로. **`Adapter.parser_version`**을 두어 캐시의 파서 버전이 다르면 개정판이 같아도 다시 받게 했다(KOFIA=2).
  3. **확정 초안 명령 미사용** → `cases.py draft`가 초안을 `<데이터>/drafts/`에 저장하고 `{file, draft}` 출력, `confirm --latest` 추가. SKILL.md에서 초안 만들기를 "필수" 단계로 분리하고 출력 형식에 "확정 초안" 블록을 넣었다.
  - 수정 후 오프라인 평가(시험 질문 추가, 17개): hit@1 0.53 · hit@3 0.76 · hit@5 0.88 · 조문 적중 0.86 (수정 전 16개 0.375 · 0.75 · 0.88 · 0.85). 시험 질문은 1위(표준투자권유준칙 8.), 2~4위 금소법 제17조·시행령 제11조·감독규정 제10조.
- 2026-10-27 · 5단계 완료 기준 실사이트 확인(임시 데이터 폴더): 매매거래계좌설정약관 제6조 + 자본시장법 제70조로 확정 → 같은 질문 재매칭 시 사례 c-202610-001이 먼저, 두 경로가 1·2위, 조문도 제6조·제70조가 맨 위 → `relocate`로 두 조문 모두 제목으로 재발견 → 같은 경로 재확정 시 재확정 줄(확인횟수 2).
- 2026-10-27 · `SearchHit`에 `extra` 필드 추가(법제처: status 현행/시행예정, in_scope, type, header). `Adapter.url()`의 첫 인자를 위치 전용(`key, /`)으로 바꿨다(URL 템플릿의 `{name}`과 충돌).

## 확인된 사실

- 개발 PC: Python 3.14.6(`python`, WindowsApps), py 런처 3.14.4, Chrome `C:\Program Files\Google\Chrome\Application\chrome.exe`, Chrome 원격 디버깅 정책 없음.
- `--remote-debugging-port=0`으로 띄우면 Chrome이 `<프로필>/DevToolsActivePort`에 포트를 적는다. 임의 포트 선택에 이걸 쓴다.
- 디버깅 포트는 루프백에만 열린다(LAN 주소로 접속 안 됨 확인).
- `Browser.close` 후 프로세스·임시 프로필이 모두 정리된다(테스트 후 잔여 0 확인).
- 헤드리스 Chrome으로 세 사이트 첫 화면 모두 HTTP 200:
  - KOFIA `https://law.kofia.or.kr/` 제목 "금융투자협회 법규정보시스템"
  - KRX `https://rule.krx.co.kr/out/index.do` 제목 "KRX 법무포털"
  - 법제처 `https://www.law.go.kr/` 제목이 **빈 문자열** → 3단계에서 `main.html` 리다이렉트/iframe 구조 확인 필요
- 캡차·봇 차단 화면은 지금까지 만나지 않음.

### KOFIA (2026-10-27 확인, 상세는 `selectors.kofia.json`과 `kofia.py` 머리말)

- 현행 규정 123건. 분류: 협회규정 17, 모범규준 68(하위 폴더: 설명서 및 위험고지, 핵심설명서제도, IT 안정성 강화 및 IT감사 가이드라인), **표준약관 16** — 계획서의 "표준약관" 카테고리는 실제로 그 이름으로 있다. 시행세칙 7건.
- 트리(`lawCurrentPartTree.do`)는 WebFXTree JS 객체 `tree1`. 노드 action `gotoLawList(lawSeq, folder, 이름, …, lawType, historySeq, url)`. 현재 lawType은 전부 1(일반). 2=파일만, 3=외부 링크.
- 본문 `lawFullScreenContent.do?seq={lawSeq}&historySeq={historySeq}`. **historySeq=0이면 빈 화면**이라 실제 값이 필요(트리에 있음). 약 0.9MB(영업규정 기준).
  - `#lawcontent` 아래 `.history`(연혁), `.PT>.part`·`.CT>.chapter`·`.ST>.section`·`.partition`(편장절), `.JO#contentSeq>.article`(조문 제목), `.none/.hang/.ho/.dann/.mok`(본문·항·호·목), `.addenda`·`.addenda_none`(부칙).
  - 영업규정은 "제2-1조" 같은 편-조 번호, 약관은 "제1조".
- 조문 단위 링크: `detailArticlePrint.do?seq=&historySeq=&contentSeq=` (부칙은 규정 전체화면 링크로 대신).
- 현재 개정일: `lawFullScreen.do?seq=&historySeq=0`의 `select#p_sel`(값=historySeq, 글=날짜, selected=현행). 약 55KB.
- 사람이 보는 진입 URL: `lawView.do?seq=&historySeq=0&gubun=cur&tree=part`.
- 통합검색은 메인 폼(`#totalListForm`, POST). 결과 행에 규정명·조문제목·발췌·개정일·`popfullscreenEx3(lawSeq, historySeq)`. 한 페이지 10건, `goPage…` 링크로 넘김.
- 최신 제·개정 목록 1445건/145쪽, 개정예고 157건. 예고 상세는 POST(`revisionNoticeView.do`)라 링크로는 예고안 HWP 다운로드 URL을 쓴다.
- 개정일 포함 카탈로그 전체 수집은 규정당 1회 요청(2초 간격)이라 4~5분. `previous`를 주면 개정판이 바뀐 규정만 다시 조회한다. (2026-10-27 실행: 123건 전부 개정일 채움, 2009-12-24 ~ 2026-10-15)

### KRX (2026-10-27 확인, 상세는 `selectors.krx.json`과 `krx.py` 머리말)

- **현행 규정 82건.** 목록 화면은 "총 84건"이라 표시하지만 실제로 내려오는 행은 82건(3쪽이 18건). 원인 미확인.
- 분류: 정관 1, 업무규정 아래 공통규정 7·유가증권시장규정 14·코스닥시장규정 14·코넥스시장규정 9·파생상품시장규정 4·일반상품시장규정 10·증권시장청산결제업무규정 2·장외파생상품청산업무규정 4·거래정보저장업무규정 2·시장감시규정 9·회원관리규정 4. 트리에 없는 2건(손해배상심의위원회 운영세칙, 제단체 가입 및 후원 등에 관한 지침)은 분류 없이 넣는다. 시행세칙 23건.
- 직접 URL 진입 불가(세션·CSRF). `/out/index.do` → 메뉴 클릭(`li` jQuery 핸들러). 메뉴는 KRX규정 / 사전식검색 / 별표·서식 / 최근 제·개정규정 / 규정 제·개정예고.
- 목록: workPage iframe의 ExtJS `gridStore`(`/web/regulation/regulationListData.do`, 쪽당 20건, `pagesize`를 바꿔도 20건). 쪽 넘김은 `gridStore.load({params:{start}})` — 페이지 링크(`li[vo]`)가 하는 것과 같다. 필드: BOOKID(개정판), OBOOKID(규정 고유), TITLE, BOOKCD(규정/세칙/지침), REVCD, REVCHA, PROMULDT(공포일), STARTDT(시행일), PROMULNO, DEPTNAME, NOFORMYN(현재 전부 N).
- **ID: `reg_id` = OBOOKID(개정돼도 같다고 가정 — 연혁상 확인은 아직), `revision_id` = BOOKID.**
- 트리: `Ext.getCmp('treeMM')`, 비동기(`/out/regulation/getTreeNode.do`). 폴더별로 `expand` 하며 요청 간격을 지킨다. 노드 속성에 catcd(folder/doc), bookid, obookid, promuldt 등.
- 본문(`regulationViewPop.do`): `form[name=revi] p.jang` 머리말("[일부개정 2026. 1. 29 규정 제2392호 <시행일 : …>]"), `#innerbody` 아래 `p.jang`·`p.jeol`(장·절), `div.Mjo`(조문, `a[name=bonN] strong`에 제목), `div#hang`/`#ho`, `div.jotag`("[전문개정 …]"), `p.buchik_title` + `#innerbody > div#jo`(부칙).
- 연혁 레이어 `.layerViewW .lv_contents > div`: 최신순, onclick에 개정판 BOOKID. **연혁 날짜는 시행일 기준으로 보이고 목록 공포일과 다를 수 있다**(시장감시규정 시행세칙 2026-10-02 / 2026-10-01). `revision_date`는 공포일.
- 조문 단위 링크 없음: `printJoPop.do`는 POST 전용이고 직접 GET은 500. 원문 링크는 첫 화면 URL + `extra.navigation`("KRX규정 > 업무규정 > 시장감시규정 > 시장감시규정").
- 통합검색: 첫 화면 `#mainSchTxt`에 Enter(keydown) → `/web/search.do`, 결과는 workPage(`/out/sch/outsearch.do`). 쪽당 10건, `nextPage(n)`. 결과는 **규정 단위**(규정명·구분·YYYYMMDD·부서·발췌)라 조문 제목이 없고, 첨부 HWP 결과가 같은 항목에 붙는다.
- 최근 제·개정: "최근 제·개정규정" 메뉴 = 같은 그리드(mtype=recent, 96건). 개정예고: "규정 제·개정예고" 게시판(`/web/pds/pdsListData.do`, 426건, TITLE·UPDT만, **종료일 없음**).
- `current_revision_date`는 목록 그리드를 앞쪽부터 넘기며 OBOOKID를 찾는다(메뉴 진입 + 1~5회). 연혁은 `open_regulation` 결과의 `extra.history`에 있다.
- 실사이트 테스트 한 번(카탈로그+쌍 열기+개정일+최근 180일+검색)에 1분 남짓.

### 법제처 (2026-10-27 확인, 상세는 `selectors.lawgo.json`과 `lawgo.py` 머리말)

- **직접 URL이 된다**(계획서 미확인 항목 해소): `/법령/{공백 뺀 법령명}`, `/행정규칙/{규칙명}`, 조문은 `/법령/자본시장과금융투자업에관한법률/제8조의2`, `/행정규칙/금융투자업규정/제4-20조` — 조문 단위 원문 링크로 쓴다.
- 바깥 페이지는 1KB 정적 HTML, 본문은 `lawService` iframe(`/LSW//lsInfoP.do?lsiSeq=…&efYd=YYYYMMDD…`, 행정규칙은 `admRulInfoP.do?admRulSeq=…`). **바깥 페이지를 브라우저로 열면 iframe 본문(자본시장법 약 3MB)까지 받으므로**, 개정판 확인은 같은 출처(`/robots.txt`)에 서서 `fetch`로 바깥 HTML만 받아 iframe 주소를 정규식으로 읽는다. `revision_id` = lsiSeq/admRulSeq, 시행일 = efYd. 행정규칙은 iframe 주소에 시행일이 없어 본문 머리말에서 채운다.
- 본문 페이지는 load 뒤에 AJAX로 그려진다("Loading data..."). `div.lawcon` 개수가 1초간 그대로일 때까지 기다린다. 자본시장법 조문 596개·부칙 71개, 처음 9초 / 캐시 0.5초.
- 본문 구조(법령·행정규칙 같음): `div.lawcon`(조문) 안 `span.bl`(제목), `p`마다 항(`pty1_de2_1`)·호(`pty1_de2h`)·목(`pty1_de3`); `p.gtit`(편장절관, 글로 수준 판단); `#arDivArea p.pty3`(부칙 라벨) + `p.pty3_dep*`.
- 검색: `lsSc.do?menuId=1&query=`(법령) / `admRulSc.do?menuId=5&query=`(행정규칙)을 연 뒤 "조문내용" 탭(`a[id^=tab]`)을 누르면 AJAX로 조문 단위 결과(표 머리줄에 "조문명"). 탭 URL을 직접 열면 0건. 결과 `li[id^=liBgcolor]`(법령은 `left_li` 클래스도 있음, 행정규칙은 없음), 조문 링크 `fJoView(…, 개정판ID, 조번호, …)`. **같은 법령의 시행예정 개정판이 따로 나오고 "앞으로 시행될 법령" 아이콘**이 붙는다 → `extra.status`.
- **한계: 검색은 대상별 첫 쪽 50건만 읽는다.** "투자권유"처럼 많이 걸리는 말은 잘린다(법령 50 + 행정규칙 50). 화이트리스트 안은 4단계에서 캐시 본문 로컬 검색으로 보완할 것.
- **한계: `open_regulation`은 현행만 연다.** 시행예정 개정판 본문은 아직 못 연다(검색 결과에서 존재만 표시). 자본시장법은 2026-10-27 기준 시행예정 개정판이 3개(2026-11-13, 2026-12-09, 2027-02-04).
- `recent_changes`는 화이트리스트 법령의 현행 시행일만 본다(사이트 전체 최신법령 아님, 시행예정 아님).
- 화이트리스트 14개 모두 법제처 이름과 정확히 맞음(실사이트 테스트가 매번 확인).

## cdp.py 사용 메모 (어댑터 작성자용)

- `with Browser.launch() as b: page = b.new_page()`; `page.goto(url)`은 HTTP 상태를 돌려주고, 실패 시 2회 재시도 후 스크린샷과 함께 `NavigationError`.
- 차단 문구가 보이면 `BlockedError`(재시도 없음). 본문 검사는 3000자 이하 페이지에서만 한다(규정 본문 오탐 방지). 문구 목록은 `cdp.BLOCK_PATTERNS`.
- iframe: `page.frames()`로 id를 얻어 `evaluate(..., frame_id=)`. 격리 월드라 DOM만 공유한다. 교차 출처(별도 프로세스) iframe은 아직 미지원 — 법제처에서 필요하면 `Target.setAutoAttach`로 확장.
- `wait_for(expr)`은 JS 기준 참 거짓으로 판단한다(DOM 요소도 참).
- 창을 띄워 보려면 `REGSEARCH_HEADFUL=1`. Chrome 경로 지정은 `REGSEARCH_CHROME`.

## 다음 할 일

1. 6단계 마무리: 2026-10-27 재시험 2건(새 세션, 실제 질문만) 통과 — ① 근거 탐색 "외화증권 판매 시 추가 설명 근거" → 스킬 자동 호출, 표준투자권유준칙 15.(외화증권 등에 대한 설명의무 특칙)·14.·영업규정 제2-5조·금소법 시행령 제13조·감독규정 제12조를 라이브 확인, `cases.py draft`로 초안 파일 생성 후 "확정 초안" 블록 표시. ② 직접 지정 "KRX 시장감시규정 제3조" → `lookup.py article` 바로 사용, 장·절 경로·시점(공포번호·시행일 포함), KRX 찾아가는 경로 안내, 초안 생성. 두 세션 모두 확정 질문 문장은 본문이 아니라 앱의 다음 단계 상자로 낸 것으로 보인다(본문에는 없음). 남은 시험: 변경 확인 유형. 현업에게 초기 사례 10~20건(계획서 확정 절차 5번)을 받아 `cases.py draft/confirm`으로 넣기.
2. 현업 검토 받기: `references/aliases.json`(약칭), `references/eval_queries.json`(실제 질문·정답), `references/scope.json`(화이트리스트).
2-1. 7단계 나머지: 현업 PC에서 zip을 풀고 `check_env.py` 통과 확인(개발 PC에서는 임시 위치 설치로 검증 완료). 배포할 때마다 `python tools/package.py --refresh-catalog`.
2-2. ~~매칭 개선 과제(fork 세션 보고): "관리종목인"의 "인"을 떼지 못함~~ → 2026-10-27 해결(아래 결정 로그).
3. 매칭 조정 후보(4단계 측정 참고): 약칭이 잡힌 질문에서 site_search 점수 낮추기.
4. 법제처 시행예정 개정판 본문 열기(검색 결과의 개정판 ID로 `lsInfoP.do?lsiSeq=`를 열면 될 것으로 보이나 미확인).
5. 열린 확인: KRX OBOOKID가 개정 전후로 같은지, KRX "총 84건 vs 82건" 차이, 법제처 검색 2쪽 이상 넘기기.
6. 7단계 배포 때 `seed/catalog.json`을 새로 만들어 넣는다(`catalog.py refresh` 후 사용자 catalog.json 복사).
7. `git init` — 이후 단계를 세션 나눠 병행할 때 필요.
- 참고: KOFIA 과거 개정판 본문은 `RevisionInfo.history`의 revision_id를 `Regulation.revision_id`에 넣고 `open_regulation`을 부르면 된다(매매거래계좌설정약관 1706 → 2025-05-13 확인).

## 명령줄 (KOFIA, KRX도 같은 꼴 — `krx.py`는 catalog에 `--no-dates`, search에 `--field`·`--history`가 없다)

```
python .claude/skills/regulation-search/scripts/adapters/kofia.py catalog [--no-dates] [--out FILE]
python .claude/skills/regulation-search/scripts/adapters/kofia.py article <규정명|ID> [조문번호] [--title 제목]
python .claude/skills/regulation-search/scripts/adapters/kofia.py revision <규정명|ID>
python .claude/skills/regulation-search/scripts/adapters/kofia.py search <검색어> [--field content|title|attach] [--pages N]
python .claude/skills/regulation-search/scripts/adapters/kofia.py recent [--days 30]
```
