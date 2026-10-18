# 金融计量论文日报 · Paper Daily

每天自动追踪 **arXiv 预印本** 和 **金融/计量顶刊新文**，按你自己的研究方向打分，并生成中文创新点总结。抓取和部署都在 GitHub Actions 上跑，不需要服务器，打开一个网页就能看。

页面分两个集合，互不挤占存储：

- **每日新论文**：arXiv（q-fin / econ.EM / stat.ME 等分类）当天新增或修订的论文
- **顶刊精选**：`Journal of Finance`、`JFE`、`RFS`、`JFQA`、`Journal of Econometrics`、`Econometrica`、`REStat`、`JBES`、`JAE`、`JBF`、`JEF`、`Quantitative Finance` 近几个月的新文

数据流：GitHub Actions 定时运行 Python 脚本 → 抓取 + 打分 + 中文总结 → 写入 `web/data/*.json` → 发布静态页面到 GitHub Pages。

## 这套东西怎么工作

| 环节 | 实现 | 说明 |
| --- | --- | --- |
| 预印本抓取 | `scripts/collect_papers.py` | arXiv API，`category` 模式只按学科分类召回（关键词不再作硬门槛，交给 AI 阅卷） |
| 顶刊抓取 | `scripts/collect_journals.py` | OpenAlex API（按 ISSN 定位期刊 → 拉全量新文），被限流时自动回退 Crossref |
| AI 阅卷 | `scripts/llm_screen.py` | 模型逐篇判断相关性 0-100 分并指定方向；批量调用 + 磁盘缓存，异常时退回机械评分 |
| 机械打分 | 两个采集脚本内 | 关键词命中 + 方向描述词重合，作为兜底与并列排序依据 |
| 中文总结 | DeepSeek / OpenAI 兼容接口 | 输出「问题 / 方法 / 创新 / 证据 / 局限 / 为什么相关」六段 |
| 展示 | `web/index.html` + `app.js` | 纯静态，无需后端；支持按方向、匹配度、日期筛选 |

## 目录结构

```
config/interests.json        # 研究方向：关键词 + arXiv 分类
config/journals.json         # 顶刊清单：ISSN + OpenAlex 来源 ID
scripts/collect_papers.py    # arXiv 每日采集
scripts/collect_journals.py  # 顶刊采集（OpenAlex + Crossref 回退）
scripts/llm_screen.py        # AI 阅卷：批量判相关性 + 缓存 + 失败降级
web/                         # 静态站点
.github/workflows/paper-daily.yml
```

## AI 阅卷：模型怎么参与筛选

默认开启（`SCREEN_MODE=llm`）。完整链路：

```
抓取（放宽召回） → 机械打分（只做排序初值） → AI 批量阅卷 → 按 AI 分淘汰 → 截断配额 → 生成六段摘要
```

- **召回放宽**：arXiv 用 `ARXIV_QUERY_MODE=category`，只要落在 `q-fin.ST`、`econ.EM` 这些分类里就进候选池，不再强制关键词命中。这样「研究同一件事但换了说法」的论文不会漏（比如写 `tail dependence` 而关键词是 `tail risk`）。
- **判断标准**：模型逐篇打 0-100 分（85+ 核心相关；60-84 相关；40-59 只是沾边；20-39 只在背景里提了一句；0-19 无关），同时指定最匹配的方向 id 并给 40 字理由。低于 `SCREEN_MIN_SCORE`（默认 45）直接淘汰。
- **成本与速度**：默认每 6 篇打包一次请求，单轮最多 400 篇，并发 3。首次全量阅卷约 60 次请求、一分钟内跑完，费用约 ¥0.2~0.5；之后每天只对**新论文**调用，因为判定结果缓存在 `.paper-cache/screen_cache.json`（跟随 Actions cache 持久化）。
- **失败降级**：没配 key、请求失败或返回的 JSON 不合法时，这批论文保留机械评分，页面不会变空。想彻底关掉就把 `SCREEN_MODE` 设为 `rules`。
- **可追溯**：每篇论文的 `best_match` 里同时保留 `mechanical_score` 和 `llm_score`，方便你对照着调阈值。

> 没有摘要的论文不会被送进阅卷（模型只能看标题，容易瞎猜），这类保留机械评分。

## 六段内容是怎么写出来的

「问题 / 方法 / 创新 / 证据 / 局限 / 匹配」由模型一次性生成，**输入只有四项**：

```
标题 + 作者 + arXiv 分类（期刊论文没有分类）+ 摘要原文
```

