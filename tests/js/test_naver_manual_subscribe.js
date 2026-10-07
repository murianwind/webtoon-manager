// 수동 다운로드(네이버): 검색 결과 카드와 분석 결과 제목 옆에 카카오처럼 구독/구독 중 버튼
const { makeApp, sleep, ok, done } = require("./harness");
(async () => {
  const search = [
    { title_id: "111", title: "구독중 작품", thumbnail_url: "t1", subscription: "active" },
    { title_id: "222", title: "제외한 작품", thumbnail_url: "t2", subscription: "excluded" },
    { title_id: "333", title: "새 작품", thumbnail_url: "t3", subscription: null },
  ];
  let analysis = { title_id: "333", title: "새 작품", thumbnail_url: "t3", subscription: null, episodes: [{ episode_no: 1, subtitle: "1화", owned: false, is_locked: false }] };
  const posts = [];
  const routes = {
    "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" },
    "/api/manual-download/search": search, "/api/manual-download/analyze": () => analysis,
    "/api/naver-list/333/subscribe": (opts) => { posts.push(JSON.parse(opts.body)); return { status: "active" }; },
  };
  const { w, run } = makeApp(routes); await sleep(60); run("kakaoWebtoonsEnabled = true;");
  const $ = (id) => w.document.getElementById(id);
  run(`switchToTab("manual-download");`);
  $("manual-platform").value = "naver"; $("manual-platform").dispatchEvent(new w.Event("change"));
  const go = async (q) => { $("manual-query").value = q; $("btn-manual-analyze").click(); await sleep(40); };
  const subBtn = (card) => [...card.querySelectorAll(".webtoon-card-actions button")].find((b) => b.textContent.startsWith("구독"));

  // Scenario 1: 검색 결과 카드 — 구독 중이면 "구독 중"(비활성), 아니면 "구독"
  await go("작품");
  const cards = [...w.document.querySelectorAll("#manual-search-results .webtoon-card")];
  ok(cards.length === 3, "카드 3개: " + cards.length);
  ok(subBtn(cards[0]).textContent === "구독 중" && subBtn(cards[0]).disabled, "구독 중인 작품은 '구독 중'(누를 수 없음)");
  ok(subBtn(cards[1]).textContent === "구독" && !subBtn(cards[1]).disabled, "제외한 작품은 '구독'(다시 구독 가능)");
  ok(subBtn(cards[2]).textContent === "구독" && !subBtn(cards[2]).disabled, "미등록 작품은 '구독'");
  ok(cards[2].querySelector(".webtoon-card-actions").children[0].textContent === "이 작품 분석", "'이 작품 분석' 버튼은 그대로 첫째");

  // Scenario 2: 카드의 "구독"을 누르면 구독 API가 호출되고 "구독 중"으로 바뀐다
  subBtn(cards[2]).click(); await sleep(40);
  ok(posts.length === 1 && posts[0].title === "새 작품" && posts[0].thumbnail_url === "t3", "구독 요청 본문(제목/썸네일): " + JSON.stringify(posts));
  ok(subBtn(cards[2]).textContent === "구독 중" && subBtn(cards[2]).disabled, "누른 뒤 '구독 중'(비활성)");

  // Scenario 3: 분석 결과 — 제목 옆 구독 버튼이 상태를 따라가고, 작품 페이지 버튼 바로 앞에 있다
  const sub = $("btn-manual-subscribe");
  ok(sub.classList.contains("hidden"), "분석 전에는 숨김");
  await go("333");
  ok(!sub.classList.contains("hidden") && sub.textContent === "구독" && !sub.disabled, "미등록 작품 분석 시 '구독'");
  ok(sub.nextElementSibling === $("link-manual-page"), "구독 버튼 바로 옆에 작품 페이지 버튼");
  analysis = { ...analysis, title_id: "111", title: "구독중 작품", subscription: "active" }; await go("111");
  ok(sub.textContent === "구독 중" && sub.disabled, "구독 중인 작품 분석 시 '구독 중'(비활성)");

  // Scenario 4: 분석 화면에서 "구독"을 누르면 구독되고 "구독 중"이 된다
  analysis = { ...analysis, title_id: "333", title: "새 작품", subscription: null }; await go("333");
  posts.length = 0; sub.click(); await sleep(40);
  ok(posts.length === 1 && sub.textContent === "구독 중" && sub.disabled, "분석 화면 구독 후 '구독 중': " + JSON.stringify(posts));
  done();
})();
