// 모바일(화면 폭 640px 이하) 전용 목록 화면 레이아웃 — 위에서부터: ① 한 줄 메뉴바(좌우로 스와이프) ② 검색창 ③ 접었다 펼 수 있는 필터(+마지막 확인 시각) ④ 요일 탭(고정).
// 데스크톱은 그대로(필터 줄을 그대로 펼쳐 둔다). 화면 폭에 따른 모양은 CSS가 정하고, 여기서는 마크업 순서, 접기/펴기 동작, CSS 규칙을 확인한다(실제 폭에서의 모양은 브라우저로 따로 확인).
const fs = require("fs");
const path = require("path");
const { makeApp, sleep, idle, ok, done } = require("./harness");
const css = fs.readFileSync(path.join(__dirname, "..", "..", "app", "static", "style.css"), "utf8");

// @media (max-width: 640px) { ... } 안쪽 텍스트 / 그 밖의 텍스트
function mobileBlock(text) {
  const start = text.indexOf("@media (max-width: 640px) {");
  if (start < 0) return null;
  let depth = 0, i = text.indexOf("{", start);
  const open = i;
  for (; i < text.length; i++) { if (text[i] === "{") depth++; else if (text[i] === "}" && --depth === 0) break; }
  return { inside: text.slice(open + 1, i), outside: text.slice(0, start) + text.slice(i + 1) };
}
const ruleOf = (text, selector) => { const m = text.match(new RegExp("(?:^|\\})\\s*" + selector.replace(/[.*+?^${}()|[\]\\>]/g, "\\$&") + "\\s*\\{([^}]*)\\}", "m")); return m ? m[1].replace(/\s+/g, " ").trim() : null; };

