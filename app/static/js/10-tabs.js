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
}

document.querySelectorAll(".main-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    const page = tab.dataset.page;
    sessionStorage.setItem(ACTIVE_TAB_KEY, page);
    switchToTab(page);
  });
});
