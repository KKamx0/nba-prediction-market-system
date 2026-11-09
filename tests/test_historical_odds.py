from datetime import UTC, datetime

import pandas as pd
import pytest

from edgebot.ingest.historical_odds import (
    ColumnMap,
    ImportAbortedError,
    import_sbr,
    import_wide,
    load_file,
)


def test_wide_import_with_custom_column_names():
    df = pd.DataFrame(
        {
            "Date": ["2024-01-15", "2024-01-15"],
            "Home Team": ["Boston Celtics", "LA Clippers"],
            "Away Team": ["New York Knicks", "Golden State"],
            "H_ML": [-250, 120],
            "A_ML": [210, -140],
        }
    )
    cmap = ColumnMap(
        date="Date", home="Home Team", away="Away Team", home_odds="H_ML", away_odds="A_ML"
    )
    rep = import_wide(df, cmap, "kaggle")
    assert rep.reject_rate == 0
    r = rep.rows[0]
    assert (r["home"], r["away"], r["kind"], r["source"]) == (
        "BOS",
        "NYK",
        "closing",
        "archive:kaggle",
    )
    assert r["home_dec"] == pytest.approx(1.4)
    assert r["away_dec"] == pytest.approx(3.1)
    # stamped at the assumed 7pm ET tip (EST in January = 00:00 UTC next day)
    assert r["known_at"] == datetime(2024, 1, 16, 0, 0, tzinfo=UTC)


def test_wide_import_rejects_bad_rows_with_reasons():
    df = pd.DataFrame(
        {
            "date": ["2024-01-15", "2024-01-15", "not a date", "2024-01-15"],
            "home": ["BOS", "Springfield", "BOS", "BOS"],
            "away": ["NYK", "NYK", "NYK", "BOS"],
            "home_ml": [-250, -110, -110, -110],
            "away_ml": [210, -110, -110, 50],
        }
    )
    rep = import_wide(df, ColumnMap(), "x")
    assert len(rep.rows) == 1
    assert rep.rejected == {"unknown team": 1, "bad date or odds": 2}


def test_wide_import_missing_column_aborts():
    with pytest.raises(ImportAbortedError, match="missing columns"):
        import_wide(pd.DataFrame({"date": []}), ColumnMap(), "x")


def test_sbr_layout_pairs_rows_and_infers_year():
    df = pd.DataFrame(
        {
            "Date": [1024, 1024, 105, 105],
            "Rot": [501, 502, 701, 702],
            "VH": ["V", "H", "V", "H"],
            "Team": ["GoldenState", "LALakers", "NewJersey", "Boston"],
            "Final": [120, 115, 99, 101],
            "ML": [-150, 130, 400, -500],
        }
    )
    rep = import_sbr(df, season_start_year=2023, source="sbr")
    assert rep.reject_rate == 0
    first, second = rep.rows
    assert (first["game_date"], first["home"], first["away"]) == ("2023-10-24", "LAL", "GSW")
    assert first["home_dec"] == pytest.approx(2.3)
    assert (second["game_date"], second["home"], second["away"]) == ("2024-01-05", "BOS", "BKN")


def test_sbr_layout_rejects_misordered_pairs_and_missing_lines():
    df = pd.DataFrame(
        {
            "Date": [1024, 1024, 1025, 1025],
            "VH": ["H", "V", "V", "H"],
            "Team": ["LALakers", "GoldenState", "Boston", "NewYork"],
            "ML": [130, -150, "NL", -120],
        }
    )
    rep = import_sbr(df, season_start_year=2023, source="sbr")
    assert rep.rows == []
    assert sum(rep.rejected.values()) == 2


def test_load_file_aborts_when_mapping_looks_wrong(tmp_path):
    path = tmp_path / "odds.csv"
    pd.DataFrame(
        {
            "date": ["2024-01-15"] * 3,
            "home": ["X", "Y", "BOS"],
            "away": ["NYK"] * 3,
            "home_ml": [-110] * 3,
            "away_ml": [-110] * 3,
        }
    ).to_csv(path, index=False)
    with pytest.raises(ImportAbortedError, match="rejected"):
        load_file(path, layout="wide", source="x")
    rep = load_file(path, layout="wide", source="x", max_reject_rate=0.9)
    assert len(rep.rows) == 1


def test_imported_rows_store_and_dedupe(store):
    df = pd.DataFrame(
        {
            "date": ["2024-01-15"],
            "home": ["BOS"],
            "away": ["NYK"],
            "home_ml": [-250],
            "away_ml": [210],
        }
    )
    rows = import_wide(df, ColumnMap(), "x").rows
    assert store.insert_odds_quotes(rows) == 1
    assert store.insert_odds_quotes(rows) == 0
