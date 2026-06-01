from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import bcrypt
import psycopg2
import requests
import uuid

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI()

# All API routes live under /api so the StaticFiles mount at "/" never
# intercepts them (StaticFiles returns an HTML 404 for unknown paths,
# which breaks res.json() in the frontend).
api = APIRouter(prefix="/api")

SESSION_TTL_HOURS = 24

# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------


@contextmanager
def get_conn():
    conn = psycopg2.connect(
        host="localhost",
        port=5432,  # matches docker -p 5432:5432
        database="stocks",
        user="postgres",
        password="qu0cle",
    )
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def _safe_bcrypt_hash(password: str) -> str:
    # Truncate to 72 bytes — bcrypt's hard limit.
    pw = password.encode()[:72]
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode()


def _safe_bcrypt_verify(password: str, hashed: str) -> bool:
    pw = password.encode()[:72]
    return bcrypt.checkpw(pw, hashed.encode())


def register(name: str, password: str) -> None:
    hashed_password = _safe_bcrypt_hash(password)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (name, pass_hash) VALUES (%s, %s)",
                (name, hashed_password),
            )
        conn.commit()


def login(name: str, password: str) -> str | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, pass_hash FROM users WHERE name = %s", (name,))
            result = cur.fetchone()
            if not result or not _safe_bcrypt_verify(password, result[1]):
                return None

            user_id = result[0]
            session_id = str(uuid.uuid4())
            expires_at = datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)
            cur.execute(
                "INSERT INTO session (id, user_id, expires_at) VALUES (%s, %s, %s)",
                (session_id, user_id, expires_at),
            )
        conn.commit()
    return session_id


def admin_login(name: str, password: str) -> str | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Query the standalone admin table
            cur.execute("SELECT id, pass_hash FROM admin WHERE name = %s", (name,))
            result = cur.fetchone()
            # Plain-text password verification per init.sql seeding configuration
            if not result or result[1] != password:
                return None

            admin_id = result[0]
            session_id = str(uuid.uuid4())
            expires_at = datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)

            # Reusing the session table by mapping admin_id into the session context.
            # Note: Ensure foreign key constraints in your schema match this usage pattern.
            cur.execute(
                "INSERT INTO session (id, user_id, expires_at) VALUES (%s, %s, %s)",
                (session_id, admin_id, expires_at),
            )
        conn.commit()
    return session_id


