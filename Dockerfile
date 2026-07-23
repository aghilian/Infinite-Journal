FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8000
ENV THEJOURNAL_DATA_DIR=/data

WORKDIR /app
COPY app.py /app/app.py
COPY static /app/static

RUN useradd --system --uid 10001 --home /app journal \
    && mkdir -p /data \
    && chown -R journal:journal /data /app

USER journal
EXPOSE 8000

CMD ["python", "app.py"]
