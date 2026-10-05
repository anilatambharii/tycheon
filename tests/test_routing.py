"""Regime detection and routing, on a world where the right forecaster changes with volatility."""

from __future__ import annotations

import numpy as np
import pytest

from tycheon.calibration import ConformalCalibrator, SplitConformal, collect_scores
from tycheon.calibration.metrics import quantile_score
from tycheon.errors import DataValidationError, ModelError
from tycheon.models.baselines import RandomWalkForecaster
from tycheon.models.timesfm import TimesFMForecaster
from tycheon.routing import (
    EnsembleForecaster,
    MarkovSwitchingRegimeDetector,
    RegimeRouter,
    VolatilityRegimeDetector,
    labels_for_scores,
)

H = 3
CALM, TURBULENT = 0.006, 0.024


@pytest.fixture(scope="module")
def world(bars_from_returns):
    """6400 bars alternating calm and turbulent blocks. Returns (bars, true regime per bar)."""
    rng = np.random.default_rng(21)
    regimes = []
    state = 0
    while len(regimes) < 6400:
        regimes.extend([state] * int(rng.integers(180, 320)))
        state = 1 - state
    regimes = np.array(regimes[:6400])
    sigma = np.where(regimes == 0, CALM, TURBULENT)
    return bars_from_returns(sigma * rng.standard_normal(6400)), regimes


@pytest.fixture(scope="module")
def members(gaussian_cls):
    return {
        "calm-model": gaussian_cls("calm-model", sigma=CALM),
        "turbulent-model": gaussian_cls("turbulent-model", sigma=TURBULENT),
        "random-walk": RandomWalkForecaster(window=250),
    }


@pytest.fixture(scope="module")
def detector():
    return VolatilityRegimeDetector(n_regimes=2, window=20, lookback=500, min_history=150)


@pytest.fixture(scope="module")
def member_scores(world, members):
    bars, _ = world
    as_of = bars["available_at"].iloc[-1]
    return {
        name: collect_scores(
            model, bars, as_of=as_of, horizon=H, n_origins=700, max_history=300, n_samples=60
        )
        for name, model in members.items()
    }


@pytest.fixture(scope="module")
def labels(world, detector, member_scores):
    bars, _ = world
    return labels_for_scores(detector, bars, next(iter(member_scores.values())))


# ----------------------------------------------------------------------- detectors
def test_the_volatility_detector_recovers_the_true_regimes(world, detector) -> None:
    bars, truth = world
    found = detector.labels(bars)
    ok = found >= 0
    assert ok[:140].sum() == 0, "no label before there is enough history"
    # allow a transition window around each true switch (the detector is causal, so it lags)
    switch = np.flatnonzero(np.diff(truth) != 0) + 1
    near = np.zeros(len(truth), dtype=bool)
    for s in switch:
        near[s : s + 40] = True
    judged = ok & ~near
    assert (found[judged] == truth[judged]).mean() > 0.9


def test_detector_labels_are_ordered_by_volatility(world, detector) -> None:
    bars, _ = world
    found = detector.labels(bars)
    r = np.diff(np.log(bars["close"].to_numpy()), prepend=np.nan)
    assert np.nanstd(r[found == 1]) > 2 * np.nanstd(r[found == 0])


@pytest.mark.leakage
def test_a_regime_label_never_depends_on_later_bars(world, detector) -> None:
    """Causality: the label at bar t is identical computed from bars[:t+1] or from all bars."""
    bars, _ = world
    full = detector.labels(bars)
    for t in (400, 1500, 3200, 5000):
        assert detector.labels(bars.iloc[: t + 1])[-1] == full[t]


def test_the_kmeans_and_quantile_methods_agree_on_clear_regimes(world) -> None:
    bars, _ = world
    quantile = VolatilityRegimeDetector(method="quantile", min_history=150).labels(bars)
    kmeans = VolatilityRegimeDetector(method="kmeans", min_history=150).labels(bars)
    both = (quantile >= 0) & (kmeans >= 0)
    assert (quantile[both] == kmeans[both]).mean() > 0.8


