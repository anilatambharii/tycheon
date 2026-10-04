"""Providers: the as-of guard every adapter inherits, plus file, yfinance and vendor adapters."""

from __future__ import annotations

import sys
import types

import pandas as pd
import pytest

from tycheon.data import MarketDataProvider, SampleProvider
from tycheon.data.providers.base import ProviderBase, bar_duration_for
from tycheon.data.providers.file import FileProvider
from tycheon.data.providers.licensed import LicensedVendorProvider, LicensedVendorStub
from tycheon.data.providers.yfinance import NonCommercialDataWarning, YFinanceProvider
from tycheon.errors import (
    DataValidationError,
    LookaheadError,
    MissingCredentialsError,
    OptionalDependencyError,
    ProviderDisabledError,
    ProviderError,
)


def _utc(text: str) -> pd.Timestamp:
    return pd.Timestamp(text, tz="UTC")


class _Fixed(ProviderBase):
    """A provider over an in-memory frame, to test the base-class guard in isolation."""

    name = "fixed"

    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame

    def _load_bars(self, symbol: str, frequency: str) -> pd.DataFrame:
        return self.frame


# ---------------------------------------------------------------- the base guard
def test_providers_satisfy_the_protocol() -> None:
    assert isinstance(SampleProvider(), MarketDataProvider)
    assert isinstance(FileProvider("."), MarketDataProvider)


@pytest.mark.leakage
def test_the_base_class_hides_bars_not_yet_available(bars_factory) -> None:
    bars = bars_factory(20)
    as_of = pd.Timestamp(bars["available_at"].iloc[9])
    out = _Fixed(bars).fetch_bars("TEST", start=None, end=None, as_of=as_of)
    assert len(out) == 10
    assert out["available_at"].max() <= as_of


@pytest.mark.leakage
def test_the_base_class_refuses_a_window_past_as_of(bars_factory) -> None:
    with pytest.raises(LookaheadError):
        _Fixed(bars_factory(20)).fetch_bars(
            "TEST", start=None, end=_utc("2024-01-20"), as_of=_utc("2024-01-10")
        )


def test_the_window_is_applied(bars_factory) -> None:
    out = _Fixed(bars_factory(20)).fetch_bars(
        "TEST", start=_utc("2024-01-05"), end=_utc("2024-01-08"), as_of=_utc("2030-01-01")
    )
    assert out.index[0] == _utc("2024-01-05")
    assert out.index[-1] == _utc("2024-01-08")


def test_a_naive_as_of_is_refused_by_every_provider(bars_factory) -> None:
    with pytest.raises(DataValidationError, match="timezone-aware"):
        _Fixed(bars_factory(3)).fetch_bars(
            "TEST", start=None, end=None, as_of=pd.Timestamp("2030-01-01")
        )


def test_symbols_are_validated_before_anything_is_loaded(bars_factory) -> None:
    with pytest.raises(DataValidationError, match="invalid symbol"):
        _Fixed(bars_factory(3)).fetch_bars("../x", start=None, end=None, as_of=_utc("2030-01-01"))
    with pytest.raises(DataValidationError, match="invalid symbol"):
        _Fixed(bars_factory(3)).fetch_corporate_actions("a/b", as_of=_utc("2030-01-01"))


def test_bar_duration() -> None:
    assert bar_duration_for("1D") == pd.Timedelta("1D")
    assert bar_duration_for("5min") == pd.Timedelta("5min")
    for bad in ("nonsense", "0min", "-1D"):
        with pytest.raises(DataValidationError):
            bar_duration_for(bad)


def test_default_corporate_actions_are_empty(bars_factory) -> None:
    assert _Fixed(bars_factory(3)).fetch_corporate_actions("TEST", as_of=_utc("2030-01-01")).empty


# --------------------------------------------------------------------- the files
def _write_csv(path, frame: pd.DataFrame, column: str = "timestamps") -> None:
    out = frame[["open", "high", "low", "close", "volume", "amount"]].copy()
    out.insert(0, column, frame.index.tz_localize(None))
    out.to_csv(path, index=False)


