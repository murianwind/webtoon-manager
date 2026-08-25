const STATUS_LABEL = {
  active: "구독중",
  unsubscribed: "구독해제",
  excluded: "목록제외",
};

const DAY_LABEL = { mon: "월", tue: "화", wed: "수", thu: "목", fri: "금", sat: "토", sun: "일" };
const SCHEDULE_JOB_IDS = ["discovery_job", "download_job"];

async function apiCall(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = body.detail;
    const message = Array.isArray(detail)
      ? detail.map((d) => d.msg).join(", ")
      : detail || `요청 실패 (${res.status})`;
    throw new Error(message);
  }
  return res.json();
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

function makeButton(label, onClick) {
  const btn = document.createElement("button");
  btn.textContent = label;
  btn.addEventListener("click", onClick);
  return btn;
}

function naverUrl(titleId) {
  return `https://comic.naver.com/webtoon/list?titleId=${titleId}`;
}

function badgesHtml(w) {
  const parts = [];
  if (w.is_finished) parts.push('<span class="badge finished">완결</span>');
  if (w.is_paused) parts.push('<span class="badge paused">휴재</span>');
  if (w.has_new_episode) parts.push('<span class="badge new-episode">UP</span>');
  return parts.join("");
}

// ── 탭 전환 ─────────────────────────────────────────────

const pageLoaders = {
  "naver-list": loadNaverList,
  unsubscribed: () => loadSubscriptionTab("unsubscribed"),
  excluded: () => loadSubscriptionTab("excluded"),
  "manual-download": () => {},
  registry: loadRegistryPage,
  settings: loadSettingsPage,
};

document.querySelectorAll(".main-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".main-tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".page").forEach((p) => p.classList.add("hidden"));
    tab.classList.add("active");
    const page = tab.dataset.page;
    document.getElementById(`page-${page}`).classList.remove("hidden");
    stopJobPolling();
    pageLoaders[page]?.();
  });
});

// ── 공용 카드 빌더 ───────────────────────────────────────

function buildWebtoonCard(w, context) {
  const card = document.createElement("div");
  card.className = "webtoon-card";
  card.dataset.titleId = w.title_id;

  const metaParts = [];
  if (context !== "naver-list" && w.last_downloaded_no > 0) metaParts.push(`${w.last_downloaded_no}화까지 다운로드`);
  if (w.author_summary) metaParts.push(w.author_summary);
  if (w.is_adult) metaParts.push("🔞");
  const statusBadge =
    context === "naver-list" && w.status ? `<span class="badge ${w.status}">${STATUS_LABEL[w.status] || w.status}</span>` : "";

  card.innerHTML = `
    ${w.thumbnail_url ? `<img src="${escapeHtml(w.thumbnail_url)}" alt="" loading="lazy" />` : '<div class="thumb-placeholder"></div>'}
    <div class="webtoon-card-body">
      <div class="webtoon-card-title"><a href="${naverUrl(w.title_id)}" target="_blank" rel="noopener">${escapeHtml(w.title)}</a></div>
      <div class="webtoon-card-meta">${escapeHtml(metaParts.join(" · "))}</div>
      <div class="webtoon-card-badges">${badgesHtml(w)}${statusBadge}</div>
    </div>
    <div class="webtoon-card-actions"></div>
  `;

  const actions = card.querySelector(".webtoon-card-actions");
  if (context === "naver-list") {
    if (w.status === "active") {
      actions.appendChild(makeButton("구독해제", () => naverListUnsubscribe(w)));
    } else {
      actions.appendChild(makeButton("구독", () => naverListAction(w, "subscribe")));
      actions.appendChild(makeButton("목록제외", () => naverListAction(w, "exclude")));
    }
  } else {
    actions.appendChild(makeButton("구독", () => subscriptionAction(w.title_id, "subscribe", context)));
  }

  return card;
}

// ── 네이버 웹툰 전체목록 ─────────────────────────────────

let naverListCache = [];
const NAVER_LIST_PREFS_KEY = "naverListPrefs";

