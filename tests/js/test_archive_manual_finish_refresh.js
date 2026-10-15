// 아카이빙 수동 실행: "완결 처리로 이동"이 끝나면 이동을 마친 웹툰은 선택 목록(과 위의 아카이빙 대상 목록)에서 사라지고, 성공했으면 "완결 처리로 이동" 체크도 풀린다.
// 새로고침하면 사라지는 건 원래도 그랬지만, 그러면 진행 로그가 같이 사라져서 "성공했다는데 목록엔 그대로"라 헷갈렸다 — 로그는 그대로 두고 목록만 서버 기준으로 다시 그린다.
const { makeApp, sleep, ok, done } = require("./harness");
(async () => {
  let targets = ["A", "B", "C"].map((id) => ({ title_id: id, title_name: `웹툰${id}`, enabled: true, platform: "naver", source_type: "webtoon", dest_base_path: "보관", dest_type: "local", filename_template_preset_id: null, display_name: null }));
  let jobStatus = { status: "idle", log: [] };
  const runs = []; let targetGets = 0;
  let onRun = () => {};
  const routes = {
    "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" },
    "/api/archive/targets": () => { targetGets++; return targets; }, "/api/archive/presets": [], "/api/archive/settings": { local_available: true, rclone_available: false, conflict_policy: "skip" },
    "/api/webtoons": [], "/api/kakao-webtoons": [],
    "/api/archive/run": (opts) => { const body = JSON.parse(opts.body); runs.push(body); jobStatus = { status: "running", log: ["[웹툰A] 이동 완료: 0001.zip"] }; onRun(body); return { status: "started" }; },
    "/api/jobs/status": () => ({ archive: jobStatus }),
  };
  const { w, run } = makeApp(routes); await sleep(60);
  const $ = (id) => w.document.getElementById(id);
  w.confirm = () => true; w.alert = () => {};
  const listIds = () => [...w.document.querySelectorAll("#archive-manual-select-list input")].map((i) => i.value);
  const checkedIds = () => [...w.document.querySelectorAll("#archive-manual-select-list input:checked")].map((i) => i.value);
  const topIds = () => [...w.document.querySelectorAll("#archive-target-list .archive-target-item, #archive-target-list [data-title-id]")].length;
  const poll = async () => { run("window.__p = refreshArchiveJobStatus();"); await w.__p; await sleep(60); };
  const check = (id, on = true) => { const cb = w.document.querySelector(`#archive-manual-select-list input[value="${id}"]`); cb.checked = on; };
  run("window.__p = loadArchiveManualSelectList();"); await w.__p; await sleep(40);

  // Scenario 1: 완결 처리로 이동 — A는 문제없이 끝나 대상이 삭제되고, B는 실패해 남는다. 실행 중에는 목록이 그대로다
  check("A"); check("B"); $("archive-manual-full-move-toggle").checked = true; $("archive-manual-full-move-toggle").dispatchEvent(new w.Event("change"));
  onRun = () => { targets = targets.filter((t) => t.title_id !== "A"); };
  $("btn-run-archive-now").click(); await sleep(80);
  ok(runs.length === 1 && JSON.stringify(runs[0]) === JSON.stringify({ title_ids: ["A", "B"], full_move: true }), "완결 처리 실행 요청: " + JSON.stringify(runs));
  await poll();
  ok(JSON.stringify(listIds()) === JSON.stringify(["A", "B", "C"]) && /이동 완료/.test($("archive-log").textContent), "실행 중에는 목록이 그대로이고 로그가 보인다: " + listIds());
  // 끝났다(성공): 이동을 마친 A는 목록에서 사라지고, 남은 B의 선택은 유지, 완결 처리 체크는 풀리고 경고도 숨는다. 로그는 그대로
  jobStatus = { status: "success", log: ["[웹툰A] 이동 완료: 0001.zip", "[웹툰A] 완결 처리 완료 — 아카이빙 대상에서 삭제했습니다", "1개 파일 이동 완료"] };
  await poll();
  ok(JSON.stringify(listIds()) === JSON.stringify(["B", "C"]), "끝난 뒤 이동을 마친 웹툰은 사라짐: " + listIds());
  ok(JSON.stringify(checkedIds()) === JSON.stringify(["B"]), "남은 항목의 선택은 유지: " + checkedIds());
  ok($("archive-manual-full-move-toggle").checked === false && $("archive-manual-full-move-warning").classList.contains("hidden"), "성공하면 완결 처리로 이동 체크와 경고가 풀린다");
  ok(/완결 처리 완료/.test($("archive-log").textContent) && $("archive-status-badge").textContent === "success", "진행 로그와 상태는 그대로 남는다: " + $("archive-log").textContent);
  // 아카이빙 대상 목록(위 카드)도 같이 다시 그려진다 — 서버 목록을 다시 읽었다
  const getsBefore = targetGets; await poll(); ok(targetGets === getsBefore, "끝난 뒤 계속 폴링해도 다시 읽지 않는다(한 번만 갱신)");

  // Scenario 2: 실패(오류)로 끝나면 목록은 서버 기준으로 갱신하되 완결 처리 체크는 그대로 둔다(다시 시도하기 쉽게)
  check("B"); $("archive-manual-full-move-toggle").checked = true;
  onRun = () => {};
  $("btn-run-archive-now").click(); await sleep(80);
  jobStatus = { status: "error", log: ["[웹툰B] 이동 실패: 0002.zip — 디스크 오류"] };
  await poll();
  ok(JSON.stringify(listIds()) === JSON.stringify(["B", "C"]) && $("archive-manual-full-move-toggle").checked === true, "오류로 끝나면 체크는 유지: " + $("archive-manual-full-move-toggle").checked);

  // Scenario 3: 완결 처리가 아닌 일반 수동 이동은 대상이 그대로라서 목록을 다시 읽지 않는다
  $("archive-manual-full-move-toggle").checked = false; check("C");
  $("btn-run-archive-now").click(); await sleep(80);
  jobStatus = { status: "success", log: ["끝"] };
  const before3 = targetGets; await poll();
  ok(targetGets === before3, "일반 수동 이동 뒤에는 목록을 다시 읽지 않는다");
  done();
})();