def validate_session(session_id: str) -> int:
    """Return user_id for a valid non-expired session, or raise 401."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id FROM session WHERE id = %s AND expires_at > %s",
                (session_id, datetime.now(timezone.utc)),
            )
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return row[0]


def validate_admin_session(session_id: str) -> int:
    """Verifies that the session belongs to a valid account inside the admin table."""
    admin_id = validate_session(session_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Check the standalone admin table to verify identity
            cur.execute("SELECT id FROM admin WHERE id = %s", (admin_id,))
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="Admin access required")
    return admin_id


# ---------------------------------------------------------------------------
# API-key / upstream data helpers
# ---------------------------------------------------------------------------


def get_api_key() -> str:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT api_key FROM settings LIMIT 1")
            row = cur.fetchone()
    return row[0] if row else ""


def fetch_stock_data(symbol: str) -> dict:
    api_key = get_api_key()
    url = (
        "https://www.alphavantage.co/query"
        f"?function=TIME_SERIES_DAILY&symbol={symbol}&outputsize=full&apikey={api_key}"
    )
    return requests.get(url).json()


def rows_to_candles(rows) -> list[dict]:
    """Convert DB candle rows (ts, open, high, low, close, fake) to API format."""
    return [
        {
            "candle_close_timestamp": str(r[0]),
            "open": str(r[1]),
            "high": str(r[2]),
            "low": str(r[3]),
            "close": str(r[4]),
            "fake": "true" if r[5] else "false",
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class UserAuth(BaseModel):
    username: str
    password: str


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------


@api.post("/register")
def api_register(auth: UserAuth):
    try:
        register(auth.username, auth.password)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "success", "message": "User successfully registered"}


@api.post("/login")
def api_login(auth: UserAuth):
    session_id = login(auth.username, auth.password)
    if not session_id:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return {"status": "success", "session_id": session_id}


@api.post("/admin/login")
def api_admin_login(auth: UserAuth):
    session_id = admin_login(auth.username, auth.password)
    if not session_id:
        raise HTTPException(
            status_code=401, detail="Invalid admin username or password"
        )
    return {"status": "success", "session_id": session_id}


# ---------------------------------------------------------------------------
# Dashboard  —  POST /dashboard?session=…&action=…&index=…&time=…
#
#   GET       – all dashboard entries for this user (non-deleted)
#   GENERATE  – add a new entry (index+time) to user's dashboard + data table
#   TMPDELETE – user flags (index+time) for deletion  →  is_deleted=TRUE
#   RESTORE   – admin un-flags (index+time)           →  is_deleted=FALSE
#   DELETE    – admin hard-deletes the data row (cascade removes candles too)
# ---------------------------------------------------------------------------


@api.post("/dashboard/")
def dashboard(
    session: str,
    action: str,
    index: str | None = None,
    time: str | None = None,
):
    user_id = validate_session(session)

    if action == "GET":
        # Return all non-deleted dashboard entries for this user,
        # shaped as { data: [ { idx_name, 5M, 1H, 1D, 1W }, ... ] }
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT symbol_name, timeframe
                    FROM dashboard
                    WHERE user_id = %s AND is_deleted = FALSE
                    ORDER BY symbol_name, timeframe
                    """,
                    (user_id,),
                )
                rows = cur.fetchall()

        # Pivot into per-symbol dicts
        symbols: dict[str, dict] = {}
        for sym, tf in rows:
            if sym not in symbols:
                symbols[sym] = {
                    "idx_name": sym,
                    "5M": False,
                    "1H": False,
                    "1D": False,
                    "1W": False,
                }
            if tf in symbols[sym]:
                symbols[sym][tf] = True

        return {"status": "success", "data": list(symbols.values())}

    if action == "GENERATE":
        if not index or not time:
            raise HTTPException(status_code=400, detail="index and time are required")
        with get_conn() as conn:
            with conn.cursor() as cur:
                # Insert into data table (model/data paths are placeholders until training runs)
                cur.execute(
                    """
                    INSERT INTO data (user_id, symbol_name, timeframe, model_path, data_path)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    RETURNING id
                    """,
                    (
                        user_id,
                        index,
                        time,
                        f"models/{user_id}/{index}_{time}.pkl",
                        f"data/{user_id}/{index}_{time}.json",
                    ),
                )
                # Also ensure dashboard row exists
                cur.execute(
                    """
                    INSERT INTO dashboard (user_id, symbol_name, timeframe)
                    VALUES (%s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (user_id, index, time),
                )
            conn.commit()
        return {
            "status": "success",
            "message": f"New model generated for {index} [{time}]",
        }

    if action == "TMPDELETE":
        if not index or not time:
            raise HTTPException(status_code=400, detail="index and time are required")
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE dashboard SET is_deleted = TRUE
                    WHERE user_id = %s AND symbol_name = %s AND timeframe = %s
                    """,
                    (user_id, index, time),
                )
                cur.execute(
                    """
                    UPDATE data SET is_deleted = TRUE
                    WHERE user_id = %s AND symbol_name = %s AND timeframe = %s
                    """,
                    (user_id, index, time),
                )
            conn.commit()
        return {
            "status": "success",
            "message": f"{index} [{time}] flagged for deletion",
        }

    if action == "RESTORE":
        validate_admin_session(session)
        if not index or not time:
            raise HTTPException(status_code=400, detail="index and time are required")
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE dashboard SET is_deleted = FALSE
                    WHERE symbol_name = %s AND timeframe = %s
                    """,
                    (index, time),
                )
                cur.execute(
                    """
                    UPDATE data SET is_deleted = FALSE
                    WHERE symbol_name = %s AND timeframe = %s
                    """,
                    (index, time),
                )
            conn.commit()
        return {"status": "success", "message": f"{index} [{time}] restored"}

    if action == "DELETE":
        validate_admin_session(session)
        if not index or not time:
            raise HTTPException(status_code=400, detail="index and time are required")
        with get_conn() as conn:
            with conn.cursor() as cur:
                # Cascade on data deletes candles too
                cur.execute(
                    "DELETE FROM data WHERE symbol_name = %s AND timeframe = %s",
                    (index, time),
                )
                cur.execute(
                    "DELETE FROM dashboard WHERE symbol_name = %s AND timeframe = %s",
                    (index, time),
                )
            conn.commit()
        return {"status": "success", "message": f"{index} [{time}] permanently deleted"}

    raise HTTPException(status_code=400, detail=f"Unknown dashboard action: {action!r}")


# ---------------------------------------------------------------------------
# Chart  —  POST /chart?session=…&action=…&index=…&time=…
#
#   GET   – return candle rows (historical + forecast) from DB for index+time
#   REGEN – re-fetch upstream, replace candles in DB, return fresh dataset
# ---------------------------------------------------------------------------


@api.post("/chart/")
def chart(
    session: str,
    action: str,
    index: str | None = None,
    time: str | None = None,
):
    user_id = validate_session(session)

    if not index or not time:
        raise HTTPException(status_code=400, detail="index and time are required")

    if action == "GET":
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT c.candle_close_timestamp, c.open, c.high, c.low, c.close, c.fake
                    FROM candles c
                    JOIN data d ON d.id = c.data_id
                    WHERE d.user_id = %s AND d.symbol_name = %s AND d.timeframe = %s
                    ORDER BY c.candle_close_timestamp
                    """,
                    (user_id, index, time),
                )
                rows = cur.fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail=f"No data for {index} [{time}]")
        return {"status": "success", "data": rows_to_candles(rows)}

    if action == "REGEN":
        # Pull fresh data from upstream, wipe old candles, store new ones
        try:
            raw = fetch_stock_data(index)
            series = raw.get("Time Series (Daily)", {})
            if not series:
                raise ValueError("Empty series from upstream")
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Upstream data error: {exc}")

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM data WHERE user_id = %s AND symbol_name = %s AND timeframe = %s",
                    (user_id, index, time),
                )
                row = cur.fetchone()
                if not row:
                    raise HTTPException(
                        status_code=404, detail=f"No data entry for {index} [{time}]"
                    )
                data_id = row[0]

                cur.execute("DELETE FROM candles WHERE data_id = %s", (data_id,))

                candle_rows = []
                for date_str, ohlc in sorted(series.items()):
                    ts = int(
                        datetime.strptime(date_str, "%Y-%m-%d")
                        .replace(tzinfo=timezone.utc)
                        .timestamp()
                    )
                    candle_rows.append(
                        (
                            data_id,
                            ts,
                            ohlc["1. open"],
                            ohlc["2. high"],
                            ohlc["3. low"],
                            ohlc["4. close"],
                            False,
                        )
                    )

                cur.executemany(
                    """
                    INSERT INTO candles
                        (data_id, candle_close_timestamp, open, high, low, close, fake)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    candle_rows,
                )
            conn.commit()

        # Re-fetch to return consistent shape
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT candle_close_timestamp, open, high, low, close, fake
                    FROM candles WHERE data_id = %s
                    ORDER BY candle_close_timestamp
                    """,
                    (data_id,),
                )
                rows = cur.fetchall()

        return {
            "status": "success",
            "message": f"Model for {index} [{time}] retrained",
            "data": rows_to_candles(rows),
        }

    raise HTTPException(status_code=400, detail=f"Unknown chart action: {action!r}")


