// ① PC: "마지막 확인" 문구는 맨 위 제목(웹툰 구독 관리) 오른쪽에 보인다(전체목록 화면에서만, 모바일은 접히는 필터 안에 그대로).
// ② 요일 탭은 검색/필터와 같은 고정 영역 안에 있어서 스크롤하면 함께 고정된다(PC, 모바일 모두). 필터 줄이 두 줄로 접혀도 위치 계산이 어긋나지 않는다.
// ③ 요일 탭 줄이 세로로 1px 넘쳐서(음수 마진) 생기던 작은 스크롤바/드래그 이동을 없앤다.
const fs = require("fs");
const path = require("path");
const { makeApp, sleep, idle, ok, done } = require("./harness");
const css = fs.readFileSync(path.join(__dirname, "..", "..", "app", "static", "style.css"), "utf8");
function mobileBlock(text) {
  const start = text.indexOf("@media (max-width: 640px) {");
  let depth = 0, i = text.indexOf("{", start);
  const open = i;
  for (; i < text.length; i++) { if (text[i] === "{") depth++; else if (text[i] === "}" && --depth === 0) break; }
  return { inside: text.slice(open + 1, i), outside: text.slice(0, start) + text.slice(i + 1) };
}
const ruleOf = (text, selector) => { const m = text.match(new RegExp("(?:^|\\})\\s*" + selector.replace(/[.*+?^${}()|[\]\\>#]/g, "\\$&") + "\\s*\\{([^}]*)\\}", "m")); return m ? m[1].replace(/\s+/g, " ").trim() : null; };

(async () => {
  let listError = null;
  const { w, run } = makeApp({ "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" }, "/api/naver-list": () => { if (listError) throw new Error(listError); return []; }, "/api/kakao-list": { items: [], version: "v", refreshing: false } });
  await idle(); run("kakaoWebtoonsEnabled = true;");
  const $ = (id) => w.document.getElementById(id);
  const call = (code) => { run(`window.__r = ${code};`); return w.__r; };

  // Scenario 1: 머리글 — 제목 오른쪽에 "마지막 확인" 칸. 같은 문구를 모바일용(필터 안) 칸에도 넣는다(화면 폭에 따라 CSS가 하나만 보이게 한다)
  const header = w.document.querySelector("header");
  ok(header && header.querySelector("h1") && header.contains($("header-list-status")) && !!(header.querySelector("h1").compareDocumentPosition($("header-list-status")) & w.Node.DOCUMENT_POSITION_FOLLOWING), "머리글: 제목 다음에 상태 칸");
  run("naverListCache = [{}, {}]; naverListLoadedAt = Date.parse('2026-11-03T09:48:36Z'); setNaverListStatus({ refreshed_at: '2026-11-03T08:37:28Z' }, '');");
  const text = $("header-list-status").textContent;
  ok(/^마지막 확인: .+ \(2개, 카카오페이지 목록: .+ 기준\)$/.test(text) && $("naver-list-refresh-status").textContent === text && $("header-list-status").title === text, "두 칸이 같은 문구(길면 말줄임이라 title에 전체): " + text);
  run("showNaverListStatus('확인 중...');"); ok($("header-list-status").textContent === "확인 중..." && $("naver-list-refresh-status").textContent === "확인 중...", "확인 중... 도 두 칸에");

  // Scenario 2: 새로고침이 실패해도 두 칸에 같은 안내
  listError = "서버 오류"; run("window.__p = loadNaverList(true);"); await w.__p; await idle();
  ok($("header-list-status").textContent === "새로고침 실패: 서버 오류" && $("naver-list-refresh-status").textContent === "새로고침 실패: 서버 오류", "실패 문구도 두 칸에: " + $("header-list-status").textContent);
  listError = null;

  // Scenario 3: 전체목록 화면에서만 보인다 — 다른 메뉴로 가면 숨기고 돌아오면 다시
  run("showNaverListStatus('마지막 확인: 테스트');"); run("switchToTab('excluded');");
  ok($("header-list-status").classList.contains("hidden"), "다른 화면에서는 숨김");
  run("switchToTab('naver-list');"); ok(!$("header-list-status").classList.contains("hidden"), "전체목록으로 돌아오면 보임");

  // Scenario 4: 마크업 — 고정 툴바(검색 → 접기 버튼 → 필터 → 요일 탭 → 선택 막대) 안에 요일 탭이 들어 있다(툴바가 한 덩어리로 고정되므로 필터가 두 줄이 돼도 위치가 어긋나지 않는다). 마지막 확인 줄은 모바일용으로 필터 안
  const kids = (page) => [...w.document.querySelector(`#page-${page} .list-toolbar`).children];
  const kinds = (page) => kids(page).map((e) => e.classList.contains("clearable-wrap") ? "search" : e.classList.contains("filter-toggle") ? "toggle" : e.classList.contains("list-filters") ? "filters" : e.classList.contains("weekday-tabs") ? "tabs" : e.id === "naver-list-bulk-bar" ? "bulk" : "?");
  ok(JSON.stringify(kinds("naver-list")) === JSON.stringify(["search", "toggle", "filters", "tabs", "bulk"]), "전체목록 툴바 구성: " + kinds("naver-list"));
  ok(JSON.stringify(kinds("excluded")) === JSON.stringify(["search", "toggle", "filters", "tabs"]) && kinds("unsubscribed").join() === "search,toggle,filters", "제외됨/구독해제 툴바 구성: " + kinds("excluded") + " | " + kinds("unsubscribed"));
  ok($("page-naver-list").querySelector(".list-filters").contains($("naver-list-refresh-status")), "마지막 확인 줄(모바일용)은 접히는 필터 안");
  ok(!$("excluded-finished-note") && !$("excluded-etc-note") && !$("naver-list-etc-note"), "안내 줄(완결 N개/기타 원인)은 화면에 두지 않는다 — 고정 영역을 키우지 않고 실행 이력으로 옮겼다");

  // Scenario 5: CSS(PC) — 머리글은 제목 왼쪽/상태 오른쪽(긴 문구는 말줄임), 모바일용 칸은 숨김, 요일 탭은 툴바 한 줄 전체를 쓰고 선택 막대도 툴바 안
  const { inside: m, outside: d } = mobileBlock(css);
  const hdr = ruleOf(d, "header");
  ok(hdr && /display: flex/.test(hdr) && /justify-content: space-between/.test(hdr), "머리글: 가로 배치 제목 | 상태: " + hdr);
  const hs = ruleOf(d, ".header-status");
  ok(hs && /text-overflow: ellipsis/.test(hs) && /white-space: nowrap/.test(hs) && /overflow: hidden/.test(hs) && /min-width: 0/.test(hs), "상태 칸: 긴 문구는 말줄임(제목을 밀어내지 않게): " + hs);
  ok(/display: none/.test(ruleOf(d, ".list-head-status") || ""), "PC: 필터 안의 모바일용 줄은 숨김");
  ok(/flex: 0 0 100%/.test(ruleOf(d, ".list-toolbar > .weekday-tabs") || "") && /flex: 0 0 100%/.test(ruleOf(d, ".list-toolbar > #naver-list-bulk-bar") || ""), "PC: 요일 탭/선택 막대는 툴바에서 한 줄 전체");
  ok(!/#naver-list-bulk-bar\s*\{[^}]*top: 93px/.test(d), "예전의 고정 위치(top: 93px) 계산은 없어졌다(툴바 안으로 들어갔으니 필요 없음)");

  // Scenario 6: CSS(모바일) — 머리글 상태 칸은 숨기고(필터 안에서 보여 줌), 선택 막대는 요일 탭 아래에 고정
  ok(/display: none/.test(ruleOf(m, ".header-status") || ""), "모바일: 머리글 상태 칸 숨김");
  const bulk = ruleOf(m, ".list-toolbar > #naver-list-bulk-bar");
  ok(bulk && /position: sticky/.test(bulk) && /top: calc\(var\(--m-nav-h\) \+ var\(--m-search-h\) \+ var\(--m-tabs-h\)\)/.test(bulk), "모바일: 선택 막대는 요일 탭 아래에 고정: " + bulk);

  // Scenario 7: 요일 탭 줄 — 세로로 넘치면 작은 스크롤바가 생기고 끌면 움직인다. 세로 넘침을 막는다: overflow-y 숨김 + 음수 마진 없음(아래 선은 안쪽 그림자로)
  const bar = ruleOf(d, ".weekday-tabs"), tab = ruleOf(d, ".weekday-tab");
  ok(bar && /overflow-x: auto/.test(bar) && /overflow-y: hidden/.test(bar), "요일 탭 줄: 가로만 스크롤, 세로 스크롤 없음: " + bar);
  ok(tab && !/margin-bottom:\s*-/.test(tab) && !/border-bottom: 1px solid var\(--border\)/.test(bar) && /box-shadow: inset 0 -1px 0 var\(--border\)/.test(bar), "음수 마진 대신 안쪽 그림자로 아래 선을 그린다");
  ok(/overscroll-behavior-x: contain/.test(bar), "탭 줄을 좌우로 밀 때 페이지가 같이 움직이지 않게");
  // Scenario 8: 고정 툴바 위치 — 좁은 PC 창에서는 상단 메뉴가 두 줄로 접혀 높이가 달라진다. 툴바를 메뉴 바로 아래에 고정하려면 메뉴의 실제 높이를 재서 맞춰야 한다(41px로 고정해 두면 검색창/요일 탭이 메뉴 뒤에 가려진다)
  ok(/top: var\(--nav-h, 41px\)/.test(ruleOf(d, ".toolbar-sticky") || ""), "툴바 고정 위치는 메뉴 높이 변수: " + ruleOf(d, ".toolbar-sticky"));
  const nav = w.document.querySelector(".main-tabs"); let navHeight = 41;
  Object.defineProperty(nav, "offsetHeight", { get: () => navHeight, configurable: true });
  run("syncStickyOffsets();"); ok(w.document.documentElement.style.getPropertyValue("--nav-h") === "41px", "한 줄이면 41px: " + w.document.documentElement.style.getPropertyValue("--nav-h"));
  navHeight = 85; ok(w.document.documentElement.style.getPropertyValue("--nav-h") === "41px", "바뀌기 전에는 그대로"); w.dispatchEvent(new w.Event("resize"));
  ok(w.document.documentElement.style.getPropertyValue("--nav-h") === "85px", "창 크기가 바뀌어 메뉴가 두 줄이 되면 85px: " + w.document.documentElement.style.getPropertyValue("--nav-h"));
  navHeight = 41; w.dispatchEvent(new w.Event("resize")); ok(w.document.documentElement.style.getPropertyValue("--nav-h") === "41px", "다시 한 줄이 되면 41px");
  // Scenario 9: 요일 탭 색 — 선택한 탭은 파랑(--select), 오늘이면서 선택 안 한 탭은 빨강(--danger) 밑줄. 선택이 오늘이면 파랑(선택이 우선)
  ok(/--select:\s*#[0-9a-fA-F]{3,8}/.test(css), "선택 색(--select) 변수");
  const activeRule = ruleOf(d, ".weekday-tab.active"), todayRule = ruleOf(d, ".weekday-tab.today:not(.active)");
  ok(activeRule && /color: var\(--select\)/.test(activeRule) && /border-bottom-color: var\(--select\)/.test(activeRule), "선택한 탭: 파랑 글자와 밑줄: " + activeRule);
  ok(todayRule && /color: var\(--danger\)/.test(todayRule) && /border-bottom-color: var\(--danger\)/.test(todayRule), "오늘이지만 선택 안 한 탭: 빨강 글자와 밑줄: " + todayRule);
  done();
})();
