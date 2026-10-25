"""
appdata.py — High-performance cached settings & game telemetry persistence.

Uses mtime-aware in-memory caching and atomic file replacement so UI interactions
(tab switches, volume sliders, chat messages) never block the main GUI thread on
disk I/O, while changes written by external game subprocesses are picked up
automatically.
"""

import copy
import json
import os
import threading
import time

DATA_DIR = os.environ.get(
    "KS_DATA_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
STATS_FILE = os.path.join(DATA_DIR, "stats.json")

DEFAULT_SETTINGS = {
    "theme": "light",      # light / dark / system
    "volume": 80,          # 0-100 (audio fx for the games)
    "window": "960x640",   # last window size + position
}

DEFAULT_STATS = {
    "games": {},       # "Snake" -> {"plays": 3, "high": 12, "best": 14, "wins": 1, "losses": 2, "last": "01 Oct 2026 21:03"}
    "chat_msgs": 0,
}

_lock = threading.RLock()
_cache = {
    "settings": {"path": None, "mtime": None, "data": None},
    "stats": {"path": None, "mtime": None, "data": None},
}


def _file_mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _read_json_cached(slot, path, fallback):
    with _lock:
        mtime = _file_mtime(path)
        entry = _cache[slot]
        if entry["path"] == path and entry["mtime"] == mtime and entry["data"] is not None:
            return copy.deepcopy(entry["data"])
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = copy.deepcopy(fallback)
        except Exception:
            data = copy.deepcopy(fallback)
        entry["path"] = path
        entry["mtime"] = mtime
        entry["data"] = copy.deepcopy(data)
        return copy.deepcopy(data)


def _write_json_cached(slot, path, data):
    with _lock:
        try:
            tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, path)
            _cache[slot]["path"] = path
            _cache[slot]["mtime"] = _file_mtime(path)
            _cache[slot]["data"] = copy.deepcopy(data)
        except Exception:
            pass


def load_settings():
    """settings.json merged over the defaults (missing keys get defaults)."""
    data = _read_json_cached("settings", SETTINGS_FILE, DEFAULT_SETTINGS)
    merged = dict(DEFAULT_SETTINGS)
    for k in DEFAULT_SETTINGS:
        if k in data:
            merged[k] = data[k]
    if merged["theme"] not in ("light", "dark", "system"):
        merged["theme"] = "light"
    try:
        merged["volume"] = max(0, min(100, int(merged["volume"])))
    except Exception:
        merged["volume"] = 80
    if not isinstance(merged["window"], str):
        merged["window"] = "960x640"
    return merged


def save_settings(settings):
    _write_json_cached("settings", SETTINGS_FILE, settings)


def load_stats():
    data = _read_json_cached("stats", STATS_FILE, DEFAULT_STATS)
    merged = dict(DEFAULT_STATS)
    if isinstance(data.get("games"), dict):
        merged["games"] = copy.deepcopy(data["games"])
    try:
        merged["chat_msgs"] = max(0, int(data.get("chat_msgs", 0)))
    except Exception:
        merged["chat_msgs"] = 0
    return merged


def save_stats(stats):
    _write_json_cached("stats", STATS_FILE, stats)


def _game_entry(stats, name):
    entry = stats["games"].get(name)
    if not isinstance(entry, dict):
        entry = {}
    entry.setdefault("plays", 0)
    entry.setdefault("high", 0)
    entry.setdefault("best", 0)
    entry.setdefault("wins", 0)
    entry.setdefault("losses", 0)
    entry.setdefault("last", "")
    stats["games"][name] = entry
    return entry


def record_play(name):
    """Hub calls this when a game is launched."""
    try:
        stats = load_stats()
        entry = _game_entry(stats, name)
        entry["plays"] += 1
        entry["last"] = time.strftime("%d %b %Y %H:%M")
        save_stats(stats)
    except Exception:
        pass


def record_score(name, score, lower_better=False):
    """Games call this when they end. high = biggest score, best = smallest."""
    try:
        stats = load_stats()
        entry = _game_entry(stats, name)
        score = int(score)
        if lower_better:
            if entry["best"] == 0 or score < entry["best"]:
                entry["best"] = score
        else:
            if score > entry["high"]:
                entry["high"] = score
        save_stats(stats)
    except Exception:
        pass


def record_win(name, win):
    """Tic-Tac-Toe uses this (win = True/False)."""
    try:
        stats = load_stats()
        entry = _game_entry(stats, name)
        if win:
            entry["wins"] += 1
        else:
            entry["losses"] += 1
        entry["last"] = time.strftime("%d %b %Y %H:%M")
        save_stats(stats)
    except Exception:
        pass


def touch_chat():
    """+1 chat message sent."""
    try:
        stats = load_stats()
        stats["chat_msgs"] += 1
        save_stats(stats)
    except Exception:
        pass


def reset_stats():
    save_stats(copy.deepcopy(DEFAULT_STATS))
