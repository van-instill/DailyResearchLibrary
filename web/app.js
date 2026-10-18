const THEME_STORAGE_KEY = "paper-daily-theme";
const THEMES = new Set(["dark", "light", "eye"]);

const state = {
  datasets: {
    daily: null,
    conference: null,
  },
  theme: "dark",
  filters: {
    query: "",
    topic: "all",
    level: "all",
    collection: "daily",
    view: "all",
    date: "",
  },
};

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
  template: document.querySelector("#paperTemplate"),
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

function matchesBaseFilters(paper) {
  if (!textIncludes(paper, state.filters.query)) return false;
  if (state.filters.topic !== "all" && paper.best_match?.topic_id !== state.filters.topic) return false;
  if (state.filters.level !== "all" && levelOf(paper) !== state.filters.level) return false;
  return true;
}

function matchesView(paper) {
  if (state.filters.view === "all") return true;
  const date = selectedDate();
  const collectedAt = collectionTime(paper);
  if (state.filters.view === "daily") return dateKey(collectedAt) === state.filters.date;
  if (state.filters.view === "week") return inRange(collectedAt, startOfWeek(date), endOfWeek(date));
  if (state.filters.view === "month") return inRange(collectedAt, startOfMonth(date), endOfMonth(date));
  if (state.filters.view === "highlights") {
    return inRange(collectedAt, startOfWeek(date), endOfWeek(date)) && scoreOf(paper) >= 0.4;
  }
  return true;
}

function filteredPapers() {
  return (activeData().papers || [])
    .filter((paper) => matchesBaseFilters(paper) && matchesView(paper))
    .sort((a, b) => scoreOf(b) - scoreOf(a) || String(b.published || "").localeCompare(String(a.published || "")));
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

function renderPaper(paper) {
  const node = nodes.template.content.firstElementChild.cloneNode(true);
  const best = paper.best_match || {};
  const summary = paper.chinese_summary || {};
  const badge = node.querySelector(".match-badge");
  const level = levelOf(paper);

  badge.textContent = `${level} ${scoreOf(paper).toFixed(2)}`;
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
  setText(node, ".paper-authors", (paper.authors || []).slice(0, 8).join(", "));
  setText(node, ".summary-problem", summary.problem);
  setText(node, ".summary-method", summary.method);
  setText(node, ".summary-innovation", summary.innovation);
  setText(node, ".summary-conclusion", summary.conclusion);
  setText(node, ".match-reason", `${best.topic_name || "未分类"}：${best.llm_reason || best.reason || ""}`);

  const freshBadge = node.querySelector(".fresh-badge");
  if (freshBadge && paper.is_new_this_week) freshBadge.hidden = false;

  hydrateAbstract(node, paper, summary);

  for (const field of node.querySelectorAll(".summary-grid dd, .match-reason, .abstract-text")) {
    renderMath(field);
  }

  const tags = node.querySelector(".paper-tags");
  for (const category of (paper.categories || []).slice(0, 8)) {
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = category;
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

function viewLabels() {
  const date = selectedDate();
  const dayLabel = formatDate(date.toISOString());
  const weekStart = formatDate(startOfWeek(date).toISOString());
  const weekEndDate = endOfWeek(date);
  weekEndDate.setDate(weekEndDate.getDate() - 1);
  const weekEnd = formatDate(weekEndDate.toISOString());
  const monthLabel = `${date.getFullYear()} 年 ${String(date.getMonth() + 1).padStart(2, "0")} 月`;
  return {
    all: [state.filters.collection === "conference" ? "顶刊精选" : "全部论文", "全部已收录论文"],
    daily: ["当日论文", dayLabel],
    week: ["本周论文", `${weekStart} - ${weekEnd}`],
    month: ["月度论文", monthLabel],
    highlights: ["本周精选", `${weekStart} - ${weekEnd}`],
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
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const paper of papers) fragment.appendChild(renderPaper(paper));
  nodes.paperList.appendChild(fragment);
  refreshAllAbstracts();
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
  const papers = activeData().papers || [];
  const date = selectedDate();
  const weekPapers = papers.filter((paper) => inRange(collectionTime(paper), startOfWeek(date), endOfWeek(date)));
  const monthPapers = papers.filter((paper) => inRange(collectionTime(paper), startOfMonth(date), endOfMonth(date)));
  const top = papers.reduce((max, paper) => Math.max(max, scoreOf(paper)), 0);
  nodes.paperCount.textContent = String(papers.length);
  nodes.weekCount.textContent = String(weekPapers.length);
  nodes.monthCount.textContent = String(monthPapers.length);
  nodes.topScore.textContent = top.toFixed(2);
}

function bindEvents() {
  for (const option of nodes.themeOptions) {
    option.addEventListener("click", () => {
      applyTheme(option.dataset.themeOption);
    });
  }
  nodes.searchInput.addEventListener("input", (event) => {
    state.filters.query = event.target.value.trim();
    render();
  });
  nodes.topicFilter.addEventListener("change", (event) => {
    state.filters.topic = event.target.value;
    render();
  });
  nodes.levelFilter.addEventListener("change", (event) => {
    state.filters.level = event.target.value;
    render();
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
      render();
    });
  }
  nodes.dateFilter.addEventListener("change", (event) => {
    state.filters.date = event.target.value;
    updateStats();
    render();
  });
  for (const tab of nodes.tabs) {
    tab.addEventListener("click", () => {
      state.filters.view = tab.dataset.view;
      for (const item of nodes.tabs) item.classList.toggle("active", item === tab);
      render();
    });
  }
  nodes.paperList.addEventListener("click", (event) => {
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
  const weekly = Number(stats.weekly_new_count || 0);
  const fresh = weekly ? ` · 本周新增 ${weekly} 篇` : "";
  nodes.updatedAt.textContent = `${kind} · 更新于 ${formatDate(data.generated_at_iso)}${fresh} · ${mode} · ${stats.llm_enabled ? "LLM" : "基础"}`;
}

function resolveRepoLinks() {
  const match = location.hostname.match(/^([^.]+)\.github\.io$/);
  const settingsLink = document.querySelector(".repo-settings-link");
  const actionsLink = document.querySelector(".repo-actions-link");
  if (!match || !settingsLink || !actionsLink) return;
  const parts = location.pathname.split("/").filter(Boolean);
  const repo = parts[0] || `${match[1]}.github.io`;
  const base = `https://github.com/${match[1]}/${repo}`;
  settingsLink.href = `${base}/issues`;
  actionsLink.href = `${base}/actions`;
}

async function main() {
  resolveRepoLinks();
  applyTheme(storedTheme());
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

  updateUpdatedAt();
  hydrateTopicFilter();
  hydrateDateFilter();
  updateStats();
  render();
}

main();
