const { makeApp, sleep, ok, done } = require("./harness");
(async () => {
  let targets = [{ title_id: "777", title_name: "네이버작품", platform: "naver", dest_base_path: "보관", dest_type: "local", enabled: true, source_type: "webtoon", source_dest_type: "local", source_path: "", filename_template_preset_id: null, filename_template_preset_name: null },
                 { title_id: "kakao_61641075", title_name: "도굴왕: 엔드라인", platform: "kakao", dest_base_path: "보관", dest_type: "local", enabled: true, source_type: "webtoon", source_dest_type: "local", source_path: "", filename_template_preset_id: null, filename_template_preset_name: null }];
  const routes = {
    "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" },
    "/api/webtoons": () => [{ title_id: "777", title: "네이버작품" }, { title_id: "888", title: "다른네이버" }],
    "/api/kakao-webtoons": () => [{ title_id: 61641075, title: "도굴왕: 엔드라인" }, { title_id: 70601591, title: "뱀 가문의 막내딸입니다" }],
    "/api/archive/targets": () => targets, "/api/archive/settings": { local_available: true, rclone_available: false }, "/api/archive/presets": [],
  };
  const { w, run } = makeApp(routes); await sleep(60); run("kakaoWebtoonsEnabled = true;");
  const sel = w.document.getElementById("archive-target-webtoon-select");
  run("window.__p = loadArchiveTargetWebtoonOptions();"); await w.__p;
  const opts = Array.from(sel.options).map((o) => [o.value, o.textContent]);
  ok(JSON.stringify(opts) === JSON.stringify([["888", "[네이버] 다른네이버"], ["kakao_70601591", "[카카오] 뱀 가문의 막내딸입니다"]]), "등록 후보에 네이버와 카카오(구독 중)가 함께 나오고, 이미 등록된 것(777, kakao_61641075)은 빠짐: " + JSON.stringify(opts));
  run("kakaoWebtoonsEnabled = false;"); run("window.__p = loadArchiveTargetWebtoonOptions();"); await w.__p;
  ok(JSON.stringify(Array.from(sel.options).map((o) => o.textContent)) === '["다른네이버"]', "카카오웹툰 관리를 끄면 예전처럼 네이버만(접두사 없이)");
  run("kakaoWebtoonsEnabled = true;");
  run("window.__p = loadArchiveTargetList();"); await w.__p; await sleep(30);
  const names = Array.from(w.document.querySelectorAll("#archive-target-list .job-history-name")).map((e) => e.textContent);
  ok(JSON.stringify(names) === JSON.stringify(["네이버작품", "[카카오] 도굴왕: 엔드라인"]), "등록된 대상 목록에 카카오는 '[카카오]'가 붙음: " + JSON.stringify(names));
  ok(run("window.__l = archiveTargetLabel({platform:'kakao', title_name:'가'}) + '|' + archiveTargetLabel({platform:'naver', title_name:'나'});") === undefined && w.__l === "[카카오] 가|나", "표시 이름 규칙");
  done();
})();
