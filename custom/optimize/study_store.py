"""
SQLite store for multi-day parameter studies.

A study is identified by a hash of what it runs (symbol, time window and the params dict with its
swept lists), never by a name. Results are kept per `(params_hash, day)`, where `params_hash`
identifies one concrete backtest config, so a result computed for one study is reused by every
other study containing the same config. Running a study therefore only ever runs the pairs that
have no result yet: a brand-new grid, new catalog days (backfill), or whatever a crash left over.
"""

import hashlib
import itertools
import json
import sqlite3
from datetime import datetime
from enum import Enum
from pathlib import Path

import numpy as np
import pandas as pd

from nautilus_trader import PACKAGE_ROOT

STORE_PATH = PACKAGE_ROOT / "optuna_dbs" / "studies.db"
HASH_DISPLAY_LEN = 8

_SCHEMA = """
CREATE TABLE IF NOT EXISTS studies (
    study_hash TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_run_at TEXT
);
CREATE TABLE IF NOT EXISTS param_sets (
    params_hash TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    params_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS study_param_sets (
    study_hash TEXT NOT NULL,
    params_hash TEXT NOT NULL,
    PRIMARY KEY (study_hash, params_hash)
);
CREATE TABLE IF NOT EXISTS day_results (
    params_hash TEXT NOT NULL,
    day TEXT NOT NULL,
    pnl REAL NOT NULL,
    entered INTEGER NOT NULL,
    avg_entry_price REAL,
    max_loser REAL,
    run_at TEXT NOT NULL,
    PRIMARY KEY (params_hash, day)
);
"""


def open_store(path=STORE_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    return conn


def _now():
    return datetime.now().isoformat(timespec="seconds")


# HASHING ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def _canonical(value):
    """
    Normalize a param value so equal configs hash equally.

    Numbers all become floats, so `stop_loss=3` and `3.0` (or a numpy float out of `linspace_float`)
    are one config. Enums hash by their value, so a StrEnum and its plain string do too.
    """
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (int, float, np.integer, np.floating)):
        return round(float(value), 10)
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items()}
    return value


def _sha1(obj) -> str:
    return hashlib.sha1(json.dumps(_canonical(obj), sort_keys=True).encode(), usedforsecurity=False).hexdigest()


def _hash(kind, symbol, start_time, end_time, params) -> str:
    return _sha1({"kind": kind, "symbol": symbol, "start_time": start_time, "end_time": end_time, "params": params})


def spec_hash(symbol, start_time, end_time, params) -> str:
    """Hash a whole study spec. `params` keeps its lists, so changing the grid changes the hash."""
    return _hash("study", symbol, start_time, end_time, params)


def params_hash(symbol, start_time, end_time, config) -> str:
    """Hash one concrete backtest: the things that decide a day's result."""
    return _hash("params", symbol, start_time, end_time, config.config_hash)


def _to_json(obj) -> str:
    def default(value):
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            return float(value)
        raise TypeError(f"Cannot serialize {value!r}")

    return json.dumps(obj, default=default)


def expand_grid(params: dict) -> list[dict]:
    """Every concrete param set of `params`, where each list value is swept."""
    swept = {name: val for name, val in params.items() if isinstance(val, list)}
    for name, values in swept.items():
        if len(values) == 0:
            raise ValueError(f"{name}: an empty list sweeps nothing")
    grid = []
    for combo in itertools.product(*swept.values()):
        grid.append({**params, **dict(zip(swept, combo, strict=True))})
    return grid


def swept_names(spec_params: dict) -> list[str]:
    return [name for name, val in spec_params.items() if isinstance(val, list)]


# STUDIES ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def upsert_study(conn, symbol, start_time, end_time, params, configs) -> str:
    """Create the study for this spec if it is new, register its configs (`params` expanded), and return its hash."""
    study_hash = spec_hash(symbol, start_time, end_time, params)
    conn.execute(
        "INSERT OR IGNORE INTO studies (study_hash, symbol, start_time, end_time, spec_json, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (study_hash, symbol, start_time, end_time, _to_json(params), _now()),
    )
    for config in configs:
        add_param_set(conn, study_hash, symbol, start_time, end_time, config)
    conn.commit()
    return study_hash


def add_param_set(conn, study_hash, symbol, start_time, end_time, config) -> str:
    p_hash = params_hash(symbol, start_time, end_time, config)
    conn.execute(
        "INSERT OR IGNORE INTO param_sets (params_hash, symbol, params_json) VALUES (?, ?, ?)",
        (p_hash, symbol, config.json().decode()),
    )
    conn.execute("INSERT OR IGNORE INTO study_param_sets (study_hash, params_hash) VALUES (?, ?)", (study_hash, p_hash))
    return p_hash


