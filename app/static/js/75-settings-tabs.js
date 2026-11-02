// 카드 안의 탭 — "다운로드 · 실행 스케줄" 카드처럼 항목을 탭으로 나눠 보여 준다.
// 마크업: .card-tabs 안의 .card-tab[data-tab] 버튼과, 같은 카드 안의 .card-tab-panel[data-panel] 패널이 이름으로 짝을 이룬다.
// 탭에 data-footer="이름"이 있으면, 그 탭이 선택됐을 때만 같은 카드의 .card-tab-footer[data-footer="이름"](여러 탭이 같이 쓰는 저장 줄)이 보인다.
// 숨겨진 패널도 DOM에는 그대로 있어서(화면에서만 가림) 설정 불러오기/저장이 탭과 상관없이 동작한다.
// (index.html에서 파일 이름 순서대로 로드된다 — 클래식 스크립트라 최상위 선언은 전역으로 공유된다)

function activateCardTab(card, name) {
  let footerName = null;
  card.querySelectorAll(".card-tabs .card-tab").forEach((tab) => {
    const selected = tab.dataset.tab === name;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", String(selected));
    if (selected) footerName = tab.dataset.footer || null;
  });
  card.querySelectorAll(".card-tab-panel").forEach((panel) => panel.classList.toggle("hidden", panel.dataset.panel !== name));
  card.querySelectorAll(".card-tab-footer").forEach((footer) => footer.classList.toggle("hidden", footer.dataset.footer !== footerName));
}

document.querySelectorAll(".card-tabs").forEach((bar) => {
  bar.addEventListener("click", (ev) => {
    const tab = ev.target.closest(".card-tab");
    if (tab) activateCardTab(bar.closest(".settings-card"), tab.dataset.tab);
  });
});
