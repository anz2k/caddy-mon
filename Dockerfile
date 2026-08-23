FROM python:3.12-slim

WORKDIR /app

# Ainult FastAPI + gunicorn (kerged, piisavad ühe veebilehe jaoks)
RUN pip install --no-cache-dir fastapi==0.115.0 uvicorn[standard]==0.30.6

COPY app.py /app/app.py

# Küsi Caddy admin-API-d iga 10 sekundi järel; pole vaja püsivat salvestust
EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
