import json
import os
import random
import smtplib
import string
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path

import bcrypt
import psycopg2
import requests

from fastapi import APIRouter, BackgroundTasks, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Config — swap these out for real values before production
# ---------------------------------------------------------------------------
SMTP_HOST     = "smtp.gmail.com"
SMTP_PORT     = 587
SMTP_USER     = "your@gmail.com"
SMTP_PASSWORD = "your_app_password"
SMTP_FROM     = "QuantOracle <your@gmail.com>"

DATA_ROOT = Path("data")   # ./data/{user_id}/{symbol}/{timeframe}.json

SESSION_TTL_MINUTES  = 30   # refreshed on every authenticated action
RECOVERY_TTL_MINUTES = 30   # one-time OTP window

app = FastAPI()
api = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

@contextmanager
def get_conn():
    conn = psycopg2.connect(
        host="localhost", port=5432,
        database="stocks",
        user="backend_user", password="user123",
    )
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt()).decode()

def _verify(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode()[:72], hashed.encode())


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def _new_expiry(minutes: int = SESSION_TTL_MINUTES) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)

def _refresh_session(session_id: str, conn=None) -> None:
    """Push expires_at forward by SESSION_TTL_MINUTES from now."""
    def _do(c):
        with c.cursor() as cur:
            cur.execute(
                "UPDATE session SET expires_at = %s WHERE id = %s",
                (_new_expiry(), session_id),
            )
        c.commit()
    if conn:
        _do(conn)
    else:
        with get_conn() as c:
            _do(c)

def validate_session(session_id: str, refresh: bool = True) -> int:
    """Return user_id (positive) for a valid NORMAL user session."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, type FROM session WHERE id = %s AND expires_at > %s",
                (session_id, datetime.now(timezone.utc)),
            )
            row = cur.fetchone()
        if not row:
            raise HTTPException(401, "Invalid or expired session")
        user_id, stype = row
        if user_id < 0:
            raise HTTPException(403, "Admin session cannot access user endpoints")
        if stype != "NORMAL":
            raise HTTPException(403, "Invalid session type")
        if refresh:
            _refresh_session(session_id, conn)
    return user_id

def validate_admin_session(session_id: str, refresh: bool = True) -> int:
    """Return admin_id for a valid admin session."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, type FROM session WHERE id = %s AND expires_at > %s",
                (session_id, datetime.now(timezone.utc)),
            )
            row = cur.fetchone()
        if not row:
            raise HTTPException(401, "Invalid or expired session")
        user_id, stype = row
        if user_id >= 0:
            raise HTTPException(403, "Admin access required")
        if stype != "NORMAL":
            raise HTTPException(403, "Invalid session type")
        admin_id = -user_id
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM admin WHERE id = %s", (admin_id,))
            if not cur.fetchone():
                raise HTTPException(403, "Admin access required")
        if refresh:
            _refresh_session(session_id, conn)
    return admin_id


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def send_email(to: str, subject: str, body: str) -> None:
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"]    = SMTP_FROM
    msg["To"]      = to
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASSWORD)
        s.sendmail(SMTP_FROM, [to], msg.as_string())


# ---------------------------------------------------------------------------
# AlphaVantage helper
# ---------------------------------------------------------------------------

# AlphaVantage function + response key per timeframe
_AV_CONFIG = {
    "5M":  ("TIME_SERIES_INTRADAY", "Time Series (5min)",  "&interval=5min"),
    "1H":  ("TIME_SERIES_INTRADAY", "Time Series (60min)", "&interval=60min"),
    "1D":  ("TIME_SERIES_DAILY",    "Time Series (Daily)", ""),
    "1W":  ("TIME_SERIES_WEEKLY",   "Weekly Time Series",  ""),
}

