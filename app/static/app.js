const STATUS_LABEL = {
  active: "구독중",
  unsubscribed: "구독해제",
  excluded: "목록제외",
};

const DAY_LABEL = { mon: "월", tue: "화", wed: "수", thu: "목", fri: "금", sat: "토", sun: "일" };
const SCHEDULE_JOB_IDS = ["discovery_job", "download_job", "report_job", "archive_job"];

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

// 페이지마다 표 위쪽에 오는 내용(검색결과, 버튼 줄 등)의 높이가 달라서, 고정된
// px값으로는 어떤 페이지는 남고 어떤 페이지는 모자란다 — 실제 남은 화면 높이를
// 매번 계산해서 정확히 맞춘다("한 화면에 다 보이게" 요구사항 대응).
function fitScrollWrapperToViewport(wrapperId, reserveBelowPx = 16) {
  const wrapper = document.getElementById(wrapperId);
  if (!wrapper) return;
  const top = wrapper.getBoundingClientRect().top;
  const available = window.innerHeight - top - reserveBelowPx;
  wrapper.style.maxHeight = `${Math.max(120, available)}px`;
}

window.addEventListener("resize", () => {
  if (!document.getElementById("page-manual-download").classList.contains("hidden")) {
    fitScrollWrapperToViewport("manual-table-wrapper", 240);
  }
  if (!document.getElementById("page-episode-history").classList.contains("hidden")) {
    fitScrollWrapperToViewport("episode-history-table-wrapper", 60);
  }
});

function makeButton(label, onClick) {
  const btn = document.createElement("button");
  btn.textContent = label;
  btn.addEventListener("click", onClick);
  return btn;
}

function makeIconButton(svgMarkup, title, onClick) {
  const btn = document.createElement("button");
  btn.className = "icon-btn";
  btn.title = title;
  btn.setAttribute("aria-label", title);
  btn.innerHTML = svgMarkup;
  btn.addEventListener("click", onClick);
  return btn;
}

const READER_ICON_SVG = `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg>`;

function naverUrl(titleId) {
  return `https://comic.naver.com/webtoon/list?titleId=${titleId}`;
}

// 웹툰 뷰어 서버 주소가 설정되어 있는지 — 있으면 구독중 카드에 "뷰어에서 보기" 아이콘을 띄운다.
// 페이지 열릴 때 한 번만 확인하고(설정 탭에서 바꾸면 새로고침해야 반영됨), 카드마다
// 매번 물어보진 않는다.
let webtoonServerConfigured = false;
apiCall("/api/settings/webtoon-server")
  .then((data) => { webtoonServerConfigured = Boolean(data.webtoon_server_url); })
  .catch(() => {});

async function openInWebtoonServer(title) {
  try {
    const data = await apiCall(`/api/webtoon-server/lookup?title=${encodeURIComponent(title)}`);
    if (data.url) {
      window.open(data.url, "_blank", "noopener");
    } else {
      alert("뷰어 서버에서 이 작품을 찾지 못했습니다.");
    }
  } catch (e) {
    alert(`뷰어 서버 조회 실패: ${e.message}`);
  }
}

function badgesHtml(w) {
  const parts = [];
  if (w.is_new) parts.push('<span class="badge new-release">신작</span>');
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
  "episode-history": () => loadEpisodeHistory(1),
  "manual-run": loadManualRunPage,
  "job-history": loadJobHistoryPage,
  archive: loadArchivePage,
  settings: loadSettingsPage,
};

const ACTIVE_TAB_KEY = "activeMainTab";

function switchToTab(page) {
  document.querySelectorAll(".main-tab").forEach((t) => t.classList.toggle("active", t.dataset.page === page));
  document.querySelectorAll(".page").forEach((p) => p.classList.add("hidden"));
  document.getElementById(`page-${page}`).classList.remove("hidden");
  stopJobPolling();
  stopRegistryPolling();
  stopArchiveJobPolling();
  pageLoaders[page]?.();
}

document.querySelectorAll(".main-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    const page = tab.dataset.page;
    sessionStorage.setItem(ACTIVE_TAB_KEY, page);
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
      if (webtoonServerConfigured) {
        actions.appendChild(makeIconButton(READER_ICON_SVG, "뷰어에서 보기", () => openInWebtoonServer(w.title)));
      }
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
  const badgeFilter = document.getElementById(`${status}-badge-filter`).value;

  let rows = subscriptionCache[status] || [];
  if (query) rows = rows.filter((w) => w.title.toLowerCase().includes(query));
  if (authorFilter) rows = rows.filter((w) => (w.writer_ids || []).includes(authorFilter));
  if (tagFilter) rows = rows.filter((w) => (w.tags || []).includes(tagFilter));
  if (badgeFilter === "new") rows = rows.filter((w) => w.is_new);
  if (badgeFilter === "paused") rows = rows.filter((w) => w.is_paused);

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
  document.getElementById(`${status}-badge-filter`).addEventListener("change", () => renderSubscriptionTab(status));
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

document.getElementById("manual-query").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    document.getElementById("btn-manual-analyze").click();
  }
});

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
  // 검색결과 영역이 숨겨지면서 레이아웃이 바뀐 다음(다음 페인트 이후) 계산해야
  // 정확한 남은 높이가 나온다.
  requestAnimationFrame(() => fitScrollWrapperToViewport("manual-table-wrapper", 240));
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
  copyTextToClipboard(document.getElementById("manual-log").innerText);
});

// navigator.clipboard는 HTTPS/localhost가 아니면(예: http://192.168.x.x:8001로 접속하는
// LAN 환경) 브라우저에서 자체적으로 비활성화되어 있어서 조용히 아무 일도 안 일어난다
// (실제로 이것 때문에 "로그 복사가 안 된다"는 문제가 있었다) — 안 되면 구식 방식으로 대체한다.
function copyTextToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).catch(() => fallbackCopy(text));
  } else {
    fallbackCopy(text);
  }
}

