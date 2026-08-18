FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends krb5-user kstart python3 python3-venv tdsodbc unixodbc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN python3 -m venv /opt/venv
COPY requirements.txt .
RUN /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY init_db.py .
COPY gunicorn.conf.py .
COPY templates ./templates
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY krb5.conf /etc/krb5.conf
RUN chmod 0555 /usr/local/bin/docker-entrypoint.sh

EXPOSE 8080
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["/opt/venv/bin/gunicorn", "--config", "/app/gunicorn.conf.py", "app:app"]
