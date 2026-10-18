# 金融计量论文周报 · Paper Radar

每周自动从 **2018 年至今**的 **arXiv 预印本** 和 **金融/计量顶刊** 里挑出最贴合研究方向的论文，按 AI 判定的相关性打分，生成中文解读。抓取和部署都在 GitHub Actions 上跑，不需要服务器，打开一个网页就能看。

页面分两个集合，**合计每周新增 10 篇**，旧论文一律不删、留在归档里：

- **arXiv 新论文**：每周新增 5 篇，来自 q-fin / econ.EM / stat.ME 等分类，含"2018 起 + 相关度排序"的老预印本召回
- **顶刊精选**：`Journal of Finance`、`JFE`、`RFS`、`JFQA`、`Journal of Econometrics`、`Econometrica`、`REStat`、`JBES`、`Quantitative Finance` 等 29 本刊，每周新增 5 篇

数据流：GitHub Actions 每周五 09:00（北京时间）运行 Python 脚本 → 抓取 + AI 阅卷 + 中文解读 → 把本期数据写回 `web/data/*.json` 并提交回仓库 → 发布静态页面到 GitHub Pages。

## 每周更新规则

| 规则 | 取值 | 说明 |
| --- | --- | --- |
| 时间窗口 | `2018-01-01` → 今天 | 由 `PAPER_WINDOW_START` 控制，窗口外的论文直接丢弃 |
| 运行时间 | 每周五 09:00 | `cron: "0 1 * * 5"`（UTC，对应北京时间 9 点），跑在 GitHub 的服务器上 |
| 每期新增 | **合计 10 篇** | `WEEKLY_NEW_TOTAL=10`；两条流水线对半，`RADAR_COLLECTION_COUNT=2` → 各 5 篇 |
| 归档 | 只增不减 | 已收录的论文永远留在数据里，页面上用「本期新增」标签区分本期这 10 篇 |
| 不重复 | 与**全部历史**去重 | 去重键是 DOI + 论文 id + 标题指纹，任一命中就算同一篇 |
| 淘汰 | 无 | 上一版有"常驻 20 篇 + 冷却 56 天"，这一版取消了：论文不再被挤掉，也就不需要冷却 |
| 同一周只跑一期 | 期号幂等 | 数据顶层记 `weekly_period`（如 `2026-W42`）。工作流带 `push` 触发，改代码会重跑，但同一周已选过就不再新增，只重新生成数据；手动补稿传 `weekly_new_count` 可强制加一批 |

**更新的完整逻辑**（回答"是不是每次都把范围里的论文重新打分挑最合适的"）：

1. 抓取：两条流水线各自把 2018 年至今、命中方向关键词的**全部**候选拉回来（顶刊约一千余篇，arXiv 数百篇）；
2. 阅卷：AI 只对**没判过的**候选打分（0-100，缓存 60 天），判定结果落到 `.paper-cache/screen_cache.json`；
3. 去重：把候选里"已经收录过的"整批剔掉（比对全部历史 + `seen` 账本），剩下的叫 fresh；
4. 排序取前 N：fresh 按 AI 分数（同分看出版日期）排序，每条流水线只要前 5 篇；
5. 合并：这 5 篇标 `is_new_this_week`，与历史归档合并，归档部分**原样保留**，不重新排序淘汰；
6. 生成解读：只为还没合格解读的论文调用模型（含本期新 5 篇 + 历史上的遗留），结果按论文 id 缓存。

所以"打分"是每周对**新候选**做的，不是对归档重排；归档里的论文分数只会在它第一次入选时确定。

上一期数据有三级来源，取 `generated_at_iso` 最新的那份：Actions 缓存 → 仓库里提交过的 `web/data/*.json` → 线上站点。这一条是"不重复"的前提，工作流每次跑完都会把数据提交回 `main`，所以缓存被 GitHub 清掉（7 天不用就清）也不影响。

> 定时任务会不会因为仓库长期没动静被 GitHub 停掉？会——公开仓库连续 60 天没有活动，GitHub 会自动禁用 `schedule`。每周的数据提交本身就是仓库活动，顺带把这个坑填了。另外定时任务跑在 GitHub 的服务器上，**和你的电脑是否开机、是否锁屏完全无关**。

## 这套东西怎么工作

