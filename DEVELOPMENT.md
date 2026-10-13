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
- 계획 규칙: ① 폴더에 zip이 없으면 `new_folder`(기록 무시, 처음부터). ② 기록이 있으면 `compare` — `가진 회차 = 폴더 ∪ 기록`, 기준선 = `min(폴더 ∪ 기록)`, 기록에만 있는 회차는 `EpisodeRow.archived`(보관 폴더로 옮겨진 것). ③ 기록이 없고 파일이 하나면 `single_marker`(그 다음부터, 표식 위치는 부제목으로 보정), 여러 개면 `compare`(가장 이른 파일부터).
- **받을 수 있는 회차(무료/대여 중)는 앞에 잠긴 회차가 있어도 `to_download`에 넣고, 잠긴 회차는 모두 `locked`(번호순)에 둡니다.** `run_download`는 `to_download`를 받은 뒤 `locked[0]`을 기다무로 엽니다. 예전에는 첫 잠긴 회차에서 모든 뒤 회차를 막았지만(`blocked`), 그러면 앞이 잠긴 작품의 새 무료 회차를 못 받았습니다. 숨김(hidden) 회차는 잠긴 회차이며 받지 않습니다.
- **기록하는 곳은 한 군데**입니다 — 회차 zip을 저장하는 `download_episode`(자동, 수동, 기다무가 모두 거침). 기록이 NULL인 작품에는 더하지 않습니다(한 회차짜리 기록이 생기면 아카이빙 이력으로 처음 기록을 만드는 일이 막힘). 추적하지 않는 작품도 기록하지 않습니다.
- **처음 기록 만들기**: `initial_numbers` = 폴더 파일 번호 ∪ 아카이빙 이력의 파일 번호(`archive_history`에서 `title_id = kakao_<번호>`, 파일 이름 앞의 번호). 폴더 안의 빈 회차는 기록하지 않아서 받게 됩니다. 표식 파일 하나 + 이력 없음이면 번호가 사이트와 다를 수 있어서 만들지 않고, 첫 `run_download`에서 사이트와 맞춰 본 위치(`marker.resolved_number`)로 만듭니다. 시작할 때(`main.lifespan`, 스케줄러 시작 **전**) 한 번 `backfill_and_log`를 돌리고, 복원 직후에도 돌립니다. 요약은 실행 이력(다운로드)에 한 건으로 남습니다.
- 폴더가 비면 기록을 버립니다(다시 받으려고 비운 것으로 간주). 파일을 지워서 다시 받게 하려면 수동 다운로드를 쓰게 합니다.
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

