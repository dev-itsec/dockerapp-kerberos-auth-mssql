#!/bin/sh
set -eu

if [ "${DB_AUTH_MODE:-sql}" = "kerberos" ]; then
    : "${KRB5_PRINCIPAL:?KRB5_PRINCIPAL is required}"
    : "${KRB5_KEYTAB:?KRB5_KEYTAB is required}"
    kinit -kt "${KRB5_KEYTAB}" "${KRB5_PRINCIPAL}"
    klist
fi

if [ "${1:-}" = "/opt/venv/bin/gunicorn" ]; then
    /opt/venv/bin/python /app/init_db.py
fi

exec "$@"
