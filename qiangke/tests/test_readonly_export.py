"""Qiangke read-only CLI exporter tested without network."""
from pathlib import Path
import json
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run as cli
from qk import kingo_xk as X
from test_xk import SAMPLE


class Logger:
    def __init__(self): self.messages = []
    def info(self, text): self.messages.append(str(text))
    def warning(self, text): self.messages.append(str(text))
    def error(self, text): self.messages.append(str(text))


class Fake:
    def __init__(self, rows): self.rows = rows; self.calls = 0
    def open(self): pass
    def login(self): return True
    def list_courses(self, kind):
        self.calls += 1
        return {"rows": self.rows, "window": {}, "total": len(self.rows),
                "table_id": "TEST"}


def main():
    _, rows = X.parse_table(SAMPLE)
    out = cli.normalize_list_rows(rows, "2026-11-13T12:00:00+00:00")
    assert len(out) == 2 and out[0].course_id == "1500120001"
    assert out[0].class_id == "1500120001-101"
    assert out[0].remaining is None
    with tempfile.TemporaryDirectory() as t:
        file = Path(t) / "snapshot.json"
        args = SimpleNamespace(config="mock", kind="by_plan", standard_json=str(file))
        log = Logger()
        with patch.object(cli, "load", return_value={}), patch.object(
                cli, "_adapter", return_value=Fake(rows)):
            assert cli.cmd_list(args, log) == 0
        saved = json.loads(file.read_text(encoding="utf-8"))
        assert len(saved) == 2
        assert saved[0]["source"] == "qiangke.table"
        assert saved[0]["course_id"] == "1500120001"
        assert set(saved[0]) == {"source", "course_id", "class_id", "course_name",
                                 "teacher", "remaining", "observed_at"}
        before = file.read_bytes()
        with patch.object(cli, "load", return_value={}), patch.object(
                cli, "_adapter", return_value=Fake([{"kc": "缺失课程号"}])):
            assert cli.cmd_list(args, log) == 1
        assert before == file.read_bytes()
        with patch.object(cli, "load", return_value={}), patch.object(
                cli, "_adapter", return_value=Fake([])):
            assert cli.cmd_list(args, log) == 1
        assert before == file.read_bytes()
    print("PASS: canonical listing and local snapshot, bad rows never overwrite")
    return 0


if __name__ == "__main__":
    sys.exit(main())
