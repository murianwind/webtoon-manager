// 아카이빙: 폴더 선택기, 설정, 수동 실행, 일괄 이동
// (index.html에서 파일 이름 순서대로 로드된다 — 클래식 스크립트라 최상위 선언은 전역으로 공유된다)
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

async function renderFolderPicker(containerId, onSelect, initialPath, options) {
  // options.skipExistingCheck: true면 폴더에 이미 파일이 있어도 확인 절차 없이
  // 바로 선택된다 — 일괄 이동(bulk-move) 원본/목적지처럼, 애초에 "이미 파일이
  // 있는 폴더"를 다루는 게 목적인 선택기용. 아카이빙 대상 지정용 선택기는
  // 이 옵션을 안 주면 기존처럼 확인 절차를 그대로 거친다.
  const skipExistingCheck = !!(options && options.skipExistingCheck);
  // options.localRoots: ["archive","download"]처럼 주면, 로컬 모드일 때 이 중
  // 어느 루트(ARCHIVE_ROOT/DOWNLOAD_ROOT) 기준으로 찾아볼지 전환 버튼이 뜬다.
  // 안 주면(기존 모든 선택기) 항상 ARCHIVE_ROOT 하나만 쓰는 예전 동작 그대로다.
  const localRoots = (options && options.localRoots) || ["archive"];

  // sessionStorage는 이 브라우저 탭 안에서만 살아있고, 탭을 닫으면(다른 브라우저
  // 탭으로 이동하는 것과는 다름) 자동으로 사라진다 — 그래서 같은 탭 안에서 다른
  // 화면 갔다 오거나 새로고침해도 위치는 유지되면서, 탭/브라우저를 닫으면 다음에
  // 열었을 때는 자연히 초기화된다.
  const saved = loadSavedFolderPickerState(containerId);
  if (saved) {
    // skipExistingCheck/localRoots는 화면 탐색 상태가 아니라 "이 선택기가 애초에
    // 어떻게 동작해야 하는지"를 정하는 호출자 쪽 설정이므로, 저장된 탐색 상태를
    // 복원하더라도 매번 호출 시점 값으로 다시 맞춘다(상태 저장/복원 대상이 아님).
    saved.skipExistingCheck = skipExistingCheck;
    saved.localRoots = localRoots;
    if (!saved.localRoot) saved.localRoot = localRoots[0];
    // 아직 고른 게 없으면 펼친 채로 시작해서 로컬/rclone부터 바로 보이게 한다 —
    // "폴더 선택하기"를 한 번 더 눌러야 하는 건 클릭 한 번을 그냥 낭비하는
    // 것뿐이라는 지적이 있었다. 이미 골라둔 게 있을 때만 접어서 "선택됨: ..."
    // 요약만 보여주고, 공간을 아낀다.
    saved.expanded = !saved.selectedLabel;
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
  archiveFolderPickerState[containerId] = {
    mode: startMode, path: initialPath || "", remote: "", skipExistingCheck, localRoots, localRoot: localRoots[0],
    expanded: true, modeConfirmed: false, rootConfirmed: false,
  };
  renderFolderPickerContents(containerId, onSelect);
}

let _folderListCache = {}; // "local:root:path" 또는 "rclone:remote:path" -> {folders, current_path_selectable}

function _folderListCacheKey(isRclone, remoteOrRoot, path) {
  return `${isRclone ? "rclone" : "local"}:${remoteOrRoot || ""}:${path}`;
}

function _invalidateFolderListCache(isRclone, remoteOrRoot, path) {
  delete _folderListCache[_folderListCacheKey(isRclone, remoteOrRoot, path)];
}

function _invalidateAllRcloneCacheForRemote(remote) {
  // 원격 서비스에서 직접(이 앱 밖에서) 폴더를 새로 만들면, 캐시에 남아있는 예전
  // 목록 때문에 "이 원격 사용"을 다시 눌러도 새 폴더가 안 보이는 문제가 실제로
  // 있었다 — 원격을 다시 고를 때는 그 원격에 대한 캐시를 전부 지워서 항상
  // 새로 조회하게 한다.
  const prefix = `rclone:${remote}:`;
  for (const key of Object.keys(_folderListCache)) {
    if (key.startsWith(prefix)) delete _folderListCache[key];
  }
}

async function renderFolderPickerContents(containerId, onSelect) {
  const container = document.getElementById(containerId);
  const state = archiveFolderPickerState[containerId];
  saveFolderPickerState(containerId);

  if (!state.expanded) {
    // 접힌 상태 — 지금 뭐가 골라져 있는지 한 줄로만 보여주고, 실제 폴더 목록은
    // 눌러야 나온다. 폴더 선택기가 여러 개 세로로 쌓인 화면에서 전부 펼쳐진
    // 채로 나오면 스크롤이 끔찍하게 길어진다는 문제가 실제로 있었다.
    container.innerHTML = "";
    const summary = document.createElement("div");
    summary.className = "folder-picker-summary";
    const label = document.createElement("span");
    label.className = "folder-picker-summary-text";
    label.textContent = state.selectedLabel ? `선택됨: ${state.selectedLabel}` : "폴더를 선택하지 않았습니다.";
    const expandBtn = makeButton(state.selectedLabel ? "변경" : "폴더 선택하기", () => {
      state.expanded = true;
      renderFolderPickerContents(containerId, onSelect);
    });
    summary.appendChild(label);
    summary.appendChild(expandBtn);
    container.appendChild(summary);
    return;
  }

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
  //
  // 로컬 폴더/rclone 원격 -> (로컬이면) 보관 폴더/다운로드 폴더 -> 실제 폴더 목록,
  // 이 순서로 한 단계씩만 펼쳐서 보여준다 — 처음부터 다 펼쳐놓으면 아직 고르지도
  // 않은 다음 단계 UI(그리고 곧바로 시작되는 목록 조회 API 호출)까지 화면을
  // 차지해서 공간도 낭비되고, 뭘 먼저 눌러야 하는지도 헷갈린다는 문제가 있었다.
  const needsModeChoice = archiveSettings.rclone_available && archiveSettings.local_available;
  if (needsModeChoice) {
    const modeRow = document.createElement("div");
    modeRow.className = "folder-picker-mode-row";
    const localBtn = makeButton("로컬 폴더", () => {
      if (state.mode === "local" && state.modeConfirmed) {
        // 이미 펼쳐진 걸 다시 누르면 접는다 — 그 아래 단계(루트 선택/폴더 목록)까지 같이 접힘
        state.modeConfirmed = false;
        renderFolderPickerContents(containerId, onSelect);
        return;
      }
      archiveFolderPickerState[containerId] = {
        mode: "local", path: "", remote: "", skipExistingCheck: state.skipExistingCheck,
        localRoots: state.localRoots, localRoot: state.localRoots[0], expanded: true,
        modeConfirmed: true, rootConfirmed: false,
      };
      renderFolderPickerContents(containerId, onSelect);
    });
    const rcloneBtn = makeButton("rclone 원격", () => {
      if (state.mode === "rclone" && state.modeConfirmed) {
        state.modeConfirmed = false;
        renderFolderPickerContents(containerId, onSelect);
        return;
      }
      archiveFolderPickerState[containerId] = {
        mode: "rclone", path: "", remote: "", skipExistingCheck: state.skipExistingCheck,
        localRoots: state.localRoots, localRoot: state.localRoots[0], expanded: true,
        modeConfirmed: true, rootConfirmed: false,
      };
      renderFolderPickerContents(containerId, onSelect);
    });
    if (state.mode === "local" && state.modeConfirmed) localBtn.classList.add("folder-picker-choice-active");
    if (state.mode === "rclone" && state.modeConfirmed) rcloneBtn.classList.add("folder-picker-choice-active");
    modeRow.appendChild(localBtn);
    modeRow.appendChild(rcloneBtn);
    container.appendChild(modeRow);
  }
  if (needsModeChoice && !state.modeConfirmed) {
    return; // 로컬/rclone을 아직 안 골랐으면 그 다음 단계는 아직 안 보여줌
  }

  // localRoots가 2개 이상이면(예: 일괄 이동에서 ARCHIVE_ROOT/DOWNLOAD_ROOT 둘 다
  // 고를 수 있는 경우), 로컬 모드일 때만 어느 루트를 기준으로 찾아볼지 전환 버튼을 보여준다.
  const localRootLabels = { archive: "보관 폴더(ARCHIVE_ROOT)", download: "다운로드 폴더(네이버)", kakao_download: "카카오페이지 다운로드 폴더" };
  const needsRootChoice = state.mode === "local" && state.localRoots.length > 1;
  if (needsRootChoice) {
    const rootRow = document.createElement("div");
    rootRow.className = "folder-picker-mode-row";
    for (const rootName of state.localRoots) {
      const btn = makeButton(localRootLabels[rootName] || rootName, () => {
        if (state.localRoot === rootName && state.rootConfirmed) {
          // 이미 펼쳐진 걸 다시 누르면 접는다 — 그 아래 폴더 목록이 같이 접힘
          state.rootConfirmed = false;
          renderFolderPickerContents(containerId, onSelect);
          return;
        }
        state.localRoot = rootName;
        state.path = ""; // 루트가 바뀌면 경로 기준이 달라지므로 처음부터 다시 찾아봄
        state.rootConfirmed = true;
        renderFolderPickerContents(containerId, onSelect);
      });
      if (state.localRoot === rootName && state.rootConfirmed) btn.classList.add("folder-picker-choice-active");
      rootRow.appendChild(btn);
    }
    container.appendChild(rootRow);
  }
  if (needsRootChoice && !state.rootConfirmed) {
    return; // 보관/다운로드 폴더를 아직 안 골랐으면 실제 폴더 목록은 아직 안 보여줌
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
      archiveFolderPickerState[containerId] = {
        mode: state.mode, path: "", remote: "", skipExistingCheck: state.skipExistingCheck,
        localRoots: state.localRoots, localRoot: state.localRoot, expanded: true,
        modeConfirmed: state.modeConfirmed, rootConfirmed: state.rootConfirmed,
      };
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
        _invalidateAllRcloneCacheForRemote(state.remote);
        renderFolderPickerContents(containerId, onSelect);
      });
      remoteRow.appendChild(select);
      remoteRow.appendChild(chooseBtn);
      listArea.appendChild(remoteRow);
      return;
    }

    const isRclone = state.mode === "rclone";
    const currentPath = state.path || "";
    const cacheKey = _folderListCacheKey(isRclone, isRclone ? state.remote : state.localRoot, currentPath);

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
        : `/api/archive/folders?path=${encodeURIComponent(currentPath)}&local_root=${encodeURIComponent(state.localRoot)}`;
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
      state.expanded = false; // 골랐으면 접어서 공간을 돌려준다 — 선택 요약은 접힌 화면에 그대로 보임
      onSelect(value, destType, destType === "local" ? state.localRoot : undefined);
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
      // 일괄 이동(bulk-move)용 선택기는 애초에 "이미 파일이 있는 폴더"를 다루는
      // 게 목적이라 이 확인 절차 자체가 불필요하다 — 바로 선택되게 한다.
      if (state.skipExistingCheck) {
        selectAndShow(destValue, destType, folderPath);
        return;
      }
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
      const originalText = newFolderBtn.textContent;
      newFolderBtn.textContent = "만드는 중...";
      newFolderBtn.disabled = true;
      newFolderInput.disabled = true;
      try {
        if (isRclone) {
          await apiCall("/api/archive/rclone/folders", { method: "POST", body: JSON.stringify({ remote: state.remote, path: newPath }) });
        } else {
          await apiCall("/api/archive/folders", { method: "POST", body: JSON.stringify({ path: newPath, root: state.localRoot }) });
        }
        _invalidateFolderListCache(isRclone, isRclone ? state.remote : state.localRoot, currentPath); // 새 폴더가 생겼으니 이 경로는 다시 조회해야 함
        // 예전엔 만들자마자 그 폴더 안으로 들어가버려서, 그 폴더 자체를 고르려면
        // 다시 상위로 나와야 하는 불편함이 있었다 — 생성 후에도 같은 위치(상위
        // 목록)에 그대로 머물러서, 방금 만든 폴더를 목록에서 바로 선택할 수 있게 한다.
        newFolderInput.value = "";
        renderFolderPickerContents(containerId, onSelect);
      } catch (e) {
        newFolderBtn.textContent = originalText;
        newFolderBtn.disabled = false;
        newFolderInput.disabled = false;
        alert(e.message);
      }
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
let archiveEditingTitleId = null; // 수정 중인 대상의 title_id (null이면 신규 등록 모드)
let archiveEditingFolderTargetId = null; // 수정 중인 폴더 대상의 id (null이면 신규 등록 모드)

