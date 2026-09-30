// 구독 상태가 바뀌면 다른 탭의 목록 캐시도 갱신된다 — 구독해제/제외됨의 "목록으로"를 누른 뒤 전체목록에 바로 나타나야 하고, 반대도 마찬가지.
const { makeApp, sleep, ok, done } = require("./harness");
(async () => {
  const dbRows = { "777": { status: "excluded", ever: true, title: "제외작" } };            // 이 앱이 추적 중인 작품(네이버)
  const naverSource = [{ title_id: "100", title: "기존작" }, { title_id: "777", title: "제외작" }]; // 네이버 전체 목록(요일별)
  const out = (id, t) => ({ title_id: id, title: t, thumbnail_url: `https://n/${id}`, author_summary: "작가", is_adult: false, is_new: false, is_paused: false, has_new_episode: false, status: dbRows[id]?.status || null, ever_subscribed: !!dbRows[id]?.ever });
  const routes = {
    "/api/settings/kakao-webtoons-enabled": { enabled: false }, "/api/settings/webtoon-server": { webtoon_server_url: "" },
    "/api/naver-list": () => naverSource.filter((w) => !["excluded", "unsubscribed"].includes(dbRows[w.title_id]?.status)).map((w) => out(w.title_id, w.title)),
    "/api/webtoons": (o, url) => { const st = new URL(url, "http://x").searchParams.get("status"); return Object.entries(dbRows).filter(([, v]) => v.status === st).map(([id, v]) => ({ ...out(id, v.title), tags: [], writer_names: ["작가"], is_finished: false })); },
    "/api/webtoons/777/unregister": (o) => { dbRows["777"].status = "unregistered"; return { title_id: "777", status: "unregistered", ever_subscribed: true }; },
    "/api/naver-list/100/exclude": () => { dbRows["100"] = { status: "excluded", ever: false, title: "기존작" }; return { title_id: "100", status: "excluded", ever_subscribed: false }; },
  };
  const { w, run, calls } = makeApp(routes); await sleep(60);
  const titles = (sel) => Array.from(w.document.querySelectorAll(`${sel} .webtoon-card-title`)).map((e) => e.textContent);
  const click = (sel, label) => Array.from(w.document.querySelectorAll(`${sel} .webtoon-card-actions button`)).find((b) => b.textContent.trim() === label).click();

  run("window.__p = loadNaverList(false);"); await w.__p;
  ok(JSON.stringify(titles("#naver-list-grid")) === '["기존작"]', "처음 전체목록: 제외된 작품은 안 보임");
  run('window.__p = loadSubscriptionTab("excluded");'); await w.__p; await sleep(20);
  ok(JSON.stringify(titles("#excluded-list")) === '["제외작"]', "제외됨 탭에 제외작이 보임");
  click("#excluded-list", "목록으로"); await sleep(40);
  ok(titles("#excluded-list").length === 0, "목록으로를 누르면 제외됨 탭에서 사라짐");
  const before = calls.filter((c) => c.url.startsWith("/api/naver-list")).length;
  run('switchToTab("naver-list");'); await sleep(80);
  ok(calls.filter((c) => c.url.startsWith("/api/naver-list")).length > before, "전체목록으로 돌아오면 서버에서 다시 받음(60초 캐시에 막히지 않음)");
  ok(JSON.stringify(titles("#naver-list-grid").sort()) === JSON.stringify(["기존작", "제외작"].sort()), "'목록으로' 보낸 작품이 새로고침 없이 전체목록에 나타남: " + JSON.stringify(titles("#naver-list-grid")));

  // 반대 방향: 전체목록에서 제외 → 제외됨 탭에 바로 나타남
  click("#naver-list-grid .webtoon-card[data-title-id='100']", "목록제외"); await sleep(40);
  run('switchToTab("excluded");'); await sleep(80);
  ok(titles("#excluded-list").includes("기존작"), "전체목록에서 제외한 작품이 60초를 기다리지 않고 제외됨 탭에 나타남: " + JSON.stringify(titles("#excluded-list")));
  done();
})();
