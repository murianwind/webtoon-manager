// 설정의 "백업 / 복원" 카드: 백업 다운로드 버튼과 복원 파일 선택 칸이 한 줄에 같은 높이로 놓이고, 아래 안내 문구가 버튼을 덮지 않는다.
// (jsdom은 레이아웃을 계산하지 못해서, 문제였던 구조/스타일 규칙을 확인한다)
const fs = require("fs");
const path = require("path");
const { makeApp, sleep, ok, done } = require("./harness");

const css = fs.readFileSync(path.join(__dirname, "..", "..", "app", "static", "style.css"), "utf8");
const rules = [...css.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => ({ selector: m[1].trim(), body: m[2] }));
const bodyOf = (selector) => rules.filter((r) => r.selector.split(",").map((s) => s.trim()).includes(selector)).map((r) => r.body).join(" ");

(async () => {
  const { w } = makeApp({ "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" } });
  await sleep(60);
  const $ = (id) => w.document.getElementById(id);

  // Scenario 1: 버튼과 복원 파일 선택 칸이 같은 한 줄 묶음 안에 있다
  const row = $("btn-backup-download").parentElement;
  ok(row.classList.contains("backup-actions"), "백업 다운로드 버튼이 .backup-actions 묶음 안에 있다");
  ok(row.contains($("restore-file-input")), "복원 파일 선택 칸도 같은 묶음 안에 있다");

  // Scenario 2: 묶음은 가로 정렬 + 세로 가운데 맞춤 + 줄바꿈 가능 + 아래 간격
  const rowCss = bodyOf(".backup-actions");
  ok(/display\s*:\s*flex/.test(rowCss), "묶음이 flex(가로 정렬)");
  ok(/align-items\s*:\s*center/.test(rowCss), "묶음이 세로 가운데 맞춤");
  ok(/flex-wrap\s*:\s*wrap/.test(rowCss), "좁은 화면에서는 줄바꿈");
  ok(/gap\s*:\s*\d+px/.test(rowCss), "항목 사이 간격");

  // Scenario 3: 복원 파일 선택 칸 안의 글자와 파일 입력은 한 줄로 나란히(세로로 쌓여 키가 커지지 않게)
  const labelCss = bodyOf(".backup-actions .file-input-label");
  ok(/display\s*:\s*flex/.test(labelCss) && /align-items\s*:\s*center/.test(labelCss), "복원 파일 선택 칸은 글자와 입력이 가로로 나란히");

  // Scenario 4: 안내 문구는 위로 끌려 올라가지 않는다(.hint의 음수 위쪽 여백이 이 카드에서는 0) — 버튼을 덮던 원인
  const hint = $("backup-result").nextElementSibling;
  ok(hint.classList.contains("hint") && hint.classList.contains("hint-after-controls"), "안내 문구에 .hint-after-controls");
  const hintCss = bodyOf(".hint-after-controls");
  ok(/margin-top\s*:\s*0\b/.test(hintCss), "이 카드의 안내 문구는 위쪽 여백 0: " + hintCss.trim());
  ok(/margin-bottom\s*:\s*\d+px/.test(rowCss), "묶음 아래에 간격이 있어 문구와 붙지 않는다");

  // Scenario 5: 다른 화면의 일반 .hint(음수 여백)는 그대로다
  ok(/margin\s*:\s*-8px\s+0\s+16px/.test(bodyOf(".hint")), "일반 .hint 규칙은 바뀌지 않았다");
  done();
})();