def test_file_provider_reads_a_kronos_style_csv(tmp_path, bars_factory) -> None:
    """The example CSVs shipped with upstream Kronos use a ``timestamps`` column."""
    _write_csv(tmp_path / "TEST.csv", bars_factory(30))
    out = FileProvider(tmp_path).fetch_bars("TEST", start=None, end=None, as_of=_utc("2030-01-01"))
    assert len(out) == 30
    assert str(out.index.tz) == "UTC"
    assert (out["available_at"] - out.index == pd.Timedelta("1D")).all()


def test_file_provider_honours_the_source_timezone(tmp_path, bars_factory) -> None:
    _write_csv(tmp_path / "TEST.csv", bars_factory(5), column="datetime")
    out = FileProvider(tmp_path, source_tz="Asia/Shanghai").fetch_bars(
        "TEST", start=None, end=None, as_of=_utc("2030-01-01")
    )
    assert out.index[0] == _utc("2023-12-31 16:00")


@pytest.mark.leakage
def test_file_provider_hides_future_rows(tmp_path, bars_factory) -> None:
    bars = bars_factory(30)
    _write_csv(tmp_path / "TEST.csv", bars)
    as_of = pd.Timestamp(bars["available_at"].iloc[14])
    out = FileProvider(tmp_path).fetch_bars("TEST", start=None, end=None, as_of=as_of)
    assert len(out) == 15


def test_file_provider_availability_lag(tmp_path, bars_factory) -> None:
    _write_csv(tmp_path / "TEST.csv", bars_factory(10))
    provider = FileProvider(tmp_path, availability_lag=pd.Timedelta("2h"))
    out = provider.fetch_bars("TEST", start=None, end=None, as_of=_utc("2030-01-01"))
    assert (out["available_at"] - out.index == pd.Timedelta("26h")).all()


def test_file_provider_reads_parquet(tmp_path, bars_factory) -> None:
    bars = bars_factory(10)
    bars.to_parquet(tmp_path / "TEST.parquet")
    out = FileProvider(tmp_path, fmt="parquet").fetch_bars(
        "TEST", start=None, end=None, as_of=_utc("2030-01-01")
    )
    assert len(out) == 10


def test_file_provider_reads_actions_alongside(tmp_path, bars_factory) -> None:
    _write_csv(tmp_path / "TEST.csv", bars_factory(10))
    pd.DataFrame({"ex_date": ["2024-01-06"], "kind": ["split"], "value": [2.0]}).to_csv(
        tmp_path / "TEST.actions.csv", index=False
    )
    provider = FileProvider(tmp_path)
    assert len(provider.fetch_corporate_actions("TEST", as_of=_utc("2030-01-01"))) == 1
    assert provider.fetch_corporate_actions("TEST", as_of=_utc("2024-01-01")).empty


def test_file_provider_without_an_actions_file_has_none(tmp_path, bars_factory) -> None:
    _write_csv(tmp_path / "TEST.csv", bars_factory(5))
    assert FileProvider(tmp_path).fetch_corporate_actions("TEST", as_of=_utc("2030-01-01")).empty


def test_file_provider_errors(tmp_path, bars_factory) -> None:
    with pytest.raises(ProviderError, match="no data file"):
        FileProvider(tmp_path).fetch_bars("MISSING", start=None, end=None, as_of=_utc("2030-01-01"))
    with pytest.raises(ProviderError, match="unsupported"):
        FileProvider(tmp_path, fmt="xlsx")  # type: ignore[arg-type]
    pd.DataFrame({"a": [1], "close": [1]}).to_csv(tmp_path / "NOTIME.csv", index=False)
    with pytest.raises(ProviderError, match="no timestamp column"):
        FileProvider(tmp_path).fetch_bars("NOTIME", start=None, end=None, as_of=_utc("2030-01-01"))