function saveNaverListPrefs() {
  const prefs = {
    filterStatus: document.getElementById("naver-list-filter-status").value,
    sort: document.getElementById("naver-list-sort").value,
  };
  localStorage.setItem(NAVER_LIST_PREFS_KEY, JSON.stringify(prefs));
}

function restoreNaverListPrefs() {
  try {
    const prefs = JSON.parse(localStorage.getItem(NAVER_LIST_PREFS_KEY) || "{}");
    if (prefs.filterStatus) document.getElementById("naver-list-filter-status").value = prefs.filterStatus;
    if (prefs.sort) document.getElementById("naver-list-sort").value = prefs.sort;
  } catch (e) {
    // 저장된 값이 이상하면 그냥 기본값 사용
  }
}

async function loadNaverList() {
  const grid = document.getElementById("naver-list-grid");
  const emptyMsg = document.getElementById("naver-list-empty");
  if (grid.children.length === 0) {
    grid.innerHTML = "<p>불러오는 중...</p>";
  }
  try {
    naverListCache = await apiCall("/api/naver-list");
    renderNaverList();
  } catch (e) {
    if (grid.children.length === 0) {
      emptyMsg.textContent = `목록을 불러오지 못했습니다: ${e.message}`;
      emptyMsg.classList.remove("hidden");
    }
  }
}

function renderNaverList() {
  const grid = document.getElementById("naver-list-grid");
  const emptyMsg = document.getElementById("naver-list-empty");
  const query = document.getElementById("naver-list-search").value.trim().toLowerCase();
  const filterStatus = document.getElementById("naver-list-filter-status").value;
  const sortBy = document.getElementById("naver-list-sort").value;

  let rows = naverListCache.filter((w) => w.status !== "excluded");

  if (filterStatus === "active") rows = rows.filter((w) => w.status === "active");
  if (filterStatus === "not-active") rows = rows.filter((w) => w.status !== "active");

  if (query) {
    rows = rows.filter(
      (w) => w.title.toLowerCase().includes(query) || (w.author_summary || "").toLowerCase().includes(query)
    );
  }

  rows = [...rows];
  if (sortBy === "author") {
    rows.sort((a, b) => (a.author_summary || "").localeCompare(b.author_summary || "") || a.title.localeCompare(b.title));
  } else if (sortBy === "new-episode") {
    rows.sort((a, b) => (b.has_new_episode === true) - (a.has_new_episode === true) || a.title.localeCompare(b.title));
  } else {
    rows.sort((a, b) => a.title.localeCompare(b.title));
  }

  grid.innerHTML = "";
  emptyMsg.classList.toggle("hidden", rows.length > 0);
  for (const w of rows) {
    grid.appendChild(buildWebtoonCard(w, "naver-list"));
  }
}

async function naverListAction(webtoon, action) {
  const { title_id: titleId, title, thumbnail_url: thumbnailUrl } = webtoon;
  try {
    const updated = await apiCall(`/api/naver-list/${titleId}/${action}`, {
      method: "POST",
      body: JSON.stringify({ title, thumbnail_url: thumbnailUrl || "" }),
    });
    patchNaverListCard(titleId, webtoon, updated.status);
  } catch (e) {
    alert(e.message);
  }
}

async function naverListUnsubscribe(webtoon) {
  try {
    const updated = await apiCall(`/api/webtoons/${webtoon.title_id}/unsubscribe`, { method: "POST" });
    patchNaverListCard(webtoon.title_id, webtoon, updated.status);
  } catch (e) {
    alert(e.message);
  }
}

function patchNaverListCard(titleId, webtoon, newStatus) {
  const cacheIndex = naverListCache.findIndex((w) => w.title_id === titleId);
  if (cacheIndex >= 0) naverListCache[cacheIndex] = { ...naverListCache[cacheIndex], status: newStatus };

  const card = document.querySelector(`#naver-list-grid .webtoon-card[data-title-id="${titleId}"]`);
  if (newStatus === "excluded") {
    card?.remove();
    const grid = document.getElementById("naver-list-grid");
    document.getElementById("naver-list-empty").classList.toggle("hidden", grid.children.length > 0);
  } else if (card) {
    card.replaceWith(buildWebtoonCard({ ...webtoon, status: newStatus }, "naver-list"));
  }
}

