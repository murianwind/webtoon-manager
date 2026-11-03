// 화면 테스트 하네스(harness.js) 자체의 도우미 — 고정 sleep 대신 쓰는 idle/waitFor/시간 축소가 약속대로 동작하는지.
//  · idle(): 진행 중인 fetch가 모두 끝나고 그 뒤의 비동기 후속 처리까지 흘러간 뒤에 돌아온다(고정 시간 대기 없이 "다 끝났을 때" 진행)
//  · waitFor(조건): 조건이 참이 될 때까지 짧게 반복 확인하고, 시간이 지나도 안 되면 무엇을 기다렸는지 담아 실패한다
//  · makeApp(routes, { timeScale }): 앱의 setTimeout/setInterval 시간을 n배 빠르게(예: 3초 폴링을 0.3초로) — 타이머로 도는 동작을 빠르게 확인
const { makeApp, sleep, idle, waitFor, ok, done } = require("./harness");
(async () => {
  // Scenario 1: idle — 느린 fetch(40ms) 두 개가 이어서 나가는 동작이 끝난 뒤에 돌아온다. 아무것도 진행 중이 아니면 바로
  let finished = 0;
  const { w, run, calls } = makeApp({ "/api/slow": async () => { await sleep(40); finished++; return {}; } });
  let t = Date.now(); await idle(); ok(Date.now() - t < 30, "진행 중인 것이 없으면 바로 돌아온다");
  w.fetch = ((orig) => (...a) => orig(...a))(w.fetch);                                                                                   // (래핑돼도 계속 추적된다)
  run("window.__chain = (async () => { await apiCall('/api/slow'); await apiCall('/api/slow'); window.__chainDone = true; })();");
  await idle(); ok(w.__chainDone === true, "idle은 이어지는 두 번째 fetch와 그 뒤 처리까지 기다린다(끝난 fetch 수 " + calls.filter((c) => c.url === "/api/slow").length + ")");

  // Scenario 2: waitFor — 조건이 나중에 참이 되면 그때 돌아오고(고정 대기보다 빠르게), 끝내 안 되면 설명이 담긴 오류
  let flag = false; setTimeout(() => { flag = true; }, 60);
  t = Date.now(); await waitFor(() => flag, "깃발이 서기를"); const elapsed = Date.now() - t;
  ok(flag && elapsed >= 40 && elapsed < 400, `조건이 참이 되면 바로 돌아온다(${elapsed}ms)`);
  let message = ""; try { await waitFor(() => false, "절대 안 되는 조건", { timeout: 80 }); } catch (e) { message = e.message; }
  ok(/절대 안 되는 조건/.test(message) && /80ms/.test(message), "시간 초과 오류에 기다리던 것과 제한 시간이 담긴다: " + message);
  ok(await waitFor(() => "값", "값을") === "값", "조건이 돌려준 값을 그대로 돌려준다");

  // Scenario 3: 시간 축소 — 앱의 setTimeout이 n배 빨라진다(실제 대기 시간은 1/n). 기본은 그대로
  const fast = makeApp({}, { timeScale: 20 });
  fast.run("window.__fired = 0; setTimeout(() => { window.__fired++; }, 1000);");
  await sleep(120); ok(fast.w.__fired === 1, "1000ms 타이머가 20배 빠르게(50ms) 동작");
  const normal = makeApp({}); normal.run("window.__fired = 0; setTimeout(() => { window.__fired++; }, 80);");
  await sleep(30); ok(normal.w.__fired === 0, "기본은 시간 그대로(30ms에는 아직 안 지남)"); await sleep(120); ok(normal.w.__fired === 1, "기본 80ms 타이머는 지나면 동작");
  ok(fast.wait(1000) instanceof Promise, "wait(ms): 앱 시간 기준으로 기다리는 도우미(축소 시간에 맞춰 짧게)");
  t = Date.now(); await fast.wait(1000); ok(Date.now() - t < 250, "wait(1000)은 시간 축소 때 짧게 끝난다");
  done();
})();
