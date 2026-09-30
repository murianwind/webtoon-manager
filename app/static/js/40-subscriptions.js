// 구독해제 / 제외됨 탭
// (index.html에서 파일 이름 순서대로 로드된다 — 클래식 스크립트라 최상위 선언은 전역으로 공유된다)
// ── 구독해제 / 제외됨 ────────────────────────────────────

let subscriptionCache = { unsubscribed: [], excluded: [] };

const SUBSCRIPTION_FRESH_MS = 60 * 1000;
const subscriptionLoadedAt = { unsubscribed: 0, excluded: 0 };
const subscriptionSig = { unsubscribed: "", excluded: "" };

async function loadSubscriptionTab(status) {
  const listEl = document.getElementById(`${status}-list`);
  const emptyEl = document.getElementById(`${status}-empty`);
  const hasData = (subscriptionCache[status] || []).length > 0;
  if (hasData && Date.now() - subscriptionLoadedAt[status] < SUBSCRIPTION_FRESH_MS) {
    renderSubscriptionTab(status); // 탭을 오간 것뿐 — 그려진 카드는 그대로 둔다
    return;
  }
  if (!hasData && listEl.children.length === 0) {
    listEl.innerHTML = "<p>불러오는 중...</p>";
  }
  try {
    const naverRows = (await apiCall(`/api/webtoons?status=${status}`)).map((w) => ({ ...w, platform: "naver" }));
    let kakaoRows = [];
    if (kakaoWebtoonsEnabled) {
      try {
        // 카카오 쪽은 서버가 캐시된 목록으로 UP/신작/휴재 배지를 채워 준다(이 탭을 열 때 카카오를 부르지 않는다)
        kakaoRows = (await apiCall(`/api/kakao-webtoons?status=${status}`)).map((w) => ({ ...w, platform: "kakao" }));
      } catch (e) {
        // 조용히 무시 — 카카오 조회 실패로 네이버 쪽까지 안 보이게 하진 않음
      }
    }
    subscriptionLoadedAt[status] = Date.now();
    const rows = [...naverRows, ...kakaoRows];
    const sig = JSON.stringify(rows);
    if (sig !== subscriptionSig[status] || listEl.querySelector(".webtoon-card") === null) {
      subscriptionSig[status] = sig;
      subscriptionCache[status] = rows;
      populateFilterOptions(status);
      renderSubscriptionTab(status); // 바뀐 카드만 갱신된다
    }
  } catch (e) {
    if (listEl.children.length === 0) {
      emptyEl.textContent = `불러오지 못했습니다: ${e.message}`;
      emptyEl.classList.remove("hidden");
    }
  }
}

function populateFilterOptions(status) {
  const rows = subscriptionCache[status] || [];

  const tagNames = new Set();
  for (const w of rows) {
    (w.tags || []).forEach((t) => tagNames.add(t));
  }

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
  const tagFilter = document.getElementById(`${status}-tag-filter`).value;
  const badgeFilter = document.getElementById(`${status}-badge-filter`).value;

  let rows = subscriptionCache[status] || [];
  if (query) {
    rows = rows.filter(
      (w) =>
        w.title.toLowerCase().includes(query) ||
        (w.writer_names || []).some((n) => n.toLowerCase().includes(query)) ||
        (w.author_summary || "").toLowerCase().includes(query)
    );
  }
  if (tagFilter) rows = rows.filter((w) => (w.tags || []).includes(tagFilter));
  if (badgeFilter === "new") rows = rows.filter((w) => w.is_new);
  if (badgeFilter === "paused") rows = rows.filter((w) => w.is_paused);
  if (badgeFilter === "up") rows = rows.filter((w) => w.has_new_episode);

  rows = [...rows].sort((a, b) => a.title.localeCompare(b.title));

  emptyEl.classList.toggle("hidden", rows.length > 0);
  const built = reconcileGrid(listEl, rows, status, subscriptionCache[status] || []);
  if (built > 0) pruneMissingViewerIcons();
}

for (const status of ["unsubscribed", "excluded"]) {
  document.getElementById(`${status}-search`).addEventListener("input", () => renderSubscriptionTab(status));
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

async function deleteWebtoonPermanently(titleId, context, skipConfirm) {
  if (!skipConfirm && !confirm("완전히 삭제합니다 (되돌릴 수 없음). 계속할까요?")) return;
  try {
    await apiCall(`/api/webtoons/${titleId}`, { method: "DELETE" });
    removeCardFromTab(titleId, context);
  } catch (e) {
    alert(e.message);
  }
}

function removeCardFromTab(titleId, context) {
  const listEl = document.getElementById(`${context}-list`);
  const card = listEl.querySelector(`.webtoon-card[data-title-id="${titleId}"]`);
  card?.remove();
  document.getElementById(`${context}-empty`).classList.toggle("hidden", listEl.children.length > 0);
  subscriptionCache[context] = (subscriptionCache[context] || []).filter((w) => w.title_id !== titleId);
}

async function moveToListTab(titleId, context, everSubscribed, platform) {
  // "목록으로" — 구독 이력이 있으면(everSubscribed) 완전 삭제 대신 전용 상태로
  // 옮겨서 DB 기록을 남긴다. 그래야 완결/휴재라 네이버 자체 목록엔 없는 작품도
  // 전체목록에서 계속 찾을 수 있다. 구독 이력이 없으면 완전 삭제한다 — 어차피
  // 구독한 적 없는 건 지워도 이력을 잃을 게 없고, 네이버 목록에 없으면(완결/휴재)
  // 전체목록에서도 안 보일 수 있다는 걸 이미 알고 계신다. 카카오도 완전히 같은
  // 규칙 — URL 접두사만 플랫폼에 따라 다르다.
  const prefix = platform === "kakao" ? "/api/kakao-webtoons" : "/api/webtoons";
  try {
    if (everSubscribed) {
      await apiCall(`${prefix}/${titleId}/unregister`, { method: "POST" });
    } else {
      await apiCall(`${prefix}/${titleId}`, { method: "DELETE" });
    }
    removeCardFromTab(titleId, context);
  } catch (e) {
    alert(e.message);
  }
}
