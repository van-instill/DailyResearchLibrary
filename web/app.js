const THEME_STORAGE_KEY = "paper-daily-theme";
const THEMES = new Set(["dark", "light", "eye"]);

// 「不感兴趣」的删除记录放在本机：点了立刻从列表消失。真正写进仓库要靠
// 「同步」——生成一个预填好的 GitHub issue，Actions 读它写进 config/exclude.json，
// 以后每周筛选都不会再收这篇。
const DELETED_STORAGE_KEY = "paper-radar-deleted";
const DELETED_ISSUE_TITLE = "Excluded Papers";
// 进阶用：填过一次 fine-grained token（只有 Issues 写权限），之后就能在页面里
// 直接调 GitHub API 建 issue，不用每次跳到网页表单手动点 Create。
// 只存在本机 localStorage，除了 api.github.com 不会发给任何人。
const GH_TOKEN_STORAGE_KEY = "paper-radar-gh-token";

const state = {
  datasets: {
    daily: null,
    conference: null,
  },
  theme: "dark",
  page: 1,
  deleted: new Set(),
  filters: {
    query: "",
    topic: "all",
    level: "all",
    collection: "daily",
    view: "all",
    date: "",
    targetOnly: false,
  },
};

// 归档只增不减，一年下来会有几百篇；一次只渲染 20 张卡片，剩下的按需加载，
// 否则首屏要渲染上千个 DOM 节点 + KaTeX 公式，手机上直接卡死。
const PAGE_SIZE = 20;

const nodes = {
  updatedAt: document.querySelector("#updatedAt"),
  paperCount: document.querySelector("#paperCount"),
  weekCount: document.querySelector("#weekCount"),
  monthCount: document.querySelector("#monthCount"),
  topScore: document.querySelector("#topScore"),
  resultCount: document.querySelector("#resultCount"),
  viewTitle: document.querySelector("#viewTitle"),
  listTitle: document.querySelector("#listTitle"),
  scopeLabel: document.querySelector("#scopeLabel"),
  paperList: document.querySelector("#paperList"),
  topicFilter: document.querySelector("#topicFilter"),
  levelFilter: document.querySelector("#levelFilter"),
  dateFilter: document.querySelector("#dateFilter"),
  searchInput: document.querySelector("#searchInput"),
  themeOptions: document.querySelectorAll("[data-theme-option]"),
  collectionTabs: document.querySelectorAll("[data-collection]"),
  tabs: document.querySelectorAll(".tab"),
  loadMore: document.querySelector("#loadMore"),
  template: document.querySelector("#paperTemplate"),
  targetFilter: document.querySelector("#targetFilter"),
  deletedBtn: document.querySelector("#deletedBtn"),
  deletedCount: document.querySelector("#deletedCount"),
  deletedPanel: document.querySelector("#deletedPanel"),
  deletedClose: document.querySelector("#deletedClose"),
  deletedList: document.querySelector("#deletedList"),
  deletedSync: document.querySelector("#deletedSync"),
  deletedClear: document.querySelector("#deletedClear"),
  syncGuide: document.querySelector("#syncGuide"),
  syncGuideClose: document.querySelector("#syncGuideClose"),
  syncGuideOpen: document.querySelector("#syncGuideOpen"),
  syncGuideDone: document.querySelector("#syncGuideDone"),
  syncNote: document.querySelector("#syncNote"),
  syncTokenInput: document.querySelector("#syncTokenInput"),
  syncTokenSave: document.querySelector("#syncTokenSave"),
  syncTokenNote: document.querySelector("#syncTokenNote"),
  toast: document.querySelector("#toast"),
  toastText: document.querySelector("#toastText"),
  toastAction: document.querySelector("#toastAction"),
};

function activeData() {
  return state.datasets[state.filters.collection] || state.datasets.daily || { papers: [], topics: [], stats: {} };
}

function storedTheme() {
  try {
    const theme = localStorage.getItem(THEME_STORAGE_KEY);
    return THEMES.has(theme) ? theme : "dark";
  } catch {
    return "dark";
  }
}

function applyTheme(theme) {
  state.theme = THEMES.has(theme) ? theme : "dark";
  document.body.dataset.theme = state.theme;
  for (const option of nodes.themeOptions) {
    const active = option.dataset.themeOption === state.theme;
    option.classList.toggle("active", active);
    option.setAttribute("aria-checked", String(active));
  }
  try {
    localStorage.setItem(THEME_STORAGE_KEY, state.theme);
  } catch {
    // localStorage may be blocked in privacy-focused browser modes.
  }
}

function parseDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatDate(value) {
  const date = parseDate(value);
  if (!date) return value ? String(value).slice(0, 10) : "-";
  return date.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });
}

