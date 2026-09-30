// 웹툰 카드 빌더와 변경분만 갱신하는 목록 그리기(reconcileGrid)
// (index.html에서 파일 이름 순서대로 로드된다 — 클래식 스크립트라 최상위 선언은 전역으로 공유된다)
// ── 공용 카드 빌더 ───────────────────────────────────────

function kakaoThumbnailImgTag(titleId, cardImageUrl) {
  // 카카오페이지 작품 페이지에 나오는 공식 표지를 서버가 받아 저장해둔 것(/api/kakao-thumbnail)을
  // 먼저 보여준다 — 요일 목록 카드의 이미지는 그 작품의 공식 표지와 다른 그림이라서. 그걸 못 받았거나
  // (옛 기록 등 카카오페이지에 없는 작품) 실패하면 목록 카드 이미지로, 그것도 안 되면 자리표시자로
  // 넘어간다. Referer를 안 보내서(no-referrer) 카드 이미지가 다른 사이트에서의 직접 링크 제한에
  // 걸리는 일도 막는다.
  const placeholder = "this.onerror=null;this.replaceWith(Object.assign(document.createElement('div'),{className:'thumb-placeholder'}));";
  const fallback = cardImageUrl ? escapeHtml(cardImageUrl) : "";
  const onerror = fallback
    ? `if(!this.dataset.fallback){this.dataset.fallback='1';this.src='${fallback}';}else{${placeholder}}`
    : placeholder;
  return `<img src="/api/kakao-thumbnail/${titleId}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="${onerror}" />`;
}

// ── 목록 그리기: 이미 그린 카드는 그대로 두고, 바뀐 것만 갱신한다 ──────────────
// 탭을 오갈 때마다 800여 개 카드를 지우고 새로 만들면 브라우저가 느려지고 이미지가 사라졌다 다시 나타난다.
// 그래서 카드를 (플랫폼:번호) 키로 기억해 두고(풀), 데이터가 실제로 바뀐 카드만 새로 만든다. 필터/검색으로
// 안 보이게 된 카드도 풀에는 남겨서, 다시 보일 때 그대로 꺼내 쓴다.

function cardKeyOf(w) {
  return `${w.platform || "naver"}:${w.title_id}`;
}

function cardSignature(w, context) {
  return JSON.stringify([
    context, w.platform, w.title, w.thumbnail_url, w.author_summary, w.writer_names, w.tags, w.is_adult, w.is_new,
    w.is_paused, w.has_new_episode, w.status, w.ever_subscribed, w.last_downloaded_no, w.is_finished, webtoonServerConfigured,
  ]);
}

// rows: 지금 보여줄 항목(필터/정렬 적용됨), allRows: 필터 전 전체(풀에서 사라진 작품을 정리하는 용도).
// 새로 만든 카드 수를 돌려준다.
function reconcileGrid(grid, rows, context, allRows) {
  const pool = grid._cardPool || (grid._cardPool = new Map());
  if (allRows) {
    const live = new Set(allRows.map(cardKeyOf));
    for (const [key, node] of [...pool]) {
      if (!live.has(key)) {
        node.remove();
        pool.delete(key);
      }
    }
  }
  const wantedKeys = new Set(rows.map(cardKeyOf));
  for (const child of Array.from(grid.children)) {
    if (!child.dataset.cardKey || !wantedKeys.has(child.dataset.cardKey)) child.remove(); // 안내 문구/필터로 빠진 카드
  }
  let ref = grid.firstElementChild;
  let built = 0;
  for (const w of rows) {
    const key = cardKeyOf(w);
    const sig = cardSignature(w, context);
    let node = pool.get(key);
    if (node && node.dataset.cardSig !== sig) {
      if (node === ref) ref = ref.nextElementSibling;
      node.remove();
      node = null;
    }
    if (!node) {
      node = buildWebtoonCard(w, context);
      node.dataset.cardKey = key;
      node.dataset.cardSig = sig;
      pool.set(key, node);
      built += 1;
    }
    if (node === ref) ref = ref.nextElementSibling;
    else grid.insertBefore(node, ref);
  }
  return built;
}

