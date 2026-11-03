// 탭 전환(switchToTab)과 탭 클릭 연결
// (index.html에서 파일 이름 순서대로 로드된다 — 클래식 스크립트라 최상위 선언은 전역으로 공유된다)
// ── 탭 전환 ─────────────────────────────────────────────

const ACTIVE_TAB_KEY = "activeMainTab";

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
  scrollActiveMainTabIntoView();
}

// 모바일에서는 메뉴바가 한 줄이라 가로로 스와이프해야 모두 보인다 — 선택한 탭이 화면 밖에 가려져 있지 않게 가운데 쪽으로 가져온다(넘치지 않는 데스크톱에서는 아무것도 안 한다)
function scrollActiveMainTabIntoView() {
  const bar = document.querySelector(".main-tabs");
  const active = bar.querySelector(".main-tab.active");
  if (!active || bar.scrollWidth <= bar.clientWidth) return;
  bar.scrollLeft = Math.max(0, active.offsetLeft - (bar.clientWidth - active.offsetWidth) / 2);
}

document.querySelectorAll(".main-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    const page = tab.dataset.page;
    sessionStorage.setItem(ACTIVE_TAB_KEY, page);
    switchToTab(page);
  });
});
