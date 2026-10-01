// 카카오 작가 화면: 원작자는 "(원작)"으로 표시(선택됨/사용 가능 모두), 여러 명이 묶여 오지 않는다.
const { makeApp, sleep, ok, done } = require("./harness");
(async () => {
  const routes = {
    "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" },
    "/api/kakao/watched-authors": [
      { author_id: "연상호", author_name: "연상호", enabled: true, is_origin: true },
      { author_id: "배혜수", author_name: "배혜수", enabled: true, is_origin: false },
      { author_id: "민홍남", author_name: "민홍남", enabled: false, is_origin: true },
    ],
    "/api/kakao/origin-authors": ["민홍남", "연상호", "황은영"],
    "/api/kakao/authors/candidates": ["황은영", "오오히라 요우", "배혜수"],
  };
  const { w, run } = makeApp(routes); await sleep(60); run("kakaoWebtoonsEnabled = true;");
  run("window.__p = loadKakaoAuthorList();"); await w.__p; await sleep(30);
  const chips = (id) => Array.from(w.document.querySelectorAll(`#${id} .chip`)).map((c) => c.textContent.replace(/\s*[×✕+xX]\s*$/, "").trim());
  const selected = chips("kakao-author-registered-chips"), all = chips("kakao-author-all-chips");
  ok(JSON.stringify(selected) === JSON.stringify(["연상호 (원작)", "배혜수"]), "선택됨: 원작자에만 '(원작)': " + JSON.stringify(selected));
  ok(all.includes("민홍남 (원작)") && all.includes("황은영 (원작)") && all.includes("오오히라 요우") && !all.includes("배혜수"), "사용 가능: 꺼 둔 원작자/후보 중 원작자에 '(원작)', 이미 선택된 것은 빠짐: " + JSON.stringify(all));
  ok(!all.some((l) => l.includes(",")), "쉼표로 묶인 칩이 없음");
  done();
})();
