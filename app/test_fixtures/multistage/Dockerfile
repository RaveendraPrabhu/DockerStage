FROM python:3.11-slim AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends gcc
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.11-slim
COPY --from=builder --chown=1000:1000 /install /usr/local
COPY . /app
WORKDIR /app
EXPOSE 8501/tcp
HEALTHCHECK --interval=30s CMD curl -f http://localhost:8501/ || exit 1
CMD ["streamlit", "run", "app.py"]
