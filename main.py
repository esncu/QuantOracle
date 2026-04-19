from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import psycopg2

app = FastAPI()
db_url = "postgresql://postgres:qu0cle@localhost:5432/myapp"
testdb_url = "postgresql://backend_user:user123@localhost:5432/myapp"

def get_conn():
    return psycopg2.connect(testdb_url)


@app.get("/test/{tag}")
def read_item(tag: str, query: str | None = None):
    conn = get_conn()
    cur = conn.cursor()
    if tag == "hi":
        cur.execute("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname != 'pg_catalog' AND schemaname != 'information_schema';")
        rows = cur.fetchall()
        return {"message": "it works, check console and try changing the tag!", "query": query, "extra": rows}
    else:
        return {"message": "it works, and you changed the tag!", "query": query}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
