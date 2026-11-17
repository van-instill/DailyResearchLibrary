import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from sync_exclusions import extract_payload, merge_entries, normalize_entries  # noqa: E402


class ExtractPayloadTest(unittest.TestCase):
    def test_reads_fenced_json_block(self):
        body = '说明文字\n```json\n{"papers":[{"id":"2403.00819v2","title":"t"}]}\n```\n'
        self.assertEqual(extract_payload(body)["papers"][0]["id"], "2403.00819v2")

    def test_reads_bare_json(self):
        self.assertEqual(extract_payload('{"papers":[{"id":"a"}]}')["papers"][0]["id"], "a")

    def test_reads_bare_list(self):
        self.assertEqual(extract_payload('[{"id":"a"}]')["papers"][0]["id"], "a")

    def test_returns_empty_on_garbage(self):
        self.assertEqual(extract_payload("没有 JSON"), {})


class NormalizeEntriesTest(unittest.TestCase):
    def test_drops_entries_without_id_and_title(self):
        entries = normalize_entries({"papers": [{"reason": "x"}, {"id": "a"}, {"title": "b"}]})
        self.assertEqual([e.get("id") for e in entries], ["a", ""])
        self.assertEqual(entries[1]["title"], "b")

    def test_defaults_reason(self):
        self.assertEqual(normalize_entries({"papers": [{"id": "a"}]})[0]["reason"], "用户在前端手动删除")


class MergeEntriesTest(unittest.TestCase):
    def test_appends_and_dedupes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "exclude.json"
            path.write_text(json.dumps({"comment": "c", "papers": [{"id": "old", "title": "old"}]}), encoding="utf-8")
            entries = [
                {"id": "2403.00819v2", "title": "new", "reason": "r"},
                {"id": "OLD", "title": "old", "reason": "r"},
            ]
            added, skipped = merge_entries(path, entries)
            self.assertEqual((added, skipped), (1, 1))
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual([item["id"] for item in data["papers"]], ["old", "2403.00819v2"])

    def test_creates_file_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "config" / "exclude.json"
            added, _ = merge_entries(path, [{"id": "a", "title": "t", "reason": "r"}])
            self.assertEqual(added, 1)
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
