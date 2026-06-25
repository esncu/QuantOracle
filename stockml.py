"""
Autoregressive single-step LSTM with MC Dropout, 
trains on absolute close prices, OHLC & price delta
training seems to love clamping to mean
"""

import json
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn


# Tunable — inference params (no retraining needed)
# ---------------------------------------------------------------------------
TEMPERATURE = 1.9  # amplifies each step's deviation from previous close
# 1.0 = flat, 1.5 = moderate, 2.5 = very aggressive
CONE_GROWTH = 0.02  # per-step multiplier growth for the uncertainty cone
# spread[i] = std[i] * (1 + CONE_GROWTH * i)
MC_SAMPLES = 20  # stochastic passes; more = smoother, less volatile


# Tunable — training params (require retraining to take effect)
# ---------------------------------------------------------------------------
MAX_CANDLES = 365
HORIZON = 0.20  # fraction of real candle count; e.g. 0.20 = 20%
MAX_HORIZON = 365  # absolute ceiling on predicted candles
HIDDEN_SIZE = 128
NUM_LAYERS = 2
EPOCHS = 300
LR = 4e-5
DROPOUT = 0.4  # higher = more spread in MC cone; lower = tighter cone

FEATURES = ["open", "high", "low", "close"]


class CandleLSTM(nn.Module):
    def __init__(self, dropout=DROPOUT):
        super().__init__()
        self.lstm = nn.LSTM(
            4,
            HIDDEN_SIZE,
            NUM_LAYERS,
            batch_first=True,
            dropout=dropout if NUM_LAYERS > 1 else 0.0,
        )
        self.dropout = nn.Dropout(p=dropout)
        self.fc = nn.Linear(HIDDEN_SIZE, 1)

    def forward(self, x, hc=None):
        assert x.dim() == 3, f"Expected 3D input got {x.dim()}D: {x.shape}"
        out, hc_out = self.lstm(x, hc)
        pred = self.fc(self.dropout(out[:, -1, :]))
        return pred, hc_out

    def enable_dropout(self):
        """Keep dropout active during inference for MC sampling."""
        for m in self.modules():
            if isinstance(m, nn.Dropout):
                m.train()