function enterArchiveTargetEditMode(target) {
  if (target.source_type === "folder") {
    setArchiveTargetTypeTab("folder");
    archiveEditingFolderTargetId = target.title_id;
    document.getElementById("archive-folder-target-display-name").value = target.title_name;
    document.getElementById("btn-add-folder-archive-target").textContent = "수정 저장";
    archiveSelectedFolderTargetSourcePath = target.source_path;
    archiveSelectedFolderTargetSourceType = target.source_dest_type;
    archiveSelectedFolderTargetDestPath = target.dest_base_path;
    archiveSelectedFolderTargetDestType = target.dest_type;
    renderFolderPicker(
      "archive-folder-target-source-picker",
      (path, destType) => {
        archiveSelectedFolderTargetSourcePath = path;
        archiveSelectedFolderTargetSourceType = destType;
      },
      "",
      { skipExistingCheck: true }
    );
    renderFolderPicker("archive-folder-target-dest-picker", (path, destType) => {
      archiveSelectedFolderTargetDestPath = path;
      archiveSelectedFolderTargetDestType = destType;
    });
    return;
  }

  setArchiveTargetTypeTab("webtoon");
  archiveEditingTitleId = target.title_id;
  const banner = document.getElementById("archive-target-edit-banner");
  const bannerText = document.getElementById("archive-target-edit-banner-text");
  const currentLocation = `${target.dest_type === "rclone" ? "☁️" : "💾"} ${target.dest_base_path}`;
  bannerText.textContent = `"${archiveTargetLabel(target)}" 수정 중 — 현재 위치: ${currentLocation}`;
  banner.classList.remove("hidden");

  const select = document.getElementById("archive-target-webtoon-select");
  select.innerHTML = `<option value="${escapeHtml(target.title_id)}">${escapeHtml(archiveTargetLabel(target))}</option>`;
  select.disabled = true;

  document.getElementById("btn-add-archive-target").textContent = "수정 저장";
  archiveSelectedTargetPath = "";
  try {
    sessionStorage.removeItem("folderPickerState:archive-target-folder-picker");
  } catch (_) {
    // 조용히 무시
  }
  renderFolderPicker("archive-target-folder-picker", (path, destType) => {
    archiveSelectedTargetPath = path;
    archiveSelectedTargetDestType = destType;
  });
}