function fallbackCopy(text) {
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  try {
    document.execCommand("copy");
  } catch (e) {
    alert("클립보드 복사에 실패했습니다.");
  }
  document.body.removeChild(textarea);
}

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
  await loadKakaoAuthorList();
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

  // "사용 가능" = 선택된(enabled) 저자를 뺀 전부. 두 종류를 합친다:
  //  1) 이미 id를 아는데 비활성 상태인 작가(watchedAuthorsCache, enabled=false) — 클릭하면 바로 등록(검색 불필요)
  //  2) 아직 등록 자체가 안 된 이름 후보(authorCandidatesCache, 텍스트에서 추출) — 클릭하면 검색해서 등록
  const disabledKnown = watchedAuthorsCache.filter((a) => !a.enabled);
  const knownNames = new Set(watchedAuthorsCache.map((a) => a.author_name).filter(Boolean));
  const enabledNames = new Set(watchedAuthorsCache.filter((a) => a.enabled).map((a) => a.author_name));

  const items = [];
  for (const a of disabledKnown) {
    const label = a.author_name || a.author_id;
    if (query && !label.toLowerCase().includes(query)) continue;
    items.push({ label, onClick: () => enableKnownAuthor(a) });
  }
  for (const name of authorCandidatesCache) {
    if (enabledNames.has(name) || knownNames.has(name)) continue; // 이미 위에서 다뤄졌거나 이미 선택된 이름은 중복 표시 안 함
    if (query && !name.toLowerCase().includes(query)) continue;
    items.push({ label: name, onClick: () => searchAndRegisterAuthor(name) });
  }

  container.innerHTML = "";
  if (items.length === 0) {
    container.innerHTML = '<p class="chip-empty-message">표시할 후보가 없습니다.</p>';
    return;
  }
  for (const item of items) {
    container.appendChild(buildChip(item.label, false, item.onClick));
  }
}

