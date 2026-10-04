// 설정: 카카오페이지 로그인 쿠키, 다운로드 폴더(네이버/카카오 따로), 기타 설정
// (index.html에서 파일 이름 순서대로 로드된다 — 클래식 스크립트라 최상위 선언은 전역으로 공유된다)
// ── 카카오페이지 로그인 쿠키 (설정) ─────────────────────────────

function renderKakaoPageCookieStatus(st) {
  const el = document.getElementById("kp-cookie-status");
  if (!st.saved) {
    el.textContent = "저장된 쿠키가 없습니다.";
    return;
  }
  const parts = ["쿠키 저장됨"];
  if (st.missing && st.missing.length) parts.push(`필수 쿠키 누락: ${st.missing.join(", ")}`);
  if (st.days_left !== null && st.days_left !== undefined) {
    parts.push(st.days_left < 0 ? "만료됨(다시 export해서 붙여넣어 주세요)" : `만료까지 약 ${st.days_left}일`);
  }
  el.textContent = parts.join(" · ");
}

async function loadKakaoPageCookieStatus() {
  try {
    renderKakaoPageCookieStatus(await apiCall("/api/settings/kakao-page-login"));
  } catch (e) {
    document.getElementById("kp-cookie-status").textContent = e.message;
  }
}

document.getElementById("btn-kp-cookie-save").addEventListener("click", async () => {
  const box = document.getElementById("kp-cookie-json");
  const statusEl = document.getElementById("kp-cookie-status");
  try {
    const st = await apiCall("/api/settings/kakao-page-login", { method: "POST", body: JSON.stringify({ cookies_json: box.value }) });
    box.value = ""; // 저장했으면 화면에 쿠키가 남아 있지 않게 비운다
    renderKakaoPageCookieStatus(st);
  } catch (e) {
    statusEl.textContent = e.message;
  }
});

