FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PMM_ENV=production \
    PORT=8000

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

RUN useradd --create-home appuser && mkdir -p instance && chown -R appuser /app
USER appuser

EXPOSE 8000
CMD gunicorn --preload --bind 0.0.0.0:${PORT} --workers 2 --threads 4 --timeout 60 --access-logfile - run:app
