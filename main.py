from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import psycopg2 
import requests
import uuid
from datetime import datetime, timedelta
from passlib.hash import bcrypt 

app = FastAPI()

def get_conn():
    return psycopg2.connect(
        host="localhost",
        port=5431,
        database="stocks",
        user="backend_user",
        password="user123"
    )

def register(name, password):
    conn = get_conn()
    cur = conn.cursor()
    hashed_password = bcrypt.hash(password)
    cur.execute(
        "INSERT INTO users (name, pass_hash) VALUES (%s, %s)",(name, hashed_password)
    )
    conn.commit()
    cur.close()
    conn.close()

def login(name, password):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, pass_hash FROM users WHERE name = %s",(name,)
    )
    result = cur.fetchone()
    if not result:
        cur.close()
        conn.close()
        return None
    
    user_id, hashed_password = result
    if not bcrypt.verify(password, hashed_password):
        cur.close()
        conn.close()
        return None

    session_id = str(uuid.uuid4())
    session_expiry = datetime.utcnow() + timedelta(hours=1)
    cur.execute(
        "INSERT INTO session (id, user_id, expires_at) VALUES (%s, %s, %s)",(session_id, user_id, session_expiry)
    )
    conn.commit()
    cur.close()
    conn.close()
    return session_id

def get_api_key():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT api_key FROM settings LIMIT 1")
    api_key = cur.fetchone()[0]

    cur.close()
    conn.close()
    return api_key

def get_stock_price(symbol):
    api_key = get_api_key()

    url = f'https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol={symbol}&outputsize=full&apikey={api_key}'
    r = requests.get(url)
    data = r.json()

    print(data)

app.mount("/", StaticFiles(directory="static", html=True), name="static")
