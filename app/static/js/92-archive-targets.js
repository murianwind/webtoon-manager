// 아카이빙: 대상 목록(웹툰 + 폴더)과 이력
// (index.html에서 파일 이름 순서대로 로드된다 — 클래식 스크립트라 최상위 선언은 전역으로 공유된다)
// ── 아카이빙 대상 목록 (웹툰 + 폴더) ────────────────────────

function updateArchiveTargetBulkBar() {
  const bar = document.getElementById("archive-target-bulk-bar");
  const count = archiveTargetSelectedIds.size;
  bar.classList.toggle("hidden", count === 0);
  document.getElementById("archive-target-bulk-count").textContent = `${count}개 선택됨`;
}

document.getElementById("btn-apply-preset-to-selected").addEventListener("click", async () => {
  const presetValue = document.getElementById("archive-target-bulk-preset-select").value;
  const presetId = presetValue ? Number(presetValue) : null;
  try {
    await apiCall("/api/archive/targets/apply-preset", {
      method: "POST",
      body: JSON.stringify({ target_ids: [...archiveTargetSelectedIds], preset_id: presetId }),
    });
    archiveTargetSelectedIds = new Set();
    updateArchiveTargetBulkBar();
    loadArchiveTargetList();
  } catch (e) {
    alert(e.message);
  }
});

async function loadArchiveTargetList() {
  const container = document.getElementById("archive-target-list");
  try {
    const targets = await apiCall("/api/archive/targets");
    container.innerHTML = "";
    if (targets.length === 0) {
      container.innerHTML = '<p class="chip-empty-message">지정된 아카이빙 대상이 없습니다.</p>';
      updateArchiveTargetBulkBar();
      return;
    }
    for (const t of targets) {
      const entry = document.createElement("div");
      entry.className = "job-history-entry";
      const summary = document.createElement("div");
      summary.className = "job-history-summary archive-target-row";

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = archiveTargetSelectedIds.has(t.title_id);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) archiveTargetSelectedIds.add(t.title_id);
        else archiveTargetSelectedIds.delete(t.title_id);
        updateArchiveTargetBulkBar();
      });
      summary.appendChild(checkbox);

      const typeIcon = document.createElement("span");
      typeIcon.textContent = t.source_type === "folder" ? "📁" : "📖";
      summary.appendChild(typeIcon);

      const nameSpan = document.createElement("span");
      nameSpan.className = "job-history-name";
      nameSpan.textContent = archiveTargetLabel(t);
      summary.appendChild(nameSpan);

      const locationSpan = document.createElement("span");
      locationSpan.className = "job-history-time";
      const sourceLabel = t.source_type === "folder" ? `${t.source_dest_type === "rclone" ? "☁️" : "💾"} ${t.source_path} → ` : "";
      locationSpan.textContent = `${sourceLabel}${t.dest_type === "rclone" ? "☁️ " : "💾 "}${t.dest_base_path}`;
      summary.appendChild(locationSpan);

      const presetBadge = document.createElement("span");
      presetBadge.className = "archive-target-preset-badge";
      presetBadge.textContent = t.filename_template_preset_name || "기본";
      summary.appendChild(presetBadge);

      const statusBadge = document.createElement("span");
      statusBadge.className = "badge";
      statusBadge.textContent = t.enabled ? "사용중" : "꺼짐";
      summary.appendChild(statusBadge);

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
        archiveTargetSelectedIds.delete(t.title_id);
        loadArchiveTargetList();
        loadArchiveManualSelectList();
        loadArchiveTargetWebtoonOptions();
      });
      deleteBtn.className = "job-history-delete-btn";
      const editBtn = makeButton("수정", (ev) => {
        ev.stopPropagation();
        enterArchiveTargetEditMode(t);
      });
      editBtn.className = "job-history-delete-btn";
      summary.appendChild(editBtn);
      summary.appendChild(toggleBtn);
      summary.appendChild(deleteBtn);
      entry.appendChild(summary);
      container.appendChild(entry);
    }
    updateArchiveTargetBulkBar();
  } catch (e) {
    container.innerHTML = `<p class="error">${escapeHtml(e.message)}</p>`;
  }
}

