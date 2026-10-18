"""每周滚动更新的选稿规则（arXiv 与顶刊两条流水线共用）。

用户设定的规则：

1. 时间窗口：2018-01-01 起，到当前为止；
2. 更新频率：每周五上午 9 点跑一次（cron 在 workflow 里配置）；
3. 每期最多新增 10 篇，页面常驻 20 个位置；
4. 新抓到的不能和"当前正在展示的"重复；
5. 20 个位置里优先留高分论文，被挤掉的从页面删除；被删掉的论文进入冷却期，
   冷却结束后如果再次被抓到，可以重新进入列表。

第 4 条和第 5 条放在一起会有一个坑：如果只排除"当前展示的 20 篇"，
那么上周被挤掉的那 10 篇下一期又会原样回到候选池，列表会在两组论文之间来回跳。
所以这里引入 `seen` 冷却表：记住每篇论文最后一次被展示的时间，冷却期内不再入选。
冷却期（默认 56 天）之后才允许重新进来，等价于"后面有抓取到可以再放进来"。
"""

from __future__ import annotations

import datetime as dt
import os
from typing import Any

DEFAULT_WINDOW_START = "2018-01-01"
DEFAULT_WEEKLY_NEW = 10
DEFAULT_DISPLAY_CAP = 20
DEFAULT_COOLDOWN_DAYS = 56


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


def parse_dt(value: Any) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def window_start() -> dt.datetime:
    """论文时间窗口的起点，默认 2018-01-01（UTC 零点）。"""
    raw = (os.getenv("PAPER_WINDOW_START") or DEFAULT_WINDOW_START).strip() or DEFAULT_WINDOW_START
    try:
        day = dt.date.fromisoformat(raw[:10])
    except ValueError:
        day = dt.date.fromisoformat(DEFAULT_WINDOW_START)
    return dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc)


def published_at(paper: dict[str, Any]) -> dt.datetime | None:
    for field in ("published", "updated"):
        stamp = parse_dt(paper.get(field))
        if stamp:
            return stamp
    return None


def in_window(paper: dict[str, Any]) -> bool:
    """论文是否落在 2018-01-01 → 今天 的时间窗口内。没有日期时不拦。"""
    stamp = published_at(paper)
    if stamp is None:
        return True
    return stamp >= window_start()


def paper_key(paper: dict[str, Any]) -> str:
    for field in ("id", "doi", "paper_url"):
        value = str(paper.get(field) or "").strip()
        if value:
            return value.lower()
    return ""


def score_of(paper: dict[str, Any]) -> float:
    try:
        return float((paper.get("best_match") or {}).get("score") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def sort_key(paper: dict[str, Any]) -> tuple[float, float]:
    stamp = published_at(paper)
    return (score_of(paper), stamp.timestamp() if stamp else 0.0)


def load_seen(payload: dict[str, Any]) -> dict[str, str]:
    """从上一期数据里取出冷却表 `{论文 key: 最后展示时间}`。

    旧版数据没有 `seen` 字段（此前是"保留 + 合并"的逻辑），这时用
    `last_seen_at` 补一份初始冷却表，否则迁移当期会把刚淘汰的论文
    当成全新论文又选回来。
    """
    if not isinstance(payload, dict):
        return {}
    seen = payload.get("seen")
    if isinstance(seen, dict) and seen:
        return {str(key): str(value) for key, value in seen.items() if key and value}

    seeded: dict[str, str] = {}
    for paper in payload.get("papers") or []:
        if not isinstance(paper, dict):
            continue
        key = paper_key(paper)
        stamp = str(paper.get("last_seen_at") or paper.get("first_seen_at") or "")
        if key and stamp:
            seeded[key] = stamp
    return seeded


def weekly_new() -> int:
    return max(0, env_int("WEEKLY_NEW_PAPERS", DEFAULT_WEEKLY_NEW))


def display_cap() -> int:
    return max(0, env_int("DISPLAY_CAP", DEFAULT_DISPLAY_CAP))


def cooldown_days() -> int:
    return max(0, env_int("REENTRY_COOLDOWN_DAYS", DEFAULT_COOLDOWN_DAYS))


def select_weekly(
    previous: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    seen: dict[str, str],
    now: dt.datetime,
    *,
    weekly_new_count: int | None = None,
    cap: int | None = None,
    cooldown: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, int]]:
    """按「本期新增 N 篇 + 保留高分旧论文」的方式选出本期展示列表。

    返回 (本期论文列表, 更新后的冷却表, 统计信息)。
    """
    new_quota = weekly_new() if weekly_new_count is None else max(0, weekly_new_count)
    cap_count = display_cap() if cap is None else max(0, cap)
    cooldown_count = cooldown_days() if cooldown is None else max(0, cooldown)
    cooldown_span = dt.timedelta(days=cooldown_count)

    previous_list = [p for p in previous if isinstance(p, dict) and paper_key(p)]
    previous_keys = {paper_key(p) for p in previous_list}

    # 冷却期内的论文不再入选，避免"删掉又原样回来"的来回跳
    cooling: set[str] = set()
    for key, stamp in seen.items():
        when = parse_dt(stamp)
        if when is not None and now - when < cooldown_span:
            cooling.add(key)

    fresh: list[dict[str, Any]] = []
    cooling_blocked = 0
    fresh_keys: set[str] = set()
    for paper in candidates:
        if not isinstance(paper, dict):
            continue
        key = paper_key(paper)
        if not key or key in previous_keys:
            continue
        if key in cooling:
            cooling_blocked += 1
            continue
        if key in fresh_keys:
            continue
        fresh_keys.add(key)
        fresh.append(paper)

    fresh.sort(key=sort_key, reverse=True)
    new_batch = fresh[:new_quota]

    keep_slots = max(0, cap_count - len(new_batch))
    keepers = sorted(previous_list, key=sort_key, reverse=True)[:keep_slots]

    new_keys = {paper_key(p) for p in new_batch}
    selected: list[dict[str, Any]] = []
    for paper in [*new_batch, *keepers]:
        item = dict(paper)
        item.setdefault("first_seen_at", now.isoformat())
        item["last_seen_at"] = now.isoformat()
        item["retained_from_previous_run"] = paper_key(paper) not in new_keys
        item["is_new_this_week"] = paper_key(paper) in new_keys
        selected.append(item)
    selected.sort(key=sort_key, reverse=True)

    # 冷却表：先按保留期裁掉过期条目，再把本期展示过的论文刷新为"现在"
    horizon = cooldown_span * 3 if cooldown_span else dt.timedelta(days=180)
    updated_seen: dict[str, str] = {}
    for key, stamp in seen.items():
        when = parse_dt(stamp)
        if when is None or now - when <= horizon:
            updated_seen[key] = stamp
    for paper in selected:
        updated_seen[paper_key(paper)] = now.isoformat()

    stats = {
        "weekly_new_count": len(new_batch),
        "kept_previous_count": len(keepers),
        "display_count": len(selected),
        "dropped_from_display_count": max(0, len(previous_list) - len(keepers)),
        "cooldown_blocked_count": cooling_blocked,
        "fresh_candidate_count": len(fresh),
        "display_cap": cap_count,
        "weekly_new_quota": new_quota,
        "reentry_cooldown_days": cooldown_count,
    }
    return selected, updated_seen, stats
