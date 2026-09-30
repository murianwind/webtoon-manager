// 설정: 실행 스케줄(다운로드는 여러 개 + 대상 선택)
// (index.html에서 파일 이름 순서대로 로드된다 — 클래식 스크립트라 최상위 선언은 전역으로 공유된다)
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

// 다운로드 스케줄은 여러 개 등록할 수 있고, 스케줄마다 받을 대상(네이버/카카오페이지/둘 다)을 고른다.
// 카카오페이지 관리를 켜지 않았으면 대상 선택은 숨기고 네이버로 둔다.
const DOWNLOAD_TARGET_OPTIONS = [["naver", "네이버"], ["kakao", "카카오페이지"], ["both", "네이버 + 카카오페이지"]];

function buildDownloadScheduleEditor(entries) {
  const wrap = document.createElement("div");
  const list = document.createElement("div");
  const emptyHint = document.createElement("p");
  emptyHint.className = "hint";
  emptyHint.textContent = "등록된 다운로드 스케줄이 없습니다. 다운로드가 자동으로 실행되지 않습니다(수동 실행은 가능).";

  function refreshEmptyHint() {
    emptyHint.classList.toggle("hidden", list.children.length > 0);
  }

  function addEntry(schedule) {
    const entry = document.createElement("div");
    entry.className = "schedule-entry";
    const head = document.createElement("div");
    head.className = "schedule-entry-head";
    const targetSelect = document.createElement("select");
    targetSelect.className = "schedule-target";
    targetSelect.innerHTML = DOWNLOAD_TARGET_OPTIONS.map(([value, label]) => `<option value="${value}">${label}</option>`).join("");
    targetSelect.value = schedule.target || "naver";
    const label = document.createElement("span");
    label.textContent = "받을 대상";
    label.classList.toggle("hidden", !kakaoWebtoonsEnabled);
    targetSelect.classList.toggle("hidden", !kakaoWebtoonsEnabled);
    const removeBtn = makeButton("스케줄 삭제", () => {
      entry.remove();
      refreshEmptyHint();
    });
    removeBtn.className = "schedule-entry-remove";
    head.append(label, targetSelect, removeBtn);
    entry.append(head, buildScheduleControls("download_job", schedule));
    list.appendChild(entry);
    refreshEmptyHint();
  }

  entries.forEach(addEntry);
  const addBtn = makeButton("+ 다운로드 스케줄 추가", () =>
    addEntry({ mode: "interval", interval_minutes: 60, cron_times: [{ hour: 3, minute: 0 }], cron_days: [], target: kakaoWebtoonsEnabled ? "kakao" : "naver" })
  );
  wrap.append(list, emptyHint, addBtn);
  return wrap;
}

function readDownloadScheduleEditor(wrap) {
  return Array.from(wrap.querySelectorAll(".schedule-entry")).map((entry) => ({
    ...readScheduleControls(entry),
    target: entry.querySelector(".schedule-target").value,
  }));
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

async function saveAllSchedules(resultElId) {
  // API가 4개 작업(신작스캔/다운로드/리포트/아카이빙) 스케줄을 한 번에 같이 받는
  // 구조라("실행 스케줄" 저장, "다운로드 리포트"의 "발송 시각" 저장 둘 다 공용으로
  // 쓴다), 어느 버튼에서 눌러도 지금 화면(카드 위치와 무관하게 data-job로 찾음)에
  // 있는 4개 값을 전부 모아서 같이 보낸다 — 저장 버튼이 물리적으로 다른 카드에
  // 있어도 실제로 저장되는 내용은 항상 최신 값 그대로다.
  const resultEl = document.getElementById(resultElId);
  resultEl.textContent = "";
  try {
    const payload = {};
    for (const jobId of SCHEDULE_JOB_IDS) {
      const wrap = document.querySelector(`.schedule-block[data-job="${jobId}"] .schedule-controls`);
      payload[jobId] = jobId === "download_job" ? readDownloadScheduleEditor(wrap) : readScheduleControls(wrap);
    }
    await apiCall("/api/settings", { method: "POST", body: JSON.stringify(payload) });
    resultEl.style.color = "";
    resultEl.textContent = "저장했습니다.";
  } catch (e) {
    resultEl.textContent = e.message;
  }
}

document.getElementById("btn-save-settings").addEventListener("click", () => saveAllSchedules("settings-save-result"));
document.getElementById("btn-save-report-schedule").addEventListener("click", () => saveAllSchedules("report-schedule-save-result"));