document.getElementById("btn-add-archive-target").addEventListener("click", async () => {
  const resultEl = document.getElementById("archive-target-add-result");
  const titleId = archiveEditingTitleId || document.getElementById("archive-target-webtoon-select").value;
  if (!titleId) return;
  if (!archiveSelectedTargetPath) {
    resultEl.textContent = "폴더를 먼저 선택하세요.";
    return;
  }
  resultEl.textContent = "";
  const presetValue = document.getElementById("archive-target-webtoon-preset-select").value;
  try {
    await apiCall("/api/archive/targets", {
      method: "POST",
      body: JSON.stringify({
        title_id: titleId, dest_base_path: archiveSelectedTargetPath, dest_type: archiveSelectedTargetDestType,
        filename_template_preset_id: presetValue ? Number(presetValue) : null,
      }),
    });
    resultEl.style.color = "";
    resultEl.textContent = archiveEditingTitleId ? "수정했습니다." : "등록했습니다.";
    exitArchiveTargetEditMode();
    loadArchiveTargetList();
    loadArchiveManualSelectList();
    loadArchiveTargetWebtoonOptions();
  } catch (e) {
    resultEl.textContent = e.message;
  }
});

document.getElementById("btn-add-folder-archive-target").addEventListener("click", async () => {
  const resultEl = document.getElementById("archive-folder-target-add-result");
  if (!archiveSelectedFolderTargetSourcePath) {
    resultEl.textContent = "원본 폴더를 먼저 선택하세요.";
    return;
  }
  if (!archiveSelectedFolderTargetDestPath) {
    resultEl.textContent = "보관할 폴더를 먼저 선택하세요.";
    return;
  }
  resultEl.textContent = "";
  const presetValue = document.getElementById("archive-folder-target-preset-select").value;
  const payload = {
    display_name: document.getElementById("archive-folder-target-display-name").value.trim(),
    source_dest_type: archiveSelectedFolderTargetSourceType,
    source_path: archiveSelectedFolderTargetSourcePath,
    dest_base_path: archiveSelectedFolderTargetDestPath,
    dest_type: archiveSelectedFolderTargetDestType,
    filename_template_preset_id: presetValue ? Number(presetValue) : null,
  };
  try {
    if (archiveEditingFolderTargetId) {
      await apiCall(`/api/archive/folder-targets/${archiveEditingFolderTargetId}`, { method: "POST", body: JSON.stringify(payload) });
      resultEl.textContent = "수정했습니다.";
    } else {
      await apiCall("/api/archive/folder-targets", { method: "POST", body: JSON.stringify(payload) });
      resultEl.textContent = "등록했습니다.";
    }
    resultEl.style.color = "";
    exitFolderArchiveTargetEditMode();
    loadArchiveTargetList();
    loadArchiveManualSelectList();
    loadArchiveTemplatePreviewTitleList();
  } catch (e) {
    resultEl.textContent = e.message;
  }
});

async function loadArchiveSettings() {
  try {
    const data = await getArchiveSettingsCached();
    document.getElementById("archive-on-finish-toggle").checked = data.on_finish_unsubscribe;
    document.getElementById("archive-conflict-policy").value = data.conflict_policy;
    archiveGlobalFilenameTemplate = data.filename_template || "";
    document.getElementById("archive-filename-template").value = archiveGlobalFilenameTemplate;
    await loadArchiveTemplatePreviewTitleList();
    renderFolderPicker(
      "archive-filename-template-preview-folder-picker",
      (path, destType) => {
        archivePreviewFolderPath = path;
        archivePreviewFolderDestType = destType;
        updateArchiveFilenameTemplatePreview();
      },
      "",
      { skipExistingCheck: true }
    );
    updateArchiveFilenameTemplatePreview();
    archiveSelectedDefaultPath = data.default_base_path;
    archiveSelectedDefaultDestType = data.default_dest_type;
  } catch (e) {
    // 조용히 무시
  }
}

// 서버에 있는 실제 웹툰/폴더 대상의 실제 zip 파일 하나로 미리보기한다 — 예시 값보다
// 훨씬 신뢰도 높은 미리보기라서, 하드코딩된 샘플 값 방식을 대체한다.
let archiveTemplatePreviewDebounceTimer = null;

async function loadArchiveTemplatePreviewTitleList() {
  const select = document.getElementById("archive-filename-template-preview-title");
  try {
    const webtoons = await apiCall("/api/webtoons?status=active");
    const previousValue = select.value;
    select.innerHTML = "";
    if (webtoons.length === 0) {
      select.innerHTML = '<option value="">구독 중인 웹툰이 없습니다</option>';
      return;
    }
    for (const wt of webtoons) {
      const opt = document.createElement("option");
      opt.value = wt.title_id;
      opt.textContent = wt.title;
      select.appendChild(opt);
    }
    if (previousValue && [...select.options].some((o) => o.value === previousValue)) {
      select.value = previousValue;
    }
  } catch (e) {
    select.innerHTML = '<option value="">목록을 불러오지 못했습니다</option>';
  }
}

