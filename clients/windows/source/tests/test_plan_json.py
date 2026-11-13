"""Windows canonical JSON loader: no HTTP, no session state."""
import json
from pathlib import Path
import tempfile
import unittest

from core.plan_json import load_plan_json


def rec(**extra):
    data = dict(source="ncwu.plan", course_id="000001", class_id="001-02",
                course_name="工程制图", teacher="老师", remaining=3,
                observed_at="2026-11-13T12:00:00+08:00")
    data.update(extra)
    return data


class JsonViewerTests(unittest.TestCase):
    def save(self, folder, body):
        path = folder / "snapshot.json"
        path.write_text(body, encoding="utf-8-sig")
        return path

    def test_roundtrip_multiple_sources_and_unknown(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            rows = [rec(), rec(source="qiangke.table", course_id="000002",
                               remaining=None, observed_at=None)]
            got = load_plan_json(self.save(root, json.dumps(rows, ensure_ascii=False)))
        self.assertEqual(len(got.rows), 2)
        self.assertEqual(got.rows[0].course_id, "000001")
        self.assertEqual(got.rows[1].remaining_label, "未知")
        self.assertEqual(got.sources, ("ncwu.plan", "qiangke.table"))
        self.assertEqual(len(got.observed_at), 1)

    def test_rejects_invalid_privacy_fields_and_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            invalid = ([rec(password="secret")], [rec(remaining=-1)],
                       [rec(remaining=True)], [rec(course_id=123)],
                       [rec(source="unverified")],
                       [rec(observed_at="2026-11-13T12:00:00")],
                       {"entries": [rec()]})
            for data in invalid:
                with self.subTest(data=repr(data)[:45]), self.assertRaises(ValueError):
                    load_plan_json(self.save(root, json.dumps(data)))
            with self.assertRaises(ValueError):
                load_plan_json(self.save(root, '[{"source":"ncwu.plan","source":"ncwu.plan"}]'))
            with self.assertRaises(ValueError):
                load_plan_json(self.save(root, "[NaN]"))

    def test_rejects_file_over_limit(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            target = self.save(root, "[]")
            with target.open("ab") as f:
                f.truncate(2 * 1024 * 1024 + 1)
            with self.assertRaises(ValueError):
                load_plan_json(target)


if __name__ == "__main__":
    unittest.main()
