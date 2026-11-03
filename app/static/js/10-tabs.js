// 탭 전환(switchToTab)과 탭 클릭 연결
// (index.html에서 파일 이름 순서대로 로드된다 — 클래식 스크립트라 최상위 선언은 전역으로 공유된다)
// ── 탭 전환 ─────────────────────────────────────────────

const ACTIVE_TAB_KEY = "activeMainTab";

// 상단 메뉴(.main-tabs)의 실제 높이를 --nav-h에 넣어 둔다 — 고정 툴바(검색/필터/요일 탭)를 그 바로 아래에 붙이는 데 쓴다.
// 창이 좁으면 메뉴가 두 줄로 접혀 높이가 달라지는데, 높이를 41px로 가정해 두면 툴바가 메뉴 뒤에 가려진다. 모바일은 메뉴가 한 줄 고정 높이(--m-nav-h)라 CSS가 따로 계산한다.
function syncStickyOffsets() {
  document.documentElement.style.setProperty("--nav-h", `${document.querySelector(".main-tabs").offsetHeight}px`);
}
window.addEventListener("resize", syncStickyOffsets);

function switchToTab(page) {
  document.querySelectorAll(".main-tab").forEach((t) => t.classList.toggle("active", t.dataset.page === page));
  document.querySelectorAll(".page").forEach((p) => p.classList.add("hidden"));
  document.getElementById(`page-${page}`).classList.remove("hidden");
  stopJobPolling();
  stopRegistryPolling();
  stopArchiveJobPolling();
  stopBulkMoveJobPolling();
  pageLoaders[page]?.();
  if (page === "manual-download") syncManualPlatformSelect();
  document.getElementById("header-list-status").classList.toggle("hidden", page !== "naver-list"); // "마지막 확인" 문구는 전체목록 화면의 것이다
  scrollActiveMainTabIntoView();
}

// 모바일에서는 메뉴바가 한 줄이라 가로로 스와이프해야 모두 보인다 — 선택한 탭이 화면 밖에 가려져 있지 않게 가운데 쪽으로 가져온다(넘치지 않는 데스크톱에서는 아무것도 안 한다)
function scrollActiveMainTabIntoView() {
  const bar = document.querySelector(".main-tabs");
  const active = bar.querySelector(".main-tab.active");
  if (!active || bar.scrollWidth <= bar.clientWidth) return;
  bar.scrollLeft = Math.max(0, active.offsetLeft - (bar.clientWidth - active.offsetWidth) / 2);
}

syncStickyOffsets();
// 글꼴이 늦게 불러와져 메뉴 높이가 달라지는 경우도 따라간다(창 크기 변화는 위의 resize가 처리)
if (typeof ResizeObserver !== "undefined") new ResizeObserver(syncStickyOffsets).observe(document.querySelector(".main-tabs"));

document.querySelectorAll(".main-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    const page = tab.dataset.page;
    sessionStorage.setItem(ACTIVE_TAB_KEY, page);
    switchToTab(page);
  });
});
