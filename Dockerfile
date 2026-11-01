FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 DOWNLOAD_DIR=/app/downloads DATA_DIR=/app/data
WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends p7zip-full ffmpeg aria2 qbittorrent-nox megatools git curl unzip \
    ca-certificates build-essential \
 && curl -fsSL https://rclone.org/install.sh | bash \
 && apt-get purge -y build-essential && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
RUN chmod +x start.sh

CMD ["bash", "start.sh"]
