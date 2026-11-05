// 설정: 다운로드 리포트 — 발송하지 않는 시간, 모으는 시간(N분), 새 에피소드 발송 시간, 다운로드 뒤 뷰어 서버 스캔
// (index.html에서 파일 이름 순서대로 로드된다 — 클래식 스크립트라 최상위 선언은 전역으로 공유된다)

const QUIET_DEFAULT = { start_hour: 23, start_minute: 0, end_hour: 6, end_minute: 0, days: [] };

function refreshQuietEmptyHint() {
  document.getElementById("report-quiet-empty").classList.toggle("hidden", document.querySelectorAll("#report-quiet-list .report-quiet-row").length > 0);
}

// 발송하지 않는 시간 한 줄: 시작 시:분 ~ 끝 시:분 + 요일 칩(시작하는 날 기준, 안 고르면 매일) + 삭제
function addQuietRow(item) {
  const row = document.createElement("div");
  row.className = "schedule-time-row report-quiet-row";
  const hourOptions = Array.from({ length: 24 }, (_, h) => `<option value="${h}">${String(h).padStart(2, "0")}</option>`).join("");
  const minuteOptions = Array.from({ length: 60 }, (_, m) => `<option value="${m}">${String(m).padStart(2, "0")}</option>`).join("");
  const dayChips = Object.entries(DAY_LABEL)
    .map(([day, label]) => `<label class="day-chip"><input type="checkbox" class="quiet-day" value="${day}" ${item.days.includes(day) ? "checked" : ""} />${label}</label>`)
    .join("");
  row.innerHTML = `
    <select class="quiet-start-hour">${hourOptions}</select> 시
    <select class="quiet-start-minute">${minuteOptions}</select> 분 ~
    <select class="quiet-end-hour">${hourOptions}</select> 시
    <select class="quiet-end-minute">${minuteOptions}</select> 분
    <span class="schedule-day-chips">${dayChips}</span>
  `;
  row.querySelector(".quiet-start-hour").value = item.start_hour;
  row.querySelector(".quiet-start-minute").value = item.start_minute;
  row.querySelector(".quiet-end-hour").value = item.end_hour;
  row.querySelector(".quiet-end-minute").value = item.end_minute;
  const removeBtn = makeButton("삭제", () => {
    row.remove();
    refreshQuietEmptyHint();
  });
  removeBtn.className = "schedule-time-remove quiet-remove";
  row.appendChild(removeBtn);
  document.getElementById("report-quiet-list").appendChild(row);
  refreshQuietEmptyHint();
}

function readQuietRows() {
  return Array.from(document.querySelectorAll("#report-quiet-list .report-quiet-row")).map((row) => ({
    start_hour: Number(row.querySelector(".quiet-start-hour").value),
    start_minute: Number(row.querySelector(".quiet-start-minute").value),
    end_hour: Number(row.querySelector(".quiet-end-hour").value),
    end_minute: Number(row.querySelector(".quiet-end-minute").value),
    days: Array.from(row.querySelectorAll(".quiet-day:checked")).map((el) => el.value),
  }));
}

// ── 새 에피소드 발송 시간(신규 웹툰 + 새 에피소드를 하루 한 번 보내는 시각) ──
// 디스코드가 설정돼 있고 발송하지 않는 시간이 없을 때만 입력칸이 나온다. 발송하지 않는 시간이 있으면 그 시간이 끝난 뒤 첫 리포트에 같이 나가므로 안내만 보이고,
// 시각을 먼저 정해 뒀다면 "유지 중" 줄로 남겨 사용자가 계속 쓸지 해제할지 정하게 한다.
let listAlertState = { time: null, discord_configured: false, quiet_hours_set: false };

function showListAlertSetting(data) {
  listAlertState = data;
  const hasTime = Boolean(data.time);
  document.getElementById("list-alert-group").classList.toggle("hidden", !data.discord_configured);
  document.getElementById("list-alert-time-edit").classList.toggle("hidden", data.quiet_hours_set);
  document.getElementById("list-alert-kept").classList.toggle("hidden", !(data.quiet_hours_set && hasTime));
  document.getElementById("list-alert-quiet-hint").classList.toggle("hidden", !(data.quiet_hours_set && !hasTime));
  document.getElementById("list-alert-time").value = data.time || "";
  document.getElementById("list-alert-kept-text").textContent = hasTime
    ? `새 에피소드 발송 시간 ${data.time}이(가) 유지되고 있습니다 — 발송하지 않는 시간이 있어도 이 시각에 신규 웹툰과 새 에피소드를 따로 보냅니다.`
    : "";
}

async function loadListAlertSetting() {
  try {
    showListAlertSetting(await apiCall("/api/settings/list-alert"));
  } catch (e) {
    // 조용히 무시 — 다음에 설정 화면을 열 때 다시 읽는다
  }
}

