// 웹툰 전체목록: 구독 중인 카드에 "제외됨으로" 버튼 — 구독을 해제하는 게 아니라 강제로 제외됨으로 보낸다(네이버/카카오). 확인 창을 거친다.
const { makeApp, sleep, ok, done } = require("./harness");
const naver = (id, title, extra = {}) => ({ title_id: String(id), title, thumbnail_url: `https://n/${id}.jpg`, author_summary: "작가", is_adult: false, is_new: false, is_paused: false, has_new_episode: false, status: null, ever_subscribed: false, ...extra });
const kakao = (id, title, extra = {}) => ({ title_id: id, title, thumbnail_url: `https://k/${id}`, author_summary: "작가", is_adult: false, is_new: false, is_paused: false, has_new_episode: false, status: null, ever_subscribed: false, ...extra });
(async () => {
  const posts = [];
  const routes = {
    "/api/naver-list": () => [naver(3, "네이버 구독 중", { status: "active", ever_subscribed: true }), naver(4, "네이버 미등록")],
    "/api/kakao-list": () => ({ items: [kakao(71000001, "카카오 구독 중", { status: "active", ever_subscribed: true }), kakao(71000002, "카카오 미등록")], version: "v1", refreshing: false, refreshed_at: "2026-09-30T01:00:00+00:00" }),
    "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" }, "/api/webtoons": () => [], "/api/kakao-webtoons": () => [],
    "/api/naver-list/3/exclude": (opts) => { posts.push(["naver", JSON.parse(opts.body)]); return { ...naver(3, "네이버 구독 중"), status: "excluded", ever_subscribed: true }; },
    "/api/kakao-webtoons/71000001/exclude": (opts) => { posts.push(["kakao", JSON.parse(opts.body)]); return { ...kakao(71000001, "카카오 구독 중"), status: "excluded", ever_subscribed: true }; },
  };
  const { w, run } = makeApp(routes); await sleep(50); run("kakaoWebtoonsEnabled = true;");
  const confirms = []; let answer = false; w.confirm = (m) => { confirms.push(m); return answer; };
  run("window.__p = loadNaverList(false);"); await w.__p;
  const card = (title) => [...w.document.querySelectorAll("#naver-list-grid > .webtoon-card")].find((c) => c.querySelector(".webtoon-card-title").textContent === title);
  const buttons = (c) => [...c.querySelectorAll(".webtoon-card-actions button")].map((b) => b.textContent);

  // Scenario 1: 구독 중 카드에는 "구독해제"와 "제외됨으로"가 있고, 구독하지 않은 카드에는 "제외됨으로"가 없다(거긴 "구독"/"목록제외")
  ok(JSON.stringify(buttons(card("네이버 구독 중"))) === JSON.stringify(["구독해제", "제외됨으로"]), "네이버 구독 중: " + buttons(card("네이버 구독 중")));
  ok(JSON.stringify(buttons(card("카카오 구독 중"))) === JSON.stringify(["구독해제", "제외됨으로"]), "카카오 구독 중: " + buttons(card("카카오 구독 중")));
  ok(!buttons(card("네이버 미등록")).includes("제외됨으로") && !buttons(card("카카오 미등록")).includes("제외됨으로"), "미등록 카드에는 없다");

  // Scenario 2: 확인 창을 취소하면 요청이 나가지 않고, 안내에 다운로드 중단/다시 구독 안 함/받은 파일 유지가 들어 있다
  const clickExclude = (title) => [...card(title).querySelectorAll(".webtoon-card-actions button")].find((b) => b.textContent === "제외됨으로").click();
  answer = false; clickExclude("네이버 구독 중"); await sleep(30);
  ok(posts.length === 0 && confirms.length === 1, "취소하면 요청이 없다");
  ok(/다운로드가 멈추고/.test(confirms[0]) && /다시 구독하지 않/.test(confirms[0]) && /받은 파일은 그대로/.test(confirms[0]) && confirms[0].includes("네이버 구독 중"), "안내 문구: " + confirms[0].replace(/\n/g, " / "));

  // Scenario 3: 확인하면 제외 API(구독해제 API가 아님)로 간다 — 네이버는 /api/naver-list, 카카오는 /api/kakao-webtoons
  answer = true; clickExclude("네이버 구독 중"); await sleep(40);
  ok(posts.length === 1 && posts[0][0] === "naver" && posts[0][1].title === "네이버 구독 중" && posts[0][1].thumbnail_url === "https://n/3.jpg", "네이버 제외 요청: " + JSON.stringify(posts[0]));
  clickExclude("카카오 구독 중"); await sleep(40);
  ok(posts.length === 2 && posts[1][0] === "kakao" && posts[1][1].title === "카카오 구독 중", "카카오 제외 요청: " + JSON.stringify(posts[1]));
  done();
})();
