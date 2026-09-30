// 웹툰 전체목록(네이버+카카오): 캐시 우선 로드, 필터/정렬, 구독/제외 동작
// (index.html에서 파일 이름 순서대로 로드된다 — 클래식 스크립트라 최상위 선언은 전역으로 공유된다)
// ── 네이버 웹툰 전체목록 ─────────────────────────────────

let naverListCache = [];
const NAVER_LIST_PREFS_KEY = "naverListPrefs";

function saveNaverListPrefs() {
  const prefs = {
    filterStatus: document.getElementById("naver-list-filter-status").value,
    badgeFilter: document.getElementById("naver-list-badge-filter").value,
    sort: document.getElementById("naver-list-sort").value,
  };
  localStorage.setItem(NAVER_LIST_PREFS_KEY, JSON.stringify(prefs));
}

function restoreNaverListPrefs() {
  try {
    const prefs = JSON.parse(localStorage.getItem(NAVER_LIST_PREFS_KEY) || "{}");
    if (prefs.filterStatus) document.getElementById("naver-list-filter-status").value = prefs.filterStatus;
    if (prefs.badgeFilter) document.getElementById("naver-list-badge-filter").value = prefs.badgeFilter;
    if (prefs.sort) document.getElementById("naver-list-sort").value = prefs.sort;
  } catch (e) {
    // 저장된 값이 이상하면 그냥 기본값 사용
  }
}

const NAVER_LIST_FRESH_MS = 60 * 1000; // 탭을 오간 것뿐이면 이 시간 안에는 다시 받지 않는다
const KAKAO_LIST_POLL_MS = 3000;
const KAKAO_LIST_POLL_MAX = 60; // 3초 × 60 = 갱신 중인 카카오 목록을 최대 3분 지켜본다
let naverListLoadedAt = 0;
let naverListNaverItems = [];
let naverListKakaoItems = [];
let kakaoListVersion = null;
let kakaoListPollTimer = null;
let kakaoListPollCount = 0;

// 웹툰의 구독 상태가 바뀌면(어느 탭에서든) 다음에 각 목록을 열 때 서버에서 다시 받게 한다. 카드는 재사용되므로(reconcileGrid)
// 다시 받아도 바뀐 것만 갱신되고 깜빡이지 않는다.
function invalidateListCaches() {
  naverListLoadedAt = 0;
  kakaoListVersion = null;
  subscriptionLoadedAt.unsubscribed = 0;
  subscriptionLoadedAt.excluded = 0;
}

function combineNaverListCache() {
  naverListCache = [...naverListNaverItems, ...naverListKakaoItems];
}

function setNaverListStatus(kakaoData, extra) {
  const parts = [`${naverListCache.length}개`];
  if (kakaoData && kakaoData.refreshing) parts.push("카카오페이지 목록은 백그라운드로 갱신 중");
  else if (kakaoData && kakaoData.refreshed_at) parts.push(`카카오페이지 목록: ${formatKoreanTime(kakaoData.refreshed_at)} 기준`);
  else if (kakaoData) parts.push("카카오페이지 목록을 아직 받지 못했습니다");
  document.getElementById("naver-list-refresh-status").textContent =
    `마지막 확인: ${formatKoreanTime(new Date(naverListLoadedAt).toISOString())} (${parts.join(", ")})${extra || ""}`;
}

// 카카오 목록은 서버가 캐시에서 바로 돌려준다(3시간마다/시작할 때/새로고침 때 백그라운드로 채워짐). 바뀐 게 있을
// 때(version이 다를 때)만 화면을 갱신한다.
async function fetchKakaoListIntoCache(forceRefresh) {
  const data = await apiCall(`/api/kakao-list${forceRefresh ? "?refresh=true" : ""}`);
  if (data.version !== kakaoListVersion) {
    kakaoListVersion = data.version;
    naverListKakaoItems = data.items.map((w) => ({ ...w, platform: "kakao" }));
    combineNaverListCache();
    renderNaverList();
  }
  return data;
}

function pollKakaoList() {
  clearTimeout(kakaoListPollTimer);
  if (kakaoListPollCount >= KAKAO_LIST_POLL_MAX) return;
  kakaoListPollTimer = setTimeout(async () => {
    kakaoListPollCount += 1;
    try {
      const data = await fetchKakaoListIntoCache(false);
      setNaverListStatus(data);
      if (data.refreshing) pollKakaoList();
    } catch (e) {
      // 다음 탭 진입/새로고침 때 다시 확인한다
    }
  }, KAKAO_LIST_POLL_MS);
}

