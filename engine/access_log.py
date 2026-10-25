r"""
access_log.py -- append-only record of unlock/lock attempts (method,
outcome, timestamp), for after-the-fact awareness of when the vault was
accessed. Detection, not prevention -- this doesn't stop anyone, it just
means you'd have a record. Never logs secret values (PINs, recovery
codes, the vault password) -- only the outcome string.

Each entry embeds a hash of the previous entry, so editing or deleting
an entry from the MIDDLE of the log breaks the chain from that point
forward, detectable via verify_chain(). This does NOT, and cannot,
detect the whole file being deleted outright on its own -- there's no
way to detect the absence of a file that isn't there to check. It also
doesn't stop someone rewriting the file from scratch as a shorter-but
internally-consistent fake chain starting fresh from genesis. This is a
record for you, not a secret and not a tamper-proof seal -- the content
is plaintext, readable by anyone with the same E:\ access that's already
assumed throughout this project's threat model.

Red-team fix pass (2026-10) -- three executed attacks against the above:
tail truncation (delete the last entries, chain stays intact, history
reads "OK"), forged appends with valid chaining (plus burial: 20 junk
entries push the interesting one out of the UI's last-20 window), and a
single invalid-UTF-8 byte killing both the history view AND all further
logging. Fixes, all best-effort and never raising:

  * Entry lines gain an optional 6th field: a keyed MAC
    (engine/state_binding.py, LOG_PURPOSE) over "<body>|<entry_hash>".
    A drive-only attacker can still append syntactically valid lines,
    but not valid MACs -- verify_chain() flags any 6-field entry whose
    MAC doesn't check (when key material exists). 5-field lines remain
    readable (backward compatible with every existing log); they simply
    carry no MAC to check.
  * The chain's (entry count, head hash) is mirrored into the C:\
    anchor witness on every append (monotonic max-merge). verify_chain()
    compares the witness against the file: fewer entries than the
    witness (tail truncation), a different head (rewrite/genesis
    restart), or MORE entries than the witness (forged append) all read
    as (False, -2). Without key material the witness isn't written and
    verification falls back to the legacy chain-only behavior.
  * Files are read with errors="replace" so one corrupt byte can't
    take out the history view; lines that don't parse are skipped by
    read_entries() but flagged as a broken chain by verify_chain().
"""
import os
import hashlib
import time
import threading

_LOG_PATH = r"E:\LockerDrive\access_log.txt"
_DRIVE_ROOT = r"E:\\"
_GENESIS_HASH = "0" * 64
_log_lock = threading.RLock()

# Module-level mirror of the chain state (entry count, head hash), used
# to update the C:\ anchor witness after each append without re-reading
# the whole file. Lazily initialized from the file on first use per
# process; the witness itself is monotonic (max-merged in the anchor),
# so a stale cache can only ever make the witness STRICTER than the
# file -- which is exactly the direction we want discrepancies to be
# reported in.
_chain_state = {"count": None, "head_hash": None}


def _drive_connected() -> bool:
    return os.path.exists(_DRIVE_ROOT)