**没有读全文。** 系统不下载任何 PDF，也不做全文解析。这样做的原因很实际：这些期刊都是付费墙后的正文，没有合法稳定的批量获取途径；而要判断「这篇要不要读」，摘要的信息量已经够用。

因此写作时有一层硬约束：提示词明确要求「不要夸大摘要中没有的信息；如果证据不足，请明确说明」。所以

- 「证据」写的是**摘要里提到的**实验结果、理论结果或数据来源，摘要没写就写「证据不足，需读原文」；
- 「局限」本质是**推断**，常见表述是「需要阅读全文确认……」，不要当成作者的自述；
- 六段里唯一不依赖摘要的是「匹配」，它只对照你自己的研究方向判断。

### 各字段的含义

| 字段 | 内容 | 数据来源 |
| --- | --- | --- |
| 问题 | 论文真正要解决的问题，1–2 句，去掉空泛背景 | 摘要 |
| 方法 | 核心方法 2–3 句，含关键技术组件或数学形式 | 摘要 |
| 创新 | 相对已有工作的具体增量，2–3 点合并成一段 | 摘要 |
| 证据 | 摘要里可核验的实验/理论/数据证据，没有就写证据不足 | 摘要 |
| 局限 | 可能的问题，或需要读全文才能确认的点 | 推断 |
| 匹配 | **为什么这篇和你有关**：命中了哪个研究方向、哪些关键词/场景，以及是否需要人工复核。它会反向影响该论文的 AI 分数（`match_score_adjustment`）和等级 | 你的 `interests.json` + 摘要 |

卡片右下角那行小字也来自「匹配」（优先用模型给出的 `llm_reason`，没有时回退到关键词理由）。

### 数学公式用 LaTeX

提示词要求模型把符号和公式写成 LaTeX（行内 `$...$`，独立公式 `$$...$$`），前端用内置的 KaTeX 渲染，所以看到的是 $\sigma_t^2=\omega+\alpha\varepsilon_{t-1}^2+\beta\sigma_{t-1}^2$ 这样的排版，而不是 `sigma^2 = omega + alpha*e^2` 这类纯文本。

KaTeX 已内置在 `web/vendor/katex/`（只打包 woff2 字体，约 600 KB），不依赖任何 CDN，国内网络也能正常显示。渲染失败时自动回退成原始文本，不会白屏。

> 改提示词后需要让旧摘要失效：`scripts/llm_screen.py` 里的 `SUMMARY_CACHE_PATH` 就是缓存版本号（当前 `summary_cache.v2.json`），升一个版本即可让新格式重写一遍。


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

默认收录 `国际期刊目录.md` 里的 **29 本**，按 `group` 分成经济学（7）、统计学（6）、金融学（16），每条带 `rank`（4\* / 4 / 3 / 2）。

