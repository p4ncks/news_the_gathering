FROM python:3.12-slim
ENV DJANGO_DEBUG=false
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN DJANGO_DEBUG=true python manage.py collectstatic --noinput
RUN useradd --create-home news && mkdir -p /app/data && chown -R news:news /app
USER news
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/', timeout=3)"
CMD ["gunicorn", "gathering.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--access-logfile", "-"]