def study_exists(conn, study_hash: str) -> bool:
    return conn.execute("SELECT 1 FROM studies WHERE study_hash = ?", (study_hash,)).fetchone() is not None


def find_study(conn, hash_prefix: str) -> dict:
    """Look a study up by any unambiguous prefix of its hash."""
    rows = conn.execute(
        "SELECT study_hash, symbol, start_time, end_time, spec_json, created_at, last_run_at"
        " FROM studies WHERE study_hash LIKE ?",
        (f"{hash_prefix}%",),
    ).fetchall()
    if len(rows) == 0:
        raise KeyError(f"No study with hash starting {hash_prefix!r}")
    if len(rows) > 1:
        matches = ", ".join(r[0][:12] for r in rows)
        raise KeyError(f"Hash prefix {hash_prefix!r} is ambiguous: {matches}")
    keys = ["study_hash", "symbol", "start_time", "end_time", "spec_json", "created_at", "last_run_at"]
    study = dict(zip(keys, rows[0], strict=True))
    study["spec"] = json.loads(study.pop("spec_json"))
    return study


def touch_study(conn, study_hash):
    conn.execute("UPDATE studies SET last_run_at = ? WHERE study_hash = ?", (_now(), study_hash))
    conn.commit()


def list_studies(conn, symbol=None) -> pd.DataFrame:
    query = "SELECT study_hash, symbol, start_time, end_time, spec_json, created_at, last_run_at FROM studies"
    args = ()
    if symbol is not None:
        query += " WHERE symbol = ?"
        args = (symbol,)
    df = pd.read_sql_query(query + " ORDER BY created_at", conn, params=args)
    df["spec"] = df.pop("spec_json").map(json.loads)
    return df


# RESULTS ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def study_param_sets(conn, study_hash) -> pd.DataFrame:
    """The study's param sets as one row each: `params_hash` plus every param as a column."""
    rows = conn.execute(
        "SELECT ps.params_hash, ps.params_json FROM study_param_sets sps"
        " JOIN param_sets ps ON ps.params_hash = sps.params_hash WHERE sps.study_hash = ?",
        (study_hash,),
    ).fetchall()
    return pd.DataFrame([{"params_hash": h, **json.loads(p)} for h, p in rows])


def missing_pairs(conn, study_hash, days) -> list[tuple[str, str]]:
    """`(params_hash, day)` pairs of this study, over `days`, that have no result yet."""
    hashes = [
        r[0] for r in conn.execute("SELECT params_hash FROM study_param_sets WHERE study_hash = ?", (study_hash,))
    ]
    done = set(
        conn.execute(
            "SELECT dr.params_hash, dr.day FROM day_results dr"
            " JOIN study_param_sets sps ON sps.params_hash = dr.params_hash WHERE sps.study_hash = ?",
            (study_hash,),
        ).fetchall()
    )
    return [(h, d) for h in hashes for d in days if (h, d) not in done]


def insert_result(conn, p_hash, day, result: dict, commit=True):
    conn.execute(
        "INSERT OR REPLACE INTO day_results (params_hash, day, pnl, entered, avg_entry_price, max_loser, run_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            p_hash,
            day,
            float(result["pnl"]),
            int(result["entered"]),
            result.get("avg_entry_price"),
            result.get("max_loser"),
            _now(),
        ),
    )
    if commit:
        conn.commit()


def load_study_results(conn, study_hash) -> pd.DataFrame:
    """One row per (params_hash, day) result that this study has."""
    return pd.read_sql_query(
        "SELECT dr.params_hash, dr.day, dr.pnl, dr.entered FROM day_results dr"
        " JOIN study_param_sets sps ON sps.params_hash = dr.params_hash WHERE sps.study_hash = ?",
        conn,
        params=(study_hash,),
    )


def load_symbol_results(conn, symbol) -> pd.DataFrame:
    """One row per (params_hash, day) result of any param set in `symbol`'s studies, with every stored stat."""
    return pd.read_sql_query(
        "SELECT params_hash, day, pnl, entered, avg_entry_price, max_loser FROM day_results"
        " WHERE params_hash IN (SELECT sps.params_hash FROM study_param_sets sps"
        " JOIN studies s ON s.study_hash = sps.study_hash WHERE s.symbol = ?)",
        conn,
        params=(symbol,),
    )


def delete_study_results(conn, study_hash) -> int:
    """
    Delete every day result of this study's param sets. Results are shared, so this also clears
    them for any other study containing the same configs -- they get re-run with this one.
    """
    cur = conn.execute(
        "DELETE FROM day_results WHERE params_hash IN (SELECT params_hash FROM study_param_sets WHERE study_hash = ?)",
        (study_hash,),
    )
    conn.commit()
    return cur.rowcount
