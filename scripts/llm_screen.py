"""LLM-based relevance screening for the daily paper radar.

Mechanical keyword scoring is deterministic and free, but it is also blind to
paraphrase: a paper about "tail dependence" never matches the keyword
"tail risk", and a paper that mentions a keyword once in its related-work
paragraph scores the same as one that is actually about it.

This module sends candidates to a chat model in batches and asks for a strict
0-100 relevance score against the configured research topics. Design choices:

* **Batched** - ``SCREEN_BATCH_SIZE`` papers per request keeps the token bill
  flat (~¥0.2/day at the default settings) instead of one request per paper.
* **Cached** - verdicts are persisted to ``.paper-cache/screen_cache.json`` and
  keyed by paper id, so the 150-day journal window is not re-scored every
  morning. Only genuinely new papers cost anything.
* **Fail-safe** - if the key is missing, the request fails, or the model returns
  malformed JSON, the affected papers keep their mechanical verdict. Screening
  can therefore never produce an empty page.
"""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from collect_papers import (  # noqa: E402
    call_openai_compatible,
    has_meaningful_summary,
    llm_enabled,
    normalize_space,
    parse_datetime,
)

DEFAULT_CACHE_PATH = Path(".paper-cache/screen_cache.json")
ABSTRACT_LIMIT = 1400
CACHE_KEEP_ENTRIES = 6000


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(float(value))
    except ValueError:
        return default


def screen_mode() -> str:
    """``llm`` (default) or ``rules`` to disable model screening entirely."""
    return (os.getenv("SCREEN_MODE", "llm") or "llm").strip().lower()


def screening_active() -> bool:
    if screen_mode() != "llm":
        return False
    if os.getenv("SCREEN_MOCK", "").strip() in {"1", "true", "yes"}:
        return True
    return llm_enabled()


def _cache_path() -> Path:
    return Path(os.getenv("SCREEN_CACHE_PATH", str(DEFAULT_CACHE_PATH)))


def _load_cache() -> dict[str, Any]:
    path = _cache_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a corrupt cache must never break a run
        return {}
    entries = data.get("entries")
    return entries if isinstance(entries, dict) else {}


