# 金融计量论文日报 · Paper Daily

每天自动追踪 **arXiv 预印本** 和 **金融/计量顶刊新文**，按你自己的研究方向打分，并生成中文创新点总结。抓取和部署都在 GitHub Actions 上跑，不需要服务器，打开一个网页就能看。

页面分两个集合，互不挤占存储：

- **每日新论文**：arXiv（q-fin / econ.EM / stat.ME 等分类）当天新增或修订的论文
- **顶刊精选**：`Journal of Finance`、`JFE`、`RFS`、`JFQA`、`Journal of Econometrics`、`Econometrica`、`REStat`、`JBES`、`JAE`、`JBF`、`JEF`、`Quantitative Finance` 近几个月的新文

数据流：GitHub Actions 定时运行 Python 脚本 → 抓取 + 打分 + 中文总结 → 写入 `web/data/*.json` → 发布静态页面到 GitHub Pages。

## 这套东西怎么工作

| 环节 | 实现 | 说明 |
| --- | --- | --- |
| 预印本抓取 | `scripts/collect_papers.py` | arXiv API，按关键词 + 分类（strict 模式）检索 |
| 顶刊抓取 | `scripts/collect_journals.py` | OpenAlex API（按 ISSN 定位期刊 → 拉全量新文），被限流时自动回退 Crossref |
| 相关性打分 | 两个脚本内 | 关键词命中 + 方向描述词重合（顶刊没有 arXiv 分类，用单独的权重） |
| 中文总结 | DeepSeek / OpenAI 兼容接口 | 输出「问题 / 方法 / 创新 / 证据 / 局限 / 为什么相关」六段 |
| 展示 | `web/index.html` + `app.js` | 纯静态，无需后端；支持按方向、匹配度、日期筛选 |

## 目录结构

```
config/interests.json        # 研究方向：关键词 + arXiv 分类
config/journals.json         # 顶刊清单：ISSN + OpenAlex 来源 ID
scripts/collect_papers.py    # arXiv 每日采集
scripts/collect_journals.py  # 顶刊采集（OpenAlex + Crossref 回退）
web/                         # 静态站点
.github/workflows/paper-daily.yml
```

## 部署步骤

### 1. 传到你的 GitHub 仓库

新建一个公开仓库，把本目录推上去：

```bash
git remote set-url origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

### 2. 开启 Pages

`Settings → Pages → Build and deployment → Source` 选 **GitHub Actions**。
跑完一次后访问地址是 `https://<你的用户名>.github.io/<仓库名>/`。

### 3. 配置中文总结（推荐）

`Settings → Secrets and variables → Actions → Secrets → New repository secret`：

| Name | 值 |
| --- | --- |
| `DEEPSEEK_API_KEY` | 你的 DeepSeek Key（默认走 `https://api.deepseek.com/v1`，模型 `deepseek-chat`） |
| `OPENALEX_API_KEY` | 可选但强烈建议，见下方「OpenAlex 限流」 |

用 OpenAI 就换成 `OPENAI_API_KEY`；用别的兼容服务填 `LLM_API_KEY`，再在 Variables 里设 `LLM_BASE_URL` 和 `LLM_MODEL`。

不配 Key 也能跑，只是没有中文总结，页面会显示英文摘要首句。

### 4. 第一次手动运行

`Actions → Paper Daily → Run workflow`，`lookback_days` 填 `7`（首次回看天数）。
之后默认每天北京时间 **06:00** 自动增量更新。

### 5. 改研究方向

两种方式，选一种即可：

- **改文件**（简单直接）：编辑 `config/interests.json` 里的 `topics`，push 后自动触发
- **提 Issue**（不用碰代码）：`Issues → New issue → Research Interests`，改 JSON 里的 `keywords` 和 `arxiv_categories` 后提交

## 本地预览

```bash
python scripts/collect_papers.py --conference-output .paper-cache/conference_unused.json
python scripts/collect_journals.py
python -m http.server 8000 --directory web
```

浏览器打开 `http://localhost:8000`。本地跑的时候把 `DEEPSEEK_API_KEY` 放进环境变量即可生成中文总结。

## 配置说明

### `config/interests.json`

```json
{
  "topics": [
    {
      "id": "volatility_highfreq",
      "name": "波动率建模与高频计量",
      "description": "关注已实现波动率、GARCH 与随机波动率模型……",
      "keywords": ["realized volatility", "GARCH", "high-frequency data"],
      "arxiv_categories": ["q-fin.ST", "q-fin.TR", "econ.EM"]
    }
  ]
}
```

调整建议：

