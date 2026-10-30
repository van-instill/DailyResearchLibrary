"""每周滚动更新的选稿规则（arXiv 与顶刊两条流水线共用）。

用户设定的规则（2026-10 修订）：

1. 时间窗口：2018-01-01 起，到当前为止；
2. 更新频率：每周五上午 9 点跑一次（cron 在 workflow 里配置）；
3. **每期合计新增 10 篇**（两条流水线各 5 篇：arXiv 5 + 顶刊 5）；
4. 新增的论文绝不与"已经收录过的"重复；
5. **已经收录的论文不删除**，全部留在归档里，页面上用「本期新增」把它们区分开；
6. 因此不再需要"冷却期"概念——论文不会离开列表，也就不会出现两组论文周周来回跳。

`seen` 仍然保留，但语义从"冷却表"变成"去重账本"：记录每一篇曾经入选过的论文 key。
即使将来归档因为体积上限被裁掉一部分，账本也能保证那些论文不会又被当成新论文抓回来。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Any

DEFAULT_WINDOW_START = "2018-01-01"
DEFAULT_WEEKLY_NEW_TOTAL = 10
DEFAULT_COLLECTION_COUNT = 2
DEFAULT_EXCLUDE_PATH = "config/exclude.json"


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


def normalize_doi(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"^https?://(dx\.)?doi\.org/", "", text)
    return text.strip()


def title_key(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").lower())


def paper_keys(paper: dict[str, Any]) -> list[str]:
    """一篇论文的全部可比对身份，用于"绝不重复"的判断。

    只看 `id` 不够：同一篇论文从 OpenAlex 抓到时是 ``openalex:W123``，
    走 Crossref 兜底时是 ``doi:10.xxx``，两条路都命中的话会被当成两篇。
    所以 DOI、id、标题指纹三者都用，命中任意一个就算同一篇。
    """
    keys: list[str] = []
    doi = normalize_doi(paper.get("doi"))
    if doi:
        keys.append("doi:" + doi)
    pid = str(paper.get("id") or "").strip().lower()
    if pid:
        keys.append("id:" + pid)
    tid = title_key(paper.get("title"))
    if len(tid) >= 12:
        keys.append("t:" + tid)
    return keys


def paper_key(paper: dict[str, Any]) -> str:
    keys = paper_keys(paper)
    return keys[0] if keys else ""


def load_excluded_keys(path: str | None = None) -> set[str]:
    """读取永久排除清单，返回一组可比对的 key（id / arxiv id / 标题指纹）。

    配置文件默认 ``config/exclude.json``，格式见该文件内的 comment。
    返回的 key 会与 ``paper_keys()`` 生成的 key 做交集判断，所以
    ``id:``、``doi:``、``t:`` 三种前缀都会生成。
    """
    p = Path(path or os.getenv("EXCLUDE_PATH", DEFAULT_EXCLUDE_PATH))
    keys: set[str] = set()
    if not p.exists():
        return keys
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return keys
    for item in data.get("papers") or []:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("id") or "").strip().lower()
        if pid:
            keys.add("id:" + pid)
            # arXiv 编号常带 v1/v2 版本后缀，去掉版本号再存一份
            keys.add("id:" + re.sub(r"v\d+$", "", pid))
        doi = normalize_doi(item.get("doi"))
        if doi:
            keys.add("doi:" + doi)
        title = title_key(item.get("title"))
        if len(title) >= 12:
            keys.add("t:" + title)
    return keys


def is_excluded(paper: dict[str, Any], excluded_keys: set[str]) -> bool:
    if not excluded_keys:
        return False
    return any(key in excluded_keys for key in paper_keys(paper))


def score_of(paper: dict[str, Any]) -> float:
    try:
        return float((paper.get("best_match") or {}).get("score") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def sort_key(paper: dict[str, Any]) -> tuple[float, float]:
    stamp = published_at(paper)
    return (score_of(paper), stamp.timestamp() if stamp else 0.0)


def load_seen(payload: dict[str, Any]) -> dict[str, str]:
    """取出"曾经入选过"的账本 `{论文 key: 最后入选时间}`。

    兼容三种历史格式：
    * v2 起：顶层 `seen` 字段（本次及以后继续沿用）；
    * v1：没有 `seen`，用当期 `papers` 里的 `last_seen_at` 补种；
    * 迁移期：早期 `seen` 的 key 是裸 id / DOI，不是带前缀的规范 key。
    """
    if not isinstance(payload, dict):
        return {}
    seen = payload.get("seen")
    ledger: dict[str, str] = {}
    if isinstance(seen, dict):
        for key, value in seen.items():
            text = str(key or "").strip()
            stamp = str(value or "").strip()
            if text and stamp:
                ledger[text.lower()] = stamp
    for paper in payload.get("papers") or []:
        if not isinstance(paper, dict):
            continue
        stamp = str(paper.get("last_seen_at") or paper.get("first_seen_at") or "").strip()
        if not stamp:
            continue
        for key in paper_keys(paper):
            ledger.setdefault(key, stamp)
        # 兼容旧版裸 key（无前缀）
        raw = str(paper.get("id") or paper.get("paper_url") or "").strip().lower()
        if raw:
            ledger.setdefault(raw, stamp)
        raw_doi = normalize_doi(paper.get("doi"))
        if raw_doi:
            ledger.setdefault(raw_doi, stamp)
    return ledger


def keyset_of(papers: list[dict[str, Any]], ledger: dict[str, str] | None = None) -> set[str]:
    """归档 + 账本合并成一张"这些都已经收录过"的集合。"""
    blocked: set[str] = set(ledger or {})
    for paper in papers:
        if not isinstance(paper, dict):
            continue
        blocked.update(paper_keys(paper))
        raw = str(paper.get("id") or paper.get("paper_url") or "").strip().lower()
        if raw:
            blocked.add(raw)
        raw_doi = normalize_doi(paper.get("doi"))
        if raw_doi:
            blocked.add(raw_doi)
    return blocked


def weekly_new_total() -> int:
    """每期新增总量，跨两条流水线合计。"""
    return max(0, env_int("WEEKLY_NEW_TOTAL", DEFAULT_WEEKLY_NEW_TOTAL))


def period_key(when: dt.datetime) -> str:
    """把时间点折算成 ISO 周（如 ``2026-W42``），作为「本期」的标识。

    必须按周算而不是按「上次运行」算：工作流是 push 触发的，改一次代码、
    改一次文案都会重跑一遍。如果每跑一次就选一批新论文，一天推三次就会
    多出三十篇，和「每周 10 篇」完全对不上。
    """
    iso = when.astimezone(dt.timezone.utc).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def is_current_period(paper: dict[str, Any], period: str) -> bool:
    """这篇论文是不是「本期入选的」。

    看的是论文自己记的 `weekly_period`（它被选中的那一期），不是 first_seen_at。
    用时间戳判断会把整个归档都标成新增——归档里早期论文的 first_seen_at
    是迁移时统一回填的，几乎都落在同一周。
    """
    return str(paper.get("weekly_period") or "").strip() == period


def stored_period(payload: dict[str, Any]) -> str:
    """上一份数据里记录的期号。

    写在顶层 `weekly_period`；早期版本只塞进了 `stats`，这里一并兼容，
    否则升级后的第一次运行会认不出「这周已经选过」而多推一批。
    """
    if not isinstance(payload, dict):
        return ""
    direct = str(payload.get("weekly_period") or "").strip()
    if direct:
        return direct
    stats = payload.get("stats")
    if isinstance(stats, dict):
        return str(stats.get("weekly_period") or "").strip()
    return ""


def collection_count() -> int:
    return max(1, env_int("RADAR_COLLECTION_COUNT", DEFAULT_COLLECTION_COUNT))


def weekly_new() -> int:
    """单条流水线本期的新增配额。

    默认按总量对半分（10 篇 → arXiv 5 + 顶刊 5）；显式设置
    `WEEKLY_NEW_PAPERS` 时以它为准，方便临时只调一条线。
    """
    raw = (os.getenv("WEEKLY_NEW_PAPERS") or "").strip()
    if raw:
        try:
            return max(0, int(float(raw)))
        except ValueError:
            pass
    total = weekly_new_total()
    if total <= 0:
        return 0
    return max(1, round(total / collection_count()))


def select_weekly(
    previous: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    seen: dict[str, str],
    now: dt.datetime,
    *,
    weekly_new_count: int | None = None,
    cap: int | None = None,
    last_period: str | None = None,
    last_period_had_new: bool = True,
    excluded_keys: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, Any]]:
    """选出本期列表：`本期新增 N 篇` + `全部历史归档`。

    与旧版（滚动窗口 20 个位置）的关键差别：**不再有"挤掉"**。
    归档只增不减，所以 new_batch 只是"给本期贴个新标签"，
    keepers 全部原样保留，`is_new_this_week` 用来在前端做区分。

    `last_period` 是上一份数据里记录的期号。等于本期时说明这周已经跑过一遍，
    通常要跳过选稿（workflow 被 push 触发重跑、或手动点了一次），只把数据重新
    生成一遍。但有一个例外：上一期若因为采集失败而"空跑"（`last_period_had_new`
    为 False，本期一篇都没选上），此时应该允许补选，否则这一周就永远缺稿了。
    想强制加一批就显式传 `weekly_new_count`。

    返回 (本期论文列表, 更新后的账本, 统计信息)。
    """
    period = period_key(now)
    excluded_keys = excluded_keys or set()
    new_quota = weekly_new() if weekly_new_count is None else max(0, weekly_new_count)
    if weekly_new_count is None and last_period and last_period == period and last_period_had_new:
        # 本周已经选过稿了：候选里没见过的论文留到下周，否则改一次代码就多推一批
        new_quota = 0
    archive_cap = 0 if cap is None else max(0, cap)

    previous_list: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for paper in previous:
        if not isinstance(paper, dict):
            continue
        if is_excluded(paper, excluded_keys):
            # 被永久排除的论文：即使已经在归档里，也不再保留
            continue
        keys = paper_keys(paper)
        if not keys:
            continue
        # 历史数据里也可能有重复（早期两条抓取路径都命中同一篇），顺手去重
        if any(key in seen_keys for key in keys):
            continue
        seen_keys.update(keys)
        previous_list.append(paper)

    blocked = keyset_of(previous_list, seen)

    fresh: list[dict[str, Any]] = []
    fresh_keys: set[str] = set()
    duplicates = 0
    for paper in candidates:
        if not isinstance(paper, dict):
            continue
        if is_excluded(paper, excluded_keys):
            # 被永久排除的论文：候选阶段就拦掉，不会再被选回来
            continue
        keys = paper_keys(paper)
        if not keys:
            continue
        if any(key in blocked for key in keys):
            duplicates += 1
            continue
        if any(key in fresh_keys for key in keys):
            duplicates += 1
            continue
        if not in_window(paper):
            continue
        fresh_keys.update(keys)
        fresh.append(paper)

    fresh.sort(key=sort_key, reverse=True)
    new_batch = fresh[:new_quota]

    new_keys = {key for paper in new_batch for key in paper_keys(paper)}
    selected: list[dict[str, Any]] = []
    for paper in new_batch:
        item = dict(paper)
        item.setdefault("first_seen_at", now.isoformat())
        item["last_seen_at"] = now.isoformat()
        item["retained_from_previous_run"] = False
        # 记下它属于哪一期，后面「本期新增」标签就靠这个字段，
        # 不依赖本次运行是否又选了它（同周重跑时 new_batch 是空的）
        item["weekly_period"] = period
        item["is_new_this_week"] = True
        selected.append(item)

    kept = 0
    for paper in previous_list:
        item = dict(paper)
        item.setdefault("first_seen_at", item.get("last_seen_at") or now.isoformat())
        item["retained_from_previous_run"] = True
        item["is_new_this_week"] = is_current_period(item, period) or any(
            key in new_keys for key in paper_keys(paper)
        )
        selected.append(item)
        kept += 1

    selected.sort(key=sort_key, reverse=True)

    # 账本：只记录"入选过"的 key，永不删除，用来兜住归档被裁掉的情况
    updated_seen = dict(seen)
    for paper in new_batch:
        stamp = now.isoformat()
        for key in paper_keys(paper):
            updated_seen[key] = stamp

    archived = len(selected)
    trimmed = 0
    if archive_cap and archived > archive_cap:
        # 只有显式设置硬上限时才裁历史，正常配置下不会触发
        ordered = sorted(selected, key=sort_key, reverse=True)
        trimmed = archived - archive_cap
        selected = ordered[:archive_cap]

    stats = {
        "weekly_period": period,
        # period_new_count 是「本期一共几篇」，同周重跑不变；
        # weekly_new_count 是「本次运行选了几篇」，同周重跑会是 0。前端两个都要能对上。
        "period_new_count": sum(1 for item in selected if item.get("is_new_this_week")),
        "weekly_new_count": len(new_batch),
        "weekly_new_quota": new_quota,
        "weekly_new_total": weekly_new_total(),
        "kept_previous_count": kept,
        "display_count": len(selected),
        "archive_count": len(selected),
        "dropped_from_display_count": trimmed,
        "duplicate_candidate_count": duplicates,
        "fresh_candidate_count": len(fresh),
    }
    return selected, updated_seen, stats
