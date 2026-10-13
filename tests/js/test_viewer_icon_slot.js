// 웹툰 전체목록 카드: 뷰어에 없는 작품도 뷰어 아이콘 자리를 비워 두어서(숨김), 뷰어가 있든 없든 "구독해제" 버튼이 같은 줄에 정렬된다.
// (예전엔 뷰어에 없는 작품의 아이콘을 지워서 카드마다 버튼 줄 수가 달랐고, 카드 아래쪽 정렬이라 "구독해제"가 위아래로 어긋났다)
const { makeApp, sleep, ok, done } = require("./harness");
const naver = (id, title, extra = {}) => ({ title_id: String(id), title, thumbnail_url: `https://n/${id}.jpg`, author_summary: "작가", is_adult: false, is_new: false, is_paused: false, has_new_episode: false, status: "active", ever_subscribed: true, ...extra });
const kakao = (id, title, extra = {}) => ({ title_id: id, title, thumbnail_url: `https://k/${id}`, author_summary: "작가", is_adult: false, is_new: false, is_paused: false, has_new_episode: false, status: "active", ever_subscribed: true, ...extra });
(async () => {
  const routes = {
    "/api/naver-list": () => [naver(1, "뷰어에 있는 작품"), naver(2, "뷰어에 없는 네이버 작품")],
    "/api/kakao-list": () => ({ items: [kakao(71000001, "뷰어에 없는 카카오 작품")], version: "v1", refreshing: false, refreshed_at: "2026-09-30T01:00:00+00:00" }),
    "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "http://viewer" }, "/api/webtoons": () => [], "/api/kakao-webtoons": () => [],
    "/api/webtoon-server/lookup": (opts, url) => (url.includes(encodeURIComponent("뷰어에 있는 작품")) ? { url: "http://viewer/a" } : { url: null }),
  };
  const { w, run } = makeApp(routes); await sleep(50); run("kakaoWebtoonsEnabled = true; webtoonServerConfigured = true;");
  run("window.__p = loadNaverList(false);"); await w.__p; await sleep(80);
  const card = (title) => [...w.document.querySelectorAll("#naver-list-grid > .webtoon-card")].find((c) => c.querySelector(".webtoon-card-title").textContent === title);
  const rows = (c) => [...c.querySelectorAll(".webtoon-card-actions > *")];
  const viewer = (c) => c.querySelector("[data-viewer-check-title]");

  const withViewer = card("뷰어에 있는 작품"), noNaver = card("뷰어에 없는 네이버 작품"), noKakao = card("뷰어에 없는 카카오 작품");
  // Scenario 1: 뷰어에 있든 없든 카드마다 버튼 줄 수가 같다(구독해제 … 뷰어 자리)
  const counts = [withViewer, noNaver, noKakao].map((c) => rows(c).length);
  ok(counts[0] >= 2 && counts.every((n) => n === counts[0]), "카드마다 버튼 줄 수가 같다: " + counts);
  // Scenario 2: 구독해제는 모든 카드에서 첫 줄이고, 뷰어 아이콘은 모든 카드에서 마지막 줄 — 위아래로 같은 줄에 정렬된다
  ok([withViewer, noNaver, noKakao].every((c) => rows(c)[0].textContent === "구독해제" && rows(c)[rows(c).length - 1] === viewer(c)), "구독해제는 첫 줄, 뷰어 자리는 마지막 줄");
  // Scenario 3: 뷰어에 없는 작품의 아이콘은 지우지 않고 자리만 남긴다(눈에 안 보이고 눌리지 않음), 있는 작품의 아이콘은 그대로 보인다
  ok(viewer(withViewer).style.visibility !== "hidden" && !viewer(withViewer).disabled, "뷰어에 있는 작품: 아이콘이 보이고 눌린다");
  for (const c of [noNaver, noKakao]) {
    const b = viewer(c);
    ok(!!b && b.style.visibility === "hidden" && b.disabled === true && b.getAttribute("aria-hidden") === "true" && b.tabIndex === -1, "뷰어에 없는 작품: 자리만 남고 숨김/비활성");
  }
  done();
})();