- `keywords` 写英文，arXiv 和期刊摘要都是英文；**6～10 个**最合适，太多会把分数摊薄
- 避免过于宽泛的词（`machine learning`、`causal inference`），换成能定位子领域的短语
- `arxiv_categories` 填 2～4 个。金融计量的常用组合：`q-fin.ST`、`q-fin.PM`、`q-fin.RM`、`q-fin.TR`、`q-fin.EC`、`econ.EM`、`stat.ME`、`stat.AP`
- 一个方向一条，5 个方向大约每分钟能跑完

### `config/journals.json`

加期刊需要两个东西：ISSN（[portal.issn.org](https://portal.issn.org) 查）和 OpenAlex 来源 ID。

```bash
curl "https://api.openalex.org/sources?filter=issn:0022-1082&select=id,display_name"
```

拿到 `S5353659` 这样的 ID 填进 `openalex_source_id` 即可（不填也行，脚本会自己查，多花一次请求）。

常用 ISSN：JF `0022-1082`、JFE `0304-405X`、RFS `0893-9454`、JFQA `0022-1090`、JoE `0304-4076`、Econometrica `0012-9682`、REStat `0034-6535`、JBES `0735-0015`、JAE `0883-7252`、JBF `0378-4266`、JEF `0927-5398`、QF `1469-7688`。

## 常用可调参数

在 `Settings → Secrets and variables → Actions → Variables` 里加，都是可选的：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MAX_PER_TOPIC` | 15 | 每个方向最多抓多少篇 arXiv 候选 |
| `MAX_NEW_PAPERS` | 50 | 每日新论文展示上限 |
| `MAX_SUMMARIES` | 20 | 每天最多调用模型多少次（控制成本） |
| `JOURNAL_LOOKBACK_DAYS` | 150 | 顶刊回看天数 |
| `MAX_NEW_JOURNAL_PAPERS` | 40 | 顶刊单轮新增上限 |
| `MAX_JOURNAL_SUMMARIES` | 30 | 顶刊每日总结条数上限 |
| `MAX_JOURNAL_PER_VENUE` | 6 | 单刊最多保留几篇，避免高产期刊刷屏 |
| `MIN_PAPER_SCORE` | 0.08 | 每日论文最低相关性分 |
| `MIN_JOURNAL_SCORE` | 0.18 | 顶刊最低相关性分 |
| `ARXIV_QUERY_MODE` | strict | `strict` = 关键词 AND 分类，`keyword` = 只按关键词 |
| `LOOKBACK_DAYS` | 1 | 定时任务默认只抓上一次运行之后的新文 |

## 已知限制（重要）

- **OpenAlex 免费额度按 IP 共享**：不带 key 时每天只有约 $0.10 的公共额度，GitHub Actions 的共享 IP 常常已经被别的任务打满。建议花一分钟申请免费 key（每天 $1，独立额度）：

  ```bash
  curl -X POST https://api.openalex.org/api-keys -H "Content-Type: application/json" \
       -d '{"email":"你的邮箱"}'
  ```

  点邮件里的链接激活，把 key 存成 `OPENALEX_API_KEY` secret。即使不配，脚本也会自动回退到 Crossref，页面不会空白。
- **Elsevier 期刊（JFE / JoE / JBF）经常不提供摘要**：OpenAlex 和 Crossref 都拿不到，这几本刊的部分论文只有标题和 DOI。脚本不会让模型凭标题猜内容，这类条目会明确标注「未提供摘要」。
- **arXiv 429**：同时开太多方向会被限流，脚本默认每个方向之间停 15 秒；被限流时自动跳过并使用缓存数据。
- **分数只反映关键词与分类重合度**，不是论文质量的判断，high 只是「很可能相关」，最终还是要点开原文。

## 与原项目（Futuresxy/paper-daily）的差异

- 研究方向换成金融计量（5 个方向 + 12 本金融/计量期刊）
- 新增 `scripts/collect_journals.py`：用 OpenAlex 按 ISSN 抓顶刊，Crossref 兜底，替代原来的 DBLP 会议抓取
- 「顶会精品」改成「顶刊精选」，页面标题、示意图标链接（自动指向你自己的仓库）都做了调整
- 相关性分级门槛按金融文本的实际分布重新标定（high ≥ 0.40 / medium ≥ 0.30），且只命中 1 个关键词不判 high
- arXiv 查询默认 `strict`（关键词 AND 分类），减少跨领域噪声

原始项目：<https://github.com/Futuresxy/paper-daily>（仓库里没有 LICENSE 文件，个人自用没问题，二次分发前请先确认授权）