def test_file_provider_cannot_read_outside_its_root(tmp_path, bars_factory) -> None:
    outside = tmp_path / "secret.csv"
    _write_csv(outside, bars_factory(5))
    root = tmp_path / "data"
    root.mkdir()
    with pytest.raises(DataValidationError, match="invalid symbol"):
        FileProvider(root).fetch_bars("../secret", start=None, end=None, as_of=_utc("2030-01-01"))


# ---------------------------------------------------------------------- yfinance
def _fake_yfinance(frame: pd.DataFrame, actions: pd.DataFrame | None = None) -> types.ModuleType:
    module = types.ModuleType("yfinance")

    class Ticker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def history(self, **kwargs):
            out = frame.rename(columns=str.capitalize)[["Open", "High", "Low", "Close", "Volume"]]
            out.index = out.index.tz_convert("America/New_York")
            return out

        @property
        def actions(self):
            return actions

    module.Ticker = Ticker  # type: ignore[attr-defined]
    return module


def test_yfinance_is_off_by_default(monkeypatch) -> None:
    monkeypatch.delenv("TYCHEON_ALLOW_YFINANCE", raising=False)
    with pytest.raises(ProviderDisabledError, match="TYCHEON_ALLOW_YFINANCE"):
        YFinanceProvider()


@pytest.mark.parametrize("env", ["prod", "production", "staging", "cloud", "PROD"])
def test_yfinance_is_never_available_in_a_hosted_environment(monkeypatch, env) -> None:
    """Even with the flag set: the cloud product never serves Yahoo data."""
    monkeypatch.setenv("TYCHEON_ENV", env)
    monkeypatch.setenv("TYCHEON_ALLOW_YFINANCE", "true")
    with pytest.raises(ProviderDisabledError, match="never available"):
        YFinanceProvider()


def test_yfinance_warns_that_it_is_non_commercial(monkeypatch) -> None:
    monkeypatch.setenv("TYCHEON_ENV", "local")
    monkeypatch.setenv("TYCHEON_ALLOW_YFINANCE", "true")
    with pytest.warns(NonCommercialDataWarning, match="non-commercial"):
        provider = YFinanceProvider()
    assert provider.commercial_use is False
    assert "DEV ONLY" in provider.name


def test_yfinance_requires_the_package_and_names_the_fix(monkeypatch) -> None:
    monkeypatch.setenv("TYCHEON_ALLOW_YFINANCE", "true")
    monkeypatch.setitem(sys.modules, "yfinance", None)  # makes `import yfinance` raise ImportError
    with pytest.warns(NonCommercialDataWarning):
        provider = YFinanceProvider()
    with pytest.raises(OptionalDependencyError, match="pip install yfinance"):
        provider.fetch_bars("AAPL", start=None, end=None, as_of=_utc("2030-01-01"))


def test_yfinance_bars_still_pass_through_the_as_of_guard(monkeypatch, bars_factory) -> None:
    bars = bars_factory(30)
    monkeypatch.setenv("TYCHEON_ALLOW_YFINANCE", "true")
    monkeypatch.setitem(sys.modules, "yfinance", _fake_yfinance(bars))
    with pytest.warns(NonCommercialDataWarning):
        provider = YFinanceProvider()
    as_of = pd.Timestamp(bars["available_at"].iloc[9])
    out = provider.fetch_bars("AAPL", start=None, end=None, as_of=as_of)
    assert len(out) == 10
    assert out["available_at"].max() <= as_of


def test_yfinance_rejects_a_frequency_it_cannot_serve(monkeypatch, bars_factory) -> None:
    monkeypatch.setenv("TYCHEON_ALLOW_YFINANCE", "true")
    monkeypatch.setitem(sys.modules, "yfinance", _fake_yfinance(bars_factory(5)))
    with pytest.warns(NonCommercialDataWarning):
        provider = YFinanceProvider()
    with pytest.raises(ProviderDisabledError, match="not supported"):
        provider.fetch_bars(
            "AAPL", start=None, end=None, as_of=_utc("2030-01-01"), frequency="7min"
        )


