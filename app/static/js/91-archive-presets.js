// 아카이빙: 파일명 변경 프리셋
// (index.html에서 파일 이름 순서대로 로드된다 — 클래식 스크립트라 최상위 선언은 전역으로 공유된다)
// ── 파일명 변경 프리셋 ────────────────────────────────────
let filenamePresetsCache = [];

async function loadFilenamePresets() {
  try {
    filenamePresetsCache = await apiCall("/api/archive/presets");
  } catch (e) {
    filenamePresetsCache = [];
  }

  function fillPresetSelect(select, defaultLabel) {
    const prevValue = select.value;
    select.innerHTML = `<option value="">${defaultLabel}</option>`;
    for (const p of filenamePresetsCache) {
      const opt = document.createElement("option");
      opt.value = p.id;
      opt.textContent = p.name;
      select.appendChild(opt);
    }
    if (prevValue && [...select.options].some((o) => o.value === prevValue)) {
      select.value = prevValue;
    }
  }

  fillPresetSelect(document.getElementById("archive-preset-select"), "기본 (전역)");
  fillPresetSelect(document.getElementById("archive-target-bulk-preset-select"), "기본 (전역)");
  fillPresetSelect(document.getElementById("archive-target-webtoon-preset-select"), "기본 (전역)");
  fillPresetSelect(document.getElementById("archive-folder-target-preset-select"), "기본 (전역)");
  // 일괄 이동은 "웹툰/폴더 대상"과 달리 전역 기본값을 상속하는 개념이 없다 —
  // 애초에 회차/웹툰 개념이 없는 임의 폴더 이동이라, 안 고르면 그냥 원본 그대로다.
  fillPresetSelect(document.getElementById("bulk-move-preset-select"), "선택 안 함 (원본 파일명 그대로)");
}

let archiveGlobalFilenameTemplate = "";

function loadPresetIntoEditor(presetId) {
  if (!presetId) {
    document.getElementById("archive-filename-template").value = archiveGlobalFilenameTemplate;
    updateArchiveFilenameTemplatePreview();
    return;
  }
  const preset = filenamePresetsCache.find((p) => String(p.id) === String(presetId));
  document.getElementById("archive-filename-template").value = preset ? preset.template : "";
  updateArchiveFilenameTemplatePreview();
}

document.getElementById("archive-preset-select").addEventListener("change", (e) => loadPresetIntoEditor(e.target.value));

document.getElementById("btn-new-preset").addEventListener("click", async () => {
  const name = prompt("새 프리셋 이름을 입력하세요:");
  if (!name || !name.trim()) return;
  try {
    const created = await apiCall("/api/archive/presets", { method: "POST", body: JSON.stringify({ name: name.trim(), template: "" }) });
    await loadFilenamePresets();
    document.getElementById("archive-preset-select").value = created.id;
    loadPresetIntoEditor(created.id);
  } catch (e) {
    alert(e.message);
  }
});

document.getElementById("btn-delete-preset").addEventListener("click", async () => {
  const presetId = document.getElementById("archive-preset-select").value;
  if (!presetId) {
    alert("삭제할 프리셋을 선택하세요 (기본 값은 삭제할 수 없습니다).");
    return;
  }
  if (!confirm("이 프리셋을 삭제할까요? 이 프리셋을 쓰던 대상들은 기본(전역) 값으로 돌아갑니다.")) return;
  try {
    await apiCall(`/api/archive/presets/${presetId}`, { method: "DELETE" });
    await loadFilenamePresets();
    loadPresetIntoEditor("");
    loadArchiveTargetList();
  } catch (e) {
    alert(e.message);
  }
});

document.getElementById("btn-save-preset").addEventListener("click", async () => {
  const resultEl = document.getElementById("archive-preset-save-result");
  const presetId = document.getElementById("archive-preset-select").value;
  const template = document.getElementById("archive-filename-template").value;
  resultEl.textContent = "";
  try {
    if (presetId) {
      const preset = filenamePresetsCache.find((p) => String(p.id) === String(presetId));
      await apiCall(`/api/archive/presets/${presetId}`, { method: "POST", body: JSON.stringify({ name: preset.name, template }) });
    } else {
      // "기본(전역)" 편집 중 저장 -> 전역 설정에 반영 (기존 저장 방식 그대로)
      await apiCall("/api/archive/settings", {
        method: "POST",
        body: JSON.stringify({
          default_base_path: archiveSelectedDefaultPath || "",
          default_dest_type: archiveSelectedDefaultDestType,
          conflict_policy: document.getElementById("archive-conflict-policy").value,
          on_finish_unsubscribe: document.getElementById("archive-on-finish-toggle").checked,
          filename_template: template,
        }),
      });
      archiveGlobalFilenameTemplate = template;
      invalidateArchiveCaches();
    }
    await loadFilenamePresets();
    document.getElementById("archive-preset-select").value = presetId;
    resultEl.style.color = "";
    resultEl.textContent = "저장했습니다.";
  } catch (e) {
    resultEl.textContent = e.message;
  }
});
