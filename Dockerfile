FROM python:3.14-slim

WORKDIR /srv
COPY requirements.txt .
RUN python -m pip install --no-cache-dir --disable-pip-version-check -r requirements.txt \
    && addgroup --system vettore \
    && adduser --system --ingroup vettore --home /nonexistent --no-create-home vettore \
    && mkdir -p /data \
    && chown -R vettore:vettore /srv /data

COPY --chown=vettore:vettore app ./app

ENV VETTORE_DB=/data/vettore.db
VOLUME /data
EXPOSE 8080
USER vettore

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8080/healthz', timeout=2).read()" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
