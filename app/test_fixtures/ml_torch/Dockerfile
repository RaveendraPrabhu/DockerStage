FROM python:3.10-slim-bullseye
ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /workspace
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential git curl \
 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN python -m pip install --upgrade pip \
 && python -m pip install -r requirements.txt
COPY src/ ./src/
ENTRYPOINT ["python", "-m", "src.train"]
