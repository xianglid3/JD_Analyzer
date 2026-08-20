import os
import psycopg2
from dotenv import load_dotenv
from contextlib import contextmanager

load_dotenv()
SUPABASE_URL = os.environ["SUPABASE_URL"]

#connect dat db
def get_connection():
    return psycopg2.connect(SUPABASE_URL)


#??
@contextmanager
def get_cursor(commit=False):
    connection = get_connection()
    cur = connection.cursor()
    try:
        yield cur
        if commit:
            connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cur.close()
        connection.close()
