// 화면 테스트 실행기 — tests/js의 test_*.js를 하나씩 별도 프로세스로 돌린다.
// 사용법: (tests/js에서) npm install && npm test      또는  node run.js [이름조각]
const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const pattern = process.argv[2] || "";
const files = fs.readdirSync(__dirname).filter((f) => /^test_.*\.js$/.test(f) && f.includes(pattern)).sort();
let failed = 0;
for (const f of files) {
  const r = spawnSync(process.execPath, [path.join(__dirname, f)], { encoding: "utf8", timeout: 120000 });
  const ok = r.status === 0;
  console.log(`${ok ? "PASS" : "FAIL"}  ${f}`);
  if (!ok) { failed++; console.log((r.stdout + r.stderr).split("\n").filter((l) => !l.includes("Not implemented")).slice(-15).join("\n")); }
}
console.log(`\n${files.length - failed}/${files.length} 통과`);
process.exit(failed ? 1 : 0);
