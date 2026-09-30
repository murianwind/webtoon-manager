const { makeApp, sleep, ok, done } = require("./harness");
const naver = (id, title, extra = {}) => ({ title_id: String(id), title, thumbnail_url: `https://n/${id}.jpg`, author_summary: "작가", is_adult: false, is_new: false, is_paused: false, has_new_episode: false, status: null, ever_subscribed: false, ...extra });
const kakao = (id, title, extra = {}) => ({ title_id: id, title, thumbnail_url: `https://k/${id}`, author_summary: "작가", is_adult: false, is_new: false, is_paused: false, has_new_episode: false, status: null, ever_subscribed: false, ...extra });
(async () => {
  let naverData = [naver(1, "나"), naver(2, "라"), naver(3, "다", { status: "active", ever_subscribed: true })];
  let kakaoResp = { items: [kakao(71000001, "가"), kakao(71000002, "마")], version: "v1", refreshing: false, refreshed_at: "2026-09-30T01:00:00+00:00" };
  const routes = {
    "/api/naver-list": () => naverData, "/api/kakao-list": () => kakaoResp,
    "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" },
    "/api/webtoons": () => [], "/api/kakao-webtoons": () => [],
  };
  const { w, run, calls } = makeApp(routes);
  await sleep(50);
  run("kakaoWebtoonsEnabled = true;");
  const grid = w.document.getElementById("naver-list-grid");
  const cards = () => Array.from(grid.querySelectorAll(":scope > .webtoon-card"));
  const titles = () => cards().map((c) => c.querySelector(".webtoon-card-title").textContent);
  const count = (u) => calls.filter((c) => c.url.startsWith(u)).length;

  console.log("1) 처음 열기");
  run("window.__p = loadNaverList(false);"); await w.__p;
  ok(JSON.stringify(titles()) === JSON.stringify(["가", "나", "다", "라", "마"]), "네이버+카카오 카드가 제목순으로 그려짐: " + titles().join(","));
  ok(count("/api/naver-list") === 1 && count("/api/kakao-list") === 1, "네이버/카카오 목록을 각각 1번씩 받음");
  const first = cards();

  console.log("2) 탭을 오가도 카드가 그대로");
  for (let i = 0; i < 3; i++) { run(`switchToTab("unsubscribed");`); await sleep(20); run(`switchToTab("naver-list");`); await sleep(20); }
  ok(count("/api/naver-list") === 1 && count("/api/kakao-list") === 1, "60초 안에 탭을 오가면 서버를 다시 부르지 않음");
  ok(cards().length === 5 && cards().every((c, i) => c === first[i]), "카드 5개가 모두 같은 DOM 노드(다시 그리지 않음)");

  console.log("3) 실제로 바뀐 카드만 교체");
  naverData = [naver(1, "나"), naver(2, "라", { has_new_episode: true }), naver(3, "다", { status: "active", ever_subscribed: true })];
  kakaoResp = { ...kakaoResp, version: "v2" }; // 카카오 쪽은 version이 바뀌었지만 내용은 같음
  run("window.__p = loadNaverList(true);"); await w.__p;
  const after = cards();
  const sameCount = after.filter((c, i) => c === first[i]).length;
  ok(sameCount === 4 && after[3] !== first[3], "'라'(UP 표시 추가)만 새로 만들고 나머지 4개는 그대로: 같은 노드 " + sameCount + "개");
  ok(after[3].innerHTML.includes("UP") || after[3].querySelector(".badge") !== null, "바뀐 카드에 UP 배지가 반영됨");

  console.log("4) 검색/필터로 숨겼다 다시 보여도 같은 카드 재사용");
  const search = w.document.getElementById("naver-list-search");
  search.value = "마"; search.dispatchEvent(new w.Event("input")); await sleep(10);
  ok(titles().join(",") === "마", "검색하면 일치하는 카드만 보임");
  const shown = cards()[0];
  search.value = ""; search.dispatchEvent(new w.Event("input")); await sleep(10);
  ok(titles().length === 5 && cards()[4] === shown, "검색을 지우면 카드가 복원되고 '마'는 같은 노드");
  ok(cards().filter((c, i) => c === after[i]).length === 5, "5개 모두 이전과 같은 노드");

  console.log("5) 카카오 목록을 백그라운드로 갱신 중이면 지켜보다가 바뀌면 반영");
  kakaoResp = { items: [kakao(71000001, "가")], version: "v3", refreshing: true, refreshed_at: null };
  const before = count("/api/kakao-list");
  run("window.__p = loadNaverList(true);"); await w.__p;
  ok(titles().join(",") === "가,나,다,라", "받는 동안엔 있는 것만 보여줌(기다리지 않음): " + titles().join(","));
  ok(w.document.getElementById("naver-list-refresh-status").textContent.includes("백그라운드로 갱신 중"), "상태 문구에 '백그라운드로 갱신 중' 표시");
  kakaoResp = { items: [kakao(71000001, "가"), kakao(71000002, "마"), kakao(71000009, "새작품", { has_new_episode: true })], version: "v4", refreshing: false, refreshed_at: "2026-09-30T04:00:00+00:00" };
  await sleep(3400);
  ok(count("/api/kakao-list") > before + 1, "3초 뒤 다시 확인함");
  ok(titles().join(",") === "가,나,다,라,마,새작품", "갱신이 끝나면 새 작품이 나타남: " + titles().join(","));
  ok(!w.document.getElementById("naver-list-refresh-status").textContent.includes("갱신 중"), "끝나면 '갱신 중' 문구 사라짐");
  const stable = count("/api/kakao-list"); await sleep(3400);
  ok(count("/api/kakao-list") === stable, "갱신이 끝나면 더 이상 확인하지 않음");

  console.log("6) 스크롤: 카드가 사라졌다 나타나지 않도록 content-visibility 적용");
  const css = require("fs").readFileSync(require("path").join(__dirname, "..", "..", "app", "static", "style.css"), "utf8");
  ok(/\.webtoon-card\s*\{[^}]*content-visibility:\s*auto/.test(css), "CSS에 화면 밖 카드 렌더링 생략 규칙이 있음");
  done();
})();
