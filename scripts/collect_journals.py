"""Collect recent finance / econometrics journal articles via the OpenAlex API.

This is the "顶刊精选" half of the daily radar. It reuses the LLM helpers from
``collect_papers.py`` (same prompt, same JSON schema) but scores papers with a
journal-specific formula, because journal records have no arXiv categories.

Output schema matches ``conference_papers.json`` consumed by ``web/app.js``;
``source_type`` stays ``conference`` so the front end keeps two collections.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import email.utils
import html as html_lib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from collect_papers import (  # noqa: E402
    Topic,
    dedupe_papers,
    has_meaningful_summary,
    html_to_text,
    llm_enabled,
    load_json,
    normalize_space,
    parse_datetime,
    parse_topics,
    summarize_with_llm,
    trim_papers_for_storage,
    write_json,
)

OPENALEX_SOURCES_URL = "https://api.openalex.org/sources"
OPENALEX_WORKS_URL = "https://api.openalex.org/works"
CROSSREF_WORKS_URL = "https://api.crossref.org/works"
DEFAULT_JOURNAL_CONFIG = Path("config/journals.json")
DEFAULT_JOURNAL_OUTPUT = Path("web/data/conference_papers.json")
WORK_SELECT = (
    "id,doi,title,publication_date,primary_location,authorships,"
    "abstract_inverted_index,topics,open_access,best_oa_location,type"
)
SOURCE_SELECT = "id,display_name,issn_l,works_count"
RETAINED_LEVELS = {"high", "medium"}
HIGH_THRESHOLD = 0.46
MEDIUM_THRESHOLD = 0.28


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not str(value).strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not str(value).strip():
        return default
    try:
        return float(value)
    except ValueError:
        return default


def contact_email() -> str:
    return (
        os.getenv("OPENALEX_EMAIL")
        or os.getenv("CONTACT_EMAIL")
        or "paper-daily@users.noreply.github.com"
    )


def openalex_request(url: str, timeout: float = 60, retries: int | None = None) -> dict[str, Any]:
    headers = {"User-Agent": "paper-daily-journal-collector/1.0"}
    api_key = os.getenv("OPENALEX_API_KEY", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    attempts = env_int("OPENALEX_RETRIES", 1) if retries is None else retries
    last_error: Exception | None = None
    for attempt in range(attempts + 1):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in {429, 500, 502, 503, 504} or attempt == attempts:
                raise
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            last_error = exc
            if attempt == attempts:
                raise
        # 429 多为免费额度/限流，退避时间长一点，宁可慢也不要少抓
        wait = 30 * (attempt + 1) if "429" in str(last_error) else 5 * (attempt + 1)
        print(f"Warning: OpenAlex retry in {wait}s ({last_error})", file=sys.stderr, flush=True)
        time.sleep(wait)
    raise RuntimeError(f"OpenAlex request failed: {last_error}")


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def venue_key(value: str) -> str:
    """Loose key so "The Journal of Finance" and "Journal of Finance" collapse together."""
    key = normalize_name(value)
    return key[4:] if key.startswith("the ") else key


def resolve_source_id(venue: dict[str, Any]) -> str:
    """Resolve the authoritative OpenAlex source id for a venue (ISSN driven)."""
    explicit = str(venue.get("openalex_source_id") or "").strip()
    if explicit:
        return explicit
    issns = [str(item).strip() for item in venue.get("issn") or [] if str(item).strip()]
    if not issns:
        raise ValueError(f"venue {venue.get('name')} has no ISSN")
    url = OPENALEX_SOURCES_URL + "?" + urllib.parse.urlencode(
        {"filter": "issn:" + "|".join(issns), "per-page": "10", "select": SOURCE_SELECT}
    )
    data = openalex_request(url)
    results = [item for item in data.get("results") or [] if isinstance(item, dict)]
    if not results:
        raise ValueError(f"no OpenAlex source for ISSN {', '.join(issns)}")
    expected = normalize_name(str(venue.get("name") or ""))
    for item in results:
        if normalize_name(str(item.get("display_name") or "")) == expected:
            return str(item.get("id") or "")
    results.sort(key=lambda item: int(item.get("works_count") or 0), reverse=True)
    return str(results[0].get("id") or "")


def abstract_from_inverted_index(index: Any) -> str:
    if not isinstance(index, dict) or not index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, spots in index.items():
        if not isinstance(spots, list):
            continue
        for spot in spots:
            if isinstance(spot, int):
                positions.append((spot, word))
    if not positions:
        return ""
    positions.sort(key=lambda item: item[0])
    return normalize_space(" ".join(word for _, word in positions))


def iso_date(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return f"{text}T00:00:00+00:00"
    parsed = parse_datetime(text)
    return parsed.isoformat() if parsed else text


def work_year(value: str | None) -> int:
    match = re.match(r"(\d{4})", str(value or ""))
    return int(match.group(1)) if match else dt.datetime.now(dt.timezone.utc).year


NOISE_TITLE_PATTERNS = (
    "frontmatter",
    "backmatter",
    "issue information",
    "editorial board",
    "table of contents",
    "index to volume",
    "call for papers",
)


def is_noise_title(title: str) -> bool:
    """Drop issue furniture (front/back matter, TOC) that carries no research content."""
    lowered = normalize_space(title).lower()
    return any(pattern in lowered for pattern in NOISE_TITLE_PATTERNS)


def widen_from_date(from_date: str) -> str:
    """Relax the window to Jan 1 of the current year.

    Some publishers (e.g. Wiley for Econometrica) only deposit the year, so their
    records carry a ``YYYY-01-01`` date and are filtered out by a mid-year
    ``from_publication_date``. Falling back to the start of the year recovers them.
    """
    year_start = f"{dt.date.today().year}-01-01"
    return year_start if year_start < from_date else from_date


def paper_from_work(work: dict[str, Any], venue: dict[str, Any]) -> dict[str, Any] | None:
    title = normalize_space(str(work.get("title") or ""))
    if not title or is_noise_title(title):
        return None
    openalex_id = str(work.get("id") or "")
    short_id = openalex_id.rsplit("/", 1)[-1] if openalex_id else ""
    doi = str(work.get("doi") or "")

    authors = []
    for authorship in work.get("authorships") or []:
        name = normalize_space(str(((authorship or {}).get("author") or {}).get("display_name") or ""))
        if name:
            authors.append(name)

    primary = work.get("primary_location") or {}
    source_info = primary.get("source") or {}
    venue_name = str(venue.get("name") or source_info.get("display_name") or "")
    published = iso_date(work.get("publication_date"))

    topics = []
    for item in work.get("topics") or []:
        name = str((item or {}).get("display_name") or "").strip()
        if name and name not in topics:
            topics.append(name)
    tags = [str(venue.get("group") or "期刊")] + topics[:2]

    paper_url = doi or openalex_id
    pdf_url = ""
    for candidate in (
        (work.get("best_oa_location") or {}).get("pdf_url"),
        (work.get("open_access") or {}).get("oa_url"),
        primary.get("pdf_url") if isinstance(primary, dict) else "",
    ):
        if candidate:
            pdf_url = str(candidate)
            break

    return {
        "id": f"openalex:{short_id}" if short_id else paper_url,
        "title": title,
        "authors": authors[:20],
        "summary": abstract_from_inverted_index(work.get("abstract_inverted_index")),
        "published": published,
        "updated": published,
        "categories": tags,
        "paper_url": paper_url,
        "pdf_url": pdf_url or paper_url,
        "source": venue_name or str(venue.get("name") or ""),
        "source_type": "conference",
        "venue": venue_name or str(venue.get("name") or ""),
        "venue_group": str(venue.get("group") or ""),
        "doi": doi,
        "conference": {
            "id": str(venue.get("id") or ""),
            "name": venue_name or str(venue.get("name") or ""),
            "year": work_year(work.get("publication_date")),
        },
    }


def fetch_venue_works(
    venue: dict[str, Any],
    source_id: str,
    from_date: str,
    per_page: int,
    max_pages: int,
) -> list[dict[str, Any]]:
    papers: list[dict[str, Any]] = []
    for page in range(1, max(1, max_pages) + 1):
        params = {
            "filter": f"primary_location.source.id:{source_id},from_publication_date:{from_date},type:article",
            "per-page": str(max(1, min(200, per_page))),
            "page": str(page),
            "select": WORK_SELECT,
        }
        url = OPENALEX_WORKS_URL + "?" + urllib.parse.urlencode(params)
        data = openalex_request(url)
        results = data.get("results") or []
        for work in results:
            paper = paper_from_work(work, venue)
            if paper:
                papers.append(paper)
        if len(results) < per_page:
            break
    return papers


def crossref_request(url: str, timeout: float = 60, retries: int = 2) -> dict[str, Any]:
    headers = {"User-Agent": f"paper-daily-journal-collector/1.0 (mailto:{contact_email()})"}
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt == retries:
                raise
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Crossref request failed: {last_error}")


def paper_from_crossref(item: dict[str, Any], venue: dict[str, Any]) -> dict[str, Any] | None:
    titles = item.get("title") or []
    title = normalize_space(html_lib.unescape(str(titles[0]))) if titles else ""
    doi = str(item.get("DOI") or "")
    if not title or not doi or is_noise_title(title):
        return None

    authors = []
    for author in item.get("author") or []:
        name = normalize_space(
            " ".join(part for part in [author.get("given"), author.get("family")] if part)
        )
        if name:
            authors.append(name)

    published = ""
    for key in ("published-online", "published", "published-print", "issued"):
        parts = ((item.get(key) or {}).get("date-parts") or [[]])[0]
        if parts and parts[0]:
            year = int(parts[0])
            month = int(parts[1]) if len(parts) > 1 and parts[1] else 1
            day = int(parts[2]) if len(parts) > 2 and parts[2] else 1
            published = f"{year:04d}-{month:02d}-{day:02d}T00:00:00+00:00"
            break

    abstract = str(item.get("abstract") or "")
    if abstract:
        abstract = html_to_text(abstract)
        abstract = re.sub(r"^\s*(Abstract|ABSTRACT)\s*[:.]?\s*", "", abstract)

    venue_name = str(venue.get("name") or html_lib.unescape(str((item.get("container-title") or [""])[0])))
    paper_url = f"https://doi.org/{doi}"
    return {
        "id": f"doi:{doi.lower()}",
        "title": title,
        "authors": authors[:20],
        "summary": normalize_space(abstract),
        "published": published,
        "updated": published,
        "categories": [str(venue.get("group") or "期刊")],
        "paper_url": paper_url,
        "pdf_url": paper_url,
        "source": venue_name,
        "source_type": "conference",
        "venue": venue_name,
        "venue_group": str(venue.get("group") or ""),
        "doi": paper_url,
        "metadata_source": "crossref",
        "conference": {
            "id": str(venue.get("id") or ""),
            "name": venue_name,
            "year": work_year(published),
        },
    }


def fetch_venue_crossref(venue: dict[str, Any], from_date: str, rows: int) -> list[dict[str, Any]]:
    issns = [str(item).strip() for item in venue.get("issn") or [] if str(item).strip()]
    if not issns:
        return []
    params = {
        "filter": f"issn:{issns[0]},from-pub-date:{from_date},type:journal-article",
        "rows": str(max(1, min(200, rows))),
        "sort": "published",
        "order": "desc",
        "select": "DOI,title,author,abstract,published,published-print,published-online,issued,container-title,type",
        "mailto": contact_email(),
    }
    url = CROSSREF_WORKS_URL + "?" + urllib.parse.urlencode(params)
    data = crossref_request(url)
    papers = []
    for item in ((data.get("message") or {}).get("items") or []):
        paper = paper_from_crossref(item, venue)
        if paper:
            papers.append(paper)
    return papers


def journal_score(topic: Topic, paper: dict[str, Any]) -> dict[str, Any]:
    """Keyword / topical-overlap score tuned for journal records (no arXiv categories)."""
    haystack = f"{paper.get('title', '')} {paper.get('summary', '')}".lower()
    hits = []
    weighted = 0.0
    for keyword in topic.keywords:
        normalized = keyword.lower()
        if normalized and normalized in haystack:
            hits.append(keyword)
            weighted += min(1.0, max(0.35, len(normalized.split()) / 5))
    keyword_score = min(1.0, weighted / 2.0)

    topic_terms = set(re.findall(r"[a-zA-Z0-9]+", f"{topic.description} {' '.join(topic.keywords)}".lower()))
    paper_terms = set(re.findall(r"[a-zA-Z0-9]+", haystack))
    overlap = len(topic_terms & paper_terms) / max(8, len(topic_terms) * 0.18) if topic_terms else 0.0
    overlap_score = min(1.0, overlap)

    score = round(min(1.0, 0.75 * keyword_score + 0.25 * overlap_score), 3)
    if score >= HIGH_THRESHOLD and len(hits) >= 2:
        level = "high"
    elif score >= MEDIUM_THRESHOLD or len(hits) >= 2:
        level = "medium"
    else:
        level = "low"

    reason_parts = []
    if hits:
        reason_parts.append("关键词命中：" + "、".join(hits[:6]))
    venue = paper.get("venue") or "期刊"
    reason_parts.append(f"来源：{venue}")
    if not hits:
        reason_parts.append("仅文本弱相关，建议人工复核")
    return {
        "topic_id": topic.id,
        "topic_name": topic.name,
        "score": score,
        "level": level,
        "reason": "；".join(reason_parts),
        "keyword_hits": hits[:6],
    }


def score_and_filter(
    papers: list[dict[str, Any]],
    topics: list[Topic],
    min_score: float | None = None,
) -> tuple[list[dict[str, Any]], int]:
    kept = []
    dropped = 0
    threshold = env_float("MIN_JOURNAL_SCORE", 0.18) if min_score is None else min_score
    for paper in papers:
        matches = [journal_score(topic, paper) for topic in topics]
        matches.sort(key=lambda item: item["score"], reverse=True)
        best = matches[0]
        if not best.get("keyword_hits") and float(best.get("score") or 0.0) < threshold:
            dropped += 1
            continue
        paper["matches"] = matches
        paper["best_match"] = best
        kept.append(paper)
    return kept, dropped


def dedupe_by_doi(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique = []
    for paper in papers:
        key = str(paper.get("doi") or "").lower() or str(paper.get("id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(paper)
    return unique


def journal_fallback_summary(paper: dict[str, Any]) -> dict[str, str]:
    reason = str((paper.get("best_match") or {}).get("reason") or "与配置方向存在关键词匹配。")
    venue = str(paper.get("venue") or "该刊")
    if not has_meaningful_summary(paper):
        return {
            "problem": f"出版商未向开放索引提供这篇 {venue} 文章的摘要，目前只有题录信息。",
            "method": "请打开原文链接查看模型设定、识别策略与实证设计。",
            "innovation": "缺少摘要，无法可靠判断创新点，已避免让模型凭标题猜测。",
            "evidence": "暂无公开摘要，需要阅读原文核验。",
            "limitations": "缺摘要会降低相关性打分与中文总结的准确度。",
            "why_relevant": reason,
        }
    first_sentence = re.split(r"(?<=[.!?])\s+", str(paper.get("summary") or ""))[0]
    return {
        "problem": "尚未配置模型 API Key，当前只展示英文摘要首句。",
        "method": first_sentence[:300] if first_sentence else "请打开原文查看方法细节。",
        "innovation": "配置 DEEPSEEK_API_KEY 后会自动生成中文创新点总结。",
        "evidence": "证据可在原文摘要中核验。",
        "limitations": "基础模式不做深度技术对比。",
        "why_relevant": reason,
    }


def apply_summaries(papers: list[dict[str, Any]], topics: list[Topic], max_summaries: int) -> dict[str, Any]:
    topics_by_id = {topic.id: topic for topic in topics}
    jobs = []
    for paper in papers:
        if not has_meaningful_summary(paper):
            continue
        if len(jobs) >= max_summaries:
            break
        topic = topics_by_id.get(str((paper.get("best_match") or {}).get("topic_id")))
        if topic:
            jobs.append((topic, paper))

    stats = {"summary_jobs": len(jobs), "summary_succeeded": 0, "summary_failed": 0}
    if not llm_enabled() or not jobs:
        return stats

    concurrency = max(1, env_int("LLM_CONCURRENCY", 2))
    by_id = {str(paper.get("id")): paper for paper in papers}

    def run(job: tuple[Topic, dict[str, Any]]) -> tuple[str, dict[str, str], dict[str, Any]]:
        topic, paper = job
        summary, adjusted = summarize_with_llm(topic, paper, paper.get("best_match") or {})
        return str(paper.get("id")), summary, adjusted

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(run, job) for job in jobs]
        for future in concurrent.futures.as_completed(futures):
            try:
                paper_id, summary, adjusted = future.result()
            except Exception as exc:  # noqa: BLE001
                stats["summary_failed"] += 1
                print(f"Warning: journal summary failed: {exc}", file=sys.stderr, flush=True)
                continue
            stats["summary_succeeded"] += 1
            paper = by_id.get(paper_id)
            if not paper:
                continue
            paper["chinese_summary"] = summary
            paper["best_match"] = adjusted
            paper["matches"] = [
                adjusted if match.get("topic_id") == adjusted.get("topic_id") else match
                for match in paper.get("matches", [])
            ]
    return stats


def load_retained(
    existing: dict[str, Any],
    now: dt.datetime,
    retention_days: int,
    canonical_venues: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    cutoff = now - dt.timedelta(days=max(1, retention_days))
    retained = []
    for paper in existing.get("papers") or []:
        if not isinstance(paper, dict) or not paper.get("title"):
            continue
        published = parse_datetime(str(paper.get("published") or ""))
        if published and published < cutoff:
            continue
        level = str((paper.get("best_match") or {}).get("level") or "low").lower()
        if level not in RETAINED_LEVELS and published and (now - published).days > 45:
            continue
        if canonical_venues is not None:
            key = venue_key(str(paper.get("venue") or ""))
            canonical = canonical_venues.get(key)
            if not canonical:
                # 该刊已从配置里移除（或刊名对不上），不再保留
                continue
            paper = {**paper, "venue": canonical}
        retained.append(paper)
    return retained


def merge_papers(
    fresh: list[dict[str, Any]], retained: list[dict[str, Any]], now: dt.datetime
) -> list[dict[str, Any]]:
    now_iso = now.isoformat()
    previous = {}
    for paper in retained:
        key = str(paper.get("doi") or "").lower() or str(paper.get("id") or "")
        if key:
            previous[key] = paper

    merged = []
    for paper in fresh:
        key = str(paper.get("doi") or "").lower() or str(paper.get("id") or "")
        old = previous.pop(key, None) or {}
        paper["first_seen_at"] = old.get("first_seen_at") or now_iso
        paper["last_seen_at"] = now_iso
        paper["retained_from_previous_run"] = False
        if old and not has_meaningful_summary(paper) and has_meaningful_summary(old):
            paper["summary"] = old.get("summary", "")
            paper["chinese_summary"] = old.get("chinese_summary", {})
        merged.append(paper)

    for old in previous.values():
        carried = dict(old)
        carried["last_seen_at"] = now_iso
        carried["retained_from_previous_run"] = True
        merged.append(carried)

    return dedupe_papers(merged)


def sort_papers(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        papers,
        key=lambda paper: (
            float((paper.get("best_match") or {}).get("score") or 0),
            str(paper.get("published") or ""),
        ),
        reverse=True,
    )


def collect(
    topics_config_path: Path,
    journal_config_path: Path,
    output_path: Path,
    lookback_days: int,
    max_papers: int,
    max_summaries: int,
    max_stored_papers: int,
    max_data_bytes: int,
    clear_cache: bool,
) -> dict[str, Any]:
    topics = parse_topics(load_json(topics_config_path))
    journal_config = load_json(journal_config_path)
    venues = [venue for venue in journal_config.get("venues", []) if isinstance(venue, dict)]
    if journal_config.get("enabled", True) is False:
        venues = []

    now = dt.datetime.now(dt.timezone.utc)
    lookback = max(1, env_int("JOURNAL_LOOKBACK_DAYS", lookback_days))
    from_date = (now - dt.timedelta(days=lookback)).date().isoformat()
    per_page = env_int("JOURNAL_PER_VENUE_RESULTS", int(journal_config.get("per_venue_results", 100)))
    max_pages = max(1, env_int("JOURNAL_MAX_PAGES", int(journal_config.get("max_pages", 2))))
    delay = env_float("JOURNAL_VENUE_DELAY_SECONDS", 1.0)

    candidates: list[dict[str, Any]] = []
    source_stats: dict[str, Any] = {}
    use_openalex = True
    for index, venue in enumerate(venues):
        name = str(venue.get("name") or venue.get("id") or "venue")
        if index and delay > 0:
            time.sleep(delay)
        if use_openalex:
            print(f"Fetching OpenAlex journal works: {name}", flush=True)
            try:
                source_id = resolve_source_id(venue)
                works = fetch_venue_works(venue, source_id, from_date, per_page, max_pages)
                stats = {"type": "openalex_journal", "source_id": source_id, "fetched": len(works)}
                if not works:
                    widened = widen_from_date(from_date)
                    if widened != from_date:
                        print(
                            f"Notice: {name} returned 0 since {from_date}; retrying from {widened}",
                            flush=True,
                        )
                        works = fetch_venue_works(venue, source_id, widened, per_page, max_pages)
                        stats.update({"fetched": len(works), "from_date": widened})
                        if works:
                            stats["type"] = "openalex_journal_widened"
                candidates.extend(works)
                source_stats[name] = stats
                if works:
                    continue
                # OpenAlex 完全没有这本刊的近期记录，改走 Crossref 兜底
                print(f"Notice: {name} empty on OpenAlex, falling back to Crossref", flush=True)
                source_stats[name] = {**stats, "openalex_empty": True}
            except Exception as exc:  # noqa: BLE001
                print(f"Warning: OpenAlex failed for {name}: {exc}", file=sys.stderr, flush=True)
                if "429" in str(exc):
                    # 免费额度（按 IP 共享）被打满，本轮回退 Crossref，不再浪费重试时间
                    use_openalex = False

        print(f"Fetching Crossref journal works: {name}", flush=True)
        try:
            works = fetch_venue_crossref(venue, from_date, per_page)
            stats = {"type": "crossref_fallback", "fetched": len(works)}
            if not works:
                widened = widen_from_date(from_date)
                if widened != from_date:
                    print(
                        f"Notice: {name} returned 0 on Crossref since {from_date}; retrying from {widened}",
                        flush=True,
                    )
                    works = fetch_venue_crossref(venue, widened, per_page)
                    stats.update({"fetched": len(works), "from_date": widened})
            candidates.extend(works)
            source_stats[name] = {**source_stats.get(name, {}), **stats}
        except Exception as exc:  # noqa: BLE001
            source_stats[name] = {"type": "crossref_fallback", "fetched": 0, "error": str(exc)}
            print(f"Warning: journal fetch failed for {name}: {exc}", file=sys.stderr, flush=True)

    raw_count = len(candidates)
    unique_candidates = dedupe_by_doi(candidates)
    from llm_screen import apply_verdict, screen_papers, screening_active

    screen_llm = screening_active()
    screened_count = 0
    if screen_llm:
        # 机械分只作为排序初值和兜底，淘汰交给模型
        scored, _ = score_and_filter(unique_candidates, topics, min_score=0.0)
        verdicts = screen_papers(scored, topics, label="journals")
        min_screen = env_float("SCREEN_MIN_SCORE", 45.0)
        kept: list[dict[str, Any]] = []
        dropped = 0
        for paper in scored:
            verdict = verdicts.get(str(paper.get("id", "")))
            if verdict:
                apply_verdict(paper, verdict, topics)
                if float(verdict["score"]) < min_screen:
                    dropped += 1
                    continue
            kept.append(paper)
        screened_count = len(scored)
        print(
            f"AI screening kept {len(kept)}/{len(scored)} journal candidates (min_score={min_screen:g})",
            flush=True,
        )
        scored = sort_papers(kept)
    else:
        scored, dropped = score_and_filter(unique_candidates, topics)
        scored = sort_papers(scored)

    max_per_venue = env_int("MAX_JOURNAL_PER_VENUE", 6)
    if max_per_venue > 0:
        capped = []
        per_venue_count: dict[str, int] = {}
        for paper in scored:
            paper_venue = str(paper.get("venue") or "")
            if per_venue_count.get(paper_venue, 0) >= max_per_venue:
                continue
            per_venue_count[paper_venue] = per_venue_count.get(paper_venue, 0) + 1
            capped.append(paper)
        scored = capped

    if max_papers > 0:
        scored = scored[:max_papers]

    summary_stats = apply_summaries(scored, topics, max_summaries)
    for paper in scored:
        if "chinese_summary" not in paper:
            paper["chinese_summary"] = journal_fallback_summary(paper)

    existing: dict[str, Any] = {}
    if not clear_cache and output_path.exists():
        try:
            existing = load_json(output_path)
        except Exception as exc:  # noqa: BLE001
            print(f"Warning: cannot read existing journal data: {exc}", file=sys.stderr)
    canonical_venues = {
        venue_key(str(venue.get("name") or "")): str(venue.get("name") or "")
        for venue in venues
        if venue.get("name")
    }
    retained = load_retained(
        existing,
        now,
        env_int("JOURNAL_RETENTION_DAYS", 400),
        canonical_venues,
    )
    merged = sort_papers(merge_papers(scored, retained, now))

    stats = {
        "candidate_paper_count": raw_count,
        "new_paper_count": len(scored),
        "filtered_low_relevance_count": dropped,
        "retained_paper_count": len(retained),
        "venue_count": len(venues),
        "lookback_days": lookback,
        "from_publication_date": from_date,
        "llm_enabled": llm_enabled(),
        "screen_mode": "llm" if screen_llm else "rules",
        "screened_paper_count": screened_count,
        "source_stats": source_stats,
        "collection_mode": "incremental",
        "high_count": sum(1 for paper in merged if str((paper.get("best_match") or {}).get("level")) == "high"),
        "with_abstract_count": sum(1 for paper in merged if has_meaningful_summary(paper)),
        **summary_stats,
    }
    payload = {
        "generated_at": email.utils.format_datetime(now),
        "generated_at_iso": now.isoformat(),
        "config_source": "file",
        "data_kind": "journal",
        "topics": [topic.__dict__ for topic in topics],
        "papers": merged,
        "stats": stats,
    }
    trimmed, storage_stats = trim_papers_for_storage(payload, max_stored_papers, max_data_bytes)
    payload["papers"] = sort_papers(trimmed)
    payload["stats"].update(storage_stats)
    payload["stats"]["paper_count"] = len(payload["papers"])
    write_json(output_path, payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect recent journal articles into paper-daily.")
    parser.add_argument("--config", type=Path, default=Path("config/interests.json"))
    parser.add_argument("--journals-config", type=Path, default=DEFAULT_JOURNAL_CONFIG)
    parser.add_argument(
        "--output", type=Path, default=Path(os.getenv("JOURNAL_OUTPUT", str(DEFAULT_JOURNAL_OUTPUT)))
    )
    parser.add_argument("--lookback-days", type=int, default=env_int("JOURNAL_LOOKBACK_DAYS", 150))
    parser.add_argument("--max-papers", type=int, default=env_int("MAX_NEW_JOURNAL_PAPERS", 40))
    parser.add_argument("--max-summaries", type=int, default=env_int("MAX_JOURNAL_SUMMARIES", 30))
    parser.add_argument("--max-stored-papers", type=int, default=env_int("MAX_STORED_JOURNAL_PAPERS", 300))
    parser.add_argument("--max-data-bytes", type=int, default=env_int("MAX_DATA_BYTES", 8 * 1024 * 1024))
    parser.add_argument(
        "--clear-cache", action="store_true", default=os.getenv("CLEAR_PAPER_CACHE", "").lower() == "true"
    )
    args = parser.parse_args()

    payload = collect(
        args.config,
        args.journals_config,
        args.output,
        args.lookback_days,
        args.max_papers,
        args.max_summaries,
        args.max_stored_papers,
        args.max_data_bytes,
        args.clear_cache,
    )
    print(f"Wrote {len(payload['papers'])} journal papers to {args.output}")
    stats = payload["stats"]
    print(
        "Journal stats: "
        f"venues={stats.get('venue_count', 0)}, "
        f"raw={stats.get('candidate_paper_count', 0)}, "
        f"new={stats.get('new_paper_count', 0)}, "
        f"filtered={stats.get('filtered_low_relevance_count', 0)}, "
        f"with_abstract={stats.get('with_abstract_count', 0)}, "
        f"llm={stats.get('llm_enabled')}"
    )


if __name__ == "__main__":
    main()