def test_yfinance_maps_dividends_and_splits(monkeypatch, bars_factory) -> None:
    raw = pd.DataFrame(
        {"Dividends": [0.0, 0.5, 0.0], "Stock Splits": [0.0, 0.0, 2.0]},
        index=pd.DatetimeIndex(["2024-01-02", "2024-01-03", "2024-01-04"], tz="UTC"),
    )
    monkeypatch.setenv("TYCHEON_ALLOW_YFINANCE", "true")
    monkeypatch.setitem(sys.modules, "yfinance", _fake_yfinance(bars_factory(5), raw))
    with pytest.warns(NonCommercialDataWarning):
        provider = YFinanceProvider()
    actions = provider.fetch_corporate_actions("AAPL", as_of=_utc("2030-01-01"))
    assert sorted(actions["kind"]) == ["dividend", "split"]
    assert sorted(actions["value"]) == [0.5, 2.0]


def test_yfinance_without_actions_returns_an_empty_frame(monkeypatch, bars_factory) -> None:
    monkeypatch.setenv("TYCHEON_ALLOW_YFINANCE", "true")
    monkeypatch.setitem(sys.modules, "yfinance", _fake_yfinance(bars_factory(5), pd.DataFrame()))
    with pytest.warns(NonCommercialDataWarning):
        provider = YFinanceProvider()
    assert provider.fetch_corporate_actions("AAPL", as_of=_utc("2030-01-01")).empty


# ---------------------------------------------------------------- licensed vendors
def test_a_vendor_without_a_key_refuses_and_says_how_to_fix_it(monkeypatch) -> None:
    monkeypatch.delenv("TYCHEON_DATA_API_KEY", raising=False)
    stub = LicensedVendorStub()
    assert not stub.has_credentials
    with pytest.raises(MissingCredentialsError, match="TYCHEON_DATA_API_KEY"):
        stub.fetch_bars("AAPL", start=None, end=None, as_of=_utc("2030-01-01"))


def test_the_key_is_never_printed(monkeypatch) -> None:
    monkeypatch.delenv("TYCHEON_DATA_API_KEY", raising=False)
    leaked_value = "sk-live-0123456789abcdef"  # pragma: allowlist secret
    stub = LicensedVendorStub(api_key=leaked_value)
    assert leaked_value not in repr(stub)
    assert leaked_value not in str(stub)
    assert "set" in repr(stub)
    assert stub.has_credentials
    with pytest.raises(ProviderError) as excinfo:
        stub.fetch_bars("AAPL", start=None, end=None, as_of=_utc("2030-01-01"))
    assert leaked_value not in str(excinfo.value)


def test_the_key_can_come_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("TYCHEON_DATA_API_KEY", "  from-env  ")
    assert LicensedVendorStub().has_credentials


def test_the_stub_refuses_to_pretend_to_be_a_vendor() -> None:
    with pytest.raises(ProviderError, match="stub"):
        LicensedVendorStub(api_key="k").fetch_bars(
            "AAPL", start=None, end=None, as_of=_utc("2030-01-01")
        )


@pytest.mark.leakage
def test_a_customer_adapter_inherits_the_as_of_guard(bars_factory) -> None:
    class Acme(LicensedVendorProvider):
        name = "acme"

        def _request_bars(self, symbol, frequency, *, api_key):
            assert api_key == "customer-key"  # pragma: allowlist secret
            return bars_factory(30)

    provider = Acme(api_key="customer-key")  # pragma: allowlist secret
    as_of = pd.Timestamp(bars_factory(30)["available_at"].iloc[4])
    assert len(provider.fetch_bars("AAPL", start=None, end=None, as_of=as_of)) == 5
    assert provider.commercial_use is True
    assert "does not redistribute" in provider.license_terms
