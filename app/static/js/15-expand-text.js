// 잘려서(…) 안 보이는 글자를 눌러서 펼친다 — 눌러서 펼쳐지는 항목이 아닌 목록(다운로드 이력의 "회차 부제 · 시각", 아카이빙 이력의 "파일 이름 · 시각", 아카이빙 대상의 "폴더 → 보관 위치",
// 카카오 분석 표의 부제)이 좁은 화면에서 "…"로 잘려 내용을 읽을 수 없던 것을, 눌러서 그 자리에서 전체 내용(여러 줄로 줄바꿈)으로 본다. 다시 누르면 접힌다.
// 실제로 잘렸을 때만 동작하고(잘리지 않았으면 아무 일도 없다), 글자를 드래그해 복사하는 중이면 건드리지 않으며, 눌러서 펼쳐지는 항목(실행 이력: 항목 안에 로그가 있다)은 원래 동작대로 둔다.
// 이벤트는 문서 한 곳에서 받아서(위임) 목록을 다시 그려도 항목마다 다시 달 필요가 없다. 펼친 모양은 style.css의 .text-expanded.
const EXPANDABLE_TEXT_SELECTOR = ".job-history-summary .job-history-time, #kakao-manual-table td:nth-child(3)";

document.addEventListener("click", (event) => {
  const cell = event.target.closest(EXPANDABLE_TEXT_SELECTOR);
  if (!cell) return;
  const entry = cell.closest(".job-history-entry");
  if (entry && entry.querySelector(".job-log")) return; // 눌러서 로그가 펼쳐지는 항목은 그대로
  if (String(window.getSelection()).length > 0) return; // 복사하려고 글자를 고르는 중
  if (cell.classList.contains("text-expanded")) cell.classList.remove("text-expanded");
  else if (cell.scrollWidth > cell.clientWidth + 1) cell.classList.add("text-expanded");
});
