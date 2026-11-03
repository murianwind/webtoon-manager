# 개발 문서

이 문서는 **개발자용**입니다. 일반 사용자를 위한 설명서는 [README.md](README.md)이고, 앱의 "도움말" 탭에는 README만 나옵니다.

## 문서 규칙

- **README.md는 일반 사용자 설명서만** 담습니다. 코드 구조, 테스트, 내부 동작 원리, API 이름 같은 개발 내용은 이 문서에 씁니다.
- 아래는 `tests/backend/test_docs.py`가 자동으로 검사하므로, 어기면 CI가 실패합니다.
  - README의 설치용 YAML이 `docker-compose.yml`과 같은지(환경변수 · 볼륨 · 포트 · 라벨)
  - `docker-compose.yml`이 쓰는 `${변수}`가 README 환경변수 표에 모두 설명돼 있는지
  - README에 개발자 용어(파일 경로, 테스트 도구, 내부 필드 이름 등)가 없는지
  - README가 도움말 탭에서 깨지지 않는지(코드 블록, 표, 접기, 목차, 내부 링크)
- **README에서 코드 블록은 번호 목록 안에 넣지 마세요.** 도움말 변환기(Python-Markdown)는 목록 안의 코드 블록을 처리하지 못해서 한 줄로 뭉개집니다(GitHub에서는 정상으로 보여서 놓치기 쉽습니다). 목록 밖에 두면 둘 다 정상입니다.
- 도움말은 이미지에 들어 있는 `README.md`를 읽어 보여줍니다(`app/help_page.py`). 새 이미지가 배포되면 도움말도 같이 바뀌므로, 배포된 버전과 항상 일치합니다.

## 코드 구조

- `app/api/routes.py`는 도메인별 라우터를 `/api` 아래로 묶기만 합니다. 실제 엔드포인트는 `app/api/` 안의 모듈에 있습니다: `webtoons`(네이버 구독 목록), `kakao_webtoons`(카카오 전체목록/구독), `kakao_download`(카카오 로그인/수동 다운로드), `registry`(작가/태그), `manual_download`(네이버 수동 다운로드), `schedule`(실행 스케줄), `discord`, `app_settings`(일반 설정/다운로드 폴더), `jobs`(실행/진행상황), `history`, `system`(백업/복원/도움말), `archive`(아카이빙). 둘 이상의 모듈이 쓰는 모델/헬퍼만 `common.py`에 있고, 모듈끼리는 서로 가져오지 않습니다(모듈 → common 방향만).
- 화면 스크립트는 `app/static/js/`의 파일들로 나뉘어 있고 `index.html`이 **파일 이름 순서대로** 로드합니다(클래식 스크립트라 최상위 선언을 전역으로 공유). `99-main.js`는 다른 파일의 함수를 즉시 참조하므로 반드시 마지막이어야 합니다. 새 파일을 추가할 땐 번호 접두사로 순서를 정하세요.
- `app/log_redaction.py`: 디스코드 웹훅/봇 토큰, 카카오페이지 서명 주소의 `token`/`signature`/`credential` 값, 저장된 카카오 로그인 쿠키 값을 로그에서 가립니다.
- `app/report_seen.py`: 다운로드 리포트가 같은 회차를 두 번 보고하지 않게, 이미 보고한 회차(받은/실패한 회차, 미등록 작품의 새 에피소드)를 기억합니다. 전송이 성공한 뒤에만 기록하고, 테스트 발송은 기록하지 않습니다.
- `app/kakao_authors.py`: 카카오페이지 작품 "정보"의 글/그림/원작 구분을 읽어 관심 작가 등록 규칙(원작자 우선, 없으면 글 작가, 그림 작가 제외)을 적용합니다.
- `app/download_roots.py`: 네이버/카카오 다운로드 폴더를 정하는 곳. 폴더를 쓰는 코드는 모두 여기를 거칩니다.
- 80줄이 넘는 함수는 "지금 동작을 그대로 고정하는" 특성 테스트를 먼저 쓴 뒤 단계별 작은 함수로 나눴습니다. 새로 큰 함수가 생기면 같은 방식으로 다루세요.

## 테스트

테스트는 실제 카카오/네이버 서버를 부르지 않습니다. 응답 형태는 `tests/fixtures/`의 실제 캡처에서 뽑은 샘플을 씁니다.

- **백엔드**: `python tests/run_backend.py [이름조각] [--coverage]` — 테스트마다 새 임시 폴더(DB/다운로드/보관/쿠키)로 별도 프로세스에서 돌립니다. 의존성은 `pip install -r requirements-dev.txt`.
- **화면**: `cd tests/js && npm ci && npm test` — jsdom으로 `index.html`과 스크립트들을 브라우저처럼 순서대로 로드해서 확인하고, 스크립트 오류(파일 사이 로드 순서 문제 포함)는 실패로 처리합니다. Node.js가 필요합니다.
- **정적 검사**: `python -m pyflakes app tests/backend` (미사용 임포트/변수, 미정의 이름), `actionlint .github/workflows/*.yml`
- 새 테스트는 `tests/backend/test_*.py` 또는 `tests/js/test_*.js` 이름으로 파일만 추가하면 자동으로 포함됩니다.
- **테스트는 일반 사용자 권한으로 돌려 보세요.** 루트로 돌리면 `/dl` 같은 시스템 루트 폴더를 만들 수 있어서 통과해 버리지만, 깃헙 액션(일반 사용자)에서는 `PermissionError`로 실패합니다(실제로 그랬습니다). 실제로 폴더가 만들어지는 경로는 항상 `tempfile` 임시 폴더를 쓰고, 개발 환경 전용 경로를 박지 마세요. `tests/backend/test_hygiene.py`가 이를 검사합니다.
- rclone이 낀 분기는 `tests/backend/fake_rclone.py`(메모리 안의 원격 폴더)로 흉내 냅니다 — 테스트에서 `FakeRclone().install()`만 부르면 됩니다.

## 깃헙 액션 (CI / 배포)

- **`.github/workflows/tests.yml`**: `main`이 아닌 브랜치에 푸시하거나 PR을 올리면 백엔드/화면 테스트를 돌립니다(수동 실행도 가능). 다른 워크플로가 불러 쓸 수도 있습니다(`workflow_call`).
- **`.github/workflows/build.yml`**: `main`에 푸시하면 ① `tests.yml`을 불러 테스트를 돌리고 ② **테스트가 모두 통과했을 때만**(`needs`) 이미지를 빌드해서 GHCR(`ghcr.io/murianwind/webtoon-manager:latest`)에 올립니다. 이 이미지를 Watchtower가 받아 컨테이너를 갱신합니다. 수동 실행을 다른 브랜치에서 눌러도 `latest` 이미지는 `main`에서만 만듭니다.
- **이전 실행 취소**: 두 워크플로 모두 `concurrency`의 `cancel-in-progress`가 켜져 있어서, 같은 브랜치에 푸시가 잇따라 오면 이전 실행(테스트나 빌드 중이던 것)을 강제로 취소하고 가장 마지막 것만 실행합니다. 그룹 이름은 서로 달라야 합니다(같으면 불려 쓰는 쪽과 부르는 쪽이 서로를 취소합니다) — `tests.yml`은 그룹에 `github.event_name`을 넣어 구분합니다.
- 워크플로를 고칠 때는 `actionlint .github/workflows/*.yml`로 먼저 확인하세요(CI에서도 같은 검사를 합니다). `test_docs.py`가 "테스트 후 빌드", "이전 실행 취소" 규칙이 유지되는지 검사합니다.

## 카카오페이지 연동 메모

README(사용자 설명서)에서 뺀 내부 동작 설명입니다. 코드를 고칠 때 참고하세요.

**회차 목록**
- 항상 `cursor_direction=NEXT`, `sort_type=asc`, `cursor_index=0`부터 이어 부릅니다. `INIT`으로 시작하면 서버가 계정에 저장된 작품별 정렬("첫화부터/최신 순")을 따르고, `cursor_index`는 "정렬된 목록 안의 순번"이라서, 최신순으로 저장된 작품은 앞 회차가 통째로 빠집니다(개미 1~24화 사례).
- 동영상 회차(`slide_type`이 이미지(SD03)가 아닌 것)와 제목에 "체험판"이 있는 회차는 목록에서 빼고 뒤 회차 번호를 당깁니다(`is_excluded_item`). 예전 도구가 이 회차들에 번호를 주지 않았기 때문에, 그러지 않으면 같은 회차 번호가 하나씩 어긋납니다. 분석/수동 다운로드/자동 다운로드가 모두 `list_episodes`를 거치므로 한 곳에서 빠집니다. 빼 둔 개수(`_excluded_video_count`, 동영상+체험판)는 분석 화면의 "사이트 회차 수" 비교에 쓰입니다.
- `hidden: true` 회차는 "N일 후 무료"로 열릴 **예정**인 회차입니다(유료 + 미래의 `free_change_dt`, 탈조클럽 32~36화 사례). 사이트 목록에는 보이므로 빼지 않고 잠긴 회차로 둡니다. `hidden`이면 접근 가능으로 표시돼 있어도 받지 않습니다.
- 사이트 전체 회차 수는 작품 정보의 `on_sale_count`입니다. 가져온 회차(+동영상으로 뺀 수)가 이보다 적으면 분석 화면이 경고합니다.

**이미지 받기**
- 회차 이미지는 `viewer/data`가 주는 서명된 임시 주소(`token=`/`signature=`)로 받습니다. 한 글자만 달라져도 404가 되므로 주소를 다시 인코딩하지 않고 그대로 보냅니다(`URL(..., encoded=True)`).
- 회차마다 첫 이미지로 먼저 확인하고, 안 되면 주소를 새로 받아 한 번 더 시도한 뒤에도 안 되면 그 회차를 실패로 끝냅니다(반쪽짜리 파일은 남기지 않음). 실패 로그에는 응답 코드와 서버/경로만 남기고 토큰은 남기지 않습니다.
- 기다무로 연 회차의 받기가 실패하면 기다무는 이미 소모된 상태이고, 그 회차는 대여 기간 동안 "보유"로 남아 이용권 없이 다시 받을 수 있습니다.

**이용권**
- `ticket/my`(보유 이용권), `ticket/ready_to_use`(사용 가능 여부), `ticket/use`(사용). 기다무는 `RT05`, 일반 대여권은 `RT01`(대여권.har로 확인), 선물권은 `RT06`입니다. **`RT05`와 `RT01`만 쓰고**(선물권은 쓰지 않음) 결제(`ticket/buy`, `store/*`)는 코드에 아예 없습니다 — `tests/backend/test_kp_rental_ticket.py`가 모듈 소스에 구매/결제 주소가 없는지 검사합니다.
- 기다무 지원 여부는 이용권 응답이 아니라 작품 정보의 `is_waitfree`로 판단합니다(이용권 응답은 기다무가 없는 작품에서도 "사용 중"으로 나옵니다). 충전 주기는 `charged_period_by_minute`(분)입니다.
- 자동 다운로드는 받을 수 있는 회차를 다 받은 뒤 첫 잠긴 회차 하나를 기다무로 엽니다. 최신 회차처럼 `waitfree_blocked`인 회차는 건드리지 않습니다.
- **대여권 필요 알림**: `RunResult.ticket_needed`가 "받을 회차를 다 받았는데 남은 첫 잠긴 회차(기다무로 막 연 회차는 건너뜀)가 기다무로 열 수 없는 회차"인지 판정합니다(기다무 없는 작품이거나 `waitfree_blocked`). 숨김/"N일 후 무료" 회차, 오류/실패, 받을 회차가 남은 경우는 대상이 아닙니다. `scheduler._notify_kakao_ticket_needed`가 웹훅으로 보내고, 알린 회차 번호를 `kakao_webtoons.ticket_notified_no`에 기록해 같은 회차는 다시 알리지 않습니다. `send_webhook_notification`은 전송 성공 여부를 돌려주며, 웹훅이 없거나 실패하면 기록하지 않아 다음 실행에서 다시 시도합니다.
- **"더 이상 받을 회차 없음" 알림**: `RunResult.caught_up_episode`가 "연재작(`finished`가 아님)이고 `nothing_left`일 때 마지막 회차"를 돌려줍니다(완결작은 기존 완결 확인 알림이 맡음). `scheduler._notify_kakao_caught_up`이 보내고 마지막 회차 번호를 `kakao_webtoons.caught_up_notified_no`에 기록해 같은 회차는 다시 알리지 않습니다. 기록이 NULL인데 이번에 받은 회차가 없으면(이미 따라잡은 작품을 처음 확인) 알리지 않고 기준만 기록합니다 — 이 기능을 켠 첫 실행에 연재작 전부에 알림이 가는 것을 막습니다. 대여권 필요 알림과는 조건이 겹치지 않습니다(이쪽은 잠긴 회차가 없을 때만).

**로그인 쿠키**
- 필수 쿠키: `_kau`, `_kpwtkn`, `_T_ANO`, `_kahai`, `_kawlt`, `_kpdid`(`_karmt`는 있으면 같이 씀). 암호화해서 저장하고 백업에는 포함하지 않습니다. 로그인이 풀리거나 만료가 5일 안으로 다가오면 하루 한 번 디스코드로 알립니다.

