FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# System dependencies (Postgres client, WeasyPrint deps, etc.)
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    libcairo2 \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-xlib-2.0-0 \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Collect static files (STATIC_ROOT must be set in settings)
RUN python manage.py collectstatic --no-input

EXPOSE 8000

# Run Gunicorn
CMD ["gunicorn", "ardua_books.wsgi:application", "--bind", "0.0.0.0:8000"]

# Runserver (temporary)
#CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

