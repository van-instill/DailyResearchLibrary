import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import notify


def _paper(title, url="https://example.org/p1", topic="波动率建模与高频计量", venue="", journal=""):
    paper = {
        "title": title,
        "paper_url": url,
        "best_match": {"topic_name": topic},
    }
    if venue:
        paper["venue"] = venue
    if journal:
        paper["conference"] = journal
    return paper


class BuildMessageTest(unittest.TestCase):
    def test_markdown_counts_both_collections(self):
        daily = [_paper("A study of HAR"), _paper("Another one", url="https://example.org/p2")]
        journal = [_paper("Volatility forecasting", venue="Journal of Econometrics")]
        title, body = notify.build_markdown(daily, journal, "2026-W42", "https://x.github.io/repo/")
        self.assertIn("2026-W42", title)
        self.assertIn("本周新收录 3 篇", body)
        self.assertIn("arXiv 2 篇 · 顶刊 1 篇", body)
        # 两集合都在，给出顶刊专属链接
        self.assertIn("https://x.github.io/repo/#latest", body)
        self.assertIn("https://x.github.io/repo/#journal-latest", body)
        self.assertIn("Journal of Econometrics", body)

    def test_only_journal_week_has_no_secondary_link(self):
        journal = [_paper("Solo", venue="Review of Financial Studies")]
        _, body = notify.build_markdown([], journal, "2026-W43", "https://x.github.io/repo")
        self.assertIn("本周新收录 1 篇", body)
        self.assertNotIn("#journal-latest", body)

    def test_every_paper_gets_its_own_link(self):
        daily = [_paper(f"Paper {i}", url=f"https://example.org/{i}") for i in range(3)]
        _, body = notify.build_markdown(daily, [], "2026-W44", "https://x.github.io/repo")
        for i in range(3):
            self.assertIn(f"https://example.org/{i}", body)

    def test_markdown_special_chars_in_title_are_escaped(self):
        daily = [_paper("Returns [t] and *alpha*_x`y")]
        _, body = notify.build_markdown(daily, [], "2026-W45", "https://x.github.io/repo")
        self.assertIn("\\[t\\]", body)
        self.assertIn("\\*alpha\\*", body)
        self.assertIn("\\_x", body)

    def test_more_than_ten_papers_is_capped_not_crashed(self):
        # 曾经这里会 IndexError：编号只有一个 ①—⑩ 的字符串
        daily = [_paper(f"D{i}", url=f"https://e.org/d{i}") for i in range(12)]
        journal = [_paper(f"J{i}", url=f"https://e.org/j{i}", venue="Quantitative Economics") for i in range(12)]
        title, body = notify.build_markdown(daily, journal, "2026-W50", "https://x.github.io/r")
        self.assertIn("24", title)
        self.assertEqual(body.count("](https://e.org/"), 10)
        self.assertIn("另有 14 篇", body)
        html_body = notify.build_html(daily, journal, "2026-W50", "https://x.github.io/r")
        self.assertEqual(html_body.count("https://e.org/"), 0)  # html 版不嵌单篇链接

    def test_marker_falls_back_after_ten(self):
        self.assertEqual(notify.marker(0), "①")
        self.assertEqual(notify.marker(9), "⑩")
        self.assertEqual(notify.marker(10), "11")
        self.assertEqual(notify.marker(42), "43")

    def test_long_venue_is_truncated(self):
        journal = [_paper("T", venue="Some Extremely Long Journal Name That Goes On And On Forever")]
        _, body = notify.build_markdown([], journal, "2026-W46", "https://x.github.io/repo")
        self.assertIn("…", body)
        self.assertNotIn("Goes On And On Forever", body)

    def test_html_escapes_title(self):
        daily = [_paper("<script>alert(1)</script>")]
        html_body = notify.build_html(daily, [], "2026-W47", "https://x.github.io/repo")
        self.assertNotIn("<script>alert", html_body)
        self.assertIn("&lt;script&gt;", html_body)