加期刊需要两个东西：ISSN（[portal.issn.org](https://portal.issn.org) 查）和 OpenAlex 来源 ID。

```bash
curl "https://api.openalex.org/sources?filter=issn:0022-1082&select=id,display_name"
```

拿到 `S5353659` 这样的 ID 填进 `openalex_source_id` 即可。**不填也能用**——脚本会按 ISSN 现查（每本多一次请求）。仓库里有 `scripts/_probe_journals.py`，改一下里面的刊名清单就能批量解析 ISSN 并顺带统计各刊的摘要覆盖率。

`per_venue_results` 默认 25：单刊在一轮里最多取多少条近期记录。29 本 × 25 ≈ 700 条候选，够 AI 阅卷挑；调太大会让 OpenAlex 请求变慢且候选池里堆满低相关论文。

## 常用可调参数

在 `Settings → Secrets and variables → Actions → Variables` 里加，都是可选的：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MAX_PER_TOPIC` | 20 | 每个方向最多抓多少篇 arXiv 候选 |
| `MAX_NEW_PAPERS` | 50 | 每日新论文展示上限 |
| `MAX_SUMMARIES` | 30 | 每天最多生成多少篇六段中文分析（控制成本） |
| `JOURNAL_LOOKBACK_DAYS` | 150 | 顶刊回看天数 |
| `MAX_NEW_JOURNAL_PAPERS` | 40 | 顶刊单轮新增上限 |
| `MAX_JOURNAL_SUMMARIES` | 30 | 顶刊每日总结条数上限 |
| `MAX_JOURNAL_PER_VENUE` | 6 | 单刊最多保留几篇，避免高产期刊刷屏 |
| `MIN_PAPER_SCORE` | 0.08 | 每日论文最低机械相关性分（AI 阅卷关闭时生效） |
| `MIN_JOURNAL_SCORE` | 0.18 | 顶刊最低机械相关性分（AI 阅卷关闭时生效） |
| `ARXIV_QUERY_MODE` | category | `category` = 只按分类召回（推荐，配合 AI 阅卷）；`strict` = 关键词 AND 分类；`keyword` = 只按关键词 |
| `SCREEN_MODE` | llm | `llm` 用模型阅卷；`rules` 退回纯机械评分 |
| `SCREEN_MIN_SCORE` | 45 | AI 分低于此值淘汰（0-100）。想多看调到 35，只看硬核调到 60 |
| `SCREEN_BATCH_SIZE` | 6 | 每次请求塞几篇，越大越省钱但越容易输出截断 |
| `SCREEN_MAX_PAPERS` | 400 | 单轮最多阅卷多少篇，超出部分保留机械评分 |
| `SCREEN_CONCURRENCY` | 3 | 并发请求数，遇到 429 就调小 |
| `SCREEN_CACHE_DAYS` | 60 | AI 判定结果的缓存天数，越大越省钱 |
| `LOOKBACK_DAYS` | 1 | 定时任务默认只抓上一次运行之后的新文 |

## 已知限制（重要）

- **OpenAlex 免费额度按 IP 共享**：不带 key 时每天只有约 $0.10 的公共额度，GitHub Actions 的共享 IP 常常已经被别的任务打满。建议花一分钟申请免费 key（每天 $1，独立额度）：

  ```bash
  curl -X POST https://api.openalex.org/api-keys -H "Content-Type: application/json" \
       -d '{"email":"你的邮箱"}'
  ```

  点邮件里的链接激活，把 key 存成 `OPENALEX_API_KEY` secret。即使不配，脚本也会自动回退到 Crossref，页面不会空白。
- **少数期刊不给开放索引摘要**：这里说的「不给」是指出版社**不把摘要写进 Crossref 等元数据**，不是网页上不显示。OpenAlex 的摘要由 Crossref 派生，所以一家没有、另一家必然也没有；Semantic Scholar 同样是 `null`；直接抓出版社页面（T&F / ScienceDirect）会被 Cloudflare 挡回 `403`。实测 112 篇顶刊论文里只有 10 篇属于这种情况（Informa 6 篇、Elsevier 4 篇）。
  - 兜底：`scripts/collect_journals.py` 会按标题去 **arXiv 找同名预印本**（标题相似度 ≥ 0.92 才采用），拿到就用它的摘要，结果按 DOI 缓存在 `.paper-cache/preprint_cache.json`。首次实测 10 篇里救回 4 篇，且都是标题完全一致的同一篇工作。
  - 配置：`PREPRINT_MAX_LOOKUPS`（默认 20）控制单次最多查几篇，设为 0 关闭。arXiv 建议请求间隔 3 秒，脚本已遵守。
  - 为什么不搜网页：Google/Bing 都禁止程序化抓取且会返回验证码，属于不可用也不合规的路子；arXiv API 是公开的，反而是更稳的等价手段。仍然拿不到的少数条目会明确标注「出版商未向开放索引提供摘要」，请点「原文」自行查看。
- **AI 阅卷依赖摘要质量**：这是当前最影响效果的一环。想让某本刊多出内容，先确认它的摘要覆盖率（用 `scripts/_probe_journals.py` 可以批量统计）。
- **arXiv 429**：同时开太多方向会被限流，脚本默认每个方向之间停 15 秒；被限流时自动跳过并使用缓存数据。
- **分数只反映关键词与分类重合度**，不是论文质量的判断，high 只是「很可能相关」，最终还是要点开原文。

## 与原项目（Futuresxy/paper-daily）的差异

- 研究方向换成金融计量（5 个方向 + 12 本金融/计量期刊）
- 新增 `scripts/collect_journals.py`：用 OpenAlex 按 ISSN 抓顶刊，Crossref 兜底，替代原来的 DBLP 会议抓取
- 「顶会精品」改成「顶刊精选」，页面标题、示意图标链接（自动指向你自己的仓库）都做了调整
- 相关性分级门槛按金融文本的实际分布重新标定（high ≥ 0.40 / medium ≥ 0.30），且只命中 1 个关键词不判 high
- arXiv 查询默认 `strict`（关键词 AND 分类），减少跨领域噪声

原始项目：<https://github.com/Futuresxy/paper-daily>（仓库里没有 LICENSE 文件，个人自用没问题，二次分发前请先确认授权）
