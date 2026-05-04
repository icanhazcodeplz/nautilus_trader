import os

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from numpy.lib.stride_tricks import sliding_window_view
from torch.utils.data import Dataset

from minGRU_pytorch.minLSTM import minLSTM

from custom.backtest_utils.load_catalog_data import get_catalog_data
from nautilus_trader.model import QuoteTick, TradeTick

torch.set_float32_matmul_precision("high")

# --- Config ---
THRESHOLD = 0.20
TICK_LOOKBACK = 100
SPREAD_LOOKBACK = 10
PRICE_TO_MID_LOOKBACK = 10
CANDLE_10S_LOOKBACK = 30  # 5 min / 10s
CANDLE_1M_LOOKBACK = 60  # 1 hour / 1m
HIDDEN_DIM = 64

CONTEXT_DIM = (
    SPREAD_LOOKBACK + PRICE_TO_MID_LOOKBACK + CANDLE_10S_LOOKBACK + CANDLE_1M_LOOKBACK
)


# --- Model ---
class TickClassifier(nn.Module):
    def __init__(self, tick_input_dim, context_dim, hidden_dim):
        super().__init__()
        self.tick_proj = nn.Linear(tick_input_dim, hidden_dim)
        self.lstm = minLSTM(dim=hidden_dim)
        self.head = nn.Sequential(
            nn.Linear(hidden_dim + context_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(hidden_dim, 1),
        )

    def forward(self, tick_feat, context):
        x = self.tick_proj(tick_feat)
        h = self.lstm(x)
        h_last = h[:, -1, :]
        combined = torch.cat([h_last, context], dim=-1)
        return self.head(combined).squeeze(-1)


# --- Dataset ---
class TickDataset(Dataset):
    def __init__(self, tick_feat, ctx_feat, target_labels):
        self.tick_feat = torch.from_numpy(tick_feat)
        self.ctx_feat = torch.from_numpy(ctx_feat)
        self.labels = torch.from_numpy(target_labels)

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        return self.tick_feat[idx], self.ctx_feat[idx], self.labels[idx]


# --- Data loading ---
def load_data(symbol, start, end):
    trades = get_catalog_data(
        symbol=symbol, start=start, end=end, data_cls=TradeTick, venue="ALPACA"
    )
    quotes = get_catalog_data(
        symbol=symbol, start=start, end=end, data_cls=QuoteTick, venue="ALPACA"
    )
    print(f"trades={len(trades)} quotes={len(quotes)}")

    trades_df = (
        pd.DataFrame(
            {
                "ts_event": [t.ts_event for t in trades],
                "price": [float(t.price) for t in trades],
                "size": [float(t.size) for t in trades],
            }
        )
        .sort_values("ts_event")
        .reset_index(drop=True)
    )

    quotes_df = (
        pd.DataFrame(
            {
                "ts_event": [q.ts_event for q in quotes],
                "bid_price": [float(q.bid_price) for q in quotes],
                "ask_price": [float(q.ask_price) for q in quotes],
            }
        )
        .sort_values("ts_event")
        .reset_index(drop=True)
    )

    trades_df = pd.merge_asof(
        trades_df,
        quotes_df,
        on="ts_event",
        direction="backward",
        allow_exact_matches=True,
    )
    trades_df = trades_df.dropna(subset=["bid_price", "ask_price"]).reset_index(
        drop=True
    )

    prices = trades_df["price"].values
    sizes = trades_df["size"].values
    ts_events = trades_df["ts_event"].values
    spreads = (trades_df["ask_price"] - trades_df["bid_price"]).values
    mid_prices = ((trades_df["ask_price"] + trades_df["bid_price"]) / 2).values
    price_to_mids = prices - mid_prices

    quotes_df["ts_dt"] = pd.to_datetime(quotes_df["ts_event"], utc=True)
    quotes_df["mid"] = (quotes_df["bid_price"] + quotes_df["ask_price"]) / 2

    candle_10s = quotes_df.set_index("ts_dt")["mid"].resample("10s").last().ffill()
    candle_1m = quotes_df.set_index("ts_dt")["mid"].resample("1min").last().ffill()

    return {
        "prices": prices,
        "sizes": sizes,
        "ts_events": ts_events,
        "spreads": spreads,
        "mid_prices": mid_prices,
        "price_to_mids": price_to_mids,
        "c10s_times": candle_10s.index.astype("int64").values,
        "c10s_values": candle_10s.values,
        "c1m_times": candle_1m.index.astype("int64").values,
        "c1m_values": candle_1m.values,
    }


# --- Label computation ---
def compute_labels(price_arr, threshold, subsample_ticks=1):
    n = len(price_arr)
    result = np.full(n, -1, dtype=np.int64)
    indices_to_label = range(0, n, subsample_ticks)
    total = len(indices_to_label)
    for count, i in enumerate(indices_to_label):
        base = price_arr[i]
        up_target = base + threshold
        down_target = base - threshold
        for j in range(i + 1, n):
            if price_arr[j] >= up_target:
                result[i] = 1
                break
            elif price_arr[j] <= down_target:
                result[i] = 0
                break
        if count % 10000 == 0 and count > 0:
            print(f"  {count}/{total} labels computed...")
    return result


# --- Feature building ---
def build_features(data, valid_indices):
    prices = data["prices"]
    sizes = data["sizes"]
    ts_events = data["ts_events"]
    spreads = data["spreads"]
    price_to_mids = data["price_to_mids"]
    c10s_times = data["c10s_times"]
    c10s_values = data["c10s_values"]
    c1m_times = data["c1m_times"]
    c1m_values = data["c1m_values"]

    current_prices = prices[valid_indices]

    # Tick features: (N, 100, 2) — [price_delta_from_current, log1p_size]
    tick_p_windows = sliding_window_view(prices, TICK_LOOKBACK)
    tick_s_windows = sliding_window_view(np.log1p(sizes), TICK_LOOKBACK)
    win_starts = valid_indices - TICK_LOOKBACK
    tick_p = tick_p_windows[win_starts] - current_prices[:, None]
    tick_s = tick_s_windows[win_starts]
    tick_features = np.stack([tick_p, tick_s], axis=-1).astype(np.float32)

    # Spread features: (N, 10)
    spread_windows = sliding_window_view(spreads, SPREAD_LOOKBACK)
    spread_features = spread_windows[valid_indices - SPREAD_LOOKBACK].astype(
        np.float32
    )

    # Price-to-mid features: (N, 10)
    ptm_windows = sliding_window_view(price_to_mids, PRICE_TO_MID_LOOKBACK)
    ptm_features = ptm_windows[valid_indices - PRICE_TO_MID_LOOKBACK].astype(
        np.float32
    )

    # 10-second candle midpoints: (N, 30) relative to current price
    c10s_positions = np.searchsorted(c10s_times, ts_events[valid_indices], side="right")
    pad_c10s = np.pad(c10s_values, (CANDLE_10S_LOOKBACK, 0), mode="edge")
    c10s_idx = c10s_positions[:, None] + np.arange(CANDLE_10S_LOOKBACK)[None, :]
    candle_10s_features = (pad_c10s[c10s_idx] - current_prices[:, None]).astype(
        np.float32
    )

    # 1-minute candle midpoints: (N, 60) relative to current price
    c1m_positions = np.searchsorted(c1m_times, ts_events[valid_indices], side="right")
    pad_c1m = np.pad(c1m_values, (CANDLE_1M_LOOKBACK, 0), mode="edge")
    c1m_idx = c1m_positions[:, None] + np.arange(CANDLE_1M_LOOKBACK)[None, :]
    candle_1m_features = (pad_c1m[c1m_idx] - current_prices[:, None]).astype(
        np.float32
    )

    context_features = np.concatenate(
        [spread_features, ptm_features, candle_10s_features, candle_1m_features], axis=1
    )

    return tick_features, context_features


def get_valid_indices(data, labels, subsample_ticks=1):
    ts_events = data["ts_events"]
    min_ts = ts_events[0] + 3600 * 1_000_000_000
    all_indices = np.arange(len(labels))
    valid_mask = (
        (all_indices >= TICK_LOOKBACK)
        & (ts_events >= min_ts)
        & (labels >= 0)
        & (all_indices % subsample_ticks == 0)
    )
    return np.where(valid_mask)[0]


def build_live_features(trade_ticks, quote_tick, candle_10s_mids, candle_1m_mids):
    """Build feature tensors from live strategy cache data for a single prediction.

    Args:
        trade_ticks: list of last TICK_LOOKBACK TradeTick objects (oldest first)
        quote_tick: most recent QuoteTick
        candle_10s_mids: list/array of last CANDLE_10S_LOOKBACK 10-second candle midpoints
        candle_1m_mids: list/array of last CANDLE_1M_LOOKBACK 1-minute candle midpoints

    Returns:
        (tick_feat, ctx_feat) tensors ready for model forward pass, both with batch dim=1
    """
    current_price = float(trade_ticks[-1].price)

    prices = np.array([float(t.price) for t in trade_ticks[-TICK_LOOKBACK:]])
    sizes = np.array([float(t.size) for t in trade_ticks[-TICK_LOOKBACK:]])

    tick_p = prices - current_price
    tick_s = np.log1p(sizes)
    tick_feat = np.stack([tick_p, tick_s], axis=-1).astype(np.float32)

    bid = float(quote_tick.bid_price)
    ask = float(quote_tick.ask_price)
    mid = (ask + bid) / 2

    spread_feat = np.full(SPREAD_LOOKBACK, ask - bid, dtype=np.float32)
    ptm_feat = np.array(
        [float(t.price) - mid for t in trade_ticks[-PRICE_TO_MID_LOOKBACK:]],
        dtype=np.float32,
    )

    c10s = np.array(candle_10s_mids[-CANDLE_10S_LOOKBACK:], dtype=np.float64)
    c1m = np.array(candle_1m_mids[-CANDLE_1M_LOOKBACK:], dtype=np.float64)

    if len(c10s) < CANDLE_10S_LOOKBACK:
        c10s = np.pad(c10s, (CANDLE_10S_LOOKBACK - len(c10s), 0), mode="edge")
    if len(c1m) < CANDLE_1M_LOOKBACK:
        c1m = np.pad(c1m, (CANDLE_1M_LOOKBACK - len(c1m), 0), mode="edge")

    c10s_feat = (c10s - current_price).astype(np.float32)
    c1m_feat = (c1m - current_price).astype(np.float32)

    ctx_feat = np.concatenate([spread_feat, ptm_feat, c10s_feat, c1m_feat])

    tick_tensor = torch.from_numpy(tick_feat).unsqueeze(0)
    ctx_tensor = torch.from_numpy(ctx_feat).unsqueeze(0)
    return tick_tensor, ctx_tensor


def prepare_day(symbol, date_str, subsample_ticks=1):
    """Load one day of market-hours data, compute labels and features.

    date_str: 'YYYY-MM-DD'. Market hours: 09:30-16:00 ET (13:30-20:00 UTC during EDT).
    """
    start = f"{date_str} 12:30:00"
    end = f"{date_str} 20:00:00"

    print(f"\n--- {date_str} ---")
    data = load_data(symbol, start, end)
    if len(data["prices"]) == 0:
        print(f"  No data for {date_str}, skipping")
        return None

    print("  Computing labels...")
    labels = compute_labels(data["prices"], THRESHOLD, subsample_ticks=subsample_ticks)
    print(
        f"  up={np.sum(labels == 1)}, down={np.sum(labels == 0)}, "
        f"unlabeled={np.sum(labels == -1)}"
    )

    valid_indices = get_valid_indices(data, labels, subsample_ticks=subsample_ticks)
    if len(valid_indices) == 0:
        print(f"  No valid samples for {date_str}, skipping")
        return None

    tick_features, context_features = build_features(data, valid_indices)
    labels_arr = labels[valid_indices].astype(np.float32)
    ts_arr = data["ts_events"][valid_indices]
    prices_arr = data["prices"][valid_indices]

    print(f"  Valid samples: {len(valid_indices)}")
    return {
        "tick_features": tick_features,
        "context_features": context_features,
        "labels": labels_arr,
        "ts_events": ts_arr,
        "prices": prices_arr,
    }


def prepare_multi_day(symbol, dates, subsample_ticks=1):
    """Load and prepare data for multiple days, concatenated."""
    all_tick = []
    all_ctx = []
    all_labels = []
    all_ts = []
    all_prices = []

    for date_str in dates:
        result = prepare_day(symbol, date_str, subsample_ticks=subsample_ticks)
        if result is None:
            continue
        all_tick.append(result["tick_features"])
        all_ctx.append(result["context_features"])
        all_labels.append(result["labels"])
        all_ts.append(result["ts_events"])
        all_prices.append(result["prices"])

    tick_features = np.concatenate(all_tick)
    context_features = np.concatenate(all_ctx)
    labels_arr = np.concatenate(all_labels)
    ts_events = np.concatenate(all_ts)
    prices = np.concatenate(all_prices)

    print(f"\n--- Combined ---")
    print(f"Total samples: {len(labels_arr)}")
    print(f"Class balance: {labels_arr.mean():.3f} (1=up, 0=down)")

    return {
        "tick_features": tick_features,
        "context_features": context_features,
        "labels": labels_arr,
        "ts_events": ts_events,
        "prices": prices,
    }


def get_device():
    return torch.device("mps" if torch.backends.mps.is_available() else "cpu")


def load_model(model_path, device):
    model = TickClassifier(
        tick_input_dim=2, context_dim=CONTEXT_DIM, hidden_dim=HIDDEN_DIM
    ).to(device)
    model.load_state_dict(torch.load(model_path, map_location=device, weights_only=True))
    model.eval()
    return model
