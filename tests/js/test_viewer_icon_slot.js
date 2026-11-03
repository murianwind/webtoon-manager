// 웹툰 전체목록 카드: 뷰어에 없는 작품의 뷰어 아이콘은 지우지 않고 숨기며(눌리지 않음, 줄도 안 차지함), 그 카드의 셋째 줄은 분석 버튼이 차지해서 뷰어가 있든 없든 "구독해제" 버튼이 같은 줄에 정렬된다.
const { makeApp, sleep, idle, ok, done } = require("./harness");
const { naver: plainNaver, kakao: plainKakao, subscribed } = require("./factories");
const naver = subscribed(plainNaver), kakao = subscribed(plainKakao); // 이 테스트의 작품은 기본이 구독 중(뷰어 아이콘은 구독 중 카드에만 붙는다)
(async () => {
  const routes = {
    "/api/naver-list": () => [naver(1, "뷰어에 있는 작품"), naver(2, "뷰어에 없는 네이버 작품")],
    "/api/kakao-list": () => ({ items: [kakao(71000001, "뷰어에 없는 카카오 작품")], version: "v1", refreshing: false, refreshed_at: "2026-09-30T01:00:00+00:00" }),
    "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "http://viewer" }, "/api/webtoons": () => [], "/api/kakao-webtoons": () => [],
    "/api/webtoon-server/lookup": (opts, url) => (url.includes(encodeURIComponent("뷰어에 있는 작품")) ? { url: "http://viewer/a" } : { url: null }),
  };
  const { w, run } = makeApp(routes); await idle(); run("kakaoWebtoonsEnabled = true; webtoonServerConfigured = true;");
  run("window.__p = loadNaverList(false);"); await w.__p; await idle();
  const card = (title) => [...w.document.querySelectorAll("#naver-list-grid > .webtoon-card")].find((c) => c.querySelector(".webtoon-card-title").textContent === title);
  const rows = (c) => [...c.querySelectorAll(".webtoon-card-actions > *")];
  const viewer = (c) => c.querySelector("[data-viewer-check-title]");

  const withViewer = card("뷰어에 있는 작품"), noNaver = card("뷰어에 없는 네이버 작품"), noKakao = card("뷰어에 없는 카카오 작품");
  // Scenario 1: 뷰어에 있든 없든 카드마다 보이는 줄 수가 같다(구독해제 / 제외됨으로 / 뷰어+분석 또는 분석) — 숨긴 아이콘은 줄을 차지하지 않는다
  const visibleRows = (c) => rows(c).filter((el) => el.style.display !== "none");
  const counts = [withViewer, noNaver, noKakao].map((c) => visibleRows(c).length);
  ok(counts[0] === 3 && counts.every((n) => n === counts[0]), "카드마다 보이는 줄 수가 같다: " + counts);
  // Scenario 2: 구독해제는 모든 카드에서 첫 줄이고, 셋째 줄에는 분석 버튼이 있다(뷰어에 있는 카드는 아이콘 옆, 없는 카드는 단독) — 위아래로 같은 줄에 정렬된다
  ok([withViewer, noNaver, noKakao].every((c) => visibleRows(c)[0].textContent === "구독해제" && (visibleRows(c)[2].dataset.analyze === "1" || visibleRows(c)[2].querySelector("[data-analyze]") !== null)), "구독해제는 첫 줄, 셋째 줄에 분석");
  // Scenario 3: 뷰어에 없는 작품의 아이콘은 지우지 않되 눈에 안 보이고 눌리지 않으며 줄도 차지하지 않는다, 있는 작품의 아이콘은 그대로 보인다
  ok(viewer(withViewer).style.visibility !== "hidden" && !viewer(withViewer).disabled, "뷰어에 있는 작품: 아이콘이 보이고 눌린다");
  for (const c of [noNaver, noKakao]) {
    const b = viewer(c);
    ok(!!b && b.style.visibility === "hidden" && b.disabled === true && b.getAttribute("aria-hidden") === "true" && b.tabIndex === -1 && b.style.display === "none", "뷰어에 없는 작품: 숨김/비활성, 줄 없음");
  }
  done();
})();
