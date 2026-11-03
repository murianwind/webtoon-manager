// 제외됨 화면은 완결 웹툰을 보여 주지 않는다 — 완결 작품은 수동 다운로드에서 분석해서 구독할 수 있으니 제외됨 목록에 쌓아 둘 필요가 없다.
// (요일별 탭의 개수, 검색, "기타" 탭 모두에서 빠진다. 몇 개를 숨겼는지는 화면이 아니라 실행 이력에 남긴다. 구독해제 화면과 전체목록은 그대로.)
const { makeApp, sleep, idle, ok, done } = require("./harness");
const { listItem } = require("./factories");
(async () => {
  const mk = (platform, id, title, weekdays, extra = {}) => listItem(platform, id, title, weekdays, { status: "excluded", ...extra });
  const { w, run, wait } = makeApp({ "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" }, "/api/naver-list": [], "/api/kakao-list": { items: [], version: "v", refreshing: false } }, { timeScale: 5, now: "2026-11-03T03:00:00Z" }); // 검색 지연(250ms)을 5배 빠르게 — 고정 sleep 없이 확인
  await idle(); run("kakaoWebtoonsEnabled = true;");
  const $ = (id) => w.document.getElementById(id);
  const titles = (gridId) => [...w.document.querySelectorAll(`#${gridId} .webtoon-card-title`)].map((e) => e.textContent.trim()).sort();
  const tabs = (id) => [...$(id).querySelectorAll(".weekday-tab")];
  const label = (t) => t.firstChild.textContent.trim();
  const count = (id, name) => tabs(id).find((t) => label(t) === name).querySelector(".weekday-tab-count").textContent;
  const showExcluded = (rows) => { w.__rows = rows; run("subscriptionCache.excluded = __rows; populateFilterOptions('excluded'); renderSubscriptionTab('excluded');"); };
  const note = () => $("excluded-finished-note");

  // Scenario 1: 완결은 어느 탭에도, 검색에도 안 나온다. 탭 개수에도 안 센다. 완결이 아닌 작품(휴재 포함)은 그대로
  const rows = [
    mk("naver", 1, "연재중 화요일", ["tue"]), mk("naver", 2, "완결 화요일", ["tue"], { is_finished: true }), mk("naver", 3, "휴재 목요일", ["thu"], { is_paused: true }),
    mk("naver", 4, "완결 기타", [], { is_finished: true }), mk("naver", 5, "요일 모름", []), mk("kakao", 6, "카카오 완결", [], { is_finished: true }), mk("kakao", 7, "카카오 연재 화", ["tue"]),
  ];
  showExcluded(rows);
  ok(JSON.stringify(titles("excluded-list")) === JSON.stringify(["연재중 화요일", "카카오 연재 화"].sort()), "화요일 탭에 완결 제외: " + titles("excluded-list"));
  ok(count("excluded-weekday-tabs", "화") === "2" && count("excluded-weekday-tabs", "기타") === "1" && count("excluded-weekday-tabs", "전체") === "4", "탭 개수에서도 완결 제외: 화 " + count("excluded-weekday-tabs", "화") + " 기타 " + count("excluded-weekday-tabs", "기타") + " 전체 " + count("excluded-weekday-tabs", "전체"));
  tabs("excluded-weekday-tabs").find((t) => label(t) === "기타").click(); ok(JSON.stringify(titles("excluded-list")) === '["요일 모름"]', "기타 탭에도 완결이 없다: " + titles("excluded-list"));
  tabs("excluded-weekday-tabs").find((t) => label(t) === "전체").click(); ok(titles("excluded-list").length === 4 && !titles("excluded-list").some((t) => t.includes("완결")), "전체 탭도 완결 제외");
  $("excluded-search").value = "완결"; $("excluded-search").dispatchEvent(new w.Event("input")); await wait(350);
  ok(titles("excluded-list").length === 0 && !$("excluded-empty").classList.contains("hidden"), "검색해도 완결은 안 나온다");
  $("excluded-search").value = ""; $("excluded-search").dispatchEvent(new w.Event("input")); await wait(350);

  // Scenario 1b: 제외됨 검색은 작가 이름(writer_names)으로도 찾는다 — 제목/저자 요약에 없어도
  showExcluded([mk("naver", 41, "제목과무관", ["tue"], { author_summary: "요약에없음", writer_names: ["김작가"] }), mk("naver", 42, "다른작품", ["tue"])]);
  $("excluded-search").value = "김작가"; $("excluded-search").dispatchEvent(new w.Event("input")); await wait(350); await idle();
  ok(JSON.stringify(titles("excluded-list")) === '["제목과무관"]', "작가 이름으로 검색: " + titles("excluded-list"));
  $("excluded-search").value = ""; $("excluded-search").dispatchEvent(new w.Event("input")); await wait(350); await idle();
  showExcluded(rows);

  // Scenario 2: 몇 개를 숨겼는지는 화면에 띄우지 않는다(빠른 상태 스캔의 실행 이력에 남긴다) — 안내 줄 칸 자체가 없다. 완결만 있으면 빈 목록 안내
  ok(!note(), "안내 줄 칸이 없다");
  showExcluded([mk("naver", 9, "완결뿐", [], { is_finished: true })]); ok(titles("excluded-list").length === 0 && !$("excluded-empty").classList.contains("hidden"), "완결만 있으면 빈 목록 + 안내");

  // Scenario 3: 구독해제 화면과 전체목록은 그대로 — 완결이어도 보인다
  w.__unsub = [mk("naver", 21, "구독해제 완결", [], { status: "unsubscribed", ever_subscribed: true, is_finished: true })];
  run("subscriptionCache.unsubscribed = __unsub; renderSubscriptionTab('unsubscribed');");
  ok(titles("unsubscribed-list").length === 1 && !$("unsubscribed-finished-note"), "구독해제 화면에는 영향 없음");
  w.__list = [mk("naver", 31, "목록의 완결", ["tue"], { status: null, is_finished: true })];
  run("naverListNaverItems = __list; naverListKakaoItems = []; combineNaverListCache(); renderNaverList();");
  ok(titles("naver-list-grid").length === 1, "전체목록에는 영향 없음");
  done();
})();
