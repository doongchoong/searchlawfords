# 동봉 Python (runtime/python)

현업 PC에 Python이 없어도 스킬이 돌도록 넣은 python.org 공식 **Windows embeddable package (64-bit)**이다.
설치하지 않고 이 폴더의 `python.exe`를 바로 쓴다. 스킬 스크립트는 표준 라이브러리만 쓰므로 pip이 필요 없다.

- 버전: Python 3.14.6 (`python-3.14.6-embed-amd64.zip`, 12,570,832 bytes)
- SHA256: `df901e84a896ff1ee720ad03377e0c8d8c2244fda79808aeeaff6316df1cb75c` (python.org 릴리스 페이지 값과 일치 확인, 2026-10-27)
- `python.exe` 디지털 서명: Python Software Foundation (유효, 2026-10-27 확인)
- 라이선스: `python/LICENSE.txt` (PSF License — 재배포 허용)
- 바꾼 것: `python/python314._pth`에 `../../scripts` 한 줄을 더했다(스킬 모듈 import용). 나머지는 원본 그대로.

업데이트할 때: 같은 방식으로 새 embeddable zip을 받아 python.org의 SHA256과 대조한 뒤 이 폴더를 통째로 바꾸고,
`._pth`에 `../../scripts`를 다시 넣고, `tests/`를 이 Python으로 돌려 확인한다.