async function loadNaverList(forceRefresh) {
  const grid = document.getElementById("naver-list-grid");
  const emptyMsg = document.getElementById("naver-list-empty");
  const statusEl = document.getElementById("naver-list-refresh-status");
  const btn = document.getElementById("btn-refresh-naver-list");

  const hasData = naverListCache.length > 0;
  if (!forceRefresh && hasData && Date.now() - naverListLoadedAt < NAVER_LIST_FRESH_MS) {
    renderNaverList(); // 탭을 오간 것뿐 — 이미 그려진 카드는 그대로 두고 바뀐 것만 반영한다
    return;
  }
  if (!hasData) grid.innerHTML = "<p>불러오는 중...</p>";
  btn.disabled = true;
  statusEl.textContent = "확인 중...";

  try {
    naverListNaverItems = (await apiCall("/api/naver-list")).map((w) => ({ ...w, platform: "naver" }));
    combineNaverListCache();
    renderNaverList();
    let kakaoData = null;
    let kakaoError = "";
    if (kakaoWebtoonsEnabled) {
      try {
        kakaoData = await fetchKakaoListIntoCache(forceRefresh);
        kakaoListPollCount = 0;
        if (kakaoData.refreshing) pollKakaoList();
      } catch (e) {
        kakaoError = ` (카카오 목록 조회 실패: ${e.message})`;
      }
    } else if (naverListKakaoItems.length > 0) {
      naverListKakaoItems = [];
      kakaoListVersion = null;
      combineNaverListCache();
      renderNaverList();
    }
    naverListLoadedAt = Date.now();
    setNaverListStatus(kakaoData, kakaoError);
  } catch (e) {
    if (!hasData) {
      grid.innerHTML = "";
      emptyMsg.textContent = `목록을 불러오지 못했습니다: ${e.message}`;
      emptyMsg.classList.remove("hidden");
    }
    statusEl.textContent = `새로고침 실패: ${e.message}`;
  } finally {
    btn.disabled = false;
  }
}

function renderNaverList() {
  const grid = document.getElementById("naver-list-grid");
  const emptyMsg = document.getElementById("naver-list-empty");
  const query = document.getElementById("naver-list-search").value.trim().toLowerCase();
  const filterStatus = document.getElementById("naver-list-filter-status").value;
  const badgeFilter = document.getElementById("naver-list-badge-filter").value;
  const sortBy = document.getElementById("naver-list-sort").value;

  // 구독해제/목록제외한 작품은 원래 여기서 안 보이고 각자의 탭에서만 보이는 게
  // 기본 규칙이다. "unregistered" 상태(구독 이력 있는 것을 "목록으로" 보낸 것)는
  // 어느 탭에도 안 속하니 그 규칙과 무관하게 항상 여기서 보인다. "unsubscribed"
  // 상태는 실제로 구독한 적이 없으면(제외됨 → 목록으로만 거친 경우) 예외로 두고
  // 여기서도 보여준다 — 이 두 경우를 안 걸러내면 "목록으로"를 눌러도 전체목록
  // 어디에도 안 보이고 그냥 사라진 것처럼 느껴진다.
  let rows = naverListCache.filter((w) => w.status !== "excluded" && !(w.status === "unsubscribed" && w.ever_subscribed));

  if (filterStatus === "active") rows = rows.filter((w) => w.status === "active");
  if (filterStatus === "not-active") rows = rows.filter((w) => w.status !== "active");
  if (badgeFilter === "new") rows = rows.filter((w) => w.is_new);
  if (badgeFilter === "paused") rows = rows.filter((w) => w.is_paused);
  if (badgeFilter === "up") rows = rows.filter((w) => w.has_new_episode);

  if (query) {
    rows = rows.filter(
      (w) => w.title.toLowerCase().includes(query) || (w.author_summary || "").toLowerCase().includes(query)
    );
  }

  rows = [...rows];
  if (sortBy === "author") {
    rows.sort((a, b) => (a.author_summary || "").localeCompare(b.author_summary || "") || a.title.localeCompare(b.title));
  } else if (sortBy === "new-episode") {
    rows.sort((a, b) => (b.has_new_episode === true) - (a.has_new_episode === true) || a.title.localeCompare(b.title));
  } else {
    rows.sort((a, b) => a.title.localeCompare(b.title));
  }

  emptyMsg.classList.toggle("hidden", rows.length > 0);
  const built = reconcileGrid(grid, rows, "naver-list", naverListCache);
  updateNaverListBulkBar(); // 남아 있는 카드의 체크 상태는 유지되므로, 선택 수만 다시 센다
  if (built > 0) {
    naverListBulkSelectStartScrollY = null; // 카드 구성이 바뀌었으니 "선택을 시작한 위치"도 다시 잡아야 함
    pruneMissingViewerIcons(); // 새로 만든 카드의 뷰어 아이콘만 확인하면 된다
  }
}