| 环节 | 实现 | 说明 |
| --- | --- | --- |
| 预印本抓取 | `scripts/collect_papers.py` | arXiv API。两趟：最近 7 天 + 「2018 至今 + relevance 排序」的窗口召回 |
| 顶刊抓取 | `scripts/collect_journals.py` | OpenAlex API，`title_and_abstract.search` 多关键词 OR 召回 2018 至今的论文，按相关度排序；被限流时回退 Crossref |
| AI 阅卷 | `scripts/llm_screen.py` | 模型逐篇判断相关性 0-100 分并指定方向；批量调用 + 磁盘缓存，异常时退回机械评分 |
| 机械打分 | 两个采集脚本内 | 关键词命中 + 方向描述词重合，作为兜底与并列排序依据 |
| 每周选稿 | `scripts/weekly_select.py` | 时间窗过滤 + 每期 5 篇新（合计 10）+ 历史归档只增不减 + 全量去重 |
| 中文解读 | DeepSeek / OpenAI 兼容接口 | 输出「问题 / 方法 / 创新 / 主要结论」+ 摘要逐句直译 + 原文引用，通过校验才入库 |
| 展示 | `web/index.html` + `app.js` | 纯静态，无需后端；中英文摘要一键切换、2 行折叠、每次渲染 20 张卡片 |

## 目录结构

```
config/interests.json        # 研究方向：关键词 + arXiv 分类
config/journals.json         # 顶刊清单：ISSN + OpenAlex 来源 ID
scripts/collect_papers.py    # arXiv 采集
scripts/collect_journals.py  # 顶刊采集（OpenAlex + Crossref 回退）
scripts/weekly_select.py     # 每周选稿（时间窗/配额/全量去重）
scripts/llm_screen.py        # AI 阅卷：批量判相关性 + 缓存 + 失败降级
scripts/notify.py            # 每周推送：把本期新增推到微信
web/                         # 静态站点
.github/workflows/paper-daily.yml
```

## AI 阅卷：模型怎么参与筛选

默认开启（`SCREEN_MODE=llm`）。完整链路：

```
抓取（2018 至今放宽召回） → 机械打分（只做排序初值） → AI 批量阅卷 → 按 AI 分淘汰 → 与全部历史去重后取前 5 篇 → 合并进归档（旧文不删） → 只给还没解读的论文生成中文解读
```

- **召回放宽**：arXiv 用 `ARXIV_QUERY_MODE=category`，只要落在 `q-fin.ST`、`econ.EM` 这些分类里就进候选池，不再强制关键词命中。这样「研究同一件事但换了说法」的论文不会漏（比如写 `tail dependence` 而关键词是 `tail risk`）。
- **判断标准**：模型逐篇打 0-100 分（85+ 核心相关；60-84 相关；40-59 只是沾边；20-39 只在背景里提了一句；0-19 无关），同时指定最匹配的方向 id 并给 40 字理由。低于 `SCREEN_MIN_SCORE`（默认 45）直接淘汰。
- **成本与速度**：默认每 6 篇打包一次请求，单轮最多 1200 篇，并发 3。首次全量阅卷约 200 次请求、几分钟跑完，费用约 ¥1；之后每周只对**没判过的论文**调用，因为判定结果缓存在 `.paper-cache/screen_cache.json`（跟随 Actions cache 持久化）。中文解读只给「还没有合格解读」的论文生成（本周新 5 篇 + 历史遗留），一篇一条缓存，不会重复花钱。
- **失败降级**：没配 key、请求失败或返回的 JSON 不合法时，这批论文保留机械评分，页面不会变空。想彻底关掉就把 `SCREEN_MODE` 设为 `rules`。
- **可追溯**：每篇论文的 `best_match` 里同时保留 `mechanical_score` 和 `llm_score`，方便你对照着调阈值。

> 没有摘要的论文不会被送进阅卷（模型只能看标题，容易瞎猜），这类保留机械评分。

## 卡片上的内容是怎么写出来的

「问题 / 方法 / 创新 / 主要结论」加上「中文摘要」，由模型一次性生成，**输入只有四项**：

```
标题 + 作者 + arXiv 分类（期刊论文没有分类）+ 摘要原文
```

**没有读全文。** 系统不下载任何 PDF，也不做全文解析。这样做的原因很实际：这些期刊都是付费墙后的正文，没有合法稳定的批量获取途径；而要判断「这篇要不要读」，摘要的信息量已经够用。

