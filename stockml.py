"""
stockml.py — LSTM-based OHLC prediction for QuantOracle
--------------------------------------------------------
Input:  real candle data JSON  (list of candle dicts with fake=false)
Output: prediction candle JSON (list of candle dicts with fake=true)
        saved model            .pt file

Usage (called from main.py background task):
    from stockml import train_and_predict
    train_and_predict(data_path, pred_path, model_path, lock_path, horizon=20)
"""

import json
import math
from pathlib import Path

import torch
import torch.nn as nn
import numpy as np


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_CANDLES  = 100    # cap input to keep training fast on low-end hardware
HORIZON      = 20     # number of candles to predict ahead
HIDDEN_SIZE  = 64
NUM_LAYERS   = 2
EPOCHS       = 100
LR           = 1e-3
FEATURES     = ["open", "high", "low", "close"]   # order matters for tensors


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class CandleLSTM(nn.Module):
    def __init__(self, input_size=4, hidden_size=HIDDEN_SIZE,
                 num_layers=NUM_LAYERS, horizon=HORIZON):
        super().__init__()
        self.horizon = horizon
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers,
                            batch_first=True, dropout=0.2)
        # Predict open+close for each horizon step; high=max(o,c), low=min(o,c)
        self.fc = nn.Linear(hidden_size, horizon * 2)

    def forward(self, x):
        # x: (batch, seq_len, features)
        out, _ = self.lstm(x)
        last    = out[:, -1, :]          # take last timestep
        pred    = self.fc(last)          # (batch, horizon*2)
        return pred.view(-1, self.horizon, 2)  # (batch, horizon, 2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_real(data_path: Path) -> list[dict]:
    """Load and return only real (fake=false) candles, capped at MAX_CANDLES."""
    candles = json.loads(data_path.read_text())
    real    = [c for c in candles if str(c.get("fake", "false")).lower() == "false"]
    return real[-MAX_CANDLES:]   # keep most recent


def _to_tensor(candles: list[dict]) -> tuple[torch.Tensor, np.ndarray, np.ndarray]:
    """
    Normalise OHLC to [0,1] per-feature using min-max over the window.
    Returns (tensor, mins_array, scales_array) for denormalisation.
    """
    arr = np.array(
        [[float(c[f]) for f in FEATURES] for c in candles],
        dtype=np.float32,
    )
    mins   = arr.min(axis=0)
    maxs   = arr.max(axis=0)
    scales = np.where((maxs - mins) == 0, 1.0, maxs - mins)
    normed = (arr - mins) / scales
    return torch.tensor(normed).unsqueeze(0), mins, scales  # (1, seq, 4)


def _denorm(value: float, min_: float, scale: float) -> float:
    return round(float(value * scale + min_), 4)


def _next_timestamps(last_ts: int, n: int, timeframe: str) -> list[int]:
    """Generate n future UNIX timestamps after last_ts based on timeframe."""
    intervals = {"5M": 300, "1H": 3600, "1D": 86400, "1W": 604800}
    step = intervals.get(timeframe, 86400)
    return [last_ts + step * (i + 1) for i in range(n)]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def train_and_predict(
    data_path:  Path,
    pred_path:  Path,
    model_path: Path,
    lock_path:  Path,
    timeframe:  str  = "1D",
    horizon:    int  = HORIZON,
) -> None:
    """
    Full pipeline: load data → train LSTM → predict → save preds + model.
    The lock_path file must be created by the caller before calling this,
    and will be deleted by this function on completion (or error).
    """
    try:
        _run(data_path, pred_path, model_path, timeframe, horizon)
    finally:
        if lock_path.exists():
            lock_path.unlink()


def inference_only(
    data_path:  Path,
    pred_path:  Path,
    model_path: Path,
    lock_path:  Path,
    timeframe:  str = "1D",
    horizon:    int = HORIZON,
) -> None:
    """
    Load existing model, run inference on current real data, overwrite preds.
    Used by REFRESH action — does NOT retrain.
    """
    try:
        _run_inference(data_path, pred_path, model_path, timeframe, horizon)
    finally:
        if lock_path.exists():
            lock_path.unlink()


