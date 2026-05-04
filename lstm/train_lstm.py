
import torch
import torch.nn as nn
from torch.utils.data import DataLoader

from lstm.lstm_common import (
    CONTEXT_DIM,
    HIDDEN_DIM,
    TickClassifier,
    TickDataset,
    get_device,
    prepare_multi_day,
)

# --- Config ---
symbol = "AAPL"
train_dates = [
    # January 2026 (Jan 1 = New Year's, Jan 19 = MLK Day)
    "2026-01-02", "2026-01-05", "2026-01-06", "2026-01-08", "2026-01-09",
    "2026-01-12", "2026-01-13", "2026-01-14", "2026-01-15", "2026-01-16",
    "2026-01-20", "2026-01-21", "2026-01-22", "2026-01-23",
    "2026-01-26", "2026-01-27", "2026-01-28", "2026-01-29", "2026-01-30",
    # March 2026
    "2026-03-02",
    "2026-03-03",
    "2026-03-04",
    "2026-03-05",
    "2026-03-09",
    "2026-03-10",
    "2026-03-11",
    "2026-03-12",
    "2026-03-13",
    "2026-03-16",
    "2026-03-17",
    "2026-03-18",
    "2026-03-19",
    "2026-03-20",
    "2026-03-23",
    "2026-03-24",
    "2026-03-25",
    "2026-03-27",
    "2026-03-30",
    "2026-03-31",
    # April 2026 (April 3 = Good Friday, closed)
    "2026-04-01",
    "2026-04-02",
    "2026-04-06",
    "2026-04-07",
    "2026-04-08",
    "2026-04-09",
    "2026-04-10",
    "2026-04-13",
    "2026-04-14",
]
val_dates = [
    "2026-01-07",
    "2026-01-30",
    "2026-03-06",
    "2026-03-26",
    "2026-04-15",
]

LABEL_SUBSAMPLE_TICKS = 30
LEARNING_RATE = 3e-4
WEIGHT_DECAY = 1e-4
BATCH_SIZE = 256
MAX_EPOCHS = 50
PATIENCE = 10

# --- Load and prepare data ---
train_result = prepare_multi_day(symbol, train_dates, subsample_ticks=LABEL_SUBSAMPLE_TICKS)
val_result = prepare_multi_day(symbol, val_dates, subsample_ticks=LABEL_SUBSAMPLE_TICKS)

tick_features = train_result["tick_features"]
context_features = train_result["context_features"]
labels_arr = train_result["labels"]

# --- Train/val datasets (train = first 4 days, val = last day) ---
split_idx = len(labels_arr)

train_ds = TickDataset(tick_features, context_features, labels_arr)
val_ds = TickDataset(
    val_result["tick_features"],
    val_result["context_features"],
    val_result["labels"],
)

train_dl = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True, num_workers=0)
val_dl = DataLoader(val_ds, batch_size=BATCH_SIZE, shuffle=False, num_workers=0)

# --- Training setup ---
device = get_device()
model = TickClassifier(tick_input_dim=2, context_dim=CONTEXT_DIM, hidden_dim=HIDDEN_DIM).to(device)

pos_count = max((labels_arr == 1).sum(), 1)
neg_count = max((labels_arr == 0).sum(), 1)
pos_weight = torch.tensor([neg_count / pos_count], dtype=torch.float32).to(device)
criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
optimizer = torch.optim.AdamW(model.parameters(), lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY)
scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
    optimizer, mode="min", factor=0.5, patience=3, min_lr=1e-5
)

print(f"\nModel params: {sum(p.numel() for p in model.parameters()):,}")
print(f"pos_weight: {pos_weight.item():.3f}")
print(f"Train: {len(train_ds):,} | Val: {len(val_ds):,}")
print(f"Device: {device}\n")

# --- Training loop ---
best_val_loss = float("inf")
patience_counter = 0
best_model_path = "lstm/lstm_best.pt"

for epoch in range(MAX_EPOCHS):
    model.train()
    train_loss = 0.0
    train_correct = 0
    train_total = 0

    for tick_feat, ctx_feat, target in train_dl:
        tick_feat = tick_feat.to(device)
        ctx_feat = ctx_feat.to(device)
        target = target.to(device)

        optimizer.zero_grad()
        logits = model(tick_feat, ctx_feat)
        loss = criterion(logits, target)
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()

        train_loss += loss.item() * len(target)
        train_correct += ((logits > 0).float() == target).sum().item()
        train_total += len(target)

    model.eval()
    val_loss = 0.0
    val_correct = 0
    val_total = 0

    with torch.no_grad():
        for tick_feat, ctx_feat, target in val_dl:
            tick_feat = tick_feat.to(device)
            ctx_feat = ctx_feat.to(device)
            target = target.to(device)

            logits = model(tick_feat, ctx_feat)
            loss = criterion(logits, target)

            val_loss += loss.item() * len(target)
            val_correct += ((logits > 0).float() == target).sum().item()
            val_total += len(target)

    train_loss /= train_total
    val_loss /= val_total
    train_acc = train_correct / train_total
    val_acc = val_correct / val_total

    scheduler.step(val_loss)
    lr = optimizer.param_groups[0]["lr"]

    print(
        f"Epoch {epoch + 1:3d}/{MAX_EPOCHS} | "
        f"train_loss={train_loss:.6f} train_acc={train_acc:.4f} | "
        f"val_loss={val_loss:.6f} val_acc={val_acc:.4f} | "
        f"lr={lr:.1e}"
    )

    if val_loss < best_val_loss:
        best_val_loss = val_loss
        patience_counter = 0
        torch.save(model.state_dict(), best_model_path)
    else:
        patience_counter += 1
        if patience_counter >= PATIENCE:
            print(f"Early stopping at epoch {epoch + 1}")
            break

print(f"\nBest val_loss: {best_val_loss:.6f}")
print(f"Model saved to: {best_model_path}")
