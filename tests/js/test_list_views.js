// 웹툰 전체목록/제외됨 화면 — 요일별 탭(오늘 요일이 기본), 플랫폼 필터(네이버/카카오), 태그 필터는 네이버만 볼 때만, 한 번에 많이 그려서 브라우저가 멎는 문제를 막는 점진 렌더링과 검색 지연.
const { makeApp, sleep, idle, ok, done } = require("./harness");
const { listItem } = require("./factories");
(async () => {
  const mk = listItem;
  const base = [
    mk("naver", 1, "네이버 월화", ["mon", "tue"]), mk("naver", 2, "네이버 화", ["tue"]), mk("naver", 3, "네이버 일", ["sun"]), mk("naver", 4, "네이버 기타", []),
    mk("kakao", 5, "카카오 화", ["tue"]), mk("kakao", 6, "카카오 수", ["wed"]), mk("kakao", 7, "카카오 기타", []),
  ];
  const { w, run, wait } = makeApp({ "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" }, "/api/naver-list": [], "/api/kakao-list": { items: [], version: "v", refreshing: false } }, { timeScale: 5, now: "2026-11-03T03:00:00Z" }); // 검색 지연(250ms)을 5배 빠르게 — 고정 sleep 없이 확인
  await idle(); run("kakaoWebtoonsEnabled = true;");        // 2026-11-03 = 화요일(한국시간 12시)
  const $ = (id) => w.document.getElementById(id);
  const feed = (rows) => { w.__rows = rows; run("naverListNaverItems = __rows.filter((r) => r.platform === 'naver'); naverListKakaoItems = __rows.filter((r) => r.platform === 'kakao'); combineNaverListCache(); renderNaverList();"); };
  const titles = (gridId = "naver-list-grid") => [...w.document.querySelectorAll(`#${gridId} .webtoon-card-title`)].map((e) => e.textContent.trim());
  const tabs = (id = "naver-list-weekday-tabs") => [...$(id).querySelectorAll(".weekday-tab")];
  const tabLabel = (t) => t.firstChild.textContent.trim();
  const activeTab = (id) => { const a = tabs(id).find((t) => t.classList.contains("active")); return a ? tabLabel(a) : null; };
  const clickTab = (label, id) => tabs(id).find((t) => tabLabel(t) === label).click();
  const set = (id, value) => { $(id).value = value; $(id).dispatchEvent(new w.Event("change")); };
  const call = (code) => { run(`window.__r = ${code};`); return w.__r; };

  // Scenario 1: 요일 계산 — 한국시간 기준(UTC 일요일 15:30은 한국시간 월요일 00:30)
  ok(call("weekdayKeyOf(new Date('2026-11-01T15:30:00Z'))") === "mon" && call("weekdayKeyOf(new Date('2026-11-03T14:59:00Z'))") === "tue" && call("weekdayKeyOf(new Date('2026-11-08T00:00:00Z'))") === "sun", "한국시간 기준 요일 계산");

  // Scenario 2: 전체목록 — 요일 탭(월~일, 기타, 전체)이 만들어지고 오늘(화요일) 탭이 기본으로 선택된다. 그 요일 작품만 보인다(여러 요일 연재는 각 요일에)
  feed(base);
  ok(JSON.stringify(tabs().map(tabLabel)) === JSON.stringify(["월", "화", "수", "목", "금", "토", "일", "기타", "전체"]) && $("naver-list-weekday-tabs").getAttribute("role") === "tablist", "탭 구성: " + tabs().map(tabLabel));
  ok(activeTab("naver-list-weekday-tabs") === "화" && tabs().find((t) => tabLabel(t) === "화").getAttribute("aria-selected") === "true", "오늘(화요일) 탭이 기본 선택");
  ok(JSON.stringify(titles().sort()) === JSON.stringify(["네이버 월화", "네이버 화", "카카오 화"]), "화요일 작품만: " + titles());
  ok(JSON.stringify(tabs().map((t) => t.querySelector(".weekday-tab-count").textContent)) === JSON.stringify(["1", "3", "1", "0", "0", "0", "1", "2", "7"]), "탭마다 개수: " + tabs().map((t) => t.querySelector(".weekday-tab-count").textContent));
  clickTab("일"); ok(JSON.stringify(titles()) === '["네이버 일"]' && activeTab("naver-list-weekday-tabs") === "일", "일요일 탭");
  clickTab("기타"); ok(JSON.stringify(titles().sort()) === JSON.stringify(["네이버 기타", "카카오 기타"]), "기타 = 요일 정보 없는 작품(완결/장기 휴재 등): " + titles());
  clickTab("전체"); ok(titles().length === 7, "전체 탭은 전부");
  clickTab("월"); ok(JSON.stringify(titles()) === '["네이버 월화"]', "월요일 탭");

  // Scenario 2b: 오늘 표시 — 선택한 탭은 파란색 밑줄, 오늘(화요일)이 아닌 다른 탭을 선택하면 오늘 탭은 빨간색 밑줄로 따로 표시된다(오늘을 선택했으면 오늘=선택). 접근성: 오늘 탭에 aria-current
  clickTab("화");
  const tabByLabel = (label, id) => tabs(id).find((t) => tabLabel(t) === label);
  ok(tabByLabel("화").classList.contains("active") && tabByLabel("화").classList.contains("today") && tabs().filter((t) => t.classList.contains("today")).length === 1, "오늘을 선택하면 선택과 오늘이 같은 탭");
  clickTab("수");
  ok(tabByLabel("수").classList.contains("active") && !tabByLabel("수").classList.contains("today") && tabByLabel("화").classList.contains("today") && !tabByLabel("화").classList.contains("active"), "수요일을 선택하면 수=선택(파랑), 화=오늘(빨강)");
  ok(tabByLabel("화").getAttribute("aria-current") === "date" && !tabByLabel("수").hasAttribute("aria-current") && tabByLabel("화").title === "오늘", "오늘 탭 표시(aria-current, 제목 '오늘')");
  clickTab("기타"); ok(tabByLabel("화").classList.contains("today") && tabByLabel("기타").classList.contains("active"), "기타/전체를 골라도 오늘 탭은 따로 표시");
  clickTab("월");

  // Scenario 2c: 기타 탭은 해당 작품이 하나도 없으면 숨긴다. 기타를 보던 중 비게 되면 오늘 탭으로 돌아온다
  const noEtc = base.filter((r) => r.weekdays.length > 0);
  feed(noEtc); ok(JSON.stringify(tabs().map(tabLabel)) === JSON.stringify(["월", "화", "수", "목", "금", "토", "일", "전체"]), "기타가 0이면 탭 없음: " + tabs().map(tabLabel));
  feed(base); ok(tabs().map(tabLabel).includes("기타"), "기타 작품이 생기면 탭이 나타남");
  clickTab("기타"); ok(activeTab("naver-list-weekday-tabs") === "기타" && titles().length === 2, "기타를 보는 중");
  feed(noEtc); ok(activeTab("naver-list-weekday-tabs") === "화" && !tabs().map(tabLabel).includes("기타"), "보던 기타가 비면 오늘 탭으로: " + activeTab("naver-list-weekday-tabs"));
  feed(base); clickTab("월");

  // Scenario 3: 검색하면 요일과 상관없이 전체에서 찾는다(탭은 "전체"로 보이고, 검색을 지우면 보던 탭으로 돌아간다)
  $("naver-list-search").value = "카카오 수"; $("naver-list-search").dispatchEvent(new w.Event("input")); await wait(350);
  ok(JSON.stringify(titles()) === '["카카오 수"]' && activeTab("naver-list-weekday-tabs") === "전체", "검색은 전체에서: " + titles());
  $("naver-list-search").value = ""; $("naver-list-search").dispatchEvent(new w.Event("input")); await wait(350);
  ok(activeTab("naver-list-weekday-tabs") === "월" && titles().length === 1, "검색을 지우면 보던 탭(월)으로");

  // Scenario 4: 플랫폼 필터(전체/네이버/카카오) — 카카오 관리를 켠 경우에만 보이고, 고르면 그 플랫폼만, 탭 개수도 따라 바뀐다. 선택은 저장된다
  const platformSel = $("naver-list-platform-filter");
  ok(!platformSel.classList.contains("hidden") && JSON.stringify([...platformSel.options].map((o) => o.textContent.trim())) === JSON.stringify(["플랫폼 전체", "네이버", "카카오"]), "플랫폼 필터 옵션(다른 '전체' 선택과 구별되는 이름)");
  clickTab("전체"); set("naver-list-platform-filter", "kakao");
  ok(JSON.stringify(titles().sort()) === JSON.stringify(["카카오 기타", "카카오 수", "카카오 화"]) && tabs().find((t) => tabLabel(t) === "전체").querySelector(".weekday-tab-count").textContent === "3", "카카오만: " + titles());
  set("naver-list-platform-filter", "naver"); ok(titles().length === 4 && titles().every((t) => t.startsWith("네이버")), "네이버만");
  ok(JSON.parse(w.localStorage.getItem("naverListPrefs")).platform === "naver", "플랫폼 선택이 저장됨");
  set("naver-list-platform-filter", ""); ok(titles().length === 7, "전체로 되돌림");
  run("kakaoWebtoonsEnabled = false; renderNaverList();"); ok(platformSel.classList.contains("hidden"), "카카오 관리를 끄면 플랫폼 필터를 숨긴다");
  run("kakaoWebtoonsEnabled = true; renderNaverList();"); ok(!platformSel.classList.contains("hidden"), "켜면 다시 보임");

  // Scenario 5: 제외됨 — 같은 요일 탭/플랫폼 필터. 태그 필터는 네이버만 볼 때만 나온다(다른 플랫폼으로 바꾸면 숨기고 값도 비운다)
  const ex = (platform, id, title, weekdays, tags) => mk(platform, id, title, weekdays, { status: "excluded", tags });
  w.__ex = [ex("naver", 11, "제외 네이버 화(태그A)", ["tue"], ["태그A"]), ex("naver", 12, "제외 네이버 화(태그B)", ["tue"], ["태그B"]), ex("naver", 13, "제외 네이버 수", ["wed"], ["태그A"]), ex("kakao", 14, "제외 카카오 화", ["tue"], []), ex("kakao", 15, "제외 카카오 기타", [], [])];
  run("subscriptionCache.excluded = __ex; populateFilterOptions('excluded'); renderSubscriptionTab('excluded');");
  const exTitles = () => titles("excluded-list");
  ok(activeTab("excluded-weekday-tabs") === "화" && JSON.stringify(exTitles().sort()) === JSON.stringify(["제외 네이버 화(태그A)", "제외 네이버 화(태그B)", "제외 카카오 화"]), "제외됨도 오늘 탭: " + exTitles());
  ok(activeTab("naver-list-weekday-tabs") !== null && JSON.stringify(tabs("excluded-weekday-tabs").map(tabLabel)) === JSON.stringify(["월", "화", "수", "목", "금", "토", "일", "기타", "전체"]), "탭은 화면마다 따로");
  const tagSel = $("excluded-tag-filter"), exPlatform = $("excluded-platform-filter");
  w.__exNoEtc = w.__ex.filter((r) => r.weekdays.length > 0);
  run("subscriptionCache.excluded = __exNoEtc; renderSubscriptionTab('excluded');"); ok(!tabs("excluded-weekday-tabs").map(tabLabel).includes("기타"), "제외됨도 기타가 0이면 탭 없음");
  ok(tabs("excluded-weekday-tabs").some((t) => t.classList.contains("today") && tabLabel(t) === "화"), "제외됨도 오늘 탭 표시");
  run("subscriptionCache.excluded = __ex; renderSubscriptionTab('excluded');");
  ok(tagSel.classList.contains("hidden"), "플랫폼이 전체면 태그 필터는 숨김");
  const setEx = (id, v) => { $(id).value = v; $(id).dispatchEvent(new w.Event("change")); };
  setEx("excluded-platform-filter", "naver"); ok(!tagSel.classList.contains("hidden") && [...tagSel.options].map((o) => o.value).includes("태그A"), "네이버만 볼 때 태그 필터가 나온다");
  setEx("excluded-tag-filter", "태그A"); clickTab("전체", "excluded-weekday-tabs"); ok(JSON.stringify(exTitles().sort()) === JSON.stringify(["제외 네이버 수", "제외 네이버 화(태그A)"]), "태그 필터 동작: " + exTitles());
  setEx("excluded-platform-filter", "kakao"); ok(tagSel.classList.contains("hidden") && tagSel.value === "" && JSON.stringify(exTitles().sort()) === JSON.stringify(["제외 카카오 기타", "제외 카카오 화"]), "카카오로 바꾸면 태그 필터가 숨겨지고 값도 비워진다(숨은 필터가 목록을 거르지 않게): " + exTitles());
  setEx("excluded-platform-filter", ""); ok(tagSel.classList.contains("hidden") && exTitles().length === 5, "전체로 돌아오면 숨김 유지");
  ok(!w.document.getElementById("unsubscribed-tag-filter").classList.contains("hidden"), "구독해제 화면의 태그 필터는 그대로");

  // Scenario 6: 점진 렌더링 — 한 번에 많이 그리면 브라우저가 멎는다. 처음엔 48개만 그리고 "더 보기"(남은 개수)나 스크롤로 이어서 그린다. 필터/탭을 바꾸면 다시 처음 48개
  const many = Array.from({ length: 150 }, (_, i) => mk("naver", 1000 + i, `많은작품 ${String(i).padStart(3, "0")}`, ["tue"]));
  let built = 0; run("window.__build = buildWebtoonCard; buildWebtoonCard = function (...a) { window.__built = (window.__built || 0) + 1; return window.__build(...a); };");
  w.__built = 0; feed(many); clickTab("전체"); run("resetListWindow('naver-list'); renderNaverList();");        // 카드가 만들어지는 횟수를 센다(작품이 바뀐 첫 그리기에서 만든다)
  const more = () => $("naver-list-grid").nextElementSibling;
  ok(titles().length === 48 && more().classList.contains("list-more") && !more().classList.contains("hidden") && /더 보기/.test(more().textContent) && /남은 102개/.test(more().textContent), "처음 48개 + 더 보기(남은 102개): " + titles().length + " " + more().textContent.trim());
  ok(w.__built === 48, "카드는 보이는 만큼만 만든다: " + w.__built);
  more().querySelector("button").click(); ok(titles().length === 96 && w.__built === 96 && /남은 54개/.test(more().textContent), "더 보기 → 96개(새로 만든 것만 48개 더): " + titles().length + "/" + w.__built);
  more().querySelector("button").click(); more().querySelector("button").click(); ok(titles().length === 150 && more().classList.contains("hidden"), "끝까지 그리면 더 보기가 사라진다");
  clickTab("월"); clickTab("전체"); ok(titles().length === 48, "탭을 바꾸면 처음 48개부터");
  // 스크롤로도 이어진다: 더 보기 줄이 화면 가까이 오면(IntersectionObserver) 다음 48개
  const observers = []; w.IntersectionObserver = class { constructor(cb) { this.cb = cb; observers.push(this); } observe(el) { this.el = el; } unobserve() {} disconnect() {} };
  w.document.getElementById("naver-list-grid").nextElementSibling.remove(); w.document.getElementById("naver-list-grid")._moreBox = null;                  // 관찰자를 새로 붙이도록 더 보기 줄을 다시 만든다
  run("resetListWindow('naver-list'); renderNaverList();");
  ok(observers.length === 1 && titles().length === 48, "관찰자가 더 보기 줄을 지켜본다");
  observers[0].cb([{ isIntersecting: false, target: observers[0].el }]); ok(titles().length === 48, "화면 밖이면 그대로");
  observers[0].cb([{ isIntersecting: true, target: observers[0].el }]); ok(titles().length === 96, "화면에 가까워지면 다음 48개: " + titles().length);
  delete w.IntersectionObserver;

  // Scenario 7: 구독해제/제외됨 목록도 같은 방식으로 나눠 그린다
  w.__ex2 = Array.from({ length: 120 }, (_, i) => mk("naver", 2000 + i, `제외많음 ${i}`, ["tue"], { status: "excluded" }));
  run("subscriptionCache.excluded = __ex2; renderSubscriptionTab('excluded');"); clickTab("전체", "excluded-weekday-tabs");
  ok(exTitles().length === 48 && /남은 72개/.test($("excluded-list").nextElementSibling.textContent), "제외됨도 48개씩: " + exTitles().length);
  w.__unsub = Array.from({ length: 100 }, (_, i) => mk("naver", 3000 + i, `구독해제 ${i}`, [], { status: "unsubscribed", ever_subscribed: true }));
  run("subscriptionCache.unsubscribed = __unsub; renderSubscriptionTab('unsubscribed');");
  ok(titles("unsubscribed-list").length === 48 && /남은 52개/.test($("unsubscribed-list").nextElementSibling.textContent), "구독해제도 48개씩");

  // Scenario 8: 검색 입력은 잠깐 기다렸다가(250ms) 한 번만 다시 그린다 — 글자마다 수백 개 카드를 다시 걸러서 그리면 멎는다
  feed(base); clickTab("전체");
  let renders = 0; run("window.__renderNaverList = renderNaverList; renderNaverList = function (...a) { window.__renders = (window.__renders || 0) + 1; return window.__renderNaverList(...a); };"); w.__renders = 0;
  for (const text of ["네", "네이", "네이버"]) { $("naver-list-search").value = text; $("naver-list-search").dispatchEvent(new w.Event("input")); await idle(); }
  ok(w.__renders === 0, "입력하는 동안에는 다시 그리지 않는다: " + w.__renders);
  await wait(330); ok(w.__renders === 1 && titles().every((t) => t.startsWith("네이버")), "멈추면 한 번만 다시 그린다: " + w.__renders);
  done();
})();