async function saveListAlertTime(time, resultText) {
  const resultEl = document.getElementById("list-alert-save-result");
  resultEl.textContent = "";
  try {
    showListAlertSetting(await apiCall("/api/settings/list-alert", { method: "POST", body: JSON.stringify({ time: time || null }) }));
    resultEl.textContent = resultText;
  } catch (e) {
    resultEl.textContent = e.message;
  }
}

document.getElementById("btn-save-list-alert").addEventListener("click", () => saveListAlertTime(document.getElementById("list-alert-time").value, "저장했습니다."));
document.getElementById("btn-clear-list-alert").addEventListener("click", () => saveListAlertTime(null, ""));

async function loadReportSettings() {
  try {
    const data = await apiCall("/api/settings/report");
    document.getElementById("report-quiet-list").innerHTML = "";
    data.quiet_hours.forEach(addQuietRow);
    refreshQuietEmptyHint();
    document.getElementById("report-batch-minutes").value = data.batch_minutes;
  } catch (e) {
    document.getElementById("report-settings-save-result").textContent = e.message;
  }
  loadListAlertSetting();
  loadViewerScanSettings();
}

document.getElementById("btn-add-report-quiet").addEventListener("click", () => addQuietRow(QUIET_DEFAULT));

document.getElementById("btn-save-report-settings").addEventListener("click", async () => {
  const resultEl = document.getElementById("report-settings-save-result");
  resultEl.textContent = "";
  try {
    const quietRows = readQuietRows();
    // 새 에피소드 발송 시간을 먼저 정해 둔 뒤 발송하지 않는 시간을 처음 설정하는 경우 — 안내하고 기존 발송 시간을 유지할지 묻는다
    const needsListAlertChoice = quietRows.length > 0 && !listAlertState.quiet_hours_set && Boolean(listAlertState.time);
    const keepListAlertTime = needsListAlertChoice
      ? confirm(
          `새 에피소드 발송 시간이 ${listAlertState.time}(으)로 설정되어 있습니다.\n\n발송하지 않는 시간을 설정하면, 발송 시간 설정 없이도 신규 웹툰과 새 에피소드가 발송하지 않는 시간이 끝난 뒤 처음 나가는 다운로드 리포트에 같이 나갑니다.\n\n기존 발송 시간(${listAlertState.time})을 유지할까요?\n[확인] 유지 — ${listAlertState.time}에 따로 보냅니다\n[취소] 해제 — 발송하지 않는 시간이 끝난 뒤 리포트에 같이 보냅니다`
        )
      : true;
    await apiCall("/api/settings/report", {
      method: "POST",
      body: JSON.stringify({ quiet_hours: quietRows, batch_minutes: Number(document.getElementById("report-batch-minutes").value) }),
    });
    if (needsListAlertChoice && !keepListAlertTime) await apiCall("/api/settings/list-alert", { method: "POST", body: JSON.stringify({ time: null }) });
    await loadListAlertSetting(); // 발송하지 않는 시간이 바뀌었으니 시각 입력칸/안내를 다시 정한다
    resultEl.style.color = "";
    resultEl.textContent = "저장했습니다.";
  } catch (e) {
    resultEl.textContent = e.message; // 겹치는 시간대 등 — 어디가 문제인지 그대로 보여 준다(입력한 행은 그대로 둔다)
  }
});

// ── 다운로드 뒤 뷰어 서버 스캔 ─────────────────────────────────────

function showViewerScanSettings(data) {
  document.getElementById("viewer-scan-toggle").checked = data.enabled;
  document.getElementById("viewer-scan-max-wait").value = data.max_wait_minutes;
  const tokenInput = document.getElementById("viewer-scan-token");
  tokenInput.value = "";
  tokenInput.placeholder = data.token_set ? "저장됨(바꾸려면 새 값 입력)" : "연동 토큰 입력(저장된 값 없음)";
}

async function loadViewerScanSettings() {
  try {
    showViewerScanSettings(await apiCall("/api/settings/viewer-scan"));
  } catch (e) {
    // 조용히 무시 — 다음에 설정 화면을 열 때 다시 읽는다
  }
}

async function saveViewerScanSettings(clearToken) {
  const resultEl = document.getElementById("viewer-scan-save-result");
  resultEl.textContent = "";
  try {
    const data = await apiCall("/api/settings/viewer-scan", {
      method: "POST",
      body: JSON.stringify({
        enabled: document.getElementById("viewer-scan-toggle").checked,
        token: document.getElementById("viewer-scan-token").value || null, // 안 적으면 기존 토큰 유지
        clear_token: clearToken,
        max_wait_minutes: Number(document.getElementById("viewer-scan-max-wait").value),
      }),
    });
    showViewerScanSettings(data);
    resultEl.textContent = "저장했습니다.";
  } catch (e) {
    resultEl.textContent = e.message;
  }
}

document.getElementById("btn-save-viewer-scan").addEventListener("click", () => saveViewerScanSettings(false));
document.getElementById("btn-clear-viewer-scan-token").addEventListener("click", () => saveViewerScanSettings(true));
