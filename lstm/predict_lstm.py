import time

import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader

pd.set_option("display.max_columns", None)
pd.set_option("display.max_rows", 100)
pd.set_option("display.width", None)

from lstm.lstm_common import (
    THRESHOLD,
    TickDataset,
    build_features,
    compute_labels,
    get_device,
    get_valid_indices,
    load_data,
    load_model,
)

# --- Config ---
best_model_path = "lstm/lstm_best.pt"

symbol = "AAPL"
start = "2026-04-13 13:30:00"
end = "2026-04-13 17:00:00"

LABEL_SUBSAMPLE_TICKS = 50
BATCH_SIZE = 256

# --- Load and prepare data ---
data = load_data(symbol, start, end)

print("Computing labels...")
t0 = time.time()
labels = compute_labels(data["prices"], THRESHOLD, subsample_ticks=LABEL_SUBSAMPLE_TICKS)
print(f"Labels computed in {time.time() - t0:.1f}s")
print(
    f"  up={np.sum(labels == 1)}, down={np.sum(labels == 0)}, "
    f"unlabeled={np.sum(labels == -1)}"
)

valid_indices = get_valid_indices(data, labels, subsample_ticks=LABEL_SUBSAMPLE_TICKS)
print(f"Valid samples: {len(valid_indices)}")

print("Building features...")
tick_features, context_features = build_features(data, valid_indices)
labels_arr = labels[valid_indices].astype(np.float32)

# --- Use last 10% as validation (same split as training) ---
split_idx = int(len(valid_indices) * 0.9)

val_tick = tick_features[split_idx:]
val_ctx = context_features[split_idx:]
val_labels = labels_arr[split_idx:]
val_indices = valid_indices[split_idx:]

val_ds = TickDataset(val_tick, val_ctx, val_labels)
val_dl = DataLoader(val_ds, batch_size=BATCH_SIZE, shuffle=False, num_workers=0)

# --- Load model ---
device = get_device()
model = load_model(best_model_path, device)
print(f"Loaded model from {best_model_path}")

# --- Latency benchmark ---
print("\nBenchmarking single-prediction latency...")
single_ds = TickDataset(val_tick[:1], val_ctx[:1], val_labels[:1])
single_dl = DataLoader(single_ds, batch_size=1, num_workers=0)

for tick_feat, ctx_feat, _ in single_dl:
    tick_feat = tick_feat.to(device)
    ctx_feat = ctx_feat.to(device)
    break

with torch.no_grad():
    model(tick_feat, ctx_feat)

n_warmup = 5
n_runs = 50
for _ in range(n_warmup):
    with torch.no_grad():
        model(tick_feat, ctx_feat)

if torch.backends.mps.is_available():
    torch.mps.synchronize()

times = []
for _ in range(n_runs):
    t0 = time.perf_counter()
    with torch.no_grad():
        model(tick_feat, ctx_feat)
    if torch.backends.mps.is_available():
        torch.mps.synchronize()
    times.append(time.perf_counter() - t0)

times_ms = [t * 1000 for t in times]
print(
    f"Single prediction latency ({n_runs} runs):\n"
    f"  mean:   {np.mean(times_ms):.2f} ms\n"
    f"  median: {np.median(times_ms):.2f} ms\n"
    f"  min:    {np.min(times_ms):.2f} ms\n"
    f"  max:    {np.max(times_ms):.2f} ms\n"
    f"  p95:    {np.percentile(times_ms, 95):.2f} ms\n"
)

# --- Predictions on validation set ---
all_logits = []
all_targets = []

with torch.no_grad():
    for tick_feat, ctx_feat, target in val_dl:
        tick_feat = tick_feat.to(device)
        ctx_feat = ctx_feat.to(device)

        logits = model(tick_feat, ctx_feat)
        all_logits.append(logits.cpu())
        all_targets.append(target)

logits = torch.cat(all_logits)
targets = torch.cat(all_targets)
probs = torch.sigmoid(logits).numpy()
preds = (logits > 0).float().numpy()
actuals = targets.numpy()

# --- Results ---
ts_events = data["ts_events"]
prices = data["prices"]

results_df = pd.DataFrame(
    {
        "prediction_time_est": pd.to_datetime(ts_events[val_indices], utc=True)
        .tz_convert("US/Eastern")
        .strftime("%H:%M:%S"),
        "price": prices[val_indices].round(2),
        "prob_up": probs.round(3),
        "predicted": preds.astype(int),
        "actual": actuals.astype(int),
        "correct": (preds == actuals).astype(int),
    }
)

accuracy = results_df["correct"].mean()
n_up_pred = (results_df["predicted"] == 1).sum()
n_down_pred = (results_df["predicted"] == 0).sum()
n_up_actual = (results_df["actual"] == 1).sum()
n_down_actual = (results_df["actual"] == 0).sum()

tp = ((results_df["predicted"] == 1) & (results_df["actual"] == 1)).sum()
fp = ((results_df["predicted"] == 1) & (results_df["actual"] == 0)).sum()
fn = ((results_df["predicted"] == 0) & (results_df["actual"] == 1)).sum()
tn = ((results_df["predicted"] == 0) & (results_df["actual"] == 0)).sum()

precision_up = tp / max(tp + fp, 1)
recall_up = tp / max(tp + fn, 1)

print(results_df.to_string())
print(
    f"\nAccuracy: {accuracy:.3f}  n={len(results_df)}\n"
    f"Predicted: up={n_up_pred} down={n_down_pred}\n"
    f"Actual:    up={n_up_actual} down={n_down_actual}\n"
    f"\nConfusion matrix:\n"
    f"  TP={tp}  FP={fp}\n"
    f"  FN={fn}  TN={tn}\n"
    f"\nPrecision(up): {precision_up:.3f}  Recall(up): {recall_up:.3f}"
)
