from __future__ import annotations

from datetime import date
import json

import pytest

from dashboard_v2.services.sec_fundamentals_provider import (
    SECFundamentalsError,
    analyze_company_facts,
    assess_ticker_from_sec,
    extract_quarterly_facts,
    fetch_sec_json,
    load_ticker_cik_map,
    resolve_ticker_cik,
    valid_sec_user_agent,
)


def _sec_facts(*, eps_current=2.0, eps_prior=1.0, revenue_current=120.0, revenue_prior=100.0,
               current_filed="2026-08-01", prior_filed="2025-08-01"):
    def record(start, end, val, filed, form="10-Q"):
        return {"start": start, "end": end, "val": val, "filed": filed, "form": form, "fp": "Q2"}

    return {
        "entityName": "Example Corp",
        "facts": {"us-gaap": {
            "EarningsPerShareDiluted": {"units": {"USD/shares": [
                record("2026-04-01", "2026-06-30", eps_current, current_filed),
                record("2025-04-01", "2025-06-30", eps_prior, prior_filed),
            ]}},
            "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
                record("2026-04-01", "2026-06-30", revenue_current, current_filed),
                record("2025-04-01", "2025-06-30", revenue_prior, prior_filed),
            ]}},
        }},
    }


def test_user_agent_requires_an_identifiable_contact_email():
    assert valid_sec_user_agent("PortfolioDashboardV2 contact: maintainer@mydomain.com")
    assert not valid_sec_user_agent("")
    assert not valid_sec_user_agent("PortfolioDashboardV2 contact: your-email@example.com")
    assert not valid_sec_user_agent("PortfolioDashboardV2 contact: your-real-email@yourdomain.com")
    with pytest.raises(SECFundamentalsError, match="SEC_USER_AGENT_NOT_CONFIGURED"):
        fetch_sec_json("https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json", "")


def test_ticker_map_keeps_duplicates_and_normalizes_share_class_punctuation(monkeypatch):
    def fake_fetch(url, user_agent):
        assert url.endswith("company_tickers.json")
        return {
            "0": {"cik_str": 1, "ticker": "BRK-B", "title": "Company A"},
            "1": {"cik_str": 1, "ticker": "BRK.B", "title": "Company A"},
            "2": {"cik_str": 2, "ticker": "BRK-B", "title": "Company B"},
            "3": {"cik_str": 3, "ticker": "NVDA", "title": "NVIDIA"},
        }
    monkeypatch.setattr("dashboard_v2.services.sec_fundamentals_provider.fetch_sec_json", fake_fetch)
    ticker_map = load_ticker_cik_map("PortfolioDashboardV2 ops@mydomain.com")
    assert len(ticker_map["BRK-B"]) == 3
    cik, _, error = resolve_ticker_cik("NVDA", ticker_map)
    assert cik == 3 and error is None
    cik, _, error = resolve_ticker_cik("BRK.B", ticker_map)
    assert cik is None and error == "AMBIGUOUS_TICKER_CIK_MAPPING"


def test_positive_eps_and_revenue_yoy_growth_pass_gate():
    out = analyze_company_facts(
        "NVDA", 1045810, "Example Corp", _sec_facts(), as_of_date=date(2026, 10, 22)
    )
    assert out["fundamental_gate_status"] == "PASS"
    assert out["fundamental_damage_status"] == "CLEAR"
    assert out["eps_growth_yoy_pct"] == pytest.approx(100.0)
    assert out["revenue_growth_yoy_pct"] == pytest.approx(20.0)
    assert out["latest_fiscal_quarter_end"] == "2026-06-30"
    assert out["latest_filing_date"] == "2026-08-01"
    assert out["eps_fact_tag"] == "EarningsPerShareDiluted"


def test_nonpositive_eps_growth_fails_gate_and_flags_latest_quarter_damage():
    out = analyze_company_facts(
        "ABC", 123, "Example", _sec_facts(eps_current=0.8, eps_prior=1.0), as_of_date=date(2026, 10, 22)
    )
    assert out["fundamental_gate_status"] == "FAIL"
    assert out["fundamental_damage_status"] == "DETECTED"
    assert out["eps_growth_yoy_pct"] == pytest.approx(-20.0)
    assert "EPS_YOY_GROWTH_NOT_POSITIVE" in out["fundamental_gate_reason"]


def test_nonpositive_revenue_growth_fails_gate():
    out = analyze_company_facts(
        "ABC", 123, "Example", _sec_facts(revenue_current=90, revenue_prior=100), as_of_date=date(2026, 10, 22)
    )
    assert out["fundamental_gate_status"] == "FAIL"
    assert out["revenue_growth_yoy_pct"] == pytest.approx(-10.0)
    assert "REVENUE_YOY_GROWTH_NOT_POSITIVE" in out["fundamental_gate_reason"]


def test_latest_quarter_loss_fails_but_loss_to_profit_is_review_not_fake_growth():
    loss = analyze_company_facts(
        "ABC", 123, "Example", _sec_facts(eps_current=-0.2), as_of_date=date(2026, 10, 22)
    )
    assert loss["fundamental_gate_status"] == "FAIL"
    assert loss["fundamental_damage_status"] == "DETECTED"

    turn_positive = analyze_company_facts(
        "ABC", 123, "Example", _sec_facts(eps_current=0.2, eps_prior=-0.1), as_of_date=date(2026, 10, 22)
    )
    assert turn_positive["fundamental_gate_status"] == "REVIEW"
    assert turn_positive["eps_growth_yoy_pct"] is None
    assert "PRIOR_YEAR_EPS_NON_POSITIVE" in turn_positive["fundamental_gate_reason"]