# ---------------------------------------------------------------------------
# Admin endpoints  —  all require role='admin' session
#
#   GET  /admin/users                     – all users with their data rows
#   DELETE /admin/users/{user_id}         – delete user (cascades everything)
#   DELETE /admin/data/{data_id}          – hard-delete one data+candle entry
#   POST   /admin/data/{data_id}/restore  – un-flag is_deleted on data+dashboard
# ---------------------------------------------------------------------------


@api.get("/admin/users")
def admin_list_users(session: str):
    validate_admin_session(session)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, role, created_at FROM users ORDER BY id")
            users = cur.fetchall()
            result = []
            for uid, name, role, created in users:
                cur.execute(
                    """
                    SELECT id, symbol_name, timeframe, is_deleted, created_at
                    FROM data WHERE user_id = %s ORDER BY id
                    """,
                    (uid,),
                )
                data_rows = cur.fetchall()
                result.append(
                    {
                        "id": uid,
                        "name": name,
                        "role": role,
                        "created_at": created.isoformat(),
                        "data": [
                            {
                                "id": d[0],
                                "symbol_name": d[1],
                                "timeframe": d[2],
                                "deleted": d[3],
                                "created_at": d[4].isoformat(),
                            }
                            for d in data_rows
                        ],
                    }
                )
    return result


@api.delete("/admin/users/{target_user_id}")
def admin_delete_user(target_user_id: int, session: str):
    validate_admin_session(session)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (target_user_id,))
        conn.commit()
    return {"status": "success", "message": f"User {target_user_id} deleted"}


@api.delete("/admin/data/{data_id}")
def admin_delete_data(data_id: int, session: str):
    validate_admin_session(session)
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Get symbol+timeframe+user_id before deleting so we can clean dashboard too
            cur.execute(
                "SELECT user_id, symbol_name, timeframe FROM data WHERE id = %s",
                (data_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Data entry not found")
            uid, sym, tf = row
            cur.execute("DELETE FROM data WHERE id = %s", (data_id,))
            cur.execute(
                "DELETE FROM dashboard WHERE user_id = %s AND symbol_name = %s AND timeframe = %s",
                (uid, sym, tf),
            )
        conn.commit()
    return {"status": "success", "message": f"Data entry {data_id} permanently deleted"}


@api.post("/admin/data/{data_id}/restore")
def admin_restore_data(data_id: int, session: str):
    validate_admin_session(session)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, symbol_name, timeframe FROM data WHERE id = %s",
                (data_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Data entry not found")
            uid, sym, tf = row
            cur.execute("UPDATE data SET is_deleted = FALSE WHERE id = %s", (data_id,))
            cur.execute(
                """
                UPDATE dashboard SET is_deleted = FALSE
                WHERE user_id = %s AND symbol_name = %s AND timeframe = %s
                """,
                (uid, sym, tf),
            )
        conn.commit()
    return {"status": "success", "message": f"Data entry {data_id} restored"}


# ---------------------------------------------------------------------------
# Register API router, then static (static must be LAST — it catches everything)
# ---------------------------------------------------------------------------
app.include_router(api)
app.mount("/", StaticFiles(directory="static", html=True), name="static")
