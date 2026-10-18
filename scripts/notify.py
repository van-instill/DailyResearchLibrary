#!/usr/bin/env python3
"""把「本周新增论文」推送到微信。

微信个人号没有开放接口，「文件传输助手」无法被程序投放消息。可行的正经路线是
借微信服务号的消息通道：程序把内容发给一个推送服务，服务用它的服务号给你发一条
消息卡片，点开即进入论文雷达的「本期新增」页。

支持的通道（配了哪个环境变量就用哪个，可同时配多个）：

    SERVERCHAN_SENDKEY   Server酱（方糖服务号），免费 5 条/天
    PUSHPLUS_TOKEN       PushPlus（推送加服务号）
    WXPUSHER_APP_TOKEN    WxPusher（+ WXPUSHER_UIDS）
    WECOM_BOT_WEBHOOK    企业微信群机器人（消息可同步到微信「企业微信」入口）
    NOTIFY_WEBHOOK_URL   任意自定义 webhook，POST 一个 JSON

只推一次：状态记在 .paper-cache/notify_state.json，同一期重跑不会重复打扰。
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_SITE = "https://van-instill.github.io/DailyResearchLibrary/"
STATE_NAME = "notify_state.json"
MAX_ITEMS = 10
TIMEOUT = 20

MARK = "①②③④⑤⑥⑦⑧⑨⑩"


# ---------------------------------------------------------------- 数据准备


def load_payload(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def fresh_papers(root: Path) -> tuple[list[dict], list[dict], str]:
    """返回 (arXiv 本期新增, 顶刊本期新增, 期号)。"""
    daily = load_payload(root / "web" / "data" / "papers.json")
    journal = load_payload(root / "web" / "data" / "conference_papers.json")

    def pick(payload: dict) -> list[dict]:
        return [p for p in payload.get("papers") or [] if p.get("is_new_this_week")]

    period = daily.get("weekly_period") or journal.get("weekly_period") or ""
    if not period:
        iso = datetime.now(timezone.utc).isocalendar()
        period = f"{iso.year}-W{iso.week:02d}"
    return pick(daily), pick(journal), period


# ---------------------------------------------------------------- 文案渲染


def md_escape(text: str) -> str:
    """标题里的 * _ [ ] ` 会打断 markdown，转义掉，避免整段格式崩掉。"""
    out = []
    for ch in str(text or ""):
        if ch in "\\*_[]`":
            out.append("\\" + ch)
        elif ch == "\n":
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out).strip()


def short_venue(paper: dict, fallback: str) -> str:
    venue = paper.get("venue") or paper.get("conference") or ""
    if not venue:
        return fallback
    venue = str(venue).split("(")[0].strip()
    return venue if len(venue) <= 34 else venue[:33] + "…"


def entry_line(index: int, paper: dict, group: str) -> str:
    """一条论文：编号 + 标题（带链接）+ 期刊/方向。"""
    title = md_escape(paper.get("title") or "Untitled")
    url = paper.get("paper_url") or paper.get("doi") or paper.get("pdf_url") or ""
    head = f"**{MARK[index]} {title}**"
    if url:
        head = f"**[{MARK[index]} {title}]({url})**"
    topic = ((paper.get("best_match") or {}).get("topic_name") or "").strip()
    meta = " · ".join(x for x in (short_venue(paper, group), topic) if x)
    return f"{head}\n{meta}"


def build_markdown(new_daily: list[dict], new_journal: list[dict], period: str, site: str) -> tuple[str, str]:
    total = len(new_daily) + len(new_journal)
    title = f"论文雷达 {period}｜本周新增 {total} 篇"

    counts = []
    if new_daily:
        counts.append(f"arXiv {len(new_daily)} 篇")
    if new_journal:
        counts.append(f"顶刊 {len(new_journal)} 篇")

    lines = [f"**本周新收录 {total} 篇**（{' · '.join(counts)}）", ""]
    index = 0
    for group, papers in (("arXiv", new_daily), ("顶刊", new_journal)):
        if not papers:
            continue
        lines.append(f"**{group}**")
        lines.append("")
        for paper in papers[:MAX_ITEMS]:
            lines.append(entry_line(index, paper, group))
            lines.append("")
            index += 1
    base = site.rstrip("/")
    lines.append(f"👉 [**打开论文雷达 · 本期新增**]({base}/#latest)")
    if new_daily and new_journal:
        lines.append("")
        lines.append(f"[只顶刊的 {len(new_journal)} 篇]({base}/#journal-latest)")
    return title, "\n".join(lines).strip()


