"""
stockml.py - LSTM-based OHLC prediction for QuantOracle

Trains on candle-to-candle price CHANGES (returns) rather than absolute prices.
This forces the model to learn momentum and direction instead of regressing
to the mean price level, producing dynamic predictions that compound naturally.

Architecture: autoregressive single-step LSTM with MC Dropout
- Input:  sequence of per-candle changes: [d_open, d_high, d_low, d_close]
          where d_x = (x_t - x_{t-1}) / x_{t-1}  (percentage return)
- Output: predicted [d_open, d_close] for the next candle
- high = max(open, close), low = min(open, close) for pred candles
- Predictions are accumulated back to absolute prices from the last real candle

MC Dropout (50 passes):
- Mean trajectory  → purple prediction candles (fake=true)
- Std of passes    → cone bounds, widens rightward as uncertainty compounds

Output: {timeframe}_preds.json
  {
    "predictions": [ {candle_close_timestamp, open, high, low, close, fake} ],
    "forecast":    { "upper": [{candle_close_timestamp, value}],
                     "lower": [{candle_close_timestamp, value}] }
  }
"""

import json
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

MAX_CANDLES = 100
HORIZON = 20
HIDDEN_SIZE = 128
NUM_LAYERS = 2
EPOCHS = 300
LR = 5e-4
MC_SAMPLES = 50
RETURN_DECAY = 0.80  # dampens returns per step; prevents indefinite trending
MEAN_REVERT_MIX = 0.30  # blend toward historical mean return
FEATURES = ["open", "high", "low", "close"]


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class CandleLSTM(nn.Module):
    def __init__(self):
        super().__init__()
        self.lstm = nn.LSTM(
            4,
            HIDDEN_SIZE,
            NUM_LAYERS,
            batch_first=True,
            dropout=0.3 if NUM_LAYERS > 1 else 0.0,
        )
        self.dropout = nn.Dropout(p=0.3)
        self.fc = nn.Linear(HIDDEN_SIZE, 2)  # d_open, d_close

    def forward(self, x, hc=None):
        assert x.dim() == 3, f"Expected 3D input got {x.dim()}D: {x.shape}"
        out, hc_out = self.lstm(x, hc)
        pred = self.fc(self.dropout(out[:, -1, :]))  # (batch, 2)
        return pred, hc_out

    def enable_dropout(self):
        for m in self.modules():
            if isinstance(m, nn.Dropout):
                m.train()


# ---------------------------------------------------------------------------
# Return space helpers
# ---------------------------------------------------------------------------


def _to_returns(arr: np.ndarray) -> np.ndarray:
    """
    Convert absolute OHLC array (N, 4) to percentage returns (N-1, 4).
    d_x[t] = (x[t] - x[t-1]) / x[t-1]
    Clipped to [-0.5, 0.5] to avoid exploding gradients on large moves.
    """
    eps = 1e-8
    returns = (arr[1:] - arr[:-1]) / (arr[:-1] + eps)
    return np.clip(returns, -0.5, 0.5).astype(np.float32)


