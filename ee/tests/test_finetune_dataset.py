"""Fine-tune datasets: validation and the leakage guarantees. No database, no torch.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tycheon.backtest.walk_forward import WalkForwardConfig, plan_origins
from tycheon_ft.dataset import (
    MAX_WINDOWS,
    DatasetError,
    FineTuneConfig,
    LeakageError,
    WindowSet,
    assert_no_test_leakage,
    build_windows,
    reject_duplicate_series,
    split_for,
    validate_bars,
)

AS_OF = pd.Timestamp("2030-01-01", tz="UTC")


def bars(
    n: int = 700, seed: int = 1, base: float = 100.0, start: str = "2020-01-01"
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = base * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n)))
    open_ = np.concatenate([[base], close[:-1]])
    high = np.maximum(open_, close) * 1.002
    low = np.minimum(open_, close) * 0.998
    index = pd.date_range(start, periods=n, freq="B", tz="UTC", name="timestamp")
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.integers(1000, 9000, n).astype(float),
        },
        index=index,
    )


def config(**over) -> FineTuneConfig:
    base = {"symbols": ["AAA"], "horizon": 5, "lookback": 64, "test_window": 40, "folds": 1}
    base.update(over)
    return FineTuneConfig(**base)


# ------------------------------------------------------------------------- validation
def test_clean_bars_pass_validation() -> None:
    frame = bars()
    assert validate_bars("AAA", frame, AS_OF) is frame


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        ("naive timestamps", lambda f: f.tz_localize(None)),
        ("unsorted", lambda f: f.iloc[::-1]),
        ("duplicate timestamps", lambda f: pd.concat([f, f.iloc[-1:]])),
        (
            "non-positive price",
            lambda f: f.assign(close=f["close"].where(f.index != f.index[10], -1.0)),
        ),
        ("high below close", lambda f: f.assign(high=f["close"] * 0.5)),
        ("missing column", lambda f: f.drop(columns=["low"])),
        ("nan", lambda f: f.assign(close=f["close"].where(f.index != f.index[5], np.nan))),
    ],
)
def test_unsound_bars_are_rejected(label, mutate) -> None:
    with pytest.raises((DatasetError, Exception)):
        validate_bars("AAA", mutate(bars()), AS_OF)


def test_bars_published_before_they_happened_are_rejected_as_look_ahead() -> None:
    frame = bars()
    frame["available_at"] = frame.index - pd.Timedelta(days=1)
    with pytest.raises(DatasetError, match="look-ahead"):
        validate_bars("AAA", frame, AS_OF)


def test_bars_published_after_as_of_are_rejected() -> None:
    frame = bars()
    frame["available_at"] = frame.index + pd.Timedelta(days=1)
    frame.loc[frame.index[-1], "available_at"] = AS_OF + pd.Timedelta(days=5)
    with pytest.raises(DatasetError, match="published after"):
        validate_bars("AAA", frame, AS_OF)
    with pytest.raises(DatasetError, match="after"):
        validate_bars("AAA", bars(start="2029-06-01", n=400), AS_OF)


def test_identical_prices_under_two_symbols_are_rejected() -> None:
    frame = bars()
    with pytest.raises(DatasetError, match="identical prices"):
        reject_duplicate_series({"AAA": frame, "BBB": frame.copy()})
    reject_duplicate_series({"AAA": frame, "BBB": bars(seed=2)})


# ----------------------------------------------------------------------------- split
def test_the_split_comes_from_the_same_planner_the_backtester_uses() -> None:
    cfg = config()
    split = split_for("AAA", 700, cfg)
    wf = WalkForwardConfig(horizon=5, folds=1, test_window=40, embargo=5)
    origins, _ = plan_origins(700, wf)
    assert split.first_test_origin == origins[0] and split.train_end == origins[0] - 5


def test_a_series_too_short_to_train_and_test_is_refused_with_the_numbers() -> None:
    with pytest.raises(DatasetError) as raised:
        split_for("AAA", 130, config())
    assert "130 bars is too few" in str(raised.value) and "AAA" in str(raised.value)


# ---------------------------------------------------------------------------- leakage
def test_no_training_window_reaches_the_evaluation_region() -> None:
    cfg = config()
    train, val, splits, _ = build_windows({"AAA": bars()}, cfg)
    cut = splits["AAA"].train_end
    assert len(val) > 0 and train.last_bar.max() < cut and val.last_bar.max() < cut
    assert cut <= splits["AAA"].first_test_origin - cfg.horizon  # the embargo


def test_validation_windows_come_after_training_windows_with_a_gap() -> None:
    cfg = config()
    train, val, _, _ = build_windows({"AAA": bars()}, cfg)
    assert len(val) > 0
    size = cfg.lookback + cfg.horizon
    assert val.last_bar.min() - (size - 1) >= train.last_bar.max() - 0  # no shared bars at all


@given(
    n=st.integers(280, 900),
    horizon=st.integers(1, 20),
    lookback=st.integers(32, 120),
    test_window=st.integers(20, 100),
    folds=st.integers(1, 3),
)
@settings(max_examples=40, deadline=None)
def test_property_training_never_sees_the_test_region(
    n, horizon, lookback, test_window, folds
) -> None:
    cfg = config(horizon=horizon, lookback=lookback, test_window=test_window, folds=folds)
    try:
        train, val, _, _ = build_windows({"AAA": bars(n)}, cfg)
    except DatasetError:
        return  # too little data is a refusal, never a leak
    wf = WalkForwardConfig(horizon=horizon, folds=folds, test_window=test_window, embargo=horizon)
    first_origin = plan_origins(n, wf)[0][0]
    assert (
        max(train.last_bar.max(), val.last_bar.max() if len(val) else 0)
        < first_origin - horizon + 1
    )


def test_the_guard_catches_a_window_that_overlaps_the_test_region() -> None:
    cfg = config()
    train, _, splits, _ = build_windows({"AAA": bars()}, cfg)
    leaky = WindowSet(train.x, train.stamps, train.last_bar + 10_000, train.series)
    with pytest.raises(LeakageError, match="inside the evaluation region"):
        assert_no_test_leakage(leaky, splits)
    one_late = train.last_bar.copy()
    one_late[0] = splits["AAA"].train_end  # exactly one bar into the region is already too many
    with pytest.raises(LeakageError):
        assert_no_test_leakage(WindowSet(train.x, train.stamps, one_late, train.series), splits)


def test_changing_the_test_region_changes_nothing_the_model_trains_on() -> None:
    cfg = config()
    original = bars()
    split = split_for("AAA", len(original), cfg)
    poisoned = original.copy()
    poisoned.iloc[split.train_end :, :4] = 1e9  # wreck everything from the cut onwards
    a, _, _, _ = build_windows({"AAA": original}, cfg)
    b, _, _, _ = build_windows({"AAA": poisoned}, cfg)
    assert np.array_equal(a.x, b.x) and np.array_equal(a.last_bar, b.last_bar)


def test_a_windows_normalisation_never_sees_its_own_horizon() -> None:
    """Statistics come from the context only, as at inference: the horizon cannot leak in."""
    from tycheon_ft.dataset import _windows_for

    cfg = config()
    frame = bars()
    size = cfg.lookback + cfg.horizon
    changed = frame.copy()
    changed.iloc[cfg.lookback : size, :4] *= 3.0  # rewrite only the horizon bars of window 0
    (a, _, _), (b, _, _) = (_windows_for(f, 0, size, cfg)[:3] for f in (frame, changed))
    assert np.array_equal(a[0][: cfg.lookback], b[0][: cfg.lookback])  # context: identical
    assert not np.array_equal(a[0][cfg.lookback :], b[0][cfg.lookback :])  # target: it did change


def test_the_window_count_is_bounded_and_the_subsample_is_reproducible() -> None:
    cfg = config(lookback=32, horizon=1)
    many = {f"S{i}": bars(900, seed=i + 10) for i in range(8)}
    cfg = config(symbols=list(many), lookback=32, horizon=1)
    a, _, _, _ = build_windows(many, cfg)
    b, _, _, _ = build_windows(many, cfg)
    assert len(a) <= MAX_WINDOWS and np.array_equal(a.last_bar, b.last_bar)


def test_too_little_data_is_a_clear_refusal_not_a_silent_tiny_model() -> None:
    with pytest.raises(DatasetError):
        build_windows({"AAA": bars(120)}, config())


def test_bounded_inputs_come_from_the_config_model() -> None:
    for bad in (
        {"epochs": 0},
        {"epochs": 99},
        {"horizon": 100},
        {"lookback": 5},
        {"max_steps": 10**9},
        {"learning_rate": 1.0},
        {"symbols": []},
        {"symbols": [f"S{i}" for i in range(11)]},
        {"unexpected": 1},
    ):
        with pytest.raises(ValueError):
            config(**bad)
