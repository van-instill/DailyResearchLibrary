"""补全论文作者的机构信息，并判断一作/二作是否来自目标院校。

arXiv 的 API 不带作者单位，OpenAlex 的 works 记录里 `authorships[].institutions`
才是权威来源。这里按 DOI 去 OpenAlex 换一次机构信息，结果缓存在
`.paper-cache/affiliations.json`，避免每周重复请求。

只落两个字段：
- `author_schools`：命中目标院校的作者（前端据此加粗 + 标注学校名）
- `author_institutions`：前三位作者的机构名（顶刊从 OpenAlex 采集时直接带上，
  arXiv 走 DOI 查询补；留给以后想按机构筛选时用）

目标院校清单在 `config/target_schools.json`，改清单不需要重新抓数据——
匹配是在读取缓存时做的，换一份 aliases 重新跑一遍即可。
"""

from __future__ import annotations

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

OPENALEX_WORKS_URL = "https://api.openalex.org/works"
DEFAULT_SCHOOLS_PATH = "config/target_schools.json"
DEFAULT_CACHE_PATH = ".paper-cache/affiliations.json"
CACHE_VERSION = 1

# 只关心一作/二作，多存一位当兜底（前端只加粗前两位）
AUTHOR_LIMIT = 3

_PUNCTUATION = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")
_ARXIV_ID = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")
_DOI_PREFIX = re.compile(r"^https?://(dx\.)?doi\.org/", re.IGNORECASE)


def normalize_name(value: Any) -> str:
    """机构名归一化：小写、& → and、标点变空格、压空格。"""
    text = str(value or "").lower().replace("&", " and ")
    text = _PUNCTUATION.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def load_target_schools(path: str | os.PathLike[str] = DEFAULT_SCHOOLS_PATH) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    schools: list[dict[str, Any]] = []
    for item in data.get("schools") or []:
        if not isinstance(item, dict) or not item.get("en"):
            continue
        aliases = [normalize_name(alias) for alias in item.get("aliases") or [] if alias]
        if not aliases:
            aliases = [normalize_name(item["en"])]
        schools.append({**item, "_aliases_norm": [alias for alias in aliases if alias]})
    return schools


def match_school(institution: str, schools: list[dict[str, Any]]) -> dict[str, Any] | None:
    """整词匹配机构名，避免 Smithsonian 命中 mit、Northeastern 命中 northwestern。"""
    name = normalize_name(institution)
    if not name:
        return None
    for school in schools:
        for alias in school.get("_aliases_norm") or []:
            if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", name):
                return school
    return None


def paper_doi(paper: dict[str, Any]) -> str:
    """取论文 DOI。arXiv 论文用 DataCite 的 10.48550 前缀，OpenAlex 也认。"""
    doi = _DOI_PREFIX.sub("", str(paper.get("doi") or "").strip())
    if doi:
        return doi.lower()
    paper_id = str(paper.get("id") or "")
    if paper_id.startswith("openalex:"):
        return ""
    if _ARXIV_ID.match(paper_id):
        return f"10.48550/arxiv.{paper_id.split('v')[0]}"
    url = str(paper.get("paper_url") or "")
    arxiv_match = re.search(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})", url, re.IGNORECASE)
    if arxiv_match:
        return f"10.48550/arxiv.{arxiv_match.group(1)}"
    return ""


def authorships_from_work(work: dict[str, Any], limit: int = AUTHOR_LIMIT) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for authorship in (work.get("authorships") or [])[:limit]:
        if not isinstance(authorship, dict):
            continue
        name = str((authorship.get("author") or {}).get("display_name") or "").strip()
        institutions: list[str] = []
        for institution in authorship.get("institutions") or []:
            label = str((institution or {}).get("display_name") or "").strip()
            if label and label not in institutions:
                institutions.append(label)
        result.append({"name": name, "insts": institutions})
    return result


