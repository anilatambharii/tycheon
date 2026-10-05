"""Score collection: replaying a forecaster at past origins, with nothing from the future."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tycheon.calibration import ScoreSet, collect_scores
from tycheon.data.asof import filter_as_of, to_ns
from tycheon.errors import DataValidationError, LookaheadError
from tycheon.models.timesfm import TimesFMForecaster

H = 5
SIGMA = 0.012


@pytest.fixture(scope="module")
def right_scores(known_bars, gaussian_cls) -> ScoreSet:
    """A forecaster that is exactly right: sigma matches the data-generating process."""
    model = gaussian_cls("right", sigma=SIGMA)
    return collect_scores(
        model, known_bars, as_of=known_bars["available_at"].iloc[-1], horizon=H, n_origins=300,
        max_history=40, n_samples=80,
    )  # fmt: skip


def test_scores_have_the_documented_shapes(right_scores, known_bars) -> None:
    s = right_scores
    assert s.n == 300 and s.horizon == H
    assert s.base_logq.shape == (300, len(s.levels), H)
    assert s.realized.shape == s.outcome_times.shape == (300, H)
    assert s.samples is not None and s.samples.shape == (300, 80, H)
    assert s.origin_times.is_monotonic_increasing
    assert s.model_id == "right"


def test_the_last_origin_leaves_a_full_horizon_of_known_outcomes(right_scores, known_bars) -> None:
    """The last usable origin is `horizon` bars before the end: its outcomes are all published."""
    available = pd.DatetimeIndex(known_bars["available_at"])
    assert right_scores.origin_times[-1] == available[-1 - H]
    assert right_scores.max_outcome_time == available[-1]


@pytest.mark.leakage
def test_no_outcome_is_published_after_as_of(known_bars, gaussian_cls) -> None:
    as_of = known_bars["available_at"].iloc[4000]
    bars = filter_as_of(known_bars, as_of)
    s = collect_scores(
        gaussian_cls("g", SIGMA), bars, as_of=as_of, horizon=H, n_origins=50, max_history=40
    )
    assert s.max_outcome_time <= as_of
    assert (s.outcome_times <= as_of.value).all()


@pytest.mark.leakage
def test_bars_published_after_as_of_are_refused(known_bars, gaussian_cls) -> None:
    as_of = known_bars["available_at"].iloc[4000]
    with pytest.raises(LookaheadError):
        collect_scores(gaussian_cls("g", SIGMA), known_bars, as_of=as_of, horizon=H, n_origins=10)


@pytest.mark.leakage
def test_scores_do_not_depend_on_data_that_arrived_later(known_bars, gaussian_cls) -> None:
    """Scores at as_of must be identical whether or not later bars exist in the store."""
    as_of = known_bars["available_at"].iloc[3000]
    model = gaussian_cls("g", SIGMA)
    truncated = known_bars.iloc[:3001]
    from_full = filter_as_of(known_bars, as_of)
    a = collect_scores(model, truncated, as_of=as_of, horizon=H, n_origins=40, max_history=40)
    b = collect_scores(model, from_full, as_of=as_of, horizon=H, n_origins=40, max_history=40)
    np.testing.assert_array_equal(a.base_logq, b.base_logq)
    np.testing.assert_array_equal(a.realized, b.realized)
    assert a.origin_times.equals(b.origin_times)


def test_the_realised_outcomes_are_log_returns_from_the_origin(right_scores, known_bars) -> None:
    close = known_bars["close"].to_numpy()
    available = pd.DatetimeIndex(known_bars["available_at"])
    i = int(available.get_loc(right_scores.origin_times[10]))
    expected = np.log(close[i + 1 : i + 1 + H] / close[i])
    np.testing.assert_allclose(right_scores.realized[10], expected)


def test_the_exactly_right_forecaster_has_a_flat_pit(right_scores) -> None:
    pit = right_scores.pit()
    assert pit.shape == (300, H)
    assert abs(pit.mean() - 0.5) < 0.03
    # each decile holds roughly a tenth of the outcomes
    counts, _ = np.histogram(pit, bins=10, range=(0, 1))
    assert counts.min() > 0.6 * pit.size / 10


def test_overlapping_origins_are_flagged(known_bars, gaussian_cls) -> None:
    s = collect_scores(
        gaussian_cls("g", SIGMA), known_bars, as_of=known_bars["available_at"].iloc[-1],
        horizon=H, n_origins=40, stride=1, max_history=40,
    )  # fmt: skip
    assert any("overlapping horizons" in note for note in s.notes)
    assert s.stride == 1


def test_default_stride_does_not_overlap(right_scores) -> None:
    assert right_scores.stride == H
    assert right_scores.notes == ()


def test_too_little_data_is_an_error(known_bars, gaussian_cls) -> None:
    tiny = known_bars.iloc[:30]
    with pytest.raises(DataValidationError, match="too few"):
        collect_scores(
            gaussian_cls("g", SIGMA),
            tiny,
            as_of=tiny["available_at"].iloc[-1],
            horizon=H,
            n_origins=5,
        )


def test_invalid_arguments_are_rejected(known_bars, gaussian_cls) -> None:
    as_of = known_bars["available_at"].iloc[-1]
    model = gaussian_cls("g", SIGMA)
    with pytest.raises(ValueError, match="positive"):
        collect_scores(model, known_bars, as_of=as_of, horizon=0, n_origins=5)
    with pytest.raises(ValueError, match="stride"):
        collect_scores(model, known_bars, as_of=as_of, horizon=H, n_origins=5, stride=0)


def test_a_quantile_only_model_is_scored_on_the_levels_it_has(known_bars, fake_timesfm) -> None:
    """TimesFM supports 0.1 to 0.9 only: the score set must say so rather than invent tails."""
    model = TimesFMForecaster(engine=fake_timesfm)
    s = collect_scores(
        model, known_bars, as_of=known_bars["available_at"].iloc[-1], horizon=H, n_origins=40,
        max_history=60,
    )  # fmt: skip
    assert min(s.levels) >= 0.1 - 1e-12 and max(s.levels) <= 0.9 + 1e-12
    assert 0.5 in s.levels and 0.01 not in s.levels
    assert s.samples is None
    assert s.pit().shape == (40, H)


def test_split_recent_purges_fit_origins_whose_outcomes_overlap_the_holdout(
    known_bars, gaussian_cls
) -> None:
    s = collect_scores(
        gaussian_cls("g", SIGMA), known_bars, as_of=known_bars["available_at"].iloc[-1],
        horizon=H, n_origins=60, stride=1, max_history=40,
    )  # fmt: skip
    older, recent = s.split_recent(20)
    assert recent.n == 20
    assert older.n < 40, "overlapping origins just before the holdout must be dropped"
    assert older.outcome_times.max() <= to_ns(recent.origin_times)[0]


def test_split_recent_validates_the_holdout_size(right_scores) -> None:
    with pytest.raises(ValueError, match="holdout"):
        right_scores.split_recent(0)
    with pytest.raises(ValueError, match="holdout"):
        right_scores.split_recent(right_scores.n)


def test_a_malformed_score_set_is_rejected(right_scores) -> None:
    with pytest.raises(DataValidationError):
        ScoreSet(
            model_id="x",
            horizon=H,
            levels=right_scores.levels,
            origin_times=right_scores.origin_times,
            outcome_times=right_scores.outcome_times,
            base_logq=right_scores.base_logq[:, :2, :],
            realized=right_scores.realized,
            samples=None,
            n_samples=1,
            stride=1,
        )