def _run(data_path, pred_path, model_path, timeframe, horizon):
    candles = _load_real(data_path)
    if not candles:
        raise ValueError("No real candles found in data file")

    seq_len = len(candles)
    # Need at least horizon+1 candles (1 for input, horizon for target).
    # Train even if quality will be poor (by design).
    window = max(1, min(seq_len - horizon, MAX_CANDLES - horizon))

    x_tensor, mins, scales = _to_tensor(candles)  # (1, seq_len, 4)

    # Build training pairs: sliding windows of size `window`
    xs, ys = [], []
    arr = x_tensor.squeeze(0).numpy()   # (seq_len, 4)
    for i in range(len(arr) - window - horizon + 1):
        xs.append(arr[i : i + window])
        # Target: open+close only for next `horizon` steps
        target = arr[i + window : i + window + horizon, [0, 3]]  # open, close
        ys.append(target)

    if not xs:
        # Fallback: use the whole sequence as one training sample
        xs = [arr[:seq_len - horizon]]
        ys = [arr[seq_len - horizon:, [0, 3]]]

    X = torch.tensor(np.array(xs), dtype=torch.float32)  # (N, window, 4)
    Y = torch.tensor(np.array(ys), dtype=torch.float32)  # (N, horizon, 2)

    input_size = X.shape[2]
    model = CandleLSTM(input_size=input_size, hidden_size=HIDDEN_SIZE,
                       num_layers=NUM_LAYERS, horizon=horizon)
    optimiser = torch.optim.Adam(model.parameters(), lr=LR)
    loss_fn   = nn.MSELoss()

    model.train()
    for _ in range(EPOCHS):
        optimiser.zero_grad()
        pred = model(X)
        loss = loss_fn(pred, Y)
        loss.backward()
        optimiser.step()

    # Save model
    model_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({
        "state_dict": model.state_dict(),
        "mins":       mins.tolist(),
        "scales":     scales.tolist(),
        "window":     window,
        "horizon":    horizon,
        "input_size": input_size,
    }, model_path)

    _predict_and_save(model, candles, arr, mins, scales, window,
                      horizon, pred_path, timeframe)


def _run_inference(data_path, pred_path, model_path, timeframe, horizon):
    if not model_path.exists():
        raise FileNotFoundError(f"No model found at {model_path} — run REGEN first")

    candles = _load_real(data_path)
    if not candles:
        raise ValueError("No real candles found in data file")

    checkpoint = torch.load(model_path, weights_only=True)
    mins       = np.array(checkpoint["mins"],    dtype=np.float32)
    scales     = np.array(checkpoint["scales"],  dtype=np.float32)
    window     = checkpoint["window"]
    saved_h    = checkpoint["horizon"]
    input_size = checkpoint["input_size"]

    model = CandleLSTM(input_size=input_size, hidden_size=HIDDEN_SIZE,
                       num_layers=NUM_LAYERS, horizon=saved_h)
    model.load_state_dict(checkpoint["state_dict"])

    arr = np.array(
        [[float(c[f]) for f in FEATURES] for c in candles],
        dtype=np.float32,
    )
    normed = (arr - mins) / scales

    _predict_and_save(model, candles, normed, mins, scales, window,
                      saved_h, pred_path, timeframe)


def _predict_and_save(model, candles, normed_arr, mins, scales,
                      window, horizon, pred_path, timeframe):
    """Run inference on the last `window` candles and write pred_path."""
    model.eval()
    seq = normed_arr[-window:]             # (window, 4)
    x   = torch.tensor(seq, dtype=torch.float32).unsqueeze(0)  # (1, window, 4)

    with torch.no_grad():
        raw = model(x).squeeze(0).numpy()  # (horizon, 2)  — open, close (normalised)

    last_ts   = int(candles[-1]["candle_close_timestamp"])
    timestamps = _next_timestamps(last_ts, horizon, timeframe)

    preds = []
    for i, ts in enumerate(timestamps):
        o = _denorm(raw[i, 0], mins[0], scales[0])   # open
        c = _denorm(raw[i, 1], mins[3], scales[3])   # close
        h = max(o, c)
        l = min(o, c)
        preds.append({
            "candle_close_timestamp": str(ts),
            "open":  str(o),
            "high":  str(h),
            "low":   str(l),
            "close": str(c),
            "fake":  "true",
        })

    pred_path.parent.mkdir(parents=True, exist_ok=True)
    pred_path.write_text(json.dumps(preds, indent=2))
