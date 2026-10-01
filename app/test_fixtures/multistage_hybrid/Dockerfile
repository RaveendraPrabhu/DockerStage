# Stage 1: Node.js frontend builder
FROM node:20-slim AS frontend
WORKDIR /build
COPY package.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# Stage 2: Python runtime
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY --from=frontend /build/dist ./static
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app:main", "--host", "0.0.0.0"]
