FROM python:3.11-slim

RUN apt-get update && apt-get install -y nodejs npm

WORKDIR /app
COPY requirements.txt package.json ./

RUN pip install -r requirements.txt
RUN npm install

COPY . .

# Run build step via node
RUN npm run build

EXPOSE 8000
CMD ["uvicorn", "app:main", "--host", "0.0.0.0"]