document.getElementById("btn-refresh-naver-list").addEventListener("click", loadNaverList);
document.getElementById("naver-list-search").addEventListener("input", renderNaverList);
document.getElementById("naver-list-filter-status").addEventListener("change", () => {
  saveNaverListPrefs();
  renderNaverList();
});
document.getElementById("naver-list-sort").addEventListener("change", () => {
  saveNaverListPrefs();
  renderNaverList();
});

// ── 구독해제 / 제외됨 ────────────────────────────────────

let subscriptionCache = { unsubscribed: [], excluded: [] };
let authorNameMap = {}; // author_id -> author_name (작가 필터 드롭다운용)

async function loadAuthorNameMap() {
  try {
    const authors = await apiCall("/api/watched-authors");
    authorNameMap = Object.fromEntries(authors.map((a) => [a.author_id, a.author_name || a.author_id]));
  } catch (e) {
    authorNameMap = {};
  }
}

async function loadSubscriptionTab(status) {
  const listEl = document.getElementById(`${status}-list`);
  const emptyEl = document.getElementById(`${status}-empty`);
  if (listEl.children.length === 0) {
    listEl.innerHTML = "<p>불러오는 중...</p>";
  }
  try {
    await loadAuthorNameMap();
    subscriptionCache[status] = await apiCall(`/api/webtoons?status=${status}`);
    populateFilterOptions(status);
    renderSubscriptionTab(status);
  } catch (e) {
    if (listEl.children.length === 0) {
      emptyEl.textContent = `불러오지 못했습니다: ${e.message}`;
      emptyEl.classList.remove("hidden");
    }
  }
}

function populateFilterOptions(status) {
  const rows = subscriptionCache[status] || [];

  const authorIds = new Set();
  const tagNames = new Set();
  for (const w of rows) {
    (w.writer_ids || []).forEach((id) => authorIds.add(id));
    (w.tags || []).forEach((t) => tagNames.add(t));
  }

  const authorSelect = document.getElementById(`${status}-author-filter`);
  const currentAuthor = authorSelect.value;
  authorSelect.innerHTML = '<option value="">작가 전체</option>';
  for (const id of authorIds) {
    const opt = document.createElement("option");
    opt.value = id;
    opt.textContent = authorNameMap[id] || id;
    authorSelect.appendChild(opt);
  }
  authorSelect.value = currentAuthor;

  const tagSelect = document.getElementById(`${status}-tag-filter`);
  const currentTag = tagSelect.value;
  tagSelect.innerHTML = '<option value="">태그 전체</option>';
  for (const tag of [...tagNames].sort()) {
    const opt = document.createElement("option");
    opt.value = tag;
    opt.textContent = tag;
    tagSelect.appendChild(opt);
  }
  tagSelect.value = currentTag;
}

function renderSubscriptionTab(status) {
  const listEl = document.getElementById(`${status}-list`);
  const emptyEl = document.getElementById(`${status}-empty`);
  const query = document.getElementById(`${status}-search`).value.trim().toLowerCase();
  const authorFilter = document.getElementById(`${status}-author-filter`).value;
  const tagFilter = document.getElementById(`${status}-tag-filter`).value;

  let rows = subscriptionCache[status] || [];
  if (query) rows = rows.filter((w) => w.title.toLowerCase().includes(query));
  if (authorFilter) rows = rows.filter((w) => (w.writer_ids || []).includes(authorFilter));
  if (tagFilter) rows = rows.filter((w) => (w.tags || []).includes(tagFilter));

  rows = [...rows].sort((a, b) => a.title.localeCompare(b.title));

  listEl.innerHTML = "";
  emptyEl.classList.toggle("hidden", rows.length > 0);
  for (const w of rows) {
    listEl.appendChild(buildWebtoonCard(w, status));
  }
}

for (const status of ["unsubscribed", "excluded"]) {
  document.getElementById(`${status}-search`).addEventListener("input", () => renderSubscriptionTab(status));
  document.getElementById(`${status}-author-filter`).addEventListener("change", () => renderSubscriptionTab(status));
  document.getElementById(`${status}-tag-filter`).addEventListener("change", () => renderSubscriptionTab(status));
}