def _request_json(url: str, timeout: float) -> Any:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "paper-radar-affiliations/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_authorships(
    paper: dict[str, Any],
    *,
    mailto: str = "",
    timeout: float = 25,
) -> list[dict[str, Any]] | None:
    """按 DOI 查 OpenAlex。返回 [] 表示 OpenAlex 确实没这条记录，None 表示请求失败。"""
    doi = paper_doi(paper)
    if not doi:
        return []
    params = {"select": "id,authorships"}
    if mailto:
        params["mailto"] = mailto
    url = f"{OPENALEX_WORKS_URL}/doi:{urllib.parse.quote(doi, safe='/')}?{urllib.parse.urlencode(params)}"
    try:
        work = _request_json(url, timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return []
        return None
    except Exception:  # noqa: BLE001 - 网络问题不该让整条流水线失败
        return None
    if not isinstance(work, dict):
        return []
    return authorships_from_work(work)


def _load_cache(path: Path) -> dict[str, Any]:
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("entries"), dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {"version": CACHE_VERSION, "entries": {}}


def _write_cache(path: Path, cache: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def apply_affiliations(
    paper: dict[str, Any],
    authorships: list[dict[str, Any]],
    schools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把机构写回论文对象，返回命中的目标院校作者。"""
    institutions = [item for item in authorships if item.get("insts")]
    if institutions:
        paper["author_institutions"] = [
            {"name": item.get("name", ""), "insts": list(item.get("insts") or [])} for item in institutions
        ]
    else:
        paper.pop("author_institutions", None)

    matches: list[dict[str, Any]] = []
    for index, item in enumerate(authorships):
        for institution in item.get("insts") or []:
            school = match_school(institution, schools)
            if school:
                matches.append(
                    {
                        "i": index,
                        "name": item.get("name", ""),
                        "school": school.get("en", ""),
                        "school_zh": school.get("zh", ""),
                        "inst": institution,
                    }
                )
                break
    if matches:
        paper["author_schools"] = matches
    else:
        paper.pop("author_schools", None)
    return matches


def enrich_papers(
    papers: list[dict[str, Any]],
    *,
    schools: list[dict[str, Any]] | None = None,
    schools_path: str | os.PathLike[str] = DEFAULT_SCHOOLS_PATH,
    cache_path: str | os.PathLike[str] = DEFAULT_CACHE_PATH,
    mailto: str = "",
    delay: float = 0.2,
    timeout: float = 25,
    max_lookups: int | None = None,
    logger: Any = None,
) -> dict[str, int]:
    """给一批论文补全机构信息。返回统计信息。"""
    log = logger or (lambda *args, **kwargs: None)
    if schools is None:
        schools = load_target_schools(schools_path)
    cache_file = Path(cache_path)
    cache = _load_cache(cache_file)
    entries: dict[str, Any] = cache["entries"]

    stats = {"papers": len(papers), "fetched": 0, "cached": 0, "failed": 0, "matched": 0}
    for paper in papers:
        if not isinstance(paper, dict):
            continue
        key = str(paper.get("id") or paper.get("title") or "")
        if not key:
            continue

        # 顶刊是从 OpenAlex 采的，authorships 里本来就带 institutions，直接复用不用再查
        existing = paper.get("author_institutions")
        if isinstance(existing, list) and existing:
            authorships = [
                {"name": str(item.get("name") or ""), "insts": list(item.get("insts") or [])}
                for item in existing
                if isinstance(item, dict)
            ]
            matches = apply_affiliations(paper, authorships, schools)
            if matches:
                stats["matched"] += 1
                labels = ", ".join("{}(第{}作者)".format(m["school"], m["i"] + 1) for m in matches)
                log(f"  ★ {paper.get('title', '')[:58]} -> {labels}")
            continue

        entry = entries.get(key)
        if not isinstance(entry, dict):
            # 首次跑全量归档会撞上几百篇没缓存的老论文，限个上限免得流水线跑不完；
            # 没轮到的下次运行继续补（缓存是按 id 累积的）
            if max_lookups is not None and stats["fetched"] >= max_lookups:
                continue
            authorships = fetch_authorships(paper, mailto=mailto, timeout=timeout)
            if authorships is None:
                stats["failed"] += 1
                continue
            entry = {"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "authorships": authorships}
            entries[key] = entry
            stats["fetched"] += 1
            if delay:
                time.sleep(delay)
        else:
            stats["cached"] += 1

        matches = apply_affiliations(paper, entry.get("authorships") or [], schools)
        if matches:
            stats["matched"] += 1
            labels = ", ".join("{}(第{}作者)".format(m["school"], m["i"] + 1) for m in matches)
            log(f"  ★ {paper.get('title', '')[:58]} -> {labels}")

    cache["version"] = CACHE_VERSION
    _write_cache(cache_file, cache)
    return stats


def enrich_file(
    path: str | os.PathLike[str],
    *,
    schools_path: str | os.PathLike[str] = DEFAULT_SCHOOLS_PATH,
    cache_path: str | os.PathLike[str] = DEFAULT_CACHE_PATH,
    mailto: str = "",
    delay: float = 0.2,
    dry_run: bool = False,
) -> dict[str, int]:
    target = Path(path)
    if not target.exists():
        return {"papers": 0, "fetched": 0, "cached": 0, "failed": 0, "matched": 0}
    data = json.loads(target.read_text(encoding="utf-8"))
    papers = data.get("papers") or []
    stats = enrich_papers(
        papers,
        schools_path=schools_path,
        cache_path=cache_path,
        mailto=mailto,
        delay=delay,
        logger=print,
    )
    if not dry_run:
        data["papers"] = papers
        # 和 collect_papers.write_json 保持一致（indent=2 + 结尾换行），否则整份文件
        # 都会被重排成一次几万行的假 diff
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return stats


def resolve_contact_email() -> str:
    return os.getenv("CONTACT_EMAIL") or os.getenv("OPENALEX_EMAIL") or ""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(".")
    if "--root" in args:
        root = Path(args[args.index("--root") + 1])
    dry_run = "--dry-run" in args
    files = [root / "web/data/papers.json", root / "web/data/conference_papers.json"]
    total = {"papers": 0, "fetched": 0, "cached": 0, "failed": 0, "matched": 0}
    for path in files:
        if not path.exists():
            continue
        print(f"== {path} ==")
        stats = enrich_file(
            path,
            schools_path=root / DEFAULT_SCHOOLS_PATH,
            cache_path=root / DEFAULT_CACHE_PATH,
            mailto=resolve_contact_email(),
            dry_run=dry_run,
        )
        for key, value in stats.items():
            total[key] += value
        print(
            f"   {stats['papers']} 篇 / 新查 {stats['fetched']} / 缓存 {stats['cached']} / "
            f"失败 {stats['failed']} / 命中目标院校 {stats['matched']}"
        )
    print(f"合计：{total['papers']} 篇，命中目标院校作者 {total['matched']} 篇")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