**무엇을 받을지(`plan_by_folder_rules`)와 받은 회차 기록(`app/kakao_records.py`)**
- 작품마다 `kakao_webtoons.downloaded_numbers`(JSON 배열, NULL = 기록 없음)에 "이 앱이 받았거나 폴더에서 확인한 회차 번호"를 둡니다. **빈 기록(`[]`)과 NULL은 다릅니다** — NULL이면 예전 폴더 규칙을, 있으면 기록을 씁니다.
- 계획 규칙: ① 폴더에 zip이 없으면 `new_folder`(기록 무시, 처음부터). ② 기록이 있으면 `compare` — `가진 회차 = 폴더 ∪ 기록`, 기준선 = `min(폴더 ∪ 기록)`, 기록에만 있는 회차는 `EpisodeRow.archived`(기록에는 있고 폴더에는 없는 것 — 아카이빙으로 옮겼거나 파일을 지운 경우이며 화면 문구는 "폴더에 없음". 아카이빙 이력을 보는 게 아니다). ③ 기록이 없고 파일이 하나면 `single_marker`(그 다음부터, 표식 위치는 부제목으로 보정), 여러 개면 `compare`(가장 이른 파일부터).
- **받을 수 있는 회차(무료/대여 중)는 앞에 잠긴 회차가 있어도 `to_download`에 넣고, 잠긴 회차는 모두 `locked`(번호순)에 둡니다.** `run_download`는 `to_download`를 받은 뒤 `locked[0]`을 기다무로 엽니다. 예전에는 첫 잠긴 회차에서 모든 뒤 회차를 막았지만(`blocked`), 그러면 앞이 잠긴 작품의 새 무료 회차를 못 받았습니다. 숨김(hidden) 회차는 잠긴 회차이며 받지 않습니다.
- **기록하는 곳은 한 군데**입니다 — 회차 zip을 저장하는 `download_episode`(자동, 수동, 기다무가 모두 거침). 기록이 NULL인 작품에는 더하지 않습니다(한 회차짜리 기록이 생기면 아카이빙 이력으로 처음 기록을 만드는 일이 막힘). 추적하지 않는 작품도 기록하지 않습니다.
- **처음 기록 만들기**: `initial_numbers` = 폴더 파일 번호 ∪ 아카이빙 이력의 파일 번호(`archive_history`에서 `title_id = kakao_<번호>`, 파일 이름 앞의 번호). 폴더 안의 빈 회차는 기록하지 않아서 받게 됩니다. 표식 파일 하나 + 이력 없음이면 번호가 사이트와 다를 수 있어서 만들지 않고, 첫 `run_download`에서 사이트와 맞춰 본 위치(`marker.resolved_number`)로 만듭니다. 시작할 때(`main.lifespan`, 스케줄러 시작 **전**) 한 번 `backfill_and_log`를 돌리고, 복원 직후에도 돌립니다. 요약은 실행 이력(다운로드)에 한 건으로 남습니다.
- 폴더가 비어도 기록은 지우지 않습니다(아래 "폴더가 비어도 받은 회차 기록 유지" 참고). 다시 받게 하려면 수동 다운로드를 쓰거나 "받지 않은 것으로 기록"으로 직접 지정합니다.
- 알려진 한계: 보관 폴더의 실제 파일은 읽지 않고 아카이빙 이력만 읽습니다. 이력 보관기간으로 지워졌거나 "폴더 일괄 이동"(작품과 연결되지 않는 이력)으로 옮긴 회차는 소급되지 않습니다. 이미 중간이 비어 폴더에 파일 하나만 남은 작품은 빈 회차를 구분할 수 없습니다.
- 저장 형태는 `{번호 4자리}_{부제목}.zip`이고 페이지 수는 붙이지 않습니다. 예전 도구의 `#페이지수`가 붙은 파일도 같은 번호로 인식합니다.
- 자동 다운로드는 네이버와 같은 설정(작품당 상한 `max_new_episodes_per_title`, 배치 사이 쉬는 시간 `batch_rest_minutes`)을 쓰고, 상한에 걸리면 쉬었다가 같은 실행 안에서 이어 받습니다(`_download_kakao_title`). 안전장치로 한 작품당 배치 수에 상한(`_KAKAO_MAX_BATCHES_PER_TITLE`)이 있습니다.

**목록과 캐시**
- 요일별 목록은 요일마다 여러 페이지를 받아야 해서 캐시를 쓰고, 프로그램 시작 때와 3시간마다 백그라운드로 갱신합니다. 일부 요일이 실패하면 멀쩡한 예전 목록을 그대로 둡니다. 화면은 이미 그린 카드는 두고 바뀐 카드만 갱신합니다(`reconcileGrid`).
- 구독 상태를 바꾸는 요청(`/api/webtoons`, `/api/kakao-webtoons`, `/api/naver-list`)이 성공하면 전체목록/구독해제/제외됨 목록 캐시를 모두 무효화합니다(`apiCall` 안에서 한 번에 처리).
- 요일 목록 카드 이미지는 공식 표지와 달라서, 작품을 처음 볼 때 공식 표지를 받아 DB 파일 옆 `kakao_cover_cache`에 저장해 둡니다.
- 예전 카카오웹툰 시절 기록은 카카오페이지 목록을 처음 온전히 받은 직후 한 번 자동으로 변환합니다(제목이 정확히 하나만 일치하는 작품만). 못 옮긴 "제외됨" 기록은 지웁니다.

**폴더 이름**
- 금지문자 치환 규칙이 다릅니다. 네이버는 전각 문자(`：`), 카카오는 밑줄(`_`)입니다. 웹툰 뷰어 서버 조회와 아카이빙은 플랫폼별 규칙으로 폴더를 찾습니다.

**아카이빙과 표지/info.xml**
- 주기/수동 실행은 마지막 회차 파일을 남기고 옮기며, info.xml은 복사해 매번 덮어쓰고 커버는 목적지에 없을 때만 복사합니다. 완결 처리는 전부 옮기고 info.xml/커버도 덮어씁니다.
- **뷰어에서 읽지 않은 회차 건너뛰기(정기 아카이빙만)**: 뷰어 서버 주소(`webtoon_server_url`)가 설정돼 있으면 `scheduler/archive_job.py`가 `_build_read_numbers_provider`로 만든 조회 함수를 `run_periodic_archive(read_numbers_provider=...)`에 넘기고, 웹툰 대상마다 `TitleArchiveJob.read_numbers`(읽은 회차 번호 집합)가 채워집니다. `_archive_title`은 마지막 회차 보존을 적용한 뒤 `read_filter.filter_read_files`로 읽은 회차만 남깁니다. 아카이빙은 별도 스레드의 동기 코드라 조회 함수가 `run_coroutine_threadsafe`로 잡의 이벤트 루프에서 `webtoon_server_client.fetch_read_numbers`를 실행합니다. 뷰어의 `GET /api/lookup/read-state?series=<폴더명>`을 호출하며(`{"read_numbers": [...]}`, '읽는 중' 회차는 뷰어가 이미 뺌), 이 경로는 `/api/lookup/latest`처럼 뷰어 관리자 게이트 예외 목록에 있어야 합니다. 조회 실패·미매칭·주소 미설정이면 `None`이라 거르지 않고 기존처럼 옮기며(빈 집합과 구분), 수동 실행·완결 전체 이동·완결 구독해제·폴더 대상은 적용하지 않습니다. 테스트: `tests/backend/test_archive_skip_unread.py`, `tests/backend/test_webtoon_server_read_numbers.py`.
- 카카오페이지 공식 표지로 교체하는 것은 완결 처리일 때만, 폴더의 info.xml `<Web>`이 카카오페이지 주소일 때만 합니다(옛 카카오웹툰 주소면 안 함 — 메타 동기화로 info.xml을 새로 써야 합니다).
- 아카이빙 대상 id는 내부적으로 네이버는 `title_id`, 카카오는 `kakao_<시리즈 번호>`입니다.
- 메타 동기화는 네이버/카카오 모두 info.xml과 커버를 새로 교체합니다. 다운로드할 때는 커버가 없을 때만 받습니다.

## 백업/복원 설계

- **무엇을 백업하나**: DB 11개 테이블(`repository._BACKUP_TABLES`)과 설정. 컬럼 목록은 `repository._*_COLUMNS`에 하드코딩돼 있고(조작된 백업의 SQL 주입 방지), 컬럼이 늘어나면 여기도 늘려야 합니다 — 새 컬럼을 빠뜨리면 복원 때 조용히 버려집니다. `job_history`(실행 로그)는 운영 기록이라 백업/복원 대상이 아니고 복원해도 그대로 남습니다.
- **설정 키 세 종류**(`repository._SECRET/_CACHE/_ALERT_STATE_SETTING_KEYS`): ① 비밀값(웹훅, 봇 토큰, 채널 ID, 카카오 쿠키) — 백업에서 빼고, 복원해도 지금 값을 남기고, 백업 파일에 들어 있어도 무시(조작된 백업이 웹훅을 바꿔치기하지 못하게). ② 캐시(카카오 전체목록) — 백업에서 빼고 복원해도 그대로. ③ 알림 기록(하루 한 번 알림 시각들) — 백업에서 빼고 복원하면 지워서, 환경이 바뀐 뒤 다시 알릴 수 있게 한다. 그 밖의 설정(`report_last_sent_at`, `report_seen_new_episodes`, `kakao_legacy_migrated` 포함)은 백업에 넣습니다 — 새 컨테이너에 복원했을 때 지난 리포트를 다시 보내거나 옛 카카오웹툰 기록을 다시 변환하는 일을 막기 위해서입니다.
- **형식 버전**: `_meta.format_version`(`BACKUP_FORMAT_VERSION`). 없으면 1(예전 백업)로 보고 복원하며, 현재 버전보다 높으면 거부합니다. 백업 테이블이 하나도 없는 JSON(`{}` 등)도 거부합니다 — 거부는 모두 데이터를 지우기 **전에** 합니다.
- **복원 흐름**(`POST /api/restore` → `app/backup_restore.py`): ① `repository.restore_all`(한 트랜잭션, 실패하면 롤백) ② 환경 점검 `check_environment`: 이 환경에 없는 다운로드 폴더 설정은 기본값으로 되돌리고, 구독 작품의 폴더 유무, rclone 설정 파일/보관 폴더 유무, 다시 입력할 비밀값을 계산 ③ 스케줄 즉시 재적용(`reschedule_all`) ④ 카카오가 켜져 있으면 전체목록 갱신 시작. 결과는 `{"status", "warnings", "reenter"}`로 돌려주고 화면이 목록으로 보여 줍니다(없으면 자동 새로고침).
- **새 컨테이너 복원의 핵심 위험**: 카카오페이지는 작품 폴더가 없으면 처음부터 전부 받습니다(폴더 규칙 1번). 볼륨이 안 붙은 채 스케줄이 돌면 이미 가진 회차를 다시 받으므로, 복원 직후 폴더 점검 경고를 반드시 띄웁니다.
- 비밀값을 암호로 보호해서 백업에 포함하는 방식은 아직 없습니다(암호화 키가 데이터 볼륨의 `.secret.key`에 있어 다른 환경으로 옮길 수 없기 때문에 현재는 "다시 입력"으로 처리).

## 알려진 문제 (미해결)

- **일반 대여권(RT01) 사용** (`KakaoPageClient.use_rental_ticket`): `ticket/ready_to_use`가 알려 주는 `available.ticket_rental_type == "RT01"`, `my.ticket_rental_count`(작품별 보유 수 — 대여권은 작품에 귀속), 그리고 "기다무로 못 여는 회차"(`series.is_waitfree`가 거짓이거나 `single.waitfree_block`이 참)일 때만 `POST ticket/use`(`product_id`, `ticket_type=RT01`)를 **한 번** 보냅니다(재시도 없음). 응답을 못 받으면 `read_access_type == "RAT2"`(대여 중)인지 다시 조회해서 판단합니다 — 이중 소모 방지. 대상 판정은 `rental_eligible`(기다무 대기 회차와 숨김/"N일 후 무료" 회차는 제외) 한 곳입니다.
  - **자동**: `run_download(use_rental_tickets=...)`가 기다무 단계 뒤에 `_download_locked_with_rental`로 잠긴 회차를 번호순으로 보되, **`rental_eligible`이 아닌 회차(기다무로 열 수 있는 충전 대기 회차, 숨김/"N일 후 무료")는 건너뛰고** 기다무로 못 여는 회차에만 보유 수까지 엽니다(번호가 비어도 다음 실행의 누락 회차 비교로 채워집니다). 이미 받은 회차(폴더/받은 회차 기록)는 `plan_by_folder_rules`가 `plan.locked`에 넣지 않으므로 대여가 끝나 다시 잠겨 보이는 회차에는 쓰지 않습니다. 대여권이 0장이거나 사용이 거절되면 그 작품은 거기서 멈춥니다(보유 수는 작품마다 같아서 뒤 회차도 못 엽니다). 설정은 `settings`의 `kakao_rental_ticket_auto_use`(`"1"`일 때만 켜짐, 기본 꺼짐; `GET/POST /api/settings/kakao-rental-ticket-auto-use`)이고, 작품별 설정은 없습니다(대여권이 작품에 귀속돼 있어서 전역 하나로 충분). `RunResult.rental_used/rental_remaining`가 결과이고, `ticket_needed`는 기다무와 대여권으로 **연 회차 번호를 뺀 나머지 잠긴 회차**로 판정합니다 — 설정이 꺼진 실행은 첫 잠긴 회차가 대여권이 필요한 회차일 때(예전과 같음), 켜진 실행(`rental_skip_mode`)은 대여권으로 열려고 했는데 보유 대여권이 0장(`rental_remaining == 0`)이어서 못 연 첫 회차일 때입니다(보유 수를 확인한 적이 없거나 남아 있으면 단정하지 않음). `_kakao_remaining_free`와 `nothing_left`도 연 회차를 "받을 회차/잠긴 회차"에서 뺍니다. 자동 다운로드 한 번이 끝나면 `_kakao_download_summary`가 작품 수, 받은 회차, 대여권 사용 장수, 대여권이 필요해 멈춘 작품 수, 기다무 충전을 기다리는 작품 수(`RunResult.waiting_for_waitfree`), 오류 수를 한 줄로 로그에 남깁니다. 쓸 때마다 `scheduler._notify_kakao_rental_used`가 웹훅으로 알립니다(배치마다 한 번).
  - **수동**: 분석 표에서 보유 대여권이 있고 `rental_eligible`인 회차가 `state: "rental"`(선택 가능)입니다. 화면은 확인 창을 거쳐 `POST /api/kakao-manual/run`에 `use_rental_tickets: true`를 보내고, 서버는 이 값이 있을 때만 `download_selected`가 대여권을 씁니다(보유 수를 넘으면 앞 번호부터, 나머지는 "대여권이 부족합니다"로 건너뜀).
  - **분석 표 상태**(`kakao_download._kakao_episode_state`): 지금 열 수 있으면 `waitfree`/`rental`, 못 열면 이유별로 `waitfree_wait`(기다무로 열 수 있는데 충전 중)/`rental_needed`(`rental_eligible`인데 보유 대여권 0장), 숨김·"N일 후 무료"는 `locked`입니다. 이용권 조회가 안 됐거나(`tickets is None`) 이미 받은 회차는 사정을 단정하지 않고 `locked`를 유지합니다. 새 상태는 모두 `selectable: false`이고 배지는 `50-manual-download.js`의 `KAKAO_STATE_BADGE`, 색은 `.kp-waitfree-wait`/`.kp-rental-needed`, 상태 칸은 `table-layout: fixed`라 108px입니다.
  - 테스트: `test_kp_rental_ticket.py`(사용 로직, 가짜 서버 `fake_kakao_rental_site.py`), `test_kp_rental_auto.py`(설정/자동 연결/알림), `test_kp_rental_manual.py`(수동 API), `tests/js/test_kakao_rental_ui.js`(화면).
