"""把前端「删除」同步到永久排除清单。

流程：网页上点「同步到仓库」→ 打开一个正文是 JSON 的 GitHub issue（标题固定
`Excluded Papers`）→ 本脚本在 Actions 里读这条 issue，把里面的论文合并进
`config/exclude.json`，然后回一句评论并关掉 issue。

issue 正文里可以只有一段 ```json 代码块，直接贴裸 JSON 也认。
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_EXCLUDE_PATH = "config/exclude.json"
ISSUE_TITLE = "Excluded Papers"
JSON_BLOCK = re.compile(r"```(?:json)?\s*(.+?)```", re.DOTALL | re.IGNORECASE)


def github_request(url: str, token: str, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "paper-radar-sync/1.0",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body.strip() else {}


def extract_payload(body: str) -> dict[str, Any]:
    """从 issue 正文里抠出 JSON，容忍前后有说明文字。"""
    text = (body or "").strip()
    if not text:
        return {}
    candidates: list[str] = []
    for block in JSON_BLOCK.findall(text):
        candidates.append(block.strip())
    candidates.append(text)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"papers": parsed}
    return {}


def normalize_entries(payload: dict[str, Any]) -> list[dict[str, str]]:
    raw = payload.get("papers")
    if not isinstance(raw, list):
        return []
    entries: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        paper_id = str(item.get("id") or "").strip()
        title = str(item.get("title") or "").strip()
        if not paper_id and not title:
            continue
        entry = {"id": paper_id, "title": title}
        reason = str(item.get("reason") or "").strip()
        entry["reason"] = reason or "用户在前端手动删除"
        entries.append(entry)
    return entries


def merge_entries(exclude_path: str | os.PathLike[str], entries: list[dict[str, str]]) -> tuple[int, int]:
    """把新条目并进排除清单，返回 (新增条数, 已有条数)。"""
    path = Path(exclude_path)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    else:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("comment", "永久排除清单：这些论文即使再次被抓到，也不会进入归档。")
    papers = data.get("papers")
    if not isinstance(papers, list):
        papers = []

    existing_ids = {str(item.get("id") or "").strip().lower() for item in papers if isinstance(item, dict)}
    added = 0
    skipped = 0
    for entry in entries:
        key = entry["id"].lower()
        if key and key in existing_ids:
            skipped += 1
            continue
        papers.append(entry)
        if key:
            existing_ids.add(key)
        added += 1

    data["papers"] = papers
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return added, skipped


def fetch_issue(repo: str, number: str, token: str) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{repo}/issues/{urllib.parse.quote(str(number))}"
    return github_request(url, token)


def comment_and_close(repo: str, number: str, token: str, message: str) -> None:
    base = f"https://api.github.com/repos/{repo}/issues/{number}"
    try:
        github_request(f"{base}/comments", token, method="POST", payload={"body": message})
        github_request(base, token, method="PATCH", payload={"state": "closed", "state_reason": "completed"})
    except Exception as exc:  # noqa: BLE001 - 关不掉 issue 不该让整条流水线失败
        print(f"Warning: 没能自动关闭 issue：{exc}", file=sys.stderr)


def main() -> int:
    repo = os.getenv("GITHUB_REPOSITORY", "")
    number = os.getenv("ISSUE_NUMBER", "").strip()
    token = os.getenv("GITHUB_TOKEN", "")
    exclude_path = os.getenv("EXCLUDE_PATH", DEFAULT_EXCLUDE_PATH)

    if not repo or not number:
        print("跳过：没有 ISSUE_NUMBER（不是 issue 触发的运行）")
        return 0

    body = os.getenv("ISSUE_BODY", "")
    if body.strip():
        # 本地/测试场景：直接用环境变量里的正文，省一次 API 请求
        issue = {"body": body, "title": os.getenv("ISSUE_TITLE", ISSUE_TITLE)}
    else:
        if not token:
            print("跳过：缺少 GITHUB_TOKEN，无法读取 issue")
            return 0
        issue = fetch_issue(repo, number, token)

    entries = normalize_entries(extract_payload(str(issue.get("body") or "")))
    if not entries:
        print("issue 正文里没有可解析的论文条目，什么都不做")
        return 0

    added, skipped = merge_entries(exclude_path, entries)
    print(f"合并完成：新增 {added} 篇，已存在 {skipped} 篇（共提交 {len(entries)} 条）")

    if token:
        comment_and_close(
            repo,
            number,
            token,
            f"已把 {added} 篇论文写进 `config/exclude.json`，"
            f"本次运行会顺带把数据里这几篇删掉。重复 {skipped} 篇。",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
