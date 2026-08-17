import os
import psycopg2
from dotenv import load_dotenv


load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]

#connect dat db

def get_connection():
    return psycopg2.connect(SUPABASE_URL)