- 받은 회차 기록의 소급 한계는 위 "받은 회차 기록" 항목을 보세요(이력/보관 폴더의 파일을 읽지 못하는 경우).

## 수동 다운로드 표 머리글 고정과 검색창 지우기 버튼

- **표 머리글 고정(sticky)**: `#manual-table`, `#kakao-manual-table`에 `overflow`를 걸면(둥근 모서리를 자르려고 `hidden`을 넣었던 적이 있음) 그 표가 스크롤 영역이 되어 바깥 `.scroll-table-wrapper` 안의 `position: sticky` 머리글이 먹지 않습니다. 모서리는 wrapper가 자르므로 표에는 `overflow`를 두지 않습니다. 진행(6번째) 칸은 "이미받음" 배지 + "보관 폴더로 옮김"이 한 줄에 들어가도록 190px입니다(`table-layout: fixed`라 좁으면 잘림). 레이아웃은 jsdom이 계산하지 못해서 `tests/js/test_manual_table_layout.js`가 스타일 규칙(overflow 없음, sticky, 진행 칸 폭)을 확인합니다.
- **`.hint`의 음수 위쪽 여백**: `.hint`는 입력칸에 바짝 붙이려고 `margin: -8px 0 16px`입니다. 버튼처럼 키가 있는 요소 바로 아래에 두면 문구가 8px 끌려 올라가 버튼을 덮으므로, 그런 곳에는 `hint-after-controls`(위쪽 여백 0)를 같이 씁니다(설정의 백업/복원 카드). 그 카드의 버튼과 복원 파일 선택 칸은 `.backup-actions`(flex, 세로 가운데 맞춤)로 한 줄에 묶습니다. `tests/js/test_backup_card_layout.js`가 구조와 스타일 규칙을 확인합니다.
- **검색창 지우기(×)**: `input.clearable`마다 `initClearableInputs()`(`00-core.js`, 시작 때 한 번)가 래퍼와 버튼을 붙입니다. 버튼은 CSS `input:placeholder-shown + .input-clear`로 숨겨서, 앱 시작 때 저장된 검색어를 코드로 채워도 버튼이 맞게 보입니다(그래서 이 입력 칸들은 placeholder가 있어야 합니다). 누르면 값을 비우고 `input` 이벤트를 보내 기존 검색 처리를 그대로 탑니다.

## 구독해제 웹툰의 새 에피소드 알림 (`app/new_episode_watch.py`)

- 신작 스캔(discovery job)의 한 단계로 `new_episode_watch.run`이 돕니다. 네이버는 구독해제(`status=unsubscribed`) 작품마다 `naver_api.fetch_latest_episode`(목록 1페이지)로 최신 회차 번호를, 카카오는 `KakaoPageClient.fetch_series_item`(회차 1개 요청)의 `on_sale_count`(회차 수)를 봅니다. 제외됨과 구독 중 작품은 보지 않습니다.
- 판정은 `decide(seen, latest)` 한 곳: 기준이 없으면(NULL) 알리지 않고 기준만 기록, 값이 커졌을 때만 알림, 못 가져왔거나 그대로/줄었으면 아무것도 안 합니다. 기준은 `webtoons.new_episode_seen_no`와 `kakao_webtoons.new_episode_seen_no`에 둡니다.
- 알림은 한 메시지로 묶어 `discord_notify.send_webhook_notification`으로 보내고, **전송에 성공했을 때만** 기준을 갱신합니다(웹훅이 없거나 실패하면 다음 스캔에서 다시). 카카오 쿠키가 없으면 카카오 쪽만 건너뜁니다.

## 네이버 매일+ 웹툰 숨기기 (`app/daily_plus.py`)

- 요일별 전체 목록 API와 통합검색 응답에는 매일+를 가리키는 값이 확인되지 않아서, **모바일 매일+ 페이지(`m.comic.naver.com/webtoon/weekday?week=dailyPlus`)의 본문 목록 링크**(`/webtoon/list?titleId=N&week=dailyPlus`)로 구분합니다(HAR 캡처로 확인). 같은 페이지의 "이달의 신작" 캐러셀 링크에는 `week`가 없어서 매일+가 아닙니다. 파싱 샘플은 `tests/fixtures/naver_daily_plus_sample.html`(캡처 발췌)입니다.
- 모바일 UA로 하루에 한 번만 받아 `settings`(`naver_daily_plus_ids`)에 담습니다. 받기에 실패하거나 페이지 구조가 바뀌어 하나도 못 찾으면 예전 기록을 그대로 쓰고, 기록이 없으면 아무것도 숨기지 않습니다(일반 작품이 사라지는 사고 방지). 페이지가 바뀌어 매일+가 다시 보이면 이 정규식(`_LINK_RE`)부터 확인하세요.
- 거르는 곳은 `GET /api/naver-list`(전체목록: 구독 중인 것과 DB에만 있는 장기휴재 작품 포함), `GET /api/webtoons`(구독해제/제외됨 탭), `GET /api/manual-download/search`(수동 다운로드 검색) 세 곳입니다. 화면과 검색만 숨기고, 직접 구독한 작품의 자동 다운로드, 구독 상태, 직접 titleId로 하는 분석은 건드리지 않습니다.
- **신작 스캔은 매일+를 추가하지 않습니다**: 숨기기만 하고 추가를 막지 않았더니 신작 스캔이 새로 찾은 매일+ 작품을 구독 중으로 추가해서, 보이지 않는 채로 자동 다운로드 대상이 되는 문제가 있었습니다. `tracker._add_discovered_titles`(구독 작가/미구독 관심 작가 두 경로가 공유)와 `scan_curation_tags`(태그)가 `daily_plus.current_ids`에 있는 작품은 건너뜁니다. 매일+ 목록을 모르면(빈 목록) 아무것도 거르지 않습니다.
- **이미 자동 추가된 매일+ 정리**: `repository.exclude_auto_added_titles`가 "구독 중이고 `added_source`가 `artist`/`tag`인" 매일+ 작품을 `제외됨`으로 바꿉니다(직접 구독 `manual`, 구독해제, 제외됨은 그대로). 신작 스캔 시작 때 `tracker.exclude_auto_added_daily_plus`(목록을 새로 받아 정리)가, 네이버 다운로드 시작 때는 저장된 목록만으로(네트워크 없이) 돕니다. 테스트: `tests/backend/test_daily_plus_discovery.py`.
- **알림에서도 뺍니다**: 리포트의 미등록 새 에피소드 후보(`scheduler._collect_unregistered_new_episodes`)와 구독해제 새 에피소드 확인(`new_episode_watch.scan_naver`)도 `daily_plus.current_ids`에 있는 작품을 건너뜁니다(목록을 모르면 거르지 않음). 구독해제/제외됨의 정보 갱신(`refresh_inactive_metadata`)은 눈에 보이는 영향이 없어 그대로 둡니다. 테스트: `tests/backend/test_daily_plus_alerts.py`.

## 스케줄러 지연 허용 시간 (`misfire_grace_time`)

- APScheduler 기본값은 1초라서, 앱이 다른 일로 1초만 바빠도 그 회차가 `Run time of job ... was missed by 0:00:01.x`로 **실행되지 않고 버려집니다**(실제로 06:50 다운로드가 이렇게 건너뛰어졌습니다). 그래서 `create_scheduler()`에서 `job_defaults`로 `misfire_grace_time=600`(10분)과 `coalesce=True`를 줍니다. 10분 안으로 늦은 실행은 그대로 실행하고, 여러 번 밀린 실행은 한 번으로 합칩니다. 앱이 잠깐 내려갔다 10분 안에 올라온 경우에도 놓친 스케줄이 한 번 실행됩니다.
- 스케줄대로 실제로 돌았는지는 **실행 이력**(이력 탭)으로 확인합니다. 작업이 한 번 돌면 받은 게 없어도 기록되므로, 기록이 없으면 그 회차는 실행되지 않은 것입니다(다운로드 이력은 실제로 받은 회차가 있을 때만 남습니다). 로그에서는 `Running job ...` 줄이 있는지, 또는 `was missed by` 경고가 있는지 보면 됩니다.

## 수동 다운로드: 다시 분석 버튼과 새로고침 시 설정 경쟁

- **다시 분석**: 분석 결과 제목 옆(구독 버튼, 작품 페이지 버튼 오른쪽)의 `btn-kakao-manual-reanalyze`/`btn-manual-reanalyze`가 `bindReanalyzeButton`(`50-manual-download.js`) 한 곳에서 같은 방식으로 동작합니다. 지금 보고 있는 작품(`kakaoManualAnalysis.series_id`/`manualAnalyzeResult.title_id`)으로 기존 분석 함수(`runKakaoManualAnalyze`/`runManualAnalyze`)를 다시 부르고, 분석하는 동안은 `분석 중…`으로 비활성화해서 연타해도 요청이 겹치지 않습니다. 분석 함수가 실패해도 스스로 알림 창을 띄우고 끝나므로 버튼은 항상 원래대로 돌아옵니다. 테스트: `tests/js/test_manual_reanalyze.js`.
- **새로고침 시 플랫폼 선택이 안 나오던 문제**: 새로고침하면 `99-main.js`가 마지막에 보던 탭(`sessionStorage`)을 즉시 복원하는데, `kakaoWebtoonsEnabled`는 서버에서 비동기로 가져오므로 복원 시점에는 아직 `false`라서 수동 다운로드의 플랫폼(네이버/카카오페이지) 선택이 숨겨진 채로 그려졌고, 값이 도착한 뒤에는 다시 맞춰 주는 곳이 없었습니다(탭을 다시 눌러야 나왔음). 그래서 이 값은 항상 `setKakaoWebtoonsEnabled(enabled)`(`00-core.js`)로 바꾸고, 값이 정해지거나 바뀔 때 `kakao-webtoons-enabled-changed` 이벤트가 나갑니다. 이 값에 기대는 화면은 이 이벤트를 들어서 다시 맞추면 됩니다(수동 다운로드의 `syncManualPlatformSelect`가 그렇게 합니다). 테스트: `tests/js/test_manual_platform_select_startup.js`. 같은 방식의 경쟁이 있을 수 있는 다른 화면(전체목록의 카카오 표시, 설정의 스케줄 대상 선택 등)은 아직 이 이벤트를 듣지 않습니다.

## GitHub Actions 러너 고정

- 워크플로의 `runs-on`은 `ubuntu-24.04`로 고정합니다. `ubuntu-latest`는 2026-10-19부터 몇 주에 걸쳐 Ubuntu 26.04로 바뀌어서, 테스트 → 이미지 빌드 → 자동 갱신으로 이어지는 배포 파이프라인이 예고 없이 깨질 수 있기 때문입니다(`actionlint` 같은 러너에 미리 설치된 도구나 `setup-python`/`setup-node`의 새 OS 지원 여부에 기대고 있습니다). 26.04로 옮길 때는 `ubuntu-26.04`로 먼저 한 번 돌려 본 뒤 값을 바꾸세요. `tests/backend/test_workflows_pinned.py`가 모든 `runs-on`이 같은 고정 버전인지 검사합니다.

## 신작 스캔의 구독 규칙과 "제외됨으로" 버튼

