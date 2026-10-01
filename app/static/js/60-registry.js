// 작가/태그 관리(네이버) + 카카오 관심 작가
// (index.html에서 파일 이름 순서대로 로드된다 — 클래식 스크립트라 최상위 선언은 전역으로 공유된다)
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
    if (kakaoWebtoonsEnabled) loadKakaoAuthorList(); // 카카오 작가도 함께 채워졌으니 목록을 새로 불러온다
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
      buildChip(`${a.author_name || a.author_id}${a.is_origin ? " (원작)" : ""}`, true, async () => {
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
    items.push({ label: a.is_origin ? `${label} (원작)` : label, onClick: () => enableKnownAuthor(a) });
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

let kakaoOriginNames = new Set(); // 원작 작가로 나온 적 있는 이름(카카오 목록은 역할을 안 알려줘서 작품 정보에서 모은 것)

function kakaoAuthorLabel(name) {
  return kakaoOriginNames.has(name) ? `${name} (원작)` : name;
}

async function loadKakaoAuthorList() {
  const registeredEl = document.getElementById("kakao-author-registered-chips");
  try {
    [kakaoWatchedAuthorsCache, kakaoOriginNames] = await Promise.all([
      apiCall("/api/kakao/watched-authors"),
      apiCall("/api/kakao/origin-authors").then((names) => new Set(names)).catch(() => new Set()),
    ]);
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
      buildChip(kakaoAuthorLabel(a.author_name), true, async () => {
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
    items.push({ label: kakaoAuthorLabel(a.author_name), onClick: () => enableKnownKakaoAuthor(a) });
  }
  for (const name of kakaoAuthorCandidatesCache) {
    if (enabledNames.has(name) || knownNames.has(name)) continue;
    if (query && !name.toLowerCase().includes(query)) continue;
    items.push({
      label: kakaoAuthorLabel(name),
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
