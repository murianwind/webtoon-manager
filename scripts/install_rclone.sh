#!/bin/sh
# Docker 빌드에서 wiserain/rclone(원드라이브 안정성 개선 포크)의 최신 릴리즈를 설치한다.
# 포크의 설치 스크립트는 최신 버전을 GitHub API로 조회하는데, 빌드 서버에서 API가 막히면(403, 요청 횟수 제한) 버전을 빈 값으로 두고
# "이미 최신이 설치돼 있다"며 아무것도 설치하지 않고 끝난다. 그래서 여기서는
#  1) 최신 버전을 API 대신 릴리즈 페이지("latest")의 이동 주소에서 알아내고(요청 횟수 제한이 없다),
#  2) 그 버전을 설치 스크립트에 직접 넘기고(스크립트는 첫 인자를 버전으로 쓴다),
#  3) 실패하면 재시도하고, 끝내 못 하면 빌드를 실패시키고,
#  4) 설치 뒤 rclone이 실제로 돌아가는지 확인한다.
# 환경 변수(테스트/조정용): RCLONE_TRIES(기본 4번), RCLONE_WAIT(재시도 간격 초, 기본 10), RCLONE_LATEST_URL, RCLONE_INSTALL_URL
set -u

LATEST_URL="${RCLONE_LATEST_URL:-https://github.com/wiserain/rclone/releases/latest}"
INSTALL_URL="${RCLONE_INSTALL_URL:-https://raw.githubusercontent.com/wiserain/rclone/mod/install.sh}"
TRIES="${RCLONE_TRIES:-4}"
WAIT="${RCLONE_WAIT:-10}"

attempt=1
while [ "$attempt" -le "$TRIES" ]; do
    tag=$(curl -fsSIL -o /dev/null -w '%{url_effective}' "$LATEST_URL" 2>/dev/null | sed -n 's#.*/releases/tag/##p')
    if [ -z "$tag" ]; then
        echo "rclone 최신 버전을 알아내지 못했습니다 (시도 $attempt/$TRIES)" >&2
    else
        echo "rclone $tag 설치 (시도 $attempt/$TRIES)"
        # 설치 스크립트의 종료 코드는 보지 않고 결과(rclone이 실제로 있는지)로 판단한다 — "이미 최신"(3)도 rclone이 있으면 성공이다
        curl -fsSL "$INSTALL_URL" | bash -s -- "$tag"
        if rclone version >/dev/null 2>&1; then
            rclone version | head -n 1
            exit 0
        fi
        echo "rclone 설치를 확인하지 못했습니다 (시도 $attempt/$TRIES)" >&2
    fi
    attempt=$((attempt + 1))
    [ "$attempt" -le "$TRIES" ] && sleep "$WAIT"
done
echo "rclone 설치에 실패했습니다" >&2
exit 1