def _load_real(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    real = [c for c in data if str(c.get("fake", "false")).lower() == "false"]
    return real[-MAX_CANDLES:]


def _to_arr(candles: list[dict]) -> np.ndarray:
    return np.array(
        [[float(c[f]) for f in FEATURES] for c in candles], dtype=np.float32
    )


def _future_ts(last_ts: int, n: int, tf: str) -> list[int]:
    step = {"5M": 300, "1H": 3600, "1D": 86400, "1W": 604800}.get(tf, 86400)
    return [last_ts + step * (i + 1) for i in range(n)]


def _make_input(d_o: float, d_h: float, d_l: float, d_c: float) -> torch.Tensor:
    """Single return-space candle as (1, 1, 4) LSTM input."""
    return torch.tensor([[[d_o, d_h, d_l, d_c]]], dtype=torch.float32)


# ---------------------------------------------------------------------------
# Training on returns
# ---------------------------------------------------------------------------


def _train(returns: np.ndarray, window: int, horizon: int) -> CandleLSTM:
    """
    Build sliding windows over the return series and train.
    X: (N, window, 4)  — input windows of returns
    Y: (N, horizon, 2) — target [d_open, d_close] for next horizon steps
    """
    xs, ys = [], []
    for i in range(len(returns) - window - horizon + 1):
        xs.append(returns[i : i + window])
        ys.append(returns[i + window : i + window + horizon, [0, 3]])  # d_open, d_close

    if not xs:
        split = max(1, len(returns) - horizon)
        xs = [returns[:split]]
        ys = [returns[split:, [0, 3]]]
        horizon = len(ys[0])

    X = torch.tensor(np.array(xs), dtype=torch.float32)  # (N, window, 4)
    Y = torch.tensor(np.array(ys), dtype=torch.float32)  # (N, horizon, 2)

    model = CandleLSTM()
    opt = torch.optim.Adam(model.parameters(), lr=LR)
    loss_fn = nn.MSELoss()

    model.train()
    for epoch in range(EPOCHS):
        opt.zero_grad()
        _, hc = model(X)
        step_input = X[:, -1:, :]  # (N, 1, 4) — last return as seed

        # Scheduled sampling: mix teacher forcing with model output
        teacher_ratio = max(0.5, 1.0 - epoch / EPOCHS)
        step_loss = None

        for t in range(horizon):
            pred_oc, hc = model(step_input, hc)  # (N, 2)  d_open, d_close
            tgt = Y[:, t, :]  # (N, 2)
            sl = loss_fn(pred_oc, tgt)
            step_loss = sl if step_loss is None else step_loss + sl

            if torch.rand(1).item() < teacher_ratio:
                do = tgt[:, 0:1]
                dc = tgt[:, 1:2]
            else:
                do = pred_oc[:, 0:1].detach()
                dc = pred_oc[:, 1:2].detach()

            dh = torch.max(do, dc)
            dl = torch.min(do, dc)
            step_input = torch.stack([do, dh, dl, dc], dim=2)  # (N,1,4)

        if step_loss is not None:
            (step_loss / horizon).backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()

    return model


# ---------------------------------------------------------------------------
# MC Dropout rollout in return space → absolute price predictions + cone
# ---------------------------------------------------------------------------


def _mc_rollout(
    model: CandleLSTM,
    seed_returns: np.ndarray,
    last_abs: np.ndarray,
    horizon: int,
    last_ts: int,
    tf: str,
    hist_mean_return: np.ndarray = None,
    n_samples: int = MC_SAMPLES,
):
    """
    seed_returns : (window, 4)  — normalised return window as model input
    last_abs     : (4,)         — last real candle's absolute OHLC prices
                                  used to accumulate predictions back to prices

    Returns:
      predictions : list[dict]  — mean candles, fake=true, absolute prices
      forecast    : dict        — {"upper": [...], "lower": [...]}
    """
    model.eval()
    model.enable_dropout()

    seed = torch.tensor(
        seed_returns[np.newaxis, :, :], dtype=torch.float32
    )  # (1,window,4)
    assert seed.dim() == 3

    timestamps = _future_ts(last_ts, horizon, tf)

    if hist_mean_return is None:
        hist_mean_return = np.zeros(4, dtype=np.float32)

    # Collect all sample trajectories in absolute price space
    # Shape: (n_samples, horizon)  — just close prices for cone
    all_closes = np.zeros((n_samples, horizon), dtype=np.float32)
    all_opens = np.zeros((n_samples, horizon), dtype=np.float32)

    with torch.no_grad():
        for s in range(n_samples):
            _, hc = model(seed)
            step_input = _make_input(*seed_returns[-1].tolist())  # (1,1,4)

            # Accumulate from last real absolute prices
            prev_open = float(last_abs[0])
            prev_close = float(last_abs[3])

            for t in range(horizon):
                pred_ret, hc = model(step_input, hc)  # (1, 2) — d_open, d_close
                d_o = pred_ret[0, 0].item()
                d_c = pred_ret[0, 1].item()

                # 1. Mean reversion: blend toward historical mean return
                d_o = (1 - MEAN_REVERT_MIX) * d_o + MEAN_REVERT_MIX * float(
                    hist_mean_return[0]
                )
                d_c = (1 - MEAN_REVERT_MIX) * d_c + MEAN_REVERT_MIX * float(
                    hist_mean_return[3]
                )

                # 2. Decay: dampen further-out steps progressively
                step_decay = RETURN_DECAY**t
                d_o *= step_decay
                d_c *= step_decay

                # Convert return → absolute price
                abs_o = prev_open * (1.0 + d_o)
                abs_c = prev_close * (1.0 + d_c)

                all_opens[s, t] = abs_o
                all_closes[s, t] = abs_c

                # Next step input: compute returns relative to this candle
                d_h = max(d_o, d_c)
                d_l = min(d_o, d_c)
                step_input = _make_input(d_o, d_h, d_l, d_c)

                prev_open = abs_o
                prev_close = abs_c

    # Mean trajectory → prediction candles
    mean_o = all_opens.mean(axis=0)  # (horizon,)
    mean_c = all_closes.mean(axis=0)

    # Std across samples → uncertainty cone, widens rightward
    std_c = all_closes.std(axis=0)
    cone_scale = np.array([1.0 + 0.2 * i for i in range(horizon)], dtype=np.float32)
    spread = std_c * cone_scale

    predictions = []
    upper = []
    lower = []

    for i, ts in enumerate(timestamps):
        o = round(float(mean_o[i]), 4)
        c = round(float(mean_c[i]), 4)
        h = max(o, c)
        l = min(o, c)

        predictions.append(
            {
                "candle_close_timestamp": str(ts),
                "open": str(o),
                "high": str(h),
                "low": str(l),
                "close": str(c),
                "fake": "true",
            }
        )

        upper.append(
            {
                "candle_close_timestamp": str(ts),
                "value": str(round(float(mean_c[i] + spread[i]), 4)),
            }
        )
        lower.append(
            {
                "candle_close_timestamp": str(ts),
                "value": str(round(float(mean_c[i] - spread[i]), 4)),
            }
        )

    return predictions, {"upper": upper, "lower": lower}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def train_and_predict(
    data_path: Path,
    pred_path: Path,
    model_path: Path,
    lock_path: Path,
    timeframe: str = "1D",
    horizon: int = HORIZON,
) -> None:
    try:
        candles = _load_real(data_path)
        if not candles:
            raise ValueError("No real candles found")

        arr = _to_arr(candles)
        returns = _to_returns(arr)  # (N-1, 4) return series

        window = max(1, min(len(returns) - horizon, MAX_CANDLES - horizon))
        model = _train(returns, window, horizon)

        model_path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "state_dict": model.state_dict(),
                "window": window,
                "horizon": horizon,
                "hist_mean": returns.mean(axis=0).tolist(),
            },
            model_path,
        )

        last_ts = int(candles[-1]["candle_close_timestamp"])
        last_abs = arr[-1]  # (4,) last real OHLC
        seed_returns = returns[-window:]  # (window, 4)
        hist_mean = returns.mean(axis=0)
        preds, forecast = _mc_rollout(
            model,
            seed_returns,
            last_abs,
            horizon,
            last_ts,
            timeframe,
            hist_mean_return=hist_mean,
        )

        pred_path.parent.mkdir(parents=True, exist_ok=True)
        pred_path.write_text(
            json.dumps({"predictions": preds, "forecast": forecast}, indent=2)
        )

    finally:
        if lock_path.exists():
            lock_path.unlink()