提示词是"忠实转述"而不是"读后感"：只允许使用摘要里的内容，摘要有数值才能写数值，没写就只做定性描述。具体要求：

- **问题 / 方法 / 创新 / 主要结论**：摘要的转写与归纳，不是作者的额外表述；
- **主要结论**：摘要有数值就原样照抄，没有就只写方向，不许自己补一个"显著为正"；
- **中文摘要**：**逐句直译**（原文一句就译一句，不合并、不省略、不概括成要点），和右侧「English」是同一段原文的两种语言。

### 怎么保证不是"AI 瞎编"

三件事叠起来做，任何一条不过就不上页面：

1. **只喂原文。** 没有摘要的论文直接不调用模型（只显示"缺摘要，请读原文"），杜绝"看着标题编方法/结论"。
2. **强制引用原文。** 模型必须额外输出 `evidence`——从英文摘要里**逐字摘抄**的一句原话，用来支撑「主要结论」。
3. **机械核对。** 生成后用代码验三样：① `evidence` 必须真的出现在原摘要里（归一化后精确匹配，或与某一句的相似度 ≥ 0.86）；② 结论/方法/创新里的数值（小数、百分数、三位以上整数）必须能在原文找到，凭空冒出的数字直接判不合格；③ 中文摘要长度不得低于原摘要词数的 1.05 倍，短于此说明被压缩成了要点。

不合格就带着"哪里不合格"重试一次；两次仍不合格，这篇就显示占位文案「请阅读原文」，**不会把没通过校验的内容放上去**。通过校验的摘要带 `v=4` 与 `evidence` 写入缓存，页面上展示的每一条都过过这道关。

> 这套机制能挡住"引用不到原文""数字凭空出现""摘要被压缩"，但它是机器校验，不是逻辑证明：模型仍可能在原文范围内做出错误取舍。看到可疑结论，点「原文」核对一句即可。

### 各字段的含义

| 字段 | 内容 | 数据来源 |
| --- | --- | --- |
| 问题 | 论文真正要解决的问题，1–2 句，去掉空泛背景 | 摘要 |
| 方法 | 核心方法 2–3 句，含关键技术组件或数学形式 | 摘要 |
| 创新 | 相对已有工作的具体增量，2–3 点合并成一段 | 摘要 |
| 主要结论 | 作者最终得到的结果或主张，带原文给出的数值、效应方向、成立条件 | 摘要 |
| 中文摘要 | 英文摘要的逐句直译，和「English」标签是同一段的两种语言 | 摘要 |
| 页脚一行 | 为什么匹配你的方向（`llm_reason`，没有时回退关键词理由） | `interests.json` + 摘要 |

相关性判断仍在后台参与打分：模型同时输出 `match_score_adjustment` 和 `match_level`，卡片左上角的分数就是据此调整过的。

### 页面交互

- **中文摘要 / English**：一键切换，只渲染当前语言，不占双倍高度；
- **展开 / 收起**：摘要默认只露 **2 行**，超出才出现「展开」按钮，短摘要不显示多余按钮；
- **本期新增**：本期新排进来的论文带一个绿色标签，另有「本期新增」标签页可以只看这一批；
- **分页**：归档会一直变长，所以每屏只渲染 20 张卡片，底部「加载更多」翻下一屏。

### 数学公式用 LaTeX

提示词要求模型把符号和公式写成 LaTeX（行内 `$...$`，独立公式 `$$...$$`），前端用内置的 KaTeX 渲染，所以看到的是 $\sigma_t^2=\omega+\alpha\varepsilon_{t-1}^2+\beta\sigma_{t-1}^2$ 这样的排版，而不是 `sigma^2 = omega + alpha*e^2` 这类纯文本。

KaTeX 已内置在 `web/vendor/katex/`（只打包 woff2 字体，约 600 KB），不依赖任何 CDN，国内网络也能正常显示。渲染失败时自动回退成原始文本，不会白屏。

### 数字和术语也统一成数学模式

模型只负责写数学符号；数字和英文缩写由 `scripts/latexify.py` 在落盘前统一处理，所以新旧论文的排版是一致的。

| 处理 | 例子 | 说明 |
| --- | --- | --- |
| 数字 | `$0.42$`、`$5\%$`、`$3{,}120$`、`$-3.5\%$` | 数学模式下是正体 + 等宽数字，小数点能在纵向对齐 |
| 英文缩写 | `$\text{HAR}$`、`$\text{GARCH(1,1)}$`、`$\text{DiD}$`、`$\text{mfBm}$` | 用 `\text{}` 保持正体直立，不加会被当成 H·A·R 三个变量相乘 |
| 普通英文词 | `high-frequency`、`Hurst`、`The` | **不包**：数学模型里是斜体，中文句子夹一串斜体反而更乱 |

