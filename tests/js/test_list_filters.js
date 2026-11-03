// 목록 화면의 공용 거르기(전체목록/구독해제/제외됨이 같이 쓴다) — 뱃지 필터, 검색(제목/작가/저자 요약), 플랫폼 필터, 요일 탭 적용.
// 예전에는 같은 거르기 코드가 전체목록과 구독/제외됨 그리기 함수에 따로 복사돼 있었다.
const { makeApp, idle, ok, done } = require("./harness");
const { listItem } = require("./factories");
(async () => {
  const { w, run } = makeApp({ "/api/diagnostics/weekday-gaps": { status: null, naver: { total: 0, reasons: {}, failed_http: {}, samples: {} }, kakao: { total: 0, reasons: {}, failed_http: {}, samples: {} } } }, { now: "2026-11-03T03:00:00Z" }); // 화요일
  await idle();
  const fn = (name) => w.eval(name);
  const row = (extra) => listItem("naver", 1, "쌍갑포차", ["tue"], extra);

  // Scenario 1: 뱃지 필터 — 비어 있으면 전부, new/paused/up은 해당 표시가 있는 것만, 모르는 값은 전부(거르지 않음)
  const badge = fn("matchesBadgeFilter");
  ok(badge(row({}), "") === true && badge(row({}), "알수없음") === true, "필터 없음/모르는 값은 통과");
  ok(badge(row({ is_new: true }), "new") && !badge(row({}), "new"), "new");
  ok(badge(row({ is_paused: true }), "paused") && !badge(row({}), "paused"), "paused");
  ok(badge(row({ has_new_episode: true }), "up") && !badge(row({}), "up"), "up");

  // Scenario 2: 검색 — 소문자로 바꾼 검색어가 제목/저자 요약에 들어 있으면 통과(비면 전부). 작가 이름까지 볼지는 호출하는 쪽이 정한다(구독해제/제외됨은 작가 이름도)
  const search = fn("matchesSearch");
  ok(search(row({ author_summary: "허먼 / 김" }), "", false) && search(row({ author_summary: "허먼 / 김" }), "쌍갑", false) && search(row({ author_summary: "허먼 / 김" }), "허먼", false), "제목/저자 요약");
  ok(!search(row({ author_summary: "허먼" }), "없는말", false), "맞는 게 없으면 제외");
  ok(!search(row({ writer_names: ["김작가"] }), "김작가", false) && search(row({ writer_names: ["김작가"] }), "김작가", true), "작가 이름은 지정했을 때만");
  ok(search(row({ title: "ABC" }), "abc", false), "대소문자 무시(검색어는 소문자로 들어온다)");

  // Scenario 3: 공용 거르기 — 플랫폼(없으면 네이버로 본다)과 뱃지를 한 번에
  const common = fn("applyCommonFilters");
  const rows = [listItem("naver", 1, "N1", ["tue"], { is_new: true }), listItem("kakao", 2, "K1", ["tue"], { is_new: true }), listItem("naver", 3, "N2", ["tue"]), { ...listItem("naver", 4, "플랫폼없음", ["tue"]), platform: undefined }];
  ok(common(rows, { platform: "", badge: "" }).length === 4, "필터 없음");
  ok(common(rows, { platform: "kakao", badge: "" }).map((r) => r.title).join() === "K1", "카카오만");
  ok(common(rows, { platform: "naver", badge: "" }).map((r) => r.title).join() === "N1,N2,플랫폼없음", "네이버만(플랫폼 값이 없으면 네이버)");
  ok(common(rows, { platform: "", badge: "new" }).map((r) => r.title).join() === "N1,K1", "뱃지만");
  ok(common(rows, { platform: "naver", badge: "new" }).map((r) => r.title).join() === "N1", "둘 다");

  // Scenario 4: 요일 탭 적용 — 개수는 (탭을 적용하기 전) 넘겨받은 목록 기준, 탭 줄을 그리고, 고른 탭의 작품만 돌려준다. 검색 중이면 전체. 탭을 누르면 창을 처음으로 돌리고 다시 그리라고 알린다
  const tabsEl = w.document.createElement("div"), noteEl = w.document.createElement("p"); w.document.body.append(tabsEl, noteEl);
  const mixed = [listItem("naver", 1, "화", ["tue"]), listItem("naver", 2, "수", ["wed"]), listItem("naver", 3, "기타", [])];
  let rerenders = 0; w.__apply = fn("applyWeekdayTabs");
  const labelOf = (el) => [...el.querySelectorAll(".weekday-tab")].map((t) => t.firstChild.textContent.trim() + ":" + t.querySelector(".weekday-tab-count").textContent).join(" ");
  let shown = w.__apply("ctx-a", mixed, { query: "", tabsEl, etcNoteEl: noteEl, etcStatus: null, onChange: () => { rerenders++; } });
  ok(shown.map((r) => r.title).join() === "화", "오늘(화요일) 탭의 작품만: " + shown.map((r) => r.title));
  ok(labelOf(tabsEl) === "월:0 화:1 수:1 목:0 금:0 토:0 일:0 기타:1 전체:3", "탭 개수는 받은 목록 기준: " + labelOf(tabsEl));
  [...tabsEl.querySelectorAll(".weekday-tab")].find((t) => t.firstChild.textContent.trim() === "수").click();
  ok(rerenders === 1, "탭을 누르면 다시 그리라고 알린다");
  shown = w.__apply("ctx-a", mixed, { query: "", tabsEl, etcNoteEl: noteEl, etcStatus: null, onChange: () => { rerenders++; } });
  ok(shown.map((r) => r.title).join() === "수", "누른 탭이 기억된다: " + shown.map((r) => r.title));
  shown = w.__apply("ctx-a", mixed, { query: "검색중", tabsEl, etcNoteEl: noteEl, etcStatus: null, onChange: () => {} });
  ok(shown.length === 3 && tabsEl.querySelector(".weekday-tab.active").firstChild.textContent.trim() === "전체", "검색 중에는 전체");
  ok(w.__apply("ctx-b", mixed, { query: "", tabsEl, etcNoteEl: noteEl, etcStatus: null, onChange: () => {} }).map((r) => r.title).join() === "화", "화면(ctx)마다 따로 기억한다");
  done();
})();
