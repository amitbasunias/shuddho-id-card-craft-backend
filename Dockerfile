FROM python:3.12-slim-bullseye

RUN apt-get update && apt-get install -y \
    inkscape \
    ghostscript \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    libglib2.0-0 \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN python manage.py collectstatic --noinput


EXPOSE 8080

CMD ["gunicorn", "saas_admin.wsgi:application", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "1200"]
