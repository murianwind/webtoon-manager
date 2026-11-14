// 수동 실행 화면 맨 위의 버튼 줄(.manual-run-buttons) — 좁은 화면(모바일)에서 버튼이 여섯 개를 한 줄에 억지로 넣으려다 글자가 세로로 쪼개지고 화면 밖으로 밀려 레이아웃이 깨진 적이 있다.
// 줄이 넘치면 다음 줄로 내려가고(flex-wrap), 버튼 글자는 줄바꿈되지 않으며(한 글자씩 세로로 쪼개지지 않게), 선택 상자가 화면보다 커지지 않는다.
// jsdom은 레이아웃을 계산하지 않아서 스타일 규칙이 실제 파일에 들어 있는지를 직접 읽어 확인한다.
const { makeApp, idle, ok, done } = require("./harness");
(async () => {
  const css = require("fs").readFileSync(require("path").join(__dirname, "../../app/static/style.css"), "utf8");
  const block = (selector) => { const m = css.match(new RegExp(selector.replace(/[.#>\s]/g, (c) => (c === " " ? "\\s+" : "\\" + c)) + "\\s*\\{([^}]*)\\}")); return m ? m[1].replace(/\s+/g, " ").trim() : ""; };
  const row = block(".manual-run-buttons"), buttons = block(".manual-run-buttons button"), select = block(".manual-run-buttons .run-target-select");
  ok(/display:\s*flex/.test(row) && /flex-wrap:\s*wrap/.test(row), "버튼 줄은 넘치면 다음 줄로: " + row);
  ok(/white-space:\s*nowrap/.test(buttons), "버튼 글자는 줄바꿈하지 않는다(한 글자씩 세로로 쪼개지지 않게): " + buttons);
  ok(/max-width:\s*100%/.test(select), "선택 상자가 화면보다 커지지 않는다: " + select);

  // 마크업: 이 줄 안에 버튼 여섯 개와 선택 상자가 모두 들어 있다(줄바꿈으로 내려가도 누락되지 않는다)
  const { w } = makeApp({}); await idle();
  const rowEl = w.document.querySelector(".manual-run-buttons");
  const ids = [...rowEl.querySelectorAll("button")].map((b) => b.id);
  ok(JSON.stringify(ids) === JSON.stringify(["btn-run-discovery", "btn-run-quick-scan", "btn-run-download", "btn-run-metadata-sync", "btn-run-report"]) && rowEl.querySelector("select#run-download-target"), "버튼 줄의 구성: " + ids);
  done();
})();
