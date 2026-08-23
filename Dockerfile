FROM python:3.12-slim

WORKDIR /app

# Only FastAPI + uvicorn (lightweight, enough for one web page)
RUN pip install --no-cache-dir fastapi==0.115.0 uvicorn[standard]==0.30.6 httpx==0.27.2

COPY app.py /app/app.py

# Poll the Caddy admin API every 10 seconds; no persistent storage needed
EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