def _save_cache(entries: dict[str, Any]) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if len(entries) > CACHE_KEEP_ENTRIES:
            ordered = sorted(
                entries.items(),
                key=lambda item: str(item[1].get("at") or ""),
                reverse=True,
            )[:CACHE_KEEP_ENTRIES]
            entries = dict(ordered)
        path.write_text(
            json.dumps({"version": 1, "entries": entries}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: cannot write screen cache: {exc}", file=sys.stderr, flush=True)


def _cache_key(paper: dict[str, Any]) -> str:
    return str(paper.get("id") or paper.get("paper_url") or paper.get("title") or "").strip()


def _cache_fresh(entry: dict[str, Any], max_age_days: int) -> bool:
    stamp = parse_datetime(str(entry.get("at") or ""))
    if not stamp:
        return False
    return (dt.datetime.now(dt.timezone.utc) - stamp).days <= max_age_days


def _topic_block(topics: list[Any]) -> str:
    lines = []
    for topic in topics:
        keywords = ", ".join(topic.keywords[:10])
        lines.append(f"- id: {topic.id} | 名称：{topic.name}\n  描述：{topic.description}\n  关键词：{keywords}")
    return "\n".join(lines)


SCREEN_SYSTEM_PROMPT = (
    "你是金融计量与经济学的文献筛选助手。你只输出合法 JSON，不输出 Markdown，不解释。"
)


def _build_prompt(batch: list[dict[str, Any]], topics: list[Any]) -> str:
    papers_block = []
    title_only_ids = []
    for paper in batch:
        title = normalize_space(str(paper.get("title") or ""))
        has_abstract = has_meaningful_summary(paper)
        abstract = normalize_space(str(paper.get("summary") or ""))[:ABSTRACT_LIMIT]
        if not has_abstract:
            abstract = "（该刊未提供摘要，只有标题）"
            title_only_ids.append(_cache_key(paper))
        venue = paper.get("venue") or ", ".join(paper.get("categories", [])[:3])
        papers_block.append(
            f'<paper id="{_cache_key(paper)}">\n'
            f"标题：{title}\n"
            f"来源：{venue}\n"
            f"摘要：{abstract}\n"
            f"</paper>"
        )
    title_only_note = ""
    if title_only_ids:
        title_only_note = (
            "\n注意：本次有论文没有摘要。对这类论文只根据标题判断它是否研究我关心的主题，"
            "不确定就给 30-45 分，并在 reason 开头写「仅凭标题」。不要臆测它的方法或结论。\n"
        )
    return f"""下面是我的研究方向：
{_topic_block(topics)}

下面是待判断的论文。请逐篇判断它和我的研究方向的相关程度。

打分标准（必须严格，不要因为摘要里出现某个关键词就给高分，要看论文真正在研究什么）：
- 85-100：核心主题正是某个方向的研究对象，方法或实证设计也贴合同一领域
- 60-84：明确属于某个方向的论文，但主题偏边缘或只是其中一个分支
- 40-59：方法或背景沾边，但不是该方向关心的问题
- 20-39：只在背景、文献综述或应用场景里顺带提到
- 0-19：无关（其他学科、纯数学/纯理论、没有可迁移的实证或计量内容）

同时给出最匹配的方向 id（必须来自上面列表），以及不超过 40 字的中文理由，说明具体匹配在哪。
{title_only_note}
{chr(10).join(papers_block)}

严格输出如下 json（results 数组长度必须等于论文数量，id 要原样返回）：
{{"results":[{{"id":"论文id","score":0,"topic_id":"方向id","reason":"中文理由"}}]}}"""


def _clamp_score(value: Any) -> float | None:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(100.0, score))


def _level_from_score(score: float) -> str:
    if score >= 80:
        return "high"
    if score >= 55:
        return "medium"
    return "low"


def _mock_screen(batch: list[dict[str, Any]], topics: list[Any]) -> dict[str, dict[str, Any]]:
    """Offline stand-in used by ``SCREEN_MOCK=1`` to exercise the plumbing."""
    results = {}
    for paper in batch:
        title = str(paper.get("title") or "")
        score = 72.0 if len(title) % 3 else 41.0
        results[_cache_key(paper)] = {
            "score": score,
            "topic_id": topics[0].id if topics else "",
            "reason": "模拟判定（SCREEN_MOCK）",
        }
    return results


def _screen_batch(
    batch: list[dict[str, Any]],
    topics: list[Any],
    retries: int = 2,
) -> dict[str, dict[str, Any]]:
    if os.getenv("SCREEN_MOCK", "").strip() in {"1", "true", "yes"}:
        return _mock_screen(batch, topics)

    prompt = _build_prompt(batch, topics)
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            data = call_openai_compatible(prompt)
            raw_results = data.get("results") if isinstance(data, dict) else None
            if not isinstance(raw_results, list):
                raise ValueError("model response has no results array")
            parsed: dict[str, dict[str, Any]] = {}
            for item in raw_results:
                if not isinstance(item, dict):
                    continue
                paper_id = str(item.get("id") or "").strip()
                score = _clamp_score(item.get("score"))
                if not paper_id or score is None:
                    continue
                parsed[paper_id] = {
                    "score": score,
                    "topic_id": str(item.get("topic_id") or ""),
                    "reason": normalize_space(str(item.get("reason") or ""))[:120],
                }
            if not parsed:
                raise ValueError("model returned no usable verdicts")
            return parsed
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
    print(f"Warning: LLM screening batch failed: {last_error}", file=sys.stderr, flush=True)
    return {}


def screen_papers(
    papers: list[dict[str, Any]],
    topics: list[Any],
    label: str = "",
) -> dict[str, dict[str, Any]]:
    """Return ``{paper_id: verdict}`` for every paper that could be screened.

    Verdict shape: ``{"score": 0-100, "topic_id": str, "reason": str, "source": str}``.
    Papers missing from the result keep their mechanical verdict.
    """
    if not papers or not topics or not screening_active():
        return {}

    cache = _load_cache()
    max_age = _env_int("SCREEN_CACHE_DAYS", 60)
    batch_size = max(1, _env_int("SCREEN_BATCH_SIZE", 6))
    max_papers = _env_int("SCREEN_MAX_PAPERS", 400)
    concurrency = max(1, _env_int("SCREEN_CONCURRENCY", 3))

    verdicts: dict[str, dict[str, Any]] = {}
    pending: list[dict[str, Any]] = []
    # 顶刊里 Elsevier 系常常不给摘要，但"这题是不是我关心的"看标题往往能判断，
    # 所以默认允许只用标题判相关性（只判相关性，不生成创新点总结）。
    allow_title_only = (os.getenv("SCREEN_ALLOW_TITLE_ONLY", "true") or "true").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    for paper in papers:
        paper_id = _cache_key(paper)
        if not paper_id:
            continue
        cached = cache.get(paper_id)
        if isinstance(cached, dict) and _cache_fresh(cached, max_age):
            verdicts[paper_id] = {
                "score": float(cached.get("score") or 0),
                "topic_id": str(cached.get("topic_id") or ""),
                "reason": str(cached.get("reason") or ""),
                "source": "cache",
            }
            continue
        if not has_meaningful_summary(paper) and not allow_title_only:
            continue
        pending.append(paper)

    fresh_count = len(pending)
    if len(pending) > max_papers:
        # 2018 起的宽窗口下，候选横跨 8 年，按时间截断会把所有老论文都切掉，
        # 所以宽窗口模式改成按机械分截断（时间只作为同分时的次序）。
        truncate_by = (os.getenv("SCREEN_TRUNCATE_BY", "recency") or "recency").strip().lower()

        def _recency(paper: dict[str, Any]) -> dt.datetime:
            stamp = parse_datetime(str(paper.get("published") or ""))
            return stamp or dt.datetime.min.replace(tzinfo=dt.timezone.utc)

        def _mechanical(paper: dict[str, Any]) -> float:
            try:
                return float((paper.get("best_match") or {}).get("score") or 0.0)
            except (TypeError, ValueError):
                return 0.0

        if truncate_by == "score":
            pending.sort(key=lambda paper: (_mechanical(paper), _recency(paper)), reverse=True)
        else:
            pending.sort(key=_recency, reverse=True)
        pending = pending[:max_papers]
        print(
            f"Warning: screening capped at {max_papers} papers (of {fresh_count}, by {truncate_by}); "
            f"raise SCREEN_MAX_PAPERS to cover more",
            file=sys.stderr,
            flush=True,
        )

    if not pending:
        print(f"Screening{(' ' + label) if label else ''}: all {len(verdicts)} verdicts reused from cache", flush=True)
        return verdicts

    persist_cache = os.getenv("SCREEN_MOCK", "").strip() not in {"1", "true", "yes"}

    batches = [pending[i : i + batch_size] for i in range(0, len(pending), batch_size)]
    print(
        f"Screening{(' ' + label) if label else ''}: {len(pending)} papers in {len(batches)} batches "
        f"(reused {len(verdicts)} from cache)",
        flush=True,
    )

    started = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(_screen_batch, batch, topics): batch for batch in batches}
        for future in concurrent.futures.as_completed(futures):
            batch = futures[future]
            try:
                parsed = future.result()
            except Exception as exc:  # noqa: BLE001
                print(f"Warning: screening worker crashed: {exc}", file=sys.stderr, flush=True)
                continue
            for paper in batch:
                paper_id = _cache_key(paper)
                verdict = parsed.get(paper_id)
                if not verdict:
                    continue
                verdicts[paper_id] = {**verdict, "source": "llm"}
                if persist_cache:
                    cache[paper_id] = {
                        **verdict,
                        "model": os.getenv("LLM_MODEL", "deepseek-chat"),
                        "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                    }

    if persist_cache:
        _save_cache(cache)
    scored = sum(1 for v in verdicts.values() if v.get("source") == "llm")
    print(
        f"Screening done: {scored} new verdicts, {len(verdicts)} total, "
        f"{time.time() - started:.1f}s",
        flush=True,
    )
    return verdicts


# v3：卡片不再展示「证据 / 局限 / 匹配」，改为「问题 / 方法 / 创新 / 主要结论」，
# 字段变了就必须重写，否则旧条目里没有 conclusion，页面会空一块。
SUMMARY_CACHE_PATH = Path(".paper-cache/summary_cache.v3.json")
SUMMARY_FIELDS = ("problem", "method", "innovation", "conclusion", "abstract_zh", "why_relevant")


def _summary_cache_path() -> Path:
    return Path(os.getenv("SUMMARY_CACHE_PATH", str(SUMMARY_CACHE_PATH)))


def load_summary_cache() -> dict[str, Any]:
    path = _summary_cache_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    entries = data.get("entries")
    return entries if isinstance(entries, dict) else {}


def save_summary_cache(entries: dict[str, Any]) -> None:
    path = _summary_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if len(entries) > CACHE_KEEP_ENTRIES:
            ordered = sorted(
                entries.items(), key=lambda item: str(item[1].get("at") or ""), reverse=True
            )[:CACHE_KEEP_ENTRIES]
            entries = dict(ordered)
        path.write_text(
            json.dumps({"version": 1, "entries": entries}, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: cannot write summary cache: {exc}", file=sys.stderr, flush=True)


def is_real_summary(summary: Any) -> bool:
    """True only for a model-written summary, not a placeholder."""
    if not isinstance(summary, dict):
        return False
    problem = str(summary.get("problem") or "")
    if len(problem) < 15:
        return False
    conclusion = str(summary.get("conclusion") or "")
    if len(conclusion) < 10:
        return False
    placeholders = ("未配置模型", "尚未配置", "没有提供足够摘要", "出版商未向开放索引", "DBLP")
    return not any(marker in problem for marker in placeholders)


def store_summary(entries: dict[str, Any], paper_id: str, summary: Any) -> None:
    if not paper_id or not is_real_summary(summary):
        return
    entries[paper_id] = {
        **{key: str(summary.get(key) or "") for key in SUMMARY_FIELDS},
        "model": os.getenv("LLM_MODEL", "deepseek-chat"),
        "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }


def apply_verdict(paper: dict[str, Any], verdict: dict[str, Any], topics: list[Any]) -> None:
    """Overwrite the mechanical match with the model verdict, keeping a trace of both."""
    match = dict(paper.get("best_match") or {})
    match["mechanical_score"] = match.get("score")
    match["score"] = round(float(verdict["score"]) / 100.0, 3)
    match["level"] = _level_from_score(float(verdict["score"]))
    match["llm_score"] = round(float(verdict["score"]), 1)
    match["llm_source"] = verdict.get("source", "llm")
    if verdict.get("topic_id"):
        matched = next((t for t in topics if t.id == verdict["topic_id"]), None)
        if matched is not None:
            match["topic_id"] = matched.id
            match["topic_name"] = matched.name
    if verdict.get("reason"):
        manual = "；关键词命中：" + "、".join(match.get("keyword_hits") or []) if match.get("keyword_hits") else ""
        match["reason"] = f"AI 判定：{verdict['reason']}{manual}"
    paper["best_match"] = match