def test_stale_filing_never_passes_even_if_growth_is_positive():
    facts = _sec_facts(current_filed="2025-08-01", prior_filed="2024-08-01")
    for metric_rows in (
        facts["facts"]["us-gaap"]["EarningsPerShareDiluted"]["units"]["USD/shares"],
        facts["facts"]["us-gaap"]["RevenueFromContractWithCustomerExcludingAssessedTax"]["units"]["USD"],
    ):
        metric_rows[0].update({"start": "2025-04-01", "end": "2025-06-30"})
        metric_rows[1].update({"start": "2024-04-01", "end": "2024-06-30"})
    out = analyze_company_facts("ABC", 123, "Example", facts, as_of_date=date(2026, 10, 22))
    assert out["fundamental_gate_status"] == "REVIEW"
    assert out["fundamental_data_status"] == "STALE_REPORTED_FACTS"
    assert out["fundamental_gate_reason"] == "LATEST_SELECTED_QUARTERLY_FACTS_STALE"


def test_missing_company_facts_and_unsupported_ticker_fail_closed():
    result = assess_ticker_from_sec("^GSPC", {}, {}, as_of_date=date(2026, 10, 22))
    assert result["fundamental_gate_status"] == "REVIEW"
    assert result["fundamental_gate_reason"] == "UNSUPPORTED_OR_NON_COMPANY_TICKER"

    no_facts = analyze_company_facts("ETF", 10, "ETF", {"facts": {"us-gaap": {}}}, as_of_date=date(2026, 10, 22))
    assert no_facts["fundamental_gate_status"] == "REVIEW"
    assert no_facts["fundamental_data_status"] == "NO_COMPARABLE_XBRL_FACTS"


def test_only_standalone_quarter_durations_are_selected():
    facts = _sec_facts()
    rev = facts["facts"]["us-gaap"]["RevenueFromContractWithCustomerExcludingAssessedTax"]["units"]["USD"]
    rev.append({"start": "2026-01-01", "end": "2026-06-30", "val": 300, "filed": "2026-08-01", "form": "10-Q"})
    tag, extracted = extract_quarterly_facts(facts, ("RevenueFromContractWithCustomerExcludingAssessedTax",), "revenue", as_of_date=date(2026, 10, 22))
    assert tag == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert extracted[date(2026, 6, 30)].value == 120


def test_latest_unfiled_fact_after_asof_cutoff_is_not_used():
    facts = _sec_facts(current_filed="2026-11-01")
    out = analyze_company_facts("ABC", 123, "Example", facts, as_of_date=date(2026, 10, 22))
    assert out["fundamental_gate_status"] == "REVIEW"
    assert out["fundamental_data_status"] == "NO_COMPARABLE_XBRL_FACTS"


def test_latest_quarter_without_yoy_comparator_does_not_fall_back_to_older_quarter():
    facts = _sec_facts()
    eps = facts["facts"]["us-gaap"]["EarningsPerShareDiluted"]["units"]["USD/shares"]
    revenue = facts["facts"]["us-gaap"]["RevenueFromContractWithCustomerExcludingAssessedTax"]["units"]["USD"]
    eps.extend([
        {"start": "2026-07-01", "end": "2026-09-30", "val": 3.0, "filed": "2026-10-20", "form": "8-K"},
    ])
    revenue.extend([
        {"start": "2026-07-01", "end": "2026-09-30", "val": 140, "filed": "2026-10-20", "form": "8-K"},
    ])
    out = analyze_company_facts("ABC", 123, "Example", facts, as_of_date=date(2026, 10, 22))
    assert out["fundamental_gate_status"] == "REVIEW"
    assert out["fundamental_data_status"] == "NO_COMPARABLE_QUARTER_PAIR"


def test_one_stale_required_fact_cannot_be_masked_by_the_other_recent_filing():
    facts = _sec_facts(current_filed="2026-04-01", prior_filed="2024-08-01")
    for metric_rows in (
        facts["facts"]["us-gaap"]["EarningsPerShareDiluted"]["units"]["USD/shares"],
        facts["facts"]["us-gaap"]["RevenueFromContractWithCustomerExcludingAssessedTax"]["units"]["USD"],
    ):
        metric_rows[0].update({"start": "2025-04-01", "end": "2025-06-30"})
        metric_rows[1].update({"start": "2024-04-01", "end": "2024-06-30", "filed": "2024-08-01"})
    revenue = facts["facts"]["us-gaap"]["RevenueFromContractWithCustomerExcludingAssessedTax"]["units"]["USD"]
    revenue[0]["filed"] = "2025-08-01"
    out = analyze_company_facts("ABC", 123, "Example", facts, as_of_date=date(2026, 10, 22))
    assert out["fundamental_gate_status"] == "REVIEW"
    assert out["fundamental_report_age_days"] > 180
    assert out["latest_filing_date"] == "2026-04-01"
    assert out["oldest_required_filing_date"] == "2025-08-01"
