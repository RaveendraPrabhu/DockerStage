ARG PYTHON_VERSION=3.11
FROM python:${PYTHON_VERSION}-slim
ARG APP_ENV=prod
RUN pip install --no-cache-dir fastapi uvicorn[standard]
CMD ["uvicorn", "main:app"]
