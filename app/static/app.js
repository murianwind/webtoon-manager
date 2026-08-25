const STATUS_LABEL = {
  active: "구독중",
  unsubscribed: "구독해제",
  excluded: "목록제외",
};

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

// ── 탭 전환 ─────────────────────────────────────────────

const pageLoaders = {
  "naver-list": loadNaverList,
  active: () => loadSubscriptionTab("active"),
  unsubscribed: () => loadSubscriptionTab("unsubscribed"),
  excluded: () => loadSubscriptionTab("excluded"),
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

// ── 네이버 웹툰 전체목록 ─────────────────────────────────

let naverListCache = [];

async function loadNaverList() {
  const grid = document.getElementById("naver-list-grid");
  const emptyMsg = document.getElementById("naver-list-empty");
  // 카드가 이미 떠있으면(탭 재방문 등) 새로고침 중에도 화면을 비우지 않는다 —
  // fetch가 끝나야 교체되므로 깜빡임 없이 그대로 유지된다.
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

function buildNaverListCard(w) {
  const card = document.createElement("div");
  card.className = "webtoon-card";
  card.dataset.titleId = w.title_id;

  const statusBadge = w.status
    ? `<span class="badge ${w.status}">${STATUS_LABEL[w.status] || w.status}</span>`
    : "";

  card.innerHTML = `
    ${w.thumbnail_url ? `<img src="${escapeHtml(w.thumbnail_url)}" alt="" loading="lazy" />` : '<div class="thumb-placeholder"></div>'}
    <div class="webtoon-card-body">
      <div class="webtoon-card-title">${escapeHtml(w.title)}</div>
      <div class="webtoon-card-meta">${escapeHtml(w.author_summary || "")}</div>
      ${statusBadge}
    </div>
    <div class="webtoon-card-actions"></div>
  `;

  const actions = card.querySelector(".webtoon-card-actions");
  if (w.status === "active") {
    // 이미 구독중인 항목은 "목록제외"가 필요 없다 — 구독해제 버튼 하나만 보여준다.
    actions.appendChild(makeButton("구독해제", () => naverListUnsubscribe(w)));
  } else {
    actions.appendChild(makeButton("구독", () => naverListAction(w, "subscribe")));
    actions.appendChild(makeButton("목록제외", () => naverListAction(w, "exclude")));
  }

  return card;
}

function renderNaverList() {
  const grid = document.getElementById("naver-list-grid");
  const emptyMsg = document.getElementById("naver-list-empty");
  const query = document.getElementById("naver-list-search").value.trim().toLowerCase();

  // 목록제외한 웹툰은 전체목록에서 아예 보이지 않고 "제외됨" 탭에서만 보인다.
  const visible = naverListCache.filter((w) => w.status !== "excluded");
  const filtered = query ? visible.filter((w) => w.title.toLowerCase().includes(query)) : visible;

  grid.innerHTML = "";
  emptyMsg.classList.toggle("hidden", filtered.length > 0);
  for (const w of filtered) {
    grid.appendChild(buildNaverListCard(w));
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
  const { title_id: titleId } = webtoon;
  try {
    const updated = await apiCall(`/api/webtoons/${titleId}/unsubscribe`, { method: "POST" });
    patchNaverListCard(titleId, webtoon, updated.status);
  } catch (e) {
    alert(e.message);
  }
}

function patchNaverListCard(titleId, webtoon, newStatus) {
  // 서버를 다시 조회하지 않고, 캐시와 화면에서 이 카드 하나만 즉시 반영한다
  // (전체 목록을 다시 그리면 모든 이미지가 리로드되며 깜빡인다).
  const cacheIndex = naverListCache.findIndex((w) => w.title_id === titleId);
  if (cacheIndex >= 0) naverListCache[cacheIndex] = { ...naverListCache[cacheIndex], status: newStatus };

  const card = document.querySelector(`#naver-list-grid .webtoon-card[data-title-id="${titleId}"]`);
  if (newStatus === "excluded") {
    card?.remove();
    const grid = document.getElementById("naver-list-grid");
    document.getElementById("naver-list-empty").classList.toggle("hidden", grid.children.length > 0);
  } else if (card) {
    card.replaceWith(buildNaverListCard({ ...webtoon, status: newStatus }));
  }
}

document.getElementById("btn-refresh-naver-list").addEventListener("click", loadNaverList);
document.getElementById("naver-list-search").addEventListener("input", renderNaverList);

// ── 구독중 / 구독해제 / 제외됨 ─────────────────────────────

async function loadSubscriptionTab(status) {
  const listEl = document.getElementById(`${status}-list`);
  const emptyEl = document.getElementById(`${status}-empty`);
  if (listEl.children.length === 0) {
    listEl.innerHTML = "<p>불러오는 중...</p>";
  }
  try {
    const rows = await apiCall(`/api/webtoons?status=${status}`);
    renderSubscriptionTab(status, rows);
  } catch (e) {
    if (listEl.children.length === 0) {
      emptyEl.textContent = `불러오지 못했습니다: ${e.message}`;
      emptyEl.classList.remove("hidden");
    }
  }
}

function buildSubscriptionCard(status, w) {
  const card = document.createElement("div");
  card.className = "webtoon-card";
  card.dataset.titleId = w.title_id;

  const metaParts = [`${w.last_downloaded_no}화까지`];
  if (w.is_finished) metaParts.push("완결");
  if (w.is_adult) metaParts.push("🔞");

  card.innerHTML = `
    ${w.thumbnail_url ? `<img src="${escapeHtml(w.thumbnail_url)}" alt="" loading="lazy" />` : '<div class="thumb-placeholder"></div>'}
    <div class="webtoon-card-body">
      <div class="webtoon-card-title">${escapeHtml(w.title)}</div>
      <div class="webtoon-card-meta">${escapeHtml(metaParts.join(" · "))}</div>
    </div>
    <div class="webtoon-card-actions"></div>
  `;

  const actions = card.querySelector(".webtoon-card-actions");
  if (status !== "active") {
    actions.appendChild(makeButton("구독", () => subscriptionAction(w.title_id, "subscribe", status)));
  }
  if (status === "active") {
    actions.appendChild(makeButton("구독해제", () => subscriptionAction(w.title_id, "unsubscribe", status)));
  }

  return card;
}

function renderSubscriptionTab(status, rows) {
  const listEl = document.getElementById(`${status}-list`);
  const emptyEl = document.getElementById(`${status}-empty`);
  listEl.innerHTML = "";
  emptyEl.classList.toggle("hidden", rows.length > 0);
  for (const w of rows) {
    listEl.appendChild(buildSubscriptionCard(status, w));
  }
}

async function subscriptionAction(titleId, action, currentTab) {
  try {
    await apiCall(`/api/webtoons/${titleId}/${action}`, { method: "POST" });
    // 이 액션은 항상 현재 탭에서 다른 탭으로 이동하는 동작이라, 서버를 다시
    // 조회하지 않고 카드만 바로 지운다 (깜빡임 없음).
    const listEl = document.getElementById(`${currentTab}-list`);
    const card = listEl.querySelector(`.webtoon-card[data-title-id="${titleId}"]`);
    card?.remove();
    document.getElementById(`${currentTab}-empty`).classList.toggle("hidden", listEl.children.length > 0);
  } catch (e) {
    alert(e.message);
  }
}

// ── 설정 ────────────────────────────────────────────────

let jobPollTimer = null;

const DAY_LABEL = { mon: "월", tue: "화", wed: "수", thu: "목", fri: "금", sat: "토", sun: "일" };
const SCHEDULE_JOB_IDS = ["discovery_job", "download_job", "commands_job"];

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
  await refreshJobStatus();
  startJobPolling();
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

loadNaverList();
