import logging

import pyodbc

from app import prepare_table


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("database-startup")


try:
    prepare_table()
    logger.info("Database table is ready")
except pyodbc.Error:
    logger.exception(
        "Initial database connection failed; starting WSGI server for diagnostics"
    )