def _last_hash() -> str:
    if not os.path.exists(_LOG_PATH):
        return _GENESIS_HASH
    with open(_LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
        lines = [l for l in f if l.strip()]
    if not lines:
        return _GENESIS_HASH
    parts = lines[-1].rstrip("\n").split("|")
    return parts[4] if len(parts) in (5, 6) else _GENESIS_HASH


def _read_lines() -> tuple:
    """(entries, bad_line_count). Reads with errors="replace" (a single
    corrupt byte must not kill the history view) and counts non-empty
    lines that don't parse as 5- or 6-field entries."""
    entries = []
    bad = 0
    if not os.path.exists(_LOG_PATH):
        return entries, bad
    with open(_LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("|")
            if len(parts) not in (5, 6):
                bad += 1
                continue
            entry = {
                "timestamp": parts[0], "method": parts[1], "outcome": parts[2],
                "prev_hash": parts[3], "entry_hash": parts[4],
            }
            if len(parts) == 6:
                entry["mac"] = parts[5]
            entries.append(entry)
    return entries, bad


def _sync_chain_state() -> None:
    if _chain_state["count"] is None:
        entries, _ = _read_lines()
        _chain_state["count"] = len(entries)
        _chain_state["head_hash"] = entries[-1]["entry_hash"] if entries else _GENESIS_HASH


def record(method: str, outcome: str) -> None:
    """Best-effort -- never raises. A logging failure must never block or
    crash a real unlock/lock attempt; the auth decision already happened
    by the time this is called. Appends "<ts>|<method>|<outcome>|<prev>|
    <hash>" plus, when key material exists, a 6th field: the keyed MAC
    over "<body>|<entry_hash>". Also mirrors (count, head_hash) into the
    C:\\ anchor witness so later truncation/rewrites are detectable."""
    try:
        with _log_lock:
            if not _drive_connected():
                return
            os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
            _sync_chain_state()
            timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            prev_hash = _last_hash()
            if prev_hash == _GENESIS_HASH and _chain_state["head_hash"]:
                prev_hash = _chain_state["head_hash"]
            safe_method = str(method).replace("|", "/").replace("\r", " ").replace("\n", " ").strip() or "UNKNOWN"
            safe_outcome = str(outcome).replace("|", "/").replace("\r", " ").replace("\n", " ").strip() or "UNKNOWN"
            body = f"{timestamp}|{safe_method}|{safe_outcome}|{prev_hash}"
            entry_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
            line = f"{body}|{entry_hash}"
            try:
                import state_binding
                mac = state_binding.compute_binding(
                    state_binding.LOG_PURPOSE, f"{body}|{entry_hash}".encode("utf-8"))
            except Exception:
                mac = None
            if mac:
                line = f"{line}|{mac}"
            with open(_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            _chain_state["count"] = (_chain_state["count"] or 0) + 1
            _chain_state["head_hash"] = entry_hash
            try:
                import local_anchor
                local_anchor.witness_update(
                    "log", count=_chain_state["count"], head_hash=entry_hash)
            except Exception:
                pass
    except Exception:
        pass


def read_entries() -> list:
    """Parsed entries, oldest first. [] on a missing/empty log, never
    raises. Accepts both the legacy 5-field lines and the new 6-field
    (MAC-carrying) lines; lines that parse as neither are skipped
    (verify_chain is what flags them)."""
    with _log_lock:
        entries, _bad = _read_lines()
        return entries


def verify_chain():
    """(intact: bool, first_broken_index: int). first_broken_index is -1
    when intact, -2 when the C:\\ anchor witness disagrees with the file
    (truncated tail, rewritten genesis, or entries appended past what
    the witness recorded). See module docstring for exactly what this
    can and cannot detect."""
    with _log_lock:
        try:
            entries, bad = _read_lines()
            if bad:
                # skipped/corrupt lines: the chain is broken at the end of
                # what parsed -- a gap anywhere means later links can't be
                # trusted to connect to what came before it
                return False, len(entries)
            expected_prev = _GENESIS_HASH
            for i, e in enumerate(entries):
                if e["prev_hash"] != expected_prev:
                    return False, i
                body = f"{e['timestamp']}|{e['method']}|{e['outcome']}|{e['prev_hash']}"
                if hashlib.sha256(body.encode("utf-8")).hexdigest() != e["entry_hash"]:
                    return False, i
                if "mac" in e:
                    try:
                        import state_binding
                        check = state_binding.verify_binding(
                            state_binding.LOG_PURPOSE,
                            f"{body}|{e['entry_hash']}".encode("utf-8"),
                            e["mac"],
                        )
                    except Exception:
                        check = None
                    if check is False:
                        return False, i  # forged/edited entry with a MAC that doesn't verify
                expected_prev = e["entry_hash"]
            # Anchor-witness comparison: the (count, head_hash) mirror only
            # exists once key material has been available for a record();
            # without it (or on a tampered anchor) this is skipped and the
            # legacy chain-only verdict stands.
            try:
                import local_anchor
                witness = local_anchor.witness_get("log")
            except Exception:
                witness = None
            if witness:
                file_count = len(entries)
                file_head = entries[-1]["entry_hash"] if entries else _GENESIS_HASH
                w_count = witness.get("count")
                w_head = witness.get("head_hash")
                if (isinstance(w_count, int) and w_count != file_count) or (
                        isinstance(w_head, str) and w_head != file_head):
                    return False, -2
            return True, -1
        except Exception:
            # never raise -- a broken verification must not take down the
            # history view; report the chain as broken instead
            return False, 0