function exitArchiveTargetEditMode() {
  archiveEditingTitleId = null;
  document.getElementById("archive-target-edit-banner").classList.add("hidden");
  document.getElementById("archive-target-webtoon-select").disabled = false;
  document.getElementById("btn-add-archive-target").textContent = "등록";
  loadArchiveTargetWebtoonOptions();
}

function exitFolderArchiveTargetEditMode() {
  archiveEditingFolderTargetId = null;
  document.getElementById("archive-folder-target-display-name").value = "";
  document.getElementById("btn-add-folder-archive-target").textContent = "등록";
}

document.getElementById("btn-cancel-archive-edit").addEventListener("click", () => {
  exitArchiveTargetEditMode();
  exitFolderArchiveTargetEditMode();
});

function setArchiveTargetTypeTab(type) {
  document.querySelectorAll(".archive-target-type-tab").forEach((btn) => btn.classList.toggle("active", btn.dataset.type === type));
  document.getElementById("archive-target-webtoon-panel").classList.toggle("hidden", type !== "webtoon");
  document.getElementById("archive-target-folder-panel").classList.toggle("hidden", type !== "folder");
}

document.querySelectorAll(".archive-target-type-tab").forEach((btn) => {
  btn.addEventListener("click", () => setArchiveTargetTypeTab(btn.dataset.type));
});