async function subscriptionAction(titleId, action, currentTab) {
  try {
    await apiCall(`/api/webtoons/${titleId}/${action}`, { method: "POST" });
    const listEl = document.getElementById(`${currentTab}-list`);
    const card = listEl.querySelector(`.webtoon-card[data-title-id="${titleId}"]`);
    card?.remove();
    document.getElementById(`${currentTab}-empty`).classList.toggle("hidden", listEl.children.length > 0);
    subscriptionCache[currentTab] = (subscriptionCache[currentTab] || []).filter((w) => w.title_id !== titleId);
  } catch (e) {
    alert(e.message);
  }
}

// ── 수동 다운로드 ────────────────────────────────────────

let manualAnalyzeResult = null;
let manualPollTimer = null;

document.getElementById("btn-manual-analyze").addEventListener("click", async () => {
  const query = document.getElementById("manual-query").value.trim();
  if (!query) return;

  const resultsEl = document.getElementById("manual-search-results");

  if (/^\d+$/.test(query)) {
    // 숫자만 입력 -> titleId로 바로 분석
    resultsEl.classList.add("hidden");
    await runManualAnalyze(query);
    return;
  }

  // 텍스트 입력 -> 제목 검색 후보 표시
  try {
    const matches = await apiCall(`/api/manual-download/search?query=${encodeURIComponent(query)}`);
    resultsEl.innerHTML = "";
    if (matches.length === 0) {
      resultsEl.innerHTML = "<p>일치하는 웹툰이 없습니다.</p>";
      resultsEl.classList.remove("hidden");
      return;
    }
    for (const m of matches) {
      const card = document.createElement("div");
      card.className = "webtoon-card";
      card.innerHTML = `
        ${m.thumbnail_url ? `<img src="${escapeHtml(m.thumbnail_url)}" alt="" loading="lazy" />` : '<div class="thumb-placeholder"></div>'}
        <div class="webtoon-card-body"><div class="webtoon-card-title">${escapeHtml(m.title)}</div></div>
        <div class="webtoon-card-actions"></div>
      `;
      card.querySelector(".webtoon-card-actions").appendChild(
        makeButton("이 작품 분석", () => runManualAnalyze(m.title_id))
      );
      resultsEl.appendChild(card);
    }
    resultsEl.classList.remove("hidden");
  } catch (e) {
    alert(e.message);
  }
});

async function runManualAnalyze(titleId) {
  try {
    manualAnalyzeResult = await apiCall(`/api/manual-download/analyze?title_id=${encodeURIComponent(titleId)}`);
    document.getElementById("manual-search-results").classList.add("hidden");
    renderManualTable();
  } catch (e) {
    alert(e.message);
  }
}

function renderManualTable() {
  document.getElementById("manual-result").classList.remove("hidden");
  document.getElementById("manual-title-name").textContent = manualAnalyzeResult.title;

  const tbody = document.getElementById("manual-tbody");
  tbody.innerHTML = "";
  for (const ep of manualAnalyzeResult.episodes) {
    const tr = document.createElement("tr");
    const statusLabel = ep.is_locked ? "유료/잠김" : ep.owned ? "보유함" : "미보유";
    tr.innerHTML = `
      <td><input type="checkbox" class="manual-ep-checkbox" data-no="${ep.episode_no}" ${ep.is_locked ? "disabled" : ""} /></td>
      <td>${ep.episode_no}</td>
      <td>${escapeHtml(ep.subtitle)}</td>
      <td>${statusLabel}</td>
    `;
    tbody.appendChild(tr);
  }
}

