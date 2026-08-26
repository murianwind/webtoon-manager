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

// 로그의 타임스탬프는 서버가 UTC로 찍어서 보내므로(예: "...+00:00"), 그냥 문자열을
// 잘라 쓰면 한국시간이 아니라 UTC 그대로 보인다 — 항상 이 함수로 KST 변환해서 쓴다.
function formatKoreanTime(isoString, options = {}) {
  const date = new Date(isoString);
  if (isNaN(date.getTime())) return isoString || "";
  return date.toLocaleString("ko-KR", { timeZone: "Asia/Seoul", ...options });
}

function formatKoreanTimeOnly(isoString) {
  return formatKoreanTime(isoString, { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
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

const ACTIVE_TAB_KEY = "activeMainTab";

function switchToTab(page) {
  document.querySelectorAll(".main-tab").forEach((t) => t.classList.toggle("active", t.dataset.page === page));
  document.querySelectorAll(".page").forEach((p) => p.classList.add("hidden"));
  document.getElementById(`page-${page}`).classList.remove("hidden");
  stopJobPolling();
  stopRegistryPolling();
  pageLoaders[page]?.();
}

document.querySelectorAll(".main-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    const page = tab.dataset.page;
    localStorage.setItem(ACTIVE_TAB_KEY, page);
    switchToTab(page);
  });
});

// ── 공용 카드 빌더 ───────────────────────────────────────

function buildWebtoonCard(w, context) {
  const card = document.createElement("div");
  card.className = "webtoon-card";
  card.dataset.titleId = w.title_id;

  const metaParts = [];
  if (context !== "naver-list" && w.last_downloaded_no > 0) metaParts.push(`${w.last_downloaded_no}화까지 다운로드`);
  const authorText = context === "naver-list" ? w.author_summary : (w.writer_names || []).join(", ");
  if (authorText) metaParts.push(authorText);
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
    if (context === "unsubscribed") {
      // 완전 삭제는 아니고 excluded로 옮긴다 — 이후 작가/태그 자동추가로 다시 안 들어오게 확정.
      actions.appendChild(makeButton("목록에서 제거", () => subscriptionAction(w.title_id, "remove", "unsubscribed")));
    }
    if (context === "excluded" && w.is_finished) {
      // 완결작만 완전 삭제 허용 — 완결작은 자동추가 로직이 원래 다시 안 건드리므로 안전하다.
      actions.appendChild(makeButton("완전 삭제", () => deleteWebtoonPermanently(w.title_id)));
    }
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
  const statusEl = document.getElementById("naver-list-refresh-status");
  const btn = document.getElementById("btn-refresh-naver-list");

  if (grid.children.length === 0) {
    grid.innerHTML = "<p>불러오는 중...</p>";
  }
  btn.disabled = true;
  statusEl.textContent = "새로고침 중...";

  try {
    naverListCache = await apiCall("/api/naver-list");
    renderNaverList();
    statusEl.textContent = `마지막 새로고침: ${formatKoreanTime(new Date().toISOString())} (${naverListCache.length}개)`;
  } catch (e) {
    if (grid.children.length === 0) {
      emptyMsg.textContent = `목록을 불러오지 못했습니다: ${e.message}`;
      emptyMsg.classList.remove("hidden");
    }
    statusEl.textContent = `새로고침 실패: ${e.message}`;
  } finally {
    btn.disabled = false;
  }
}