document.getElementById("btn-kp-cookie-check").addEventListener("click", async () => {
  const btn = document.getElementById("btn-kp-cookie-check");
  const statusEl = document.getElementById("kp-cookie-status");
  btn.disabled = true;
  statusEl.textContent = "확인 중...";
  try {
    const r = await apiCall("/api/settings/kakao-page-login/check", { method: "POST" });
    renderKakaoPageCookieStatus(r);
    statusEl.textContent = `${r.message} (${statusEl.textContent})`;
  } catch (e) {
    statusEl.textContent = e.message;
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("btn-kp-cookie-delete").addEventListener("click", async () => {
  if (!confirm("저장된 카카오페이지 로그인 쿠키를 삭제할까요? 삭제하면 카카오페이지 다운로드를 할 수 없습니다.")) return;
  try {
    renderKakaoPageCookieStatus(await apiCall("/api/settings/kakao-page-login", { method: "DELETE" }));
  } catch (e) {
    document.getElementById("kp-cookie-status").textContent = e.message;
  }
});

// ── 설정: 다운로드 폴더(네이버/카카오페이지 따로) ─────────────────

function syncDownloadRootsVisibility() {
  document.getElementById("kakao-download-root-group").classList.toggle("hidden", !kakaoWebtoonsEnabled);
}

// 다운로드 폴더는 직접 입력하지 않고, 컨테이너에 마운트된 다운로드 폴더(base) 안에서 고르거나 새로 만든다.
// 폴더 목록/새 폴더 만들기는 아카이빙의 폴더 찾아보기와 같은 API(local_root=download_base)를 쓴다.
let downloadRootsInfo = null;
const downloadRootDraft = { naver: "", kakao: "" }; // 저장 전에 고른 절대 경로("" = 기본)
const downloadRootBrowse = { naver: { open: false, path: "" }, kakao: { open: false, path: "" } };

function downloadHostPath(absPath) {
  const info = downloadRootsInfo;
  if (!info || !info.host_path || !absPath) return "";
  const base = info.base.replace(/\/+$/, "");
  if (absPath !== base && !absPath.startsWith(base + "/")) return "";
  const rest = absPath.slice(base.length).replace(/^\/+/, "");
  if (!rest) return info.host_path;
  const sep = info.host_path.includes("\\") ? "\\" : "/";
  return info.host_path.replace(/[\\/]+$/, "") + sep + rest.split("/").join(sep);
}

function downloadRootLabel(kind) {
  const info = downloadRootsInfo;
  const draft = downloadRootDraft[kind];
  if (draft) return draft;
  return kind === "kakao" ? "네이버와 같은 폴더" : `기본 폴더 (${info.base})`;
}

async function renderDownloadRootPicker(kind) {
  const box = document.getElementById(`${kind}-download-root-picker`);
  const info = downloadRootsInfo;
  const browse = downloadRootBrowse[kind];
  box.innerHTML = "";

  const current = document.createElement("div");
  current.className = "drp-current";
  const shownAbs = downloadRootDraft[kind] || (kind === "kakao" ? downloadRootDraft.naver || info.base : info.base);
  const host = downloadHostPath(shownAbs);
  const pathEl = document.createElement("span");
  pathEl.className = "drp-path";
  // 호스트 경로를 알면 그것을 앞에(사용자가 아는 실제 폴더), 컨테이너 안 경로는 괄호로 덧붙인다
  pathEl.textContent = host ? host : downloadRootLabel(kind);
  current.appendChild(pathEl);
  if (host) {
    const containerEl = document.createElement("span");
    containerEl.className = "drp-host";
    containerEl.textContent = downloadRootDraft[kind] ? `(컨테이너: ${downloadRootDraft[kind]})` : `(컨테이너: ${shownAbs}${kind === "kakao" ? ", 네이버와 같은 폴더" : ", 기본 폴더"})`;
    current.appendChild(containerEl);
  }
  current.appendChild(makeButton(browse.open ? "닫기" : "폴더 선택", () => {
    browse.open = !browse.open;
    renderDownloadRootPicker(kind);
  }));
  if (downloadRootDraft[kind]) {
    current.appendChild(makeButton(kind === "kakao" ? "네이버와 같게" : "기본으로", () => {
      downloadRootDraft[kind] = "";
      renderDownloadRootPicker(kind);
    }));
  }
  box.appendChild(current);
  if (!browse.open) return;

  const panel = document.createElement("div");
  panel.className = "drp-browser";
  const crumbs = document.createElement("div");
  crumbs.className = "drp-crumbs";
  const segments = browse.path ? browse.path.split("/") : [];
  crumbs.appendChild(makeButton(info.host_path || info.base, () => { browse.path = ""; renderDownloadRootPicker(kind); }));
  segments.forEach((name, index) => {
    crumbs.appendChild(document.createTextNode(" / "));
    const target = segments.slice(0, index + 1).join("/");
    crumbs.appendChild(makeButton(name, () => { browse.path = target; renderDownloadRootPicker(kind); }));
  });
  panel.appendChild(crumbs);

  const list = document.createElement("div");
  list.className = "drp-list";
  list.innerHTML = '<span class="hint-inline">불러오는 중...</span>';
  panel.appendChild(list);

  const actions = document.createElement("div");
  actions.className = "drp-actions";
  const absHere = browse.path ? `${info.base}/${browse.path}` : info.base;
  actions.appendChild(makeButton("이 폴더 선택", () => {
    downloadRootDraft[kind] = absHere;
    browse.open = false;
    renderDownloadRootPicker(kind);
  }));
  actions.appendChild(makeButton("새 폴더 만들기", async () => {
    const name = (prompt("새 폴더 이름") || "").trim();
    if (!name) return;
    if (/[\\/]/.test(name)) { alert("폴더 이름에는 / 나 \\ 를 쓸 수 없습니다."); return; }
    try {
      await apiCall("/api/archive/folders", { method: "POST", body: JSON.stringify({ path: browse.path ? `${browse.path}/${name}` : name, root: "download_base" }) });
      renderDownloadRootPicker(kind);
    } catch (e) {
      alert(e.message);
    }
  }));
  panel.appendChild(actions);
  box.appendChild(panel);

  try {
    const data = await apiCall(`/api/archive/folders?path=${encodeURIComponent(browse.path)}&local_root=download_base`);
    list.innerHTML = "";
    if (data.folders.length === 0) list.innerHTML = '<span class="hint-inline">하위 폴더가 없습니다.</span>';
    for (const folder of data.folders) {
      list.appendChild(makeButton(`📁 ${folder.name}`, () => { browse.path = folder.path; renderDownloadRootPicker(kind); }));
    }
  } catch (e) {
    list.innerHTML = `<span class="error">${escapeHtml(e.message)}</span>`;
  }
}

function downloadRootsStatusText(data) {
  const part = (label, side) => (side.effective_host ? `${label}: ${side.effective_host} (컨테이너: ${side.effective})` : `${label}: ${side.effective}`);
  return `현재 받는 폴더 — ${part("네이버", data.naver)}${kakaoWebtoonsEnabled ? ` / ${part("카카오페이지", data.kakao)}` : ""}`;
}

async function loadDownloadRoots() {
  const statusEl = document.getElementById("download-roots-status");
  syncDownloadRootsVisibility();
  try {
    const data = await apiCall("/api/settings/download-roots");
    downloadRootsInfo = data;
    downloadRootDraft.naver = data.naver.path || "";
    downloadRootDraft.kakao = data.kakao.path || "";
    renderDownloadRootPicker("naver");
    renderDownloadRootPicker("kakao");
    statusEl.classList.remove("error");
    statusEl.textContent = downloadRootsStatusText(data);
  } catch (e) {
    statusEl.classList.add("error");
    statusEl.textContent = e.message;
  }
}

document.getElementById("btn-download-roots-save").addEventListener("click", async () => {
  const statusEl = document.getElementById("download-roots-status");
  statusEl.classList.remove("error");
  statusEl.textContent = "";
  try {
    const body = { naver: downloadRootDraft.naver, kakao: downloadRootDraft.kakao };
    const data = await apiCall("/api/settings/download-roots", { method: "POST", body: JSON.stringify(body) });
    downloadRootsInfo = data;
    statusEl.textContent = `저장했습니다. ${downloadRootsStatusText(data)}`;
  } catch (e) {
    statusEl.classList.add("error");
    statusEl.textContent = e.message;
  }
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

async function loadAppPublicBaseUrl() {
  try {
    const data = await apiCall("/api/settings/app-public-base-url");
    document.getElementById("app-public-base-url").value = data.app_public_base_url;
  } catch (e) {
    // 조용히 무시 — 이 필드 하나 때문에 설정 탭 전체 로드가 막히면 안 됨
  }
}

document.getElementById("btn-save-app-public-base-url").addEventListener("click", async () => {
  const resultEl = document.getElementById("app-public-base-url-save-result");
  resultEl.textContent = "";
  try {
    const url = document.getElementById("app-public-base-url").value.trim();
    await apiCall("/api/settings/app-public-base-url", {
      method: "POST",
      body: JSON.stringify({ app_public_base_url: url }),
    });
    resultEl.style.color = "";
    resultEl.textContent = "저장했습니다.";
  } catch (e) {
    resultEl.textContent = e.message;
  }
});

async function loadUnregisteredNewEpisodesToggle() {
  try {
    const data = await apiCall("/api/settings/report-unregistered-new-episodes");
    document.getElementById("report-unregistered-toggle").checked = data.enabled;
  } catch (e) {
    // 조용히 무시 — 이 필드 하나 때문에 설정 탭 전체 로드가 막히면 안 됨
  }
}

document.getElementById("report-unregistered-toggle").addEventListener("change", async (e) => {
  try {
    await apiCall("/api/settings/report-unregistered-new-episodes", {
      method: "POST",
      body: JSON.stringify({ enabled: e.target.checked }),
    });
  } catch (err) {
    alert(err.message);
    e.target.checked = !e.target.checked; // 저장 실패하면 화면도 원래대로 되돌림
  }
});

async function loadKakaoWebtoonsEnabledToggle() {
  try {
    const data = await apiCall("/api/settings/kakao-webtoons-enabled");
    kakaoWebtoonsEnabled = data.enabled;
    document.getElementById("kakao-webtoons-enabled-toggle").checked = data.enabled;
  } catch (e) {
    // 조용히 무시 — 이 필드 하나 때문에 설정 탭 전체 로드가 막히면 안 됨
  }
}

document.getElementById("kakao-webtoons-enabled-toggle").addEventListener("change", async (e) => {
  try {
    await apiCall("/api/settings/kakao-webtoons-enabled", {
      method: "POST",
      body: JSON.stringify({ enabled: e.target.checked }),
    });
    kakaoWebtoonsEnabled = e.target.checked;
    syncDownloadRootsVisibility();
  } catch (err) {
    alert(err.message);
    e.target.checked = !e.target.checked;
  }
});

// 진행 상황 로그(수동 실행/아카이빙/폴더 일괄 이동/수동 다운로드 공통)의 스크롤 규칙:
// 맨 아래를 보고 있으면 새 줄을 따라 내려가고, 위로 올려 읽는 중이면 그 위치를 그대로 두고, 다시 맨 아래(근처)로 내리면
// 따라가기를 다시 시작한다. 2초마다 상태를 받아 오지만 새 줄이 없으면 다시 그리지 않는다(텍스트 선택/스크롤이 풀리지 않게).
const LOG_FOLLOW_THRESHOLD_PX = 24; // 맨 아래에서 이만큼 안쪽이면 "맨 아래를 보고 있다"로 본다
const renderedJobLogs = new Map(); // jobName → 마지막으로 그린 로그(같으면 다시 그리지 않음)

function renderJobLog(jobName, lines) {
  const container = document.getElementById(`${jobName}-log`);
  const signature = lines.join("\n");
  if (renderedJobLogs.get(jobName) === signature) return;
  const firstRender = !renderedJobLogs.has(jobName);
  const following = firstRender || container.scrollHeight - container.scrollTop - container.clientHeight <= LOG_FOLLOW_THRESHOLD_PX;
  const previousTop = container.scrollTop;
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
  renderedJobLogs.set(jobName, signature);
  container.scrollTop = following ? container.scrollHeight : previousTop;
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