document.getElementById("btn-manual-select-all").addEventListener("click", () => {
  document.querySelectorAll(".manual-ep-checkbox:not(:disabled)").forEach((cb) => (cb.checked = true));
});
document.getElementById("btn-manual-select-none").addEventListener("click", () => {
  document.querySelectorAll(".manual-ep-checkbox").forEach((cb) => (cb.checked = false));
});
document.getElementById("btn-manual-select-missing").addEventListener("click", () => {
  document.querySelectorAll(".manual-ep-checkbox").forEach((cb) => {
    const no = Number(cb.dataset.no);
    const ep = manualAnalyzeResult.episodes.find((e) => e.episode_no === no);
    cb.checked = !cb.disabled && ep && !ep.owned;
  });
});

document.getElementById("btn-manual-download").addEventListener("click", async () => {
  const episodeNos = Array.from(document.querySelectorAll(".manual-ep-checkbox:checked")).map((cb) => Number(cb.dataset.no));
  if (episodeNos.length === 0) {
    alert("다운로드할 회차를 선택해주세요.");
    return;
  }
  try {
    await apiCall("/api/manual-download/run", {
      method: "POST",
      body: JSON.stringify({ title_id: manualAnalyzeResult.title_id, episode_nos: episodeNos }),
    });
    startManualPolling();
  } catch (e) {
    alert(e.message);
  }
});

document.getElementById("btn-copy-manual-log").addEventListener("click", () => {
  const text = document.getElementById("manual-log").innerText;
  navigator.clipboard?.writeText(text);
});

function startManualPolling() {
  stopManualPolling();
  refreshManualStatus();
  manualPollTimer = setInterval(refreshManualStatus, 2000);
}
function stopManualPolling() {
  if (manualPollTimer) {
    clearInterval(manualPollTimer);
    manualPollTimer = null;
  }
}
async function refreshManualStatus() {
  let statuses;
  try {
    statuses = await apiCall("/api/jobs/status");
  } catch (e) {
    return;
  }
  const st = statuses.manual;
  if (!st) return;
  const badge = document.getElementById("manual-status-badge");
  badge.textContent = st.status;
  badge.className = `badge job-${st.status}`;
  renderJobLog("manual", st.log);
  if (st.status !== "running") stopManualPolling();
}

// ── 작가/태그 관리 (별도 페이지) ───────────────────────────

async function loadRegistryPage() {
  loadAuthorList();
  loadTagList();
  document.getElementById("author-search-results").innerHTML = "";
  document.getElementById("tag-catalog-results").innerHTML = "";
}

document.getElementById("btn-search-author").addEventListener("click", async () => {
  const name = document.getElementById("author-search-input").value.trim();
  const resultsEl = document.getElementById("author-search-results");
  if (!name) return;
  resultsEl.innerHTML = "<p>검색 중...</p>";
  try {
    const results = await apiCall(`/api/authors/search?name=${encodeURIComponent(name)}`);
    resultsEl.innerHTML = "";
    if (results.length === 0) {
      resultsEl.innerHTML = "<p>일치하는 작가를 찾지 못했습니다. 직접 추가를 이용해주세요.</p>";
      return;
    }
    for (const r of results) {
      const row = document.createElement("div");
      row.className = "registry-row";
      row.innerHTML = `<span>${escapeHtml(r.author_name)} <span class="registry-meta">(예: ${escapeHtml(r.sample_title)})</span></span>`;
      row.appendChild(
        makeButton("등록", async () => {
          await apiCall("/api/watched-authors", {
            method: "POST",
            body: JSON.stringify({ author_id: r.author_id, author_name: r.author_name }),
          });
          loadAuthorList();
        })
      );
      resultsEl.appendChild(row);
    }
  } catch (e) {
    resultsEl.innerHTML = `<p class="error">${escapeHtml(e.message)}</p>`;
  }
});

async function loadAuthorList() {
  const container = document.getElementById("author-list");
  try {
    const authors = await apiCall("/api/watched-authors");
    container.innerHTML = "";
    if (authors.length === 0) {
      container.innerHTML = "<p>아직 발견된 작가가 없습니다. 웹툰을 구독하면 자동으로 채워집니다.</p>";
      return;
    }
    for (const a of authors) {
      const row = document.createElement("div");
      row.className = `registry-row${a.enabled ? "" : " disabled"}`;
      row.innerHTML = `<span>${escapeHtml(a.author_name || a.author_id)} <span class="registry-meta">(${escapeHtml(a.author_id)})</span></span>`;
      row.appendChild(
        makeButton(a.enabled ? "해제" : "등록", async () => {
          await apiCall(`/api/watched-authors/${a.author_id}/${a.enabled ? "disable" : "enable"}`, { method: "POST" });
          loadAuthorList();
        })
      );
      container.appendChild(row);
    }
  } catch (e) {
    container.innerHTML = `<p class="error">${escapeHtml(e.message)}</p>`;
  }
}

