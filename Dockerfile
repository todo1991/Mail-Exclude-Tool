FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data

ENV DB_PATH=/app/data/mail_exclude.db \
    PYTHONUNBUFFERED=1

EXPOSE 5000

CMD ["gunicorn", "-w", "1", "--threads", "4", "-b", "0.0.0.0:5000", \
     "--access-logfile", "-", "--error-logfile", "-", "wsgi:app"]
