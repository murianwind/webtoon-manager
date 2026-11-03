"""요일 진단 — "기타"에 왜 남았는지를 원인별 개수로 알려 준다(HAR를 일일이 받지 않고 화면에서 바로 확인하게).

요일을 아는 작품(요일별 목록 또는 작품 페이지 조회 결과)은 세지 않고, 모르는 작품을 원인별로 센다.
네이버: finished(완결) / no_weekday_on_page(작품 페이지에 요일 없음) / check_failed(조회 실패 — HTTP 상태별 또는 네트워크 오류) / pending(아직 조회 안 함).
카카오: finished(완결) / ended(서비스 종료: 작품 정보 404·410) / sale_stopped(판매 중지: 작품 정보 state ST62) / hiatus(휴재) / serving_not_listed(연재 중인데 요일 목록에 없음) / check_failed / pending /
catalog_not_loaded(요일별 목록 캐시를 아직 못 받아서 판단 불가).
원인마다 작품 이름 샘플을 최대 SAMPLE_LIMIT개 담는다. 구독해제는 구독한 적 있는 작품만(화면과 같은 규칙).
"""
from app import kakao_catalog, kakao_status, repository, weekdays

SAMPLE_LIMIT = 5


def _failed_label(http: int | None) -> str:
    return "network" if http is None else str(http)


def _summary(entries: list[tuple[str, str, int | None]]) -> dict:
    """entries: (원인, 작품 이름, 실패 HTTP 상태) 목록 → {total, reasons, samples, failed_http}."""
    reasons: dict[str, int] = {}
    samples: dict[str, list[str]] = {}
    failed_http: dict[str, int] = {}
    for reason, title, http in entries:
        reasons[reason] = reasons.get(reason, 0) + 1
        if len(samples.setdefault(reason, [])) < SAMPLE_LIMIT:
            samples[reason].append(title)
        if reason == "check_failed":
            label = _failed_label(http)
            failed_http[label] = failed_http.get(label, 0) + 1
    return {"total": len(entries), "reasons": reasons, "samples": samples, "failed_http": failed_http}


def _shown(status: str, ever_subscribed: bool, wanted: str | None) -> bool:
    """이 작품이 (요청한 상태의) 화면에 나오는가 — 구독해제는 구독한 적 있는 것만."""
    if wanted is not None and status != wanted:
        return False
    return status != repository.STATUS_UNSUBSCRIBED or ever_subscribed


def _naver_entries(wanted: str | None) -> list[tuple[str, str, int | None]]:
    known = weekdays.get_naver_map()
    lookup = weekdays._get_lookup()
    entries = []
    for wt in repository.list_all():
        if not _shown(wt.status, wt.ever_subscribed, wanted) or known.get(wt.title_id):
            continue
        entry = lookup.get(wt.title_id)
        if wt.is_finished:
            entries.append(("finished", wt.title, None))
        elif entry is None:
            entries.append(("pending", wt.title, None))
        elif not entry.get("ok"):
            entries.append(("check_failed", wt.title, entry.get("http")))
        else:
            entries.append(("no_weekday_on_page", wt.title, None))
    return entries


def _kakao_entries(wanted: str | None) -> list[tuple[str, str, int | None]]:
    catalog_items = kakao_catalog.snapshot()[0]
    listed = {item["title_id"] for item in catalog_items if item.get("weekdays")}
    lookup = kakao_status.get_lookup()
    entries = []
    for row in repository.get_kakao_webtoons_map().values():
        if not _shown(row["status"], row["ever_subscribed"], wanted) or row["title_id"] in listed or kakao_status.weekdays_of(row["title_id"]):
            continue  # 요일을 아는 작품(요일별 목록 또는 작품 개요에서 알아낸 요일)은 세지 않는다
        entry = lookup.get(str(row["title_id"]))
        title = row["title"]
        if row["is_finished"] or (entry is not None and entry.get("on_issue") == "N"):
            entries.append(("finished", title, None))
        elif entry is None:
            entries.append(("pending", title, None))
        elif kakao_status.is_ended(entry):
            entries.append(("ended", title, None))
        elif kakao_status.is_sale_stopped(entry.get("state")):
            entries.append(("sale_stopped", title, None))
        elif entry.get("on_issue") == "P":
            entries.append(("hiatus", title, None))
        elif entry.get("on_issue") == "Y":
            entries.append(("serving_not_listed" if catalog_items else "catalog_not_loaded", title, None))
        else:
            entries.append(("check_failed", title, entry.get("http")))
    return entries


def summarize(status: str | None = None) -> dict:
    """status: active/unsubscribed/excluded 중 하나면 그 상태만, None이면 전부."""
    return {"status": status, "naver": _summary(_naver_entries(status)), "kakao": _summary(_kakao_entries(status))}
