# naver-webtoon-manager

네이버 웹툰 구독 관리 + 자동 다운로드 + 압축 + ComicInfo.xml 생성을 하나로 묶은 서비스.
기존 `NWebtoon_Downloader`(다운로드 엔진), `webtoon_manager.py`(구독 추적 + 디스코드 알림),
`change.py`(압축)를 대체한다. Windows GUI 자동화(pywinauto)는 쓰지 않는다.

## 구성

- Python 3.12 + FastAPI + APScheduler, 단일 Docker 컨테이너
- 데이터는 컨테이너 안 SQLite(`webtoons.db`) 한 파일에 저장 (기존 `ID_list.txt` +
  `webtoon_state.json`을 대체)
- 구독관리 웹페이지는 LAN 전용, 별도 인증 없음

## 폴더/파일 네이밍 규칙 (기존과 동일하게 유지)

```
{DOWNLOAD_ROOT}/{제목}/[{화번호:04d}] {부제목}/{이미지번호:04d}{확장자}
```

다운로드 완료 후 각 회차 폴더는 `change.py`와 동일한 번호 이어붙이기 규칙으로 zip 압축되고
원본 폴더는 삭제된다. 압축 결과와 함께 `ComicInfo.xml`, `cover.{ext}`가 웹툰 루트 폴더에 생성된다.

## 실행 주기 (3분리 구조)

| 잡 | 기본 주기 | 하는 일 |
|---|---|---|
| discovery_job | 6시간 | 완결 감지, 작가 신작 자동추가, 태그 신작 자동추가 |
| download_job | 1시간 | 구독 중인 웹툰의 새 회차 다운로드 + 압축 + info.xml |
| commands_job | 5분 | 디스코드 완결-확인 스레드 명령만 확인 (가벼움) |

주기는 `.env`의 `SCAN_INTERVAL_MINUTES` / `DOWNLOAD_INTERVAL_MINUTES` /
`COMMANDS_ONLY_INTERVAL_MINUTES`로 조정한다.

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
   - `WEBTOON_WEBHOOK_URL`, `WEBTOON_BOT_TOKEN`, `WEBTOON_NOTIFY_CHANNEL_ID` (선택)
4. Deploy the stack
5. `http://<호스트>:8000` 접속 → "기존 ID_list.txt 붙여넣기로 한 번에 가져오기"로 기존
   구독 목록을 옮긴다.

자동 업데이트가 필요 없으면 `docker-compose.yml`의 `labels` 블록은 지워도 됩니다
(이미 Watchtower를 쓰고 있지 않다면 있으나 없으나 동작에 차이 없음).

## 로컬 실행 (개발용)

```bash
cp .env.example .env
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## 기존 hermes 크론과의 관계

이 프로젝트가 `webtoon_manager.py`의 역할(구독목록 관리, 작가/태그 자동추가, 완결감지,
디스코드 알림)을 전부 흡수했으므로, hermes의 관련 크론 잡은 이 스택 배포 후 제거한다.
