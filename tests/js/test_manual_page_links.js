// 수동 다운로드: 검색 결과 카드의 제목 → 작품 페이지 링크, 분석 결과 제목 옆 "작품 페이지" 버튼 (네이버 / 카카오페이지)
const { makeApp, sleep, ok, done } = require("./harness");
(async () => {
  const kakaoSearch = [
    { title_id: 111, title: "블루엔젤 리부트 [19세 완전판]", thumbnail_url: "", authors: "치선", status: "연재", subscription: "active" },
    { title_id: 222, title: "흑기사[단행본]", thumbnail_url: "", authors: "이현세", status: "완결", subscription: null },
  ];
  const naverSearch = [{ title_id: "333", title: "네이버 작품", thumbnail_url: "" }];
  const kakaoAnalysis = (id, title) => ({ series_id: id, title, folder: "/dl/x", mode: "new_folder", existing_count: 0, marker: null, to_download_count: 1, locked_count: 0, downloaded_count: 0,
    before_start_count: 0, cookie_saved: true, logged_in: true, thumbnail_url: "t", authors: "a", subscription: null, tickets: null, site_total: 1, listed_count: 1, hidden_count: 0,
    excluded_video_count: 0, episodes: [{ number: 1, subtitle: "1화", state: "free", expire: null, free_at: null, downloaded: false, before_start: false, selectable: true }] });
  let kakaoCur = kakaoAnalysis(111, "블루엔젤 리부트");
  const routes = {
    "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" },
    "/api/kakao-manual/search": kakaoSearch, "/api/kakao-manual/analyze": () => kakaoCur,
    "/api/manual-download/search": naverSearch,
    "/api/manual-download/analyze": { title_id: "333", title: "네이버 작품", episodes: [{ episode_no: 1, subtitle: "1화", owned: false, is_locked: false }] },
  };
  const { w, run } = makeApp(routes); await sleep(60); run("kakaoWebtoonsEnabled = true;");
  const $ = (id) => w.document.getElementById(id);
  run(`switchToTab("manual-download");`);
  const setPlatform = (p) => { $("manual-platform").value = p; $("manual-platform").dispatchEvent(new w.Event("change")); };
  const search = async (q) => { $("manual-query").value = q; $("btn-manual-analyze").click(); await sleep(40); };
  const cardLinks = () => [...w.document.querySelectorAll("#manual-search-results .webtoon-card-title a")];

  // Scenario 1: 카카오 검색 결과 카드의 제목을 누르면 카카오페이지 작품 페이지가 새 탭으로 열린다
  setPlatform("kakao"); await search("블루");
  let links = cardLinks();
  ok(links.length === 2, "카카오 카드 제목이 링크로 만들어짐: " + links.length);
  ok(links[0].href === "https://page.kakao.com/content/111" && links[1].href === "https://page.kakao.com/content/222", "카카오 카드 링크 주소: " + links.map((a) => a.href));
  ok(links.every((a) => a.target === "_blank" && a.rel.includes("noopener")), "새 탭 + noopener");
  ok(links[0].textContent === "블루엔젤 리부트 [19세 완전판]", "제목 글자는 그대로");

  // Scenario 2: 네이버 검색 결과 카드도 같다
  setPlatform("naver"); await search("네이버");
  links = cardLinks();
  ok(links.length === 1 && links[0].href === "https://comic.naver.com/webtoon/list?titleId=333", "네이버 카드 링크 주소: " + links.map((a) => a.href));

  // Scenario 3: 카카오 분석 전에는 작품 페이지 버튼이 보이지 않고, 분석하면 구독 버튼 옆에 생긴다
  setPlatform("kakao");
  ok($("link-kakao-manual-page").classList.contains("hidden"), "분석 전에는 숨김");
  await search("111");
  const btn = $("link-kakao-manual-page");
  ok(!btn.classList.contains("hidden") && btn.href === "https://page.kakao.com/content/111" && btn.target === "_blank", "분석 후 작품 페이지 버튼: " + btn.href);
  ok(btn.previousElementSibling === $("btn-kakao-manual-subscribe"), "구독/구독 중 버튼 바로 옆에 있음");

  // Scenario 4: 다른 작품을 분석하면 주소가 그 작품으로 바뀐다
  kakaoCur = kakaoAnalysis(222, "흑기사"); await search("222");
  ok($("link-kakao-manual-page").href === "https://page.kakao.com/content/222", "다른 작품 분석 시 주소 갱신: " + $("link-kakao-manual-page").href);

  // Scenario 5: 네이버 분석 결과에도 작품 페이지 버튼이 생긴다
  setPlatform("naver"); await search("333");
  ok($("link-manual-page").href === "https://comic.naver.com/webtoon/list?titleId=333" && !$("link-manual-page").classList.contains("hidden"), "네이버 분석 결과 작품 페이지 버튼: " + $("link-manual-page").href);
  done();
})();
