"""store.py 동시 쓰기 테스트. 실제로 여러 Python 프로세스를 띄워 같은 파일에 쓴다.

    python -m unittest discover -s tests
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import store  # noqa: E402

WORKERS = 6


def run_workers(code: str, data_dir: str, n: int = WORKERS) -> None:
    prelude = f"import sys; sys.path.insert(0, {str(SCRIPTS)!r}); import store, os, time\n"
    env = dict(os.environ, REGSEARCH_DATA_DIR=data_dir)
    procs = [
        subprocess.Popen([sys.executable, "-c", prelude + code.replace("WORKER", str(i))], env=env)
        for i in range(n)
    ]
    for p in procs:
        assert p.wait(timeout=120) == 0, "작업 프로세스 실패"


class StoreConcurrencyTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self._old = os.environ.get("REGSEARCH_DATA_DIR")
        os.environ["REGSEARCH_DATA_DIR"] = self.dir

    def tearDown(self):
        if self._old is None:
            os.environ.pop("REGSEARCH_DATA_DIR", None)
        else:
            os.environ["REGSEARCH_DATA_DIR"] = self._old
        self._tmp.cleanup()

    def test_concurrent_append_keeps_every_line(self):
        run_workers(
            "for j in range(40):\n"
            "    store.append_jsonl(store.data_dir() / 'cases.jsonl', {'w': WORKER, 'j': j, '주제': '동시 쓰기'})\n",
            self.dir,
        )
        records = store.read_jsonl(Path(self.dir) / "cases.jsonl")
        self.assertEqual(len(records), WORKERS * 40)
        self.assertEqual(len({(r["w"], r["j"]) for r in records}), WORKERS * 40)

    def test_concurrent_update_json_loses_no_increment(self):
        run_workers(
            "for _ in range(30):\n"
            "    store.update_json(store.data_dir() / 'catalog.json', lambda c: {'n': c['n'] + 1}, default={'n': 0})\n",
            self.dir,
        )
        self.assertEqual(store.read_json(Path(self.dir) / "catalog.json"), {"n": WORKERS * 30})

    def test_throttle_spaces_requests_across_processes(self):
        interval = 0.2
        run_workers(
            "for _ in range(3):\n"
            f"    store.throttle('example.com', {interval})\n"
            "    store.append_jsonl(store.data_dir() / 'stamps.jsonl', {'t': time.time()})\n",
            self.dir,
            n=4,
        )
        stamps = sorted(r["t"] for r in store.read_jsonl(Path(self.dir) / "stamps.jsonl"))
        self.assertEqual(len(stamps), 12)
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        # 기록은 throttle 직후라 약간 밀릴 수 있으므로 여유를 둔다
        self.assertGreaterEqual(min(gaps), interval * 0.8, gaps)

    def test_read_jsonl_skips_broken_line(self):
        path = Path(self.dir) / "cases.jsonl"
        path.write_text('{"id": 1}\n{깨진 줄\n{"id": 2}\n', encoding="utf-8")
        self.assertEqual([r["id"] for r in store.read_jsonl(path)], [1, 2])

    def test_append_after_hand_edit_without_trailing_newline(self):
        path = Path(self.dir) / "cases.jsonl"
        path.write_text('{"id": 1}', encoding="utf-8")
        store.append_jsonl(path, {"id": 2})
        self.assertEqual([r["id"] for r in store.read_jsonl(path)], [1, 2])

    def test_write_json_leaves_no_temp_files(self):
        path = Path(self.dir) / "catalog.json"
        store.write_json(path, {"규정": ["a"]})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"규정": ["a"]})
        self.assertEqual([p.name for p in Path(self.dir).glob(".catalog.json.*")], [])


if __name__ == "__main__":
    unittest.main()
