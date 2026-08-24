# webtoon-manager

네이버 웹툰 구독 관리 + 자동 다운로드 + 압축 + ComicInfo.xml 생성을 하나로 묶은 서비스.
기존 `NWebtoon_Downloader`(다운로드 엔진), `webtoon_manager.py`(구독 추적 + 디스코드 알림),
`change.py`(압축)를 대체한다. Windows GUI 자동화(pywinauto)는 쓰지 않는다.

## 구성

- Python 3.12 + FastAPI + APScheduler, 단일 Docker 컨테이너
- 데이터는 컨테이너 안 SQLite(`webtoons.db`) 한 파일에 저장
- 웹페이지는 LAN 전용, 별도 인증 없음
- 페이지 구성: **네이버 웹툰 전체목록**(구독/목록제외) / **구독중** / **구독해제** / **제외됨** / **설정**

## 폴더/파일 네이밍 규칙 (기존과 동일하게 유지)

```
{DOWNLOAD_ROOT}/{제목}/[{화번호:04d}] {부제목}/{이미지번호:04d}{확장자}
```

회차는 한 화씩 순서대로 처리한다: **다운로드 → 압축(zip) → 원본 폴더 삭제 → 다음 화**.
번호나 부제목이 어긋나는 회차가 있어도 실제 폴더 생성 순서를 그대로 신뢰한다(change.py와 동일).
ComicInfo.xml과 커버 이미지가 없으면 다운로드 전에 먼저 생성한다.

기존에 이미 폴더에 회차가 받아져 있던 웹툰(예: 예전 시스템에서 넘어온 웹툰)은, 폴더에 있는
회차 수를 세어 "몇 화까지 이미 받았는지"를 처음 한 번 자동으로 추론한 뒤 그 다음 화부터
이어받는다 — 처음부터 다시 받지 않는다.

## 실행 주기

기본값은 아래와 같고, **설정 페이지에서 언제든 바꿀 수 있다** (바꾸면 재시작 없이 바로 반영됨).

| 잡 | 기본 주기 | 하는 일 |
|---|---|---|
| discovery_job | 6시간 | 완결 감지, 작가 신작 자동추가, 태그 신작 자동추가 |
| download_job | 1시간 | 구독 중인 웹툰의 새 회차 다운로드 + 압축 + info.xml |
| commands_job | 5분 | 디스코드 완결-확인 스레드 명령만 확인 |

설정 페이지에서 "신작 스캔 실행" / "다운로드 실행" 버튼으로 즉시 수동 실행할 수 있고,
같은 페이지에서 실시간 진행 로그를 볼 수 있다.

## 네이버 웹툰 전체목록

첫 화면은 네이버 웹툰의 요일별 연재작 전체를 불러와 카드 형태로 보여준다. 여기서 바로
"구독"(다운로드 대상에 추가) 또는 "목록제외"(다시는 자동으로 추가되지 않도록 제외)할 수 있다.
titleId를 직접 입력해서 추가하는 기능은 없다 — 항상 이 목록에서 골라서 구독한다.

> 참고: 이 목록은 네이버의 비공개 내부 API(`/api/webtoon/titlelist/weekday`)를 사용한다.
> 네이버가 API 응답 구조를 바꾸면 목록이 비어 보일 수 있는데, 이 경우 브라우저 개발자
> 도구(F12) → Network 탭에서 `comic.naver.com/webtoon` 접속 시 호출되는 요청을 확인해서
> `app/naver_api.py`의 `fetch_full_webtoon_list`를 갱신해야 한다.

## 성인 웹툰 인증

Playwright `storage_state()` 형식(JSON)으로 export한 브라우저 쿠키 파일에서
`NID_AUT`/`NID_SES`만 자동으로 추출해서 쓴다. 파일 경로는 `.env`의
`COOKIE_FILE_PATH`로 지정하며 코드에는 하드코딩되어 있지 않다.

## Portainer 배포 (Web editor)

로컬 빌드나 저장소 clone이 필요 없습니다 — GitHub Actions가 push할 때마다 이미지를
미리 빌드해서 GHCR에 올려두고, Portainer는 그 이미지를 pull만 합니다.

1. Portainer → Stacks → Add stack → **Web editor** 선택
2. `docker-compose.yml` 내용을 그대로 붙여넣기 (다른 텍스트 섞이지 않게 주의)
3. 아래로 스크롤해서 **Environment variables** 섹션에 다음을 하나씩 추가
   - `WEBTOON_DOWNLOAD_HOST_PATH` — 예: `D:\Downloads\Webtoon\Webtoon_Download`
   - `APP_DATA_HOST_PATH` — DB 등을 저장할 빈 폴더
   - `COOKIE_DIR_HOST_PATH` — 쿠키 export 파일이 있는 폴더
   - `COOKIE_FILE_NAME` — 쿠키 파일명 (기본 `cookies.json`)
   - `WEBTOON_WEBHOOK_URL` (선택, `WEBTOON_BOT_TOKEN`/`WEBTOON_NOTIFY_CHANNEL_ID`는 완결 확인
     스레드 기능을 쓸 때만 필요)
4. Deploy the stack
5. `http://<호스트>:8000` 접속

자동 업데이트가 필요 없으면 `docker-compose.yml`의 `labels` 블록은 지워도 됩니다.

## 로컬 실행 (개발용)

```bash
cp .env.example .env
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## 기존 hermes 크론과의 관계

이 프로젝트가 `webtoon_manager.py`의 역할(구독목록 관리, 작가/태그 자동추가, 완결감지,
디스코드 알림)을 전부 흡수했으므로, hermes의 관련 크론 잡은 배포 후 제거한다.
