import time

import numpy as np
import torch
from torch.utils.data import DataLoader

from lstm.lstm_common import (
    CANDLE_10S_LOOKBACK,
    CANDLE_1M_LOOKBACK,
    PRICE_TO_MID_LOOKBACK,
    SPREAD_LOOKBACK,
    THRESHOLD,
    TickDataset,
    compute_labels,
    build_features,
    get_device,
    get_valid_indices,
    load_data,
    load_model,
)

# --- Config ---
best_model_path = "lstm/lstm_best.pt"

symbol = "AAPL"
start = "2026-04-13 12:30:00"
end = "2026-04-13 20:00:00"

LABEL_SUBSAMPLE_TICKS = 50
BATCH_SIZE = 256

# --- Load data ---
data = load_data(symbol, start, end)

print("Computing labels...")
t0 = time.time()
labels = compute_labels(data["prices"], THRESHOLD, subsample_ticks=LABEL_SUBSAMPLE_TICKS)
print(f"Labels computed in {time.time() - t0:.1f}s")

valid_indices = get_valid_indices(data, labels, subsample_ticks=LABEL_SUBSAMPLE_TICKS)
print(f"Valid samples: {len(valid_indices)}")

print("Building features...")
tick_features, context_features = build_features(data, valid_indices)
labels_arr = labels[valid_indices].astype(np.float32)

# --- Load model ---
device = get_device()
model = load_model(best_model_path, device)


def evaluate(tick_feat_np, ctx_feat_np, labels_np):
    ds = TickDataset(tick_feat_np, ctx_feat_np, labels_np)
    dl = DataLoader(ds, batch_size=BATCH_SIZE, shuffle=False, num_workers=0)
    correct = 0
    total = 0
    with torch.no_grad():
        for tf, cf, target in dl:
            tf = tf.to(device)
            cf = cf.to(device)
            logits = model(tf, cf)
            preds = (logits > 0).float()
            correct += (preds == target.to(device)).sum().item()
            total += len(target)
    return correct / total


# --- Baseline accuracy ---
baseline_acc = evaluate(tick_features, context_features, labels_arr)
print(f"\nBaseline accuracy: {baseline_acc:.4f}\n")

# --- Feature group definitions ---
# Context features layout: [spread(10), price_to_mid(10), candle_10s(30), candle_1m(60)]
ctx_groups = {
    "spread": (0, SPREAD_LOOKBACK),
    "price_to_mid": (SPREAD_LOOKBACK, SPREAD_LOOKBACK + PRICE_TO_MID_LOOKBACK),
    "candle_10s_mid": (
        SPREAD_LOOKBACK + PRICE_TO_MID_LOOKBACK,
        SPREAD_LOOKBACK + PRICE_TO_MID_LOOKBACK + CANDLE_10S_LOOKBACK,
    ),
    "candle_1m_mid": (
        SPREAD_LOOKBACK + PRICE_TO_MID_LOOKBACK + CANDLE_10S_LOOKBACK,
        SPREAD_LOOKBACK + PRICE_TO_MID_LOOKBACK + CANDLE_10S_LOOKBACK + CANDLE_1M_LOOKBACK,
    ),
}

results = []

# --- Ablation: zero out each tick feature channel ---
for ch_idx, ch_name in enumerate(["tick_price_delta", "tick_log_size"]):
    ablated = tick_features.copy()
    ablated[:, :, ch_idx] = 0.0
    acc = evaluate(ablated, context_features, labels_arr)
    drop = baseline_acc - acc
    results.append((ch_name, acc, drop))
    print(f"  Zero {ch_name:20s} → acc={acc:.4f}  drop={drop:+.4f}")

# --- Ablation: zero out each context feature group ---
for group_name, (start_col, end_col) in ctx_groups.items():
    ablated_ctx = context_features.copy()
    ablated_ctx[:, start_col:end_col] = 0.0
    acc = evaluate(tick_features, ablated_ctx, labels_arr)
    drop = baseline_acc - acc
    results.append((group_name, acc, drop))
    print(f"  Zero {group_name:20s} → acc={acc:.4f}  drop={drop:+.4f}")

# --- Ablation: zero out ALL tick features ---
ablated_all_tick = np.zeros_like(tick_features)
acc = evaluate(ablated_all_tick, context_features, labels_arr)
drop = baseline_acc - acc
results.append(("ALL_tick_features", acc, drop))
print(f"  Zero {'ALL_tick_features':20s} → acc={acc:.4f}  drop={drop:+.4f}")

# --- Ablation: zero out ALL context features ---
ablated_all_ctx = np.zeros_like(context_features)
acc = evaluate(tick_features, ablated_all_ctx, labels_arr)
drop = baseline_acc - acc
results.append(("ALL_context", acc, drop))
print(f"  Zero {'ALL_context':20s} → acc={acc:.4f}  drop={drop:+.4f}")

# --- Summary sorted by importance ---
results.sort(key=lambda x: x[2], reverse=True)
print("\n--- Feature importance (sorted by accuracy drop) ---")
print(f"{'Feature':<25s} {'Acc w/o':>8s} {'Drop':>8s}")
print("-" * 43)
for name, acc, drop in results:
    marker = " ***" if drop > 0.01 else ""
    print(f"{name:<25s} {acc:>8.4f} {drop:>+8.4f}{marker}")
print(f"\n{'Baseline':<25s} {baseline_acc:>8.4f}")
