// 웹툰 전체목록: 구독 중 카드와 구독하지 않은 카드의 버튼이 같은 줄에 오도록 맞춘다(네이버/카카오 모두).
// 구독 중 카드는 "구독해제 / 제외됨으로 / 뷰어 아이콘" 3줄, 구독하지 않은 카드는 "구독 / 목록제외" 2줄이라 카드 아래쪽 정렬에서 한 줄씩 어긋났다.
// 규칙(지금 보이는 목록 기준): 뷰어 버튼이 실제로 보이는 카드가 하나라도 있으면 모든 카드를 3줄로(뷰어 버튼이 없는 카드에는 보이지 않는 자리),
// 하나도 없으면 모든 카드를 2줄로 둔다 — "아직 미등록만"처럼 뷰어 버튼이 필요 없는 목록에 쓸데없는 빈 줄이 생기지 않게.
const { makeApp, sleep, ok, done } = require("./harness");
const naver = (id, title, extra = {}) => ({ title_id: String(id), title, thumbnail_url: `https://n/${id}.jpg`, author_summary: "작가", is_adult: false, is_new: false, is_paused: false, has_new_episode: false, status: null, ever_subscribed: false, ...extra });
const kakao = (id, title, extra = {}) => ({ title_id: id, title, thumbnail_url: `https://k/${id}`, author_summary: "작가", is_adult: false, is_new: false, is_paused: false, has_new_episode: false, status: null, ever_subscribed: false, ...extra });
const SUB = { status: "active", ever_subscribed: true };

async function render({ serverUrl = "http://viewer", items, inViewer = () => true }) {
  const routes = {
    "/api/naver-list": () => items.filter((i) => i.platform !== "kakao"),
    "/api/kakao-list": () => ({ items: items.filter((i) => i.platform === "kakao"), version: "v1", refreshing: false, refreshed_at: "2026-09-30T01:00:00+00:00" }),
    "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: serverUrl }, "/api/webtoons": () => [], "/api/kakao-webtoons": () => [],
    "/api/webtoon-server/lookup": (opts, url) => ({ url: inViewer(decodeURIComponent((url.match(/title=([^&]+)/) || [])[1] || "")) ? "http://viewer/x" : null }),
  };
  const { w, run } = makeApp(routes); await sleep(50); run(`kakaoWebtoonsEnabled = true; webtoonServerConfigured = ${serverUrl ? "true" : "false"};`);
  run("window.__p = loadNaverList(false);"); await w.__p; await sleep(100);
  const card = (title) => [...w.document.querySelectorAll("#naver-list-grid > .webtoon-card")].find((c) => c.querySelector(".webtoon-card-title").textContent === title);
  // 화면에서 실제로 자리를 차지하는 줄(display:none이 아닌 것)
  const rows = (title) => [...card(title).querySelectorAll(".webtoon-card-actions > *")].filter((el) => el.style.display !== "none");
  const setFilter = async (value) => { const sel = w.document.getElementById("naver-list-filter-status"); sel.value = value; sel.dispatchEvent(new w.Event("change")); await sleep(60); };
  return { w, run, rows, card, setFilter };
}
const MIXED = [naver(1, "네이버 구독 중", SUB), naver(2, "네이버 미구독"), kakao(71000001, "카카오 구독 중", { ...SUB, platform: "kakao" }), kakao(71000002, "카카오 미구독", { platform: "kakao" })];
const TITLES = ["네이버 구독 중", "네이버 미구독", "카카오 구독 중", "카카오 미구독"];