document.getElementById("btn-add-author").addEventListener("click", async () => {
  const authorId = document.getElementById("add-author-id").value.trim();
  const authorName = document.getElementById("add-author-name").value.trim();
  if (!authorId) return;
  try {
    await apiCall("/api/watched-authors", { method: "POST", body: JSON.stringify({ author_id: authorId, author_name: authorName }) });
    document.getElementById("add-author-id").value = "";
    document.getElementById("add-author-name").value = "";
    loadAuthorList();
  } catch (e) {
    alert(e.message);
  }
});

let tagCatalogCache = [];

async function ensureTagCatalog() {
  if (tagCatalogCache.length > 0) return tagCatalogCache;
  tagCatalogCache = await apiCall("/api/tags/catalog");
  return tagCatalogCache;
}

document.getElementById("tag-catalog-search").addEventListener("input", async (e) => {
  const query = e.target.value.trim().toLowerCase();
  const resultsEl = document.getElementById("tag-catalog-results");
  if (!query) {
    resultsEl.innerHTML = "";
    return;
  }
  try {
    const catalog = await ensureTagCatalog();
    const matches = catalog.filter((t) => t.tag_name.toLowerCase().includes(query)).slice(0, 20);
    resultsEl.innerHTML = "";
    for (const t of matches) {
      const row = document.createElement("div");
      row.className = "registry-row";
      row.innerHTML = `<span>${escapeHtml(t.tag_name)}</span>`;
      row.appendChild(
        makeButton("추가", async () => {
          await apiCall("/api/watched-tags", { method: "POST", body: JSON.stringify({ tag_id: t.tag_id, tag_name: t.tag_name }) });
          loadTagList();
        })
      );
      resultsEl.appendChild(row);
    }
  } catch (e) {
    resultsEl.innerHTML = `<p class="error">${escapeHtml(e.message)}</p>`;
  }
});

async function loadTagList() {
  const container = document.getElementById("tag-list");
  try {
    const tags = await apiCall("/api/watched-tags");
    container.innerHTML = "";
    for (const t of tags) {
      const row = document.createElement("div");
      row.className = `registry-row${t.enabled ? "" : " disabled"}`;
      row.innerHTML = `<span>${escapeHtml(t.tag_name || t.tag_id)}</span>`;
      row.appendChild(
        makeButton(t.enabled ? "해제" : "등록", async () => {
          await apiCall(`/api/watched-tags/${t.tag_id}/${t.enabled ? "disable" : "enable"}`, { method: "POST" });
          loadTagList();
        })
      );
      container.appendChild(row);
    }
  } catch (e) {
    container.innerHTML = `<p class="error">${escapeHtml(e.message)}</p>`;
  }
}

// ── 설정: 스케줄 ─────────────────────────────────────────