`compact_for_storage()` 是两条流水线所有论文（新抓的 + 归档里的）写盘前的必经点，处理就放在这里。函数幂等，已经包过的 `$...$` 不会重复处理；遇到未闭合的 `$` 会整段放行，而不是把半句话当公式拆掉。

> 改提示词或改字段后，必须让旧摘要失效：`scripts/llm_screen.py` 里的 `SUMMARY_CACHE_PATH` 就是缓存版本号（当前 `summary_cache.v4.json`），升一个版本即可让新格式重写一遍。`is_real_summary()` 也会校验 `v` 与 `evidence`，旧格式的摘要会被自动当成草稿重新生成。


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
之后默认**每周五北京时间 09:00** 自动更新（`cron: "0 1 * * 5"`，UTC 时间）。

### 5. 改研究方向

两种方式，选一种即可：

- **改文件**（简单直接）：编辑 `config/interests.json` 里的 `topics`，push 后自动触发
- **提 Issue**（不用碰代码）：`Issues → New issue → Research Interests`，改 JSON 里的 `keywords` 和 `arxiv_categories` 后提交

### 6. 每周微信推送（可选）

每周五更新完、页面部署上线之后，会往微信发一条卡片：**本期新增 10 篇的标题列表 + 一个「打开论文雷达」按钮**，
点开直接落在「本期新增」页（`#latest`），带一个「只看顶刊」的副链接（`#journal-latest`）。

微信个人号没有开放接口，所以「文件传输助手」无法由程序投放消息（第三方 hook 个人微信有封号风险，不建议）。
正规做法是借微信服务号的模板消息：程序把内容发给一个推送服务，服务用它的服务号给你发卡片。以下通道任选其一，
**只填你用的那一个 Secret 即可**：