- **구독 이력(`ever_subscribed`)이 제외됨의 두 종류를 가릅니다.** `set_status(active)`가 `ever_subscribed`를 1로 세우고 이후 다른 상태로 바뀌어도 풀리지 않으므로, 제외됨 + `ever_subscribed=0`은 "구독하지 않은 채 목록제외로 보낸 것"(`/api/naver-list/{id}/exclude`가 새 행을 `mark_ever_subscribed=False`로 만든다), 제외됨 + `ever_subscribed=1`은 "구독했다가(또는 구독 중에) 제외한 것"입니다. 판정은 `tracker._is_exclusion_without_history` 한 곳입니다.
- **네이버**(`_add_discovered_titles` — 구독 작가/미구독 관심 작가 두 경로가 공유, `scan_curation_tags` — 태그): DB에 없으면 새로 구독하고, 제외됨 + 이력 없음이면 `set_status(active)`로 다시 구독하며(알림에 "제외됨에서 구독"), 그 밖의 상태(구독 중/구독해제/목록/이력 있는 제외됨)는 건드리지 않습니다. 완결작(`_is_other_finished`/태그 항목의 `finished`)과 매일+, 등록 작가 역할이 안 맞는 작품은 어느 경우에도 구독하지 않습니다.
- **카카오**(`scan_kakao_authors_for_new_titles`): 같은 규칙입니다. 완결 여부는 작가 검색 결과(`kakao_api.search_by_author`)의 `is_finished`(`on_issue == "N"`)를 씁니다. 제외됨 + 이력 없음 + 완결 아님은 **이미 본 작품이어도, 처음 스캔하는 작가여도** 구독하고(기준선은 앱에 없는 작품의 대량 추가를 막으려는 것이라 이 경우와 무관), 앱에 없는 새 완결작은 추가하지 않되 "본 작품"으로는 기록합니다.
- **"제외됨으로" 버튼**(`20-cards.js`의 구독 중 카드 → `excludeSubscribedWebtoon`, `30-naver-list.js`): 확인 창을 거쳐 기존 제외 API(`/api/naver-list/{id}/exclude`, `/api/kakao-webtoons/{id}/exclude`)를 부릅니다. 서버 변경은 없습니다 — 구독 중 작품에도 제외 API가 동작하고 이력은 유지됩니다. 사용자가 제외됨 작품을 직접 다시 구독하면(`subscribe` API) 상태가 active가 되어 제외됨 상태가 사라지고, 다시 제외하면 이력이 있으므로 영구 제외입니다. 테스트: `tests/backend/test_resubscribe_excluded.py`, `tests/js/test_exclude_subscribed_button.js`.

## 수동 다운로드: 표지·정보 받기 (브라우저 다운로드)

- 분석 결과 제목 옆(`다시 분석` 오른쪽)의 `btn-manual-metadata`/`btn-kakao-manual-metadata`가 `bindMetadataZipButton`(`50-manual-download.js`) 한 곳에서 같은 방식으로 동작합니다. `fetch`로 zip을 받아 blob → 임시 주소 → 앵커 클릭으로 브라우저 다운로드를 시작하고(파일 이름은 `Content-Disposition`의 `filename*=UTF-8''…`에서 읽음), 받는 동안은 `받는 중…`으로 비활성화합니다. 임시 주소는 바로 지우면 일부 브라우저에서 다운로드가 끊겨서 100ms 뒤에 지웁니다. 링크 이동 방식 대신 `fetch`를 쓰는 이유는 실패(404 등)를 안내 창으로 보여 주고 `X-Metadata-Warning`을 읽으려는 것입니다.
- **서버는 아무것도 쓰지 않습니다**: `GET /api/manual-download/metadata-zip?title_id=`(네이버, `manual_download.build_metadata_bundle`)와 `GET /api/kakao-manual/metadata-zip?series_id=`(카카오페이지, `kakao_page_download.build_series_metadata_bundle`)가 폴더에 저장하던 것과 같은 내용을 메모리에서 만들어 `metadata_zip.MetadataBundle`에 담고, `api/common.metadata_zip_response`가 zip 응답으로 보냅니다. 폴더에 쓰는 `write_comicinfo_file`/`write_info_xml`을 부르지 않는 이유는 카카오의 `write_info_xml`이 작가 정보를 DB에 저장하는 부수 효과가 있기 때문입니다(그래서 `build_*_comicinfo_xml`을 직접 씁니다). 네이버 표지 받기는 `comicinfo.fetch_cover_image`(확장자, 바이트)로 분리해서 폴더에 저장하는 `download_cover_image`와 공유합니다.
- 표지를 못 받으면 `info.xml`만 담은 zip을 보내고 `X-Metadata-Warning: cover-missing` 헤더로 알립니다(헤더라서 ASCII 코드). 작품 정보를 못 받으면 404입니다. 카카오는 `client_or_anonymous`라서 로그인 쿠키가 없어도 됩니다. 테스트: `tests/backend/test_metadata_zip_naver.py`, `test_metadata_zip_kakao.py`, `tests/js/test_metadata_zip_button.js`.

## 전체목록 카드: 뷰어 아이콘 자리 유지

- 뷰어 서버를 설정하면 구독 중 카드에 "뷰어에서 보기" 아이콘이 붙고, 렌더링 뒤에 실제로 뷰어에 있는지 확인해서(`pruneMissingViewerIcons`) 없는 작품은 숨깁니다. 예전에는 아이콘을 **지워서** 카드마다 버튼 줄 수가 2~3줄로 달라졌고, 카드 안에서 버튼이 아래쪽 정렬이라 "구독해제" 버튼이 카드마다 위아래로 어긋났습니다. 지금은 `hideViewerIcon`이 아이콘을 지우지 않고 자리만 남긴 채(`visibility: hidden`, 비활성, `aria-hidden`) 숨겨서 모든 카드의 버튼 줄 수가 같습니다 — 같은 줄의 카드는 높이가 같고 버튼이 같은 줄 수만큼 아래에서 쌓이므로 "구독해제"가 같은 줄에 정렬됩니다. 뷰어 서버가 없으면 아이콘이 처음부터 없어서 모든 카드가 같은 줄 수입니다. 테스트: `tests/js/test_viewer_icon_slot.js`.

## 다운로드 속도 조절 (`app/download_pacing.py`)

- 자동 다운로드는 **작품을 한 개씩 차례로** 받습니다(네이버 `for webtoon in active_webtoons`, 카카오 `for webtoon in subscribed` — 둘 다 순차). 그래서 한 작품이 상한(`max_new_episodes_per_title`)에 걸려 `batch_rest_minutes`를 쉬는 동안 **모든 작품이 함께 멈춥니다.** 이 쉬는 시간이 사이트에 대한 전체 냉각 시간 역할을 하므로, 쉬는 동안 다른 작품을 먼저 받는 방식은 요청이 끊김 없이 이어져 쉬는 의미가 없어서 택하지 않았습니다(검토 후 보류).
- 값은 `download_pacing.limits(settings)`가 돌려줍니다: 설정 화면에서 저장한 값(DB `settings`의 `download_max_new_episodes_per_title`, `download_batch_rest_minutes`)이 있으면 그 값, 없으면 코드 기본값(`Settings`의 `ClassVar`). 배치마다 다시 읽어서 재시작 없이 다음 배치부터 적용되고(`scheduler._download_pending_batches`, `_download_kakao_title`), DB에 범위 밖이거나 숫자가 아닌 값이 있으면 그 항목만 기본값으로 돌아갑니다. 범위는 회차 0~200(0=제한 없음), 쉬는 시간 0.5~120분이고 저장 전 검사는 `download_pacing.validate`(서버, 400)와 `validateDownloadPacingInput`(화면) 두 곳이 같은 범위를 씁니다.
- API: `GET/POST/DELETE /api/settings/download-pacing`(DELETE = 기본값으로). 화면은 설정 탭의 "다운로드 속도 조절" 카드(`72-settings-jobs.js`). 테스트: `tests/backend/test_download_pacing.py`, `tests/js/test_download_pacing_settings.js`.

## 옵션 관리 원칙 (숨겨진 옵션을 두지 않는다)

- **환경변수(또는 작업 폴더의 `.env`)로 바꿀 수 있는 것은 `app/config.py`의 `Settings` 필드 6개(경로)뿐입니다**: `DOWNLOAD_ROOT`, `WEBTOON_DOWNLOAD_HOST_PATH`, `ARCHIVE_ROOT`, `RCLONE_CONFIG_PATH`, `DATABASE_PATH`, `COOKIE_FILE_PATH`. 그 밖의 값(`delay_seconds`, `request_timeout_seconds`, `max_concurrent_downloads`, `artist_scan_concurrency`, `folder_zero_fill`, `image_zero_fill`, 속도 조절의 기본값 `max_new_episodes_per_title`/`batch_rest_minutes`)은 `Settings`의 **`ClassVar`**라서 pydantic 필드가 아니고 환경변수를 읽지 않는 고정값입니다. 호출부는 그대로 `settings.delay_seconds`처럼 읽습니다(테스트가 `SimpleNamespace`로 값을 바꿔 넣는 방식도 그대로 동작).
- 새 옵션을 환경변수로 추가하려면 `Settings`에 필드를 두고 **README의 "모든 설정 옵션"과 `docker-compose.yml`(머리말의 변수 목록과 `environment`)에 같이 적어야 합니다** — `tests/backend/test_config_options.py`가 (1) 필드가 경로 6개뿐인지, (2) 필드마다 README와 compose `environment`에 있는지, (3) compose의 `${변수}`가 머리말 목록과 README에 있는지, (4) 고정값이 환경변수를 줘도 바뀌지 않는지를 검사해서 문서가 빠지면 실패합니다. 사용자가 만지는 값은 환경변수가 아니라 설정 화면(DB `settings`)에 두는 것이 원칙입니다(예: `download_pacing`).
- **지운 환경변수**(`REMOVED_ENV_VARIABLES`: `MAX_NEW_EPISODES_PER_TITLE`, `BATCH_REST_MINUTES`, `DELAY_SECONDS`, `REQUEST_TIMEOUT_SECONDS`, `MAX_CONCURRENT_DOWNLOADS`, `ARTIST_SCAN_CONCURRENCY`, `FOLDER_ZERO_FILL`, `IMAGE_ZERO_FILL`)를 환경변수나 `.env`에 지정해 둔 배포는 값이 조용히 무시되지 않도록 `main.lifespan`이 시작할 때 `config.warn_removed_env_variables`로 경고를 한 번 남깁니다(`.env`는 `KEY=VALUE` 줄의 이름만 봅니다). 지운 이름도 README "더 이상 쓰이지 않는 환경변수"에 남깁니다.
- DB `settings`에 있는 값 중 설정 화면에 없는 것은 옵션이 아니라 **앱 내부 상태**입니다(알림을 이미 보냈는지, 마이그레이션을 마쳤는지, 매일+ 목록 캐시, 리포트 마지막 발송 시각 등).

## 네이버 핵심 경로 특성화 테스트

- `tests/backend/test_naver_core_api.py`(`naver_api.py`), `test_naver_core_downloader.py`(`downloader.py`), `test_naver_core_zipper.py`(`zipper.py`), `test_naver_core_folder_scanner.py`(`folder_scanner.py`)는 **현재 동작을 그대로 고정하는 특성화 테스트**입니다(이 모듈들의 커버리지는 20~25%에서 약 100%로 올랐습니다). 네이버를 호출하는 쪽은 `tests/backend/fake_naver_http.py`의 `FakeSession(handler)`를 쓰는데, `handler(url, params, attempt)`가 응답 또는 예외를 돌려주며 `attempt`(같은 요청의 몇 번째 시도인지)로 "처음엔 실패하고 다시 하면 성공" 같은 재시도를 만듭니다. 가짜 응답은 코드가 문서로 남긴 실제 응답 모양(HAR 캡처 기반)을 따르므로 **네이버가 응답 형식을 바꿨는지는 잡지 못합니다**(그건 실서버에서만 확인됩니다).
- 일부러 [현재 동작]으로 고정한 것(바꾸려면 테스트도 함께 의도적으로 바꿔야 합니다): 회차 목록의 뒤쪽 페이지 하나가 끝내 실패해도 오류 없이 나머지만 돌려줌(`fetch_all_episodes`), 이미지 받기가 HTTP 오류로 끝내 실패해도 원인을 로그에 남기지 않음(`_download_single_image`는 예외일 때만 기록).
- 새 테스트가 실제로 동작 변경을 잡는지 확인하려고 운영 코드를 일시적으로 바꾸는 변형 검사를 10가지로 해 보았고(정렬 기준, 무료 회차 중단, 재시도 간격, 실패 폴더 삭제, 동시 받기 상한, `Retry-After`, 압축 번호 이어붙이기, 원본 폴더 삭제, 최신부터 검사, 일련번호 정렬) 모두 테스트가 실패로 잡았습니다.

## 대여 만료 판정, 번호순 처리, 받은 기록 고치기 (도굴왕 사건 이후)