def fetch_av(symbol: str, timeframe: str, api_key: str) -> dict:
    """Fetch compact OHLC data from AlphaVantage for any supported timeframe."""
    cfg = _AV_CONFIG.get(timeframe)
    if not cfg:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    function, series_key, extra_params = cfg
    url = (
        "https://www.alphavantage.co/query"
        f"?function={function}&symbol={symbol}"
        f"&outputsize=compact&apikey={api_key}{extra_params}"
    )
    raw = requests.get(url, timeout=15).json()
    if raw.get("Note"):          raise ValueError(f"API rate limit: {raw['Note']}")
    if raw.get("Information"):   raise ValueError(raw["Information"])
    if raw.get("Error Message"): raise ValueError(raw["Error Message"])
    series = raw.get(series_key, {})
    if not series:
        raise ValueError(f"No data returned for {symbol} [{timeframe}] — check symbol and API key")
    return series


def series_to_candles(series: dict) -> list[dict]:
    """Convert AV series dict to candle list. Handles both daily and intraday keys."""
    candles = []
    for date_str, ohlc in sorted(series.items()):
        # Daily/weekly:  "2026-06-05"
        # Intraday:      "2026-06-05 09:30:00"
        fmt = "%Y-%m-%d %H:%M:%S" if " " in date_str else "%Y-%m-%d"
        ts = int(
            datetime.strptime(date_str, fmt)
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )
        candles.append({
            "candle_close_timestamp": str(ts),
            "open":  ohlc["1. open"],
            "high":  ohlc["2. high"],
            "low":   ohlc["3. low"],
            "close": ohlc["4. close"],
            "fake":  "false",
        })
    return candles


# ---------------------------------------------------------------------------
# File storage helpers
# data_path: data/{user_id}/{SYMBOL}/{timeframe}.json
# pred_path: data/{user_id}/{SYMBOL}/{timeframe}_preds.json  (ML, future)
# ---------------------------------------------------------------------------

def data_path(user_id: int, symbol: str, timeframe: str) -> Path:
    return DATA_ROOT / str(user_id) / symbol / f"{timeframe}.json"

def pred_path(user_id: int, symbol: str, timeframe: str) -> Path:
    return DATA_ROOT / str(user_id) / symbol / f"{timeframe}_preds.json"

def model_path(user_id: int, symbol: str, timeframe: str) -> Path:
    return DATA_ROOT / str(user_id) / symbol / f"{timeframe}_model.pt"

def lock_path(user_id: int, symbol: str, timeframe: str) -> Path:
    return DATA_ROOT / str(user_id) / symbol / f"{timeframe}.lock"

def write_candles(user_id: int, symbol: str, timeframe: str, candles: list[dict]) -> Path:
    p = data_path(user_id, symbol, timeframe)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(candles, indent=2))
    return p

def read_candles(path: str | Path) -> list[dict] | None:
    p = Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text())

def read_preds(user_id: int, symbol: str, timeframe: str) -> list[dict]:
    p = pred_path(user_id, symbol, timeframe)
    if not p.exists():
        return []
    return json.loads(p.read_text())


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class UserAuth(BaseModel):
    username: str
    password: str
    email: str | None = None   # required for register, ignored on login

class RecoveryRequest(BaseModel):
    email: str

class RecoveryConfirm(BaseModel):
    session_id: str
    otp: str
    new_password: str


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

@api.post("/register")
def api_register(auth: UserAuth):
    if not auth.email:
        raise HTTPException(400, "email is required for registration")
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (email, name, pass_hash) VALUES (%s, %s, %s)",
                    (auth.email.lower().strip(), auth.username.strip(), _hash(auth.password)),
                )
            conn.commit()
    except Exception as exc:
        raise HTTPException(400, str(exc))
    return {"status": "success", "message": "Registered successfully"}


@api.post("/login")
def api_login(auth: UserAuth):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, pass_hash FROM users WHERE name = %s", (auth.username,))
            row = cur.fetchone()
        if not row or not _verify(auth.password, row[1]):
            raise HTTPException(401, "Invalid username or password")
        sid = str(uuid.uuid4())
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO session (id, user_id, type, expires_at) VALUES (%s, %s, 'NORMAL', %s)",
                (sid, row[0], _new_expiry()),
            )
        conn.commit()
    return {"status": "success", "session_id": sid}


