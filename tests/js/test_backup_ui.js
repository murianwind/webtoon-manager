// 백업/복원 화면 — 복원 결과(경고/다시 입력할 항목) 표시, 확인할 게 없으면 자동 새로고침, 확인 창 문구, 실패 처리
const { makeApp, sleep, ok, done } = require("./harness");
(async () => {
  let response = { status: "restored", warnings: [], reenter: [] }; let fail = null;
  const routes = {
    "/api/settings/kakao-webtoons-enabled": { enabled: false }, "/api/settings/webtoon-server": { webtoon_server_url: "" },
    "/api/restore": () => { if (fail) throw new Error(fail); return response; },
  };
  const { w, run, calls } = makeApp(routes); await sleep(60);
  const $ = (id) => w.document.getElementById(id);
  run("window.__reloads = 0; scheduleReload = () => { window.__reloads += 1; };");
  const confirms = []; w.confirm = (m) => { confirms.push(m); return true; };
  const pick = async (data) => {
    const file = { text: async () => (typeof data === "string" ? data : JSON.stringify(data)) };
    Object.defineProperty($("restore-file-input"), "files", { value: [file], configurable: true });
    $("restore-file-input").dispatchEvent(new w.Event("change")); await sleep(60);
  };

  // 1) 확인할 게 없으면: 완료 문구 + 자동 새로고침, 확인 창에 "유지되는 것" 안내
  await pick({ webtoons: [], _meta: { format_version: 2 } });
  ok(calls.some((c) => c.url === "/api/restore" && c.method === "POST"), "복원 요청을 보냄");
  ok(/디스코드|카카오/.test(confirms[0]) && /유지/.test(confirms[0]) && /모두 지우고/.test(confirms[0]), "확인 창: 데이터를 모두 지우고 복원하지만 로그인/웹훅 같은 지금 설정은 유지된다고 안내: " + confirms[0]);
  ok(/복원 완료/.test($("backup-result").textContent) && w.__reloads === 1 && $("backup-report").classList.contains("hidden"), "경고가 없으면 '복원 완료' + 자동 새로고침(결과 영역은 숨김)");

  // 2) 경고/다시 입력할 항목이 있으면: 목록으로 보여 주고 자동 새로고침하지 않는다(읽을 시간) + 직접 새로고침 버튼
  w.__reloads = 0;
  response = { status: "restored", warnings: ["카카오페이지 구독 작품 2개의 폴더가 없습니다(쌍갑포차, 개미). 자동 다운로드가 처음부터 전부 받습니다."], reenter: ["디스코드 웹훅 주소 — 알림과 리포트를 받으려면", "카카오페이지 로그인 쿠키 — 카카오페이지 다운로드를 쓰려면"] };
  await pick({ webtoons: [] });
  const rep = $("backup-report");
  ok(!rep.classList.contains("hidden") && w.__reloads === 0, "경고가 있으면 결과 영역이 보이고 자동으로 새로고침하지 않음");
  const items = (sel) => Array.from(rep.querySelectorAll(sel)).map((e) => e.textContent);
  ok(items(".backup-warnings li").length === 1 && items(".backup-warnings li")[0].includes("폴더가 없습니다"), "확인할 것 목록: " + JSON.stringify(items(".backup-warnings li")));
  ok(items(".backup-reenter li").length === 2 && items(".backup-reenter li")[1].includes("카카오페이지 로그인 쿠키"), "다시 입력할 항목 목록: " + JSON.stringify(items(".backup-reenter li")));
  ok(/복원 완료/.test($("backup-result").textContent) && !!rep.querySelector("button"), "복원 완료 문구 + 새로고침 버튼");
  rep.querySelector("button").click(); ok(true, "새로고침 버튼 동작(예외 없음)");

  // 3) 서버가 거부하면: 실패 문구, 결과 영역 숨김, 새로고침 안 함
  w.__reloads = 0; fail = "백업 파일이 아닌 것 같습니다 (백업 데이터가 하나도 들어 있지 않습니다).";
  await pick({ foo: 1 });
  ok(/복원 실패/.test($("backup-result").textContent) && $("backup-result").textContent.includes("백업 파일이 아닌 것 같습니다") && w.__reloads === 0 && $("backup-report").classList.contains("hidden"), "서버가 거부하면 실패 사유를 그대로 보여 줌");
  fail = null;

  // 4) JSON이 아닌 파일: 서버에 요청도 보내지 않고 실패
  const restoreCalls = () => calls.filter((c) => c.url === "/api/restore").length;
  const sent = restoreCalls(); await pick("이건 JSON이 아님");
  ok(/복원 실패/.test($("backup-result").textContent) && restoreCalls() === sent, "JSON이 아니면 서버에 보내지 않고 실패 표시");

  // 5) 확인 창에서 취소하면 아무것도 안 보냄
  w.confirm = () => false; const n = restoreCalls(); await pick({ webtoons: [] });
  ok(restoreCalls() === n, "확인 창에서 취소하면 복원 요청을 보내지 않음");
  done();
})();
