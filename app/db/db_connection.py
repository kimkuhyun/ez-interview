import psycopg2
from config.config import Config

def get_connection():
    conn = psycopg2.connect(
        host=Config.DB_HOST,
        dbname=Config.DB_NAME,
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        port=Config.DB_PORT
    )
    return conn