@api.post("/admin/login")
def api_admin_login(auth: UserAuth):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, pass_hash FROM admin WHERE name = %s", (auth.username,))
            row = cur.fetchone()
        if not row or not _verify(auth.password, row[1]):
            raise HTTPException(401, "Invalid admin credentials")
        sid = str(uuid.uuid4())
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO session (id, user_id, type, expires_at) VALUES (%s, %s, 'NORMAL', %s)",
                (sid, -row[0], _new_expiry()),
            )
        conn.commit()
    return {"status": "success", "session_id": sid}


@api.post("/recover/request")
def api_recover_request(body: RecoveryRequest):
    """Send a 6-digit OTP to the user's email and create a RECOVERY session."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE email = %s", (body.email.lower().strip(),))
            row = cur.fetchone()
        if not row:
            # Don't reveal whether email exists
            return {"status": "success", "message": "If that email exists, a code was sent"}
        user_id = row[0]
        otp = "".join(random.choices(string.digits, k=6))
        sid = str(uuid.uuid4())
        with conn.cursor() as cur:
            # Store OTP in session id field hack — we store otp in a separate column
            # For simplicity: encode otp into the session row via a dedicated table later;
            # for now store as "otp:{code}" in type field with RECOVERY prefix
            cur.execute(
                "INSERT INTO session (id, user_id, type, expires_at) VALUES (%s, %s, %s, %s)",
                (sid, user_id, f"RECOVERY:{otp}", _new_expiry(RECOVERY_TTL_MINUTES)),
            )
        conn.commit()
    try:
        send_email(
            body.email,
            "QuantOracle — Password Recovery",
            f"Your one-time recovery code is: {otp}\n\nIt expires in {RECOVERY_TTL_MINUTES} minutes.",
        )
    except Exception as exc:
        # Clean up the session if email failed — don't leave a dangling OTP
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM session WHERE id = %s", (sid,))
            conn.commit()
        raise HTTPException(500, f"Failed to send recovery email: {exc}")
    return {"status": "success", "message": "If that email exists, a code was sent", "session_id": sid}


@api.post("/recover/confirm")
def api_recover_confirm(body: RecoveryConfirm):
    """Validate OTP and set new password."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, type FROM session WHERE id = %s AND expires_at > %s",
                (body.session_id, datetime.now(timezone.utc)),
            )
            row = cur.fetchone()
        if not row:
            raise HTTPException(401, "Recovery session invalid or expired")
        user_id, stype = row
        if not stype.startswith("RECOVERY:"):
            raise HTTPException(403, "Not a recovery session")
        expected_otp = stype.split(":", 1)[1]
        if body.otp != expected_otp:
            raise HTTPException(401, "Invalid recovery code")
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET pass_hash = %s WHERE id = %s",
                (_hash(body.new_password), user_id),
            )
            cur.execute("DELETE FROM session WHERE id = %s", (body.session_id,))
        conn.commit()
    return {"status": "success", "message": "Password updated"}


# ---------------------------------------------------------------------------
# Dashboard endpoints
# POST /api/dashboard/?session=…&action=…
#
# Actions:
#   GET              — list all dashboards with their data entries + green/red status
#   CREATE           — create a new named dashboard
#   RENAME           — rename a dashboard  (requires: dash_id, name)
#   DELETE_DASH      — admin hard-delete a dashboard
#   TMPDELETE_DASH   — user flags a dashboard for deletion
#
#   GENERATE  — add symbol+timeframe to a dashboard, pull AV data, store file
#   REGEN     — re-pull AV data for an existing data entry, overwrite file
#   TMPDELETE — user flags a data entry for deletion
#   RESTORE   — admin un-flags a data entry
#   DELETE    — admin hard-deletes a data entry
# ---------------------------------------------------------------------------

