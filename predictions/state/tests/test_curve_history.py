import tempfile
from pathlib import Path
from predictions.state import curve_history

def test_init_creates_schema_idempotently():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.db"
        curve_history.init_db(db)
        curve_history.init_db(db)  # idempotent
        with curve_history._connect(db) as con:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"curve_snapshots", "smart_wallet_seed"}.issubset(tables)

def test_record_and_read_snapshot():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.db"
        curve_history.init_db(db)
        curve_history.record_snapshot(db, mint="A" * 44, fetched_at_unix=1000, bonding_curve_pct=42.5,
                                      market_cap_sol=12.3, reply_count=4, recent_trades_count=17)
        rows = curve_history.read_snapshots(db, mint="A" * 44, since_unix=0)
        assert len(rows) == 1
        assert rows[0]["bonding_curve_pct"] == 42.5
