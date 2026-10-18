"""一次性探查：把国际期刊目录里的刊名解析成 ISSN，并统计 OpenAlex/Crossref 的可达性。"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import urllib.request

MAILTO = "paperdaily@example.com"
UA = f"paper-daily-probe/1.0 (mailto:{MAILTO})"

JOURNALS = [
    ("经济学", "The Quarterly Journal of Economics", "4*"),
    ("经济学", "The Review of Economic Studies", "4*"),
    ("经济学", "Econometrica", "4*"),
    ("经济学", "The Review of Economics and Statistics", "4"),
    ("经济学", "Quantitative Economics", "4"),
    ("经济学", "Econometric Theory", "4"),
    ("经济学", "Econometric Reviews", "3"),
    ("统计学", "The Annals of Statistics", "4*"),
    ("统计学", "Journal of the American Statistical Association", "4"),
    ("统计学", "Journal of Econometrics", "4"),
    ("统计学", "Biometrika", "4"),
    ("统计学", "Journal of Business & Economic Statistics", "4"),
    ("统计学", "Journal of the Royal Statistical Society: Series B", "4"),
    ("金融学", "The Journal of Finance", "4*"),
    ("金融学", "Journal of Financial Economics", "4*"),
    ("金融学", "The Review of Financial Studies", "4*"),
    ("金融学", "Review of Finance", "4"),
    ("金融学", "Journal of Financial and Quantitative Analysis", "4"),
    ("金融学", "Journal of Corporate Finance", "4"),
    ("金融学", "Journal of Financial Intermediation", "4"),
    ("金融学", "Journal of Money, Credit and Banking", "4"),
    ("金融学", "Journal of Futures Markets", "3"),
    ("金融学", "Quantitative Finance", "3"),
    ("金融学", "Mathematical Finance", "3"),
    ("金融学", "Finance and Stochastics", "3"),
    ("金融学", "The Review of Asset Pricing Studies", "3"),
    ("金融学", "Journal of Financial Econometrics", "3"),
    ("金融学", "Insurance: Mathematics and Economics", "3"),
    ("金融学", "SIAM Journal on Financial Mathematics", "2"),
]


def get_json(url: str, timeout: int = 40) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def resolve(name: str) -> dict:
    params = urllib.parse.urlencode({"query": name, "rows": "5", "mailto": MAILTO})
    data = get_json(f"https://api.crossref.org/journals?{params}")
    items = data.get("message", {}).get("items", [])
    if not items:
        return {"ok": False, "reason": "no journal match"}
    # 优先标题完全一致的，否则取第一本
    target = norm(name)
    best = next((it for it in items if norm(str(it.get("title") or "")) == target), items[0])
    issns = sorted({str(x) for x in (best.get("ISSN") or []) if x})
    return {
        "ok": True,
        "title": str(best.get("title") or ""),
        "issns": issns,
        "publisher": str(best.get("publisher") or ""),
        "exact": norm(str(best.get("title") or "")) == target,
    }


def abstract_coverage(issn: str, rows: int = 20) -> tuple[int, int]:
    params = urllib.parse.urlencode(
        {
            "filter": f"issn:{issn},type:journal-article",
            "rows": str(rows),
            "sort": "published",
            "order": "desc",
            "select": "title,abstract,published",
            "mailto": MAILTO,
        }
    )
    data = get_json(f"https://api.crossref.org/works?{params}")
    items = data.get("message", {}).get("items", [])
    with_abs = sum(1 for it in items if len(str(it.get("abstract") or "")) > 120)
    return len(items), with_abs


def main() -> None:
    report = []
    for group, name, rank in JOURNALS:
        try:
            info = resolve(name)
        except Exception as exc:  # noqa: BLE001
            report.append({"group": group, "name": name, "rank": rank, "ok": False, "reason": str(exc)})
            time.sleep(1)
            continue
        if not info.get("ok"):
            report.append({"group": group, "name": name, "rank": rank, **info})
            time.sleep(1)
            continue
        total, with_abs = 0, 0
        if info["issns"]:
            try:
                total, with_abs = abstract_coverage(info["issns"][0])
            except Exception as exc:  # noqa: BLE001
                info["abs_error"] = str(exc)
        report.append(
            {
                "group": group,
                "name": name,
                "rank": rank,
                "matched_title": info["title"],
                "exact": info["exact"],
                "issns": info["issns"],
                "publisher": info["publisher"],
                "recent": total,
                "with_abstract": with_abs,
            }
        )
        print(
            f"{name[:46]:48s} -> {','.join(info['issns'])[:22]:24s} "
            f"abs {with_abs}/{total} | {info['publisher'][:28]}",
            flush=True,
        )
        time.sleep(1)

    with open(".paper-cache/journal_probe.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    missing = [r for r in report if not r.get("issns")]
    low = [r for r in report if r.get("recent") and r["with_abstract"] / max(1, r["recent"]) < 0.3]
    low_detail = [(r["name"][:30], f"{r['with_abstract']}/{r['recent']}") for r in low]
    print(f"\n解析失败 {len(missing)} 本：{[r['name'] for r in missing]}")
    print(f"摘要覆盖率 <30% 的刊 {len(low)} 本：{low_detail}")


if __name__ == "__main__":
    main()