def build_html(new_daily: list[dict], new_journal: list[dict], period: str, site: str) -> str:
    total = len(new_daily) + len(new_journal)
    rows = []
    index = 0
    for group, papers in (("arXiv", new_daily), ("顶刊", new_journal)):
        for paper in papers[:MAX_ITEMS]:
            sub = " · ".join(
                x
                for x in (
                    short_venue(paper, group),
                    ((paper.get("best_match") or {}).get("topic_name") or "").strip(),
                )
                if x
            )
            rows.append(
                '<div style="padding:10px 0;border-bottom:1px solid #eceff3">'
                f'<div style="font-size:14px;line-height:1.5;color:#1b1f24;font-weight:600">'
                f"{MARK[index]} {html.escape(paper.get('title') or 'Untitled')}</div>"
                f'<div style="margin-top:4px;font-size:12px;color:#6b7280">{html.escape(sub)}</div>'
                "</div>"
            )
            index += 1
    body = "".join(rows)
    base = site.rstrip("/")
    secondary = ""
    if new_daily and new_journal:
        secondary = (
            f'<a href="{html.escape(base + "/#journal-latest")}" '
            'style="display:inline-block;margin-left:8px;padding:10px 16px;background:#eef2f9;'
            'color:#2f6fed;border-radius:8px;text-decoration:none;font-size:14px">只看顶刊</a>'
        )
    return (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif;max-width:640px">'
        f'<div style="font-size:13px;color:#6b7280;margin-bottom:6px">论文雷达 · {html.escape(period)}</div>'
        f'<div style="font-size:20px;font-weight:700;color:#1b1f24;margin-bottom:4px">本周新增 {total} 篇</div>'
        f'<div style="font-size:13px;color:#6b7280;margin-bottom:12px">'
        f"arXiv {len(new_daily)} 篇 · 顶刊 {len(new_journal)} 篇</div>"
        f"{body}"
        f'<div style="margin-top:16px"><a href="{html.escape(base + "/#latest")}" '
        'style="display:inline-block;padding:10px 16px;background:#2f6fed;color:#fff;'
        f'border-radius:8px;text-decoration:none;font-size:14px">打开论文雷达 · 本期新增</a>{secondary}</div>'
        "</div>"
    )


# ---------------------------------------------------------------- 通道实现


def _post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def _post_form(url: str, payload: dict) -> dict:
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def serverchan_endpoint(sendkey: str) -> str:
    """SC3 的 key 以 sctp 开头，走 push.ft07.com；老 key 走 sctapi。"""
    key = (sendkey or "").strip()
    if key.startswith("sctp"):
        uid = key[4:].split("t", 1)[0]
        if uid.isdigit():
            return f"https://{uid}.push.ft07.com/send/{key}.send"
    return f"https://sctapi.ftqq.com/{key}.send"


def send_serverchan(title: str, markdown: str, _html: str) -> str:
    key = os.environ.get("SERVERCHAN_SENDKEY", "").strip()
    resp = _post_form(serverchan_endpoint(key), {"title": title, "desp": markdown})
    if resp.get("code") != 0:
        raise RuntimeError(f"serverchan: {resp}")
    return "ok"


def send_pushplus(title: str, markdown: str, _html: str) -> str:
    token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    resp = _post_json(
        "https://www.pushplus.plus/send",
        {"token": token, "title": title, "content": markdown, "template": "markdown"},
    )
    if resp.get("code") != 200:
        raise RuntimeError(f"pushplus: {resp}")
    return "ok"


def send_wxpusher(title: str, _markdown: str, html_body: str) -> str:
    app_token = os.environ.get("WXPUSHER_APP_TOKEN", "").strip()
    uids = [u.strip() for u in os.environ.get("WXPUSHER_UIDS", "").split(",") if u.strip()]
    payload = {
        "appToken": app_token,
        "content": html_body,
        "summary": title[:99],
        "contentType": 2,
        "url": os.environ.get("SITE_URL", DEFAULT_SITE).rstrip("/") + "/#latest",
    }
    if uids:
        payload["uids"] = uids
    else:
        payload["topicIds"] = [int(t) for t in os.environ.get("WXPUSHER_TOPIC_IDS", "").split(",") if t.strip().isdigit()]
    resp = _post_json("https://wxpusher.zjiecode.com/api/send/message", payload)
    if resp.get("code") != 1000:
        raise RuntimeError(f"wxpusher: {resp}")
    return "ok"