function buildScheduleControls(jobId, schedule) {
  const wrap = document.createElement("div");

  const modeSelect = document.createElement("select");
  modeSelect.className = "schedule-mode";
  modeSelect.innerHTML = `
    <option value="off">끄기</option>
    <option value="interval">주기(분)마다</option>
    <option value="cron">특정 시각</option>
  `;
  modeSelect.value = schedule.mode;

  const intervalRow = document.createElement("div");
  intervalRow.className = "schedule-row schedule-interval-row";
  intervalRow.innerHTML = `<input type="number" min="1" class="schedule-interval" value="${schedule.interval_minutes}" /> 분마다`;

  const cronRow = document.createElement("div");
  cronRow.className = "schedule-row schedule-cron-row";
  const hourOptions = Array.from({ length: 24 }, (_, h) => `<option value="${h}">${String(h).padStart(2, "0")}</option>`).join("");
  const minuteOptions = Array.from({ length: 60 }, (_, m) => `<option value="${m}">${String(m).padStart(2, "0")}</option>`).join("");
  const dayCheckboxes = Object.entries(DAY_LABEL)
    .map(
      ([day, label]) => `
      <label class="day-checkbox">
        <input type="checkbox" class="schedule-day" value="${day}" ${schedule.cron_days.includes(day) ? "checked" : ""} />
        ${label}
      </label>`
    )
    .join("");
  cronRow.innerHTML = `
    <select class="schedule-hour">${hourOptions}</select> 시
    <select class="schedule-minute">${minuteOptions}</select> 분
    <span class="day-checkbox-group">${dayCheckboxes}<span class="schedule-day-hint">(아무 요일도 안 고르면 매일)</span></span>
  `;
  cronRow.querySelector(".schedule-hour").value = schedule.cron_hour;
  cronRow.querySelector(".schedule-minute").value = schedule.cron_minute;

  function updateVisibility() {
    intervalRow.classList.toggle("hidden", modeSelect.value !== "interval");
    cronRow.classList.toggle("hidden", modeSelect.value !== "cron");
  }
  modeSelect.addEventListener("change", updateVisibility);
  updateVisibility();

  wrap.appendChild(modeSelect);
  wrap.appendChild(intervalRow);
  wrap.appendChild(cronRow);
  return wrap;
}

function readScheduleControls(wrap) {
  return {
    mode: wrap.querySelector(".schedule-mode").value,
    interval_minutes: Number(wrap.querySelector(".schedule-interval").value) || 60,
    cron_hour: Number(wrap.querySelector(".schedule-hour").value) || 0,
    cron_minute: Number(wrap.querySelector(".schedule-minute").value) || 0,
    cron_days: Array.from(wrap.querySelectorAll(".schedule-day:checked")).map((el) => el.value),
  };
}

document.getElementById("btn-save-settings").addEventListener("click", async () => {
  const resultEl = document.getElementById("settings-save-result");
  resultEl.textContent = "";
  try {
    const payload = {};
    for (const jobId of SCHEDULE_JOB_IDS) {
      const wrap = document.querySelector(`.schedule-block[data-job="${jobId}"] .schedule-controls`);
      payload[jobId] = readScheduleControls(wrap);
    }
    await apiCall("/api/settings", { method: "POST", body: JSON.stringify(payload) });
    resultEl.style.color = "";
    resultEl.textContent = "저장했습니다.";
  } catch (e) {
    resultEl.textContent = e.message;
  }
});

// ── 설정: 디스코드 ───────────────────────────────────────

async function loadDiscordSettings() {
  try {
    const s = await apiCall("/api/settings/discord");
    document.getElementById("discord-webhook-url").value = s.webhook_url;
    document.getElementById("discord-channel-id").value = s.notify_channel_id;
    document.getElementById("discord-bot-token").placeholder = s.bot_token_set ? "설정됨 (변경하려면 입력)" : "미설정";
    document.getElementById("discord-bot-status").textContent = s.bot_ready ? "🟢 봇 연결됨" : "⚪ 봇 연결 안 됨";
  } catch (e) {
    document.getElementById("discord-save-result").textContent = e.message;
  }
}

document.getElementById("btn-save-discord").addEventListener("click", async () => {
  const resultEl = document.getElementById("discord-save-result");
  resultEl.textContent = "";
  try {
    await apiCall("/api/settings/discord", {
      method: "POST",
      body: JSON.stringify({
        webhook_url: document.getElementById("discord-webhook-url").value.trim(),
        bot_token: document.getElementById("discord-bot-token").value.trim(),
        notify_channel_id: document.getElementById("discord-channel-id").value.trim(),
      }),
    });
    document.getElementById("discord-bot-token").value = "";
    resultEl.style.color = "";
    resultEl.textContent = "저장했습니다. 봇을 재연결하는 중입니다 (몇 초 걸릴 수 있음).";
    setTimeout(loadDiscordSettings, 3000);
  } catch (e) {
    resultEl.textContent = e.message;
  }
});