(async () => {
  // Scenario 1: 구독 중 카드의 뷰어 버튼이 보이면 네 종류 카드 모두 3줄 — 첫째는 구독/구독해제, 둘째는 목록제외/제외됨으로, 셋째는 뷰어 자리
  let { rows, card } = await render({ items: MIXED });
  ok(TITLES.every((t) => rows(t).length === 3), "섞인 목록: 모든 카드가 3줄: " + TITLES.map((t) => rows(t).length));
  ok(rows("네이버 미구독")[0].textContent === "구독" && rows("네이버 구독 중")[0].textContent === "구독해제" && rows("카카오 미구독")[1].textContent === "목록제외" && rows("카카오 구독 중")[1].textContent === "제외됨으로", "첫째/둘째 줄이 같은 자리");
  // 구독하지 않은 카드의 셋째 줄은 눈에 안 보이고 눌리지 않는 자리(구독 중 카드의 뷰어 아이콘과 같은 버튼이라 높이가 같다), 뷰어 확인 대상이 아니다
  for (const t of ["네이버 미구독", "카카오 미구독"]) {
    const slot = rows(t)[2];
    ok(slot.tagName === "BUTTON" && slot.style.visibility === "hidden" && slot.disabled && slot.getAttribute("aria-hidden") === "true" && slot.tabIndex === -1 && slot.className === rows("네이버 구독 중")[2].className && !slot.hasAttribute("data-viewer-check-title"), t + ": 보이지 않는 뷰어 자리");
  }
  ok(rows("네이버 구독 중")[2].style.visibility !== "hidden", "구독 중 카드의 뷰어 버튼은 보인다");

  // Scenario 2: 구독 중 카드가 하나도 없는 목록("아직 미등록만" 등)에는 빈 줄을 만들지 않는다 — 모두 2줄
  ({ rows } = await render({ items: [naver(2, "네이버 미구독"), kakao(71000002, "카카오 미구독", { platform: "kakao" })] }));
  ok(["네이버 미구독", "카카오 미구독"].every((t) => rows(t).length === 2), "미구독만: 모두 2줄: " + ["네이버 미구독", "카카오 미구독"].map((t) => rows(t).length));

  // Scenario 3: 구독 중 카드가 있어도 뷰어에 있는 작품이 하나도 없으면(뷰어 확인 뒤 아이콘이 모두 숨음) 줄이 접혀 모두 2줄
  ({ rows } = await render({ items: MIXED, inViewer: () => false }));
  ok(TITLES.every((t) => rows(t).length === 2), "뷰어에 있는 작품이 없음: 모두 2줄: " + TITLES.map((t) => rows(t).length));
  // Scenario 4: 일부만 뷰어에 있으면 3줄을 유지하고, 뷰어에 없는 구독 중 카드는 아이콘을 숨긴 자리를 남긴다
  ({ rows } = await render({ items: MIXED, inViewer: (t) => t === "네이버 구독 중" }));
  ok(TITLES.every((t) => rows(t).length === 3), "일부만 뷰어에 있음: 모두 3줄: " + TITLES.map((t) => rows(t).length));
  ok(rows("카카오 구독 중")[2].style.visibility === "hidden" && rows("네이버 구독 중")[2].style.visibility !== "hidden", "뷰어에 없는 카드는 숨긴 자리, 있는 카드는 보이는 버튼");

  // Scenario 5: 필터를 바꾸면 그 목록 기준으로 다시 맞춘다 — 전체(3줄) → 아직 미등록만(2줄) → 다시 전체(3줄)
  let ctx = await render({ items: MIXED });
  ok(TITLES.every((t) => ctx.rows(t).length === 3), "전체: 3줄");
  await ctx.setFilter("not-active");
  ok(["네이버 미구독", "카카오 미구독"].every((t) => ctx.rows(t).length === 2), "아직 미등록만: 2줄(빈 줄 없음): " + ["네이버 미구독", "카카오 미구독"].map((t) => ctx.rows(t).length));
  await ctx.setFilter("all");
  ok(TITLES.every((t) => ctx.rows(t).length === 3), "다시 전체: 3줄");

  // Scenario 6: 뷰어 서버를 설정하지 않았으면 처음부터 뷰어 줄이 없다 — 모두 2줄이고 자리도 만들지 않는다
  ({ rows, card } = await render({ serverUrl: "", items: MIXED }));
  ok(TITLES.every((t) => rows(t).length === 2) && !card("네이버 미구독").querySelector("[data-viewer-slot]"), "뷰어 서버 없음: 모두 2줄, 자리 없음");
  done();
})();
