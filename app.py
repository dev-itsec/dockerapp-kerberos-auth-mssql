import os
from contextlib import closing
from datetime import datetime

import pyodbc
from flask import Flask, render_template, request


app = Flask(__name__)
TABLE = "dbo.DockerKerberosDemo"


def odbc_value(value):
    return "{" + str(value).replace("}", "}}") + "}"


def connection_string():
    mode = os.getenv("DB_AUTH_MODE", "sql").lower()
    server = os.getenv("DB_SERVER", "lime.matrix.com")
    port = os.getenv("DB_PORT", "1433")
    parts = [
        "Driver={FreeTDS}",
        f"Server={server}",
        f"Port={port}",
        "TDS_Version=7.4",
        "ClientCharset=UTF-8",
        f"Database={odbc_value(os.environ['DB_NAME'])}",
        f"Encryption={os.getenv('DB_ENCRYPTION', 'request')}",
    ]
    if mode == "sql":
        parts.extend(
            [
                f"UID={odbc_value(os.environ['DB_USER'])}",
                f"PWD={odbc_value(os.environ['DB_PASSWORD'])}",
            ]
        )
    elif mode == "kerberos":
        realm = os.getenv("KRB5_REALM", "MATRIX.COM")
        parts.extend(
            [
                "Trusted_Connection=yes",
                f"REALM={realm}",
                f"ServerSPN=MSSQLSvc/{server}:{port}",
            ]
        )
    else:
        raise RuntimeError("DB_AUTH_MODE must be sql or kerberos")
    return ";".join(parts) + ";"


def connect():
    return pyodbc.connect(connection_string(), autocommit=True)


def prepare_table():
    with closing(connect()) as connection:
        connection.execute(
            f"""
            IF OBJECT_ID(N'{TABLE}', N'U') IS NULL
            CREATE TABLE {TABLE} (
                Id int IDENTITY PRIMARY KEY,
                Text nvarchar(500) NOT NULL,
                UpdatedAt datetime2 NOT NULL DEFAULT SYSUTCDATETIME()
            )
            """
        )


def item_from_row(row):
    updated_at = row[2]
    if isinstance(updated_at, datetime):
        updated_at = updated_at.isoformat(timespec="milliseconds") + "Z"
    return {"id": row[0], "text": row[1], "updated_at": updated_at}


def validated_text():
    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text", "")).strip()
    if not text:
        return None, ({"status": "error", "error": "Введите текст записи"}, 400)
    if len(text) > 500:
        return None, ({"status": "error", "error": "Максимум 500 символов"}, 400)
    return text, None


def database_error(error):
    app.logger.exception("Database API request failed")
    return {
        "status": "error",
        "error": "База данных временно недоступна",
        "code": str(error.args[0]),
    }, 503


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/items")
def api_items():
    try:
        with closing(connect()) as connection:
            rows = connection.execute(
                f"SELECT Id, Text, UpdatedAt FROM {TABLE} ORDER BY Id DESC"
            ).fetchall()
        items = [item_from_row(row) for row in rows]
        return {"status": "ok", "items": items, "count": len(items)}
    except pyodbc.Error as error:
        return database_error(error)


@app.post("/api/items")
def api_create():
    text, error_response = validated_text()
    if error_response:
        return error_response
    try:
        with closing(connect()) as connection:
            row = connection.execute(
                f"""
                INSERT INTO {TABLE} (Text)
                OUTPUT INSERTED.Id, INSERTED.Text, INSERTED.UpdatedAt
                VALUES (?)
                """,
                text,
            ).fetchone()
        return {"status": "ok", "item": item_from_row(row)}, 201
    except pyodbc.Error as error:
        return database_error(error)


@app.put("/api/items/<int:item_id>")
def api_update(item_id):
    text, error_response = validated_text()
    if error_response:
        return error_response
    try:
        with closing(connect()) as connection:
            row = connection.execute(
                f"""
                UPDATE {TABLE}
                SET Text = ?, UpdatedAt = SYSUTCDATETIME()
                OUTPUT INSERTED.Id, INSERTED.Text, INSERTED.UpdatedAt
                WHERE Id = ?
                """,
                text,
                item_id,
            ).fetchone()
        if row is None:
            return {"status": "error", "error": "Запись не найдена"}, 404
        return {"status": "ok", "item": item_from_row(row)}
    except pyodbc.Error as error:
        return database_error(error)


@app.delete("/api/items/<int:item_id>")
def api_delete(item_id):
    try:
        with closing(connect()) as connection:
            cursor = connection.execute(f"DELETE FROM {TABLE} WHERE Id = ?", item_id)
        if cursor.rowcount == 0:
            return {"status": "error", "error": "Запись не найдена"}, 404
        return {"status": "ok"}
    except pyodbc.Error as error:
        return database_error(error)


def health_payload():
    mode = os.getenv("DB_AUTH_MODE", "sql").lower()
    with closing(connect()) as connection:
        database_name = connection.execute("SELECT DB_NAME()").fetchval()
        if mode == "kerberos":
            scheme = connection.execute(
                "SELECT CONVERT(nvarchar(40), CONNECTIONPROPERTY('auth_scheme'))"
            ).fetchval()
        else:
            scheme = "SQL"
    return {
        "status": "ok",
        "api": True,
        "database": True,
        "database_name": database_name,
        "mode": mode,
        "auth_scheme": scheme,
    }


@app.get("/api/health")
@app.get("/health")
def health():
    try:
        return health_payload()
    except pyodbc.Error as error:
        return database_error(error)


if __name__ == "__main__":
    try:
        prepare_table()
    except pyodbc.Error:
        app.logger.exception(
            "Initial database connection failed; starting web server for diagnostics"
        )
    app.run(host="0.0.0.0", port=8080, debug=False)
