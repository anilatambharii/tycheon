"""The walk-forward engine: layout, refit schedule, embargo, guards, and the leakage canary."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest

from tycheon.backtest import (
    CostModel,
    GuardedForecaster,
    WalkForwardConfig,
    assert_fit_data_precedes,
    assert_known_by,
    calibrated_ensemble_factory,
    calibrated_factory,
    ensemble_factory,
    evaluate,
    static,
    strategy_result,
    survivorship_warning,
    walk_forward,
)
from tycheon.backtest.costs import positions
from tycheon.backtest.walk_forward import plan_origins
from tycheon.errors import DataValidationError, LookaheadError
from tycheon.models.baselines import DriftForecaster, RandomWalkForecaster

if TYPE_CHECKING:
    import pandas as pd

    from tycheon.calibration.scores import ScoreSet

H = 5


# ----------------------------------------------------------------------- layout
def test_origins_are_anchored_at_the_end_and_spaced_by_the_stride() -> None:
    config = WalkForwardConfig(horizon=5, folds=3, test_window=20, embargo=5)
    origins, folds = plan_origins(1000, config)
    assert len(origins) == 12 and origins[-1] == 1000 - 1 - 5
    assert np.diff(origins).tolist() == [5] * 11
    assert folds == [0] * 4 + [1] * 4 + [2] * 4


def test_a_smaller_stride_gives_more_origins_per_fold_and_a_note() -> None:
    config = WalkForwardConfig(horizon=5, folds=2, test_window=20, stride=1)
    assert config.origins_per_fold == 20


@pytest.mark.parametrize(
    "kwargs",
    [
        {"horizon": 0},
        {"horizon": 5, "folds": 0},
        {"horizon": 5, "embargo": -1},
        {"horizon": 5, "stride": 0},
        {"horizon": 5, "refit": "sometimes"},
        {"horizon": 5, "refit": "every:0"},
    ],
)
def test_invalid_configs_are_rejected(kwargs) -> None:
    with pytest.raises(ValueError):
        WalkForwardConfig(**kwargs)


class Recorder:
    """A factory that records what it was asked to train on."""

    def __init__(self) -> None:
        self.calls: list[tuple[pd.DataFrame, pd.Timestamp]] = []

    def __call__(self, history: pd.DataFrame, as_of: pd.Timestamp):
        self.calls.append((history, as_of))
        return RandomWalkForecaster()


def _bars(known_bars: pd.DataFrame, n: int = 1500) -> pd.DataFrame:
    return known_bars.iloc[:n]


@pytest.mark.parametrize(
    ("refit", "builds"), [("fold", 3), ("never", 1), ("every:3", 4), ("every:100", 1)]
)
def test_the_refit_schedule_decides_how_often_the_factory_is_called(
    known_bars, refit, builds
) -> None:
    rec = Recorder()
    config = WalkForwardConfig(horizon=H, folds=3, test_window=20, embargo=5, refit=refit)
    result = walk_forward(rec, _bars(known_bars), config)
    assert len(rec.calls) == builds == len(result.refits)


def test_training_history_ends_one_embargo_before_the_first_origin_it_serves(known_bars) -> None:
    bars = _bars(known_bars)
    rec = Recorder()
    config = WalkForwardConfig(horizon=H, folds=3, test_window=20, embargo=7)
    origins, folds = plan_origins(len(bars), config)
    walk_forward(rec, bars, config)
    for fold, (history, fit_as_of) in enumerate(rec.calls):
        first_origin = origins[folds.index(fold)]
        assert len(history) == first_origin - 7 + 1
        assert history["available_at"].max() == fit_as_of
        assert fit_as_of < bars["available_at"].iloc[first_origin]


def test_train_window_caps_the_training_history(known_bars) -> None:
    rec = Recorder()
    config = WalkForwardConfig(
        horizon=H, folds=2, test_window=20, embargo=5, train_window=300, refit="fold"
    )
    walk_forward(rec, _bars(known_bars), config)
    assert all(len(history) == 300 for history, _ in rec.calls)


def test_a_series_too_short_for_the_layout_is_refused(known_bars) -> None:
    config = WalkForwardConfig(horizon=H, folds=3, test_window=63, embargo=5, min_history=60)
    with pytest.raises(DataValidationError, match="too few"):
        walk_forward(static(RandomWalkForecaster()), known_bars.iloc[:100], config)


def test_the_scoreset_records_what_actually_happened(known_bars) -> None:
    bars = _bars(known_bars)
    config = WalkForwardConfig(horizon=H, folds=2, test_window=20, embargo=5, n_samples=40)
    result = walk_forward(static(RandomWalkForecaster()), bars, config)
    s = result.scores
    assert s.n == 8 and s.origin_times.is_monotonic_increasing
    assert result.guard_checks == s.n
    origins, _ = plan_origins(len(bars), config)
    close = bars["close"].to_numpy()
    for row, i in enumerate(origins):
        assert s.realized[row] == pytest.approx(np.log(close[i + 1 : i + 1 + H] / close[i]))
        assert s.origin_times[row] == bars["available_at"].iloc[i]
        assert (s.outcome_times[row] > s.origin_times[row].value).all()
    assert result.n_folds == 2 and result.to_dict()["n_origins"] == 8


# ----------------------------------------------------------------------- guards
def test_the_guard_refuses_history_published_after_the_origin(known_bars) -> None:
    guarded = GuardedForecaster(RandomWalkForecaster())
    history = known_bars.iloc[:200]
    origin = history["available_at"].iloc[100]
    guarded.expected_origin = origin
    with pytest.raises(LookaheadError, match="after the origin"):
        guarded.predict(history, H, 10, origin)


def test_the_guard_refuses_an_as_of_that_is_not_the_origin_being_scored(known_bars) -> None:
    guarded = GuardedForecaster(RandomWalkForecaster())
    history = known_bars.iloc[:200]
    guarded.expected_origin = history["available_at"].iloc[100]
    with pytest.raises(LookaheadError, match="origin being scored"):
        guarded.predict(history.iloc[:101], H, 10, history["available_at"].iloc[150])


def test_the_guard_passes_clean_calls_and_counts_them(known_bars) -> None:
    guarded = GuardedForecaster(RandomWalkForecaster())
    history = known_bars.iloc[:200]
    origin = history["available_at"].iloc[-1]
    guarded.expected_origin = origin
    guarded.predict(history, H, 10, origin)
    guarded.predict(history, H, 10, origin)
    assert guarded.checks == 2 and guarded.model_id == "random-walk"


def test_a_bar_known_exactly_at_the_origin_is_allowed(known_bars) -> None:
    history = known_bars.iloc[:50]
    assert_known_by(history, history["available_at"].iloc[-1])  # available_at == origin: fine
    with pytest.raises(LookaheadError):
        assert_known_by(history, history["available_at"].iloc[-2])


def test_fit_data_must_end_an_embargo_before_the_first_origin(known_bars) -> None:
    bars = known_bars.iloc[:300]
    first_origin = bars["available_at"].iloc[200]
    assert_fit_data_precedes(
        bars.iloc[:196], first_origin, 5, bars
    )  # exactly 5 bars apart: allowed
    with pytest.raises(LookaheadError, match="embargo"):
        assert_fit_data_precedes(bars.iloc[:199], first_origin, 5, bars)


# ------------------------------------------------------------ the leakage canary
def poison(bars: pd.DataFrame, after: int) -> pd.DataFrame:
    """Same timestamps and publication times, wildly different prices after position ``after``."""
    out = bars.copy()
    rng = np.random.default_rng(99)
    factor = np.exp(rng.normal(0, 1.0, len(out) - after - 1))
    cols = ["open", "high", "low", "close"]
    out.iloc[after + 1 :, [out.columns.get_loc(c) for c in cols]] = (
        out[cols].iloc[after + 1 :].to_numpy() * factor[:, None]
    )
    return out


def forecasts_up_to(result, origins: list[int], p: int) -> tuple[np.ndarray, np.ndarray | None]:
    keep = [row for row, i in enumerate(origins) if i <= p]
    s: ScoreSet = result.scores
    paths = None if s.samples is None else s.samples[keep]
    return s.base_logq[keep], paths


def assert_no_peeking(make, bars: pd.DataFrame, config: WalkForwardConfig) -> int:
    """Poison everything after the middle origin; forecasts at or before it must not move."""
    origins, _ = plan_origins(len(bars), config)
    p = origins[len(origins) // 2]
    clean = walk_forward(make(), bars, config)
    dirty = walk_forward(make(), poison(bars, p), config)
    q_clean, s_clean = forecasts_up_to(clean, origins, p)
    q_dirty, s_dirty = forecasts_up_to(dirty, origins, p)
    assert q_clean.shape[0] >= 3
    # the poison really changes what happens afterwards (otherwise this proves nothing)
    assert not np.allclose(clean.scores.realized[-1], dirty.scores.realized[-1])
    np.testing.assert_array_equal(q_clean, q_dirty)
    if s_clean is not None:
        np.testing.assert_array_equal(s_clean, s_dirty)
    return q_clean.shape[0]


@pytest.mark.parametrize(
    "make",
    [
        lambda: static(RandomWalkForecaster()),
        lambda: static(DriftForecaster(window=250)),
    ],
    ids=["random-walk", "drift"],
)
def test_canary_baselines_do_not_see_the_future(known_bars, make) -> None:
    config = WalkForwardConfig(horizon=H, folds=3, test_window=30, embargo=5, n_samples=30)
    assert_no_peeking(make, _bars(known_bars), config)


def test_canary_the_ensemble_and_its_fitted_router_do_not_see_the_future(known_bars) -> None:
    members = {"random-walk": RandomWalkForecaster, "drift": lambda: DriftForecaster(window=250)}
    config = WalkForwardConfig(horizon=H, folds=3, test_window=30, embargo=5, n_samples=30)

    def make():
        return ensemble_factory(members, horizon=H, n_origins=40, n_samples=30, max_history=300)

    assert_no_peeking(make, _bars(known_bars, 1200), config)


def test_canary_a_calibrated_forecaster_does_not_see_the_future(known_bars) -> None:
    config = WalkForwardConfig(horizon=H, folds=3, test_window=30, embargo=5, n_samples=30)

    def make():
        return calibrated_factory(
            static(RandomWalkForecaster()), horizon=H, n_origins=60, n_samples=30, max_history=300
        )

    assert_no_peeking(make, _bars(known_bars, 1200), config)


def test_canary_the_calibrated_ensemble_does_not_see_the_future(known_bars) -> None:
    members = {"random-walk": RandomWalkForecaster, "drift": lambda: DriftForecaster(window=250)}
    config = WalkForwardConfig(horizon=H, folds=3, test_window=30, embargo=5, n_samples=30)

    def make():
        return calibrated_ensemble_factory(
            members, horizon=H, n_origins=60, n_samples=30, max_history=300
        )

    assert_no_peeking(make, _bars(known_bars, 1200), config)


def test_the_canary_catches_a_forecaster_that_cheats(known_bars) -> None:
    """Test the test: a forecaster that reads the future must fail the canary."""
    bars = _bars(known_bars, 1200)

    class Cheat(RandomWalkForecaster):
        model_id = "cheat"

        def __init__(self, source: pd.DataFrame) -> None:
            super().__init__()
            self.source = source

        def predict(self, history, horizon, n_samples, as_of, *, seed=None):
            dist = super().predict(history, horizon, n_samples, as_of, seed=seed)
            pos = int(self.source["available_at"].searchsorted(as_of, side="right")) - 1
            future = self.source["close"].iloc[pos + horizon]
            bend = np.log(future / dist.last_close)
            # centre the quantiles and paths on what is about to happen
            from dataclasses import replace

            return replace(
                dist,
                quantiles=dist.quantiles * np.exp(bend),
                samples=None if dist.samples is None else dist.samples * np.exp(bend),
            )

    config = WalkForwardConfig(horizon=H, folds=3, test_window=30, embargo=5, n_samples=30)
    origins, _ = plan_origins(len(bars), config)
    p = origins[len(origins) // 2]
    dirty_bars = poison(bars, p)
    clean = walk_forward(static(Cheat(bars)), bars, config)
    dirty = walk_forward(static(Cheat(dirty_bars)), dirty_bars, config)
    q_clean, _ = forecasts_up_to(clean, origins, p)
    q_dirty, _ = forecasts_up_to(dirty, origins, p)
    assert not np.array_equal(q_clean, q_dirty)


# --------------------------------------------------------------- end to end sanity
def test_a_correct_gaussian_forecaster_is_calibrated_and_indistinguishable_from_the_walk(
    known_bars, gaussian_cls
) -> None:
    from tests.conftest import TRUE_SIGMA

    config = WalkForwardConfig(horizon=H, folds=3, test_window=400, embargo=5, n_samples=80)
    gauss = walk_forward(static(gaussian_cls("gauss", TRUE_SIGMA)), known_bars, config).scores
    rw = walk_forward(static(RandomWalkForecaster()), known_bars, config).scores
    from dataclasses import replace

    ev = evaluate(gauss, replace(rw, model_id="random-walk"))
    assert ev.coverage[0.9][0] == pytest.approx(0.9, abs=0.04)
    assert ev.coverage[0.5][0] == pytest.approx(0.5, abs=0.06)
    assert ev.verdict in {"indistinguishable from the random walk"}


def test_calibration_inside_a_walk_forward_repairs_an_overconfident_model(
    known_bars, gaussian_cls
) -> None:
    """A model with half the true volatility is repaired, with refits and an embargo."""
    from tests.conftest import TRUE_SIGMA

    config = WalkForwardConfig(horizon=H, folds=3, test_window=300, embargo=5, n_samples=60)
    bad = static(gaussian_cls("narrow", TRUE_SIGMA / 2))
    raw = walk_forward(bad, known_bars, config)
    fixed = walk_forward(
        calibrated_factory(
            bad, horizon=H, n_origins=150, n_samples=60, max_history=60, model_id="narrow"
        ),
        known_bars,
        config,
    )
    rw = walk_forward(static(RandomWalkForecaster()), known_bars, config).scores
    from dataclasses import replace

    rw = replace(rw, model_id="random-walk")
    raw_cov = evaluate(raw.scores, rw).coverage[0.9][0]
    fixed_cov = evaluate(fixed.scores, rw).coverage[0.9][0]
    assert raw_cov < 0.65
    assert fixed_cov == pytest.approx(0.9, abs=0.05)


# ------------------------------------------------------------------------ costs
def test_positions_follow_the_sign_of_the_forecast_beyond_the_edge() -> None:
    p = positions(np.array([0.02, -0.02, 0.001, -0.001, 0.0]), min_edge=0.005)
    assert p.tolist() == [1.0, -1.0, 0.0, 0.0, 0.0]


def _score_set(median: np.ndarray, realized_end: np.ndarray) -> ScoreSet:
    from tests.test_backtest_metrics import make_scores

    return make_scores(median, realized_end, stride=2)


def test_costs_are_charged_on_turnover_and_the_final_exit_by_hand() -> None:
    median = np.array([0.01, 0.01, -0.01, 0.0])  # long, long, short, flat
    real = np.array([0.02, -0.01, -0.03, 0.05])
    s = _score_set(median, real)
    cost = CostModel(spread_bps=2, slippage_bps=1, fee_bps=1)  # one way = 1 + 1 + 1 = 3 bps
    assert cost.one_way == pytest.approx(3e-4)
    r = strategy_result(s, cost)
    gross = np.array([0.02, -0.01, 0.03, 0.0])
    turnover = np.array([1, 0, 2, 1])  # in, hold, flip, exit
    net = gross - turnover * 3e-4
    assert r.gross_mean == pytest.approx(gross.mean())
    assert r.net_mean == pytest.approx(net.mean())
    assert r.turnover == pytest.approx(4.0) and r.n_trades == 3
    assert r.hit_rate == pytest.approx(2 / 3)
    assert r.net_mean < r.gross_mean


def test_a_final_open_position_pays_to_close() -> None:
    s = _score_set(np.array([0.01, 0.01]), np.array([0.0, 0.0]))
    r = strategy_result(s, CostModel(spread_bps=0, slippage_bps=10))
    assert r.cost_per_period == pytest.approx((10e-4 + 10e-4) / 2)


def test_a_flat_strategy_costs_nothing() -> None:
    r = strategy_result(_score_set(np.zeros(5), np.ones(5) * 0.01), CostModel())
    assert r.n_trades == 0 and r.net_mean == 0.0


def test_the_strategy_refuses_overlapping_horizons() -> None:
    from tests.test_backtest_metrics import make_scores

    with pytest.raises(ValueError, match="non-overlapping"):
        strategy_result(make_scores(np.zeros(5), np.zeros(5), stride=1), CostModel())


def test_negative_costs_are_rejected() -> None:
    with pytest.raises(ValueError):
        CostModel(spread_bps=-1)


# ----------------------------------------------------------------- survivorship
def test_a_static_real_universe_gets_a_survivorship_warning() -> None:
    w = survivorship_warning(["A", "B"], universe_kind="static", synthetic=False)
    assert w is not None and "survivorship" in w and "2 symbols" in w


@pytest.mark.parametrize(("kind", "synthetic"), [("point_in_time", False), ("static", True)])
def test_no_warning_for_point_in_time_or_synthetic_universes(kind, synthetic) -> None:
    assert survivorship_warning(["A"], universe_kind=kind, synthetic=synthetic) is None