- **대여 만료일 형식**: 실제 서버(`기다무.har`, `tests/fixtures/kakao_waitfree_reopen.json`)는 `rent_expire_dt`를 ISO 8601 `2026-10-12T01:46:22+09:00`로 줍니다. 예전 `_is_accessible`은 `"%Y-%m-%d %H:%M:%S"`만 읽고 **못 읽으면 "대여 중"(True)** 으로 봐서 만료된 기다무 대여가 영영 `보유`로 보였고, 받기 실패 → "여기서 멈춥니다"로 작품이 막혔습니다(기존 테스트가 실제와 다른 형식을 써서 못 잡았음). 지금은 `_parse_expire`(`datetime.fromisoformat` + KST 변환)가 ISO/예전 형식/초 없는 형식을 읽고, **못 읽으면 받을 수 없는 회차로 보고** 모양(숫자를 9로 바꾼 것)당 한 번 경고를 남깁니다(`_warned_expire_formats`). 만료된 대여를 기다무로 다시 여는 요청은 새 회차를 여는 것과 같습니다(`ticket/ready_to_use` → `RAT3`/`available.RT05` → `ticket/use` `RT05` → `RAT2`, 3일 대여) — 그래서 만료 회차는 잠금(기다무 가능)이 되어 `_download_first_locked_with_waitfree`가 그대로 엽니다.
- **번호순 처리**(`run_download`): 후보 = 받을 수 있는 회차 + 기다무 대상(`plan.locked[0]`, 기다무 지원 작품)을 번호순으로 정렬해 차례로 처리합니다. 기다무 대상은 자기 번호 차례에 열고(못 열면 건너뜀), 열린 회차는 받고, 받기에 실패하면 거기서 멈춥니다(이전과 같음). 예전에는 열린 회차를 모두 받은 뒤 기다무를 썼습니다. 대여권 단계(건너뛰기 방식)는 그대로입니다. 작품당 상한(`max_episodes`)도 번호순으로 셉니다.
- **실패 원인**: `KakaoPageClient.last_failure`(이미지 목록 HTTP 상태/빈 응답의 `result_code`, 이미지 받기 상태)를 `download_episode` 시작에서 지우고, 받기 실패 로그 줄 뒤에 ` · 원인: …`으로 붙입니다(`_failure_note`). 이미지 목록 요청의 HTTP 오류는 예전에는 아무것도 기록하지 않았습니다.
- **받은 기록 고치기**: 카카오는 `kakao_records.mark`(`POST /api/kakao-manual/record`)가 `downloaded_numbers`(받은 회차 기록)와 새 컬럼 `kakao_webtoons.pending_numbers`("받지 않은 것으로" 기록한 회차, 시작 지점보다 앞이어도 받을 대상 — `plan_by_folder_rules(pending=…)`가 `skipped` 판정에서 뺌, 받으면 `download_episode`가 지움)를 고칩니다. 폴더에 파일이 있는 회차는 `ignored_in_folder`로 돌려주고 안 바꿉니다. 네이버는 `manual_download.mark_record`(`POST /api/manual-download/record`)가 `last_downloaded_no` 한 값만 고칩니다(받은 것으로 = 고른 최댓값, 받지 않은 것으로 = 고른 최솟값 − 1). 스케줄러는 매번 `max(DB, 폴더 마지막)`을 쓰므로 폴더에 그보다 뒤까지 파일이 있으면 되돌아오고, 그 경우 응답의 `warning`으로 알립니다. 둘 다 구독 중(`active`)만 허용하고, 네이버 분석 응답에는 기록으로만 받은 회차를 `recorded`로 구분해 줍니다. 화면은 구독 중일 때만 기록 버튼을 보이고(아래 "툴바" 참고), 카카오는 구독 중이면 잠긴 회차 체크박스를 켜되 `data-selectable="0"`으로 표시해 전체선택/다운로드에서는 뺍니다. 새 컬럼은 백업(`_KAKAO_WEBTOON_COLUMNS`)과 마이그레이션(`db.py`)에 들어 있고, 구버전 백업(컬럼 없음)은 그대로 복원됩니다. 테스트: `test_kp_expiry_real_format.py`, `test_kp_sequential_order.py`, `test_kp_failure_reason.py`, `test_manual_record_kakao.py`, `test_manual_record_naver.py`, `tests/js/test_manual_record_ui.js`.

## 수동 다운로드 툴바와 전체목록 카드 버튼 정렬

- **툴바(한 줄)**: 카카오는 `btn-kakao-manual-select-all` · `select-missing` · `record-select-all` · `select-none` · `download` · `record-done` · `record-undone` 일곱 개, 네이버는 `btn-manual-select-all` · `select-missing` · `select-none` · `download` · `record-done` · `record-undone` 여섯 개가 한 컨테이너(`.manual-actions`)에 이 순서로 있습니다. 처음에는 앞의 두세 개만 보이고(`refreshKakaoManualSelectionActions`/`refreshManualSelectionActions`가 `hidden`을 토글), **체크박스가 하나라도 켜지면**(표 `change` 이벤트, 또는 선택 버튼이 값을 바꾼 뒤 직접 호출) 나머지가 나타납니다. 기록 버튼과 `기록용 전체선택`은 구독 중(`subscription === "active"`)일 때만 보입니다. 버튼 줄 바로 아래의 `.manual-actions-hint`가 처음 버튼들의 안내이고, 숨겨진 버튼의 안내(`kakao-hint-select-missing`, `kakao-hint-record-select-all`)는 같이 숨깁니다. 네이버에는 "전체선택"이 이미 기록할 수 있는 회차를 모두 고르므로 `기록용 전체선택`이 없습니다. 테스트: `tests/js/test_manual_toolbar_layout.js`.
- **전체목록 카드 정렬**: 같은 목록에 구독 중 카드(구독해제 / 제외됨으로 / 뷰어 아이콘 3줄)와 구독하지 않은 카드(구독 / 목록제외 2줄)가 섞여 있어서 카드 아래쪽 정렬에서 버튼이 한 줄씩 어긋났습니다. **지금 보이는 목록 기준**으로 `syncViewerRows(grid)`가 맞춥니다: 뷰어 버튼이 실제로 보이는 카드가 하나라도 있으면 모든 카드를 3줄로 맞추고(아이콘이 없는 카드에는 `makeViewerSlotPlaceholder`의 보이지 않는 자리, 뷰어에 없는 작품의 숨긴 아이콘은 그대로 자리), 하나도 없으면 모두 2줄로 둡니다(자리를 지우고 숨긴 아이콘은 `display:none`) — "아직 미등록만"처럼 뷰어 버튼이 필요 없는 목록이나 뷰어 서버가 없을 때 쓸데없는 빈 줄이 생기지 않게 하려는 것입니다. 카드를 그린 직후(`renderNaverList`, 필터를 바꿀 때마다)와 뷰어 확인(`pruneMissingViewerIcons`)이 끝난 뒤에 다시 맞춥니다. 자리는 `data-viewer-slot`만 있고 `data-viewer-check-title`이 없어서 뷰어 확인 대상이 아닙니다. 테스트: `tests/js/test_card_action_alignment.js`.

## 의존성 업데이트 (코드 정리 2단계)

- **왜**: 2026-10-13 `pip-audit`에서 고정 버전 7개에 알려진 취약점이 있었습니다(aiohttp 68건, Pillow 33건, starlette 14건, cryptography 10건, requests 4건, lxml 2건, Markdown 2건). 새 고정 버전 조합은 전이 의존성 51개 전체에서 **0건**입니다.
- **바뀐 고정 버전**(`requirements.txt`): fastapi 0.115.0 → 0.143.0, uvicorn[standard] 0.30.6 → 0.54.0, aiohttp 3.10.5 → 3.14.5, aiofiles 24.1.0 → 25.1.0, beautifulsoup4 4.12.3 → 4.15.0, lxml 5.3.0 → 6.1.3, requests 2.32.3 → 2.34.2, Pillow 10.4.0 → 12.3.0, APScheduler 3.10.4 → 3.11.3, tzdata 2024.2 → 2026.5, pydantic 2.9.2 → 2.14.1, pydantic-settings 2.5.2 → 2.15.0, discord.py 2.4.0 → 2.7.1, cryptography 43.0.1 → 50.0.2, Markdown 3.7 → 3.11. **starlette 1.7.0은 새로 직접 고정했습니다** — fastapi의 전이 의존성인데 보안 공지가 가장 많은 쪽이라 버전이 흔들리지 않게 합니다. 개발용(`requirements-dev.txt`)은 그대로입니다.
- **확인한 것**: 깨끗한 가상환경에 `requirements-dev.txt`를 새로 설치해 `pip check` 통과, 백엔드 테스트 72개와 화면 테스트 27개 통과, `pyflakes` 깨끗, 실제 서버를 띄워 시작과 주요 화면/API(`/`, `/api/jobs/status`, `/api/settings/*`, 정적 파일 `no-cache`)가 200으로 응답하는 것을 확인했습니다. 코드 수정은 필요 없었습니다. 한계: 가짜 서버 기반 테스트라 실제 네이버·카카오 서버와의 동작은 배포 후 로그로 확인해야 합니다(네이버 핵심 경로는 1단계에서 보강한 특성화 테스트가 지킵니다).
- **알려진 경고**: `discord.py`가 Python 3.12에서 `audioop` 지원 중단 경고를 냅니다. 기본 이미지(`python:3.12-slim`)를 3.13 이상으로 올릴 때만 문제이고(그때는 `audioop-lts` 필요), 지금은 영향이 없습니다.
- **되돌리기**: 문제가 생기면 `requirements.txt`를 이전 고정 버전(위 "→" 왼쪽 값, starlette 줄 삭제)으로 되돌려 다시 빌드합니다.
- **다시 점검하는 법**: 새 가상환경에 설치한 뒤 `pip freeze > freeze.txt; pip-audit -r freeze.txt`. 주기적으로(분기마다 등) 돌려서 새 공지가 있으면 같은 순서(가상환경 설치 → 전체 테스트 → 서버 시작 확인)로 올립니다.

## 모듈 구조 (코드 정리 3단계)

너무 커진 세 모듈(`kakao_page_download.py` 1042줄, `repository.py` 1312줄, `scheduler.py` 1121줄)을 같은 이름의 **패키지**로 바꿔서 역할별로 나눴습니다. `from app import repository`, `repository.get(...)`, `scheduler.create_scheduler()`처럼 **부르는 쪽은 그대로**입니다(`__init__.py`가 바깥에서 쓰는 이름을 다시 내보냅니다). 정의는 소스 그대로 옮겼고(동작 변경 없음) import는 도구로 만든 뒤 `pyflakes`로 확인했습니다. 의존 방향은 아래쪽이 위쪽을 모릅니다(순환 import 없음).

- **`app/kakao_page_download/`**: `models.py`(상수, 데이터 모양, 폴더 규칙으로 받을 회차 정하기, 이름·만료 판정, 기다무/대여권 대상 판정 — 서버 통신과 실행이 없는 부분) → `client.py`(`KakaoPageClient`: 쿠키, 재시도, 회차 목록, 이용권, 이미지) → `runner.py`(회차 받기, 자동 번호순/수동 다운로드, 기다무·대여권, info.xml/표지 쓰기). 순수한 판정 함수(`rental_eligible`, `free_date_of`, `waitfree_supported`)는 `runner`가 아니라 `models`에 있습니다(`models`가 `runner`를 가져오면 순환 import).
- **`app/repository/`**: `common.py`(상태/출처 상수, 현재 시각) · `naver_webtoons.py` · `settings.py` · `watchlist.py`(관심 작가/태그) · `kakao_webtoons.py` · `archive.py`(대상, 파일 이름 틀 프리셋, 이력, 완결 후 대기) · `history.py`(실행 이력, 회차 이력) · `backup.py`. DB 접근 도우미(`fetchone` 등)는 `app.db`에서 직접 가져옵니다 — `repository.fetchone`은 더 이상 없습니다.
- **`app/scheduler/`**: `common.py`(작업별 기본 스케줄) · `naver_download.py` · `kakao_download.py` · `download_job.py`(네이버+카카오를 한 번에 돌리고 이력 정리/실패 알림) · `archive_job.py` · `report_job.py` · `discovery_job.py`(신작 스캔, 카카오 목록 갱신, 새로 완결된 작품 알림) · `schedule.py`(트리거 만들기, `create_scheduler`, `reschedule_all`).
- **테스트에서 내부 이름을 바꿔치기할 때(monkeypatch)**: 함수를 **실제로 부르는 모듈**의 이름을 바꿔야 합니다. 패키지(`scheduler`, `repository`, `kakao_page_download`)의 이름을 바꾸면 그 이름을 패키지를 거쳐 부르는 다른 모듈(API, 스케줄러 등)에는 효과가 있지만, **같은 하위 모듈 안에서 부르는 함수에는 효과가 없습니다**(예: `scheduler.download_job._run_download_job_impl`, `scheduler.naver_download.download_single_episode`, `kakao_page_download.runner.write_series_metadata`). 효과 없는 바꿔치기는 조용히 지나가서 테스트가 실제 코드를 돌리다 멈출 수 있으니(`test_kp_schedule`이 그랬습니다), 바꿔치기가 먹혔는지는 결과 단언으로 확인합니다. 전역 모듈의 속성(`asyncio.sleep`, `kakao_cover.fetch_official_cover_bytes`, `cookie_health.finalize_and_notify` 등)은 그 모듈을 직접 바꿉니다.

### 아카이빙(`app/archiver/`)도 같은 방식으로 나눴습니다

- `settings.py`(충돌 정책, 기본 경로/대상 종류, 파일 이름 틀, 완결 시 구독해제 후 아카이빙) · `naming.py`(zip 이름 해석, 이름 틀 렌더링, 미리보기) · `transfer.py`(로컬/rclone 목적지 정하기, 충돌 정책, 회차 파일 이동, 메타 파일 처리, 작품·보관 폴더 경로 도우미) · `title_archive.py`(작품 아카이빙: 정기/수동/완결 후) · `folder_archive.py`(폴더 대상 아카이빙과 일괄 이동) · `params.py`(아래 값 묶음, 다른 모듈을 가져오지 않는 맨 아래 계층). 순수한 경로 도우미(`_title_folder`, `_local_archive_path`, `_derive_folder_display_name`)는 순환 import를 피하려고 `transfer.py`에 있습니다.
- **인자 14~16개이던 함수를 값 묶음으로 바꿨습니다**: `_archive_title(job: TitleArchiveJob, report: ProgressReport | None)`, `_move_title_files(files, dest: TitleDestination, report)`, `_move_folder_files(rel_files, move: FolderMove, report)`. `ProgressReport`는 진행 문구/이름 충돌/이동 실패를 알리는 통로(셋 다 선택)라서, 함수 안에서 "지정했으면"을 매번 확인하던 분기가 `report.progress(...)`/`report.conflict(...)`/`report.failure(...)` 한 줄로 줄었습니다. 바깥에서 부르는 공개 함수(`run_periodic_archive`, `manual_archive_now`, `process_pending_finish_archives`, `bulk_move_folder`)의 인자는 그대로입니다. 테스트: `tests/backend/test_archiver_params.py`(값 묶음 규칙)와 기존 아카이빙 테스트.
- **테스트의 내부 이름 바꿔치기 위치**: `archiver.title_archive._move_episode_file` / `_get_effective_filename_template`, `archiver.folder_archive._bulk_move_single_file` / `_generic_copy_file` / `_download_kakao_cover_to_temp_file` / `get_conflict_policy` / `_get_effective_filename_template`처럼 **실제로 부르는 하위 모듈**을 바꿉니다(위 모듈 구조 설명의 규칙).

## 위생 정리 (코드 정리 4단계)

