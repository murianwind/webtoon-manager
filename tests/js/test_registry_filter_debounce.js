// 작가/태그 후보 검색 입력(60-registry.js) — 글자마다 목록을 다시 그리지 않고, 입력이 멈춘 뒤 한 번만 그린다(후보가 많으면 입력마다 다시 그리는 게 멈칫거림의 원인이 된다).
// 전체목록/구독해제/제외됨/다운로드 이력 검색과 같은 규칙(250ms).
const { makeApp, idle, ok, done } = require("./harness");
(async () => {
  const { w, run, wait } = makeApp({}, { timeScale: 5 });
  await idle();
  const typeInto = (id, ...texts) => { for (const text of texts) { w.document.getElementById(id).value = text; w.document.getElementById(id).dispatchEvent(new w.Event("input")); } };
  const spy = (fn) => run(`window.__${fn}Calls = 0; (function () { const real = ${fn}; ${fn} = function () { window.__${fn}Calls++; return real.apply(this, arguments); }; })();`);

  for (const [inputId, fn] of [["author-candidate-filter", "renderAllAuthors"], ["tag-catalog-search", "renderAllTags"], ["kakao-author-candidate-filter", "renderKakaoAllAuthors"]]) {
    spy(fn);
    typeInto(inputId, "ㅅ", "쌍", "쌍갑", "쌍갑포");
    await wait(50); ok(w[`__${fn}Calls`] === 0, `${inputId}: 입력하는 동안에는 다시 그리지 않는다: ${w[`__${fn}Calls`]}`);
    await wait(400); ok(w[`__${fn}Calls`] === 1, `${inputId}: 멈추면 한 번만 그린다: ${w[`__${fn}Calls`]}`);
    typeInto(inputId, "다시"); await wait(400); ok(w[`__${fn}Calls`] === 2, `${inputId}: 다음 입력도 멈춘 뒤 한 번씩`);
  }
  done();
})();