def test_three_regimes_are_ordered(world) -> None:
    bars, _ = world
    three = VolatilityRegimeDetector(n_regimes=3, min_history=150).labels(bars)
    assert set(np.unique(three[three >= 0])) == {0, 1, 2}


def test_the_markov_switching_detector_finds_the_regimes(world) -> None:
    bars, truth = world
    window = bars.iloc[:1500]
    found = MarkovSwitchingRegimeDetector(lookback=1000, min_history=200).labels(window)
    ok = found >= 0
    assert ok.sum() == 1000 and not ok[:500].any()
    assert (found[ok] == truth[:1500][ok]).mean() > 0.85


def test_the_markov_switching_detector_needs_history(world) -> None:
    bars, _ = world
    assert (MarkovSwitchingRegimeDetector(min_history=200).labels(bars.iloc[:100]) == -1).all()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"n_regimes": 1}, "n_regimes"),
        ({"method": "hmm"}, "method"),
        ({"window": 30, "min_history": 20}, "min_history"),
    ],
)
def test_detector_settings_are_validated(kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        VolatilityRegimeDetector(**kwargs)


def test_detectors_need_positive_closes(bars_from_returns) -> None:
    bars = bars_from_returns(np.zeros(300))
    bars["close"] = -1.0
    with pytest.raises(DataValidationError, match="positive"):
        VolatilityRegimeDetector().labels(bars)


# ---------------------------------------------------------------------- the router
def test_the_router_puts_weight_on_whoever_wins_in_each_regime(member_scores, labels) -> None:
    """The headline test: weights follow the regime."""
    w = RegimeRouter().fit(member_scores, labels)
    calm, turb = w.by_regime[0], w.by_regime[1]
    assert calm["calm-model"] > calm["turbulent-model"] + 0.2
    assert turb["turbulent-model"] > turb["calm-model"] + 0.2
    assert calm["calm-model"] > 0.5 and turb["turbulent-model"] > 0.5
    for regime in (calm, turb, w.overall):
        assert sum(regime.values()) == pytest.approx(1.0)
    assert set(w.model_ids) == {"calm-model", "turbulent-model", "random-walk"}
    assert w.n_by_regime[0] > 100 and w.n_by_regime[1] > 100


def test_weights_follow_the_recent_losses_in_that_regime(member_scores, labels) -> None:
    w = RegimeRouter().fit(member_scores, labels)
    loss = w.mean_loss_by_regime
    assert loss[0]["calm-model"] < loss[0]["turbulent-model"]
    assert loss[1]["turbulent-model"] < loss[1]["calm-model"]


def test_shrinkage_keeps_every_candidate_alive(member_scores, labels) -> None:
    w = RegimeRouter(shrinkage=0.3).fit(member_scores, labels)
    for regime in w.by_regime.values():
        assert min(regime.values()) >= 0.3 / 3 - 1e-12
    sharp = RegimeRouter(shrinkage=0.0, sensitivity=50.0).fit(member_scores, labels)
    assert min(sharp.by_regime[0].values()) < 0.01


def test_a_regime_with_too_few_origins_uses_the_overall_weights_and_says_so(
    member_scores, labels
) -> None:
    rare = labels.copy()
    rare[:] = 0
    rare[-3:] = 1  # three origins in regime 1
    w = RegimeRouter(min_per_regime=8).fit(member_scores, rare)
    assert 1 not in w.by_regime
    assert w.for_regime(1) == w.overall
    assert any("regime 1" in n for n in w.notes)


def test_the_random_walk_must_always_be_a_candidate(
    member_scores, labels, members, detector
) -> None:
    without = {k: v for k, v in member_scores.items() if k != "random-walk"}
    with pytest.raises(ModelError, match="random-walk must always be a candidate"):
        RegimeRouter().fit(without, labels)
    weights = RegimeRouter().fit(member_scores, labels)
    with pytest.raises(ModelError, match="random-walk must always be a candidate"):
        EnsembleForecaster(
            {k: v for k, v in members.items() if k != "random-walk"}, detector, weights
        )


def test_candidates_must_be_scored_at_the_same_origins(member_scores, labels) -> None:
    broken = dict(member_scores)
    broken["calm-model"] = member_scores["calm-model"].take(slice(0, 100))
    with pytest.raises(DataValidationError, match="same origins"):
        RegimeRouter().fit(broken, labels[:100])


def test_labels_must_match_the_origins(member_scores, labels) -> None:
    with pytest.raises(DataValidationError, match="one entry per scored origin"):
        RegimeRouter().fit(member_scores, labels[:-5])


@pytest.mark.parametrize("shrinkage", [-0.1, 1.0])
def test_router_shrinkage_is_validated(shrinkage) -> None:
    with pytest.raises(ValueError, match="shrinkage"):
        RegimeRouter(shrinkage=shrinkage)


# ----------------------------------------------------------------- the online replay
@pytest.mark.leakage
def test_the_replayed_weights_at_an_origin_use_only_what_was_known_then(
    member_scores, labels
) -> None:
    """Truncating the history after origin k must not change anything the replay did up to k."""
    router = RegimeRouter()
    full = router.replay(member_scores, labels)
    k = 350
    cut = {name: s.take(slice(0, k)) for name, s in member_scores.items()}
    short = router.replay(cut, labels[:k])
    np.testing.assert_array_equal(full.base_logq[:k], short.base_logq)


def test_the_routed_ensemble_beats_every_fixed_forecaster_out_of_sample(
    member_scores, labels
) -> None:
    """Each fixed model is wrong in one regime; routing should win overall, on honest weights."""
    ensemble = RegimeRouter().replay(member_scores, labels)
    levels = ensemble.levels

    def score(s) -> float:
        keep = [s.levels.index(lv) for lv in levels]
        return float(quantile_score(levels, s.base_logq[:, keep, :], s.realized)[150:].mean())

    fixed = {name: score(s) for name, s in member_scores.items()}
    assert score(ensemble) < min(fixed.values()), (score(ensemble), fixed)


def test_the_replayed_ensemble_can_itself_be_calibrated(member_scores, labels) -> None:
    ens = RegimeRouter().replay(member_scores, labels)
    assert ens.model_id == "regime-ensemble" and ens.samples is not None
    status = ConformalCalibrator(SplitConformal(), holdout_fraction=0.3).fit(ens).status()
    assert status[0] in ("calibrated", "stale")


# -------------------------------------------------------------- the ensemble forecaster
def _fit(member_scores, labels):
    return RegimeRouter().fit(member_scores, labels)


def test_the_ensemble_shifts_its_weights_when_the_regime_shifts(
    world, members, detector, member_scores, labels
) -> None:
    """The same forecaster, asked at the end of a calm spell and then of a turbulent one."""
    bars, truth = world
    ens = EnsembleForecaster(members, detector, _fit(member_scores, labels))
    calm_end = int(np.flatnonzero((truth == 0) & (np.arange(len(truth)) > 3000))[100])
    turb_end = int(np.flatnonzero((truth == 1) & (np.arange(len(truth)) > 3000))[100])
    out = {}
    for name, t in (("calm", calm_end), ("turbulent", turb_end)):
        history = bars.iloc[: t + 1]
        out[name] = ens.predict(history, H, 200, history["available_at"].iloc[-1], seed=1)
    calm_mix, turb_mix = out["calm"].model_mix, out["turbulent"].model_mix
    assert calm_mix["calm-model"] > calm_mix["turbulent-model"]
    assert turb_mix["turbulent-model"] > turb_mix["calm-model"]
    assert out["calm"].metadata.diagnostics["regime"] == 0
    assert out["turbulent"].metadata.diagnostics["regime"] == 1
    assert out["turbulent"].samples[:, -1].std() > 1.5 * out["calm"].samples[:, -1].std() * (
        out["turbulent"].last_close / out["calm"].last_close
    )


def test_the_ensemble_output_carries_the_required_provenance(
    world, members, detector, member_scores, labels
) -> None:
    bars, _ = world
    ens = EnsembleForecaster(members, detector, _fit(member_scores, labels))
    history = bars.iloc[:4000]
    d = ens.predict(history, H, 300, history["available_at"].iloc[-1], seed=2)
    assert d.model_card == "docs/models/regime-ensemble.md"
    assert d.metadata.model_id == "regime-ensemble"
    assert d.calibration_status == "uncalibrated" and d.calibration is None
    assert sum(d.model_mix.values()) == pytest.approx(1.0)
    assert set(d.model_mix) <= {"calm-model", "turbulent-model", "random-walk"}
    assert d.samples.shape == (300, H) and d.has_paths
    assert d.metadata.diagnostics["mode"] == "pool"
    assert d.to_dict()["model_mix"] == d.model_mix


def test_pool_mode_leaves_quantile_only_members_out_and_says_so(
    world, members, detector, member_scores, labels, fake_timesfm
) -> None:
    bars, _ = world
    mixed = {**members, "timesfm": TimesFMForecaster(engine=fake_timesfm)}
    ens = EnsembleForecaster(mixed, detector, _fit(member_scores, labels))
    history = bars.iloc[:4000]
    d = ens.predict(history, H, 100, history["available_at"].iloc[-1])
    assert "timesfm" not in d.model_mix and d.has_paths
    assert d.metadata.diagnostics["excluded_members"] == ["timesfm"]


def test_quantile_mode_averages_quantiles_and_returns_no_paths(
    world, members, detector, member_scores, labels, fake_timesfm
) -> None:
    bars, _ = world
    tfm = TimesFMForecaster(engine=fake_timesfm)
    scored_tfm = collect_scores(
        tfm, bars, as_of=bars["available_at"].iloc[-1], horizon=H, n_origins=700, max_history=300
    )
    mixed = {**members, "timesfm": tfm}
    weights = RegimeRouter().fit({**member_scores, "timesfm": scored_tfm}, labels)
    ens = EnsembleForecaster(mixed, detector, weights, mode="quantile")
    history = bars.iloc[:4000]
    d = ens.predict(history, H, 100, history["available_at"].iloc[-1])
    assert d.samples is None and not d.has_paths and not ens.supports_paths
    assert (np.diff(d.quantiles, axis=0) >= 0).all()
    assert set(d.model_mix) <= set(mixed) and "timesfm" in d.model_mix
    assert min(d.quantile_levels) >= 0.1 - 1e-9 and max(d.quantile_levels) <= 0.9 + 1e-9


def test_a_member_the_router_never_scored_is_an_error_not_a_silent_drop(
    members, detector, member_scores, labels, fake_timesfm
) -> None:
    weights = _fit(member_scores, labels)
    mixed = {**members, "timesfm": TimesFMForecaster(engine=fake_timesfm)}
    with pytest.raises(ModelError, match="not scored by the router"):
        EnsembleForecaster(mixed, detector, weights, mode="quantile")


def test_ensemble_settings_are_validated(members, detector, member_scores, labels) -> None:
    weights = _fit(member_scores, labels)
    with pytest.raises(ValueError, match="mode"):
        EnsembleForecaster(members, detector, weights, mode="vibes")


def test_an_unseen_regime_falls_back_to_the_overall_weights(
    world, members, detector, member_scores
) -> None:
    bars, _ = world
    only_calm = RegimeRouter().fit(
        member_scores, np.zeros(next(iter(member_scores.values())).n, dtype=np.int64)
    )
    ens = EnsembleForecaster(members, detector, only_calm)
    regime, w = ens.current_weights(bars.iloc[:4000])
    assert sum(w.values()) == pytest.approx(1.0) and regime in (-1, 0, 1)
