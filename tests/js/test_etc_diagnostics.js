// "기타" 탭 원인 안내 — 기타를 볼 때 그 작품들이 왜 요일을 모르는지(완결/서비스 종료/휴재/확인 대기/확인 실패 …)를 원인별 개수로 보여 준다.
// 서버(/api/diagnostics/weekday-gaps)가 센 결과를 그대로 보여 주고, 기타가 아닌 탭에서는 요청도 안 하고 숨긴다. 목록을 다시 불러오면 새로 센다.
const { makeApp, idle, ok, done } = require("./harness");
const { listItem } = require("./factories");
(async () => {
  const gaps = {
    status: "excluded",
    naver: { total: 6, reasons: { finished: 1, no_weekday_on_page: 1, check_failed: 2, pending: 2 }, failed_http: { "403": 1, network: 1 }, samples: { pending: ["대기작A", "대기작B"], check_failed: ["실패작"], finished: ["완결작"], no_weekday_on_page: ["요일없음작"] } },
    kakao: { total: 4, reasons: { ended: 1, sale_stopped: 1, hiatus: 1, serving_not_listed: 1 }, failed_http: {}, samples: { ended: ["종료작"], sale_stopped: ["중지작"], hiatus: ["휴재작"], serving_not_listed: ["연재작"] } },
  };
  let gapData = gaps;
  const excludedRows = [listItem("naver", 1, "기타작품", [], { status: "excluded" }), listItem("naver", 2, "화요일작품", ["tue"], { status: "excluded" })];
  const { w, run, calls } = makeApp({
    "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" },
    "/api/naver-list": [], "/api/kakao-list": { items: [], version: "v", refreshing: false },
    "/api/webtoons": () => excludedRows, "/api/kakao-webtoons": () => [],
    "/api/diagnostics/weekday-gaps": () => (typeof gapData === "function" ? gapData() : gapData),
  }, { now: "2026-11-03T03:00:00Z" });
  await idle(); run("kakaoWebtoonsEnabled = true;");
  const $ = (id) => w.document.getElementById(id);
  const gapCalls = () => calls.filter((c) => c.url.startsWith("/api/diagnostics/weekday-gaps")).map((c) => c.url);
  const tab = (label) => [...$("excluded-weekday-tabs").querySelectorAll(".weekday-tab")].find((t) => t.firstChild.textContent.trim() === label);

  // Scenario 1: 설명 문장 만들기(순수 함수) — 플랫폼별 개수와 원인별 개수, 확인 실패는 HTTP 상태까지. 없으면 빈 문자열
  const text = w.eval("describeEtcDiagnostics")(gaps);
  ok(text.includes("네이버 6개") && text.includes("완결 1") && text.includes("작품 페이지에 요일 없음 1") && text.includes("요일 확인 대기 2"), "네이버 원인: " + text);
  ok(text.includes("확인 실패 2(HTTP 403 1, 네트워크 오류 1)"), "확인 실패는 상태별: " + text);
  ok(text.includes("카카오 4개") && text.includes("서비스 종료 1") && text.includes("판매 중지 1") && text.includes("휴재 1") && text.includes("연재 중이지만 요일 목록에 없음 1"), "카카오 원인: " + text);
  ok(w.eval("describeEtcDiagnostics")({ status: "excluded", naver: { total: 0, reasons: {}, failed_http: {}, samples: {} }, kakao: { total: 0, reasons: {}, failed_http: {}, samples: {} } }) === "", "원인이 없으면 빈 문자열");
  const onlyKakao = w.eval("describeEtcDiagnostics")({ status: null, naver: { total: 0, reasons: {}, failed_http: {}, samples: {} }, kakao: gaps.kakao });
  ok(!onlyKakao.includes("네이버") && onlyKakao.includes("카카오 4개"), "개수가 0인 플랫폼은 빼고 보여 줌");

  // Scenario 2: 제외됨에서 기타 탭을 보면 원인을 요청해서 보여 주고(해당 상태만), 기타가 아닌 탭에서는 요청 없이 숨긴다
  run("subscriptionLoadedAt.excluded = 0; window.__p = loadSubscriptionTab('excluded');"); await w.__p; await idle();
  ok(gapCalls().length === 0 && $("excluded-etc-note").classList.contains("hidden"), "기타가 아니면(오늘 탭) 요청도 안 하고 숨김: " + gapCalls());
  tab("기타").click(); await idle();
  ok(gapCalls().length === 1 && gapCalls()[0] === "/api/diagnostics/weekday-gaps?status=excluded", "기타를 보면 제외됨 상태로 요청: " + gapCalls());
  ok(!$("excluded-etc-note").classList.contains("hidden") && $("excluded-etc-note").textContent.includes("네이버 6개"), "원인 문장 표시: " + $("excluded-etc-note").textContent);
  ok($("excluded-etc-note").title.includes("대기작A") && $("excluded-etc-note").title.includes("종료작"), "마우스를 올리면 원인별 작품 예시: " + $("excluded-etc-note").title);

  // Scenario 3: 기타를 계속 보는 동안(검색 등으로 다시 그려도) 다시 요청하지 않는다. 다른 탭으로 가면 숨기고, 목록을 다시 불러오면 새로 센다
  run("renderSubscriptionTab('excluded');"); await idle(); ok(gapCalls().length === 1, "다시 그려도 재요청 없음");
  tab("화").click(); await idle(); ok($("excluded-etc-note").classList.contains("hidden") && gapCalls().length === 1, "다른 탭으로 가면 숨김");
  tab("기타").click(); await idle(); ok(gapCalls().length === 1 && !$("excluded-etc-note").classList.contains("hidden"), "다시 기타로 돌아와도 같은 결과를 씀(재요청 없음)");
  gapData = { ...gaps, naver: { ...gaps.naver, total: 1, reasons: { pending: 1 }, failed_http: {} } };
  run("subscriptionLoadedAt.excluded = 0; window.__p = loadSubscriptionTab('excluded');"); await w.__p; await idle();
  ok(gapCalls().length === 2 && $("excluded-etc-note").textContent.includes("네이버 1개") && !$("excluded-etc-note").textContent.includes("완결 1"), "목록을 다시 불러오면 새로 센다: " + $("excluded-etc-note").textContent);

  // Scenario 4: 설명할 원인이 없거나(빈 결과) 요청이 실패하면 안내를 숨긴다(목록 표시에는 영향 없음)
  gapData = { status: "excluded", naver: { total: 0, reasons: {}, failed_http: {}, samples: {} }, kakao: { total: 0, reasons: {}, failed_http: {}, samples: {} } };
  run("subscriptionLoadedAt.excluded = 0; window.__p = loadSubscriptionTab('excluded');"); await w.__p; await idle(); ok($("excluded-etc-note").classList.contains("hidden"), "원인이 없으면 숨김");
  // 요청이 실패하면 이전에 보이던 안내도 숨기고(낡은 원인을 보여 주지 않는다), 실패는 기억하지 않아 다음에 다시 요청한다. 목록 표시는 그대로
  gapData = gaps; run("subscriptionLoadedAt.excluded = 0; window.__p = loadSubscriptionTab('excluded');"); await w.__p; await idle();
  ok(!$("excluded-etc-note").classList.contains("hidden"), "(준비) 안내가 보이는 상태");
  gapData = () => { throw new Error("서버 오류"); };
  const callsBeforeFail = gapCalls().length;
  run("subscriptionLoadedAt.excluded = 0; window.__p = loadSubscriptionTab('excluded');"); await w.__p; await idle();
  ok(gapCalls().length === callsBeforeFail + 1 && $("excluded-etc-note").classList.contains("hidden"), "요청이 실패하면 안내를 숨긴다");
  ok($("excluded-list").querySelectorAll(".webtoon-card").length > 0, "목록 표시는 그대로");
  gapData = gaps; run("renderSubscriptionTab('excluded');"); await idle();                                                          // 목록을 다시 받지 않고 그리기만 다시 해도(캐시가 비워지지 않는 경로)
  ok(gapCalls().length === callsBeforeFail + 2 && !$("excluded-etc-note").classList.contains("hidden"), "실패는 기억하지 않아 다음에 다시 요청하고 표시: " + (gapCalls().length - callsBeforeFail));
  // Scenario 5: 전체목록에서도 같다 — 기타 탭에서만, 상태를 정하지 않고(모든 상태) 요청한다
  gapData = gaps; w.__list = [listItem("naver", 1, "목록 기타", [], { status: "active" }), listItem("naver", 2, "목록 화요일", ["tue"])];
  run("naverListNaverItems = __list; naverListKakaoItems = []; combineNaverListCache(); renderNaverList();"); await idle();
  const before = gapCalls().length; ok($("naver-list-etc-note").classList.contains("hidden"), "전체목록: 기타가 아니면 숨김");
  [...$("naver-list-weekday-tabs").querySelectorAll(".weekday-tab")].find((t) => t.firstChild.textContent.trim() === "기타").click(); await idle();
  ok(gapCalls().length === before + 1 && gapCalls().pop() === "/api/diagnostics/weekday-gaps" && !$("naver-list-etc-note").classList.contains("hidden") && $("naver-list-etc-note").textContent.includes("기타 원인"), "전체목록 기타: 상태 없이 요청하고 표시: " + gapCalls().slice(-2));
  done();
})();
