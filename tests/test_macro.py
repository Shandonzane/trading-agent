import pandas as pd

from tradebot import macro


def test_monthly_data_only_counts_after_publication(monkeypatch):
    s = pd.Series([1.0, 2.0], index=pd.to_datetime(["2024-08-01", "2024-09-01"]))
    monkeypatch.setattr(macro, "fred", lambda _id: s.copy())
    days = pd.bdate_range("2024-09-01", "2024-10-31")
    k = macro._known("CPIAUCSL", days)
    # September's number is public 45 days after Sep 1 (Oct 16), not before.
    assert k[pd.Timestamp("2024-10-15")] == 1.0
    assert k[pd.Timestamp("2024-10-16")] == 2.0