@api.post("/dashboard/")
def dashboard_ep(
    session:  str,
    action:   str,
    dash_id:  int | None = None,
    name:     str | None = None,
    index:    str | None = None,
    time:     str | None = None,
    api_key:  str | None = None,
):
    user_id = validate_session(session)

    # ---- GET ---------------------------------------------------------------
    if action == "GET":
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, name, delete_set FROM dashboard WHERE user_id = %s ORDER BY id",
                    (user_id,),
                )
                dashboards = cur.fetchall()
                result = []
                for did, dname, ddel in dashboards:
                    cur.execute(
                        """
                        SELECT id, symbol_name, timeframe, data_path, delete_set
                        FROM data WHERE dashboard_id = %s ORDER BY symbol_name, timeframe
                        """,
                        (did,),
                    )
                    entries = cur.fetchall()
                    # Build per-symbol timeframe map
                    symbols: dict[str, dict] = {}
                    for eid, sym, tf, dpath, edel in entries:
                        if edel:
                            continue   # hide flagged entries from user view
                        if sym not in symbols:
                            symbols[sym] = {
                                "5M": {"id": None, "ready": False, "deleted": False},
                                "1H": {"id": None, "ready": False, "deleted": False},
                                "1D": {"id": None, "ready": False, "deleted": False},
                                "1W": {"id": None, "ready": False, "deleted": False},
                            }
                        if tf in symbols[sym]:
                            # Derive user_id from the outer loop context
                            lp = lock_path(user_id, sym, tf)
                            dp = Path(dpath) if dpath else None
                            if lp.exists():
                                state = "pending"
                            elif dp and dp.exists():
                                state = "ready"
                            else:
                                state = "empty"
                            symbols[sym][tf] = {
                                "id":      eid,
                                "state":   state,
                                "deleted": edel,
                            }
                    result.append({
                        "id":       did,
                        "name":     dname,
                        "deleted":  ddel,
                        "symbols":  symbols,
                    })
        return {"status": "success", "data": result}

    # ---- CREATE ------------------------------------------------------------
    if action == "CREATE":
        if not name:
            raise HTTPException(400, "name is required")
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO dashboard (user_id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING id",
                    (user_id, name),
                )
                row = cur.fetchone()
            conn.commit()
        if not row:
            raise HTTPException(409, f"Dashboard '{name}' already exists")
        return {"status": "success", "id": row[0], "name": name}

    # ---- RENAME ------------------------------------------------------------
    if action == "RENAME":
        if not dash_id or not name:
            raise HTTPException(400, "dash_id and name are required")
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE dashboard SET name = %s WHERE id = %s AND user_id = %s",
                    (name, dash_id, user_id),
                )
            conn.commit()
        return {"status": "success"}

    # ---- GENERATE ----------------------------------------------------------
    if action == "GENERATE":
        if not dash_id or not index or not time:
            raise HTTPException(400, "dash_id, index and time are required")
        # Verify dashboard belongs to user
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM dashboard WHERE id = %s AND user_id = %s",
                    (dash_id, user_id),
                )
                if not cur.fetchone():
                    raise HTTPException(404, "Dashboard not found")

        # Pull AV data if key provided
        candles = None
        fpath = None
        if api_key:
            try:
                series  = fetch_av(index.upper(), time or "1D", api_key)
                candles = series_to_candles(series)
                fpath   = write_candles(user_id, index.upper(), time, candles)
            except ValueError as exc:
                raise HTTPException(502, str(exc))
            except Exception as exc:
                raise HTTPException(502, f"Upstream error: {exc}")

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO data (dashboard_id, symbol_name, timeframe, data_path)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (dashboard_id, symbol_name, timeframe)
                    DO UPDATE SET
                        data_path  = COALESCE(EXCLUDED.data_path, data.data_path),
                        delete_set = FALSE
                    RETURNING id
                    """,
                    (dash_id, index.upper(), time, str(fpath) if fpath else None),
                )
                row = cur.fetchone()
                if not row:
                    # Shouldn't happen, but fall back to a SELECT
                    cur.execute(
                        "SELECT id FROM data WHERE dashboard_id=%s AND symbol_name=%s AND timeframe=%s",
                        (dash_id, index.upper(), time),
                    )
                    row = cur.fetchone()
                data_id = row[0]
            conn.commit()

        return {
            "status": "success",
            "data_id": data_id,
            "ready": candles is not None,
            "data": {"data": candles, "forecast": []} if candles else None,
        }

    # ---- REGEN -------------------------------------------------------------
    if action == "REGEN":
        if not dash_id or not index or not time:
            raise HTTPException(400, "dash_id, index and time are required")
        if not api_key:
            raise HTTPException(400, "api_key is required for REGEN")
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT d.id FROM data d
                    JOIN dashboard db ON db.id = d.dashboard_id
                    WHERE d.dashboard_id = %s AND d.symbol_name = %s AND d.timeframe = %s
                      AND db.user_id = %s
                    """,
                    (dash_id, index.upper(), time, user_id),
                )
                row = cur.fetchone()
        if not row:
            raise HTTPException(404, f"No entry for {index} [{time}] in that dashboard")
        try:
            series  = fetch_av(index.upper(), time or "1D", api_key)
            candles = series_to_candles(series)
            fpath   = write_candles(user_id, index.upper(), time, candles)
        except ValueError as exc:
            raise HTTPException(502, str(exc))
        except Exception as exc:
            raise HTTPException(502, f"Upstream error: {exc}")

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE data SET data_path = %s WHERE id = %s",
                    (str(fpath), row[0]),
                )
            conn.commit()
        return {"status": "success", "data": {"data": candles, "forecast": []}}

    # ---- TMPDELETE (data entry) --------------------------------------------
    if action == "TMPDELETE":
        if not dash_id or not index or not time:
            raise HTTPException(400, "dash_id, index and time are required")
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE data SET delete_set = TRUE
                    FROM dashboard db
                    WHERE data.dashboard_id = db.id
                      AND db.id = %s AND db.user_id = %s
                      AND data.symbol_name = %s AND data.timeframe = %s
                    """,
                    (dash_id, user_id, index.upper(), time),
                )
            conn.commit()
        return {"status": "success"}

    # ---- TMPDELETE_DASH (flag whole dashboard) ------------------------------
    if action == "TMPDELETE_DASH":
        if not dash_id:
            raise HTTPException(400, "dash_id is required")
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE dashboard SET delete_set = TRUE WHERE id = %s AND user_id = %s",
                    (dash_id, user_id),
                )
            conn.commit()
        return {"status": "success"}

    # ---- RESTORE (admin) ---------------------------------------------------
    if action == "RESTORE":
        validate_admin_session(session)
        if not dash_id or not index or not time:
            raise HTTPException(400, "dash_id, index and time are required")
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE data SET delete_set = FALSE WHERE dashboard_id = %s AND symbol_name = %s AND timeframe = %s",
                    (dash_id, index.upper(), time),
                )
            conn.commit()
        return {"status": "success"}

    # ---- DELETE (admin, data entry) ----------------------------------------
    if action == "DELETE":
        validate_admin_session(session)
        if not dash_id or not index or not time:
            raise HTTPException(400, "dash_id, index and time are required")
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM data WHERE dashboard_id = %s AND symbol_name = %s AND timeframe = %s",
                    (dash_id, index.upper(), time),
                )
            conn.commit()
        return {"status": "success"}

    # ---- DELETE_DASH (admin, whole dashboard) --------------------------------
    if action == "DELETE_DASH":
        validate_admin_session(session)
        if not dash_id:
            raise HTTPException(400, "dash_id is required")
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM dashboard WHERE id = %s", (dash_id,))
            conn.commit()
        return {"status": "success"}

    raise HTTPException(400, f"Unknown dashboard action: {action!r}")


# ---------------------------------------------------------------------------
# Chart endpoint
# POST /api/chart/?session=…&action=…&index=…&time=…&dash_id=…&api_key=…
#
#   GET   — read stored file → return {data:[...], forecast:[]}
#   REGEN — re-pull AV, overwrite file, return fresh data
#   PULL  — sessionless proxy, key supplied by caller, nothing stored
# ---------------------------------------------------------------------------

@api.post("/chart/")
def chart_ep(
    background_tasks: BackgroundTasks,
    session:  str,
    action:   str,
    index:    str | None = None,
    time:     str | None = None,
    dash_id:  int | None = None,
    api_key:  str | None = None,
):
    # PULL is sessionless
    if action == "PULL":
        if not index:
            raise HTTPException(400, "index is required")
        if not api_key:
            raise HTTPException(400, "api_key is required for PULL")
        pull_time = time or "1D"
        try:
            series  = fetch_av(index.upper(), pull_time, api_key)
            candles = series_to_candles(series)
        except ValueError as exc:
            raise HTTPException(502, str(exc))
        except Exception as exc:
            raise HTTPException(502, f"Upstream error: {exc}")
        return {"status": "success", "data": {"data": candles, "forecast": []}}

    user_id = validate_session(session)

    if not index or not time:
        raise HTTPException(400, "index and time are required")

    # Resolve data row — dash_id optional (takes first match if omitted)
    with get_conn() as conn:
        with conn.cursor() as cur:
            if dash_id:
                cur.execute(
                    """
                    SELECT d.id, d.data_path FROM data d
                    JOIN dashboard db ON db.id = d.dashboard_id
                    WHERE d.dashboard_id = %s AND d.symbol_name = %s
                      AND d.timeframe = %s AND db.user_id = %s
                    """,
                    (dash_id, index.upper(), time, user_id),
                )
            else:
                cur.execute(
                    """
                    SELECT d.id, d.data_path FROM data d
                    JOIN dashboard db ON db.id = d.dashboard_id
                    WHERE d.symbol_name = %s AND d.timeframe = %s AND db.user_id = %s
                    ORDER BY d.id LIMIT 1
                    """,
                    (index.upper(), time, user_id),
                )
            row = cur.fetchone()

    # ---- GET ---------------------------------------------------------------
    if action == "GET":
        if row and row[1]:
            candles = read_candles(row[1])
            if candles:
                preds = read_preds(user_id, index.upper(), time)
                return {"status": "success", "data": {"data": candles + preds, "forecast": []}}

        # No file — try fetching from AV if key provided
        key = api_key
        if key:
            try:
                series  = fetch_av(index.upper(), time or "1D", key)
                candles = series_to_candles(series)
                fpath   = write_candles(user_id, index.upper(), time, candles)
                # Update data_path in DB
                if row:
                    with get_conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                "UPDATE data SET data_path = %s WHERE id = %s",
                                (str(fpath), row[0]),
                            )
                        conn.commit()
                return {"status": "success", "data": {"data": candles, "forecast": []}}
            except ValueError as exc:
                raise HTTPException(502, str(exc))
            except Exception:
                pass  # fall through to tempdata

        # Final fallback — tempdata.txt
        tempdata = Path("tempdata.txt")
        if tempdata.exists():
            return {"status": "success", "data": {"data": json.loads(tempdata.read_text()), "forecast": []}}
        raise HTTPException(404, f"No data for {index} [{time}] and no API key provided")

    # ---- REFRESH -----------------------------------------------------------
    # Pull new data, append only new candles, run existing model, update preds
    if action == "REFRESH":
        if not api_key:
            raise HTTPException(400, "api_key is required for REFRESH")
        if not row:
            raise HTTPException(404, f"No data entry for {index} [{time}]")
        mp = model_path(user_id, index.upper(), time)
        if not mp.exists():
            raise HTTPException(400, "No trained model found — use REGEN first")
        lp = lock_path(user_id, index.upper(), time)
        if lp.exists():
            return {"status": "pending", "message": "Task already in progress"}

        # Pull new candles from AV
        try:
            series   = fetch_av(index.upper(), time, api_key)
            new_cands = series_to_candles(series)
        except ValueError as exc:
            raise HTTPException(502, str(exc))
        except Exception as exc:
            raise HTTPException(502, f"Upstream error: {exc}")

        # Merge: keep existing real candles, append any newer ones
        existing = read_candles(row[1]) or []
        real_existing = [c for c in existing if str(c.get("fake","false")).lower() == "false"]
        existing_ts   = {c["candle_close_timestamp"] for c in real_existing}
        appended      = real_existing + [c for c in new_cands if c["candle_close_timestamp"] not in existing_ts]

        dp = data_path(user_id, index.upper(), time)
        dp.parent.mkdir(parents=True, exist_ok=True)
        dp.write_text(json.dumps(appended, indent=2))
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE data SET data_path = %s WHERE id = %s", (str(dp), row[0]))
            conn.commit()

        # Background: run inference with existing model
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.touch()
        pp = pred_path(user_id, index.upper(), time)

        from stockml import inference_only
        background_tasks.add_task(inference_only, dp, pp, mp, lp, time)

        return {"status": "pending", "message": "Inference started"}

    # ---- REGEN -------------------------------------------------------------
    # Pull fresh data, delete old files, train new model, generate preds
    if action == "REGEN":
        if not api_key:
            raise HTTPException(400, "api_key is required for REGEN")
        if not row:
            raise HTTPException(404, f"No data entry for {index} [{time}]")
        lp = lock_path(user_id, index.upper(), time)
        if lp.exists():
            return {"status": "pending", "message": "Task already in progress"}

        try:
            series  = fetch_av(index.upper(), time, api_key)
            candles = series_to_candles(series)
        except ValueError as exc:
            raise HTTPException(502, str(exc))
        except Exception as exc:
            raise HTTPException(502, f"Upstream error: {exc}")

        # Write fresh real data, wipe old preds and model
        fpath = write_candles(user_id, index.upper(), time, candles)
        mp    = model_path(user_id, index.upper(), time)
        pp    = pred_path(user_id, index.upper(), time)
        for old_file in (mp, pp):
            if old_file.exists():
                old_file.unlink()

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE data SET data_path = %s, model_path = %s WHERE id = %s",
                    (str(fpath), str(mp), row[0]),
                )
            conn.commit()

        # Background: train new model and generate preds
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.touch()

        from stockml import train_and_predict
        background_tasks.add_task(train_and_predict, fpath, pp, mp, lp, time)

        return {"status": "pending", "message": "Training started"}

    raise HTTPException(400, f"Unknown chart action: {action!r}")


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

@api.get("/admin/users")
def admin_list_users(session: str):
    validate_admin_session(session)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, email, name, created_at FROM users ORDER BY id")
            users = cur.fetchall()
            result = []
            for uid, email, name, created in users:
                cur.execute(
                    """
                    SELECT d.id, d.name, da.id, da.symbol_name, da.timeframe,
                           da.data_path, da.delete_set
                    FROM dashboard d
                    JOIN data da ON da.dashboard_id = d.id
                    WHERE d.user_id = %s
                    ORDER BY d.id, da.symbol_name, da.timeframe
                    """,
                    (uid,),
                )
                rows = cur.fetchall()
                dashboards: dict[int, dict] = {}
                for did, dname, eid, sym, tf, dpath, edel in rows:
                    if did not in dashboards:
                        dashboards[did] = {"id": did, "name": dname, "data": []}
                    dashboards[did]["data"].append({
                        "id": eid, "symbol_name": sym, "timeframe": tf,
                        "has_data": bool(dpath and Path(dpath).exists()),
                        "deleted": edel,
                    })
                result.append({
                    "id": uid, "email": email, "name": name,
                    "created_at": created.isoformat(),
                    "dashboards": list(dashboards.values()),
                })
    return result


@api.delete("/admin/users/{target_id}")
def admin_delete_user(target_id: int, session: str):
    validate_admin_session(session)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (target_id,))
        conn.commit()
    return {"status": "success"}


@api.delete("/admin/data/{data_id}")
def admin_delete_data(data_id: int, session: str):
    validate_admin_session(session)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM data WHERE id = %s", (data_id,))
        conn.commit()
    return {"status": "success"}


@api.post("/admin/data/{data_id}/restore")
def admin_restore_data(data_id: int, session: str):
    validate_admin_session(session)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE data SET delete_set = FALSE WHERE id = %s", (data_id,))
        conn.commit()
    return {"status": "success"}


# ---------------------------------------------------------------------------
# Wire up router then static (static MUST be last)
# ---------------------------------------------------------------------------
app.include_router(api)
app.mount("/", StaticFiles(directory="static", html=True), name="static")
