// 웹툰 전체목록 카드의 "분석" 버튼 — 누르면 수동 다운로드 탭으로 이동해 그 작품의 분석 화면을 연다(네이버: titleId, 카카오: 작품 번호).
// 자리: 뷰어 버튼이 **보이는** 카드는 뷰어 아이콘 옆(같은 줄), 뷰어 버튼이 없거나 숨겨진 카드는 마지막 텍스트 버튼(목록제외/제외됨으로) 아래에 같은 크기의 버튼으로.
// 그래서 전체목록의 모든 카드는 항상 3줄이다(구독/구독해제, 목록제외/제외됨으로, 뷰어 줄 또는 분석 줄). 다른 탭(제외됨/구독해제)에는 분석 버튼이 없다.
const { makeApp, idle, ok, done } = require("./harness");
const { naver, kakao, subscribed } = require("./factories");
const SUB = { status: "active", ever_subscribed: true };
(async () => {
  const analyzed = [];
  async function render({ serverUrl = "http://viewer", items, inViewer = () => true }) {
    const routes = {
      "/api/naver-list": () => items.filter((i) => i.platform !== "kakao"),
      "/api/kakao-list": () => ({ items: items.filter((i) => i.platform === "kakao"), version: "v1", refreshing: false, refreshed_at: "2026-09-30T01:00:00+00:00" }),
      "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: serverUrl }, "/api/webtoons": () => [], "/api/kakao-webtoons": () => [],
      "/api/webtoon-server/lookup": (opts, url) => ({ url: inViewer(decodeURIComponent((url.match(/title=([^&]+)/) || [])[1] || "")) ? "http://viewer/x" : null }),
      "/api/manual-download/analyze": (opts, url) => { analyzed.push(["naver", new URL(url, "http://x").searchParams.get("title_id")]); return { title_id: "1", title: "분석한 작품", webtoon_type: "webtoon", is_finished: false, episodes: [], subscribed: false, thumbnail_url: "" }; },
      "/api/kakao-manual/analyze": (opts, url) => { analyzed.push(["kakao", new URL(url, "http://x").searchParams.get("series_id")]); return { series_id: 1, title: "분석한 카카오", episodes: [], tickets: {}, subscribed: false, stats: {}, plan: {} }; },
    };
    const app = makeApp(routes); await idle(); app.run(`kakaoWebtoonsEnabled = true; webtoonServerConfigured = ${serverUrl ? "true" : "false"};`);
    app.run("window.__p = loadNaverList(false);"); await app.w.__p; await idle();
    const card = (title) => [...app.w.document.querySelectorAll("#naver-list-grid > .webtoon-card")].find((c) => c.querySelector(".webtoon-card-title").textContent === title);
    const actions = (title) => card(title).querySelector(".webtoon-card-actions");
    const lines = (title) => [...actions(title).children].filter((el) => el.style.display !== "none");        // 화면에서 줄을 차지하는 것
    const analyzeBtn = (title) => actions(title).querySelector("[data-analyze]");
    return { ...app, card, actions, lines, analyzeBtn };
  }
  const ITEMS = [naver(1, "네이버 구독 중", SUB), naver(2, "네이버 미구독"), kakao(71000001, "카카오 구독 중", { ...SUB, platform: "kakao" }), kakao(71000002, "카카오 미구독", { platform: "kakao" })];
  const T = ["네이버 구독 중", "네이버 미구독", "카카오 구독 중", "카카오 미구독"];

  // Scenario 1: 뷰어 버튼이 보이는 구독 중 카드 — 분석 버튼이 뷰어 아이콘 바로 옆(같은 줄)에 작게. 줄 수는 3줄(텍스트 2 + 뷰어/분석 줄 1)
  let ctx = await render({ items: ITEMS });
  for (const t of ["네이버 구독 중", "카카오 구독 중"]) {
    const row = ctx.lines(t)[2], icon = ctx.actions(t).querySelector("[data-viewer-check-title]"), btn = ctx.analyzeBtn(t);
    ok(ctx.lines(t).length === 3 && row.contains(icon) && row.contains(btn) && icon.nextElementSibling === btn, `${t}: 뷰어 아이콘 옆에 분석 버튼, 3줄 (${ctx.lines(t).length}줄)`);
    ok(btn.textContent.trim() === "분석" && btn.classList.contains("analyze-inline"), `${t}: 작은(inline) 분석 버튼`);
  }
  // Scenario 2: 구독하지 않은 카드 — 목록제외 아래에 같은 크기(보통 텍스트 버튼)의 분석 버튼. 보이지 않는 자리(placeholder)는 없다
  for (const t of ["네이버 미구독", "카카오 미구독"]) {
    const L = ctx.lines(t), btn = ctx.analyzeBtn(t);
    ok(L.length === 3 && L[0].textContent === "구독" && L[1].textContent === "목록제외" && L[2] === btn && btn.parentElement === ctx.actions(t) && !btn.classList.contains("analyze-inline"), `${t}: 구독 / 목록제외 / 분석 (${L.map((e) => e.textContent.trim())})`);
    ok(!ctx.actions(t).querySelector("[data-viewer-slot]") && btn.className === L[1].className, `${t}: 자리 표시 없음, 목록제외와 같은 크기(클래스 ${btn.className})`);
  }
  ok(T.every((t) => ctx.lines(t).length === 3), "모든 카드 3줄: " + T.map((t) => ctx.lines(t).length));

  // Scenario 3: 뷰어에 없는 구독 중 카드(아이콘이 숨음) — 제외됨으로 아래에 같은 크기의 분석 버튼, 숨은 아이콘은 자리를 차지하지 않는다. 일부만 뷰어에 있어도 모두 3줄
  ctx = await render({ items: ITEMS, inViewer: (t) => t === "네이버 구독 중" });
  const nk = ctx.lines("카카오 구독 중"); ok(nk.length === 3 && nk[0].textContent === "구독해제" && nk[1].textContent === "제외됨으로" && nk[2] === ctx.analyzeBtn("카카오 구독 중") && !nk[2].classList.contains("analyze-inline"), "뷰어에 없는 구독 중 카드: " + nk.map((e) => e.textContent.trim()));
  ok(ctx.lines("네이버 구독 중")[2].contains(ctx.analyzeBtn("네이버 구독 중")) && ctx.analyzeBtn("네이버 구독 중").classList.contains("analyze-inline"), "뷰어에 있는 카드는 그대로 아이콘 옆");
  ok(T.every((t) => ctx.lines(t).length === 3), "일부만 뷰어에 있어도 모두 3줄: " + T.map((t) => ctx.lines(t).length));
  ctx = await render({ items: ITEMS, inViewer: () => false });
  ok(T.every((t) => ctx.lines(t).length === 3 && ctx.lines(t)[2] === ctx.analyzeBtn(t) && !ctx.analyzeBtn(t).classList.contains("analyze-inline")), "뷰어에 있는 작품이 없으면 모두 아래 줄 분석 버튼: " + T.map((t) => ctx.lines(t).length));
  ctx = await render({ serverUrl: "", items: ITEMS });
  ok(T.every((t) => ctx.lines(t).length === 3 && ctx.lines(t)[2] === ctx.analyzeBtn(t)), "뷰어 서버가 없으면 처음부터 아래 줄 분석 버튼");

  // Scenario 4: 클릭 — 수동 다운로드 탭으로 이동하고(플랫폼 선택/입력칸도 맞춘다) 그 작품을 분석한다. 네이버는 titleId, 카카오는 작품 번호
  ctx = await render({ items: ITEMS });
  const $ = (id) => ctx.w.document.getElementById(id);
  ctx.analyzeBtn("네이버 미구독").click(); await idle();
  ok(JSON.stringify(analyzed) === JSON.stringify([["naver", "2"]]), "네이버 분석 요청: " + JSON.stringify(analyzed));
  ok(!$("page-manual-download").classList.contains("hidden") && $("page-naver-list").classList.contains("hidden"), "수동 다운로드 탭으로 이동");
  ok($("manual-platform").value === "naver" && $("manual-query").value === "2" && !$("manual-result").classList.contains("hidden") && $("kakao-manual-result").classList.contains("hidden"), "플랫폼/입력칸/결과 영역: " + $("manual-platform").value + "|" + $("manual-query").value);
  ctx.run("switchToTab('naver-list');"); await idle();
  ctx.analyzeBtn("카카오 구독 중").click(); await idle();
  ok(JSON.stringify(analyzed.at(-1)) === JSON.stringify(["kakao", "71000001"]) && $("manual-platform").value === "kakao" && $("manual-query").value === "71000001" && !$("kakao-manual-result").classList.contains("hidden") && $("manual-result").classList.contains("hidden"), "카카오 분석: " + JSON.stringify(analyzed.at(-1)) + " 플랫폼 " + $("manual-platform").value);
  ctx.run("switchToTab('naver-list');"); await idle();
  ctx.actions("네이버 구독 중").querySelector("[data-viewer-check-title]").nextElementSibling.click(); await idle();
  ok(JSON.stringify(analyzed.at(-1)) === JSON.stringify(["naver", "1"]), "뷰어 옆 분석 버튼도 같은 동작");

  // Scenario 5: 분석 버튼은 전체목록 카드에만 — 제외됨/구독해제 카드에는 없다
  ctx.run("subscriptionCache.excluded = [" + JSON.stringify(naver(9, "제외된 작품", { status: "excluded" })) + "]; renderSubscriptionTab('excluded');"); await idle();
  ok(!ctx.w.document.querySelector("#excluded-list [data-analyze]") && ctx.w.document.querySelectorAll("#excluded-list .webtoon-card").length === 1, "제외됨 카드에는 분석 버튼이 없다");
  done();
})();