document.getElementById("btn-test-webhook").addEventListener("click", async () => {
  const result = await apiCall("/api/settings/discord/test-webhook", { method: "POST" });
  alert(result.message);
});

document.getElementById("btn-test-bot").addEventListener("click", async () => {
  const result = await apiCall("/api/settings/discord/test-bot", { method: "POST" });
  alert(result.message);
});

// ── 설정: 백업/복원 ──────────────────────────────────────

document.getElementById("btn-backup-download").addEventListener("click", async () => {
  try {
    const backup = await apiCall("/api/backup");
    const blob = new Blob([JSON.stringify(backup, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `webtoon-manager-backup-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    document.getElementById("backup-result").textContent = e.message;
  }
});

document.getElementById("restore-file-input").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  const resultEl = document.getElementById("backup-result");
  resultEl.textContent = "";
  try {
    const text = await file.text();
    const data = JSON.parse(text);
    if (!confirm("현재 데이터를 모두 지우고 이 백업으로 복원합니다. 계속할까요?")) {
      event.target.value = "";
      return;
    }
    await apiCall("/api/restore", { method: "POST", body: JSON.stringify(data) });
    resultEl.style.color = "";
    resultEl.textContent = "복원 완료. 페이지를 새로고침해주세요.";
  } catch (e) {
    resultEl.textContent = `복원 실패: ${e.message}`;
  }
  event.target.value = "";
});

// ── 설정: 수동 실행 + 진행상황 ────────────────────────────

let jobPollTimer = null;

async function loadSettingsPage() {
  try {
    const schedules = await apiCall("/api/settings");
    for (const jobId of SCHEDULE_JOB_IDS) {
      const block = document.querySelector(`.schedule-block[data-job="${jobId}"] .schedule-controls`);
      block.innerHTML = "";
      block.appendChild(buildScheduleControls(jobId, schedules[jobId]));
    }
  } catch (e) {
    document.getElementById("settings-save-result").textContent = e.message;
  }
  loadDiscordSettings();
  await refreshJobStatus();
  startJobPolling();
}

document.getElementById("btn-run-discovery").addEventListener("click", async () => {
  await apiCall("/api/jobs/discovery/run", { method: "POST" });
  await refreshJobStatus();
});

document.getElementById("btn-run-download").addEventListener("click", async () => {
  await apiCall("/api/jobs/download/run", { method: "POST" });
  await refreshJobStatus();
});

function renderJobLog(jobName, lines) {
  const container = document.getElementById(`${jobName}-log`);
  container.innerHTML = lines
    .map((line) => {
      const separatorIndex = line.indexOf(" — ");
      const timestamp = separatorIndex >= 0 ? line.slice(0, separatorIndex) : "";
      const message = separatorIndex >= 0 ? line.slice(separatorIndex + 3) : line;
      const timeLabel = timestamp.length >= 19 ? timestamp.slice(11, 19) : timestamp;
      const isError = /오류|실패/.test(message);
      return `<div class="log-line${isError ? " log-error" : ""}"><span class="log-time">${escapeHtml(timeLabel)}</span>${escapeHtml(message)}</div>`;
    })
    .join("");
  container.scrollTop = container.scrollHeight;
}

async function refreshJobStatus() {
  let statuses;
  try {
    statuses = await apiCall("/api/jobs/status");
  } catch (e) {
    return;
  }

  for (const jobName of ["discovery", "download"]) {
    const st = statuses[jobName];
    if (!st) continue;
    const badge = document.getElementById(`${jobName}-status-badge`);
    badge.textContent = st.status;
    badge.className = `badge job-${st.status}`;
    renderJobLog(jobName, st.log);
  }
}

function startJobPolling() {
  stopJobPolling();
  jobPollTimer = setInterval(refreshJobStatus, 2000);
}

function stopJobPolling() {
  if (jobPollTimer) {
    clearInterval(jobPollTimer);
    jobPollTimer = null;
  }
}

// ── 초기 로드 ────────────────────────────────────────────

restoreNaverListPrefs();
loadNaverList();
