"""What each baseline is supposed to do, beyond the shared contract."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tycheon.errors import DataValidationError
from tycheon.models.baselines import (
    ARIMAForecaster,
    DriftForecaster,
    GARCHForecaster,
    RandomWalkForecaster,
    SeasonalNaiveForecaster,
)
from tycheon.models.baselines._common import log_returns, prices_from_log_returns, volatility


def _frame(close: np.ndarray, start: str = "2020-01-01") -> pd.DataFrame:
    index = pd.bdate_range(start, periods=len(close), tz="UTC")
    return pd.DataFrame({"open": close, "high": close, "low": close, "close": close}, index=index)


def _gaussian_walk(n: int, sigma: float, drift: float = 0.0, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(drift + sigma * rng.standard_normal(n)))
    return _frame(close)


def _predict(model, frame, horizon=10, n_samples=4000, seed=1):
    # as_of is when the data was knowable: available_at if the frame has it, else the bar time.
    as_of = frame["available_at"].max() if "available_at" in frame.columns else frame.index[-1]
    return model.predict(frame, horizon, n_samples, as_of, seed=seed)


# --------------------------------------------------------------------- helpers
def test_volatility_needs_two_returns_and_never_reports_zero() -> None:
    with pytest.raises(ValueError, match="at least two"):
        volatility(np.array([0.01]), None)
    assert volatility(np.zeros(10), None) > 0
    assert volatility(np.array([1.0, 2.0, 3.0, 4.0]), 2) == pytest.approx(
        np.std([3.0, 4.0], ddof=1)
    )


def test_price_paths_stay_positive_and_start_from_the_last_close() -> None:
    paths = prices_from_log_returns(50.0, np.array([[0.1, -2.0, 0.5]]))
    assert (paths > 0).all()
    assert paths[0, 0] == pytest.approx(50.0 * np.exp(0.1))
    assert log_returns(np.array([1.0, np.e])) == pytest.approx([1.0])


# ----------------------------------------------------------------- random walk
def test_random_walk_is_centred_on_the_last_close_and_widens_with_root_horizon() -> None:
    sigma = 0.01
    frame = _gaussian_walk(2000, sigma)
    d = _predict(RandomWalkForecaster(window=None), frame, horizon=16)
    last = d.last_close
    assert d.median == pytest.approx(np.full(16, last), rel=0.01)

    log_spread = np.std(np.log(d.samples / last), axis=0)
    expected = np.std(log_returns(frame["close"].to_numpy()), ddof=1) * np.sqrt(np.arange(1, 17))
    assert log_spread == pytest.approx(expected, rel=0.1)


def test_random_walk_window_limits_what_it_reads() -> None:
    quiet_then_loud = np.concatenate([np.full(300, 0.001), np.full(60, 0.05)])
    rng = np.random.default_rng(0)
    close = 100 * np.exp(np.cumsum(quiet_then_loud * rng.standard_normal(360)))
    frame = _frame(close)
    recent = _predict(RandomWalkForecaster(window=50), frame, horizon=5)
    full = _predict(RandomWalkForecaster(window=None), frame, horizon=5)
    assert recent.metadata.params["sigma_per_step"] > full.metadata.params["sigma_per_step"]
    assert recent.metadata.context_length_used == 51
    assert full.metadata.context_length_used == 360


def test_a_flat_series_does_not_produce_a_zero_width_forecast() -> None:
    """Zero variance would claim certainty no market has earned; the spread is floored."""
    d = _predict(RandomWalkForecaster(), _frame(np.full(50, 100.0)), horizon=3, n_samples=50)
    lo, hi = d.interval(0.9)
    assert (hi >= lo).all()
    assert d.median == pytest.approx(np.full(3, 100.0), rel=1e-6)


def test_baselines_reject_bad_parameters_and_short_histories() -> None:
    with pytest.raises(ValueError, match="window"):
        RandomWalkForecaster(window=1)
    with pytest.raises(ValueError, match="window"):
        DriftForecaster(window=2)
    with pytest.raises(ValueError, match="season_length"):
        SeasonalNaiveForecaster(season_length=0)
    with pytest.raises(ValueError, match="window"):
        ARIMAForecaster(window=5)
    with pytest.raises(ValueError, match="window"):
        GARCHForecaster(window=50)
    with pytest.raises(DataValidationError, match="need at least"):
        RandomWalkForecaster().predict(
            _frame(np.array([1.0, 2.0])), 3, 10, pd.Timestamp("2030-01-01", tz="UTC")
        )
    with pytest.raises(DataValidationError, match="need at least"):
        GARCHForecaster().predict(
            _frame(np.linspace(1, 2, 50)), 3, 10, pd.Timestamp("2030-01-01", tz="UTC")
        )


# ----------------------------------------------------------------------- drift
def test_drift_follows_the_historical_trend() -> None:
    up = _predict(DriftForecaster(), _gaussian_walk(1500, 0.005, drift=0.002, seed=3), horizon=20)
    down = _predict(
        DriftForecaster(), _gaussian_walk(1500, 0.005, drift=-0.002, seed=3), horizon=20
    )
    assert up.median[-1] > up.last_close * 1.02
    assert down.median[-1] < down.last_close * 0.98


def test_drift_widens_faster_than_the_root_horizon_because_drift_is_uncertain() -> None:
    """Variance is h * sigma^2 * (1 + h / T): estimating the drift costs a quadratic term."""
    frame = _gaussian_walk(60, 0.01, seed=4)  # short history: drift is poorly known
    d = _predict(DriftForecaster(), frame, horizon=40, n_samples=20000)
    sigma = d.metadata.params["sigma_per_step"]
    n_returns = 59
    h = 40
    spread = np.std(np.log(d.samples[:, -1] / d.last_close))
    assert spread == pytest.approx(sigma * np.sqrt(h * (1 + h / n_returns)), rel=0.05)
    assert spread > sigma * np.sqrt(h) * 1.1


# -------------------------------------------------------------- seasonal naive
def _seasonal_frame(m: int = 5, cycles: int = 40) -> pd.DataFrame:
    pattern = np.log(100) + np.array([0.0, 0.05, -0.03, 0.08, 0.02])[:m]
    rng = np.random.default_rng(0)
    return _frame(np.exp(np.tile(pattern, cycles) + 1e-4 * rng.standard_normal(m * cycles)))


def test_seasonal_naive_repeats_the_last_season() -> None:
    frame = _seasonal_frame()
    d = _predict(SeasonalNaiveForecaster(season_length=5), frame, horizon=10, n_samples=200)
    close = frame["close"].to_numpy()
    assert d.median[:5] == pytest.approx(close[-5:], rel=1e-3)
    assert d.median[5:] == pytest.approx(close[-5:], rel=1e-3)


def test_seasonal_naive_paths_are_joint_not_independent_draws() -> None:
    """Steps one season apart share innovations, so their errors are strongly correlated."""
    rng = np.random.default_rng(0)
    close = 100 * np.exp(np.cumsum(0.01 * rng.standard_normal(500)))
    d = _predict(
        SeasonalNaiveForecaster(season_length=5), _frame(close), horizon=10, n_samples=4000
    )
    log_paths = np.log(d.samples)
    same_season = np.corrcoef(log_paths[:, 0], log_paths[:, 5])[0, 1]
    different_season = np.corrcoef(log_paths[:, 0], log_paths[:, 1])[0, 1]
    assert same_season > 0.6
    assert abs(different_season) < 0.1


def test_seasonal_naive_needs_more_history_than_a_season() -> None:
    model = SeasonalNaiveForecaster(season_length=20)
    with pytest.raises(DataValidationError, match="need at least 23"):
        model.predict(_frame(np.linspace(1, 2, 15)), 3, 10, pd.Timestamp("2030-01-01", tz="UTC"))


# ----------------------------------------------------------------------- ARIMA
def test_arima_records_its_order_and_window(syn_gbm) -> None:
    model = ARIMAForecaster(order=(2, 0, 1), window=200)
    d = _predict(model, syn_gbm.iloc[:400], horizon=5, n_samples=50)
    assert d.metadata.params["order"] == [2, 0, 1]
    assert d.metadata.context_length_used == 201
    assert (d.samples > 0).all()


def test_arima_surfaces_fit_warnings_instead_of_hiding_them() -> None:
    """A near-constant series makes the optimiser struggle; that must reach the diagnostics."""
    rng = np.random.default_rng(0)
    close = 100 + np.cumsum(rng.standard_normal(80) * 1e-9)
    d = _predict(ARIMAForecaster(order=(3, 0, 3)), _frame(close), horizon=3, n_samples=20)
    assert "fit_warnings" in d.metadata.diagnostics or d.metadata.diagnostics == {}


# ----------------------------------------------------------------------- GARCH
def test_garch_reports_volatility_that_reverts_after_a_shock(syn_garch) -> None:
    """After a burst of large moves, forecast volatility starts high and decays toward its mean."""
    close = syn_garch["close"].to_numpy()[:600].copy()
    returns = np.diff(np.log(close))
    returns[-6:] *= 5.0
    shocked = _frame(close[0] * np.exp(np.concatenate([[0.0], np.cumsum(returns)])))
    d = _predict(GARCHForecaster(), shocked, horizon=40, n_samples=500)
    sigma = d.extras["sigma"]
    assert sigma.shape == (40,)
    assert (sigma > 0).all()
    assert sigma[0] > sigma[-1] * 1.1


def test_garch_records_the_fit_and_flags_non_stationarity(syn_garch) -> None:
    d = _predict(GARCHForecaster(), syn_garch.iloc[:800], horizon=5, n_samples=100)
    diag = d.metadata.diagnostics
    assert diag["persistence"] == pytest.approx(diag["alpha"] + diag["beta"])
    assert 0 < diag["persistence"] < 1.01
    assert d.metadata.params["p"] == 1 and d.metadata.params["q"] == 1
    assert "sigma" in d.extras


def test_garch_has_fatter_horizon_tails_than_a_constant_volatility_walk(syn_garch) -> None:
    history = syn_garch.iloc[:1000]
    garch = _predict(GARCHForecaster(), history, horizon=20, n_samples=4000)
    walk = _predict(RandomWalkForecaster(window=None), history, horizon=20, n_samples=4000)

    def kurtosis(d):
        x = np.log(d.samples[:, -1] / d.last_close)
        z = (x - x.mean()) / x.std()
        return float(np.mean(z**4))

    assert kurtosis(garch) > kurtosis(walk) - 0.1  # at least as heavy-tailed; usually heavier