let archivePreviewType = "webtoon";
let archivePreviewFolderPath = "";
let archivePreviewFolderDestType = "local";

document.querySelectorAll(".archive-preview-type-tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    archivePreviewType = btn.dataset.type;
    document.querySelectorAll(".archive-preview-type-tab").forEach((b) => b.classList.toggle("active", b === btn));
    document.getElementById("archive-filename-template-preview-title").classList.toggle("hidden", archivePreviewType !== "webtoon");
    document.getElementById("archive-filename-template-preview-folder-picker").classList.toggle("hidden", archivePreviewType !== "folder");
    updateArchiveFilenameTemplatePreview();
  });
});

async function updateArchiveFilenameTemplatePreview() {
  const template = document.getElementById("archive-filename-template").value;
  const previewEl = document.getElementById("archive-filename-template-preview");
  let payload;
  if (archivePreviewType === "folder") {
    if (!archivePreviewFolderPath) {
      previewEl.textContent = "미리보기할 폴더를 선택하세요.";
      return;
    }
    payload = { source_type: "folder", source_dest_type: archivePreviewFolderDestType, source_path: archivePreviewFolderPath, template };
  } else {
    const titleId = document.getElementById("archive-filename-template-preview-title").value;
    if (!titleId) {
      previewEl.textContent = "미리보기할 웹툰을 선택하세요.";
      return;
    }
    payload = { source_type: "webtoon", title_id: titleId, template };
  }
  previewEl.textContent = "확인 중...";
  try {
    const result = await apiCall("/api/archive/preview-filename", { method: "POST", body: JSON.stringify(payload) });
    if (!result.original_filename) {
      previewEl.textContent = result.message;
    } else if (result.rendered_filename) {
      previewEl.textContent = `${result.original_filename}  →  ${result.rendered_filename}`;
    } else {
      previewEl.textContent = `${result.original_filename} (${result.message})`;
    }
  } catch (e) {
    previewEl.textContent = e.message;
  }
}

function scheduleArchiveFilenameTemplatePreview() {
  clearTimeout(archiveTemplatePreviewDebounceTimer);
  archiveTemplatePreviewDebounceTimer = setTimeout(updateArchiveFilenameTemplatePreview, 400);
}

document.getElementById("archive-filename-template").addEventListener("input", scheduleArchiveFilenameTemplatePreview);
document.getElementById("archive-filename-template-preview-title").addEventListener("change", updateArchiveFilenameTemplatePreview);

document.querySelectorAll("#archive-filename-template-tokens .token-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    const input = document.getElementById("archive-filename-template");
    const token = btn.dataset.token;
    const start = input.selectionStart ?? input.value.length;
    const end = input.selectionEnd ?? input.value.length;
    input.value = input.value.slice(0, start) + token + input.value.slice(end);
    input.focus();
    input.selectionStart = input.selectionEnd = start + token.length;
    updateArchiveFilenameTemplatePreview();
  });
});

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
        filename_template: archiveGlobalFilenameTemplate,
      }),
    });
    invalidateArchiveCaches();
    resultEl.style.color = "";
    resultEl.textContent = "저장했습니다.";
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

document.getElementById("archive-manual-full-move-toggle").addEventListener("change", (e) => {
  document.getElementById("archive-manual-full-move-warning").classList.toggle("hidden", !e.target.checked);
});

document.getElementById("btn-run-archive-now").addEventListener("click", async () => {
  const checked = Array.from(document.querySelectorAll("#archive-manual-select-list input:checked")).map((el) => el.value);
  const fullMove = document.getElementById("archive-manual-full-move-toggle").checked;
  if (fullMove) {
    if (checked.length === 0) {
      alert("완결 처리로 이동할 웹툰을 먼저 선택하세요.");
      return;
    }
    // 체크박스 자체가 1차 경고이고, 이 확인창이 실행 직전 마지막 재확인이다 —
    // 되돌릴 수 없는 작업(폴더 삭제까지 포함)이라 두 단계로 막는다.
    const proceed = confirm(
      `선택한 웹툰 ${checked.length}개를 완결 처리로 이동합니다.\n마지막 파일까지 전부 옮기고, 다운로드 폴더가 비면 삭제됩니다.\n되돌릴 수 없습니다. 계속할까요?`
    );
    if (!proceed) return;
  }
  const btn = document.getElementById("btn-run-archive-now");
  btn.disabled = true;
  try {
    await apiCall("/api/archive/run", { method: "POST", body: JSON.stringify({ title_ids: checked, full_move: fullMove }) });
    startArchiveJobPolling();
  } catch (e) {
    btn.disabled = false;
    alert(e.message);
  }
});

