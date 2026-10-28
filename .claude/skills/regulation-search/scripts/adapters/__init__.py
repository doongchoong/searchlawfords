"""사이트 어댑터. 상위 로직은 base.Adapter 인터페이스만 호출한다."""
from __future__ import annotations

import importlib

# 사이트 이름 → "모듈:클래스". 필요할 때만 import한다.
ADAPTERS = {
    "KOFIA": "adapters.kofia:KofiaAdapter",
    "KRX": "adapters.krx:KrxAdapter",
    "법제처": "adapters.lawgo:LawgoAdapter",
}
SITES = list(ADAPTERS)


def adapter_class(site: str):
    module, cls = ADAPTERS[site].split(":")
    return getattr(importlib.import_module(module), cls)


def get_adapter(site: str, browser=None):
    """사이트 어댑터를 만든다. browser를 넘기면 여러 어댑터가 Chrome 하나를 같이 쓴다."""
    return adapter_class(site)(browser=browser)
