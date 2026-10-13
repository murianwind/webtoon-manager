// 웹툰 전체목록: 구독 중 카드와 구독하지 않은 카드가 같은 줄에 버튼이 오도록 정렬한다(네이버/카카오 모두).
// 구독 중 카드는 "구독해제 / 제외됨으로 / 뷰어 아이콘" 3줄이고, 구독하지 않은 카드는 "구독 / 목록제외" 2줄이라 카드 아래쪽 정렬에서 한 줄씩 어긋났다.
// 뷰어 서버를 설정했으면 구독하지 않은 카드에도 보이지 않는 뷰어 자리를 둬서 3줄로 맞춘다(뷰어 서버가 없으면 모두 2줄이라 그대로).
const { makeApp, sleep, ok, done } = require("./harness");
const naver = (id, title, extra = {}) => ({ title_id: String(id), title, thumbnail_url: `https://n/${id}.jpg`, author_summary: "작가", is_adult: false, is_new: false, is_paused: false, has_new_episode: false, status: null, ever_subscribed: false, ...extra });
const kakao = (id, title, extra = {}) => ({ title_id: id, title, thumbnail_url: `https://k/${id}`, author_summary: "작가", is_adult: false, is_new: false, is_paused: false, has_new_episode: false, status: null, ever_subscribed: false, ...extra });

async function render(serverUrl) {
  const routes = {
    "/api/naver-list": () => [naver(1, "네이버 구독 중", { status: "active", ever_subscribed: true }), naver(2, "네이버 미구독")],
    "/api/kakao-list": () => ({ items: [kakao(71000001, "카카오 구독 중", { status: "active", ever_subscribed: true }), kakao(71000002, "카카오 미구독")], version: "v1", refreshing: false, refreshed_at: "2026-09-30T01:00:00+00:00" }),
    "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: serverUrl }, "/api/webtoons": () => [], "/api/kakao-webtoons": () => [],
    "/api/webtoon-server/lookup": () => ({ url: null }),
  };
  const { w, run } = makeApp(routes); await sleep(50); run(`kakaoWebtoonsEnabled = true; webtoonServerConfigured = ${serverUrl ? "true" : "false"};`);
  run("window.__p = loadNaverList(false);"); await w.__p; await sleep(80);
  const card = (title) => [...w.document.querySelectorAll("#naver-list-grid > .webtoon-card")].find((c) => c.querySelector(".webtoon-card-title").textContent === title);
  return { w, rows: (title) => [...card(title).querySelectorAll(".webtoon-card-actions > *")] };
}

(async () => {
  const titles = ["네이버 구독 중", "네이버 미구독", "카카오 구독 중", "카카오 미구독"];
  // Scenario 1: 뷰어 서버가 있으면 네 종류 카드 모두 3줄이고, 첫 줄은 구독/구독해제, 둘째 줄은 목록제외/제외됨으로, 셋째 줄은 뷰어 자리(숨김 포함)다
  let { rows } = await render("http://viewer");
  ok(titles.every((t) => rows(t).length === 3), "뷰어 서버 있음: 모든 카드가 3줄: " + titles.map((t) => rows(t).length));
  ok(rows("네이버 미구독")[0].textContent === "구독" && rows("네이버 구독 중")[0].textContent === "구독해제" && rows("카카오 미구독")[1].textContent === "목록제외" && rows("카카오 구독 중")[1].textContent === "제외됨으로", "첫째/둘째 줄이 같은 자리");
  // Scenario 2: 구독하지 않은 카드의 셋째 줄은 눈에 안 보이고 눌리지 않는 자리뿐이다(구독 중 카드의 뷰어 아이콘과 같은 크기의 버튼이라 높이가 같다)
  for (const t of ["네이버 미구독", "카카오 미구독"]) {
    const slot = rows(t)[2];
    ok(slot.tagName === "BUTTON" && slot.style.visibility === "hidden" && slot.disabled && slot.getAttribute("aria-hidden") === "true" && slot.tabIndex === -1 && slot.className === rows("네이버 구독 중")[2].className, t + ": 보이지 않는 뷰어 자리");
    ok(!slot.hasAttribute("data-viewer-check-title"), t + ": 뷰어 확인 대상이 아니다(자리일 뿐)");
  }
  // Scenario 3: 뷰어 서버가 없으면 뷰어 아이콘이 처음부터 없으므로 모든 카드가 2줄 그대로이고 자리를 만들지 않는다
  ({ rows } = await render(""));
  ok(titles.every((t) => rows(t).length === 2), "뷰어 서버 없음: 모든 카드가 2줄: " + titles.map((t) => rows(t).length));
  done();
})();
