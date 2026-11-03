// 웹툰 전체목록: 구독 중 카드와 구독하지 않은 카드의 버튼이 같은 줄에 오도록 맞춘다(네이버/카카오 모두).
// 예전에는 구독 중 카드는 "구독해제 / 제외됨으로 / 뷰어 아이콘" 3줄, 구독하지 않은 카드는 "구독 / 목록제외" 2줄이라 카드 아래쪽 정렬에서 한 줄씩 어긋났고,
// 줄을 맞추려고 뷰어가 보이는 카드가 있을 때만 보이지 않는 자리를 끼워 넣었다. 지금은 모든 카드에 "분석" 버튼이 있어서 어떤 목록이든 모든 카드가 항상 3줄이다:
// 첫째 구독/구독해제, 둘째 목록제외/제외됨으로, 셋째 뷰어 아이콘+분석(뷰어 버튼이 보일 때) 또는 분석 단독(자리 표시 없음). 분석 버튼 자체는 test_card_analyze_button.js가 본다.
const { makeApp, idle, ok, done } = require("./harness");
const { naver, kakao } = require("./factories");
const SUB = { status: "active", ever_subscribed: true };

async function render({ serverUrl = "http://viewer", items, inViewer = () => true }) {
  const routes = {
    "/api/naver-list": () => items.filter((i) => i.platform !== "kakao"),
    "/api/kakao-list": () => ({ items: items.filter((i) => i.platform === "kakao"), version: "v1", refreshing: false, refreshed_at: "2026-09-30T01:00:00+00:00" }),
    "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: serverUrl }, "/api/webtoons": () => [], "/api/kakao-webtoons": () => [],
    "/api/webtoon-server/lookup": (opts, url) => ({ url: inViewer(decodeURIComponent((url.match(/title=([^&]+)/) || [])[1] || "")) ? "http://viewer/x" : null }),
  };
  const { w, run } = makeApp(routes); await idle(); run(`kakaoWebtoonsEnabled = true; webtoonServerConfigured = ${serverUrl ? "true" : "false"};`);
  run("window.__p = loadNaverList(false);"); await w.__p; await idle();
  const card = (title) => [...w.document.querySelectorAll("#naver-list-grid > .webtoon-card")].find((c) => c.querySelector(".webtoon-card-title").textContent === title);
  // 화면에서 실제로 자리를 차지하는 줄(display:none이 아닌 것)
  const rows = (title) => [...card(title).querySelectorAll(".webtoon-card-actions > *")].filter((el) => el.style.display !== "none");
  const setFilter = async (value) => { const sel = w.document.getElementById("naver-list-filter-status"); sel.value = value; sel.dispatchEvent(new w.Event("change")); await idle(); };
  return { w, run, rows, card, setFilter };
}
const MIXED = [naver(1, "네이버 구독 중", SUB), naver(2, "네이버 미구독"), kakao(71000001, "카카오 구독 중", { ...SUB, platform: "kakao" }), kakao(71000002, "카카오 미구독", { platform: "kakao" })];
const TITLES = ["네이버 구독 중", "네이버 미구독", "카카오 구독 중", "카카오 미구독"];
const all3 = (rows, titles = TITLES) => titles.every((t) => rows(t).length === 3);
const counts = (rows, titles = TITLES) => titles.map((t) => rows(t).length);

(async () => {
  // Scenario 1: 섞인 목록 — 네 종류 카드 모두 3줄. 첫째는 구독/구독해제, 둘째는 목록제외/제외됨으로, 셋째는 뷰어+분석(구독 중이고 뷰어에 있음) 또는 분석 단독. 보이지 않는 자리 표시는 없다
  let { rows, card } = await render({ items: MIXED });
  ok(all3(rows), "섞인 목록: 모든 카드가 3줄: " + counts(rows));
  ok(rows("네이버 미구독")[0].textContent === "구독" && rows("네이버 구독 중")[0].textContent === "구독해제" && rows("카카오 미구독")[1].textContent === "목록제외" && rows("카카오 구독 중")[1].textContent === "제외됨으로", "첫째/둘째 줄 버튼");
  ok(rows("네이버 구독 중")[2].classList.contains("card-viewer-row") && rows("네이버 미구독")[2].dataset.analyze === "1", "셋째 줄: 뷰어 버튼이 있으면 뷰어+분석 줄, 없으면 분석 단독");
  ok(!card("네이버 미구독").querySelector("[data-viewer-slot]") && !card("카카오 미구독").querySelector("[data-viewer-slot]"), "보이지 않는 자리 표시는 쓰지 않는다");

  // Scenario 2: 구독 중 카드가 하나도 없는 목록("아직 미등록만" 등)도 3줄 — 셋째 줄은 분석 단독
  ({ rows } = await render({ items: [naver(2, "네이버 미구독"), kakao(71000002, "카카오 미구독", { platform: "kakao" })] }));
  ok(all3(rows, ["네이버 미구독", "카카오 미구독"]), "미구독만: 모두 3줄: " + counts(rows, ["네이버 미구독", "카카오 미구독"]));

  // Scenario 3: 구독 중 카드가 있어도 뷰어에 있는 작품이 하나도 없으면(뷰어 확인 뒤 아이콘이 모두 숨음) 숨은 아이콘은 줄을 차지하지 않고 분석이 아래 줄 — 모두 3줄
  ({ rows } = await render({ items: MIXED, inViewer: () => false }));
  ok(all3(rows) && TITLES.every((t) => rows(t)[2].dataset.analyze === "1"), "뷰어에 있는 작품이 없음: 모두 3줄(분석 단독): " + counts(rows));
  // Scenario 4: 일부만 뷰어에 있으면 — 있는 카드는 뷰어 아이콘 옆 분석, 없는 카드는 아이콘이 숨고(줄 없음) 아래 줄 분석. 모두 3줄
  ({ rows, card } = await render({ items: MIXED, inViewer: (t) => t === "네이버 구독 중" }));
  ok(all3(rows), "일부만 뷰어에 있음: 모두 3줄: " + counts(rows));
  ok(rows("네이버 구독 중")[2].classList.contains("card-viewer-row") && rows("카카오 구독 중")[2].dataset.analyze === "1" && card("카카오 구독 중").querySelector("[data-viewer-check-title]").style.display === "none", "뷰어에 없는 카드는 숨긴 아이콘 + 분석 줄");

  // Scenario 5: 필터를 바꿔도(전체 → 아직 미등록만 → 전체) 모든 카드가 3줄
  let ctx = await render({ items: MIXED });
  ok(all3(ctx.rows), "전체: 3줄");
  await ctx.setFilter("not-active");
  ok(all3(ctx.rows, ["네이버 미구독", "카카오 미구독"]), "아직 미등록만: 3줄: " + counts(ctx.rows, ["네이버 미구독", "카카오 미구독"]));
  await ctx.setFilter("all");
  ok(all3(ctx.rows), "다시 전체: 3줄");

  // Scenario 6: 뷰어 서버를 설정하지 않았으면 처음부터 뷰어 아이콘이 없다 — 모두 3줄(셋째는 분석), 자리도 만들지 않는다
  ({ rows, card } = await render({ serverUrl: "", items: MIXED }));
  ok(all3(rows) && TITLES.every((t) => rows(t)[2].dataset.analyze === "1" && !card(t).querySelector("[data-viewer-check-title], [data-viewer-slot]")), "뷰어 서버 없음: 모두 3줄(분석), 뷰어/자리 없음: " + counts(rows));
  done();
})();
