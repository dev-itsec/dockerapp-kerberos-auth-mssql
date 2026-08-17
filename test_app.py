import os
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import app


class ConnectionStringTests(unittest.TestCase):
    @patch.dict(
        os.environ,
        {"DB_AUTH_MODE": "sql", "DB_NAME": "Demo", "DB_USER": "test", "DB_PASSWORD": "secret"},
        clear=True,
    )
    def test_sql_mode(self):
        value = app.connection_string()
        self.assertIn("UID={test}", value)
        self.assertIn("PWD={secret}", value)
        self.assertNotIn("Trusted_Connection", value)

    @patch.dict(os.environ, {"DB_AUTH_MODE": "kerberos", "DB_NAME": "Demo"}, clear=True)
    def test_kerberos_mode_has_no_credentials(self):
        value = app.connection_string()
        self.assertIn("Trusted_Connection=yes", value)
        self.assertIn("ServerSPN=MSSQLSvc/lime.matrix.com:1433", value)
        self.assertNotIn("UID=", value)
        self.assertNotIn("PWD=", value)

    def test_odbc_value_escapes_closing_brace(self):
        self.assertEqual(app.odbc_value("a}b"), "{a}}b}")


class ApiTests(unittest.TestCase):
    def setUp(self):
        app.app.config["TESTING"] = True
        self.client = app.app.test_client()

    def connection_with_cursor(self, cursor):
        connection = MagicMock()
        connection.execute.return_value = cursor
        return connection

    @patch("app.connect")
    def test_list_items(self, mock_connect):
        cursor = MagicMock()
        cursor.fetchall.return_value = [(7, "API test", datetime(2026, 8, 17, 10, 30))]
        mock_connect.return_value = self.connection_with_cursor(cursor)
        response = self.client.get("/api/items")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["count"], 1)
        self.assertEqual(response.json["items"][0]["text"], "API test")

    @patch("app.connect")
    def test_create_item(self, mock_connect):
        cursor = MagicMock()
        cursor.fetchone.return_value = (8, "Создано", datetime(2026, 8, 17, 10, 31))
        mock_connect.return_value = self.connection_with_cursor(cursor)
        response = self.client.post("/api/items", json={"text": "Создано"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["item"]["id"], 8)

    def test_create_rejects_empty_text(self):
        response = self.client.post("/api/items", json={"text": " "})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["status"], "error")

    @patch("app.connect")
    def test_update_missing_item(self, mock_connect):
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        mock_connect.return_value = self.connection_with_cursor(cursor)
        response = self.client.put("/api/items/404", json={"text": "Новое значение"})
        self.assertEqual(response.status_code, 404)

    @patch("app.connect")
    def test_delete_item(self, mock_connect):
        cursor = MagicMock(rowcount=1)
        mock_connect.return_value = self.connection_with_cursor(cursor)
        response = self.client.delete("/api/items/7")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "ok"})

    @patch("app.health_payload")
    def test_health_aliases(self, mock_health):
        mock_health.return_value = {"status": "ok", "api": True}
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/api/health").status_code, 200)

    @patch("app.connect")
    @patch.dict(os.environ, {"DB_AUTH_MODE": "kerberos"}, clear=False)
    def test_kerberos_health_casts_auth_scheme_for_freetds(self, mock_connect):
        database_cursor = MagicMock()
        database_cursor.fetchval.return_value = "Demo"
        scheme_cursor = MagicMock()
        scheme_cursor.fetchval.return_value = "KERBEROS"
        connection = MagicMock()
        connection.execute.side_effect = [database_cursor, scheme_cursor]
        mock_connect.return_value = connection

        payload = app.health_payload()

        self.assertEqual(payload["auth_scheme"], "KERBEROS")
        scheme_query = connection.execute.call_args_list[1].args[0]
        self.assertIn("CONVERT(nvarchar(40)", scheme_query)
        self.assertNotIn("sys.dm_exec_connections", scheme_query)


if __name__ == "__main__":
    unittest.main()