let archiveSelectedTargetDestType = "local";
let archiveSelectedDefaultPath = "";
let archiveSelectedDefaultDestType = "local";
let archiveSelectedBulkSourcePath = "";
let archiveSelectedBulkSourceType = "local";
let archiveSelectedBulkSourceLocalRoot = "archive";
let archiveSelectedBulkDestPath = "";
let archiveSelectedBulkDestType = "local";
let archiveSelectedBulkDestLocalRoot = "archive";
let archiveSelectedFolderTargetSourcePath = "";
let archiveSelectedFolderTargetSourceType = "local";
let archiveSelectedFolderTargetDestPath = "";
let archiveSelectedFolderTargetDestType = "local";
let archiveTargetSelectedIds = new Set();

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
  renderFolderPicker(
    "archive-folder-target-source-picker",
    (path, destType) => {
      archiveSelectedFolderTargetSourcePath = path;
      archiveSelectedFolderTargetSourceType = destType;
    },
    "",
    { skipExistingCheck: true }
  );
  renderFolderPicker("archive-folder-target-dest-picker", (path, destType) => {
    archiveSelectedFolderTargetDestPath = path;
    archiveSelectedFolderTargetDestType = destType;
  });
  // 일괄 이동은 "이미 파일이 있는 폴더"를 다루는 게 목적이므로, 대상 지정용
  // 선택기와 달리 "이미 파일 있음" 확인 절차를 건너뛴다(skipExistingCheck).
  // 원본/목적지가 로컬/원격 어느 조합이든(로컬-로컬, 로컬-원격, 원격-로컬,
  // 원격-원격) 지원해야 하므로 destType/localRoot도 각각 따로 기억해둔다.
  // localRoots: ["archive","download"] — 이미 완결됐는데 구독 안 해서 자동
  // 아카이빙 대상엔 못 올리는 웹툰처럼, 다운로드 폴더에 있는 걸 그대로 보관
  // 폴더로 옮기고 싶을 때 DOWNLOAD_ROOT도 원본으로 고를 수 있게 하기 위함.
  renderFolderPicker(
    "bulk-move-source-picker",
    (path, destType, localRoot) => {
      archiveSelectedBulkSourcePath = path;
      archiveSelectedBulkSourceType = destType;
      archiveSelectedBulkSourceLocalRoot = localRoot || "archive";
      updateBulkMovePresetPreview();
    },
    "",
    { skipExistingCheck: true, localRoots: kakaoWebtoonsEnabled ? ["archive", "download", "kakao_download"] : ["archive", "download"] }
  );
  renderFolderPicker(
    "bulk-move-dest-picker",
    (path, destType, localRoot) => {
      archiveSelectedBulkDestPath = path;
      archiveSelectedBulkDestType = destType;
      archiveSelectedBulkDestLocalRoot = localRoot || "archive";
    },
    "",
    { skipExistingCheck: true, localRoots: kakaoWebtoonsEnabled ? ["archive", "download", "kakao_download"] : ["archive", "download"] }
  );
  await loadFilenamePresets();
  await loadArchiveTargetList();
  await loadArchiveSettings();
  await resumeBulkMoveStatusIfRunning(); // 탭을 나갔다 들어와도 실행 중이던 일괄이동을 이어서 보여줌
}

