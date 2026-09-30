FROM python:3.11-alpine
WORKDIR /srv
RUN apk add --no-cache --virtual .build-deps \
        gcc \
        musl-dev \
        libxml2-dev \
        libxslt-dev
RUN pip install --no-cache-dir lxml==4.9.3
COPY app.py .
ENTRYPOINT ["python", "app.py"]
