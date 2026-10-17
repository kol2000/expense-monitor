FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home app
COPY app.py ledger.py features.py ./
COPY static ./static
RUN mkdir /data && chown app:app /data
USER app
ENV DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "90", "app:application"]