(async () => {
  const { w, run } = makeApp({ "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" }, "/api/naver-list": [], "/api/kakao-list": { items: [], version: "v", refreshing: false } });
  await idle(); run("kakaoWebtoonsEnabled = true;");
  const $ = (id) => w.document.getElementById(id);
  const pageOf = (name) => $(`page-${name}`);
  const toggle = (name) => pageOf(name).querySelector(".filter-toggle");

  // Scenario 1: 마크업 순서 — 검색창 → 필터 접기 버튼 → 필터(선택들) → (마지막 확인 줄) → 요일 탭. 검색창은 필터 묶음 밖이다(검색만 따로 고정할 수 있게)
  for (const [name, search, filters, tabs] of [["naver-list", "naver-list-search", ["naver-list-platform-filter", "naver-list-filter-status", "naver-list-badge-filter", "naver-list-sort", "btn-refresh-naver-list"], "naver-list-weekday-tabs"],
                                                ["excluded", "excluded-search", ["excluded-platform-filter", "excluded-tag-filter", "excluded-badge-filter"], "excluded-weekday-tabs"],
                                                ["unsubscribed", "unsubscribed-search", ["unsubscribed-tag-filter", "unsubscribed-badge-filter"], null]]) {
    const bar = pageOf(name).querySelector(".list-toolbar"), box = bar && bar.querySelector(".list-filters");
    ok(!!bar && !!box && bar.classList.contains("toolbar"), `${name}: 툴바(.list-toolbar)와 필터 묶음(.list-filters)`);
    ok(!box.contains($(search)) && bar.contains($(search)) && filters.every((id) => box.contains($(id))), `${name}: 검색창은 필터 묶음 밖, 선택들은 묶음 안`);
    const kids = [...bar.children];
    ok(kids.findIndex((e) => e.contains($(search))) < kids.findIndex((e) => e.classList.contains("filter-toggle")) && kids.findIndex((e) => e.classList.contains("filter-toggle")) < kids.findIndex((e) => e === box), `${name}: 검색 → 접기 버튼 → 필터 순서`);
    if (tabs) ok(!!($(tabs).compareDocumentPosition(box) & w.Node.DOCUMENT_POSITION_PRECEDING), `${name}: 요일 탭은 필터 아래`);
  }
  ok(pageOf("naver-list").querySelector(".list-filters").contains($("naver-list-refresh-status")) && !!(pageOf("naver-list").querySelector(".list-filters").compareDocumentPosition($("naver-list-weekday-tabs")) & w.Node.DOCUMENT_POSITION_FOLLOWING), "전체목록: 마지막 확인 줄은 접히는 필터 안(PC에서는 머리글에 따로 보임), 요일 탭은 필터 아래");

  // Scenario 2: 접기/펴기 — 처음엔 접혀 있고(aria-expanded=false), 누르면 펴지고(페이지에 filters-open) 다시 누르면 접힌다. 화면마다 따로
  ok(!pageOf("naver-list").classList.contains("filters-open") && toggle("naver-list").getAttribute("aria-expanded") === "false" && /^필터/.test(toggle("naver-list").textContent.trim()), "처음엔 접힘");
  toggle("naver-list").click();
  ok(pageOf("naver-list").classList.contains("filters-open") && toggle("naver-list").getAttribute("aria-expanded") === "true" && toggle("naver-list").getAttribute("aria-controls") === "naver-list-filters" && $("naver-list-filters") === pageOf("naver-list").querySelector(".list-filters"), "누르면 펴짐");
  ok(!pageOf("excluded").classList.contains("filters-open"), "다른 화면은 영향 없음");
  toggle("naver-list").click(); ok(!pageOf("naver-list").classList.contains("filters-open") && toggle("naver-list").getAttribute("aria-expanded") === "false", "다시 누르면 접힘");

  // Scenario 3: 접힌 상태에서도 어떤 필터가 걸려 있는지 보이게 — 버튼에 "N개 적용"(정렬은 필터가 아니라서 세지 않고, 숨겨진 선택은 세지 않는다)
  const setSel = (id, v) => { $(id).value = v; $(id).dispatchEvent(new w.Event("change")); };
  const label = (name) => toggle(name).textContent.replace(/\s+/g, " ").trim();
  ok(label("naver-list") === "필터▾", "적용된 필터가 없으면 '필터'만(오른쪽 끝에 화살표): " + label("naver-list"));
  setSel("naver-list-filter-status", "active"); ok(/1개 적용/.test(label("naver-list")), "필터 1개 적용: " + label("naver-list"));
  setSel("naver-list-platform-filter", "kakao"); setSel("naver-list-badge-filter", "new"); ok(/3개 적용/.test(label("naver-list")), "3개 적용: " + label("naver-list"));
  setSel("naver-list-sort", "author"); ok(/3개 적용/.test(label("naver-list")), "정렬은 세지 않는다");
  setSel("naver-list-filter-status", "all"); setSel("naver-list-platform-filter", ""); setSel("naver-list-badge-filter", ""); ok(label("naver-list") === "필터▾", "모두 풀면 다시 '필터'만");
  // 제외됨: 태그 필터는 네이버만 볼 때만 보이니 숨겨져 있으면 세지 않는다
  w.__ex = [{ platform: "naver", title_id: "1", title: "가", weekdays: ["mon"], tags: ["A"], status: "excluded" }];
  run("subscriptionCache.excluded = __ex; populateFilterOptions('excluded'); renderSubscriptionTab('excluded');");
  setSel("excluded-platform-filter", "naver"); setSel("excluded-tag-filter", "A"); ok(/2개 적용/.test(label("excluded")), "네이버 + 태그 = 2개: " + label("excluded"));
  setSel("excluded-platform-filter", "kakao"); ok(/1개 적용/.test(label("excluded")), "카카오로 바꾸면 태그가 풀리고 숨겨져 플랫폼 1개만: " + label("excluded"));
  toggle("excluded").click(); ok(/필터.*▴/.test(label("excluded")), "펴면 화살표가 바뀜");
  // 숨겨진 선택은 값이 남아 있어도 세지 않는다(보이지 않는 필터가 "적용됨"으로 보이면 헷갈린다)
  setSel("excluded-platform-filter", "naver"); $("excluded-tag-filter").value = "A"; $("excluded-tag-filter").classList.add("hidden"); run("updateFilterToggleLabels();");
  ok(/1개 적용/.test(label("excluded")), "숨겨진 태그 선택은 세지 않는다: " + label("excluded"));
  $("excluded-tag-filter").classList.remove("hidden"); setSel("excluded-platform-filter", "");

  // Scenario 4: 메뉴바 — 탭을 바꾸면 선택된 탭이 한 줄 메뉴바의 가운데 쪽으로 스크롤된다(가로 스와이프로만 보이는 탭이 가려지지 않게)
  const bar = w.document.querySelector(".main-tabs"), tab = (page) => w.document.querySelector(`.main-tab[data-page="${page}"]`);
  Object.defineProperty(bar, "clientWidth", { value: 360, configurable: true }); Object.defineProperty(bar, "scrollWidth", { value: 900, configurable: true });
  Object.defineProperty(tab("archive"), "offsetLeft", { value: 600, configurable: true }); Object.defineProperty(tab("archive"), "offsetWidth", { value: 80, configurable: true });
  Object.defineProperty(tab("naver-list"), "offsetLeft", { value: 0, configurable: true }); Object.defineProperty(tab("naver-list"), "offsetWidth", { value: 100, configurable: true });
  bar.scrollLeft = 0; run("switchToTab('archive');"); ok(bar.scrollLeft === 600 - (360 - 80) / 2, "선택한 탭을 가운데로: " + bar.scrollLeft);
  run("switchToTab('naver-list');"); ok(bar.scrollLeft === 0, "맨 앞 탭이면 0(음수로 넘치지 않음): " + bar.scrollLeft);
  Object.defineProperty(bar, "scrollWidth", { value: 360, configurable: true }); bar.scrollLeft = 7; run("switchToTab('archive');"); ok(bar.scrollLeft === 7, "넘치지 않으면(데스크톱) 스크롤하지 않는다");

  // Scenario 5: CSS — 모바일(≤640px)에서만 바뀐다. 메뉴바는 한 줄+가로 스크롤, 툴바 상자는 없애서(contents) 검색창이 페이지 전체에서 고정되고, 필터는 접히고, 요일 탭이 검색창 아래에 고정된다. 데스크톱은 필터를 그대로 펼친다
  const parts = mobileBlock(css);
  ok(!!parts, "모바일 미디어 쿼리가 있다");
  const m = parts.inside, d = parts.outside;
  const navRule = ruleOf(m, ".main-tabs");
  ok(navRule && /flex-wrap: nowrap/.test(navRule) && /overflow-x: auto/.test(navRule), "메뉴바: 한 줄 + 가로 스크롤: " + navRule);
  ok(/white-space: nowrap|flex: 0 0 auto/.test(ruleOf(m, ".main-tab") || ""), "메뉴 항목이 줄어들거나 줄바꿈되지 않는다");
  ok(/display: contents/.test(ruleOf(m, ".list-toolbar") || ""), "모바일: 툴바 상자를 없앤다(검색창이 페이지 전체에서 고정되도록)");
  const searchRule = ruleOf(m, ".list-toolbar > .clearable-wrap");
  ok(searchRule && /position: sticky/.test(searchRule) && /top: var\(--m-nav-h\)/.test(searchRule) && /background: var\(--bg\)/.test(searchRule), "검색창: 메뉴바 바로 아래에 고정(배경 있음): " + searchRule);
  const tabsRule = ruleOf(m, ".list-toolbar > .weekday-tabs");   // 요일 탭은 고정 툴바 안에 있고, 모바일에서는 툴바 상자를 없애므로(contents) 페이지 전체에서 고정된다
  ok(tabsRule && /position: sticky/.test(tabsRule) && /top: calc\(var\(--m-nav-h\) \+ var\(--m-search-h\)\)/.test(tabsRule) && /background: var\(--bg\)/.test(tabsRule), "요일 탭: 검색창 바로 아래에 고정: " + tabsRule);
  ok(/display: none/.test(ruleOf(m, ".list-filters") || "") && /display: flex/.test(ruleOf(m, ".filters-open .list-filters") || "") && /display: none/.test(ruleOf(m, ".list-head-status") || ""), "필터 묶음은 접혀 있다가 filters-open에서 펼쳐진다");
  ok(/display: (flex|block)/.test(ruleOf(m, ".filter-toggle") || ""), "모바일에서 접기 버튼이 보인다");
  ok(/display: none/.test(ruleOf(d, ".filter-toggle") || "") && /display: contents/.test(ruleOf(d, ".list-filters") || ""), "데스크톱: 접기 버튼은 없고 필터는 예전처럼 툴바에 그대로(contents)");
  ok(!/\.weekday-tabs[^{]*\{[^}]*sticky/.test(d), "데스크톱의 요일 탭은 고정하지 않는다(모바일만)");
  ok(/--m-nav-h:\s*\d+px/.test(css) && /--m-search-h:\s*\d+px/.test(css), "고정 위치 계산용 높이 변수가 있다");
  done();
})();