// 웹툰 유형 아카이빙 대상의 표시 이름 — 카카오페이지 작품은 "[카카오]"를 붙인다(대상 id는 "kakao_<시리즈 번호>")
function archiveTargetLabel(t) {
  return t.platform === "kakao" ? `[카카오] ${t.title_name}` : t.title_name;
}

async function loadArchiveTargetWebtoonOptions() {
  const select = document.getElementById("archive-target-webtoon-select");
  try {
    const [webtoons, kakaoWebtoons, targets] = await Promise.all([
      apiCall("/api/webtoons?status=active"),
      // 구독 중인 카카오페이지 작품도 등록할 수 있다(카카오웹툰 관리를 켰을 때). 조회가 실패해도 네이버 목록은 그대로 보여준다.
      kakaoWebtoonsEnabled ? apiCall("/api/kakao-webtoons?status=active").catch(() => []) : Promise.resolve([]),
      apiCall("/api/archive/targets"),
    ]);
    const registeredIds = new Set(targets.map((t) => t.title_id));
    select.innerHTML = "";
    const addOption = (value, label) => {
      if (registeredIds.has(value)) return; // 이미 등록된 웹툰은 다시 고를 필요가 없음
      const opt = document.createElement("option");
      opt.value = value;
      opt.textContent = label;
      select.appendChild(opt);
    };
    for (const w of webtoons) addOption(w.title_id, kakaoWebtoonsEnabled ? `[네이버] ${w.title}` : w.title);
    for (const w of kakaoWebtoons) addOption(`kakao_${w.title_id}`, `[카카오] ${w.title}`);
    if (select.options.length === 0) {
      select.innerHTML = '<option value="">등록 가능한 웹툰이 없습니다</option>';
    }
  } catch (e) {
    select.innerHTML = `<option>${escapeHtml(e.message)}</option>`;
  }
}
