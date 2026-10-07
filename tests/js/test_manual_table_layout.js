// 수동 다운로드 표: 스크롤해도 머리글(번호/제목/상태/대여 만료/진행) 줄이 고정되고, 진행 칸에 "이미받음 + 보관 폴더로 옮김"이 잘리지 않고 다 보인다.
// (jsdom은 레이아웃을 계산하지 못해서, 문제를 일으켰던 CSS 규칙이 없는지/필요한 규칙이 있는지를 스타일 시트 글자로 확인한다)
const fs = require("fs");
const path = require("path");
const { ok, done } = require("./harness");
const css = fs.readFileSync(path.join(__dirname, "..", "..", "app", "static", "style.css"), "utf8");

const rules = [...css.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => ({ selector: m[1].trim(), body: m[2] }));
const rulesFor = (needle) => rules.filter((r) => r.selector.split(",").map((s) => s.trim()).includes(needle));

// Scenario 1: 표 자체에 overflow가 걸려 있으면(둥근 모서리를 자르려고 넣었던 hidden) 그 표가 스크롤 영역이 되어 머리글 고정(sticky)이 먹지 않는다
for (const table of ["#manual-table", "#kakao-manual-table"]) {
  const overflow = rulesFor(table).filter((r) => /overflow\s*:\s*(hidden|auto|scroll)/.test(r.body));
  ok(overflow.length === 0, `${table}에 overflow 규칙이 없다(머리글 고정을 막음): ${overflow.map((r) => r.selector).join(" | ")}`);
}

// Scenario 2: 스크롤 영역은 표를 감싼 wrapper이고, 그 안의 머리글 칸이 위에 고정된다
const sticky = rulesFor(".scroll-table-wrapper thead th").find((r) => /position\s*:\s*sticky/.test(r.body) && /top\s*:\s*0/.test(r.body));
ok(!!sticky, "wrapper 안 머리글 칸이 position: sticky; top: 0");
const wrapper = rulesFor(".scroll-table-wrapper").find((r) => /overflow-y\s*:\s*auto/.test(r.body));
ok(!!wrapper, "wrapper가 세로 스크롤 영역");
const stickyHasBackground = rulesFor("#manual-table th, #kakao-manual-table th").length > 0 || /#kakao-manual-table th[^{]*\{[^}]*background/.test(css);
ok(stickyHasBackground, "머리글 칸에 배경색이 있어 아래 줄이 비쳐 보이지 않는다");

// Scenario 3: 진행(6번째) 칸이 "이미받음" 배지 + "보관 폴더로 옮김" 글자를 담을 만큼 넓다
const progress = rulesFor("#kakao-manual-table th:nth-child(6)").concat(rulesFor("#kakao-manual-table td:nth-child(6)")).map((r) => r.body).join(" ");
const width = /width\s*:\s*(\d+)px/.exec(progress);
ok(width && Number(width[1]) >= 180, `진행 칸 폭이 180px 이상: ${width ? width[1] : "없음"}px`);
done();