function renderNaverList() {
  const grid = document.getElementById("naver-list-grid");
  const emptyMsg = document.getElementById("naver-list-empty");
  const query = document.getElementById("naver-list-search").value.trim().toLowerCase();
  const filterStatus = document.getElementById("naver-list-filter-status").value;
  const sortBy = document.getElementById("naver-list-sort").value;

  // 구독해제/목록제외한 작품은 여기서 안 보이고, 각자의 탭(구독해제/제외됨)에서만 보인다.
  let rows = naverListCache.filter((w) => w.status !== "excluded" && w.status !== "unsubscribed");

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
  if (newStatus === "excluded" || newStatus === "unsubscribed") {
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

async function loadSubscriptionTab(status) {
  const listEl = document.getElementById(`${status}-list`);
  const emptyEl = document.getElementById(`${status}-empty`);
  if (listEl.children.length === 0) {
    listEl.innerHTML = "<p>불러오는 중...</p>";
  }
  try {
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

  // 작가 이름은 각 웹툰 자체에 저장된 writer_ids/writer_names에서 직접 뽑는다
  // (별도 레지스트리 조회 없이, 지금 보이는 웹툰들의 실제 데이터만으로 채운다).
  const authorNames = new Map(); // id -> name
  const tagNames = new Set();
  for (const w of rows) {
    const ids = w.writer_ids || [];
    const names = w.writer_names || [];
    ids.forEach((id, i) => {
      if (!authorNames.has(id)) authorNames.set(id, names[i] || id);
    });
    (w.tags || []).forEach((t) => tagNames.add(t));
  }

  const authorSelect = document.getElementById(`${status}-author-filter`);
  const currentAuthor = authorSelect.value;
  authorSelect.innerHTML = '<option value="">작가 전체</option>';
  for (const [id, name] of [...authorNames.entries()].sort((a, b) => a[1].localeCompare(b[1]))) {
    const opt = document.createElement("option");
    opt.value = id;
    opt.textContent = name;
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

async function deleteWebtoonPermanently(titleId) {
  if (!confirm("완전히 삭제합니다 (되돌릴 수 없음). 계속할까요?")) return;
  try {
    await apiCall(`/api/webtoons/${titleId}`, { method: "DELETE" });
    const listEl = document.getElementById("excluded-list");
    const card = listEl.querySelector(`.webtoon-card[data-title-id="${titleId}"]`);
    card?.remove();
    document.getElementById("excluded-empty").classList.toggle("hidden", listEl.children.length > 0);
    subscriptionCache.excluded = (subscriptionCache.excluded || []).filter((w) => w.title_id !== titleId);
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

let registryPollTimer = null;

async function loadRegistryPage() {
  await loadAuthorList();
  await loadTagList();
  await refreshRegistryJobStatuses();
}

document.getElementById("btn-resync-registry").addEventListener("click", async () => {
  await apiCall("/api/registry/resync", { method: "POST" });
  startRegistryPolling();
});

async function refreshRegistryJobStatuses() {
  let statuses;
  try {
    statuses = await apiCall("/api/jobs/status");
  } catch (e) {
    return;
  }
  applyJobStatusToLabel(statuses.registry, "registry-resync-status", "재동기화 중...");

  if (statuses.registry?.status !== "running") {
    loadAuthorList();
    loadTagList();
    stopRegistryPolling();
  }
}

function applyJobStatusToLabel(st, elementId, runningText) {
  const el = document.getElementById(elementId);
  if (!st || !el) return;
  if (st.status === "running") el.textContent = runningText;
  else if (st.status === "success") el.textContent = "완료됨";
  else if (st.status === "error") el.textContent = "오류 발생 (설정 탭 실행 이력 참고)";
  else el.textContent = "";
}

function startRegistryPolling() {
  stopRegistryPolling();
  refreshRegistryJobStatuses();
  registryPollTimer = setInterval(refreshRegistryJobStatuses, 2000);
}
function stopRegistryPolling() {
  if (registryPollTimer) {
    clearInterval(registryPollTimer);
    registryPollTimer = null;
  }
}

// 칩 하나를 만든다. selected=true면 파란 "선택됨" 칩(× 아이콘, 클릭 시 onAction),
// false면 회색 "사용 가능" 칩(+ 아이콘, 클릭 시 onAction).
function buildChip(label, selected, onAction) {
  const chip = document.createElement("button");
  chip.type = "button";
  chip.className = `chip ${selected ? "chip-selected" : "chip-available"}`;
  chip.innerHTML = `<span>${escapeHtml(label)}</span><span class="chip-icon">${selected ? "×" : "+"}</span>`;
  chip.addEventListener("click", onAction);
  return chip;
}

async function searchAndRegisterAuthor(name) {
  try {
    const results = await apiCall(`/api/authors/search?name=${encodeURIComponent(name)}`);
    if (results.length === 0) {
      alert(`"${name}"과 일치하는 작가를 찾지 못했습니다.`);
      return;
    }
    // 이름으로 정확히 검색했을 때 후보가 여러 명이면(동명이인 등) 첫 번째로 등록한다 —
    // 후보가 여러 개 나오는 경우는 드물고, 필요하면 언제든 "제외"로 되돌릴 수 있다.
    const r = results[0];
    await apiCall("/api/watched-authors", {
      method: "POST",
      body: JSON.stringify({ author_id: r.author_id, author_name: r.author_name }),
    });
    loadAuthorList();
  } catch (e) {
    alert(e.message);
  }
}

// 등록된 작가(watchedAuthorsCache, enabled=true) / 사용 가능(네이버 연재중 목록에서
// 뽑은 이름 후보 중 아직 등록 안 한 것) — 칩을 클릭하면 서로 반대쪽으로 이동한다.
let watchedAuthorsCache = [];
let authorCandidatesCache = [];

async function loadAuthorList() {
  const registeredEl = document.getElementById("author-registered-chips");
  try {
    watchedAuthorsCache = await apiCall("/api/authors/interested");
  } catch (e) {
    registeredEl.innerHTML = `<p class="error">${escapeHtml(e.message)}</p>`;
    return;
  }
  renderRegisteredAuthors();

  if (authorCandidatesCache.length === 0) {
    try {
      authorCandidatesCache = await apiCall("/api/authors/candidates");
    } catch (e) {
      document.getElementById("author-all-chips").innerHTML = `<p class="error">${escapeHtml(e.message)}</p>`;
      return;
    }
  }
  renderAllAuthors();
}

function renderRegisteredAuthors() {
  const container = document.getElementById("author-registered-chips");
  const registered = watchedAuthorsCache.filter((a) => a.enabled);
  container.innerHTML = "";
  if (registered.length === 0) {
    container.innerHTML =
      '<p class="chip-empty-message">구독중인 웹툰이 없거나, 아직 저자 정보를 확인하지 못했습니다. "지금 전체 재동기화"를 눌러보세요.</p>';
    return;
  }
  for (const a of registered) {
    container.appendChild(
      buildChip(a.author_name || a.author_id, true, async () => {
        await apiCall(`/api/watched-authors/${a.author_id}/disable`, {
          method: "POST",
          body: JSON.stringify({ author_name: a.author_name }),
        });
        loadAuthorList();
      })
    );
  }
}

function renderAllAuthors() {
  const container = document.getElementById("author-all-chips");
  const query = document.getElementById("author-candidate-filter").value.trim().toLowerCase();
  const registeredNames = new Set(watchedAuthorsCache.filter((a) => a.enabled).map((a) => a.author_name));

  const candidates = authorCandidatesCache.filter(
    (name) => !registeredNames.has(name) && (!query || name.toLowerCase().includes(query))
  );

  container.innerHTML = "";
  if (candidates.length === 0) {
    container.innerHTML = '<p class="chip-empty-message">표시할 후보가 없습니다.</p>';
    return;
  }
  for (const name of candidates) {
    container.appendChild(buildChip(name, false, () => searchAndRegisterAuthor(name)));
  }
}

document.getElementById("author-candidate-filter").addEventListener("input", renderAllAuthors);

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

// 등록된 태그(enabled) / 사용 가능(네이버 전체 카탈로그 중 아직 등록 안 한 것)
let tagCatalogCache = [];
let watchedTagsCache = [];

async function ensureTagCatalog() {
  if (tagCatalogCache.length > 0) return tagCatalogCache;
  tagCatalogCache = await apiCall("/api/tags/catalog");
  return tagCatalogCache;
}

async function loadTagList() {
  try {
    watchedTagsCache = await apiCall("/api/watched-tags");
  } catch (e) {
    document.getElementById("tag-registered-chips").innerHTML = `<p class="error">${escapeHtml(e.message)}</p>`;
    return;
  }
  renderRegisteredTags();
  renderAllTags();
}

function renderRegisteredTags() {
  const container = document.getElementById("tag-registered-chips");
  const registered = watchedTagsCache.filter((t) => t.enabled);
  container.innerHTML = "";
  if (registered.length === 0) {
    container.innerHTML = '<p class="chip-empty-message">등록된 태그가 없습니다.</p>';
    return;
  }
  for (const t of registered) {
    container.appendChild(
      buildChip(t.tag_name || t.tag_id, true, async () => {
        await apiCall(`/api/watched-tags/${t.tag_id}/disable`, { method: "POST" });
        loadTagList();
      })
    );
  }
}

async function renderAllTags() {
  const container = document.getElementById("tag-all-chips");
  const query = document.getElementById("tag-catalog-search").value.trim().toLowerCase();
  container.innerHTML = "<p>불러오는 중...</p>";
  try {
    const catalog = await ensureTagCatalog();
    const enabledIds = new Set(watchedTagsCache.filter((t) => t.enabled).map((t) => t.tag_id));
    let items = catalog.filter((t) => !enabledIds.has(t.tag_id));
    if (query) items = items.filter((t) => t.tag_name.toLowerCase().includes(query));

    container.innerHTML = "";
    if (items.length === 0) {
      container.innerHTML = '<p class="chip-empty-message">표시할 태그가 없습니다.</p>';
      return;
    }
    for (const t of items) {
      container.appendChild(
        buildChip(t.tag_name, false, async () => {
          await apiCall("/api/watched-tags", { method: "POST", body: JSON.stringify({ tag_id: t.tag_id, tag_name: t.tag_name }) });
          loadTagList();
        })
      );
    }
  } catch (e) {
    container.innerHTML = `<p class="error">${escapeHtml(e.message)}</p>`;
  }
}

document.getElementById("tag-catalog-search").addEventListener("input", renderAllTags);

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

const BOT_TOKEN_MASK = "••••••••••••••••";

async function loadDiscordSettings() {
  try {
    const s = await apiCall("/api/settings/discord");
    document.getElementById("discord-webhook-url").value = s.webhook_url;
    document.getElementById("discord-channel-id").value = s.notify_channel_id;

    const tokenInput = document.getElementById("discord-bot-token");
    tokenInput.value = s.bot_token_set ? BOT_TOKEN_MASK : "";
    tokenInput.dataset.masked = s.bot_token_set ? "true" : "false";

    document.getElementById("discord-bot-status").textContent = s.bot_ready ? "🟢 봇 연결됨" : "⚪ 봇 연결 안 됨";
  } catch (e) {
    document.getElementById("discord-save-result").textContent = e.message;
  }
}

document.getElementById("discord-bot-token").addEventListener("focus", (e) => {
  if (e.target.dataset.masked === "true") {
    e.target.value = "";
    e.target.dataset.masked = "false";
  }
});

document.getElementById("btn-save-discord").addEventListener("click", async () => {
  const resultEl = document.getElementById("discord-save-result");
  resultEl.textContent = "";
  try {
    const tokenInput = document.getElementById("discord-bot-token");
    const rawToken = tokenInput.value.trim();
    const tokenToSend = rawToken === BOT_TOKEN_MASK ? "" : rawToken;

    await apiCall("/api/settings/discord", {
      method: "POST",
      body: JSON.stringify({
        webhook_url: document.getElementById("discord-webhook-url").value.trim(),
        bot_token: tokenToSend,
        notify_channel_id: document.getElementById("discord-channel-id").value.trim(),
      }),
    });
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
  loadJobHistory();
  await refreshJobStatus();
  startJobPolling();
}

const JOB_NAME_LABEL = { discovery: "신작 스캔", download: "다운로드", manual: "수동 다운로드", registry: "작가/태그 재동기화" };

async function loadJobHistory() {
  const container = document.getElementById("job-history-list");
  container.innerHTML = "<p>불러오는 중...</p>";
  try {
    const history = await apiCall("/api/jobs/history");
    container.innerHTML = "";
    if (history.length === 0) {
      container.innerHTML = "<p>아직 실행된 기록이 없습니다.</p>";
      return;
    }
    for (const entry of history) {
      const wrap = document.createElement("div");
      wrap.className = "job-history-entry";

      const startedLabel = entry.started_at ? formatKoreanTime(entry.started_at) : "";
      const summary = document.createElement("div");
      summary.className = "job-history-summary";
      summary.innerHTML = `
        <span class="job-history-name">${escapeHtml(JOB_NAME_LABEL[entry.job_name] || entry.job_name)}</span>
        <span class="job-history-time">${escapeHtml(startedLabel || "")}</span>
        <span class="badge job-${entry.status}">${entry.status}</span>
      `;

      const logEl = document.createElement("div");
      logEl.className = "job-log";

      summary.addEventListener("click", () => {
        wrap.classList.toggle("expanded");
        if (wrap.classList.contains("expanded") && !logEl.dataset.rendered) {
          logEl.innerHTML = entry.log
            .map((line) => {
              const sep = line.indexOf(" — ");
              const ts = sep >= 0 ? line.slice(0, sep) : "";
              const msg = sep >= 0 ? line.slice(sep + 3) : line;
              const timeLabel = ts ? formatKoreanTimeOnly(ts) : "";
              const isError = /오류|실패/.test(msg);
              return `<div class="log-line${isError ? " log-error" : ""}"><span class="log-time">${escapeHtml(timeLabel)}</span>${escapeHtml(msg)}</div>`;
            })
            .join("");
          logEl.dataset.rendered = "true";
        }
      });

      wrap.appendChild(summary);
      wrap.appendChild(logEl);
      container.appendChild(wrap);
    }
  } catch (e) {
    container.innerHTML = `<p class="error">${escapeHtml(e.message)}</p>`;
  }
}

document.getElementById("btn-refresh-history").addEventListener("click", loadJobHistory);

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
      const timeLabel = timestamp ? formatKoreanTimeOnly(timestamp) : "";
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

const savedTab = localStorage.getItem(ACTIVE_TAB_KEY);
if (savedTab && document.getElementById(`page-${savedTab}`)) {
  switchToTab(savedTab);
} else {
  loadNaverList();
}