- **앱 코드 위생 검사(`tests/backend/test_code_hygiene.py`)**: `app/` 소스를 훑어서 (1) `except` 안에서 새 예외를 던질 때 `from e`로 원인을 잇지 않은 곳, (2) 넓은 `except`(Exception/BaseException/맨 except)가 로그 없이 `pass`/`continue`/`return None`으로 삼키는 곳, (3) 데코레이터 없는 모듈 함수에서 **쓰이지 않는 인자**, (4) `hashlib.sha1`/`md5` 사용을 실패로 칩니다. 이번에 고친 것: `raise … from e` 18곳(`rclone_client`, `archiver`, API 변환 등), 조용한 예외 3곳(`job_status`의 실행 이력 저장 실패, `discord_bot`의 오류 안내 전송 실패에 경고 로그, `_extract_web_url_from_xml_text`는 깨진 XML(`ParseError`)만 받도록 좁힘), `send_webhook_notification`의 쓰이지 않던 `settings` 인자(호출 13곳)와 그 때문에 연쇄로 남은 알림 함수 8개의 `settings` 인자 제거, 카카오 목록 변경 감지 해시를 SHA1에서 SHA256으로(첫 실행 때 목록 버전 문자열이 한 번 바뀝니다).
- **네이버 목록/이미지 (1단계 특성화 테스트에서 발견한 항목)**: ① `fetch_all_episodes`는 첫 페이지는 받았는데 **뒤쪽 페이지가 끝내 실패하면 `NaverApiError`**를 냅니다(예전에는 빠진 목록을 그대로 돌려줘서, 빠진 회차를 모른 채 더 큰 번호를 받으면 그 사이 회차가 영영 누락될 수 있었습니다). 첫 페이지 실패(성인 미인증 등)는 예전처럼 빈 목록입니다. 호출부: 자동 다운로드(`_plan_naver_download`)는 그 작품만 건너뛰고 작업 로그에 이유를 남기며(작업 전체로 오류가 올라가지 않음), 수동 분석 API는 502와 이유, 수동 다운로드는 작업 상태에 실패 이유를 남깁니다(`tests/backend/test_naver_list_failure.py`). ② 이미지 받기가 HTTP 오류로 끝내 실패하면 상태를 로그에 남깁니다(예전에는 예외일 때만). ③ `titleId`가 null/빈 값인 목록 항목은 "None"이라는 가짜 번호로 들어오지 않고 건너뜁니다. ④ `download_single_episode`의 설명이 실제 반환값(`(False, None)`, 만들던 폴더는 지움)과 맞도록 고쳤습니다.
- **`db.py`의 `fix_combined_kakao_authors()` / `fix_episode_history_platforms()`**는 앱 안에서는 쓰이지 않고 테스트(`test_kakao_author_roles.py`, `test_history_platform_fix.py`)와 수동 점검용입니다(시작할 때의 자동 보정은 `_fix_*`가 합니다). 일부러 둔 것이라 지우지 않고 여기에 적어 둡니다. 죽은 코드 점검(`vulture`)에서 나머지로 잡힌 것은 FastAPI 라우트·검증 함수와 응답 모델 필드(프레임워크가 부르는 것)라서 정리할 것이 없었습니다.

## 완결 이동 뒤 대상 설정 삭제, 아카이빙 탭 구성

- **대상 설정 자동 삭제**(`archiver.title_archive._delete_target_after_full_move`): 완결 전체 이동(`process_pending_finish_archives`의 구독해제 대기열, `manual_archive_now(full_move=True)` — 웹툰 대상과 폴더 대상 모두)이 **문제없이 끝나면**(① 이번 작품의 이동 실패가 없고 ② 웹툰 대상은 다운로드 쪽 폴더가 비어서 정리됨) `repository.delete_archive_target`로 그 대상의 아카이빙 설정을 지웁니다. 그러면 대상 목록과 아카이빙 수동 실행 목록(둘 다 `list_archive_targets()` 기반)에서 사라집니다. 일부 실패하거나 남의 파일이 남아 폴더가 안 지워졌거나(`_cleanup_empty_dirs`는 빈 폴더만 지움) 보관 위치가 없어 못 옮겼으면 설정을 남깁니다(대기열에서는 지금까지처럼 빠집니다). 대상 설정이 없는 웹툰(기본 그릇 폴더로 가는 경우)은 지울 것이 없고, 마지막 파일을 남기는 정기/수동 이동은 설정을 건드리지 않습니다. 폴더 대상은 이동 실패가 없으면 완료로 봅니다(`_scan_folder_source`/`_prepare_folder_dest` 오류도 실패로 기록됨). 테스트: `tests/backend/test_archive_target_cleanup.py`.
- **아카이빙 탭 구성**(`index.html`의 `#archive-main-content`): 아카이빙 대상 → 아카이빙 공통 설정(같은 이름 파일 처리 + 파일명 변경 설정) → 완결 자동이동 설정 → 폴더 일괄 이동. 같은 이름 파일 처리는 `<select>`에서 설명이 붙은 라디오 카드(`input[name="archive-conflict-policy"]`)로 바꿨고(`getArchiveConflictPolicy`/`setArchiveConflictPolicy`), 카드마다 자기 저장 버튼이 있습니다(`btn-save-archive-conflict`, `btn-save-archive-finish`, 파일명은 `btn-save-preset`). 예전에는 공통 설정 카드의 저장 버튼 하나가 완결 자동이동 카드의 값(켜기/기본 폴더)까지 같이 저장해서 헷갈렸습니다. 지금은 `saveArchiveSettings(overrides)`가 서버에 저장된 현재 값을 읽어 **바꾼 항목만 덮어써서** 보냅니다. 테스트: `tests/js/test_archive_settings_layout.js`.

## 보관할 폴더 즐겨찾기

- **저장**: 설정 테이블의 `archive_folder_favorites`(JSON 목록) — `repository.list_folder_favorites` / `add_folder_favorite` / `delete_folder_favorite`(`FOLDER_FAVORITES_KEY`, 최대 `MAX_FOLDER_FAVORITES`=30). 비밀/캐시 설정이 아니라서 설정 백업·복원에 함께 들어갑니다. 항목은 `{id, label, dest_type: "local"|"rclone", root, path}` — `root`는 로컬이면 루트 이름(`archive`/`download`/`kakao_download`), rclone이면 원격 이름. 같은 (종류, 루트, 경로)를 다시 추가하면 기존 것을 돌려줍니다(경로는 앞뒤/겹친 슬래시와 역슬래시를 정리해 같은 표기로, `..`는 거부). 이름을 비우면 `원격 · 폴더 이름`(로컬은 `로컬 · …`, 최상위는 `최상위`). 저장된 값이 깨져 있으면 빈 목록.
- **API**(`app/api/archive.py`): `GET/POST /api/archive/favorites`, `DELETE /api/archive/favorites/{id}`. 입력 검증 오류는 이 앱의 다른 API와 같이 422, 개수 초과는 400. 테스트: `tests/backend/test_archive_favorites.py`.
- **선택기**(`app/static/js/90-archive.js`): `renderFolderPicker`가 `options.favorites`(안 주면 `FAVORITE_PICKER_IDS` — 보관 쪽 선택기 `archive-target-folder-picker`, `archive-folder-target-dest-picker`, `archive-default-folder-picker`, `bulk-move-dest-picker`)로 켭니다. 켜진 선택기는 펼친 화면 맨 위에 `renderFolderFavoritesRow`가 버튼 줄(`.folder-favorites-row`)을 그립니다 — 지금 환경에서 못 여는 것(`rclone_available`이 아니거나, 로컬이 없거나 이 선택기의 `localRoots`에 없는 루트)은 뺍니다. 버튼은 선택기 상태(`mode`/`remote`/`localRoot`/`path`)를 그 폴더로 바꾸고 `modeConfirmed`/`rootConfirmed`를 켜서 로컬·rclone·루트·원격 고르기를 건너뛰고 목록 화면으로 갑니다. 목록 화면의 `makeFolderFavoriteStar`가 현재 위치를 추가/삭제하고, 즐겨찾기 목록은 `getArchiveFavoritesCached`로 캐시하며 변경할 때마다 `invalidateArchiveFavorites()`로 비웁니다. 새로 만드는 상태 객체에는 `favorites`를 같이 옮겨야 합니다(`skipExistingCheck`와 같은 방식). 테스트: `tests/js/test_archive_favorites_ui.js`.

## 아카이빙 탭 간격/색, 수동 완결 처리 뒤 목록 갱신

- **간격 겹침의 원인**: 전역 `.hint`는 "제목(h2) 바로 밑에 끼워 넣는" 용도라 `margin: -8px 0 16px`(위쪽 음수)입니다. 같은 클래스를 h3나 버튼 밑의 설명에 그대로 쓰면 제목/버튼과 겹칩니다. 그래서 `.archive-common-section .hint`는 `margin: 0 0 12px`로 되돌리고(`h3` 아래 8px), 저장 버튼과 결과 문구는 `.archive-save-row`로 묶어 그 밑 설명(`.archive-save-row + .hint`)에 위쪽 여백을 줍니다. 폴더 선택기는 즐겨찾기 줄 아래 16px, 현재 위치 줄 아래 8px. 즐겨찾기 버튼(`.folder-favorite-chip`)은 연한 초록 배경, 별 버튼(`.folder-favorite-star`)은 연한 노랑 배경입니다. 새 `.hint`를 h3/버튼 밑에 쓸 때는 이 음수 여백을 꼭 확인하세요. 테스트: `tests/js/test_archive_spacing_css.js`(CSS 규칙을 읽어서 확인).
- **수동 완결 처리 뒤 목록 갱신**(`92-archive-targets.js`의 `onArchiveJobFinished`): 아카이빙 작업이 `running`에서 끝나는 순간 한 번만 불립니다(`archiveWasRunning`). 이 화면에서 완결 처리(`full_move`)로 시작했으면(`archiveRunFullMove === true`) 끝난 뒤 아카이빙 수동 실행 목록(`loadArchiveManualSelectList(true)` — 남은 항목의 체크 유지), 아카이빙 대상 목록, 대상 등록용 웹툰 목록을 서버 기준으로 다시 읽습니다. 성공이면 "완결 처리로 이동" 체크와 경고를 풀고, 오류면 다시 시도하기 쉽게 그대로 둡니다. 화면을 다시 열어 이어서 본 실행(`null`)은 종류를 모르니 목록만 갱신하고, 일반 수동 이동(`false`)은 대상이 그대로라 아무것도 하지 않습니다. 진행 로그는 건드리지 않습니다. 테스트: `tests/js/test_archive_manual_finish_refresh.js`.

## 폴더가 비어도 받은 회차 기록 유지 (카카오)

- **문제**: 예전에는 작품 폴더에 파일이 하나도 없으면 기록을 무시하고(`plan_by_folder_rules`의 `new_folder`) 자동 다운로드 때 기록을 통째로 지웠습니다(`sync_before_planning`). 그래서 **전체를 아카이빙해서 폴더가 없어진 작품이 연재를 재개하면** 1화부터 다시 받으려 했고, 수동 다운로드에서 "받은 것으로 기록"을 눌러도 폴더에 파일이 없으면 분석에 반영되지 않았습니다(다음 자동 실행 때 지워지기도 했습니다).
- **지금**: ① 폴더가 비어도 기록이 있으면 `plan_by_folder_rules`가 `mode="recorded_only"`로 기록을 믿고(기록 회차는 `archived` = 분석의 "폴더에 없음", 시작 지점은 기록의 가장 앞 번호) 기록에 없는 회차만 받습니다. 기록이 없거나 비어 있으면 예전처럼 `new_folder`(처음부터). ② `sync_before_planning`은 폴더가 비어도 기록을 지우지 않습니다. ③ 폴더에 파일이 없으면 폴더 자체를 만들지 않고, 새 회차를 실제로 받을 때 `download_episode`가 만듭니다. ④ 처음부터 다시 받으려면 폴더를 비우는 대신 "받지 않은 것으로 기록"(`kakao_records.mark(..., False, ...)` — 기록에서 빼고 받을 대상으로 지정)을 씁니다.
- **기록이 믿을 수 있는 이유**: 날짜로 지워지는 것은 실행 이력·다운로드 이력·**아카이빙 이력**(`delete_*_history_older_than`) 세 가지뿐이고 `kakao_webtoons.downloaded_numbers`는 그 정리 대상이 아닙니다. 기록이 바뀌는 곳은 회차를 받을 때(`download_episode`), 처음 만들 때(`sync_before_planning`/`backfill_all` — 폴더 파일 + 아카이빙 이력), 사용자가 직접 고칠 때(`mark`), 그리고 아래 아카이빙 직전 확정입니다. 그래서 기록을 아카이빙 이력에 기대지 않도록 **아카이빙으로 옮기기 직전에 폴더의 회차(남길 파일 포함)를 기록에 확정**합니다: `title_archive._archive_title`이 카카오 작품(`kakao_<번호>`)이면 `kakao_records.remember_folder_episodes`를 부릅니다(기록이 없으면 이력으로 먼저 채운 뒤 더함, 보관 설정이 모자라 못 옮기는 경우엔 건드리지 않음). 이력이 지워진 뒤에도 기록이 남습니다. 폴더 대상(작품이 아닌 폴더)은 해당 없음.
- **네이버**는 "여기까지 받음"(`last_downloaded_no`) 한 번호를 DB에 저장하고 스케줄러가 `max(DB, 폴더)`를 쓰므로 원래 폴더 상태와 상관없이 유지됩니다.
- **화면**: 분석 요약에 `폴더는 비어 있지만 받은 회차 기록(N개)이 있어서, 기록에 없는 회차만 받습니다. 새 회차를 받을 때 폴더를 만듭니다.`가 나옵니다(`kakaoManualSummaryHtml`의 `recorded_only`). 테스트: `tests/backend/test_kp_record_empty_folder.py`, `tests/js/test_kakao_recorded_only_summary.js`, 그리고 `test_kp_record.py`의 1-4/3-3.

## 기다무 충전 시각에 맞춰 자동 다운로드 (카카오, 옵션)