function buildWebtoonCard(w, context) {
  const card = document.createElement("div");
  card.className = "webtoon-card";
  card.dataset.titleId = w.title_id;
  const platform = w.platform || "naver"; // naver-list/excluded 이외 탭은 전부 네이버 전용이라 기본값 naver

  const metaParts = [];
  if (context !== "naver-list" && w.last_downloaded_no > 0) metaParts.push(`${w.last_downloaded_no}화까지 다운로드`);
  const authorText = context === "naver-list" || platform === "kakao" ? w.author_summary : (w.writer_names || []).join(", ");
  if (authorText) metaParts.push(authorText);
  if (w.is_adult) metaParts.push("🔞");
  // status가 "unregistered"(구독 이력 있는 것을 "목록으로" 보낸 상태)이거나,
  // "unsubscribed"인데 실제로 구독을 거친 적이 없으면(제외됨 → 목록으로만 오간
  // 경우) 배지를 안 보여준다 — 어느 쪽도 실제로 "지금 구독해제 상태"인 게 아니라,
  // 화면상으로는 미등록과 똑같이 취급한다.
  const showsAsUnregistered = w.status === "unregistered" || (w.status === "unsubscribed" && !w.ever_subscribed);
  const statusBadge =
    context === "naver-list" && w.status && !showsAsUnregistered
      ? `<span class="badge ${w.status}">${STATUS_LABEL[w.status] || w.status}</span>`
      : "";
  // 카카오는 구독/다운로드 개념이 없어서, "제외됨" 탭에서도 배지 자체가 필요 없다
  // (그 탭에 있다는 것 자체가 곧 "제외됨" 상태이므로).
  const platformBadge =
    (context === "naver-list" || context === "excluded")
      ? `<span class="platform-badge platform-${platform}">${platform === "kakao" ? "카카오" : "네이버"}</span>`
      : "";
  const checkboxHtml = context === "naver-list" ? '<input type="checkbox" class="webtoon-card-select" />' : "";

  card.innerHTML = `
    <div class="webtoon-card-thumb-wrap">
      ${checkboxHtml}
      ${platformBadge}
      ${platform === "kakao"
        ? kakaoThumbnailImgTag(w.title_id, w.thumbnail_url)
        : (w.thumbnail_url ? `<img src="${escapeHtml(w.thumbnail_url)}" alt="" loading="lazy" />` : '<div class="thumb-placeholder"></div>')}
    </div>
    <div class="webtoon-card-body">
      <div class="webtoon-card-title">${
        platform === "kakao"
          ? `<a href="${kakaoContentUrl(w.title_id)}" target="_blank" rel="noopener">${escapeHtml(w.title)}</a>`
          : `<a href="${naverUrl(w.title_id)}" target="_blank" rel="noopener">${escapeHtml(w.title)}</a>`
      }</div>
      <div class="webtoon-card-meta">${escapeHtml(metaParts.join(" · "))}</div>
      <div class="webtoon-card-badges">${badgesHtml(w)}${statusBadge}</div>
    </div>
    <div class="webtoon-card-actions"></div>
  `;

  const actions = card.querySelector(".webtoon-card-actions");
  if (context === "naver-list") {
    if (platform === "kakao") {
      // 카카오페이지 "구독"은 다운로드 대상이 된다는 뜻이다 — 네이버와 같은 흐름이고 뷰어 서버 설정과는 무관하다(뷰어를
      // 설정했으면 구독 중인 카드에 "뷰어에서 보기" 아이콘이 붙을 뿐).
      if (w.status === "active") {
        actions.appendChild(makeButton("구독해제", () => webtoonListAction(w, "unsubscribe", "kakao", context)));
        if (webtoonServerConfigured) {
          const viewerBtn = makeIconButton(READER_ICON_SVG, "뷰어에서 보기", () => openInWebtoonServer(w.title, "kakao"));
          viewerBtn.dataset.viewerCheckTitle = w.title; // 렌더링 뒤에 실제로 뷰어에 있는지 확인해서 없으면 지운다
          viewerBtn.dataset.viewerCheckPlatform = "kakao";
          actions.appendChild(viewerBtn);
        }
      } else {
        actions.appendChild(makeButton("구독", () => webtoonListAction(w, "subscribe", "kakao", context)));
        actions.appendChild(makeButton("목록제외", () => webtoonListAction(w, "exclude", "kakao", context)));
      }
    } else if (w.status === "active") {
      actions.appendChild(makeButton("구독해제", () => webtoonListAction(w, "unsubscribe", "naver", context)));
      if (webtoonServerConfigured) {
        const viewerBtn = makeIconButton(READER_ICON_SVG, "뷰어에서 보기", () => openInWebtoonServer(w.title, "naver"));
        viewerBtn.dataset.viewerCheckTitle = w.title;
        viewerBtn.dataset.viewerCheckPlatform = "naver";
        actions.appendChild(viewerBtn);
      }
    } else {
      actions.appendChild(makeButton("구독", () => webtoonListAction(w, "subscribe", "naver", context)));
      actions.appendChild(makeButton("목록제외", () => webtoonListAction(w, "exclude", "naver", context)));
    }
  } else if (platform === "kakao") {
    // "구독해제"/"제외됨" 탭에 들어온 카카오 항목 — 네이버와 동일한 워크플로.
    // 구독은 뷰어 서버가 설정돼 있을 때만 의미가 있으니 그때만 버튼을 보여준다.
    actions.appendChild(makeButton("구독", () => webtoonListAction(w, "subscribe", "kakao", context)));
    actions.appendChild(makeButton("목록으로", () => moveToListTab(w.title_id, context, w.ever_subscribed, "kakao")));
  } else {
    actions.appendChild(makeButton("구독", () => subscriptionAction(w.title_id, "subscribe", context)));
    if (context === "unsubscribed" || context === "excluded") {
      // "목록으로" — 구독 이력 여부에 따라 완전 삭제/전용 미등록 상태로 갈린다
      // (moveToListTab 참고). "완전 삭제"와 달리 확인 팝업은 생략한다.
      actions.appendChild(makeButton("목록으로", () => moveToListTab(w.title_id, context, w.ever_subscribed, "naver")));
    }
    if (context === "excluded" && w.is_finished) {
      // 완결작만 완전 삭제 허용 — 완결작은 자동추가 로직이 원래 다시 안 건드리므로 안전하다.
      actions.appendChild(makeButton("완전 삭제", () => deleteWebtoonPermanently(w.title_id, "excluded")));
    }
  }

  return card;
}