class ChannelTest(unittest.TestCase):
    def test_serverchan_endpoint_plain_key(self):
        self.assertEqual(
            notify.serverchan_endpoint("SCT123abc"),
            "https://sctapi.ftqq.com/SCT123abc.send",
        )

    def test_serverchan_endpoint_sc3_key(self):
        # sctp 开头的新 key 要换成 push.ft07.com，uid 夹在 sctp 和 t 之间
        self.assertEqual(
            notify.serverchan_endpoint("sctp8890tAbCdEf"),
            "https://8890.push.ft07.com/send/sctp8890tAbCdEf.send",
        )

    def test_enabled_channels_only_configured(self):
        with mock.patch.dict(os.environ, {"SERVERCHAN_SENDKEY": "k", "PUSHPLUS_TOKEN": ""}, clear=False):
            os.environ.pop("WXPUSHER_APP_TOKEN", None)
            os.environ.pop("WECOM_BOT_WEBHOOK", None)
            os.environ.pop("NOTIFY_WEBHOOK_URL", None)
            os.environ.pop("NOTIFY_CHANNELS", None)
            names = [name for name, _ in notify.enabled_channels()]
        self.assertEqual(names, ["serverchan"])

    def test_notify_channels_can_restrict(self):
        env = {"SERVERCHAN_SENDKEY": "k", "PUSHPLUS_TOKEN": "p", "NOTIFY_CHANNELS": "pushplus"}
        with mock.patch.dict(os.environ, env, clear=False):
            names = [name for name, _ in notify.enabled_channels()]
        self.assertEqual(names, ["pushplus"])


class MainFlowTest(unittest.TestCase):
    def _repo(self, root: Path, daily_new: int, journal_new: int, period="2026-W42"):
        data = root / "web" / "data"
        data.mkdir(parents=True)
        for name, count in (("papers.json", daily_new), ("conference_papers.json", journal_new)):
            papers = [
                {**_paper(f"{name} {i}", url=f"https://e.org/{name}{i}"), "is_new_this_week": True}
                for i in range(count)
            ] + [{**_paper("old"), "is_new_this_week": False}]
            (data / name).write_text(
                json.dumps({"weekly_period": period, "papers": papers}, ensure_ascii=False),
                encoding="utf-8",
            )

    def test_skips_when_nothing_new(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root, 0, 0)
            with mock.patch.dict(os.environ, {"SERVERCHAN_SENDKEY": "k"}, clear=False):
                with mock.patch.object(notify, "send_serverchan") as sender:
                    code = notify.main(["--root", str(root)])
            self.assertEqual(code, 0)
            sender.assert_not_called()

    def test_sends_once_then_remembers_period(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root, 2, 1)
            env = {"SERVERCHAN_SENDKEY": "k"}
            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch.object(notify, "send_serverchan", return_value="ok") as sender:
                    self.assertEqual(notify.main(["--root", str(root)]), 0)
                    self.assertEqual(notify.main(["--root", str(root)]), 0)
            self.assertEqual(sender.call_count, 1)
            state = json.loads((root / ".paper-cache" / "notify_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["last_period"], "2026-W42")
            self.assertEqual(state["paper_count"], 3)

    def test_force_resends_same_period(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root, 1, 0)
            with mock.patch.dict(os.environ, {"SERVERCHAN_SENDKEY": "k"}, clear=False):
                with mock.patch.object(notify, "send_serverchan", return_value="ok") as sender:
                    notify.main(["--root", str(root)])
                    notify.main(["--root", str(root), "--force"])
            self.assertEqual(sender.call_count, 2)

    def test_all_channels_failing_keeps_state_for_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root, 1, 0)
            with mock.patch.dict(os.environ, {"SERVERCHAN_SENDKEY": "k"}, clear=False):
                with mock.patch.object(notify, "send_serverchan", side_effect=RuntimeError("boom")):
                    code = notify.main(["--root", str(root)])
            self.assertEqual(code, 1)
            self.assertFalse((root / ".paper-cache" / "notify_state.json").exists())

    def test_dry_run_never_sends(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root, 1, 1)
            with mock.patch.dict(os.environ, {"SERVERCHAN_SENDKEY": "k"}, clear=False):
                with mock.patch.object(notify, "send_serverchan") as sender:
                    self.assertEqual(notify.main(["--root", str(root), "--dry-run"]), 0)
            sender.assert_not_called()

    def test_notify_enabled_false_is_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root, 1, 0)
            env = {"SERVERCHAN_SENDKEY": "k", "NOTIFY_ENABLED": "false"}
            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch.object(notify, "send_serverchan") as sender:
                    self.assertEqual(notify.main(["--root", str(root)]), 0)
            sender.assert_not_called()


if __name__ == "__main__":
    unittest.main()