async function refreshArchiveJobStatus() {
  let statuses;
  try {
    statuses = await apiCall("/api/jobs/status");
  } catch (e) {
    return;
  }
  const s = statuses.archive;
  if (!s) return;
  document.getElementById("archive-status-badge").textContent = s.status;
  renderJobLog("archive", s.log || []);
  document.getElementById("btn-run-archive-now").disabled = s.status === "running";
  if (s.status !== "running") stopArchiveJobPolling();
}

// 탭을 새로 열었을 때는, 마침 실행 중인 게 있으면 그 진행 상황을 이어서 보여주고
// (기존과 동일), 실행 중이 아니면 지난번 완료된 로그를 다시 보여주지 않고 빈
// 상태로 둔다 — 브라우저/탭을 닫았다 열면 이전 실행 기록이 안 보이는 게 맞는
// 동작이라는 요청 반영. 실행 이력 자체(다운로드 이력 탭 등)는 그대로 DB에 남는다.
async function resumeArchiveJobStatusIfRunning() {
  let statuses;
  try {
    statuses = await apiCall("/api/jobs/status");
  } catch (e) {
    return;
  }
  const s = statuses.archive;
  if (!s) return;
  if (s.status === "running") {
    document.getElementById("archive-status-badge").textContent = s.status;
    renderJobLog("archive", s.log || []);
    document.getElementById("btn-run-archive-now").disabled = true;
    startArchiveJobPolling();
  } else {
    document.getElementById("archive-status-badge").textContent = "idle";
    renderJobLog("archive", []);
    document.getElementById("btn-run-archive-now").disabled = false;
  }
}

let archiveJobPollTimer = null;

function startArchiveJobPolling() {
  stopArchiveJobPolling();
  refreshArchiveJobStatus();
  archiveJobPollTimer = setInterval(refreshArchiveJobStatus, 2000);
}

function stopArchiveJobPolling() {
  if (archiveJobPollTimer) {
    clearInterval(archiveJobPollTimer);
    archiveJobPollTimer = null;
  }
}

async function resumeBulkMoveStatusIfRunning() {
  try {
    const statuses = await apiCall("/api/jobs/status");
    const s = statuses.bulk_move;
    if (!s) return;
    if (s.status === "running") {
      // 마침 실행 중이면 그 진행 상황을 이어서 보여준다.
      document.getElementById("bulk-move-status-badge").textContent = s.status;
      document.getElementById("bulk-move-status-badge").className = `badge job-${s.status}`;
      renderJobLog("bulk_move", s.log || []);
      document.getElementById("btn-run-bulk-move").disabled = true;
      startBulkMoveJobPolling();
    } else {
      // 실행 중이 아니면(완료/오류/유휴) 이전 실행 로그는 다시 보여주지 않고
      // 빈 상태로 둔다 — 브라우저/탭을 닫았다 열면 초기화되어야 한다는 요청 반영.
      // 실제 이동 이력 자체는 "이력" 탭의 아카이빙 이력에 그대로 남아있다.
      document.getElementById("bulk-move-status-badge").textContent = "idle";
      document.getElementById("bulk-move-status-badge").className = "badge";
      renderJobLog("bulk_move", []);
      document.getElementById("btn-run-bulk-move").disabled = false;
    }
  } catch (e) {
    // 조용히 무시 — 탭 진입 자체를 막을 정도는 아님
  }
}

