FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# rclone: 아카이빙 목적지로 rclone 원격(원드라이브 등)을 쓸 때 필요. 공식 설치
# 스크립트가 아키텍처를 알아서 판별해서 받아준다.
RUN curl -s https://rclone.org/install.sh | bash

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