# Data helpers
# ---------------------------------------------------------------------------
def _load_real(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    real = [c for c in data if str(c.get("fake", "false")).lower() == "false"]
    return real[-MAX_CANDLES:]


def _to_arr(candles: list[dict]) -> np.ndarray:
    return np.array(
        [[float(c[f]) for f in FEATURES] for c in candles], dtype=np.float32
    )


def _normalise(arr: np.ndarray):
    mins = arr.min(axis=0)
    scales = arr.max(axis=0) - mins
    scales = np.where(scales == 0, 1.0, scales)
    return (
        ((arr - mins) / scales).astype(np.float32),
        mins.astype(np.float32),
        scales.astype(np.float32),
    )


def _denorm(v: float, mn: float, sc: float) -> float:
    return round(float(v * sc + mn), 4)


def _future_ts(last_ts: int, n: int, tf: str) -> list[int]:
    step = {"5M": 300, "1H": 3600, "1D": 86400, "1W": 604800}.get(tf, 86400)
    return [last_ts + step * (i + 1) for i in range(n)]


def _make_input(o: float, h: float, l: float, cl: float) -> torch.Tensor:
    return torch.tensor([[[o, h, l, cl]]], dtype=torch.float32)  # (1, 1, 4)


# Training
# ---------------------------------------------------------------------------
def _train(normed: np.ndarray, window: int, horizon: int) -> CandleLSTM:
    xs, ys = [], []
    for i in range(len(normed) - window - horizon + 1):
        xs.append(normed[i : i + window])
        ys.append(normed[i + window : i + window + horizon, 3:4])

    if not xs:
        split = max(1, len(normed) - horizon)
        xs = [normed[:split]]
        ys = [normed[split:, 3:4]
        horizon = len(ys[0])

    X = torch.tensor(np.array(xs), dtype=torch.float32)  # (N, window, 4)
    Y = torch.tensor(np.array(ys), dtype=torch.float32)  # (N, horizon, 1)

    model = CandleLSTM()
    opt = torch.optim.Adam(model.parameters(), lr=LR)
    loss_fn = nn.MSELoss()

    model.train()
    for epoch in range(EPOCHS):
        opt.zero_grad()
        _, hc = model(X)
        step_input = X[:, -1:, :]  # (N, 1, 4)
        step_loss = None

        teacher_ratio = max(0.5, 1.0 - epoch / EPOCHS)

        for t in range(horizon):
            pred_c, hc = model(step_input, hc)
            tgt = Y[:, t, :]  # (N, 1)
            sl = loss_fn(pred_c, tgt)
            step_loss = sl if step_loss is None else step_loss + sl

            # Ground truth or model output for next step's close
            if torch.rand(1).item() < teacher_ratio:
                c_col = tgt[:, 0:1]
            else:
                c_col = pred_c[:, 0:1].detach()

            o_col = step_input[:, 0, 3:4]  # previous close as open
            h_col = torch.max(o_col, c_col)
            l_col = torch.min(o_col, c_col)
            step_input = torch.stack([o_col, h_col, l_col, c_col], dim=2)  # (N,1,4)

        if step_loss is not None:
            (step_loss / horizon).backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()

    return model


# MC Dropout rollout
# ---------------------------------------------------------------------------
def _mc_rollout(
    model: CandleLSTM,
    seed_normed: np.ndarray,
    horizon: int,
    mins,
    scales,
    last_ts: int,
    tf: str,
    n_samples: int = MC_SAMPLES,
) -> tuple[list[dict], dict]:
    model.eval()
    model.enable_dropout()

    seed = torch.tensor(seed_normed[np.newaxis, :, :], dtype=torch.float32)
    assert seed.dim() == 3

    timestamps = _future_ts(last_ts, horizon, tf)

    all_o = np.zeros((n_samples, horizon), dtype=np.float32)
    all_c = np.zeros((n_samples, horizon), dtype=np.float32)

    with torch.no_grad():
        for s in range(n_samples):
            _, hc = model(seed)
            step_input = _make_input(*seed_normed[-1].tolist())

            for t in range(horizon):
                pred_c, hc = model(step_input, hc)  # (1, 1)
                c_n = pred_c[0, 0].item()

                # Temperature: amplify deviation from previous close
                last_c_n = step_input[0, 0, 3].item()
                c_n = last_c_n + (c_n - last_c_n) * TEMPERATURE

                # Open = previous close
                o_n = last_c_n
                all_o[s, t] = o_n
                all_c[s, t] = c_n

                h_n = max(o_n, c_n)
                l_n = min(o_n, c_n)
                step_input = _make_input(o_n, h_n, l_n, c_n)

    mean_o = all_o.mean(axis=0)
    mean_c = all_c.mean(axis=0)
    std_c = all_c.std(axis=0)

    cone_scale = np.array(
        [1.0 + CONE_GROWTH * i for i in range(horizon)], dtype=np.float32
    )
    spread = std_c * cone_scale

    predictions, upper, lower = [], [], []

    last_real_close = _denorm(float(seed_normed[-1, 3]), mins[3], scales[3])
    denormed_closes = [
        _denorm(float(mean_c[i]), mins[3], scales[3]) for i in range(horizon)
    ]

    for i, ts in enumerate(timestamps):
        c = denormed_closes[i]
        o = last_real_close if i == 0 else denormed_closes[i - 1]
        h = max(o, c)
        l = min(o, c)

        predictions.append(
            {
                "candle_close_timestamp": str(ts),
                "open": str(round(o, 4)),
                "high": str(h),
                "low": str(l),
                "close": str(c),
                "fake": "true",
            }
        )

        upper.append(
            {
                "candle_close_timestamp": str(ts),
                "value": str(_denorm(float(mean_c[i] + spread[i]), mins[3], scales[3])),
            }
        )
        lower.append(
            {
                "candle_close_timestamp": str(ts),
                "value": str(_denorm(float(mean_c[i] - spread[i]), mins[3], scales[3])),
            }
        )

    return predictions, {"upper": upper, "lower": lower}


# Public API
# ---------------------------------------------------------------------------
def train_and_predict(
    data_path: Path,
    pred_path: Path,
    model_path: Path,
    lock_path: Path,
    timeframe: str = "1D",
) -> None:
    try:
        candles = _load_real(data_path)
        if not candles:
            raise ValueError("No real candles found")

        arr = _to_arr(candles)
        normed, mins, sc = _normalise(arr)
        horizon = min(MAX_HORIZON, max(1, round(len(candles) * HORIZON)))
        window = max(1, min(len(normed) - horizon, MAX_CANDLES - horizon))
        model = _train(normed, window, horizon)

        model_path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "state_dict": model.state_dict(),
                "mins": mins.tolist(),
                "scales": sc.tolist(),
                "window": window,
                "horizon": horizon,
            },
            model_path,
        )

        last_ts = int(candles[-1]["candle_close_timestamp"])
        preds, forecast = _mc_rollout(
            model,
            normed[-window:],
            horizon,
            mins,
            sc,
            last_ts,
            timeframe,
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
) -> None:
    try:
        if not model_path.exists():
            raise FileNotFoundError(f"No model at {model_path} — run REGEN first")

        candles = _load_real(data_path)
        if not candles:
            raise ValueError("No real candles found")

        ck = torch.load(model_path, weights_only=True)
        mins = np.array(ck["mins"], dtype=np.float32)
        sc = np.array(ck["scales"], dtype=np.float32)
        window = ck["window"]
        saved_h = min(MAX_HORIZON, max(1, round(len(candles) * HORIZON)))

        model = CandleLSTM()
        model.load_state_dict(ck["state_dict"])

        arr = _to_arr(candles)
        normed = ((arr - mins) / sc).astype(np.float32)

        last_ts = int(candles[-1]["candle_close_timestamp"])
        preds, forecast = _mc_rollout(
            model, normed[-window:], saved_h, mins, sc, last_ts, timeframe
        )

        pred_path.parent.mkdir(parents=True, exist_ok=True)
        pred_path.write_text(
            json.dumps({"predictions": preds, "forecast": forecast}, indent=2)
        )

    finally:
        if lock_path.exists():
            lock_path.unlink()
