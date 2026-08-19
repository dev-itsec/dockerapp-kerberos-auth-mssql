#!/bin/sh
set -eu

if [ "${DB_AUTH_MODE:-sql}" = "kerberos" ]; then
    : "${KRB5_PRINCIPAL:?KRB5_PRINCIPAL is required}"
    : "${KRB5_KEYTAB:?KRB5_KEYTAB is required}"
    : "${KRB5CCNAME:?KRB5CCNAME is required}"

    case "${KRB5CCNAME}" in
        FILE:*)
            kerberos_cache="${KRB5CCNAME#FILE:}"
            ;;
        *)
            echo "Only FILE Kerberos credential cache is supported" >&2
            exit 1
            ;;
    esac

    # Первый билет нужен для init_db.py.
    kinit \
        -c "${KRB5CCNAME}" \
        -kt "${KRB5_KEYTAB}" \
        "${KRB5_PRINCIPAL}"

    klist

    if [ "${1:-}" = "/opt/venv/bin/gunicorn" ]; then
        /opt/venv/bin/python /app/init_db.py
    fi

    # k5start становится PID 1 и запускает Gunicorn дочерним процессом.
    exec k5start -v \
        -f "${KRB5_KEYTAB}" \
        -k "${kerberos_cache}" \
        -K "${KRB5_CHECK_INTERVAL_MINUTES:-5}" \
        -l "${KRB5_TICKET_LIFETIME:-10h}" \
        "${KRB5_PRINCIPAL}" \
        -- "$@"
fi

if [ "${1:-}" = "/opt/venv/bin/gunicorn" ]; then
    /opt/venv/bin/python /app/init_db.py
fi

exec "$@"