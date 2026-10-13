// 설정: 수동 실행 + 진행상황, 실행 이력
// (index.html에서 파일 이름 순서대로 로드된다 — 클래식 스크립트라 최상위 선언은 전역으로 공유된다)
// ── 설정: 수동 실행 + 진행상황 ────────────────────────────

let jobPollTimer = null;

let helpLoaded = false;

async function loadHelpPage() {
  // README는 앱을 쓰다가 바뀔 일이 없어서, 탭을 열 때마다 다시 안 받고 세션 중엔
  // 한 번만 가져온다.
  if (helpLoaded) return;
  const container = document.getElementById("help-content");
  try {
    const res = await fetch("/api/help");
    if (!res.ok) throw new Error(`불러오기 실패 (${res.status})`);
    container.innerHTML = await res.text();
    helpLoaded = true;
  } catch (e) {
    container.innerHTML = `<p>도움말을 불러오지 못했습니다: ${escapeHtml(e.message)}</p>`;
  }
}

async function loadSettingsPage() {
  try {
    const schedules = await apiCall("/api/settings");
    for (const jobId of SCHEDULE_JOB_IDS) {
      const block = document.querySelector(`.schedule-block[data-job="${jobId}"] .schedule-controls`);
      block.innerHTML = "";
      block.appendChild(jobId === "download_job" ? buildDownloadScheduleEditor(schedules[jobId]) : buildScheduleControls(jobId, schedules[jobId]));
    }
  } catch (e) {
    document.getElementById("settings-save-result").textContent = e.message;
  }
  loadDiscordSettings();
  loadWebtoonServerUrl();
  loadAppPublicBaseUrl();
  loadUnregisteredNewEpisodesToggle();
  loadKakaoWebtoonsEnabledToggle();
  loadKakaoPageCookieStatus();
  loadKakaoRentalAutoSetting();
  loadDownloadPacing();
  loadDownloadRoots();
  loadAuthorAutoRegisterSetting();
  loadUnsubscribeHistoryList();
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

const HISTORY_STATUS_LABEL = { unsubscribed: "구독해제", unregistered: "미등록(전체목록에만)", excluded: "제외됨" };

async function loadUnsubscribeHistoryList() {
  const listEl = document.getElementById("unsubscribe-history-list");
  try {
    const rows = await apiCall("/api/webtoons/history-only");
    listEl.innerHTML = "";
    if (rows.length === 0) {
      listEl.innerHTML = '<p class="chip-empty-message">초기화할 이력이 없습니다.</p>';
      return;
    }
    for (const w of rows) {
      const entry = document.createElement("div");
      entry.className = "job-history-entry";
      const summary = document.createElement("div");
      summary.className = "job-history-summary";
      const nameSpan = document.createElement("span");
      nameSpan.className = "job-history-name";
      nameSpan.textContent = w.title;
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = HISTORY_STATUS_LABEL[w.status] || w.status;
      summary.appendChild(nameSpan);
      summary.appendChild(badge);
      const resetBtn = makeButton("초기화", async () => {
        if (!confirm(`"${w.title}"의 구독 이력을 초기화합니다 (되돌릴 수 없음). 계속할까요?`)) return;
        try {
          await apiCall(`/api/webtoons/${w.title_id}`, { method: "DELETE" });
          entry.remove();
        } catch (e) {
          alert(e.message);
        }
      });
      summary.appendChild(resetBtn);
      entry.appendChild(summary);
      listEl.appendChild(entry);
    }
  } catch (e) {
    listEl.innerHTML = `<p class="error">${escapeHtml(e.message)}</p>`;
  }
}

document.getElementById("btn-register-unsubscribed").addEventListener("click", async () => {
  const resultEl = document.getElementById("register-unsubscribed-result");
  const titleId = document.getElementById("register-unsubscribed-title-id").value.trim();
  if (!titleId) return;
  resultEl.textContent = "";
  try {
    const wt = await apiCall(`/api/webtoons/${titleId}/register-unsubscribed`, { method: "POST" });
    resultEl.style.color = "";
    resultEl.textContent = `"${wt.title}"을(를) 구독해제 상태로 등록했습니다.`;
    document.getElementById("register-unsubscribed-title-id").value = "";
    loadUnsubscribeHistoryList();
  } catch (e) {
    resultEl.textContent = e.message;
  }
});


async function loadManualRunPage() {
  await refreshJobStatus();
  startJobPolling();
  loadArchiveManualSelectList();
  await resumeArchiveJobStatusIfRunning();
}

async function loadHistoryPage() {
  // 다운로드/실행/아카이빙 이력을 한 탭에 모아뒀으니, 세 개를 한 번에 로드한다.
  loadEpisodeHistory(1);
  loadJobHistoryPage();
  loadArchiveHistory(1);
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
  const target = document.getElementById("run-download-target").value;
  await apiCall(`/api/jobs/download/run${target ? `?target=${target}` : ""}`, { method: "POST" });
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

// ── 다운로드 속도 조절(작품당 한 번에 받을 회차 / 배치 사이 쉬는 시간) ─────────────────────

function renderDownloadPacing(data) {
  document.getElementById("pacing-cap").value = data.max_new_episodes_per_title;
  document.getElementById("pacing-rest").value = data.batch_rest_minutes;
  document.getElementById("btn-reset-pacing").disabled = !data.customized;
  document.getElementById("pacing-default-note").textContent =
    `기본값: 작품당 ${data.default_max_new_episodes_per_title}회차, 쉬는 시간 ${data.default_batch_rest_minutes}분` + (data.customized ? " (지금은 바꾼 값을 쓰는 중)" : " (지금은 기본값을 쓰는 중)");
}

async function loadDownloadPacing() {
  try {
    renderDownloadPacing(await apiCall("/api/settings/download-pacing"));
  } catch (e) {
    // 조용히 무시
  }
}

// 서버로 보내기 전에 입력을 검사한다(서버도 같은 범위를 검사하므로, 여기서는 빠른 안내용). 맞으면 null, 아니면 이유.
function validateDownloadPacingInput(capText, restText) {
  const cap = Number(capText);
  if (capText.trim() === "" || !Number.isInteger(cap) || cap < 0 || cap > 200) return "작품당 한 번에 받을 회차는 0~200 사이의 정수여야 합니다(0은 제한 없음).";
  const rest = Number(restText);
  if (restText.trim() === "" || Number.isNaN(rest) || rest < 0.5 || rest > 120) return "쉬는 시간은 0.5~120분(30초 이상) 사이여야 합니다.";
  return null;
}

document.getElementById("btn-save-pacing").addEventListener("click", async () => {
  const resultEl = document.getElementById("pacing-save-result");
  const capText = document.getElementById("pacing-cap").value;
  const restText = document.getElementById("pacing-rest").value;
  const problem = validateDownloadPacingInput(capText, restText);
  if (problem) {
    resultEl.textContent = problem;
    return;
  }
  resultEl.textContent = "";
  try {
    const data = await apiCall("/api/settings/download-pacing", {
      method: "POST",
      body: JSON.stringify({ max_new_episodes_per_title: Number(capText), batch_rest_minutes: Number(restText) }),
    });
    renderDownloadPacing(data);
    resultEl.textContent = "저장했습니다. 다음 배치부터 바로 적용됩니다.";
  } catch (e) {
    resultEl.textContent = e.message;
  }
});

document.getElementById("btn-reset-pacing").addEventListener("click", async () => {
  const resultEl = document.getElementById("pacing-save-result");
  resultEl.textContent = "";
  try {
    renderDownloadPacing(await apiCall("/api/settings/download-pacing", { method: "DELETE" }));
    resultEl.textContent = "기본값으로 되돌렸습니다.";
  } catch (e) {
    resultEl.textContent = e.message;
  }
});

