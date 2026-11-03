// 화면 테스트 실행기 — tests/js의 test_*.js를 파일마다 별도 프로세스로 돌린다(전역 상태가 섞이지 않게).
// 사용법: (tests/js에서) npm install && npm test      또는  node run.js [이름조각]
// 컴퓨터 코어 수만큼(최대 4개) 동시에 돌리고, 결과는 파일 이름 순서대로 출력한다.
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const pattern = process.argv[2] || "";
const files = fs.readdirSync(__dirname).filter((f) => /^test_.*\.js$/.test(f) && f.includes(pattern)).sort();
const concurrency = Math.max(1, Math.min(4, os.cpus().length));

function runOne(file) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [path.join(__dirname, file)], { stdio: ["ignore", "pipe", "pipe"] });
    let output = "";
    child.stdout.on("data", (chunk) => (output += chunk));
    child.stderr.on("data", (chunk) => (output += chunk));
    const timer = setTimeout(() => child.kill("SIGKILL"), 120000);
    child.on("close", (code) => {
      clearTimeout(timer);
      resolve({ file, ok: code === 0, output });
    });
  });
}

async function main() {
  const results = new Array(files.length);
  let next = 0;
  const workers = Array.from({ length: Math.min(concurrency, files.length) }, async () => {
    while (next < files.length) {
      const index = next++;
      results[index] = await runOne(files[index]);
    }
  });
  await Promise.all(workers);
  let failed = 0;
  for (const { file, ok, output } of results) {
    console.log(`${ok ? "PASS" : "FAIL"}  ${file}`);
    if (!ok) {
      failed++;
      console.log(output.split("\n").filter((l) => !l.includes("Not implemented")).slice(-15).join("\n"));
    }
  }
  console.log(`\n${files.length - failed}/${files.length} 통과`);
  process.exit(failed ? 1 : 0);
}
main();