async function updateBulkMovePresetPreview() {
  const previewEl = document.getElementById("bulk-move-preset-preview");
  const presetId = document.getElementById("bulk-move-preset-select").value;
  if (!presetId) {
    previewEl.textContent = "";
    return;
  }
  if (!archiveSelectedBulkSourcePath) {
    previewEl.textContent = "미리보기하려면 원본 폴더를 먼저 선택하세요.";
    return;
  }
  const preset = filenamePresetsCache.find((p) => String(p.id) === String(presetId));
  previewEl.textContent = "확인 중...";
  try {
    const payload =
      archiveSelectedBulkSourceType === "local"
        ? {
            source_type: "folder", source_dest_type: "local",
            source_local_root: archiveSelectedBulkSourceLocalRoot, source_path: archiveSelectedBulkSourcePath,
            template: preset.template,
          }
        : { source_type: "folder", source_dest_type: "rclone", source_path: archiveSelectedBulkSourcePath, template: preset.template };
    const result = await apiCall("/api/archive/preview-filename", { method: "POST", body: JSON.stringify(payload) });
    if (!result.original_filename) {
      previewEl.textContent = result.message;
    } else if (result.rendered_filename) {
      previewEl.textContent = `미리보기: ${result.original_filename}  →  ${result.rendered_filename}`;
    } else {
      previewEl.textContent = `${result.original_filename} (${result.message})`;
    }
  } catch (e) {
    previewEl.textContent = e.message;
  }
}

document.getElementById("bulk-move-preset-select").addEventListener("change", updateBulkMovePresetPreview);

document.getElementById("btn-run-bulk-move").addEventListener("click", async () => {
  const resultEl = document.getElementById("bulk-move-result");
  const btn = document.getElementById("btn-run-bulk-move");
  resultEl.textContent = "";
  if (!archiveSelectedBulkSourcePath || !archiveSelectedBulkDestPath) {
    resultEl.textContent = "원본/목적지 폴더를 모두 선택하세요.";
    return;
  }
  btn.disabled = true;
  try {
    await apiCall("/api/archive/bulk-move", {
      method: "POST",
      body: JSON.stringify({
        source_type: archiveSelectedBulkSourceType,
        source_path: archiveSelectedBulkSourcePath,
        source_local_root: archiveSelectedBulkSourceLocalRoot,
        dest_type: archiveSelectedBulkDestType,
        dest_path: archiveSelectedBulkDestPath,
        dest_local_root: archiveSelectedBulkDestLocalRoot,
        filename_template_preset_id: document.getElementById("bulk-move-preset-select").value
          ? Number(document.getElementById("bulk-move-preset-select").value)
          : null,
        regenerate_kakao_cover: document.getElementById("bulk-move-kakao-cover-toggle").checked,
      }),
    });
    // 파일 개수가 많으면 수 분 걸릴 수 있어서, 응답을 기다리지 않고 바로
    // 진행상황 폴링을 시작한다 — 끝나면(refreshBulkMoveJobStatus 안에서) 버튼을
    // 다시 켜고 이력을 새로고침한다.
    startBulkMoveJobPolling();
  } catch (e) {
    resultEl.textContent = e.message;
    btn.disabled = false;
  }
});

let bulkMoveJobPollTimer = null;

function startBulkMoveJobPolling() {
  stopBulkMoveJobPolling();
  refreshBulkMoveJobStatus();
  bulkMoveJobPollTimer = setInterval(refreshBulkMoveJobStatus, 1500);
}

function stopBulkMoveJobPolling() {
  if (bulkMoveJobPollTimer) {
    clearInterval(bulkMoveJobPollTimer);
    bulkMoveJobPollTimer = null;
  }
}

async function refreshBulkMoveJobStatus() {
  let statuses;
  try {
    statuses = await apiCall("/api/jobs/status");
  } catch (e) {
    return;
  }
  const s = statuses.bulk_move;
  if (!s) return;
  const badge = document.getElementById("bulk-move-status-badge");
  badge.textContent = s.status;
  badge.className = `badge job-${s.status}`;
  renderJobLog("bulk_move", s.log || []);
  if (s.status !== "running") {
    stopBulkMoveJobPolling();
    document.getElementById("btn-run-bulk-move").disabled = false;
    const resultEl = document.getElementById("bulk-move-result");
    resultEl.style.color = s.status === "error" ? "" : "";
    resultEl.textContent = s.status === "success" ? "이동 완료했습니다." : "이동 중 오류가 발생했습니다 (아래 로그 확인).";
    loadArchiveHistory(1);
  }
}

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
      const triggerLabel = { periodic: "주기적", manual: "수동", manual_finish: "수동완결", finish_unsubscribe: "완결자동", bulk_move: "일괄이동" }[item.trigger_type] || item.trigger_type;
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
