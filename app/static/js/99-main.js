// 초기화: 탭별 로더 연결과 첫 화면 로드. 다른 파일의 함수를 즉시 참조하므로 반드시 마지막에 로드한다.
// (한 파일이던 시절에는 함수 선언이 끌어올려져서(hoisting) 위쪽에 있어도 됐지만, 파일이 나뉘면 뒤 파일 것은 아직 없다)

const pageLoaders = {
  "naver-list": loadNaverList,
  unsubscribed: () => loadSubscriptionTab("unsubscribed"),
  excluded: () => loadSubscriptionTab("excluded"),
  "manual-download": () => {},
  registry: loadRegistryPage,
  history: loadHistoryPage,
  "manual-run": loadManualRunPage,
  archive: loadArchivePage,
  settings: loadSettingsPage,
  help: loadHelpPage,
};

initClearableInputs();
restoreNaverListPrefs();

const savedTab = sessionStorage.getItem(ACTIVE_TAB_KEY);

if (savedTab && document.getElementById(`page-${savedTab}`)) {
  switchToTab(savedTab);
} else {
  loadNaverList();
}