- **저장**: `kakao_webtoons.waitfree_next_at`(UTC ISO, 새 컬럼 — 마이그레이션과 백업 컬럼에 들어 있음). 작품마다 서버가 알려 준 "다음 충전 시각" 절대값을 그대로 저장하므로 충전 주기가 3시간/1일/3일로 달라도 따로 계산하지 않고, 재시작해도 남습니다(메모리 타이머가 아님). `app/waitfree_sync.py`가 정규화(`normalize` — `+09:00` 등을 UTC로, 못 읽는 값은 기존 값 유지), `remember`/`forget`/`delay`, 받기 시작 시각 `due_at` = 충전 시각 + 작품마다 정해진 30~120초 지터(`crc32(작품|충전시각)`라 틱마다 바뀌지 않음), `is_waiting`/`due_webtoons`/`schedule_view`를 맡습니다. 옵션은 설정 `waitfree_sync_enabled`(`"1"`).
- **시각을 알게 되는 곳**(`kakao_page_download/runner._download_first_locked_with_waitfree`): 기다무로 열었을 때 `client.last_waitfree_charged_at`(`ticket/use` 응답의 `result.waitfree_charged_at`), 아직 충전 전이라는 `ticket_info`의 `waitfree_available_at`(`ticket/my`의 `charged_at`). 기다무로 열 잠긴 회차가 없거나 기다무로 못 여는 회차뿐이면 `run_download`가 지웁니다(`forget`).
- **정기 다운로드**: `run_download_job(target, scheduled)` — `scheduled`는 APScheduler 등록(`_apply_download_schedules`의 `kwargs`)만 `True`이고 수동 실행(API)은 `False`. 옵션이 켜졌고 `scheduled`이면 `_download_kakao_subscriptions(skip_waiting=True)`가 `is_waiting`인 작품(충전 시각 + 지터 전)을 건너뜁니다(로그: `충전 시각 전인 기다무 작품 N개는 건너뜁니다`). 이 함수는 `only`(작품 목록 지정)와 `now`(테스트용 시계)도 받고 `(작품, 결과)` 목록을 돌려줍니다.
- **시각 기반 다운로드**(`scheduler/waitfree_job.run_waitfree_due_job`): 옵션이 켜지면 `schedule.apply_waitfree_job`이 1분 간격 작업(`waitfree_job`, `max_instances=1`)을 등록하고 끄면 해제합니다(`create_scheduler`/`reschedule_all`/설정 API 모두 호출, 여러 번 불러도 하나). 틱은 저장된 시각만 보고(서버 요청 없음), 받을 작품이 없으면 작업 상태·이력도 만들지 않고, `_download_job_lock`이 잡혀 있으면(정기/수동 다운로드 중) 건너뜁니다. 받을 작품이 있으면 `job_status("download")`로 한 개씩(`_download_kakao_subscriptions(only=…)` — 기존 속도 조절·알림·완결 처리 그대로) 받은 뒤: 받고도 충전 시각이 그대로면 `RETRY_MINUTES`(10분), 오류/로그인 풀림/쿠키 없음이면 `ERROR_MINUTES`(30분) 뒤로 미룹니다(같은 작품을 연속으로 두드리지 않게).
- **API/화면**: `GET/POST /api/settings/waitfree-sync`(POST는 스케줄러 등록도 바로 반영, 스케줄러가 아직 없으면 시작할 때 반영), `GET /api/waitfree/schedule`(구독 중이고 시각을 아는 작품을 `due_at` 순으로). 설정 > 실행 스케줄 카드의 다운로드 블록 아래 체크박스(`waitfree-sync-toggle`, 바꾸면 즉시 저장·실패 시 되돌림)와 그 밑 "다운로드 예정" 목록(`waitfree-schedule-list`: 한국시간 시각, 제목, 남은 시간 `waitfreeRemainingText`)이며 옵션이 꺼져 있으면 목록을 읽지 않고, 보고 있는 동안 30초마다 갱신합니다. 테스트: `tests/backend/test_waitfree_sync.py`, `tests/js/test_waitfree_sync_ui.js`.

## 스케줄: 요일을 시각마다 선택

- **저장 형식**: `JobSchedule.cron_times`의 항목이 `{hour, minute, days}`(`days`는 `mon`~`sun`, 비어 있으면 매일)입니다. 예전의 스케줄 공통 `cron_days` 필드는 없앴고, 읽을 때(`schedule_config.from_dict` — 저장된 값과 API 입력 모두) `days`가 **없는**(None) 시각마다 공통 요일을 복사해서 동작이 그대로 유지됩니다(`days: []`처럼 명시한 빈 목록은 매일이라 그대로 둠). `cron_hour`/`cron_minute` 단일 시각 형식도 계속 읽습니다. `sanitized()`가 요일을 월~일 순서로, 잘못된 값과 중복을 빼고 정리합니다.
- **실행**: `scheduler/schedule._build_trigger`가 시각마다 `CronTrigger(day_of_week=…)`를 하나씩 만들어 `OrTrigger`로 묶습니다(`days` 없으면 `*`).
- **API**: `CronTimeIn.days`(None이면 옛 화면이 보낸 공통 `cron_days` 사용), `GET /api/settings` 응답에는 `cron_days`가 없고 시각마다 `days`가 들어 있습니다(`_schedule_dict`가 정리해서 내려보냄).
- **아카이빙 10분 간격 검사**(`_check_archive_gap_against`): 이제 한 주(월 00:00 기준, 10080분) 안의 실제 실행 순간끼리 비교합니다(`_week_minutes`) — 요일이 겹치지 않으면 허용하고, 자정을 넘어 다음 날 새벽/주를 넘어가는 경우(일 23:55 다운로드 → 월 00:02 아카이빙)도 잡습니다. 오류 메시지에는 시각과 요일이 같이 나옵니다.
- **화면**(`70-settings-schedule.js`): 시각 줄(`schedule-time-row`)마다 `schedule-day-chips`(월~일 `day-chip`)가 있고 `readScheduleControls`가 줄별로 `days`를 모읍니다. 새 시각 줄은 요일 해제(매일)로 시작합니다. 예전 `day-checkbox` 공통 줄은 없앴습니다. 테스트: `tests/backend/test_schedule_time_days.py`, `tests/js/test_schedule_time_days.js`.

## CI에서 간헐적으로 실패하던 동시 받기 테스트(test_naver_core_downloader)

- **증상**: `assert tracker.max == 2` 가 `동시 받기 상한(2)을 지켜야 한다: 1`로 실패(GitHub Actions에서만, 로컬에선 통과).
- **원인**: 이미지 하나를 받는 코드가 파일을 `aiofiles`로 쓰는데 이것이 실제 스레드풀에서 돌아서, 러너가 느리거나 스레드 순서가 밀리면 첫 이미지의 "열기→받기→닫기"가 끝난 뒤에야 둘째 이미지의 파일 열기가 끝난다. 예전 헬퍼는 `iter_chunked` 안에서만 동시 수를 세서 "동시에 1개"로 재어졌다. 코드(세마포어)는 맞고 **테스트가 스레드 타이밍에 기대고 있었다**. 둘째 파일 열기만 0.2초 늦추면 로컬에서도 같은 메시지로 재현된다.
- **수정**: `InflightTracker`가 요청의 `async with` 구간 전체를 세고, `rendezvous=2`를 주면 2개가 동시에 들어올 때까지 서로 기다린다(상한이 지켜지면 정확히 2로 만나고, 상한이 낮으면 만나지 못해 시간 초과로 실패 — 타이밍과 무관). 시간 초과로 실패한 요청은 카운트에서 빼서 숫자가 쌓여 오탐하지 않게 했다. 테스트에는 "둘째 파일 열기가 느린 환경" 시나리오를 넣어 두었다. 변형 검사: 상한 +1, 상한 1, 상한 없음 모두 실패로 잡힌다.
- **비슷한 위험 후보**(바꾸지는 않음): 실제 `asyncio.sleep(0.05)`로 작업 시작을 기다리는 `test_kp_schedule.py`(79·90·92줄), `test_kp_archive_kakao.py`(85줄), `test_kp_cache.py`(29줄). 느린 러너에서 간헐적으로 실패하면 "조건이 될 때까지 짧게 반복 확인"하는 방식으로 바꾼다.

## 다운로드 리포트: 다운로드가 끝나면 모아서 보내기 + 발송하지 않는 시간 + 뷰어 스캔

- **구조**: 예전의 "리포트 발송 시각" 스케줄(`report_job` 스케줄, `schedule_report_job` 설정, 설정 화면의 발송 시각 UI, 실패 즉시 알림)을 없앴다. 대신 `app/report_dispatch.py`(설정 `report_quiet_hours`/`report_batch_minutes`, 상태 `report_dispatch_state`)와 1분마다 도는 `scheduler.report_job.run_report_tick`(항상 등록되는 내부 작업 `report_tick`)이 움직인다. 시작할 때 `report_dispatch.start()`가 `job_status.add_finish_listener`로 작업 종료를 연결하고 옛 설정을 지운다.
- **흐름**: `download`/`manual` 작업이 `job_status.finish`로 끝나면 `on_job_finished` → `note_download_finished`(마지막 다운로드 시각 `last_download_at`, 발송 기준이 없으면 그 작업의 시작 시각을 `report_last_sent_at`으로). 틱: ① 마지막 다운로드 뒤 N분(`batch_minutes`)이 안 지났으면 대기(묶음 열림) → ② 지나면 묶음 확정(`cutoff` = 마지막 다운로드 시각) → ③ `(report_last_sent_at, cutoff]` 이력(`list_episode_history_window`, 이미 보고한 회차 제외)이 없거나 웹훅이 없으면 스캔/발송 없이 `finish_cycle` → ④ 뷰어 스캔이 준비돼 있으면(`viewer_scan.is_ready`) 시작/진행 확인(`advance`)하고 끝날 때까지 대기 → ⑤ `is_quiet(now)`이면 끝날 때까지 대기(스캔은 이미 끝나 있음) → ⑥ `_run_report_job_impl(until=cutoff)`로 발송(발송 기준 시각도 `cutoff`로) → `finish_cycle`. 상태를 바꿀 때는 `update_state`로 필요한 칸만 읽고-바꾸고-쓴다(다운로드가 끝났다는 기록을 덮어쓰지 않게).
- **묶음 다시 열기**(`note_download_finished`): 묶음이 확정됐어도 스캔이 **진행 중이 아니면**(시작 전, 또는 끝나고 발송하지 않는 시간을 기다리는 중) 새 다운로드가 끝났을 때 `cutoff`/스캔 상태를 비워 한 통으로 합친다(스캔을 다시 한다). 스캔이 진행 중이면 합치지 않고 그 묶음만 먼저 보낸 뒤 `finish_cycle`이 뒤의 다운로드를 다음 묶음으로 남긴다.
- **발송하지 않는 시간**: 항목 `{start_hour, start_minute, end_hour, end_minute, days}`(요일 비면 매일, 시작하는 날 기준). `_intervals`가 한 주(월 00:00 기준 10080분) 안의 구간으로 바꾸고(주를 넘으면 둘로 나눔) `parse_quiet_hours`가 시작=끝, 범위 밖 값, 모르는 요일, **겹침**(끝 시각은 포함하지 않아 맞닿는 것은 허용)을 `ValueError`로 알린다(API는 422). `is_quiet`는 한국시간 기준.
- **자동 발송은 받은 이력이 있을 때만**: `_run_report_job_impl(until=…)`은 이력이 없으면 새 에피소드 요약만 있어도 보내지 않는다(요약은 이력과 함께만 나간다). 수동 "리포트 테스트 발송"(`force_test`)은 예전 동작 그대로. 주의: `until` 없는 일반 발송 경로(`force_test=False`, `until=None`)와 "최초 실행" 분기는 이제 운영 코드에서 쓰이지 않고 기존 특성 테스트(`test_report_job.py` 등)만 고정하고 있다 — 정리하려면 그 테스트를 `until` 경로로 옮긴 뒤 이 분기를 지우면 된다.
- **뷰어 스캔**(`app/viewer_scan.py` 공용 조정 장치, `webtoon_server_client.start_scan/scan_status`): 약속은 "변경 시각 이후에 시작한 스캔이 끝나야 그 변경이 반영된다". `request(when)`이 변경을 기록하고(준비돼 있을 때만 — 켜짐+뷰어 주소+토큰, 이미 그 뒤에 시작한 스캔이 있으면 기록하지 않음), 1분마다 도는 `tick`이 **한 번에 상태 조회 한 번 + 시작 요청 한 번 이하**로 움직인다: ① 우리가 시작한 스캔이 돌면 끝났는지만 확인(끝나기 전엔 새로 시작하지 않음, `max_wait` 지나면 추적 중단) ② 끝났거나 없고 요청이 남아 있으면 시작 — `started`(시작함) / `running`(뷰어가 이미 스캔 중 → `foreign_wait`로 끝나기를 기다렸다 시작, 요청이 `max_wait` 넘게 걸리면 버림) / 그 외(연동 없음·인증 실패 401·403·404, 연결 오류)는 요청을 버리고 재시도하지 않는다. 스캔 도중에 생긴 새 변경(`requested_at` > `started_at`)은 그 스캔이 끝난 뒤 같은 틱에서 다시 시작한다. `covered_state(when)`은 `done`(그 뒤에 시작한 스캔이 끝남, `done_started_at`)/`waiting`/`failed`. 상태는 설정 `viewer_scan_state`(JSON)에 저장돼 재시작해도 이어진다. 토큰은 `crypto`로 암호화해 저장하고 응답에 돌려주지 않으며(`token_set`만), HTTP 헤더 값이라 ASCII만 허용한다. 설정 API: `GET/POST /api/settings/report`, `GET/POST /api/settings/viewer-scan`.
- **스캔을 요청하는 곳**: ① 다운로드(자동/수동/충전 시각 모두) — `run_report_tick`이 묶음을 확정할 때 `_request_scan_for_batch`가 `(워터마크, cutoff]` 구간에 **성공한 회차**가 있으면 요청한다(리포트/웹훅과 상관없이, 실패만 있으면 요청 안 함, 워터마크는 `viewer_scan_state.watermark`로 같은 이력을 다시 따지지 않게). 리포트를 보낼 때는 `covered_state(cutoff)`가 `waiting`이면 최대 대기 안에서 기다린다. ② 자동 아카이빙(`run_archive_job`: 정기 + 완결 대기열), 수동 아카이빙(`/api/archive/run`), 폴더 일괄 이동(`/api/archive/bulk-move`)이 파일을 하나라도 옮겼으면 `viewer_scan.note_library_changed(옮긴 개수)`로 즉시 요청한다. 리포트 틱은 한 번의 확인에서 조정 장치를 한 번만 진행한다(`_advance_scan` — 상태 조회가 겹치지 않게). 테스트: `tests/backend/test_viewer_scan_requests.py`(조정 장치와 아카이빙/일괄 이동 연결), `test_report_dispatch.py`(다운로드 묶음과의 연동), `test_viewer_scan_settings.py`.
- **뷰어 쪽 변경**(webtoon-server): `INTEGRATION_TOKEN` 환경변수로 인증하는 `POST /api/integration/rescan`(스캔을 백그라운드로 시작하고 바로 반환, 이미 스캔 중이면 새로 시작하지 않음), `GET /api/integration/scan-status`, 로그인 게이트(`PROFILES_ENABLED`) 허용 목록 추가(토큰은 라우터에서 검사, 공유 프로필 링크로는 404). 스캔 자체를 한 번에 하나만 도는 공유 작업(`services._start_scan_task`)으로 바꿔 수동/자동/연동 스캔이 겹치면 합류하고 요청이 끊겨도 스캔은 계속된다.
- **화면**: `74-settings-report.js` — 발송하지 않는 시간 줄(`report-quiet-row`: 시작~끝 + 요일 칩 `quiet-day`), 모으는 시간, 뷰어 스캔 설정(토큰은 표시만, `clear_token`으로 지움). 오류(겹침 등)는 서버 메시지 그대로 보여 주고 입력은 유지한다. 테스트: `tests/backend/test_report_quiet_hours.py`, `test_report_dispatch.py`, `test_viewer_scan_settings.py`, `tests/js/test_report_settings_ui.js`.