def send_wecom_bot(title: str, markdown: str, _html: str) -> str:
    hook = os.environ.get("WECOM_BOT_WEBHOOK", "").strip()
    resp = _post_json(hook, {"msgtype": "markdown", "markdown": {"content": f"**{title}**\n{markdown}"}})
    if resp.get("errcode") not in (0, None):
        raise RuntimeError(f"wecom: {resp}")
    return "ok"


def send_generic(title: str, markdown: str, html_body: str) -> str:
    hook = os.environ.get("NOTIFY_WEBHOOK_URL", "").strip()
    site = os.environ.get("SITE_URL", DEFAULT_SITE).rstrip("/") + "/#latest"
    resp = _post_json(hook, {"title": title, "text": markdown, "markdown": markdown, "html": html_body, "url": site})
    return "ok" if resp is not None else "ok"


CHANNELS = (
    ("serverchan", "SERVERCHAN_SENDKEY", "send_serverchan"),
    ("pushplus", "PUSHPLUS_TOKEN", "send_pushplus"),
    ("wxpusher", "WXPUSHER_APP_TOKEN", "send_wxpusher"),
    ("wecom_bot", "WECOM_BOT_WEBHOOK", "send_wecom_bot"),
    ("webhook", "NOTIFY_WEBHOOK_URL", "send_generic"),
)


def enabled_channels() -> list[tuple[str, object]]:
    """NOTIFY_CHANNELS 可以限定只发哪几个，默认自动用所有已配置的。

    函数按名字现取（globals），不在模块加载时绑定，方便测试替换实现。
    """
    wanted = {c.strip() for c in os.environ.get("NOTIFY_CHANNELS", "").split(",") if c.strip()}
    picked = []
    for name, env_key, func_name in CHANNELS:
        if wanted and name not in wanted:
            continue
        if not os.environ.get(env_key, "").strip():
            continue
        fn = globals().get(func_name)
        if callable(fn):
            picked.append((name, fn))
    return picked


# ---------------------------------------------------------------- 主流程


def read_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_state(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="推送本周新增论文到微信")
    parser.add_argument("--root", default=".", help="仓库根目录")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不发送")
    parser.add_argument("--force", action="store_true", help="同一期也强制再推一次")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    state_path = root / ".paper-cache" / STATE_NAME
    site = os.environ.get("SITE_URL", DEFAULT_SITE)

    if os.environ.get("NOTIFY_ENABLED", "true").strip().lower() in ("0", "false", "no", "off"):
        print("NOTIFY_ENABLED=false，跳过推送")
        return 0

    daily, journal, period = fresh_papers(root)
    total = len(daily) + len(journal)
    if total == 0:
        print(f"{period} 没有本期新增论文，跳过推送（期号幂等，说明这周已经推过了）")
        return 0

    title, markdown = build_markdown(daily, journal, period, site)
    html_body = build_html(daily, journal, period, site)

    state = read_state(state_path)
    if not args.force and state.get("last_period") == period:
        print(f"{period} 已于 {state.get('last_sent_at', '?')} 推送过，跳过（--force 可强制重发）")
        return 0

    channels = enabled_channels()
    if args.dry_run:
        print(f"--- dry run · {total} 篇 · 期号 {period} ---")
        print(f"title: {title}\n")
        print(markdown)
        print("\nchannels:", ", ".join(n for n, _ in channels) or "(未配置任何通道)")
        return 0

    if not channels:
        print("没有配置任何推送通道，跳过。可选：SERVERCHAN_SENDKEY / PUSHPLUS_TOKEN / WXPUSHER_APP_TOKEN / WECOM_BOT_WEBHOOK")
        return 0

    failures = []
    for name, fn in channels:
        try:
            fn(title, markdown, html_body)
            print(f"推送成功：{name} · {total} 篇 · {period}")
        except (RuntimeError, urllib.error.URLError, TimeoutError, OSError) as exc:
            failures.append(f"{name}: {exc}")
            print(f"推送失败：{name} -> {exc}", file=sys.stderr)

    if len(failures) == len(channels):
        # 全挂时不写状态，下次定时任务还会再试，不至于漏掉一周
        print("所有通道都失败了", file=sys.stderr)
        return 1

    write_state(
        state_path,
        {
            "last_period": period,
            "last_sent_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "paper_count": total,
            "channels": [n for n, _ in channels],
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