async function enableKnownAuthor(author) {
  await apiCall(`/api/watched-authors/${author.author_id}/enable`, {
    method: "POST",
    body: JSON.stringify({ author_name: author.author_name }),
  });
  loadAuthorList();
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

// ── 카카오웹툰 작가 (이름 자체가 식별자 — 네이버와 동일하게 선택됨/사용가능 2단) ──

let kakaoWatchedAuthorsCache = [];
let kakaoAuthorCandidatesCache = [];

async function loadKakaoAuthorList() {
  const registeredEl = document.getElementById("kakao-author-registered-chips");
  try {
    kakaoWatchedAuthorsCache = await apiCall("/api/kakao/watched-authors");
  } catch (e) {
    registeredEl.innerHTML = `<p class="error">${escapeHtml(e.message)}</p>`;
    return;
  }
  renderKakaoRegisteredAuthors();

  if (kakaoAuthorCandidatesCache.length === 0) {
    try {
      kakaoAuthorCandidatesCache = await apiCall("/api/kakao/authors/candidates");
    } catch (e) {
      document.getElementById("kakao-author-all-chips").innerHTML = `<p class="error">${escapeHtml(e.message)}</p>`;
      return;
    }
  }
  renderKakaoAllAuthors();
}

function renderKakaoRegisteredAuthors() {
  const container = document.getElementById("kakao-author-registered-chips");
  const registered = kakaoWatchedAuthorsCache.filter((a) => a.enabled);
  container.innerHTML = "";
  if (registered.length === 0) {
    container.innerHTML = '<p class="chip-empty-message">등록된 카카오웹툰 작가가 없습니다. 오른쪽 후보에서 골라 등록하세요.</p>';
    return;
  }
  for (const a of registered) {
    container.appendChild(
      buildChip(a.author_name, true, async () => {
        await apiCall(`/api/kakao/watched-authors/${encodeURIComponent(a.author_id)}/disable`, { method: "POST" });
        loadKakaoAuthorList();
      })
    );
  }
}

function renderKakaoAllAuthors() {
  const container = document.getElementById("kakao-author-all-chips");
  const query = document.getElementById("kakao-author-candidate-filter").value.trim().toLowerCase();

  // 네이버와 동일한 패턴 — "사용 가능"은 두 종류를 합친다:
  //  1) 이미 등록했다가 제외한 작가(kakaoWatchedAuthorsCache, enabled=false) — 클릭하면
  //     바로 재등록(검색 불필요). 이 경로가 없으면 한 번 제외한 카카오 작가는
  //     후보 캐시에 우연히 다시 안 걸릴 경우 영영 재등록할 방법이 없었다(실제 버그).
  //  2) 아직 한 번도 등록 안 한 카탈로그 이름 후보 — 클릭하면 검색 확인 후 신규 등록.
  const disabledKnown = kakaoWatchedAuthorsCache.filter((a) => !a.enabled);
  const knownNames = new Set(kakaoWatchedAuthorsCache.map((a) => a.author_name).filter(Boolean));
  const enabledNames = new Set(kakaoWatchedAuthorsCache.filter((a) => a.enabled).map((a) => a.author_name));

  const items = [];
  for (const a of disabledKnown) {
    if (query && !a.author_name.toLowerCase().includes(query)) continue;
    items.push({ label: a.author_name, onClick: () => enableKnownKakaoAuthor(a) });
  }
  for (const name of kakaoAuthorCandidatesCache) {
    if (enabledNames.has(name) || knownNames.has(name)) continue;
    if (query && !name.toLowerCase().includes(query)) continue;
    items.push({
      label: name,
      onClick: async () => {
        try {
          await apiCall("/api/kakao/watched-authors", { method: "POST", body: JSON.stringify({ author_name: name }) });
          loadKakaoAuthorList();
        } catch (e) {
          alert(e.message);
        }
      },
    });
  }

  container.innerHTML = "";
  if (items.length === 0) {
    container.innerHTML = '<p class="chip-empty-message">표시할 후보가 없습니다.</p>';
    return;
  }
  for (const item of items) {
    container.appendChild(buildChip(item.label, false, item.onClick));
  }
}

async function enableKnownKakaoAuthor(author) {
  await apiCall(`/api/kakao/watched-authors/${encodeURIComponent(author.author_id)}/enable`, { method: "POST" });
  loadKakaoAuthorList();
}

document.getElementById("kakao-author-candidate-filter").addEventListener("input", renderKakaoAllAuthors);

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

  const timesList = document.createElement("div");
  timesList.className = "schedule-times-list";

  function addTimeRow(hour, minute) {
    const row = document.createElement("div");
    row.className = "schedule-time-row";
    const hourOptions = Array.from({ length: 24 }, (_, h) => `<option value="${h}">${String(h).padStart(2, "0")}</option>`).join("");
    const minuteOptions = Array.from({ length: 60 }, (_, m) => `<option value="${m}">${String(m).padStart(2, "0")}</option>`).join("");
    row.innerHTML = `
      <select class="schedule-hour">${hourOptions}</select> 시
      <select class="schedule-minute">${minuteOptions}</select> 분
    `;
    row.querySelector(".schedule-hour").value = hour;
    row.querySelector(".schedule-minute").value = minute;
    const removeBtn = makeButton("삭제", () => {
      if (timesList.children.length <= 1) return; // 최소 1개는 남겨야 함
      row.remove();
    });
    removeBtn.className = "schedule-time-remove";
    row.appendChild(removeBtn);
    timesList.appendChild(row);
  }

  for (const t of schedule.cron_times) {
    addTimeRow(t.hour, t.minute);
  }

  const addTimeBtn = makeButton("+ 시각 추가", () => addTimeRow(3, 0));

  const dayCheckboxes = Object.entries(DAY_LABEL)
    .map(
      ([day, label]) => `
      <label class="day-checkbox">
        <input type="checkbox" class="schedule-day" value="${day}" ${schedule.cron_days.includes(day) ? "checked" : ""} />
        ${label}
      </label>`
    )
    .join("");
  const dayRow = document.createElement("div");
  dayRow.innerHTML = `<span class="day-checkbox-group">${dayCheckboxes}<span class="schedule-day-hint">(아무 요일도 안 고르면 매일)</span></span>`;

  cronRow.appendChild(timesList);
  cronRow.appendChild(addTimeBtn);
  cronRow.appendChild(dayRow);

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
  const cronTimes = Array.from(wrap.querySelectorAll(".schedule-time-row")).map((row) => ({
    hour: Number(row.querySelector(".schedule-hour").value) || 0,
    minute: Number(row.querySelector(".schedule-minute").value) || 0,
  }));
  return {
    mode: wrap.querySelector(".schedule-mode").value,
    interval_minutes: Number(wrap.querySelector(".schedule-interval").value) || 60,
    cron_times: cronTimes.length > 0 ? cronTimes : [{ hour: 3, minute: 0 }],
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
const WEBHOOK_URL_MASK = "••••••••••••••••••••••••••••";

async function loadDiscordSettings() {
  try {
    const s = await apiCall("/api/settings/discord");

    const webhookInput = document.getElementById("discord-webhook-url");
    webhookInput.value = s.webhook_url_set ? WEBHOOK_URL_MASK : "";
    webhookInput.dataset.masked = s.webhook_url_set ? "true" : "false";

    document.getElementById("discord-channel-id").value = s.notify_channel_id;

    const tokenInput = document.getElementById("discord-bot-token");
    tokenInput.value = s.bot_token_set ? BOT_TOKEN_MASK : "";
    tokenInput.dataset.masked = s.bot_token_set ? "true" : "false";

    document.getElementById("discord-bot-status").textContent = s.bot_ready ? "🟢 봇 연결됨" : "⚪ 봇 연결 안 됨";
  } catch (e) {
    document.getElementById("discord-save-result").textContent = e.message;
  }
}

document.getElementById("discord-webhook-url").addEventListener("focus", (e) => {
  if (e.target.dataset.masked === "true") {
    e.target.value = "";
    e.target.dataset.masked = "false";
  }
});

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

    const webhookInput = document.getElementById("discord-webhook-url");
    const rawWebhook = webhookInput.value.trim();
    const webhookToSend = rawWebhook === WEBHOOK_URL_MASK ? "" : rawWebhook;

    await apiCall("/api/settings/discord", {
      method: "POST",
      body: JSON.stringify({
        webhook_url: webhookToSend,
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
  loadWebtoonServerUrl();
  loadAuthorAutoRegisterSetting();
}

async function loadAuthorAutoRegisterSetting() {
  try {
    const data = await apiCall("/api/settings/author-auto-register");
    document.getElementById("author-auto-register-toggle").checked = data.enabled;
  } catch (e) {
    // 조용히 무시
  }
}

document.getElementById("btn-save-author-auto-register").addEventListener("click", async () => {
  const resultEl = document.getElementById("author-auto-register-save-result");
  resultEl.textContent = "";
  try {
    await apiCall("/api/settings/author-auto-register", {
      method: "POST",
      body: JSON.stringify({ enabled: document.getElementById("author-auto-register-toggle").checked }),
    });
    resultEl.style.color = "";
    resultEl.textContent = "저장했습니다.";
  } catch (e) {
    resultEl.textContent = e.message;
  }
});


async function loadManualRunPage() {
  await refreshJobStatus();
  startJobPolling();
}

async function loadJobHistoryPage() {
  await loadJobHistoryRetentionDays();
  await loadJobHistory();
}

const JOB_NAME_LABEL = { discovery: "신작 스캔", download: "다운로드", manual: "수동 다운로드", registry: "작가/태그 재동기화", metadata_sync: "메타 동기화", report: "다운로드 리포트" };

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
      const deleteBtn = makeButton("삭제", async (ev) => {
        ev.stopPropagation();
        await apiCall(`/api/jobs/history/${entry.id}`, { method: "DELETE" });
        loadJobHistory();
      });
      deleteBtn.className = "job-history-delete-btn";
      summary.appendChild(deleteBtn);

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

document.getElementById("btn-clear-job-history").addEventListener("click", async () => {
  if (!confirm("실행 이력을 전부 지웁니다. 계속할까요?")) return;
  await apiCall("/api/jobs/history", { method: "DELETE" });
  loadJobHistory();
});

async function loadJobHistoryRetentionDays() {
  try {
    const data = await apiCall("/api/jobs/history/retention-days");
    document.getElementById("job-history-retention-days").value = data.retention_days || "";
  } catch (e) {
    // 조용히 무시
  }
}

document.getElementById("btn-save-job-history-retention").addEventListener("click", async () => {
  const resultEl = document.getElementById("job-history-retention-result");
  resultEl.textContent = "";
  const days = Number(document.getElementById("job-history-retention-days").value) || 0;
  try {
    await apiCall("/api/jobs/history/retention-days", { method: "POST", body: JSON.stringify({ retention_days: days }) });
    resultEl.style.color = "";
    resultEl.textContent = days > 0 ? "저장했습니다." : "자동삭제 껐습니다.";
  } catch (e) {
    resultEl.textContent = e.message;
  }
});

document.getElementById("btn-run-discovery").addEventListener("click", async () => {
  await apiCall("/api/jobs/discovery/run", { method: "POST" });
  await refreshJobStatus();
});

document.getElementById("btn-run-download").addEventListener("click", async () => {
  await apiCall("/api/jobs/download/run", { method: "POST" });
  await refreshJobStatus();
});

document.getElementById("btn-run-metadata-sync").addEventListener("click", async () => {
  await apiCall("/api/metadata/sync", { method: "POST" });
  await refreshJobStatus();
});

document.getElementById("btn-run-report").addEventListener("click", async () => {
  await apiCall("/api/jobs/report/run", { method: "POST" });
  await refreshJobStatus();
});

async function loadWebtoonServerUrl() {
  try {
    const data = await apiCall("/api/settings/webtoon-server");
    document.getElementById("webtoon-server-url").value = data.webtoon_server_url;
  } catch (e) {
    // 조용히 무시 — 이 필드 하나 때문에 설정 탭 전체 로드가 막히면 안 됨
  }
}

document.getElementById("btn-save-webtoon-server-url").addEventListener("click", async () => {
  const resultEl = document.getElementById("webtoon-server-save-result");
  resultEl.textContent = "";
  try {
    const url = document.getElementById("webtoon-server-url").value.trim();
    await apiCall("/api/settings/webtoon-server", {
      method: "POST",
      body: JSON.stringify({ webtoon_server_url: url }),
    });
    resultEl.style.color = "";
    resultEl.textContent = "저장했습니다.";
  } catch (e) {
    resultEl.textContent = e.message;
  }
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

  for (const jobName of ["discovery", "download", "metadata_sync", "report"]) {
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

// ── 다운로드 이력 (회차 단위) ─────────────────────────────

async function loadEpisodeHistory(page) {
  loadRetentionDays();
  const listContainer = document.getElementById("episode-history-list");
  const emptyEl = document.getElementById("episode-history-empty");
  const status = document.getElementById("episode-history-status").value;
  const search = document.getElementById("episode-history-search").value.trim();

  listContainer.innerHTML = "";
  try {
    const params = new URLSearchParams({ page: String(page) });
    if (status) params.set("status", status);
    if (search) params.set("search", search);
    const data = await apiCall(`/api/episode-history?${params.toString()}`);

    emptyEl.classList.toggle("hidden", data.items.length > 0);
    for (const item of data.items) {
      const wrap = document.createElement("div");
      wrap.className = "job-history-entry";
      const timeLabel = formatKoreanTime(item.downloaded_at);
      const statusLabel = item.status === "success" ? "성공" : `실패${item.error_msg ? ` (${item.error_msg})` : ""}`;

      const summary = document.createElement("div");
      summary.className = "job-history-summary";
      summary.innerHTML = `
        <span class="job-history-name">${escapeHtml(item.title_name)}</span>
        <span class="job-history-time">${item.episode_no}화 ${escapeHtml(item.subtitle)} · ${escapeHtml(timeLabel)}</span>
        <span class="badge job-${item.status}">${escapeHtml(statusLabel)}</span>
      `;
      const deleteBtn = makeButton("삭제", async (ev) => {
        ev.stopPropagation();
        await apiCall(`/api/episode-history/${item.id}`, { method: "DELETE" });
        loadEpisodeHistory(page);
      });
      deleteBtn.className = "job-history-delete-btn";
      summary.appendChild(deleteBtn);
      wrap.appendChild(summary);
      listContainer.appendChild(wrap);
    }
    renderEpisodeHistoryPagination(data.page, data.total, data.page_size);
  } catch (e) {
    emptyEl.textContent = e.message;
    emptyEl.classList.remove("hidden");
  }
}

function renderEpisodeHistoryPagination(page, total, pageSize) {
  const container = document.getElementById("episode-history-pagination");
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  container.innerHTML = "";
  if (totalPages <= 1) return;

  if (page > 1) container.appendChild(makeButton("이전", () => loadEpisodeHistory(page - 1)));
  const label = document.createElement("span");
  label.textContent = ` ${page} / ${totalPages} `;
  container.appendChild(label);
  if (page < totalPages) container.appendChild(makeButton("다음", () => loadEpisodeHistory(page + 1)));
}

document.getElementById("episode-history-search").addEventListener("input", () => loadEpisodeHistory(1));
document.getElementById("episode-history-status").addEventListener("change", () => loadEpisodeHistory(1));

document.getElementById("btn-clear-episode-history").addEventListener("click", async () => {
  if (!confirm("다운로드 이력을 전부 지웁니다 (받은 파일은 그대로 유지됩니다). 계속할까요?")) return;
  await apiCall("/api/episode-history", { method: "DELETE" });
  loadEpisodeHistory(1);
});

async function loadRetentionDays() {
  try {
    const data = await apiCall("/api/episode-history/retention-days");
    document.getElementById("episode-history-retention-days").value = data.retention_days || "";
  } catch (e) {
    // 조용히 무시 — 핵심 목록 표시에 지장 없어야 함
  }
}

document.getElementById("btn-save-retention-days").addEventListener("click", async () => {
  const resultEl = document.getElementById("retention-save-result");
  resultEl.textContent = "";
  const input = document.getElementById("episode-history-retention-days");
  const days = Number(input.value) || 0;
  try {
    await apiCall("/api/episode-history/retention-days", {
      method: "POST",
      body: JSON.stringify({ retention_days: days }),
    });
    resultEl.style.color = "";
    resultEl.textContent = days > 0 ? "저장했습니다." : "자동삭제 껐습니다.";
  } catch (e) {
    resultEl.textContent = e.message;
  }
});

// ── 초기 로드 ────────────────────────────────────────────

restoreNaverListPrefs();

const savedTab = sessionStorage.getItem(ACTIVE_TAB_KEY);
// ── 아카이빙 ─────────────────────────────────────────────

let archiveFolderPickerState = {}; // pickerId -> 현재 탐색 중인 경로

// 아카이빙 탭 안에 폴더 선택기가 여러 개(대상 지정용, 기본폴더용, 일괄이동
// 원본/목적지용) 있는데, 각자가 /api/archive/settings와 /api/archive/rclone/remotes를
// 따로 조회하면 화면 하나 열 때 같은 API가 8~10번씩 중복 호출되는 문제가 실제로
// 있었다 — 페이지 방문당 한 번만 조회해서 캐싱하고 재사용한다. loadArchivePage가
// 새로 호출될 때(탭 재방문)만 캐시를 지워서 최신값을 다시 받아온다.
let _archiveSettingsCache = null;
let _rcloneRemotesCache = null;

function invalidateArchiveCaches() {
  _archiveSettingsCache = null;
  _rcloneRemotesCache = null;
}

async function getArchiveSettingsCached() {
  if (!_archiveSettingsCache) {
    _archiveSettingsCache = await apiCall("/api/archive/settings");
  }
  return _archiveSettingsCache;
}

async function getRcloneRemotesCached() {
  if (!_rcloneRemotesCache) {
    _rcloneRemotesCache = await apiCall("/api/archive/rclone/remotes");
  }
  return _rcloneRemotesCache;
}


function _folderPickerStorageKey(containerId) {
  return `folderPickerState:${containerId}`;
}

function saveFolderPickerState(containerId) {
  try {
    sessionStorage.setItem(_folderPickerStorageKey(containerId), JSON.stringify(archiveFolderPickerState[containerId]));
  } catch (e) {
    // 조용히 무시 — sessionStorage를 못 쓰는 환경이어도 기능 자체는 그대로 동작해야 함
  }
}

function loadSavedFolderPickerState(containerId) {
  try {
    const raw = sessionStorage.getItem(_folderPickerStorageKey(containerId));
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    return null;
  }
}

async function renderFolderPicker(containerId, onSelect, initialPath) {
  // 새로고침해도 뭘 보고 있었는지 잃지 않게, sessionStorage에 저장된 상태가
  // 있으면 그걸 우선 복원한다 — 원격 폴더 조회가 느릴 수 있어서, 매번 원격
  // 선택부터 다시 하게 되면 특히 불편하다는 문제가 실제로 있었다.
  const saved = loadSavedFolderPickerState(containerId);
  if (saved) {
    archiveFolderPickerState[containerId] = saved;
    renderFolderPickerContents(containerId, onSelect);
    return;
  }

  // 로컬이 설정 안 돼 있는데 무조건 "local"로 시작하면, 그 즉시 폴더 조회가
  // 실패해서 에러만 뜨고 rclone으로 바꿀 방법이 없어지는 문제가 실제로 있었다 —
  // 그래서 시작 모드를 실제로 뭐가 되는지 먼저 확인해서 정한다.
  let startMode = "local";
  try {
    const archiveSettings = await getArchiveSettingsCached();
    if (!archiveSettings.local_available && archiveSettings.rclone_available) {
      startMode = "rclone";
    }
  } catch (e) {
    // 조용히 무시하고 기본값(local)으로 진행 — 아래에서 다시 확인하고 에러 표시함
  }
  archiveFolderPickerState[containerId] = { mode: startMode, path: initialPath || "", remote: "" };
  renderFolderPickerContents(containerId, onSelect);
}

let _folderListCache = {}; // "local|rclone:remote:path" -> {folders, current_path_selectable}

function _folderListCacheKey(isRclone, remote, path) {
  return `${isRclone ? "rclone" : "local"}:${remote || ""}:${path}`;
}

function _invalidateFolderListCache(isRclone, remote, path) {
  delete _folderListCache[_folderListCacheKey(isRclone, remote, path)];
}

async function renderFolderPickerContents(containerId, onSelect) {
  const container = document.getElementById(containerId);
  const state = archiveFolderPickerState[containerId];
  saveFolderPickerState(containerId);
  // 폴더를 선택하면 목록 전체를 다시 그리는데, 그때마다 스크롤이 맨 위로
  // 돌아가버리면 방금 고른 게 지금 보이는 위치에서 벗어나 있을 수 있어서
  // "선택이 제대로 됐는지" 눈으로 바로 확인하기 어려웠다 — 다시 그리기 전의
  // 스크롤 위치를 기억해뒀다가 그대로 복원한다.
  const prevScrollTop = container.querySelector(".folder-picker-list")?.scrollTop || 0;
  container.innerHTML = '<p class="hint-inline">불러오는 중...</p>';

  let archiveSettings;
  try {
    archiveSettings = await getArchiveSettingsCached();
  } catch (e) {
    container.innerHTML = `<p class="error">${escapeHtml(e.message)}</p>`;
    return;
  }
  container.innerHTML = "";

  // 모드 전환 버튼은 이 함수 안에서 무슨 일이 있어도(목록 조회가 실패하더라도)
  // 항상 살아있어야 한다 — 로컬이 미설정이라 목록 조회가 실패해도, 최소한
  // rclone으로 바꿀 방법은 남아있어야 하기 때문 (실제로 이게 막혀서 오도가도
  // 못하는 문제가 있었다).
  if (archiveSettings.rclone_available && archiveSettings.local_available) {
    const modeRow = document.createElement("div");
    modeRow.className = "folder-picker-mode-row";
    const localBtn = makeButton("로컬 폴더", () => {
      archiveFolderPickerState[containerId] = { mode: "local", path: "", remote: "" };
      renderFolderPickerContents(containerId, onSelect);
    });
    const rcloneBtn = makeButton("rclone 원격", () => {
      archiveFolderPickerState[containerId] = { mode: "rclone", path: "", remote: "" };
      renderFolderPickerContents(containerId, onSelect);
    });
    if (state.mode === "local") localBtn.disabled = true;
    if (state.mode === "rclone") rcloneBtn.disabled = true;
    modeRow.appendChild(localBtn);
    modeRow.appendChild(rcloneBtn);
    container.appendChild(modeRow);
  }

  const listArea = document.createElement("div");
  container.appendChild(listArea);

  if (state.mode === "local" && !archiveSettings.local_available) {
    listArea.innerHTML = '<p class="error">로컬 아카이빙 경로(ARCHIVE_ROOT)가 설정되어 있지 않습니다.</p>';
    return;
  }
  if (state.mode === "rclone" && !archiveSettings.rclone_available) {
    listArea.innerHTML = '<p class="error">rclone 설정 파일이 등록되어 있지 않습니다.</p>';
    return;
  }

  const backToStartBtn = () =>
    makeButton("⬅ 처음으로 돌아가기", () => {
      archiveFolderPickerState[containerId] = { mode: state.mode, path: "", remote: "" };
      renderFolderPickerContents(containerId, onSelect);
    });

  try {
    if (state.mode === "rclone" && !state.remote) {
      // 원격을 아직 안 골랐으면 원격 선택 드롭다운부터
      listArea.innerHTML = '<p class="hint-inline">⏳ 원격 목록을 불러오는 중입니다...</p>';
      const remotesData = await getRcloneRemotesCached();
      listArea.innerHTML = "";
      const remoteRow = document.createElement("div");
      remoteRow.className = "registry-add-row";
      const select = document.createElement("select");
      select.innerHTML = remotesData.remotes.map((r) => `<option value="${escapeHtml(r)}">${escapeHtml(r)}</option>`).join("");
      const chooseBtn = makeButton("이 원격 사용", () => {
        state.remote = select.value;
        renderFolderPickerContents(containerId, onSelect);
      });
      remoteRow.appendChild(select);
      remoteRow.appendChild(chooseBtn);
      listArea.appendChild(remoteRow);
      return;
    }

    const isRclone = state.mode === "rclone";
    const currentPath = state.path || "";
    const cacheKey = _folderListCacheKey(isRclone, state.remote, currentPath);

    let data = _folderListCache[cacheKey];
    if (!data) {
      // 로딩 중에도 무한정 기다리지 않고 벗어날 수 있게, 취소 버튼을 로딩 문구와
      // 함께 바로 보여준다 — 예전엔 요청이 오래 걸리는 동안(특히 원격 저장소가
      // 느리거나 응답이 없을 때) 화면에 아무 것도 못 누르고 새로고침하는 수밖에
      // 없었다. 요청 자체(백엔드의 rclone 실행)를 강제로 멈추진 못하지만, 최소한
      // 화면은 바로 이전 상태로 돌아갈 수 있게 한다.
      const controller = new AbortController();
      listArea.innerHTML = "";
      listArea.appendChild(Object.assign(document.createElement("p"), { className: "hint-inline", textContent: "⏳ 폴더 목록을 불러오는 중입니다... (원격 저장소는 응답이 느릴 수 있습니다)" }));
      const cancelBtn = makeButton("취소하고 돌아가기", () => {
        controller.abort();
        renderFolderPickerContents(containerId, onSelect);
      });
      listArea.appendChild(cancelBtn);

      const url = isRclone
        ? `/api/archive/rclone/folders?remote=${encodeURIComponent(state.remote)}&path=${encodeURIComponent(currentPath)}`
        : `/api/archive/folders?path=${encodeURIComponent(currentPath)}`;
      data = await apiCall(url, { signal: controller.signal });
      _folderListCache[cacheKey] = data;
    }
    listArea.innerHTML = "";

    const pathRow = document.createElement("div");
    pathRow.className = "folder-picker-path";
    pathRow.textContent = isRclone ? `현재 위치: ${state.remote}:/${currentPath}` : `현재 위치: /${currentPath}`;
    listArea.appendChild(pathRow);

    const listBox = document.createElement("div");
    listBox.className = "folder-picker-list";

    if (isRclone) {
      const backBtn = makeButton("⬅ 원격 다시 선택", () => {
        state.remote = "";
        state.path = "";
        renderFolderPickerContents(containerId, onSelect);
      });
      listBox.appendChild(backBtn);
    }
    if (currentPath) {
      const upBtn = makeButton("⬆ 상위 폴더", () => {
        state.path = currentPath.split("/").slice(0, -1).join("/");
        renderFolderPickerContents(containerId, onSelect);
      });
      listBox.appendChild(upBtn);
    }

    function selectAndShow(value, destType, label) {
      state.selectedLabel = label;
      onSelect(value, destType);
      // 방금 고른 걸 화면에서도 바로 보이게 다시 그린다 — 예전엔 onSelect를
      // 호출만 하고 화면엔 아무 표시가 없어서, 실제로 선택이 됐는지 눈으로
      //확인할 방법이 없었다.
      renderFolderPickerContents(containerId, onSelect);
    }

    // 폴더가 비어있는지 확인은, 목록을 그릴 때 전부 미리 하지 않고 사용자가
    // 실제로 "선택"을 누른 폴더 딱 하나에 대해서만 그 자리에서 확인한다 —
    // 예전엔 목록 조회 시점에 하위 폴더 전부를 확인해서, 폴더가 많으면(특히
    // rclone 원격은 폴더 개수만큼 원격에 개별 요청을 보내야 해서) 몇 분씩
    //걸리는 문제가 실제로 있었다.
    async function trySelectFolder(btn, warnHost, folderName, folderPath, destValue, destType) {
      const originalText = btn.textContent;
      btn.textContent = "확인 중...";
      btn.disabled = true;
      try {
        const checkResult = await apiCall(
          `/api/archive/folder-check?dest_type=${destType}&remote=${encodeURIComponent(state.remote || "")}&path=${encodeURIComponent(folderPath)}`
        );
        if (checkResult.selectable) {
          selectAndShow(destValue, destType, folderPath);
          return;
        }
        // 그 자리에서 경고 + "그래도 선택"으로 전환한다 (팝업 없이)
        btn.remove();
        const warnWrap = document.createElement("div");
        warnWrap.className = "folder-picker-warn";
        const warnText = document.createElement("span");
        warnText.textContent = `⚠ "${folderName}"엔 이미 파일이 있습니다.`;
        const proceedBtn = makeButton("그래도 선택", () => selectAndShow(destValue, destType, folderPath));
        warnWrap.appendChild(warnText);
        warnWrap.appendChild(proceedBtn);
        warnHost.appendChild(warnWrap);
      } catch (e) {
        btn.textContent = originalText;
        btn.disabled = false;
        alert(`확인 실패: ${e.message}`);
      }
    }

    for (const folder of data.folders) {
      const row = document.createElement("div");
      row.className = "folder-picker-row";
      const isSelected = state.selectedLabel === folder.path;
      if (isSelected) row.classList.add("folder-picker-row-selected");
      const nameBtn = makeButton(`📁 ${folder.name}${isSelected ? " ✅" : ""}`, () => {
        state.path = folder.path;
        renderFolderPickerContents(containerId, onSelect);
      });
      row.appendChild(nameBtn);
      const destValue = isRclone ? `${state.remote}:${folder.path}` : folder.path;

      const selectBtn = makeButton("이 폴더 선택", () => {
        trySelectFolder(selectBtn, row, folder.name, folder.path, destValue, isRclone ? "rclone" : "local");
      });
      row.appendChild(selectBtn);
      listBox.appendChild(row);
    }
    listArea.appendChild(listBox);
    listBox.scrollTop = prevScrollTop;

    const newFolderRow = document.createElement("div");
    newFolderRow.className = "registry-add-row folder-picker-new-row";
    const newFolderInput = document.createElement("input");
    newFolderInput.type = "text";
    newFolderInput.placeholder = "새 폴더 이름";
    const newFolderBtn = makeButton("새 폴더 만들기", async () => {
      const name = newFolderInput.value.trim();
      if (!name) return;
      const newPath = currentPath ? `${currentPath}/${name}` : name;
      if (isRclone) {
        await apiCall("/api/archive/rclone/folders", { method: "POST", body: JSON.stringify({ remote: state.remote, path: newPath }) });
      } else {
        await apiCall("/api/archive/folders", { method: "POST", body: JSON.stringify({ path: newPath }) });
      }
      _invalidateFolderListCache(isRclone, state.remote, currentPath); // 새 폴더가 생겼으니 이 경로는 다시 조회해야 함
      state.path = newPath;
      renderFolderPickerContents(containerId, onSelect);
    });
    newFolderRow.appendChild(newFolderInput);
    newFolderRow.appendChild(newFolderBtn);
    listArea.appendChild(newFolderRow);
  } catch (e) {
    if (e.name === "AbortError") return; // 사용자가 직접 취소한 것 — 에러로 취급 안 함
    // 조회에 실패한 위치를 계속 기억하고 있으면, 새로고침해도 매번 똑같이
    // 고장난 위치를 다시 열려다 또 실패하는 문제가 실제로 있었다(예: OneDrive의
    // Personal Vault처럼 API로 접근 자체가 원천적으로 안 되는 특수 폴더) —
    // 그래서 실패하면 저장해둔 위치를 지우고, 처음으로 되돌아갈 방법을 준다.
    try {
      sessionStorage.removeItem(_folderPickerStorageKey(containerId));
    } catch (_) {
      // 조용히 무시
    }
    listArea.innerHTML = "";
    const errorMsg = document.createElement("p");
    errorMsg.className = "error";
    errorMsg.textContent = e.message;
    listArea.appendChild(errorMsg);
    listArea.appendChild(backToStartBtn());
  }
}

let archiveSelectedTargetPath = "";
let archiveSelectedTargetDestType = "local";
let archiveSelectedDefaultPath = "";
let archiveSelectedDefaultDestType = "local";
let archiveSelectedBulkSourcePath = "";
let archiveSelectedBulkDestPath = "";

async function loadArchivePage() {
  invalidateArchiveCaches(); // 탭을 새로 열 때마다 최신값을 다시 받아오게 캐시 초기화
  try {
    const settingsCheck = await getArchiveSettingsCached();
    const isAvailable = settingsCheck.local_available || settingsCheck.rclone_available;
    document.getElementById("archive-disabled-guide").classList.toggle("hidden", isAvailable);
    document.getElementById("archive-main-content").classList.toggle("hidden", !isAvailable);
    if (!isAvailable) return;
  } catch (e) {
    // 조용히 무시하고 본문 계속 로드 시도
  }

  await loadArchiveTargetWebtoonOptions();
  renderFolderPicker("archive-target-folder-picker", (path, destType) => {
    archiveSelectedTargetPath = path;
    archiveSelectedTargetDestType = destType;
  });
  renderFolderPicker("archive-default-folder-picker", (path, destType) => {
    archiveSelectedDefaultPath = path;
    archiveSelectedDefaultDestType = destType;
  });
  renderFolderPicker("bulk-move-source-picker", (path) => {
    archiveSelectedBulkSourcePath = path;
  });
  renderFolderPicker("bulk-move-dest-picker", (path) => {
    archiveSelectedBulkDestPath = path;
  });
  await loadArchiveTargetList();
  await loadArchiveSettings();
  await loadArchiveManualSelectList();
  await refreshArchiveJobStatus();
  startArchiveJobPolling();
  await loadArchiveHistory(1);

  try {
    const schedules = await apiCall("/api/settings");
    const block = document.querySelector('.schedule-block[data-job="archive_job"] .schedule-controls');
    block.innerHTML = "";
    block.appendChild(buildScheduleControls("archive_job", schedules["archive_job"]));
  } catch (e) {
    // 조용히 무시
  }
}

async function loadArchiveTargetWebtoonOptions() {
  const select = document.getElementById("archive-target-webtoon-select");
  try {
    const [webtoons, targets] = await Promise.all([
      apiCall("/api/webtoons?status=active"),
      apiCall("/api/archive/targets"),
    ]);
    const registeredIds = new Set(targets.map((t) => t.title_id));
    select.innerHTML = "";
    for (const w of webtoons) {
      if (registeredIds.has(w.title_id)) continue; // 이미 등록된 웹툰은 다시 고를 필요가 없음
      const opt = document.createElement("option");
      opt.value = w.title_id;
      opt.textContent = w.title;
      select.appendChild(opt);
    }
    if (select.options.length === 0) {
      select.innerHTML = '<option value="">등록 가능한 웹툰이 없습니다</option>';
    }
  } catch (e) {
    select.innerHTML = `<option>${escapeHtml(e.message)}</option>`;
  }
}

async function loadArchiveTargetList() {
  const container = document.getElementById("archive-target-list");
  try {
    const targets = await apiCall("/api/archive/targets");
    container.innerHTML = "";
    if (targets.length === 0) {
      container.innerHTML = '<p class="chip-empty-message">지정된 아카이빙 대상이 없습니다.</p>';
      return;
    }
    for (const t of targets) {
      const entry = document.createElement("div");
      entry.className = "job-history-entry";
      const summary = document.createElement("div");
      summary.className = "job-history-summary";
      summary.innerHTML = `
        <span class="job-history-name">${escapeHtml(t.title_name)}</span>
        <span class="job-history-time">${t.dest_type === "rclone" ? "☁️ " : "💾 "}${escapeHtml(t.dest_base_path)}</span>
        <span class="badge">${t.enabled ? "사용중" : "꺼짐"}</span>
      `;
      const toggleBtn = makeButton(t.enabled ? "끄기" : "켜기", async (ev) => {
        ev.stopPropagation();
        await apiCall(`/api/archive/targets/${encodeURIComponent(t.title_id)}/${t.enabled ? "disable" : "enable"}`, { method: "POST" });
        loadArchiveTargetList();
        loadArchiveManualSelectList();
        loadArchiveTargetWebtoonOptions();
      });
      toggleBtn.className = "job-history-delete-btn";
      const deleteBtn = makeButton("삭제", async (ev) => {
        ev.stopPropagation();
        await apiCall(`/api/archive/targets/${encodeURIComponent(t.title_id)}`, { method: "DELETE" });
        loadArchiveTargetList();
        loadArchiveManualSelectList();
        loadArchiveTargetWebtoonOptions();
      });
      deleteBtn.className = "job-history-delete-btn";
      summary.appendChild(toggleBtn);
      summary.appendChild(deleteBtn);
      entry.appendChild(summary);
      container.appendChild(entry);
    }
  } catch (e) {
    container.innerHTML = `<p class="error">${escapeHtml(e.message)}</p>`;
  }
}

document.getElementById("btn-add-archive-target").addEventListener("click", async () => {
  const resultEl = document.getElementById("archive-target-add-result");
  const titleId = document.getElementById("archive-target-webtoon-select").value;
  if (!titleId) return;
  if (!archiveSelectedTargetPath) {
    resultEl.textContent = "폴더를 먼저 선택하세요.";
    return;
  }
  resultEl.textContent = "";
  try {
    await apiCall("/api/archive/targets", {
      method: "POST",
      body: JSON.stringify({ title_id: titleId, dest_base_path: archiveSelectedTargetPath, dest_type: archiveSelectedTargetDestType }),
    });
    resultEl.style.color = "";
    resultEl.textContent = "등록했습니다.";
    loadArchiveTargetList();
    loadArchiveManualSelectList();
    loadArchiveTargetWebtoonOptions();
  } catch (e) {
    resultEl.textContent = e.message;
  }
});

async function loadArchiveSettings() {
  try {
    const data = await getArchiveSettingsCached();
    document.getElementById("archive-on-finish-toggle").checked = data.on_finish_unsubscribe;
    document.getElementById("archive-conflict-policy").value = data.conflict_policy;
    archiveSelectedDefaultPath = data.default_base_path;
    archiveSelectedDefaultDestType = data.default_dest_type;
  } catch (e) {
    // 조용히 무시
  }
}

document.getElementById("btn-save-archive-settings").addEventListener("click", async () => {
  const resultEl = document.getElementById("archive-settings-save-result");
  resultEl.textContent = "";
  try {
    await apiCall("/api/archive/settings", {
      method: "POST",
      body: JSON.stringify({
        default_base_path: archiveSelectedDefaultPath || "",
        default_dest_type: archiveSelectedDefaultDestType,
        conflict_policy: document.getElementById("archive-conflict-policy").value,
        on_finish_unsubscribe: document.getElementById("archive-on-finish-toggle").checked,
      }),
    });
    invalidateArchiveCaches();
    resultEl.style.color = "";
    resultEl.textContent = "저장했습니다.";
  } catch (e) {
    resultEl.textContent = e.message;
  }
});

document.getElementById("btn-save-archive-schedule").addEventListener("click", async () => {
  const resultEl = document.getElementById("archive-schedule-save-result");
  resultEl.textContent = "";
  try {
    const current = await apiCall("/api/settings");
    const archiveControls = document.querySelector('.schedule-block[data-job="archive_job"] .schedule-controls');
    const updated = { ...current, archive_job: readScheduleControls(archiveControls) };
    await apiCall("/api/settings", { method: "POST", body: JSON.stringify(updated) });
    resultEl.textContent = "저장했습니다.";
    resultEl.style.color = "";
  } catch (e) {
    resultEl.textContent = e.message;
  }
});

async function loadArchiveManualSelectList() {
  const container = document.getElementById("archive-manual-select-list");
  try {
    const targets = await apiCall("/api/archive/targets");
    container.innerHTML = "";
    const enabled = targets.filter((t) => t.enabled);
    if (enabled.length === 0) {
      container.innerHTML = '<p class="chip-empty-message">지정된 대상이 없습니다.</p>';
      return;
    }
    for (const t of enabled) {
      const label = document.createElement("label");
      label.className = "chip chip-available";
      label.innerHTML = `<input type="checkbox" value="${escapeHtml(t.title_id)}" style="margin-right:6px;" />${escapeHtml(t.title_name)}`;
      container.appendChild(label);
    }
  } catch (e) {
    container.innerHTML = `<p class="error">${escapeHtml(e.message)}</p>`;
  }
}

document.getElementById("btn-run-archive-now").addEventListener("click", async () => {
  const checked = Array.from(document.querySelectorAll("#archive-manual-select-list input:checked")).map((el) => el.value);
  await apiCall("/api/archive/run", { method: "POST", body: JSON.stringify({ title_ids: checked }) });
  refreshArchiveJobStatus();
});

async function refreshArchiveJobStatus() {
  try {
    const statuses = await apiCall("/api/jobs/status");
    const s = statuses.archive;
    if (!s) return;
    document.getElementById("archive-status-badge").textContent = s.status;
    renderJobLog("archive", s.log || []);
  } catch (e) {
    // 조용히 무시
  }
}

let archiveJobPollTimer = null;

function startArchiveJobPolling() {
  stopArchiveJobPolling();
  archiveJobPollTimer = setInterval(refreshArchiveJobStatus, 2000);
}

function stopArchiveJobPolling() {
  if (archiveJobPollTimer) {
    clearInterval(archiveJobPollTimer);
    archiveJobPollTimer = null;
  }
}

document.getElementById("btn-run-bulk-move").addEventListener("click", async () => {
  const resultEl = document.getElementById("bulk-move-result");
  resultEl.textContent = "";
  if (!archiveSelectedBulkSourcePath || !archiveSelectedBulkDestPath) {
    resultEl.textContent = "원본/목적지 폴더를 모두 선택하세요.";
    return;
  }
  try {
    const data = await apiCall("/api/archive/bulk-move", {
      method: "POST",
      body: JSON.stringify({ source_path: archiveSelectedBulkSourcePath, dest_path: archiveSelectedBulkDestPath }),
    });
    resultEl.style.color = "";
    resultEl.textContent = `${data.moved}개 항목 이동 완료했습니다.`;
    loadArchiveHistory(1);
  } catch (e) {
    resultEl.textContent = e.message;
  }
});

async function loadArchiveHistory(page) {
  loadArchiveHistoryRetentionDays();
  const listContainer = document.getElementById("archive-history-list");
  listContainer.innerHTML = "";
  try {
    const data = await apiCall(`/api/archive/history?page=${page}`);
    if (data.items.length === 0) {
      listContainer.innerHTML = "<p>기록이 없습니다.</p>";
    }
    for (const item of data.items) {
      const triggerLabel = { periodic: "주기적", manual: "수동", finish_unsubscribe: "완결자동", bulk_move: "일괄이동" }[item.trigger_type] || item.trigger_type;
      const wrap = document.createElement("div");
      wrap.className = "job-history-entry";

      const summary = document.createElement("div");
      summary.className = "job-history-summary";
      summary.innerHTML = `
        <span class="job-history-name">${escapeHtml(item.title_name)}</span>
        <span class="job-history-time">${escapeHtml(item.file_name)} · ${escapeHtml(formatKoreanTime(item.archived_at))}</span>
        <span class="badge">${escapeHtml(triggerLabel)}</span>
      `;
      const deleteBtn = makeButton("삭제", async (ev) => {
        ev.stopPropagation();
        await apiCall(`/api/archive/history/${item.id}`, { method: "DELETE" });
        loadArchiveHistory(page);
      });
      deleteBtn.className = "job-history-delete-btn";
      summary.appendChild(deleteBtn);
      wrap.appendChild(summary);
      listContainer.appendChild(wrap);
    }
    renderArchiveHistoryPagination(data.page, data.total, data.page_size);
  } catch (e) {
    listContainer.innerHTML = `<p class="error">${escapeHtml(e.message)}</p>`;
  }
}

function renderArchiveHistoryPagination(page, total, pageSize) {
  const container = document.getElementById("archive-history-pagination");
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  container.innerHTML = "";
  if (totalPages <= 1) return;
  if (page > 1) container.appendChild(makeButton("이전", () => loadArchiveHistory(page - 1)));
  const label = document.createElement("span");
  label.textContent = ` ${page} / ${totalPages} `;
  container.appendChild(label);
  if (page < totalPages) container.appendChild(makeButton("다음", () => loadArchiveHistory(page + 1)));
}

document.getElementById("btn-refresh-archive-history").addEventListener("click", () => loadArchiveHistory(1));

document.getElementById("btn-clear-archive-history").addEventListener("click", async () => {
  if (!confirm("아카이빙 이력을 전부 지웁니다 (실제로 옮겨진 파일은 그대로 유지됩니다). 계속할까요?")) return;
  await apiCall("/api/archive/history", { method: "DELETE" });
  loadArchiveHistory(1);
});

async function loadArchiveHistoryRetentionDays() {
  try {
    const data = await apiCall("/api/archive/history/retention-days");
    document.getElementById("archive-history-retention-days").value = data.retention_days || "";
  } catch (e) {
    // 조용히 무시 — 핵심 목록 표시에 지장 없어야 함
  }
}

document.getElementById("btn-save-archive-history-retention").addEventListener("click", async () => {
  const resultEl = document.getElementById("archive-history-retention-result");
  resultEl.textContent = "";
  const input = document.getElementById("archive-history-retention-days");
  const days = Number(input.value) || 0;
  try {
    await apiCall("/api/archive/history/retention-days", {
      method: "POST",
      body: JSON.stringify({ retention_days: days }),
    });
    resultEl.style.color = "";
    resultEl.textContent = days > 0 ? "저장했습니다." : "자동삭제 껐습니다.";
  } catch (e) {
    resultEl.textContent = e.message;
  }
});


if (savedTab && document.getElementById(`page-${savedTab}`)) {
  switchToTab(savedTab);
} else {
  loadNaverList();
}