## 설정 화면 구성(카드와 탭)

- 설정 탭의 카드 순서: **다운로드 · 실행 스케줄**(`settings-card-run`, 카드 안의 탭) → 카카오웹툰 관리(`settings-card-kakao`, 기다무 충전 시각 옵션 포함) → 디스코드 설정 → **연동 주소**(`settings-card-links`: 웹툰 뷰어 서버 주소 + 뷰어 스캔 설정 + 이 앱의 외부 접속 주소, 디스코드 웹훅 없이도 항상 보임) → 다운로드 리포트(`settings-card-report`, 웹훅이 있을 때만) → 나머지.
- **카드 안의 탭**(`75-settings-tabs.js`, 마크업 규칙): `.card-tabs`(`role="tablist"`) 안의 `.card-tab[data-tab]` 버튼과 같은 카드 안의 `.card-tab-panel[data-panel]`이 이름으로 짝을 이룬다. 탭에 `data-footer="이름"`이 있으면 그 탭이 선택됐을 때만 `.card-tab-footer[data-footer="이름"]`(여러 탭이 같이 쓰는 저장 줄)이 보인다 — 다운로드 · 실행 스케줄 카드에서는 신작 스캔/다운로드/아카이빙 탭이 하나의 저장 버튼(`btn-save-settings`, 세 스케줄을 한 번에 저장)을 같이 쓰고, 다운로드 폴더/속도 조절 탭은 각자의 저장 버튼을 쓴다. 숨겨진 패널도 DOM에 그대로 있어서 설정 불러오기(`loadSettingsPage`)와 저장은 보고 있는 탭과 상관없이 동작하고 불러와도 선택된 탭은 유지된다. 패널 아이디: `settings-panel-roots`, `settings-panel-pacing`(예전의 `settings-card-download-roots`/`settings-card-pacing`/`settings-card-schedule` 카드는 없다). 새 탭 카드를 만들 때는 같은 마크업 규칙을 쓰면 JS를 따로 만들 필요가 없다. 테스트: `tests/js/test_settings_card_tabs.js`.

## 연동 주소 카드 표시 조건, 목록 제외 확인 페이지

- **표시 조건**: `settings-card-links`(연동 주소: 웹툰 뷰어 서버 주소 + 이 앱의 외부 접속 주소)는 리포트 링크("바로가기"/"목록 제외")용 주소라서 `settings-card-report`와 같이 **디스코드 웹훅이 있을 때만** 보인다(`updateSettingsCardVisibility`). 그 안의 뷰어 스캔 설정(`.viewer-scan-box`)은 **저장된 뷰어 서버 주소가 있을 때만** 보인다(`showViewerScanBox` — 설정 화면을 열 때와 주소를 저장할 때 갱신, 입력만 하고 저장하기 전에는 바뀌지 않음). 화면에서 숨길 뿐 저장된 값은 그대로다.
- **"목록 제외" 확인 페이지**(`api/common._render_exclude_confirm_html`, 네이버/카카오 공용): 주로 디스코드 앱에서 눌러 모바일 브라우저로 열린다. `viewport` 메타가 없으면 데스크톱 폭(약 980px)으로 그린 뒤 줄여서 글자/버튼이 아주 작게 보이므로 메타를 넣고, 글자(제목 22px/본문 18px), 화면 폭을 꽉 채우는 버튼(`.btn`, 높이 56px)과 어두운 화면 설정(`prefers-color-scheme: dark`)을 지원한다. GET은 여전히 아무것도 바꾸지 않고 버튼의 POST로만 제외한다. **보안**: 이 앱은 외부에서 열 수 있어서, 주소의 `title` 값이나 경로의 작품 번호가 스크립트 블록 안에 그대로 들어가 `</script>`로 빠져나가 실행되던 구멍을 `_js_json`(JSON으로 만들고 `<`/`>`/`&`를 `\u` 이스케이프)으로 막았다(보이는 제목은 `html.escape`). 테스트: `tests/backend/test_exclude_confirm_page.py`(크기 기준, 구성, 동작, 스크립트 삽입 방어).

## 목록 화면: 요일별 탭, 플랫폼/태그 필터, 점진 렌더링

- **요일 데이터**(`app/weekdays.py`): 모든 목록 응답이 같은 형식의 `weekdays`(mon~sun, 월~일 순서)를 가진다. 네이버는 요일별 목록 API의 `MONDAY`~`SUNDAY`를 `from_naver`로 바꾼다(모르는 값 무시). **제외됨/구독해제 탭은 네이버를 부르지 않고 DB 기록으로 만들어서** 요일을 모르므로, 전체목록(`/api/naver-list`)을 불러올 때 `save_naver_map`이 `{작품 번호: [요일]}`을 설정 `naver_weekday_map`에 저장해 두고(요일 있는 작품만, **바뀐 게 있을 때만 쓴다**) `/api/webtoons`가 그 지도로 `weekdays`를 채운다 — 전체목록을 한 번도 안 열었으면 비어 있다(화면에서 "기타"). 카카오는 요일 탭 1~7(월~일)마다 나온 작품에 요일을 모아 붙이고(`kakao_api._fetch_weekday_catalog_uncached`) 카탈로그 캐시 형식을 2로 올려(`FORMAT_VERSION`) 요일 없는 예전 캐시는 버린다; `/api/kakao-list`와 `/api/kakao-webtoons`(상태별)가 카탈로그에서 `weekdays`를 채운다. 요일별 목록에 없는 작품(완결/장기 휴재)은 `[]`. 테스트: `tests/backend/test_list_weekdays.py`.
- **화면 공용 모듈**(`25-list-view.js`, 전체목록/제외됨/구독해제가 같이 쓴다): `WEEKDAY_TABS`(월~일/기타/전체), `weekdayKeyOf`(한국시간 기준, "오늘"은 `currentDate()`로 가져와 테스트에서 바꿀 수 있다), `activeWeekdayTab`(처음 쓸 때 오늘, 이후는 선택 유지 — 화면마다 따로), `weekdayTabMatches`/`weekdayTabCounts`/`renderWeekdayTabs`(탭 옆 숫자는 다른 필터 적용 뒤 기준, 선택한 탭이 가로 스크롤에 가려지지 않게 가운데로), `syncPlatformFilter`(카카오 관리를 끄면 플랫폼 필터를 숨기고 값도 비움). 검색 중에는 요일 탭을 무시하고 "전체"로 보이며(`query ? "all" : …`) 지우면 보던 탭으로 돌아온다. **제외됨**: `excluded-platform-filter`(플랫폼 전체/네이버/카카오)와 요일 탭이 붙고, 태그 필터(`excluded-tag-filter`)는 플랫폼이 "네이버"일 때만 보이며 숨길 때 값을 비운다(`renderSubscriptionTab`). 구독해제 화면의 태그 필터는 그대로다.
- **브라우저가 멈추던 문제**: 작품 천여 개를 한 번에 카드(이미지/버튼/이벤트)로 만들던 것이 원인이다. 이제 `windowRows`가 보여줄 항목 중 `LIST_PAGE_SIZE`(48)개만 `reconcileGrid`에 넘기고, grid 바로 아래 `.list-more` 줄("더 보기 (남은 N개)" 버튼 + `IntersectionObserver`로 화면 800px 앞에서 자동)로 이어서 그린다 — 카드 만드는 일이 보이는 만큼으로 한정된다. 탭/필터/검색/정렬을 바꾸면 `resetListWindow`로 처음 48개부터, 카드 상태 갱신(구독/제외 등)은 지금 그린 개수를 유지한다. 검색 입력은 `debounce`(250ms)로 멈춘 뒤 한 번만 다시 그린다. 측정(헤드리스 브라우저, 작품 2,500개, 이미지 없음): "전체" 탭 첫 그리기 48개 6ms vs 전부 그리기 222ms(2,500개) — 실제 브라우저에서는 이미지와 레이아웃 비용이 더해져 더 크다. 기존부터 있던 `loading="lazy"` 이미지와 카드 `content-visibility: auto`는 그대로다. 테스트: `tests/js/test_list_views.js`(요일 탭, 플랫폼/태그 필터, 점진 렌더링, 검색 지연). 목록 테스트의 예제 작품에는 `weekdays`(모든 요일)를 넣어 어느 요일 탭에서든 보이게 했다.

## 모바일 목록 레이아웃(화면 폭 640px 이하만)

- **구조**: 전체목록/구독해제/제외됨의 툴바가 `.toolbar.toolbar-sticky.list-toolbar` 안에 **검색창 → `.filter-toggle`(접기 버튼) → `.list-filters`(선택들 + 새로고침 버튼)** 순서로 있다. 마지막 확인 줄(`#naver-list-refresh-status.list-head-status`)과 요일 탭(`.weekday-tabs`)은 툴바 밖 아래에 있다. **데스크톱은 그대로**: `.filter-toggle`은 숨기고 `.list-filters`를 `display: contents`로 둬서 선택들이 예전처럼 툴바 한 줄에 놓인다(요일 탭도 고정하지 않음).
- **모바일 CSS**(`@media (max-width: 640px)`): ① `.main-tabs`는 `flex-wrap: nowrap; overflow-x: auto`로 한 줄 스와이프(높이 `--m-nav-h`). ② `.list-toolbar { display: contents }`로 툴바 상자를 없애서 검색창(`.list-toolbar > .clearable-wrap`)이 **페이지 전체에서** `top: var(--m-nav-h)`에 고정되게 한다(부모 상자가 있으면 그 안에서만 고정된다). ③ `.list-filters`와 `.list-head-status`는 기본 `display: none`, 페이지(`.page`)에 `filters-open`이 붙으면 펼친다. ④ `.weekday-tabs`는 `top: calc(var(--m-nav-h) + var(--m-search-h))`에 고정, 선택 개수 막대(`#naver-list-bulk-bar`)는 그 아래(`+ --m-tabs-h`). 고정 위치는 각 줄 높이를 합해 계산하므로 **높이를 변수(`--m-nav-h` 44px, `--m-search-h` 54px, `--m-tabs-h` 44px)로 고정**해 뒀다 — 줄 높이를 바꾸면 이 변수도 같이 바꿔야 겹치지 않는다. 입력창 글자는 16px(그보다 작으면 iOS가 눌렀을 때 화면을 확대한다).
- **스크립트**: `25-list-view.js`의 `updateFilterToggleLabels`가 접기 버튼의 문구(`필터` / `필터 · N개 적용`)와 화살표(▾/▴), `aria-expanded`를 맞춘다. 세는 대상은 `.list-filters`의 선택 중 값이 기본("" / "all")이 아닌 것이고, 정렬(`data-filter-ignore`)과 숨겨진 선택(예: 네이버가 아닐 때의 태그)은 세지 않는다. 목록을 그릴 때마다 갱신한다. `10-tabs.js`의 `scrollActiveMainTabIntoView`는 탭을 바꿀 때 선택한 메뉴 탭을 메뉴바 가운데 쪽으로 가져온다(넘치지 않는 데스크톱에서는 아무것도 안 함). 테스트: `tests/js/test_mobile_layout.js`(마크업 순서, 접기/펴기, 적용 개수, 메뉴바 스크롤, 모바일/데스크톱 CSS 규칙 — 실제 폭에서의 모양은 브라우저로 따로 확인했다: 메뉴바 44px 한 줄/스와이프, 스크롤 900px 뒤 메뉴바 top 0 · 검색창 top 44 · 요일 탭 top 98).