let naverListBulkSelectStartScrollY = null;

async function pruneMissingViewerIcons() {
  // "뷰어에서 보기" 아이콘은 구독 중이면 무조건 붙는데, 실제로 뷰어 라이브러리에
  // 없는 작품도 있다(특히 카카오는 구독=다운로드가 아니라서 더 흔함) — 렌더링
  // 자체를 그 확인 때문에 늦추지 않고, 그려진 뒤 백그라운드로 하나씩 확인해서
  // 없는 것만 조용히 지운다. 뷰어가 로컬망에 있다는 전제라 확인 자체는 빠르다.
  // 네이버/카카오는 폴더명 만드는 도구가 서로 달라서 특수문자 치환 규칙도 다르므로
  // (예: 콜론 -> 네이버는 전각 콜론, 카카오는 밑줄), 같은 제목이라도 플랫폼별로
  // 따로 조회해야 한다 — (제목, 플랫폼) 조합 단위로 묶는다.
  const buttons = [...document.querySelectorAll("[data-viewer-check-title]")];
  const seen = new Map(); // "title\u0000platform" -> {title, platform}
  for (const b of buttons) {
    const platform = b.dataset.viewerCheckPlatform || "naver";
    const key = `${b.dataset.viewerCheckTitle}\u0000${platform}`;
    if (!seen.has(key)) seen.set(key, { title: b.dataset.viewerCheckTitle, platform });
  }
  await Promise.all(
    [...seen.values()].map(async ({ title, platform }) => {
      try {
        const data = await apiCall(
          `/api/webtoon-server/lookup?title=${encodeURIComponent(title)}&platform=${encodeURIComponent(platform)}`
        );
        if (!data.url) {
          document
            .querySelectorAll(
              `[data-viewer-check-title="${CSS.escape(title)}"][data-viewer-check-platform="${CSS.escape(platform)}"]`
            )
            .forEach((b) => b.remove());
        }
      } catch (e) {
        // 조회 자체가 실패하면(네트워크 문제 등) 아이콘은 그냥 둔다 — 눌렀을 때
        // 다시 시도되고, 여기서 실패했다고 성급하게 지우면 안 된다.
      }
    })
  );
}

function updateNaverListBulkBar() {
  const count = document.querySelectorAll("#naver-list-grid .webtoon-card-select:checked").length;
  document.getElementById("naver-list-bulk-bar").classList.toggle("hidden", count === 0);
  document.getElementById("naver-list-bulk-count").textContent = `${count}개 선택됨`;
}

document.getElementById("naver-list-grid").addEventListener("change", (e) => {
  if (!e.target.classList.contains("webtoon-card-select")) return;
  // 체크박스를 처음 켠 시점의 스크롤 위치를 기억해둔다 — "선택 항목 제외됨으로
  // 이동"이 끝난 뒤 이 위치로 되돌려서, 선택하려고 스크롤해 내려온 걸 다시
  // 손으로 올릴 필요가 없게 한다. 이후에 체크를 더 추가해도(맨 처음 위치 그대로
  // 유지해야 하므로) 다시 갱신하지 않는다.
  if (naverListBulkSelectStartScrollY === null && e.target.checked) {
    naverListBulkSelectStartScrollY = window.scrollY;
  }
  updateNaverListBulkBar();
});

document.getElementById("btn-bulk-exclude-naver-list").addEventListener("click", async () => {
  const checked = [...document.querySelectorAll("#naver-list-grid .webtoon-card-select:checked")];
  if (checked.length === 0) return;
  if (!confirm(`선택한 ${checked.length}개를 "제외됨"으로 옮길까요?`)) return;

  const btn = document.getElementById("btn-bulk-exclude-naver-list");
  btn.disabled = true;
  const restoreScrollY = naverListBulkSelectStartScrollY !== null ? naverListBulkSelectStartScrollY : window.scrollY;
  try {
    for (const checkbox of checked) {
      const card = checkbox.closest(".webtoon-card");
      const titleId = card.dataset.titleId;
      const webtoon = naverListCache.find((w) => String(w.title_id) === String(titleId));
      if (!webtoon) continue;
      try {
        // 여러 개를 처리하는 동안은 매번 다시 그리지 않는다(skipRender) — 하나
        // 처리할 때마다 목록 길이가 바뀌면서 스크롤이 계속 흔들리는 문제가
        // 실제로 있었다. 다 끝난 뒤 한 번만 그리고, 선택을 시작했던 스크롤
        // 위치로 되돌려서 다시 손으로 그 자리까지 내릴 필요가 없게 한다.
        await webtoonListAction(webtoon, "exclude", webtoon.platform || "naver", "naver-list", true);
      } catch (e) {
        alert(`"${webtoon.title}" 제외 실패: ${e.message}`);
      }
    }
    renderNaverList();
    window.scrollTo({ top: restoreScrollY, behavior: "auto" });
  } finally {
    btn.disabled = false;
  }
});