| Secret | 说明 |
| --- | --- |
| `SERVERCHAN_SENDKEY` | [Server酱](https://sct.ftqq.com)，扫码登录拿 SendKey，微信关注「方糖」服务号。免费 5 条/天，每周一条足够 |
| `PUSHPLUS_TOKEN` | [PushPlus](https://www.pushplus.plus)，微信扫码拿 token 并关注它的公众号，需实名认证后才能发 |
| `WXPUSHER_APP_TOKEN` + `WXPUSHER_UIDS` | [WxPusher](https://wxpusher.zjiecode.com)，支持 HTML 卡片且点击直达站点，体验最接近「卡片」 |
| `WECOM_BOT_WEBHOOK` | 企业微信群机器人，消息可同步到微信的「企业微信」入口 |
| `NOTIFY_WEBHOOK_URL` | 任意自定义 webhook，POST 一个 JSON（title/text/markdown/html/url） |

配置完可以本地先看效果，不会真的发出去：

```bash
python scripts/notify.py --dry-run          # 打印本周推送文案
python scripts/notify.py --root . --force   # 真发一次（需先设好上面的环境变量）
```

几条行为约定：

- **只在定时任务里推**（还有手动运行时勾上 `notify`）。push 触发的是代码改动，不会打扰你。
- **同一期只推一次**：推送记录存在 `.paper-cache/notify_state.json`，同一周重跑会自动跳过。
- **推送失败不影响更新**：这一步挂了也只是没收到消息，页面照常部署；所有通道都失败时不写状态，下周会重试。
- 想临时关掉：Variables 里设 `NOTIFY_ENABLED=false`。

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

`per_venue_results` 默认 50：单刊在一轮里最多取多少条「标题/摘要命中方向关键词」的记录。29 本 × 50 ≈ 1400 条候选，够 AI 阅卷挑出最合适的 5 篇。调太大只会让请求变慢，因为每周只新增 5 篇。

`window_search: true` 时走的是「2018 起 + `title_and_abstract.search` 关键词 OR」的召回路子。关掉它就退回旧的「按出版日期倒序取最近 N 条」——那样永远看不到 2018–2024 的论文。

## 常用可调参数

在 `Settings → Secrets and variables → Actions → Variables` 里加，都是可选的：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `PAPER_WINDOW_START` | 2018-01-01 | 论文时间窗口起点，早于它的论文一律丢弃 |
| `WEEKLY_NEW_TOTAL` | 10 | 每期**两条线合计**新增几篇，自动对半（10 → 各 5） |
| `RADAR_COLLECTION_COUNT` | 2 | 流水线条数，用来把总量对半分 |
| `WEEKLY_NEW_PAPERS` | 空 | 单独指定单条线的配额，设了它就不看总量 |
| `MAX_PER_TOPIC` | 25 | 每个方向最多抓多少篇 arXiv 候选 |
| `ARXIV_WINDOW_SEARCH` | true | 是否额外跑一趟「2018 至今 + relevance」的老预印本召回 |
| `ARXIV_WINDOW_RESULTS` | 40 | 每个方向那趟窗口召回取多少条 |
| `MAX_NEW_PAPERS` | 60 | arXiv 侧进入选稿的候选池上限（最终只留 5 篇新的） |
| `MAX_SUMMARIES` | 40 | 每轮最多生成多少篇中文解读（控制成本） |
| `JOURNAL_PER_VENUE_RESULTS` | 50 | 单刊每轮取多少条候选 |
| `MAX_NEW_JOURNAL_PAPERS` | 60 | 顶刊侧进入选稿的候选池上限 |
| `MAX_JOURNAL_SUMMARIES` | 40 | 顶刊每轮总结条数上限 |
| `MAX_JOURNAL_PER_VENUE` | 3 | 单刊最多进候选池几篇，避免高产期刊刷屏 |
| `MIN_PAPER_SCORE` | 0.08 | arXiv 最低机械相关性分（AI 阅卷关闭时生效） |
| `MIN_JOURNAL_SCORE` | 0.18 | 顶刊最低机械相关性分（AI 阅卷关闭时生效） |
| `ARXIV_QUERY_MODE` | category | `category` = 只按分类召回（推荐，配合 AI 阅卷）；`strict` = 关键词 AND 分类；`keyword` = 只按关键词 |
| `SCREEN_MODE` | llm | `llm` 用模型阅卷；`rules` 退回纯机械评分 |
| `SCREEN_MIN_SCORE` | 45 | AI 分低于此值淘汰（0-100）。想多看调到 35，只看硬核调到 60 |
| `SCREEN_BATCH_SIZE` | 6 | 每次请求塞几篇，越大越省钱但越容易输出截断 |
| `SCREEN_MAX_PAPERS` | 1200 | 单轮最多阅卷多少篇，超出部分保留机械评分 |
| `SCREEN_TRUNCATE_BY` | score | 候选超过上限时按什么截断：`score`（宽窗口推荐）或 `recency` |
| `SCREEN_CONCURRENCY` | 3 | 并发请求数，遇到 429 就调小 |
| `SCREEN_CACHE_DAYS` | 60 | AI 判定结果的缓存天数，越大越省钱 |
| `LOOKBACK_DAYS` | 7 | arXiv"最近几天"那一趟的窗口 |
| `NOTIFY_ENABLED` | true | 设为 `false` 关掉每周微信推送 |
| `NOTIFY_CHANNELS` | 空 | 只填了多个推送 key 时，用它限定发哪几个，如 `serverchan` |

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
- **微信推送要依赖第三方服务**：微信不给个人号开放消息接口，所以这条链路是「GitHub Actions → 推送服务 → 微信服务号」。免费额度一般是每天 5 条（每周一条够用），但服务本身不是你能控制的，哪天停了就得换一家——脚本里五个通道可以随时切换。

## 与原项目（Futuresxy/paper-daily）的差异

- 研究方向换成金融计量（5 个方向 + 12 本金融/计量期刊）
- 新增 `scripts/collect_journals.py`：用 OpenAlex 按 ISSN 抓顶刊，Crossref 兜底，替代原来的 DBLP 会议抓取
- 「顶会精品」改成「顶刊精选」，页面标题、示意图标链接（自动指向你自己的仓库）都做了调整
- 相关性分级门槛按金融文本的实际分布重新标定（high ≥ 0.40 / medium ≥ 0.30），且只命中 1 个关键词不判 high
- arXiv 查询默认 `strict`（关键词 AND 分类），减少跨领域噪声

原始项目：<https://github.com/Futuresxy/paper-daily>（仓库里没有 LICENSE 文件，个人自用没问题，二次分发前请先确认授权）