def inference_only(
    data_path: Path,
    pred_path: Path,
    model_path: Path,
    lock_path: Path,
    timeframe: str = "1D",
    horizon: int = HORIZON,
) -> None:
    try:
        if not model_path.exists():
            raise FileNotFoundError(f"No model at {model_path} — run REGEN first")

        candles = _load_real(data_path)
        if not candles:
            raise ValueError("No real candles found")

        ck = torch.load(model_path, weights_only=True)
        window = ck["window"]
        saved_h = ck["horizon"]
        hist_mean = np.array(ck.get("hist_mean", [0.0] * 4), dtype=np.float32)

        model = CandleLSTM()
        model.load_state_dict(ck["state_dict"])

        arr = _to_arr(candles)
        returns = _to_returns(arr)
        last_ts = int(candles[-1]["candle_close_timestamp"])
        last_abs = arr[-1]
        seed_returns = returns[-window:]

        preds, forecast = _mc_rollout(
            model,
            seed_returns,
            last_abs,
            saved_h,
            last_ts,
            timeframe,
            hist_mean_return=hist_mean,
        )

        pred_path.parent.mkdir(parents=True, exist_ok=True)
        pred_path.write_text(
            json.dumps({"predictions": preds, "forecast": forecast}, indent=2)
        )

    finally:
        if lock_path.exists():
            lock_path.unlink()
