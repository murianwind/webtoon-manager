// 검색창 지우기(x) 버튼: 검색어가 있을 때만 보이고, 누르면 검색어를 비우고 목록이 다시 그려지도록 input 이벤트를 보낸다.
const { makeApp, sleep, ok, done } = require("./harness");
(async () => {
  const { w } = makeApp({ "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" }, "/api/naver-list": [] });
  await sleep(60);
  const $ = (id) => w.document.getElementById(id);
  const IDS = ["naver-list-search", "unsubscribed-search", "excluded-search", "manual-query", "episode-history-search", "author-candidate-filter", "tag-catalog-search", "kakao-author-candidate-filter"];
  const clearBtn = (input) => input.parentElement.querySelector(".input-clear");

  for (const id of IDS) {
    const input = $(id);
    const btn = input.parentElement.classList.contains("clearable-wrap") ? clearBtn(input) : null;
    // Scenario 1: 모든 검색창 옆에 지우기 버튼이 있다
    ok(!!btn && btn.textContent === "×" && btn.type === "button", `${id}: 지우기 버튼이 있다`);
    if (!btn) continue;
    ok(btn.getAttribute("aria-label") === "검색어 지우기", `${id}: 접근성 이름`);

    // Scenario 2: 비었을 때는 숨고(CSS :placeholder-shown), 입력 칸은 placeholder를 유지한다
    ok(input.placeholder !== "", `${id}: placeholder가 있어 비었을 때 버튼이 CSS로 숨는다`);

    // Scenario 3: 누르면 검색어가 지워지고, 목록이 다시 그려지도록 input 이벤트가 나가고, 입력 칸으로 포커스가 돌아온다
    input.value = "검색어";
    let inputEvents = 0; input.addEventListener("input", () => inputEvents++);
    btn.click();
    ok(input.value === "", `${id}: 누르면 검색어가 지워진다`);
    ok(inputEvents === 1, `${id}: input 이벤트 1번(목록 다시 그리기): ${inputEvents}`);
    ok(w.document.activeElement === input, `${id}: 입력 칸에 포커스`);
  }

  // Scenario 4: 위치가 바뀌어도 입력 칸 id와 부모 구조는 유지된다(기존 코드가 id로 찾는다)
  ok($("naver-list-search").tagName === "INPUT" && $("naver-list-search").closest(".toolbar") !== null, "전체목록 검색창은 그대로 툴바 안에 있다");

  // Scenario 5: 이미 저장된 검색어가 코드로 채워져도(앱 시작 시 복원) 버튼은 CSS만으로 보인다 — 한 번 더 초기화해도 버튼이 중복되지 않는다
  w.eval("initClearableInputs()");
  ok(w.document.querySelectorAll("#naver-list-search ~ .input-clear, .clearable-wrap #naver-list-search").length >= 1 && $("naver-list-search").parentElement.querySelectorAll(".input-clear").length === 1, "초기화를 다시 불러도 버튼은 하나");
  done();
})();