function _webtoonListActionUrl(platform, titleId, action) {
  if (platform === "kakao") return `/api/kakao-webtoons/${titleId}/${action}`;
  // 네이버는 구독/목록제외가 /api/naver-list 밑에, 구독해제만 /api/webtoons 밑에 있다
  // (기존부터 그렇게 나뉘어 있던 것 — 여기서 통일하지 않고 그대로 흡수한다).
  return action === "unsubscribe" ? `/api/webtoons/${titleId}/unsubscribe` : `/api/naver-list/${titleId}/${action}`;
}

async function webtoonListAction(webtoon, action, platform, context, skipRender) {
  const { title_id: titleId, title, thumbnail_url: thumbnailUrl, author_summary: authorSummary } = webtoon;
  const needsBody = action === "subscribe" || action === "exclude";
  const body = platform === "kakao"
    ? { title, thumbnail_url: thumbnailUrl || "", author_summary: authorSummary || "" }
    : { title, thumbnail_url: thumbnailUrl || "" };
  try {
    const updated = await apiCall(_webtoonListActionUrl(platform, titleId, action), {
      method: "POST",
      ...(needsBody ? { body: JSON.stringify(body) } : {}),
    });
    if (context === "naver-list") {
      // "웹툰 전체목록"은 지금 걸려있는 필터/정렬을 다시 적용해서 전체를 다시
      // 그려야 한다(구독 상태가 바뀌면 필터 조건도 바뀌므로).
      patchWebtoonListCard(titleId, webtoon, updated.status, updated.ever_subscribed, platform, skipRender);
    } else {
      // "구독해제"/"제외됨" 탭에서는 무슨 액션이든(구독 포함) 그 탭 조건에 더 이상
      // 안 맞게 되므로 카드를 그냥 지운다 — 예전엔 여기서도 patchWebtoonListCard를
      // 불러서 "웹툰 전체목록" 그리드를 엉뚱하게 다시 그리는 버그가 있었다(이 탭엔
      // 그 그리드가 없어서, 실제로는 아무 화면도 안 갱신되는 것처럼 보였음).
      removeCardFromTab(titleId, context);
    }
  } catch (e) {
    alert(e.message);
  }
}

function patchWebtoonListCard(titleId, webtoon, newStatus, everSubscribed, platform, skipRender) {
  const cacheIndex = naverListCache.findIndex((w) => w.title_id === titleId && (w.platform || "naver") === platform);
  if (cacheIndex >= 0) {
    naverListCache[cacheIndex] = { ...naverListCache[cacheIndex], status: newStatus, ever_subscribed: everSubscribed };
  }
  if (skipRender) return; // 여러 개를 한꺼번에 처리할 때(일괄 제외 등) 매번 다시 그리면 그때마다 목록 길이가 바뀌어 스크롤이 계속 흔들린다 — 호출부가 다 끝난 뒤 한 번만 그리게 맡긴다.
  // 카드를 그 자리에서 바꿔치기만 하면, 지금 걸려있는 필터(구독중만/아직 미등록만
  // 등)에 따라 이 카드가 이제 안 보여야 하는 경우를 놓친다 — 예를 들어 "아직
  // 미등록만" 필터에서 구독을 누르면 이제 활성 상태라 원래는 사라져야 하는데,
  // 그 자리에 그대로 남아있는 문제가 실제로 있었다. 전체를 다시 그려서 필터/정렬을
  // 항상 다시 정확히 적용한다.
  renderNaverList();
}

document.getElementById("btn-refresh-naver-list").addEventListener("click", () => loadNaverList(true));
document.getElementById("naver-list-search").addEventListener("input", renderNaverList);
document.getElementById("naver-list-filter-status").addEventListener("change", () => {
  saveNaverListPrefs();
  renderNaverList();
});
document.getElementById("naver-list-badge-filter").addEventListener("change", () => {
  saveNaverListPrefs();
  renderNaverList();
});
document.getElementById("naver-list-sort").addEventListener("change", () => {
  saveNaverListPrefs();
  renderNaverList();
});
