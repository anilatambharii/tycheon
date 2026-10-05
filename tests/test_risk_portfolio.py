"""Portfolio aggregation, the dependence assumption, and stress scenarios."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from tycheon.errors import DataValidationError, LookaheadError, ModelError
from tycheon.risk import (
    Portfolio,
    RiskInput,
    aggregate_joint_paths,
    aggregate_portfolio,
    couple_paths,
    estimate_correlation,
    historical_replay,
    model_implied_tail,
    run_stress_suite,
    shock,
    stress_correlation,
    value_at_risk_and_es,
    worst_windows,
)

AS_OF = pd.Timestamp("2024-01-10", tz="UTC")


def correlated_bars(bars_from_returns, rho: float, n: int = 1500, seed: int = 0):
    rng = np.random.default_rng(seed)
    z = rng.multivariate_normal([0, 0], [[1, rho], [rho, 1]], size=n)
    return {"A": bars_from_returns(0.01 * z[:, 0]), "B": bars_from_returns(0.02 * z[:, 1])}


def gaussian_dist(gaussian_cls, bars, name, sigma, n_samples=20000, seed=0):
    history = bars.iloc[:300]
    as_of = history["available_at"].iloc[-1]
    return gaussian_cls(name, sigma).predict(history, 5, n_samples, as_of, seed=seed)


# ------------------------------------------------------------------- portfolios
def test_a_portfolio_exposes_weights_and_total() -> None:
    p = Portfolio.from_values({"A": 60.0, "B": 40.0}, name="p")
    assert p.symbols == ["A", "B"] and p.total_value == 100.0
    assert p.weights == {"A": 0.6, "B": 0.4}


@pytest.mark.parametrize("bad", [{}, {"A": 0.0}, {"A": -5.0}])
def test_an_invalid_portfolio_is_rejected(bad) -> None:
    with pytest.raises(DataValidationError):
        Portfolio.from_values(bad)
    with pytest.raises(DataValidationError, match="duplicate"):
        from tycheon.risk import Position

        Portfolio((Position("A", 1.0), Position("A", 2.0)))


# ------------------------------------------------------------------ correlation
def test_the_estimated_correlation_recovers_the_truth(bars_from_returns) -> None:
    bars = correlated_bars(bars_from_returns, 0.6)
    as_of = bars["A"]["available_at"].iloc[-1]
    corr = estimate_correlation(bars, as_of=as_of, lookback=1400, shrinkage=0.0)
    assert corr.loc["A", "B"] == pytest.approx(0.6, abs=0.05)
    assert list(corr.index) == ["A", "B"] and np.diag(corr.to_numpy()).tolist() == pytest.approx(
        [1, 1]
    )


def test_shrinkage_pulls_correlation_toward_zero(bars_from_returns) -> None:
    bars = correlated_bars(bars_from_returns, 0.6)
    as_of = bars["A"]["available_at"].iloc[-1]
    raw = estimate_correlation(bars, as_of=as_of, shrinkage=0.0).loc["A", "B"]
    shrunk = estimate_correlation(bars, as_of=as_of, shrinkage=0.3).loc["A", "B"]
    assert shrunk == pytest.approx(0.7 * raw, abs=0.01)


@pytest.mark.leakage
def test_correlation_refuses_data_published_after_as_of(bars_from_returns) -> None:
    bars = correlated_bars(bars_from_returns, 0.6)
    with pytest.raises(LookaheadError):
        estimate_correlation(bars, as_of=bars["A"]["available_at"].iloc[500])


def test_correlation_needs_enough_history_and_a_valid_shrinkage(bars_from_returns) -> None:
    bars = correlated_bars(bars_from_returns, 0.6, n=20)
    with pytest.raises(DataValidationError, match="common bars"):
        estimate_correlation(bars, as_of=bars["A"]["available_at"].iloc[-1])
    with pytest.raises(ValueError, match="shrinkage"):
        estimate_correlation(bars, as_of=bars["A"]["available_at"].iloc[-1], shrinkage=1.5)


def test_stress_correlation_raises_every_pair_to_at_least_the_level() -> None:
    base = pd.DataFrame(
        [[1.0, 0.1, -0.2], [0.1, 1.0, 0.3], [-0.2, 0.3, 1.0]],
        index=list("ABC"),
        columns=list("ABC"),
    )
    stressed = stress_correlation(base, 0.8)
    off = stressed.to_numpy()[~np.eye(3, dtype=bool)]
    assert off.min() > 0.78
    assert np.linalg.eigvalsh(stressed.to_numpy()).min() > 0
    with pytest.raises(ValueError, match="level"):
        stress_correlation(base, 1.5)


# --------------------------------------------------------------------- coupling
def test_coupling_imposes_the_target_dependence_and_changes_nothing_else() -> None:
    rng = np.random.default_rng(0)
    n = 30000
    paths = {
        "A": 100 * np.exp(np.cumsum(rng.normal(0, 0.01, size=(n, 4)), axis=1)),
        "B": 50 * np.exp(np.cumsum(rng.normal(0, 0.02, size=(n, 4)), axis=1)),
    }
    start = {"A": 100.0, "B": 50.0}
    rho = 0.6
    corr = pd.DataFrame([[1, rho], [rho, 1]], index=["A", "B"], columns=["A", "B"], dtype=float)
    out = couple_paths(paths, start, corr, seed=3)

    spearman = stats.spearmanr(np.log(out["A"][:, -1] / 100), np.log(out["B"][:, -1] / 50))[0]
    assert spearman == pytest.approx(6 / math.pi * math.asin(rho / 2), abs=0.02)  # Gaussian copula
    for s in ("A", "B"):
        # each asset's marginal distribution is untouched ...
        np.testing.assert_allclose(np.sort(out[s][:, -1]), np.sort(paths[s][:, -1]))
        # ... and every path stays intact: coupling only re-pairs whole paths
        assert {tuple(row) for row in out[s][:50]} <= {tuple(row) for row in paths[s]}


def test_coupling_is_reproducible_and_needs_equal_path_counts() -> None:
    rng = np.random.default_rng(1)
    a = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, size=(500, 3)), axis=1))
    corr = pd.DataFrame(np.eye(2), index=["A", "B"], columns=["A", "B"])
    x = couple_paths({"A": a, "B": a.copy()}, {"A": 100, "B": 100}, corr, seed=5)
    y = couple_paths({"A": a, "B": a.copy()}, {"A": 100, "B": 100}, corr, seed=5)
    np.testing.assert_array_equal(x["B"], y["B"])
    with pytest.raises(DataValidationError, match="same number"):
        couple_paths({"A": a, "B": a[:100]}, {"A": 100, "B": 100}, corr)


# ------------------------------------------------------------------ aggregation
def test_independent_assets_diversify_to_the_closed_form_portfolio_volatility(
    known_bars, gaussian_cls
) -> None:
    sigma_a, sigma_b = 0.01, 0.02
    fa = gaussian_dist(gaussian_cls, known_bars, "a", sigma_a, seed=1)
    fb = gaussian_dist(gaussian_cls, known_bars, "b", sigma_b, seed=2)
    p = Portfolio.from_values({"A": 600.0, "B": 400.0})
    corr = pd.DataFrame(np.eye(2), index=["A", "B"], columns=["A", "B"])
    pf = aggregate_portfolio(p, {"A": fa, "B": fb}, correlation=corr, seed=0)
    ret = pf.paths[:, -1] / p.total_value - 1.0
    expected = math.sqrt((0.6 * sigma_a) ** 2 + (0.4 * sigma_b) ** 2) * math.sqrt(
        5
    )  # 5-step horizon
    assert ret.std() == pytest.approx(expected, rel=0.03)


def test_perfect_correlation_gives_the_weighted_sum_and_diversification_helps(
    known_bars, gaussian_cls
) -> None:
    fa = gaussian_dist(gaussian_cls, known_bars, "a", 0.01, seed=1)
    fb = gaussian_dist(gaussian_cls, known_bars, "b", 0.02, seed=2)
    p = Portfolio.from_values({"A": 500.0, "B": 500.0})

    def vol(rho: float) -> float:
        corr = pd.DataFrame([[1, rho], [rho, 1]], index=["A", "B"], columns=["A", "B"], dtype=float)
        pf = aggregate_portfolio(p, {"A": fa, "B": fb}, correlation=corr, seed=0)
        return float((pf.paths[:, -1] / p.total_value - 1.0).std())

    assert vol(0.999) == pytest.approx(
        math.sqrt(5) * (0.5 * 0.01 + 0.5 * 0.02), rel=0.03
    )  # comonotone
    assert vol(0.0) < vol(0.999)
    assert vol(-0.8) < vol(0.0)


def test_a_multi_asset_portfolio_is_uncalibrated_because_dependence_is_assumed(
    known_bars, gaussian_cls
) -> None:
    fa = gaussian_dist(gaussian_cls, known_bars, "a", 0.01, n_samples=500, seed=1)
    fb = gaussian_dist(gaussian_cls, known_bars, "b", 0.02, n_samples=500, seed=2)
    p = Portfolio.from_values({"A": 500.0, "B": 500.0})
    corr = pd.DataFrame(np.eye(2), index=["A", "B"], columns=["A", "B"])
    pf = aggregate_portfolio(p, {"A": fa, "B": fb}, correlation=corr)
    inp = pf.to_risk_input()
    assert inp.calibration_status == "uncalibrated" and inp.calibration is None
    assert "gaussian-copula" in pf.dependence
    assert any("no tail dependence" in note or "Dependence is assumed" in note for note in pf.notes)
    assert not any(m.calibrated for m in value_at_risk_and_es(inp, (0.95,), n_boot=5))
    assert set(inp.model_mix) == {"A/a", "B/b"} and sum(inp.model_mix.values()) == pytest.approx(
        1.0
    )


def test_a_single_asset_portfolio_keeps_its_calibration(known_bars, gaussian_cls) -> None:
    fa = gaussian_dist(gaussian_cls, known_bars, "a", 0.01, n_samples=300)
    pf = aggregate_portfolio(Portfolio.from_values({"A": 1000.0}), {"A": fa})
    inp = pf.to_risk_input(single_asset=fa)
    assert pf.dependence.startswith("none") and pf.correlation is None
    assert inp.calibration_status == fa.calibration_status
    np.testing.assert_allclose(pf.paths, 1000.0 * fa.samples / fa.last_close)


def test_aggregation_input_errors(known_bars, gaussian_cls, fake_timesfm) -> None:
    from tycheon.models.timesfm import TimesFMForecaster

    fa = gaussian_dist(gaussian_cls, known_bars, "a", 0.01, n_samples=100)
    fb = gaussian_dist(gaussian_cls, known_bars, "b", 0.02, n_samples=100)
    p = Portfolio.from_values({"A": 500.0, "B": 500.0})
    corr = pd.DataFrame(np.eye(2), index=["A", "B"], columns=["A", "B"])
    with pytest.raises(DataValidationError, match="no forecast"):
        aggregate_portfolio(p, {"A": fa}, correlation=corr)
    with pytest.raises(ModelError, match="correlation matrix"):
        aggregate_portfolio(p, {"A": fa, "B": fb})
    history = known_bars.iloc[:300]
    quantile_only = TimesFMForecaster(engine=fake_timesfm).predict(
        history, 5, 1, history["available_at"].iloc[-1]
    )
    with pytest.raises(ModelError, match="joint sample paths"):
        aggregate_portfolio(p, {"A": fa, "B": quantile_only}, correlation=corr)
    other_time = gaussian_cls("c", 0.02).predict(
        known_bars.iloc[:310], 5, 100, known_bars["available_at"].iloc[309]
    )
    with pytest.raises(DataValidationError):
        aggregate_portfolio(p, {"A": fa, "B": other_time}, correlation=corr)


def test_joint_paths_are_valued_without_any_coupling() -> None:
    p = Portfolio.from_values({"A": 600.0, "B": 400.0})
    paths = np.stack([np.full((10, 3), 110.0), np.full((10, 3), 45.0)], axis=1)  # (n, assets, h)
    pf = aggregate_joint_paths(
        p, paths, symbols=["A", "B"], start_prices={"A": 100.0, "B": 50.0},
        index=pd.date_range(AS_OF, periods=3, freq="1D"), as_of=AS_OF,
    )  # fmt: skip
    np.testing.assert_allclose(pf.paths[0], [600 * 1.1 + 400 * 0.9] * 3)
    assert "no coupling" in pf.dependence
    with pytest.raises(DataValidationError, match="paths must be"):
        aggregate_joint_paths(
            p,
            np.ones((10, 3)),
            symbols=["A"],
            start_prices={"A": 1.0},
            index=pd.date_range(AS_OF, periods=3, freq="1D"),
            as_of=AS_OF,
        )


# -------------------------------------------------------------------- scenarios
@pytest.fixture
def crash_bars(bars_from_returns):
    """A calm series with a single engineered crash, so replay results are known by hand."""
    r = np.zeros(400)
    r[200:205] = [-0.05, -0.04, -0.03, 0.0, 0.02]  # a five-day crash and partial bounce
    return {"A": bars_from_returns(r), "B": bars_from_returns(r * 0.5)}, r


def test_historical_replay_applies_the_window_to_todays_positions(crash_bars) -> None:
    bars, r = crash_bars
    p = Portfolio.from_values({"A": 600.0, "B": 400.0})
    as_of = bars["A"]["available_at"].iloc[-1]
    res = historical_replay(
        p, bars, start=bars["A"].index[200], end=bars["A"].index[204], as_of=as_of, name="crash"
    )
    a, b = np.exp(r[200:205].sum()) - 1, np.exp(0.5 * r[200:205].sum()) - 1
    assert res.pnl_fraction == pytest.approx(0.6 * a + 0.4 * b, rel=1e-6)
    assert (
        res.max_drawdown
        == pytest.approx(
            1
            - (
                0.6 * np.exp(np.cumsum(r[200:203])[-1])
                + 0.4 * np.exp(0.5 * np.cumsum(r[200:203])[-1])
            ),
            rel=1e-4,
        )
        or res.max_drawdown > 0.05
    )
    assert res.kind == "historical-replay" and res.name == "crash" and len(res.path) == 5
    assert not res.calibrated and "not a forecast" in res.caveat


@pytest.mark.leakage
def test_a_replay_window_after_as_of_is_refused(crash_bars) -> None:
    bars, _ = crash_bars
    p = Portfolio.from_values({"A": 1.0, "B": 1.0})
    with pytest.raises(LookaheadError, match="after as_of"):
        historical_replay(p, bars, start=bars["A"].index[200], end=bars["A"].index[204],
                          as_of=bars["A"]["available_at"].iloc[150])  # fmt: skip


def test_a_replay_needs_a_bar_before_the_window(crash_bars) -> None:
    bars, _ = crash_bars
    p = Portfolio.from_values({"A": 1.0, "B": 1.0})
    with pytest.raises(DataValidationError, match="one bar before"):
        historical_replay(p, bars, start=bars["A"].index[0], end=bars["A"].index[3],
                          as_of=bars["A"]["available_at"].iloc[-1])  # fmt: skip


def test_worst_windows_finds_the_crash_and_does_not_overlap(crash_bars) -> None:
    bars, _ = crash_bars
    p = Portfolio.from_values({"A": 600.0, "B": 400.0})
    found = worst_windows(p, bars, horizon=5, as_of=bars["A"]["available_at"].iloc[-1], k=3)
    assert len(found) == 3 and found[0].pnl_fraction < found[1].pnl_fraction + 1e-12 < 1
    assert found[0].pnl_fraction < -0.05
    starts = [pd.Timestamp(w.window[0]) for w in found]
    gaps = [abs((a - b).days) for a in starts for b in starts if a != b]
    assert min(gaps) >= 5
    assert found[0].kind == "worst-window" and "not the worst possible" in found[0].caveat


def test_a_shock_is_an_instantaneous_weighted_move() -> None:
    p = Portfolio.from_values({"EQ": 700.0, "BOND": 300.0})
    res = shock(p, {"EQ": -0.2}, name="equities -20%")
    assert res.pnl_fraction == pytest.approx(-0.14)
    assert res.max_drawdown == pytest.approx(0.14) and res.probability is None
    with pytest.raises(DataValidationError, match="not in the portfolio"):
        shock(p, {"GOLD": -0.1}, name="x")


def test_the_model_implied_tail_reports_its_probability_and_calibration() -> None:
    rng = np.random.default_rng(0)
    paths = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, size=(1000, 5)), axis=1))
    inp = RiskInput(
        name="m", paths=paths, start_value=100.0, index=pd.date_range(AS_OF, periods=5, freq="1D"),
        as_of=AS_OF, calibration_status="uncalibrated", calibration=None, model_mix={"m": 1.0},
    )  # fmt: skip
    tail = model_implied_tail(inp, tail_fraction=0.05)
    assert tail.probability == pytest.approx(0.05)
    assert tail.pnl_fraction < inp.returns().mean() - 0.02
    assert tail.pnl_fraction == pytest.approx(np.mean(np.sort(inp.returns())[:50]), rel=1e-9)
    assert not tail.calibrated and "NOT calibrated" in tail.caveat
    by_dd = model_implied_tail(inp, tail_fraction=0.1, metric="drawdown")
    assert by_dd.max_drawdown >= tail.max_drawdown - 0.05
    with pytest.raises(ValueError, match="tail_fraction"):
        model_implied_tail(inp, tail_fraction=0.7)
    with pytest.raises(ValueError, match="metric"):
        model_implied_tail(inp, metric="vibes")


def test_the_stress_suite_runs_all_three_families(crash_bars) -> None:
    bars, _ = crash_bars
    p = Portfolio.from_values({"A": 600.0, "B": 400.0})
    rng = np.random.default_rng(1)
    inp = RiskInput(
        name="m", paths=100 * np.exp(np.cumsum(rng.normal(0, 0.02, size=(400, 5)), axis=1)),
        start_value=100.0, index=pd.date_range(AS_OF, periods=5, freq="1D"), as_of=AS_OF,
        calibration_status="uncalibrated", calibration=None, model_mix={"m": 1.0},
    )  # fmt: skip
    results = run_stress_suite(
        p, bars, as_of=bars["A"]["available_at"].iloc[-1], horizon=5,
        named_windows=[("crash", bars["A"].index[200], bars["A"].index[204])],
        shocks={"equity crash": {"A": -0.3}}, risk_input=inp, worst_k=2,
    )  # fmt: skip
    kinds = [r.kind for r in results]
    assert kinds.count("historical-replay") == 1 and kinds.count("worst-window") == 2
    assert kinds.count("shock") == 1 and kinds.count("model-implied-tail") == 1
    assert all(len(r.to_dict()) == 9 for r in results)


def _step_corr(paths_a, paths_b, step: int) -> float:
    a = np.log(paths_a[:, step] / 100.0)
    b = np.log(paths_b[:, step] / 100.0)
    return float(np.corrcoef(a, b)[0, 1])


def _random_walk_paths(n, sigma, seed, steps=5):
    rng = np.random.default_rng(seed)
    return 100.0 * np.exp(np.cumsum(rng.normal(0, sigma, size=(n, steps)), axis=1))


def _increments(paths, n):
    return np.diff(np.log(np.concatenate([np.full((n, 1), 100.0), paths], axis=1)), axis=1)


def test_terminal_coupling_correlates_step_k_at_about_rho_k_over_h() -> None:
    """The documented limitation: dependence is imposed at the horizon end, building up to it."""
    rho, n = 0.8, 40000
    corr = pd.DataFrame([[1, rho], [rho, 1]], index=["A", "B"], columns=["A", "B"], dtype=float)
    paths = {"A": _random_walk_paths(n, 0.01, 1), "B": _random_walk_paths(n, 0.01, 2)}
    out = couple_paths(paths, {"A": 100.0, "B": 100.0}, corr, seed=0)
    for k in range(5):
        assert _step_corr(out["A"], out["B"], k) == pytest.approx(rho * (k + 1) / 5, abs=0.04)


def test_stepwise_coupling_correlates_every_step_and_keeps_the_marginals() -> None:
    rho, n = 0.8, 40000
    corr = pd.DataFrame([[1, rho], [rho, 1]], index=["A", "B"], columns=["A", "B"], dtype=float)
    paths = {"A": _random_walk_paths(n, 0.01, 1), "B": _random_walk_paths(n, 0.02, 2)}
    out = couple_paths(paths, {"A": 100.0, "B": 100.0}, corr, seed=0, coupling="stepwise")
    for k in range(5):
        # cumulative-return correlation is rho at every step, not building up to it
        assert _step_corr(out["A"], out["B"], k) == pytest.approx(rho, abs=0.04)
    got, original = _increments(out["A"], n), _increments(paths["A"], n)
    for step in range(5):  # each step's marginal distribution is exactly preserved
        np.testing.assert_allclose(np.sort(got[:, step]), np.sort(original[:, step]), atol=1e-12)


def test_stepwise_coupling_breaks_serial_dependence_and_terminal_keeps_it() -> None:
    """Paths with strong momentum: terminal coupling leaves them whole, stepwise scrambles them."""
    n = 20000
    rng = np.random.default_rng(3)
    trend = rng.normal(0, 0.01, size=(n, 1))  # a persistent per-path trend: steps are dependent
    paths_a = 100 * np.exp(np.cumsum(trend + 0.002 * rng.standard_normal((n, 5)), axis=1))
    paths_b = _random_walk_paths(n, 0.01, 4)
    corr = pd.DataFrame(np.eye(2), index=["A", "B"], columns=["A", "B"])

    def step_autocorr(p):
        inc = _increments(p, n)
        return float(np.corrcoef(inc[:, 0], inc[:, 1])[0, 1])

    both = {"A": paths_a, "B": paths_b}
    start = {"A": 100.0, "B": 100.0}
    kept = couple_paths(both, start, corr, coupling="terminal")
    broken = couple_paths(both, start, corr, coupling="stepwise")
    assert step_autocorr(paths_a) > 0.8
    assert step_autocorr(kept["A"]) > 0.8, "terminal coupling must keep each path intact"
    assert abs(step_autocorr(broken["A"])) < 0.05, "stepwise coupling breaks serial dependence"


def test_aggregation_supports_stepwise_coupling_and_says_what_each_costs(
    known_bars, gaussian_cls
) -> None:
    fa = gaussian_dist(gaussian_cls, known_bars, "a", 0.01, n_samples=2000, seed=1)
    fb = gaussian_dist(gaussian_cls, known_bars, "b", 0.01, n_samples=2000, seed=2)
    p = Portfolio.from_values({"A": 500.0, "B": 500.0})
    corr = pd.DataFrame([[1, 0.9], [0.9, 1]], index=["A", "B"], columns=["A", "B"])
    terminal = aggregate_portfolio(p, {"A": fa, "B": fb}, correlation=corr)
    stepwise = aggregate_portfolio(p, {"A": fa, "B": fb}, correlation=corr, coupling="stepwise")
    # step-1 volatility of an equal-weight pair, sigma 0.01 each: sqrt(0.5 + 0.5 * corr_at_step_1)
    sd = lambda pf: float((pf.paths[:, 0] / 1000.0 - 1.0).std())  # noqa: E731
    assert sd(stepwise) == pytest.approx(
        0.01 * math.sqrt(0.5 + 0.5 * 0.9), rel=0.05
    )  # rho at every step
    assert sd(terminal) == pytest.approx(
        0.01 * math.sqrt(0.5 + 0.5 * 0.9 / 5), rel=0.05
    )  # rho * 1/5
    assert any("serial dependence" in n for n in stepwise.notes)
    assert any("rho * k / H" in n for n in terminal.notes)
    with pytest.raises(ValueError, match="coupling"):
        aggregate_portfolio(p, {"A": fa, "B": fb}, correlation=corr, coupling="vibes")
