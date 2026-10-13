"""카카오페이지 다운로드 엔진. 로그인 쿠키(kakao_page_auth)로 카카오페이지 API를 호출해서 회차 목록을 받고,
폴더에 이미 있는 파일과 비교해 받을 회차를 정하고, 회차를 zip으로 저장한다. 실제 브라우저 캡처(HAR)와
사용자의 실제 폴더 목록(예전 카카오 도구가 받은 1,935개 파일)으로 확인한 것들:

- 회차 목록: content/product/list — **항상 cursor_direction=NEXT, sort_type=asc, cursor_index=0부터** 시작해서 마지막
  항목의 cursor_index로 이어 부르고, has_next가 false가 될 때까지 넘긴다. cursor_index는 "정렬된 목록 안의 순번"이라
  정렬이 섞이면 안 된다 — 첫 요청을 INIT으로 하면 서버가 계정에 저장된 정렬(작품 화면의 "첫화부터/최신 순")을 따라서, 최신순으로
  저장된 작품은 첫 페이지가 뒤쪽 회차(내림차순)로 오고 그 순번에서 오름차순으로 이어 받으면 앞 회차가 통째로 빠진다(실제로
  개미 1~24화가 빠졌다).
- 이미지 목록: viewer/data → viewer_data.imageDownloadData.files[].secureUrl(서명된 임시 주소).
- 로그인 확인: user/get_profile → result_code 0 이고 profile.uid가 있으면 로그인 상태.
- **파일 앞의 번호는 "N화"가 아니라 회차 순서 번호다** — 프롤로그/예고편/후기도 번호를 하나씩 차지해서(예:
  `0003_프롤로그`, `0002_1화`) 번호와 화수의 차이가 작품마다 다르다. 번호는 사이트의 order_value를 따르고, 프롤로그
  같은 이미지 회차도 받는다(숨김 처리된 회차 — "N일 후 무료"로 열릴 예정인 회차 — 는 목록에 잠금으로 남기고 받지는 않는다). **동영상(예: "동영상 트레일러")은 받을 수 없고 제목에 "체험판"이 있는 회차는 받지 않기로 해서 목록에서 빼고,
  그만큼 뒤 회차의 번호를 당긴다** — 예전 도구는 이 회차들에 번호를 주지 않았기 때문에, 그러지 않으면 같은 회차의
  번호가 하나씩 어긋난다.
- **기다무(RT05) 대여권**: ticket/my로 기다무를 쓸 수 있는지 보고(waitfree.charged_complete), ticket/ready_to_use가
  가리키는 대여권 종류가 RT05일 때만 ticket/use(product_id, ticket_type=RT05)로 열고, 이어서 viewer/data로 이미지를
  받는다. **일반 대여권(RT01)**은 "기다무로 못 여는 회차"(기다무가 없는 작품이거나 waitfree_block인 회차)에만, 설정에서 켠 자동
  다운로드와 확인을 받은 수동 다운로드에서 쓴다(작품마다 구매해 둔 보유 수까지). **결제(캐시/소장권 구매)는 이 코드에 아예 없고,
  RT05/RT01 이외의 이용권(선물권 RT06 등)도 쓰지 않는다.**
  선물권은 사용자가 웹에서 쓰면 그 회차가 "대여 중"이 되어 그대로 받을 수 있다.
- 파일 이름은 `{번호 4자리}_{부제목}.zip`(예: 0384_384화 복국 (2).zip) — 페이지 수는 붙이지 않는다. 예전 도구가
  받은 파일은 뒤에 `#페이지수`가 붙어 있는데(예: 0384_384화 복국 (2)#48.zip), 그 파일도 같은 번호로 인식한다.
  저장 위치는 다운로드 루트 아래 `{작품 제목}` 폴더(금지문자는 카카오 도구와 같은 규칙).

받을 회차는 그 작품 폴더의 상태로 정한다(자동/수동 공통):
  1) 폴더가 없거나 zip이 하나도 없으면 폴더를 만들고 처음부터 전부 받는다.
  2) zip이 여러 개면 회차를 비교해서 누락된 회차를 번호 순서대로 받는다.
  3) zip이 딱 하나면 그 파일을 "여기까지 받았다"는 표식으로 보고 그 이후 회차부터 받는다.
     (cover.jpg, info.xml은 세지 않는다.)
  규칙 2에서도 폴더에 있는 가장 이른 파일보다 앞 회차는 누락으로 보지 않는다 — 표식(규칙 3)으로 시작한
  폴더는 이후 회차를 받으면 파일이 여러 개가 되는데, 그때 표식보다 앞 회차를 전부 "누락"으로 보고 다시 받으면
  안 되기 때문이다(예: 개미 43화 표식으로 시작했는데 1~42화를 받기 시작하면 안 된다). 앞 회차가 필요하면
  수동 다운로드에서 골라 받는다.

읽을 수 있는 회차(무료, 또는 이미 대여 중)를 받는다. 잠긴 회차를 만나면, 기다무를 쓸 수 있을 때 그 회차 하나를
기다무로 열어서 받고(기다무는 작품당 한 장씩 주기적으로 충전된다), 그 뒤 잠긴 회차부터는 멈춰서 "대기"로 알린다.
"""

from app.kakao_page_download.models import (
    DOWNLOAD_ROOT_SETTING_KEY,
    DownloadPlan,
    Episode,
    EpisodeRow,
    ExistingFile,
    RENTAL_TICKET_TYPE,
    RunResult,
    TicketInfo,
    WAITFREE_TICKET_TYPE,
    _is_accessible,
    _parse_episode,
    _warned_expire_formats,
    download_lock,
    effective_download_root,
    episode_state,
    episode_zip_name,
    free_date_of,
    is_trial_item,
    is_video_item,
    normalize_subtitle,
    parse_existing_zip_name,
    plan_by_folder_rules,
    rental_eligible,
    scan_existing_files,
    series_folder,
    waitfree_supported,
)
from app.kakao_page_download.client import (
    KakaoPageClient,
    client_from_saved_cookies,
    client_or_anonymous,
    new_session,
    persist_refreshed_cookies,
)
from app.kakao_page_download.runner import (
    build_series_metadata_bundle,
    download_episode,
    download_selected,
    run_download,
    write_info_xml,
    write_series_metadata,
)

__all__ = [
    "DOWNLOAD_ROOT_SETTING_KEY",
    "DownloadPlan",
    "Episode",
    "EpisodeRow",
    "ExistingFile",
    "KakaoPageClient",
    "RENTAL_TICKET_TYPE",
    "RunResult",
    "TicketInfo",
    "WAITFREE_TICKET_TYPE",
    "_is_accessible",
    "_parse_episode",
    "_warned_expire_formats",
    "build_series_metadata_bundle",
    "client_from_saved_cookies",
    "client_or_anonymous",
    "download_episode",
    "download_lock",
    "download_selected",
    "effective_download_root",
    "episode_state",
    "episode_zip_name",
    "free_date_of",
    "is_trial_item",
    "is_video_item",
    "new_session",
    "normalize_subtitle",
    "parse_existing_zip_name",
    "persist_refreshed_cookies",
    "plan_by_folder_rules",
    "rental_eligible",
    "run_download",
    "scan_existing_files",
    "series_folder",
    "waitfree_supported",
    "write_info_xml",
    "write_series_metadata",
]
