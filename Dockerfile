FROM python:3.13-slim

# Tailwind CLI standalone (no Node needed). Pinned version.
ADD --chmod=755 https://github.com/tailwindlabs/tailwindcss/releases/download/v3.4.13/tailwindcss-linux-x64 /usr/local/bin/tailwindcss

# Self-hosted HTMX (avoid CDN at runtime).
ADD https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js /app/static/js/htmx.min.js

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Build a static, minified Tailwind CSS scoped to classes actually used.
RUN tailwindcss -c tailwind.config.js -i static/css/input.css -o static/css/app.css --minify \
    && rm /usr/local/bin/tailwindcss

RUN mkdir -p /app/data

ENV DB_PATH=/app/data/mail_exclude.db \
    PYTHONUNBUFFERED=1

EXPOSE 5000

CMD ["gunicorn", "-w", "1", "--threads", "4", "-b", "0.0.0.0:5000", \
     "--access-logfile", "-", "--error-logfile", "-", "wsgi:app"]
