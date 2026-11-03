// 다운로드 이력 화면(80-history.js) — 목록 표시(성공/실패, HTML 이스케이프), 쪽 넘기기, 상태/검색 필터, 개별/전체 삭제, 보관 기간 저장.
// 검색 입력은 글자마다 서버에 요청하지 않고 멈춘 뒤 한 번만(다른 목록 화면과 같은 규칙).
const { makeApp, idle, ok, done } = require("./harness");
(async () => {
  const item = (id, title, extra = {}) => ({ id, title_name: title, episode_no: id, subtitle: `부제${id}`, downloaded_at: "2026-11-03T01:02:03+00:00", status: "success", error_msg: "", ...extra });
  let data = { items: [item(1, "가"), item(2, "<b>나</b>", { status: "failed", error_msg: "네트워크 오류" }), item(3, "다", { status: "failed", error_msg: "" })], page: 1, total: 3, page_size: 20 };
  let retention = { retention_days: 30 }, failLoad = null, failSave = null;
  const routes = {
    "/api/episode-history": (o, url) => { if (o.method === "DELETE") return {}; if (failLoad) throw new Error(failLoad); return { ...data, page: Number(new URL(url, "http://x").searchParams.get("page")) }; },
    "/api/episode-history/retention-days": (o) => { if (o.method === "POST") { if (failSave) throw new Error(failSave); return {}; } return retention; },
  };
  let confirmAnswer = true;
  const { w, run, calls, wait } = makeApp(routes, { timeScale: 5 });
  w.confirm = () => confirmAnswer;
  await idle();
  const $ = (id) => w.document.getElementById(id);
  const list = () => [...$("episode-history-list").querySelectorAll(".job-history-entry")];
  const historyCalls = () => calls.filter((c) => c.url.startsWith("/api/episode-history?")).map((c) => new URL(c.url, "http://x").searchParams);
  const load = async (page = 1) => { run(`window.__p = loadEpisodeHistory(${page});`); await w.__p; await idle(); };

  // Scenario 1: 목록 — 작품 이름/회차+부제+시각/상태 배지(성공, 실패는 사유 포함). 이름은 HTML로 해석되지 않는다. 항목이 있으면 빈 안내 숨김
  await load();
  ok(list().length === 3 && $("episode-history-empty").classList.contains("hidden"), "3개 항목, 빈 안내 숨김");
  const first = list()[0];
  ok(first.querySelector(".job-history-name").textContent === "가" && /1화 부제1 · /.test(first.querySelector(".job-history-time").textContent), "작품 이름과 회차/부제/시각");
  ok(first.querySelector(".badge").textContent === "성공" && first.querySelector(".badge").classList.contains("job-success"), "성공 배지");
  ok(list()[1].querySelector(".badge").textContent === "실패 (네트워크 오류)" && list()[2].querySelector(".badge").textContent === "실패", "실패 배지(사유가 있으면 함께)");
  ok(list()[1].querySelector(".job-history-name").textContent === "<b>나</b>" && !list()[1].querySelector(".job-history-name b"), "이름의 HTML은 글자로만 보인다");
  data = { ...data, items: [] }; await load(); ok(list().length === 0 && !$("episode-history-empty").classList.contains("hidden"), "항목이 없으면 빈 안내");
  data = { ...data, items: [item(1, "가"), item(2, "나"), item(3, "다")] };

  // Scenario 2: 쪽 넘기기 — 한 쪽이면 없음, 여러 쪽이면 "현재 / 전체"와 이전/다음(첫 쪽엔 이전 없음, 끝 쪽엔 다음 없음). 누르면 그 쪽을 요청
  const pager = () => [...$("episode-history-pagination").children].map((e) => e.textContent.trim());
  await load(); ok(pager().length === 0, "한 쪽이면 쪽 넘기기 없음");
  data = { ...data, total: 45 };
  await load(1); ok(JSON.stringify(pager()) === JSON.stringify(["1 / 3", "다음"]), "첫 쪽: " + pager());
  await load(2); ok(JSON.stringify(pager()) === JSON.stringify(["이전", "2 / 3", "다음"]), "가운데 쪽: " + pager());
  await load(3); ok(JSON.stringify(pager()) === JSON.stringify(["이전", "3 / 3"]), "끝 쪽: " + pager());
  calls.length = 0; [...$("episode-history-pagination").querySelectorAll("button")].find((b) => b.textContent === "이전").click(); await idle();
  ok(historyCalls().length === 1 && historyCalls()[0].get("page") === "2", "이전을 누르면 2쪽 요청");
  data = { ...data, total: 3 };

  // Scenario 3: 필터 — 상태를 바꾸면 status와 함께 1쪽부터, 검색어는 앞뒤 공백을 뗀 search로. 둘 다 없으면 파라미터를 안 붙인다
  calls.length = 0; $("episode-history-status").value = "failed"; $("episode-history-status").dispatchEvent(new w.Event("change")); await idle();
  ok(historyCalls().length === 1 && historyCalls()[0].get("status") === "failed" && historyCalls()[0].get("page") === "1" && !historyCalls()[0].has("search"), "상태 필터");
  calls.length = 0; $("episode-history-search").value = "  쌍갑  "; $("episode-history-search").dispatchEvent(new w.Event("input")); await wait(300); await idle();
  ok(historyCalls().length === 1 && historyCalls()[0].get("search") === "쌍갑" && historyCalls()[0].get("status") === "failed", "검색어(공백 제거)와 상태가 함께");
  $("episode-history-status").value = ""; $("episode-history-search").value = ""; calls.length = 0; await load(); ok(!historyCalls()[0].has("status") && !historyCalls()[0].has("search"), "필터가 없으면 파라미터 없음");

  // Scenario 4: 검색 입력은 글자마다 요청하지 않는다 — 멈춘 뒤 한 번만
  calls.length = 0; for (const text of ["쌍", "쌍갑", "쌍갑포"]) { $("episode-history-search").value = text; $("episode-history-search").dispatchEvent(new w.Event("input")); }
  await wait(50); ok(historyCalls().length === 0, "입력하는 동안에는 요청하지 않는다: " + historyCalls().length);
  await wait(400); await idle(); ok(historyCalls().length === 1 && historyCalls()[0].get("search") === "쌍갑포", "멈추면 마지막 검색어로 한 번만: " + historyCalls().length);
  $("episode-history-search").value = "";

  // Scenario 5: 개별 삭제 — DELETE /api/episode-history/{id} 뒤 같은 쪽을 다시 불러온다
  await load(2); calls.length = 0; list()[1].querySelector(".job-history-delete-btn").click(); await idle();
  ok(calls[0].method === "DELETE" && calls[0].url === "/api/episode-history/2" && historyCalls().length === 1 && historyCalls()[0].get("page") === "2", "삭제 후 같은 쪽 다시 불러옴: " + calls.map((c) => c.method + " " + c.url));
  // 전체 삭제 — 확인에서 취소하면 아무것도 안 하고, 확인하면 지운 뒤 1쪽부터
  calls.length = 0; confirmAnswer = false; $("btn-clear-episode-history").click(); await idle(); ok(calls.length === 0, "취소하면 요청 없음");
  confirmAnswer = true; $("btn-clear-episode-history").click(); await idle();
  ok(calls[0].method === "DELETE" && calls[0].url === "/api/episode-history" && historyCalls()[0].get("page") === "1", "확인하면 전체 삭제 후 1쪽");

  // Scenario 6: 보관 기간 — 저장된 값을 보여 주고(없거나 0이면 빈칸), 저장하면 일수를 보내 "저장했습니다."(0이면 "자동삭제 껐습니다."), 오류는 그대로 표시
  await load(); ok($("episode-history-retention-days").value === "30", "저장된 보관 기간 표시");
  retention = { retention_days: 0 }; await load(); ok($("episode-history-retention-days").value === "", "0이면 빈칸");
  calls.length = 0; $("episode-history-retention-days").value = "45"; $("btn-save-retention-days").click(); await idle();
  const post = calls.find((c) => c.method === "POST"); ok(JSON.parse(post.body).retention_days === 45 && $("retention-save-result").textContent === "저장했습니다.", "일수 저장");
  $("episode-history-retention-days").value = "0"; $("btn-save-retention-days").click(); await idle(); ok($("retention-save-result").textContent === "자동삭제 껐습니다.", "0이면 끔");
  $("episode-history-retention-days").value = "abc"; $("btn-save-retention-days").click(); await idle(); ok(JSON.parse(calls.filter((c) => c.method === "POST").pop().body).retention_days === 0, "숫자가 아니면 0(끔)");
  failSave = "저장 실패"; $("btn-save-retention-days").click(); await idle(); ok($("retention-save-result").textContent === "저장 실패", "저장 오류 표시");

  // Scenario 7: 목록 요청이 실패하면 빈 안내 자리에 오류 문구를 보여 준다
  failLoad = "서버 오류"; await load(); ok(!$("episode-history-empty").classList.contains("hidden") && $("episode-history-empty").textContent === "서버 오류" && list().length === 0, "오류 문구 표시");
  done();
})();