function dateKey(value) {
  const date = parseDate(value);
  if (!date) return "";
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

// 「收录」= 这篇第一次进入列表的时间。周更模式下每一期都会把 last_seen_at
// 刷成运行时间，用它做「本周」筛选会让 20 篇全部落在本周，失去意义。
function collectionTime(paper) {
  return paper.first_seen_at || paper.last_seen_at || paper.published || paper.updated || "";
}

function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function startOfWeek(date) {
  const day = startOfDay(date);
  const offset = (day.getDay() + 6) % 7;
  day.setDate(day.getDate() - offset);
  return day;
}

function endOfWeek(date) {
  const end = startOfWeek(date);
  end.setDate(end.getDate() + 7);
  return end;
}

function startOfMonth(date) {
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

function endOfMonth(date) {
  return new Date(date.getFullYear(), date.getMonth() + 1, 1);
}

function inRange(value, start, end) {
  const date = parseDate(value);
  return Boolean(date && date >= start && date < end);
}

function selectedDate() {
  return parseDate(`${state.filters.date}T12:00:00`) || new Date();
}

function scoreOf(paper) {
  return Number(paper.best_match?.score || 0);
}

function levelOf(paper) {
  return String(paper.best_match?.level || "low").toLowerCase();
}

// 相关度徽标和筛选下拉都说中文，别一半 high 一半 高
const LEVEL_LABELS = { high: "高", medium: "中", low: "低" };

// 只认一作/二作：找导师就是看这两位的单位，三作以后加粗反而干扰阅读
function targetSchoolsOf(paper) {
  return (paper.author_schools || []).filter((item) => Number(item?.i) < 2);
}

function readDeleted() {
  try {
    const raw = JSON.parse(localStorage.getItem(DELETED_STORAGE_KEY) || "[]");
    return Array.isArray(raw) ? raw.filter((item) => item && item.id) : [];
  } catch {
    return [];
  }
}

function writeDeleted(list) {
  try {
    localStorage.setItem(DELETED_STORAGE_KEY, JSON.stringify(list));
  } catch {
    // 隐私模式下 localStorage 可能不可写，忽略即可
  }
  state.deleted = new Set(list.map((item) => String(item.id)));
}

function findPaperById(paperId) {
  if (!paperId) return null;
  for (const key of ["daily", "conference"]) {
    const hit = (state.datasets[key]?.papers || []).find((paper) => String(paper.id) === String(paperId));
    if (hit) return hit;
  }
  return null;
}

// GitHub 仓库地址：线上是 <user>.github.io/<repo>，本地打开时就没有同步能力
function repoBaseUrl() {
  const match = location.hostname.match(/^([^.]+)\.github\.io$/);
  if (!match) return "";
  const parts = location.pathname.split("/").filter(Boolean);
  const repo = parts[0] || `${match[1]}.github.io`;
  return `https://github.com/${match[1]}/${repo}`;
}

let toastTimer = 0;

function hideToast() {
  if (nodes.toast) nodes.toast.hidden = true;
  clearTimeout(toastTimer);
}

function showToast(text, action = null) {
  if (!nodes.toast) return;
  nodes.toastText.textContent = text;
  if (action && nodes.toastAction) {
    nodes.toastAction.hidden = false;
    nodes.toastAction.textContent = action.label;
    nodes.toastAction.onclick = () => {
      action.run();
      hideToast();
    };
  } else if (nodes.toastAction) {
    nodes.toastAction.hidden = true;
    nodes.toastAction.onclick = null;
  }
  nodes.toast.hidden = false;
  clearTimeout(toastTimer);
  // 给够时间点「撤销」，但别一直挡着视线
  toastTimer = setTimeout(hideToast, 9000);
}

function textIncludes(paper, query) {
  if (!query) return true;
  const haystack = [
    paper.title,
    paper.summary,
    (paper.authors || []).join(" "),
    (paper.categories || []).join(" "),
    paper.best_match?.reason,
    paper.chinese_summary?.innovation,
    paper.chinese_summary?.conclusion,
    paper.chinese_summary?.abstract_zh,
  ]
    .join(" ")
    .toLowerCase();
  return haystack.includes(query.toLowerCase());
}

// 已删除的先从集合里拿掉，后面的统计、视图计数、筛选都基于这份数据
function collectionPapers() {
  return (activeData().papers || []).filter((paper) => !state.deleted.has(String(paper.id)));
}

function matchesBaseFilters(paper) {
  if (!textIncludes(paper, state.filters.query)) return false;
  if (state.filters.topic !== "all" && paper.best_match?.topic_id !== state.filters.topic) return false;
  if (state.filters.level !== "all" && levelOf(paper) !== state.filters.level) return false;
  if (state.filters.targetOnly && !targetSchoolsOf(paper).length) return false;
  return true;
}

function matchesView(paper) {
  if (state.filters.view === "all") return true;
  if (state.filters.view === "latest") return Boolean(paper.is_new_this_week);
  const date = selectedDate();
  const collectedAt = collectionTime(paper);
  if (state.filters.view === "month") return inRange(collectedAt, startOfMonth(date), endOfMonth(date));
  if (state.filters.view === "highlights") return scoreOf(paper) >= 0.6;
  return true;
}

// 排序：本期新增排在最前，其次按收录时间倒序，最后才是匹配分。
// 归档模式下"按分数排"会把半年前的高分论文压在首页，看不到新东西。
function sortPapers(list) {
  return list.sort((a, b) => {
    const fresh = Number(Boolean(b.is_new_this_week)) - Number(Boolean(a.is_new_this_week));
    if (fresh) return fresh;
    const byCollected = String(collectionTime(b)).localeCompare(String(collectionTime(a)));
    if (byCollected) return byCollected;
    return scoreOf(b) - scoreOf(a);
  });
}

function filteredPapers() {
  return sortPapers(collectionPapers().filter((paper) => matchesBaseFilters(paper) && matchesView(paper)));
}

function setText(parent, selector, text) {
  parent.querySelector(selector).textContent = text || "暂无";
}

function renderMath(element) {
  if (!element) return;
  const render =
    [window.renderMathInElement, window.renderMathInElement?.default, window.katex?.renderMathInElement]
      .find((fn) => typeof fn === "function") || null;
  if (!render) return;
  try {
    render(element, {
      delimiters: [
        { left: "$$", right: "$$", display: true },
        { left: "\\[", right: "\\]", display: true },
        { left: "$", right: "$", display: false },
        { left: "\\(", right: "\\)", display: false },
      ],
      throwOnError: false,
      strict: false,
    });
  } catch (err) {
    // 渲染失败就保留原始文本，不影响阅读
    console.warn("KaTeX render failed", err);
  }
}

function safeFilename(paper) {
  const title = String(paper.title || paper.id || "paper")
    .replace(/[\\/:*?"<>|]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 120);
  return `${title || "paper"}.pdf`;
}

function detailUrl(paper) {
  const direct = String(paper.paper_url || "").trim();
  if (direct) return direct;
  const doi = String(paper.doi || "").trim();
  if (!doi) return "";
  return /^https?:\/\//i.test(doi) ? doi : `https://doi.org/${doi}`;
}

function hydrateAbstract(node, paper, summary) {
  const panel = node.querySelector(".abstract-panel");
  if (!panel) return;
  const zh = String(summary.abstract_zh || "").trim();
  const en = String(paper.summary || "").trim();
  panel.querySelector(".abstract-zh").textContent = zh;
  panel.querySelector(".abstract-en").textContent = en;

  if (!zh && !en) {
    panel.remove();
    return;
  }
  // 只有一种语言时就不给切换键，少一个能点的按钮
  const tabs = panel.querySelectorAll(".abstract-tab");
  if (!zh) {
    tabs[0]?.remove();
    panel.dataset.lang = "en";
  } else if (!en) {
    tabs[1]?.remove();
    panel.dataset.lang = "zh";
  }
  for (const tab of panel.querySelectorAll(".abstract-tab")) {
    const active = tab.dataset.abstractLang === panel.dataset.lang;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  }
}

// 折叠状态下只露 5 行左右；内容没超出就干脆不显示"展开"
function refreshAbstract(card) {
  const body = card.querySelector(".abstract-body");
  const toggle = card.querySelector(".abstract-toggle");
  if (!body || !toggle) return;
  if (body.classList.contains("is-expanded")) {
    toggle.hidden = false;
    toggle.textContent = "收起";
    toggle.setAttribute("aria-expanded", "true");
    return;
  }
  const clipped = body.scrollHeight - body.clientHeight > 2;
  body.classList.toggle("is-clipped", clipped);
  toggle.hidden = !clipped;
  toggle.textContent = "展开";
  toggle.setAttribute("aria-expanded", "false");
}

function refreshAllAbstracts() {
  for (const card of nodes.paperList.querySelectorAll(".paper-card")) refreshAbstract(card);
}

// 卡片底部的标签：arXiv 的分类码对非本行的人完全不可读（q-fin.MF 是什么？），
// 所以先给一个学科大标签，再给中文的方向名，原始代码只留在 title 里备查。
const ARXIV_ARCHIVE_ZH = {
  "q-fin": "金融学",
  econ: "经济学",
  stat: "统计学",
  cs: "计算机科学",
  math: "数学",
  eess: "电子信息",
  nlin: "非线性科学",
  "q-bio": "生物学",
};

const ARXIV_CATEGORY_ZH = {
  "q-fin.GN": "一般金融",
  "q-fin.MF": "数学金融",
  "q-fin.PM": "投资组合管理",
  "q-fin.PR": "资产定价与对冲",
  "q-fin.RM": "风险管理",
  "q-fin.ST": "统计金融",
  "q-fin.TR": "交易与市场微观结构",
  "q-fin.CP": "计算金融",
  "econ.EM": "计量经济学",
  "econ.GN": "一般经济学",
  "econ.TH": "理论经济学",
  "stat.AP": "应用统计",
  "stat.CO": "计算统计",
  "stat.ME": "统计方法",
  "stat.ML": "统计机器学习",
  "stat.ST": "统计理论",
  "stat.OT": "其他统计",
  "math.ST": "概率统计理论",
  "math.PR": "概率论",
  "math.OC": "优化与控制",
  "math.FA": "泛函分析",
  "math.NA": "数值分析",
  "cs.LG": "机器学习",
  "cs.AI": "人工智能",
  "cs.CL": "自然语言处理",
  "cs.CE": "计算工程",
  "cs.CV": "计算机视觉",
  "cs.IT": "信息论",
  "eess.SP": "信号处理",
  "eess.SY": "系统与控制",
};

function tagParts(paper) {
  const groups = [];
  const details = [];
  const seen = new Set();
  const pushGroup = (name) => {
    if (name && !groups.includes(name)) groups.push(name);
  };
  const pushDetail = (label, code) => {
    if (!label || seen.has(label)) return;
    seen.add(label);
    details.push({ label, code });
  };

  // 顶刊的 venue_group 就是学科，categories[0] 是同一件事，别重复
  if (paper.venue_group) pushGroup(paper.venue_group);
  for (const category of paper.categories || []) {
    const raw = String(category || "").trim();
    if (!raw || raw === paper.venue_group) continue;
    const translated = ARXIV_CATEGORY_ZH[raw];
    if (translated) {
      pushGroup(ARXIV_ARCHIVE_ZH[raw.split(".")[0]]);
      pushDetail(translated, raw);
    } else {
      // 顶刊带出来的是英文主题词，本身就是给人看的，原样保留
      pushDetail(raw, "");
    }
  }
  return { groups: groups.slice(0, 2), details: details.slice(0, 5) };
}

// 作者行要能标出「一作/二作是目标院校的谁」。用 DOM 拼而不是拼 HTML 字符串，
// 作者名里带 < > 也不会破坏结构。
function renderAuthors(node, paper) {
  const container = node.querySelector(".paper-authors");
  if (!container) return;
  container.textContent = "";
  const names = (paper.authors || []).slice(0, 8);
  if (!names.length) {
    container.textContent = "暂无";
    return;
  }
  const byIndex = new Map();
  for (const item of targetSchoolsOf(paper)) byIndex.set(Number(item.i), item);

  names.forEach((name, index) => {
    if (index) container.appendChild(document.createTextNode(", "));
    const hit = byIndex.get(index);
    if (!hit) {
      container.appendChild(document.createTextNode(name));
      return;
    }
    const strong = document.createElement("strong");
    strong.className = "author-highlight";
    strong.textContent = name;
    container.appendChild(strong);

    const tag = document.createElement("span");
    tag.className = "school-tag";
    tag.textContent = hit.school_zh || hit.school || "";
    tag.title = hit.inst ? `${hit.inst}（第${index + 1}作者）` : "";
    container.appendChild(tag);
  });

  if ((paper.authors || []).length > names.length) {
    container.appendChild(document.createTextNode(" 等"));
  }
}

function renderPaper(paper) {
  const node = nodes.template.content.firstElementChild.cloneNode(true);
  const best = paper.best_match || {};
  const summary = paper.chinese_summary || {};
  const badge = node.querySelector(".match-badge");
  const level = levelOf(paper);
  node.dataset.paperId = String(paper.id || "");

  badge.textContent = `${LEVEL_LABELS[level] || level} ${scoreOf(paper).toFixed(2)}`;
  badge.classList.add(level);

  setText(node, ".paper-date", `发布 ${formatDate(paper.published)}`);
  setText(node, ".paper-source", paper.source || "paper");
  setText(node, ".paper-collected", `收录 ${formatDate(collectionTime(paper))}`);

  const titleUrl = detailUrl(paper);
  const titleLink = node.querySelector(".paper-title-link");
  titleLink.textContent = paper.title || "暂无";
  if (titleUrl) {
    titleLink.href = titleUrl;
  } else {
    titleLink.removeAttribute("href");
    titleLink.classList.add("is-static");
  }
  renderAuthors(node, paper);
  setText(node, ".summary-problem", summary.problem);
  setText(node, ".summary-method", summary.method);
  setText(node, ".summary-innovation", summary.innovation);
  setText(node, ".summary-conclusion", summary.conclusion);
  setText(node, ".match-reason", `${best.topic_name || "未分类"}：${best.llm_reason || best.reason || ""}`);

  const freshBadge = node.querySelector(".fresh-badge");
  if (freshBadge && paper.is_new_this_week) freshBadge.hidden = false;

  const targetBadge = node.querySelector(".target-badge");
  const schoolHits = targetSchoolsOf(paper);
  if (targetBadge && schoolHits.length) {
    targetBadge.hidden = false;
    targetBadge.textContent = `${schoolHits[0].school_zh || schoolHits[0].school} · 第${Number(schoolHits[0].i) + 1}作者`;
    targetBadge.title = schoolHits
      .map((item) => `${item.name}：${item.inst || item.school_zh || item.school}（第${Number(item.i) + 1}作者）`)
      .join("\n");
  }

  hydrateAbstract(node, paper, summary);

  for (const field of node.querySelectorAll(".summary-grid dd, .match-reason, .abstract-text")) {
    renderMath(field);
  }

  const tags = node.querySelector(".paper-tags");
  const { groups, details } = tagParts(paper);
  for (const group of groups) {
    const tag = document.createElement("span");
    tag.className = "tag is-group";
    tag.textContent = group;
    tags.appendChild(tag);
  }
  for (const item of details) {
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = item.label;
    if (item.code) tag.title = `arXiv ${item.code}`;
    tags.appendChild(tag);
  }

  const absLink = node.querySelector(".abs-link");
  const pdfLink = node.querySelector(".pdf-link");
  const downloadLink = node.querySelector(".download-link");
  const pdfUrl = paper.pdf_url || paper.paper_url || "#";
  absLink.href = titleUrl || "#";
  pdfLink.href = pdfUrl;
  downloadLink.href = pdfUrl;
  downloadLink.setAttribute("download", safeFilename(paper));
  downloadLink.setAttribute("target", "_blank");
  downloadLink.setAttribute("rel", "noreferrer");
  return node;
}

// 微信推送点进来的是 #latest 这种锚点，落地直接停在「本期新增」，
// 省得用户自己再找一遍。顺手记成 #journal-latest 的形式区分两个集合。
const HASH_VIEWS = new Set(["all", "latest", "month", "highlights"]);
const HASH_COLLECTIONS = { daily: "daily", arxiv: "daily", conference: "conference", journal: "conference" };

function readHash() {
  const raw = location.hash.replace(/^#/, "").toLowerCase();
  if (!raw) return null;
  const parsed = {};
  for (const part of raw.split(/[-_+/]/).filter(Boolean)) {
    if (HASH_VIEWS.has(part)) parsed.view = part;
    else if (HASH_COLLECTIONS[part]) parsed.collection = HASH_COLLECTIONS[part];
  }
  return Object.keys(parsed).length ? parsed : null;
}

function applyHash() {
  const parsed = readHash();
  if (!parsed) return false;
  if (parsed.collection) state.filters.collection = parsed.collection;
  if (parsed.view) state.filters.view = parsed.view;
  for (const item of nodes.collectionTabs) {
    item.classList.toggle("active", item.dataset.collection === state.filters.collection);
  }
  for (const item of nodes.tabs) item.classList.toggle("active", item.dataset.view === state.filters.view);
  return true;
}

function syncHash() {
  const next = state.filters.collection === "conference" ? `#journal-${state.filters.view}` : `#${state.filters.view}`;
  if (location.hash !== next) history.replaceState(null, "", next);
}

function viewLabels() {
  const date = selectedDate();
  const monthLabel = `${date.getFullYear()} 年 ${String(date.getMonth() + 1).padStart(2, "0")} 月`;
  const total = collectionPapers().length;
  const fresh = collectionPapers().filter((paper) => paper.is_new_this_week).length;
  return {
    all: [state.filters.collection === "conference" ? "顶刊精选" : "arXiv 论文", `已收录 ${total} 篇，按收录时间倒序`],
    latest: ["本期新增", `本期新增 ${fresh} 篇，旧论文不删除、留在归档里`],
    month: ["本月新增", monthLabel],
    highlights: ["高分精选", "AI 相关度 ≥ 0.60"],
  };
}

function updateHeadings(papers) {
  const labels = viewLabels()[state.filters.view];
  nodes.viewTitle.textContent = labels[0];
  nodes.listTitle.textContent = labels[0];
  nodes.scopeLabel.textContent = labels[1];
  nodes.resultCount.textContent = `${papers.length} 篇`;
}

function render() {
  const papers = filteredPapers();
  updateHeadings(papers);
  nodes.paperList.textContent = "";

  if (!papers.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "当前筛选条件下没有论文。";
    nodes.paperList.appendChild(empty);
    if (nodes.loadMore) nodes.loadMore.hidden = true;
    return;
  }

  const visible = papers.slice(0, state.page * PAGE_SIZE);
  const fragment = document.createDocumentFragment();
  for (const paper of visible) fragment.appendChild(renderPaper(paper));
  nodes.paperList.appendChild(fragment);
  refreshAllAbstracts();

  if (nodes.loadMore) {
    const rest = papers.length - visible.length;
    nodes.loadMore.hidden = rest <= 0;
    nodes.loadMore.textContent = `加载更多（还有 ${rest} 篇）`;
  }
}

function hydrateTopicFilter() {
  nodes.topicFilter.innerHTML = '<option value="all">全部方向</option>';
  for (const topic of activeData().topics || []) {
    const option = document.createElement("option");
    option.value = topic.id;
    option.textContent = topic.name;
    nodes.topicFilter.appendChild(option);
  }
}

function hydrateDateFilter() {
  const data = activeData();
  const dates = [...new Set((data.papers || []).map((paper) => dateKey(collectionTime(paper))).filter(Boolean))].sort().reverse();
  const fallback = dateKey(data.generated_at_iso || new Date().toISOString());
  const options = dates.length ? dates : [fallback];
  state.filters.date = options[0];
  nodes.dateFilter.textContent = "";
  for (const key of options) {
    const option = document.createElement("option");
    option.value = key;
    option.textContent = formatDate(`${key}T12:00:00`);
    nodes.dateFilter.appendChild(option);
  }
}

function updateStats() {
  const papers = collectionPapers();
  const date = selectedDate();
  const fresh = papers.filter((paper) => paper.is_new_this_week).length;
  const monthPapers = papers.filter((paper) => inRange(collectionTime(paper), startOfMonth(date), endOfMonth(date)));
  const top = papers.reduce((max, paper) => Math.max(max, scoreOf(paper)), 0);
  nodes.paperCount.textContent = String(papers.length);
  nodes.weekCount.textContent = String(fresh);
  nodes.monthCount.textContent = String(monthPapers.length);
  nodes.topScore.textContent = top.toFixed(2);
}

// 任何一个筛选条件变了都要回到第一页，否则会停在"第 5 页"上看着空列表
function resetAndRender() {
  state.page = 1;
  render();
}

function updateDeletedButton() {
  const count = readDeleted().length;
  if (nodes.deletedCount) nodes.deletedCount.textContent = String(count);
  if (nodes.deletedBtn) nodes.deletedBtn.hidden = count === 0;
}

function deletePaper(paper) {
  const id = String(paper.id || "");
  if (!id) return;
  const list = readDeleted().filter((item) => String(item.id) !== id);
  list.push({
    id,
    title: paper.title || "",
    venue: paper.venue || paper.source || "",
    at: new Date().toISOString(),
  });
  writeDeleted(list);
  updateDeletedButton();
  // 删掉当前页最后一篇后不要停在空白页
  if ((state.page - 1) * PAGE_SIZE >= collectionPapers().length) state.page = 1;
  render();
  updateStats();
  const short = String(paper.title || "").slice(0, 42);
  showToast(`已删除：${short}${String(paper.title || "").length > 42 ? "…" : ""}`, {
    label: "撤销",
    run: () => restorePaper(id),
  });
}

function restorePaper(id) {
  const key = String(id);
  const list = readDeleted().filter((item) => String(item.id) !== key);
  writeDeleted(list);
  updateDeletedButton();
  renderDeletedPanel();
  render();
  updateStats();
  showToast("已恢复");
}

// 数据里已经没有这条了（说明排除清单生效），本地记录就没必要留着
function pruneDeletedEntries() {
  const known = new Set();
  for (const key of ["daily", "conference"]) {
    for (const paper of state.datasets[key]?.papers || []) known.add(String(paper.id));
  }
  if (!known.size) return;
  const list = readDeleted();
  const kept = list.filter((item) => known.has(String(item.id)));
  if (kept.length !== list.length) writeDeleted(kept);
}

function renderDeletedPanel() {
  if (!nodes.deletedList) return;
  const list = readDeleted().slice().reverse();
  nodes.deletedList.textContent = "";
  if (!list.length) {
    const empty = document.createElement("li");
    empty.className = "deleted-item";
    empty.textContent = "还没有删除任何论文。";
    nodes.deletedList.appendChild(empty);
  }
  for (const item of list) {
    const row = document.createElement("li");
    row.className = "deleted-item";

    const info = document.createElement("div");
    const title = document.createElement("p");
    title.textContent = item.title || item.id;
    const meta = document.createElement("small");
    const when = item.at ? new Date(item.at).toLocaleDateString("zh-CN") : "";
    meta.textContent = [item.venue, when && `删除于 ${when}`, item.synced ? "已提交同步" : ""]
      .filter(Boolean)
      .join(" · ");
    if (item.synced) meta.classList.add("is-synced");
    info.append(title, meta);

    const restore = document.createElement("button");
    restore.type = "button";
    restore.className = "paper-link";
    restore.textContent = "恢复";
    restore.addEventListener("click", () => restorePaper(item.id));

    row.append(info, restore);
    nodes.deletedList.appendChild(row);
  }
  if (nodes.deletedSync) {
    nodes.deletedSync.disabled = !list.length;
    nodes.deletedSync.textContent = list.length ? `同步到仓库（${list.length} 篇）` : "同步到仓库";
  }
}

function openDeletedPanel() {
  renderDeletedPanel();
  if (nodes.deletedPanel) nodes.deletedPanel.hidden = false;
}

function closeDeletedPanel() {
  if (nodes.deletedPanel) nodes.deletedPanel.hidden = true;
}

// 仓库里那条「Excluded Papers」issue 就是传话筒：Actions 读它的正文，把 id/标题
// 合并进 config/exclude.json，下次选稿就不会再收。
function excludedPayload() {
  const list = readDeleted();
  return {
    papers: list.map((item) => ({
      id: String(item.id),
      title: item.title || "",
      reason: `用户在前端手动删除（${item.at ? new Date(item.at).toLocaleDateString("zh-CN") : "近期"}）`,
    })),
  };
}

function excludedIssueBody() {
  return [
    "<!-- 机器人会解析下面这段 JSON 并写入 config/exclude.json，写完自动关闭本 issue。 -->",
    "",
    "```json",
    JSON.stringify(excludedPayload(), null, 2),
    "```",
  ].join("\n");
}

function excludedIssueUrl() {
  const base = repoBaseUrl();
  if (!base) return "";
  const query = [
    `title=${encodeURIComponent(DELETED_ISSUE_TITLE)}`,
    `body=${encodeURIComponent(excludedIssueBody())}`,
  ].join("&");
  return `${base}/issues/new?${query}`;
}

function readToken() {
  try {
    return localStorage.getItem(GH_TOKEN_STORAGE_KEY) || "";
  } catch {
    return "";
  }
}

function writeToken(token) {
  try {
    if (token) localStorage.setItem(GH_TOKEN_STORAGE_KEY, token);
    else localStorage.removeItem(GH_TOKEN_STORAGE_KEY);
  } catch {
    // 隐私模式写不进去，忽略
  }
}

function setSyncNote(target, message, tone = "") {
  if (!target) return;
  target.hidden = !message;
  target.textContent = message || "";
  target.dataset.tone = message ? tone : "";
}

function openSyncGuide(note = "") {
  if (nodes.syncNote) setSyncNote(nodes.syncNote, note);
  const has = readDeleted().length > 0;
  if (nodes.syncGuideOpen) nodes.syncGuideOpen.disabled = !has;
  if (nodes.syncGuideDone) nodes.syncGuideDone.disabled = !has;
  if (nodes.syncGuide) nodes.syncGuide.hidden = false;
  if (nodes.syncTokenInput && !nodes.syncTokenInput.value) nodes.syncTokenInput.value = readToken();
}

function closeSyncGuide() {
  if (nodes.syncGuide) nodes.syncGuide.hidden = true;
}

// 标记为「已提交」：本机记住这批已经同步过，面板里会显示出来，
// 等下一次流水线跑完、这几篇从数据里消失后，记录会被自动清掉。
function markSynced() {
  const at = new Date().toISOString();
  writeDeleted(readDeleted().map((item) => ({ ...item, synced: true, syncedAt: at })));
  renderDeletedPanel();
}

// 「同步到仓库」入口：没配令牌就走引导（三步，手动点 Create）；
// 配过令牌就直接调 API，页面上一步到位。
function syncDeletedToRepo() {
  const list = readDeleted();
  if (!list.length) return;
  if (!repoBaseUrl()) {
    showToast("本地打开时无法同步，请到线上页面操作");
    return;
  }
  if (readToken()) {
    syncViaToken();
    return;
  }
  openSyncGuide();
}

async function syncViaToken() {
  const list = readDeleted();
  if (!list.length) return;
  const token = readToken();
  const base = repoBaseUrl();
  if (!base) {
    showToast("本地打开时无法同步，请到线上页面操作");
    return;
  }
  const repo = base.replace(/^https:\/\/github\.com\//, "");
  setSyncNote(nodes.syncNote, "正在提交到 GitHub…");
  if (nodes.syncGuideDone) nodes.syncGuideDone.disabled = true;
  if (nodes.syncTokenSave) nodes.syncTokenSave.disabled = true;
  try {
    const response = await fetch(`https://api.github.com/repos/${repo}/issues`, {
      method: "POST",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      body: JSON.stringify({ title: DELETED_ISSUE_TITLE, body: excludedIssueBody() }),
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.message || `HTTP ${response.status}`);
    }
    markSynced();
    closeSyncGuide();
    closeDeletedPanel();
    showToast(`已同步 ${list.length} 篇，约 1 分钟后这个页面会自动更新`);
  } catch (error) {
    const raw = String(error?.message || error);
    const hint = /bad credentials|401|resource not accessible/i.test(raw)
      ? `令牌没有权限或已过期（${raw}），重新生成一个再试`
      : /Failed to fetch|NetworkError/i.test(raw)
        ? "网络请求被拦住了，换用下面的「打开 GitHub 提交页」手动提交"
        : raw;
    setSyncNote(nodes.syncNote, `一键同步失败：${hint}`, "error");
    showToast("一键同步失败，弹窗里有原因");
  } finally {
    if (nodes.syncGuideDone) nodes.syncGuideDone.disabled = false;
    if (nodes.syncTokenSave) nodes.syncTokenSave.disabled = false;
  }
}

function bindEvents() {
  for (const option of nodes.themeOptions) {
    option.addEventListener("click", () => {
      applyTheme(option.dataset.themeOption);
    });
  }
  nodes.searchInput.addEventListener("input", (event) => {
    state.filters.query = event.target.value.trim();
    resetAndRender();
  });
  nodes.topicFilter.addEventListener("change", (event) => {
    state.filters.topic = event.target.value;
    resetAndRender();
  });
  nodes.levelFilter.addEventListener("change", (event) => {
    state.filters.level = event.target.value;
    resetAndRender();
  });
  for (const tab of nodes.collectionTabs) {
    tab.addEventListener("click", () => {
      state.filters.collection = tab.dataset.collection;
      state.filters.view = "all";
      state.filters.topic = "all";
      for (const item of nodes.collectionTabs) item.classList.toggle("active", item === tab);
      for (const item of nodes.tabs) item.classList.toggle("active", item.dataset.view === state.filters.view);
      hydrateTopicFilter();
      hydrateDateFilter();
      updateStats();
      updateUpdatedAt();
      resetAndRender();
      syncHash();
    });
  }
  nodes.dateFilter.addEventListener("change", (event) => {
    state.filters.date = event.target.value;
    updateStats();
    resetAndRender();
  });
  for (const tab of nodes.tabs) {
    tab.addEventListener("click", () => {
      state.filters.view = tab.dataset.view;
      for (const item of nodes.tabs) item.classList.toggle("active", item === tab);
      resetAndRender();
      syncHash();
    });
  }
  window.addEventListener("hashchange", () => {
    if (!applyHash()) return;
    hydrateTopicFilter();
    hydrateDateFilter();
    updateStats();
    updateUpdatedAt();
    resetAndRender();
  });
  nodes.loadMore?.addEventListener("click", () => {
    state.page += 1;
    render();
  });
  nodes.paperList.addEventListener("click", (event) => {
    const deleteButton = event.target.closest(".delete-link");
    if (deleteButton) {
      const card = deleteButton.closest(".paper-card");
      const paper = findPaperById(card?.dataset.paperId);
      if (paper) deletePaper(paper);
      return;
    }
    const tab = event.target.closest(".abstract-tab");
    if (tab) {
      const panel = tab.closest(".abstract-panel");
      panel.dataset.lang = tab.dataset.abstractLang;
      for (const item of panel.querySelectorAll(".abstract-tab")) {
        const active = item === tab;
        item.classList.toggle("active", active);
        item.setAttribute("aria-selected", String(active));
      }
      refreshAbstract(tab.closest(".paper-card"));
      return;
    }
    const toggle = event.target.closest(".abstract-toggle");
    if (toggle) {
      const card = toggle.closest(".paper-card");
      const body = card.querySelector(".abstract-body");
      body.classList.toggle("is-expanded");
      refreshAbstract(card);
    }
  });
  let resizeTimer = 0;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(refreshAllAbstracts, 150);
  });

  nodes.targetFilter?.addEventListener("click", () => {
    state.filters.targetOnly = !state.filters.targetOnly;
    nodes.targetFilter.setAttribute("aria-pressed", String(state.filters.targetOnly));
    resetAndRender();
  });

  nodes.deletedBtn?.addEventListener("click", openDeletedPanel);
  nodes.deletedClose?.addEventListener("click", closeDeletedPanel);
  nodes.deletedPanel?.addEventListener("click", (event) => {
    // 点遮罩关掉，点卡片内部不关
    if (event.target === nodes.deletedPanel) closeDeletedPanel();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (nodes.syncGuide && !nodes.syncGuide.hidden) {
      closeSyncGuide();
      return;
    }
    closeDeletedPanel();
  });
  nodes.deletedSync?.addEventListener("click", syncDeletedToRepo);
  nodes.syncGuideClose?.addEventListener("click", closeSyncGuide);
  nodes.syncGuide?.addEventListener("click", (event) => {
    if (event.target === nodes.syncGuide) closeSyncGuide();
  });
  nodes.syncGuideOpen?.addEventListener("click", () => {
    const url = excludedIssueUrl();
    if (!url) {
      showToast("本地打开时无法同步，请到线上页面操作");
      return;
    }
    const opened = window.open(url, "_blank", "noopener");
    if (opened) {
      setSyncNote(nodes.syncNote, "已在新标签打开。点页面底部绿色的 Create，再回来点「我已提交」。", "ok");
    } else {
      setSyncNote(nodes.syncNote, "浏览器把新标签拦了。允许本站弹出窗口后再点一次。", "error");
    }
  });
  nodes.syncGuideDone?.addEventListener("click", () => {
    const count = readDeleted().length;
    if (!count) return;
    markSynced();
    closeSyncGuide();
    showToast(`已记录提交（${count} 篇），约 1 分钟后生效`);
  });
  nodes.syncTokenSave?.addEventListener("click", () => {
    const token = String(nodes.syncTokenInput?.value || "").trim();
    if (!token) {
      setSyncNote(nodes.syncTokenNote, "先把令牌粘贴进来。", "error");
      return;
    }
    writeToken(token);
    setSyncNote(nodes.syncTokenNote, "");
    syncViaToken();
  });
  nodes.deletedClear?.addEventListener("click", () => {
    const count = readDeleted().length;
    if (!count) return;
    writeDeleted([]);
    updateDeletedButton();
    renderDeletedPanel();
    render();
    updateStats();
    showToast(`已恢复全部 ${count} 篇`);
  });
}

async function loadData() {
  const response = await fetch("./data/papers.json", { cache: "no-store" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

async function loadOptionalData(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) return { generated_at_iso: new Date().toISOString(), topics: [], papers: [], stats: {} };
  return response.json();
}

function updateUpdatedAt(message = "") {
  if (message) {
    nodes.updatedAt.textContent = message;
    return;
  }
  const data = activeData();
  const stats = data.stats || {};
  const mode = stats.collection_mode === "incremental" ? "增量" : "初始化";
  const kind = state.filters.collection === "conference" ? "顶刊精选" : "arXiv 新论文";
  // 用论文上的标记数，不用 stats.weekly_new_count：同一周重跑时那次运行没选新论文，
  // stats 会记成 0，但本期的 5 篇标签还在，两边就对不上了。
  const weekly = collectionPapers().filter((paper) => paper.is_new_this_week).length;
  const total = Number(stats.weekly_new_total || 0);
  const fresh = weekly ? ` · 本期新增 ${weekly} 篇${total ? `（两条线合计 ${total} 篇）` : ""}` : "";
  nodes.updatedAt.textContent = `${kind} · 更新于 ${formatDate(data.generated_at_iso)}${fresh} · ${mode} · ${stats.llm_enabled ? "LLM" : "基础"}`;
}

function resolveRepoLinks() {
  const base = repoBaseUrl();
  const settingsLink = document.querySelector(".repo-settings-link");
  const actionsLink = document.querySelector(".repo-actions-link");
  if (!base || !settingsLink || !actionsLink) return;
  settingsLink.href = `${base}/issues`;
  actionsLink.href = `${base}/actions`;
}

async function main() {
  resolveRepoLinks();
  applyTheme(storedTheme());
  writeDeleted(readDeleted());
  updateDeletedButton();
  bindEvents();
  try {
    state.datasets.daily = await loadData();
    state.datasets.conference = await loadOptionalData("./data/conference_papers.json");
  } catch (error) {
    state.datasets.daily = {
      generated_at_iso: new Date().toISOString(),
      topics: [],
      papers: [],
      stats: { llm_enabled: false },
    };
    state.datasets.conference = {
      generated_at_iso: new Date().toISOString(),
      topics: [],
      papers: [],
      stats: { llm_enabled: false },
    };
    updateUpdatedAt(`数据读取失败：${error.message}`);
  }

  applyHash();
  pruneDeletedEntries();
  updateDeletedButton();
  updateUpdatedAt();
  hydrateTopicFilter();
  hydrateDateFilter();
  updateStats();
  render();
  syncHash();
}

main();
