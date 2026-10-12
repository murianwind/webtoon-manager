// 카카오 대여권 UI — 수동 다운로드 표(대여권 상태, 선택, 확인 창, 요청)와 설정(자동 사용 체크박스, 기본 꺼짐)
const { makeApp, sleep, ok, done } = require("./harness");
(async () => {
  const ep = (number, state, extra = {}) => ({ number, subtitle: `${number}화`, state, expire: null, free_at: null, downloaded: false, before_start: false, selectable: state !== "locked", ...extra });
  const analysis = {
    series_id: 68546405, title: "블루엔젤", folder: "/dl/블루엔젤", mode: "compare", existing_count: 1, marker: null, to_download_count: 0, locked_count: 4, downloaded_count: 1, before_start_count: 0,
    cookie_saved: true, logged_in: true, thumbnail_url: "t", authors: "a", subscription: "active", site_total: 5, listed_count: 5, hidden_count: 0, excluded_video_count: 0,
    tickets: { rental_count: 2, own_count: 0, waitfree_supported: true, waitfree_ready: false, waitfree_available_at: null, waitfree_period_minutes: 1440 },
    episodes: [ep(1, "free", { downloaded: true }), ep(2, "rental"), ep(3, "rental"), ep(4, "rental"), ep(5, "locked")],
  };
  const runs = []; let autoSetting = true; const settingPosts = [];
  const routes = {
    "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" },
    "/api/kakao-manual/analyze": analysis, "/api/kakao-manual/run": (opts) => { runs.push(JSON.parse(opts.body)); return { status: "started" }; },
    "/api/settings/kakao-rental-ticket-auto-use": (opts) => { if (opts && opts.method === "POST") { settingPosts.push(JSON.parse(opts.body)); autoSetting = JSON.parse(opts.body).enabled; } return { enabled: autoSetting }; },
  };
  const { w, run } = makeApp(routes); await sleep(60); run("kakaoWebtoonsEnabled = true;");
  const $ = (id) => w.document.getElementById(id);
  const confirms = []; w.confirm = (m) => { confirms.push(m); return w.__confirm === undefined ? true : w.__confirm; };
  run(`switchToTab("manual-download");`); $("manual-platform").value = "kakao"; $("manual-platform").dispatchEvent(new w.Event("change"));
  $("manual-query").value = "68546405"; $("btn-manual-analyze").click(); await sleep(50);
  const boxes = () => [...w.document.querySelectorAll(".kakao-manual-ep-checkbox")];
  const box = (n) => boxes().find((b) => b.dataset.no === String(n));
  const checkedNos = () => boxes().filter((b) => b.checked).map((b) => Number(b.dataset.no));

  // Scenario 1: 대여권 상태 회차는 "대여권" 배지로 보이고 선택할 수 있다(기다무로 못 여는 잠긴 회차). 잠긴 회차(5번)는 선택 불가
  const rows = [...w.document.querySelectorAll("#kakao-manual-tbody tr")];
  ok(rows[1].children[3].textContent.trim() === "대여권" && rows[1].querySelector(".kp-rental") !== null, "2번 회차에 '대여권' 배지");
  ok(!box(2).disabled && box(5).disabled, "대여권 회차는 선택 가능, 잠긴 회차는 선택 불가");
  ok(box(2).dataset.rental === "1" && box(1).dataset.rental !== "1", "대여권 회차에만 dataset.rental");

  // Scenario 2: 선택 버튼은 보유 대여권 수(2장)만큼만 번호 앞선 대여권 회차를 고른다 — 나머지는 열 수 없어 골라 봐야 건너뜀이 되기 때문
  $("btn-kakao-manual-select-all").click();
  ok(JSON.stringify(checkedNos()) === JSON.stringify([1, 2, 3]), "전체선택: 이미 받은 1번(다시 받기) + 대여권 2장만큼(2,3번): " + JSON.stringify(checkedNos()));
  $("btn-kakao-manual-select-missing").click();
  ok(JSON.stringify(checkedNos()) === JSON.stringify([2, 3]), "미보유만 선택: 대여권 2장만큼(2,3번): " + JSON.stringify(checkedNos()));

  // Scenario 3: 선택 다운로드 — 확인 창에 장수/남는 장수/되돌릴 수 없음 안내, 취소하면 요청이 나가지 않는다
  w.__confirm = false; $("btn-kakao-manual-download").click(); await sleep(30);
  const msg = confirms[confirms.length - 1] || "";
  ok(/대여권 2장을 사용/.test(msg) && /남은 대여권 0장|0장이 남/.test(msg) && /되돌릴 수 없/.test(msg) && /3일/.test(msg), "확인 창 안내: " + msg.replace(/\n/g, " / "));
  ok(runs.length === 0, "확인 창에서 취소하면 요청이 나가지 않는다");

  // Scenario 4: 확인하면 use_rental_tickets=true와 고른 회차가 서버로 간다
  w.__confirm = true; $("btn-kakao-manual-download").click(); await sleep(30);
  ok(runs.length === 1 && runs[0].use_rental_tickets === true && JSON.stringify(runs[0].numbers) === JSON.stringify([2, 3]), "요청: " + JSON.stringify(runs[0]));

  // Scenario 5: 보유 수(2장)보다 많이 직접 골랐으면 앞 번호부터 보유 수만 열린다고 알려 준다
  box(4).checked = true; confirms.length = 0; w.__confirm = false; $("btn-kakao-manual-download").click(); await sleep(30);
  ok(/선택한 3개 중/.test(confirms[confirms.length - 1] || "") && /2개만/.test(confirms[confirms.length - 1] || ""), "초과 선택 안내: " + (confirms[confirms.length - 1] || "").replace(/\n/g, " / "));

  // Scenario 6: 대여권 회차를 하나도 안 골랐으면 확인 창도 없고 use_rental_tickets=false
  $("btn-kakao-manual-select-none").click(); runs.length = 0; confirms.length = 0;
  w.__confirm = true; box(1).disabled = false; box(1).checked = true; $("btn-kakao-manual-download").click(); await sleep(30);
  ok(!confirms.some((m) => /대여권/.test(m)), "대여권 안내 창이 뜨지 않는다");
  ok(runs.length === 1 && runs[0].use_rental_tickets === false, "use_rental_tickets=false: " + JSON.stringify(runs[0]));

  // Scenario 7: 설정 — 자동 사용 체크박스는 저장된 값을 보여 주고, 저장하면 서버로 간다(끄기)
  run(`switchToTab("settings");`); await sleep(60);
  const toggle = $("kakao-rental-auto-toggle");
  ok(!!toggle && toggle.checked === true, "설정을 열면 저장된 값(켜짐)이 체크박스에 나온다");
  toggle.checked = false; $("btn-save-kakao-rental-auto").click(); await sleep(30);
  ok(settingPosts.length === 1 && settingPosts[0].enabled === false, "저장하면 서버로 간다: " + JSON.stringify(settingPosts));
  ok(/저장/.test($("kakao-rental-auto-save-result").textContent), "저장 결과 문구");
  ok(/기다무로 못 여는/.test(w.document.getElementById("settings-card-kakao").textContent) && /되돌릴 수 없/.test($("kakao-rental-auto-group").textContent), "설정 안내문에 대상(기다무로 못 여는 회차)과 되돌릴 수 없다는 주의가 있다");
  done();
})();
