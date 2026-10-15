// 아카이빙 탭 여백/색 — 실제 화면에서 겹치거나 붙어 보였던 곳을 지킨다.
// 원인: 전역 .hint는 "제목(h2) 바로 밑에 끼워 넣는" 용도라 위쪽 여백이 음수(-8px)다. 같은 규칙을 h3 밑이나 버튼 밑의 설명에 그대로 쓰면 제목/버튼과 겹친다.
const fs = require("fs");
const path = require("path");
const { makeApp, sleep, ok, done } = require("./harness");
const css = fs.readFileSync(path.join(__dirname, "..", "..", "app", "static", "style.css"), "utf8");
const rule = (selector) => {
  const m = css.match(new RegExp("(?:^|\\})\\s*" + selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\s*\\{([^}]*)\\}", "m"));
  return m ? m[1] : null;
};
const px = (decls, prop) => { const m = decls && decls.match(new RegExp("(?:^|;|\\s)" + prop + ":\\s*([^;]+)")); return m ? m[1].trim() : null; };
const firstMarginTop = (decls) => { const m = px(decls, "margin"); if (m) return parseFloat(m.split(/\s+/)[0]); const t = px(decls, "margin-top"); return t ? parseFloat(t) : null; };
const marginBottom = (decls) => { const b = px(decls, "margin-bottom"); if (b) return parseFloat(b); const m = px(decls, "margin"); if (!m) return null; const p = m.split(/\s+/); return parseFloat(p.length === 1 ? p[0] : p.length === 2 ? p[0] : p[2]); };

(async () => {
  const { w } = makeApp({ "/api/settings/kakao-webtoons-enabled": { enabled: true }, "/api/settings/webtoon-server": { webtoon_server_url: "" } }); await sleep(40);
  const common = [...w.document.querySelectorAll("#archive-main-content > .settings-card")].find((c) => c.querySelector("h2").textContent.trim() === "아카이빙 공통 설정");
  // Scenario 1: 공통 설정 안 제목(h3)과 설명 사이에 여백이 있다 — 설명의 위쪽 여백이 음수가 아니고, 제목 아래 여백이 있다
  const hint = rule(".archive-common-section .hint");
  ok(hint && firstMarginTop(hint) >= 0, "공통 설정 안의 설명은 위쪽 여백이 음수가 아니다: " + hint);
  ok(marginBottom(rule(".archive-common-section h3")) >= 6, "h3 아래 여백 6px 이상");
  // Scenario 2: 저장 버튼과 밑의 설명이 겹치지 않는다 — 버튼과 결과 문구를 한 줄(.archive-save-row)로 묶고 그 아래 설명에 위쪽 여백
  const saveBtn = w.document.getElementById("btn-save-archive-conflict");
  ok(saveBtn.parentElement.classList.contains("archive-save-row") && saveBtn.parentElement.contains(w.document.getElementById("archive-conflict-save-result")), "저장 버튼과 결과 문구가 한 줄로 묶임");
  ok(saveBtn.parentElement.nextElementSibling.classList.contains("hint") && firstMarginTop(rule(".archive-save-row + .hint")) >= 8, "저장 줄 바로 밑 설명의 위쪽 여백 8px 이상");
  // Scenario 3: 폴더 선택기 — 즐겨찾기 줄과 현재 위치 줄 사이, 현재 위치 줄과 폴더 목록 사이에 여백
  ok(marginBottom(rule(".folder-favorites-row")) >= 12, "즐겨찾기 줄 아래 여백 12px 이상: " + rule(".folder-favorites-row"));
  ok(marginBottom(rule(".folder-picker-path-row")) >= 6, "현재 위치 줄 아래 여백 6px 이상: " + rule(".folder-picker-path-row"));
  // Scenario 4: 즐겨찾기 버튼은 다른 흰 버튼과 구별되는 색(연한 색 배경)이 있다 — 배경이 카드색(흰색)이 아니다. 별 버튼도 연한 색
  const chipBg = px(rule(".folder-favorite-chip"), "background");
  ok(chipBg && !/var\(--card\)|#fff\b|#ffffff|white/i.test(chipBg), "즐겨찾기 버튼 배경색: " + chipBg);
  const starBg = px(rule(".folder-favorite-star"), "background");
  ok(starBg && !/var\(--card\)|#fff\b|#ffffff|white/i.test(starBg), "별 버튼 배경색: " + starBg);
  ok(!!rule(".folder-favorite-chip-active") && /border-color/.test(rule(".folder-favorite-chip-active")), "지금 위치의 즐겨찾기는 강조");
  done();
})();
