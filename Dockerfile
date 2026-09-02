FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# rclone: 아카이빙 목적지로 rclone 원격(원드라이브 등)을 쓸 때 필요.
# 공식 rclone이 아니라 wiserain/rclone(원드라이브 안정성 개선 포크)의 최신 릴리즈를
# 받는다 — 이 프로젝트 전용 설치 스크립트가 GitHub API로 최신 태그를 자동 조회해서
# 그에 맞는 바이너리를 받아온다. 이 이미지는 push할 때마다 캐시 없이 새로 빌드되므로
# (build.yml 참고), 빌드될 때마다 자동으로 그 시점의 최신 버전이 반영된다.
RUN curl -fsSL https://raw.githubusercontent.com/wiserain/rclone/mod/install.sh | bash

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
