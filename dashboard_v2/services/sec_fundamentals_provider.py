"""SEC EDGAR company-facts provider and conservative quarterly growth gate.

Only reported quarterly XBRL facts are used. Missing, stale, ambiguous, or
non-comparable data cannot pass the gate. This module does not infer estimates
from price data and never writes to a database or changes technical signals.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import math
import threading
import time
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
DEFAULT_TIMEOUT_SECONDS = 15
MAX_REPORT_AGE_DAYS = 180
ALLOWED_FORMS = {"10-Q", "10-K", "20-F", "40-F", "6-K", "8-K"}

REVENUE_TAGS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
)
EPS_TAGS = (
    "EarningsPerShareDiluted",
    "EarningsPerShareBasic",
    "EarningsPerShareBasicAndDiluted",
)

_request_lock = threading.Lock()
_last_request_started = 0.0
MIN_REQUEST_INTERVAL_SECONDS = 0.12  # keep well below SEC's 10 requests/sec ceiling


class SECFundamentalsError(RuntimeError):
    """A safe, user-displayable SEC provider error."""


def valid_sec_user_agent(value: str | None) -> bool:
    """SEC automated access needs an identifiable app plus a real contact email."""
    text = str(value or "").strip()
    if len(text) < 8 or "@" not in text:
        return False
    lowered = text.lower()
    if any(token in lowered for token in ("your-email", "your-real-email", "yourdomain.com", "your_domain", "example.com", "replace-me", "your name")):
        return False
    return True


def fetch_sec_json(
    url: str,
    user_agent: str,
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Fetch one SEC JSON endpoint with an identified User-Agent and light throttling."""
    if not valid_sec_user_agent(user_agent):
        raise SECFundamentalsError("SEC_USER_AGENT_NOT_CONFIGURED")
    if not (url.startswith("https://www.sec.gov/") or url.startswith("https://data.sec.gov/")):
        raise SECFundamentalsError("SEC_ENDPOINT_NOT_ALLOWED")

    global _last_request_started
    with _request_lock:
        wait_for = MIN_REQUEST_INTERVAL_SECONDS - (time.monotonic() - _last_request_started)
        if wait_for > 0:
            time.sleep(wait_for)
        _last_request_started = time.monotonic()

    request = Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/json",
        },
        method="GET",
    )
    open_fn = opener or urlopen
    try:
        with open_fn(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code in (403, 429):
            raise SECFundamentalsError(f"SEC_ACCESS_LIMIT_OR_DENIED_HTTP_{exc.code}") from None
        raise SECFundamentalsError(f"SEC_HTTP_{exc.code}") from None
    except (URLError, TimeoutError, OSError) as exc:
        raise SECFundamentalsError(f"SEC_NETWORK_ERROR:{type(exc).__name__}") from None
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise SECFundamentalsError("SEC_INVALID_JSON_RESPONSE") from None
    if not isinstance(payload, dict):
        raise SECFundamentalsError("SEC_JSON_ROOT_NOT_OBJECT")
    return payload


def load_ticker_cik_map(user_agent: str) -> dict[str, list[dict[str, Any]]]:
    """Return normalized ticker -> candidate CIK/company rows.

    Duplicate mappings are preserved, not arbitrarily resolved. The caller will
    mark an ambiguous ticker as REVIEW rather than guessing which filer it is.
    """
    payload = fetch_sec_json(TICKER_MAP_URL, user_agent)
    values = payload.values() if isinstance(payload, dict) else payload
    result: dict[str, list[dict[str, Any]]] = {}
    for row in values:
        if not isinstance(row, Mapping):
            continue
        ticker = _normalize_ticker(row.get("ticker"))
        cik = row.get("cik_str", row.get("cik"))
        title = str(row.get("title", "")).strip()
        try:
            cik_number = int(cik)
        except (TypeError, ValueError, OverflowError):
            continue
        if ticker and cik_number > 0:
            result.setdefault(ticker, []).append({"cik": cik_number, "title": title})
    return result


def _normalize_ticker(ticker: Any) -> str:
    value = str(ticker or "").strip().upper()
    # SEC and common quote vendors differ on share-class punctuation.
    return value.replace(".", "-")


def resolve_ticker_cik(ticker: str, ticker_map: Mapping[str, list[dict[str, Any]]]) -> tuple[int | None, str | None, str | None]:
    key = _normalize_ticker(ticker)
    if not key or key.startswith("^") or not key.replace("-", "").isalnum():
        return None, None, "UNSUPPORTED_OR_NON_COMPANY_TICKER"
    candidates = ticker_map.get(key, [])
    distinct: dict[int, str] = {}
    for item in candidates:
        try:
            cik = int(item["cik"])
        except (KeyError, TypeError, ValueError):
            continue
        distinct[cik] = str(item.get("title", "")).strip()
    if not distinct:
        return None, None, "TICKER_NOT_FOUND_IN_SEC_MAP"
    if len(distinct) > 1:
        return None, None, "AMBIGUOUS_TICKER_CIK_MAPPING"
    cik, title = next(iter(distinct.items()))
    return cik, title, None


@dataclass(frozen=True)
class QuarterlyFact:
    start: date
    end: date
    value: float
    filed: date
    form: str
    tag: str
    unit: str

    @property
    def duration_days(self) -> int:
        return (self.end - self.start).days + 1


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _is_allowed_form(form: Any) -> bool:
    value = str(form or "").upper().strip()
    if value.endswith("/A"):
        value = value[:-2]
    return value in ALLOWED_FORMS


def _relevant_units(fact: Mapping[str, Any], metric: str) -> list[tuple[str, list[dict[str, Any]]]]:
    units = fact.get("units", {})
    if not isinstance(units, Mapping):
        return []
    output = []
    for unit, records in units.items():
        unit_text = str(unit).lower().replace(" ", "")
        if metric == "revenue":
            okay = unit_text == "usd"
        else:
            okay = ("usd/" in unit_text and "share" in unit_text) or ("usd-per-share" in unit_text)
        if okay and isinstance(records, list):
            output.append((str(unit), records))
    return output


def extract_quarterly_facts(
    company_facts: Mapping[str, Any],
    tag_names: tuple[str, ...],
    metric: str,
    *,
    as_of_date: date | None = None,
) -> tuple[str | None, dict[date, QuarterlyFact]]:
    """Extract standalone quarterly facts, excluding YTD/annual durations.

    A quarter duration must be 60–125 days. Duplicate facts for the same end
    date are resolved using duration closest to 91 days, then latest filing.
    """
    facts_root = company_facts.get("facts", {})
    if not isinstance(facts_root, Mapping):
        return None, {}
    taxonomy = facts_root.get("us-gaap", {})
    if not isinstance(taxonomy, Mapping):
        return None, {}
    cutoff = as_of_date or date.today()

    for tag in tag_names:
        fact = taxonomy.get(tag)
        if not isinstance(fact, Mapping):
            continue
        candidates: dict[date, QuarterlyFact] = {}
        for unit, records in _relevant_units(fact, metric):
            for row in records:
                if not isinstance(row, Mapping) or not _is_allowed_form(row.get("form")):
                    continue
                start, end = _parse_date(row.get("start")), _parse_date(row.get("end"))
                filed = _parse_date(row.get("filed"))
                if not start or not end or not filed or end < start or filed < end or filed > cutoff:
                    continue
                duration = (end - start).days + 1
                if not 60 <= duration <= 125:
                    continue
                try:
                    value = float(row.get("val"))
                except (TypeError, ValueError, OverflowError):
                    continue
                if not math.isfinite(value):
                    continue
                candidate = QuarterlyFact(start, end, value, filed, str(row.get("form", "")), tag, unit)
                prior = candidates.get(end)
                if prior is None:
                    candidates[end] = candidate
                else:
                    old_score = (abs(prior.duration_days - 91), -prior.filed.toordinal())
                    new_score = (abs(candidate.duration_days - 91), -candidate.filed.toordinal())
                    if new_score < old_score:
                        candidates[end] = candidate
        if len(candidates) >= 2:
            return tag, candidates
    return None, {}


def _select_latest_comparable_pair(
    eps: Mapping[date, QuarterlyFact], revenue: Mapping[date, QuarterlyFact]
) -> tuple[date, date] | None:
    common = sorted(set(eps).intersection(revenue))
    if not common:
        return None
    # Never silently fall back to an older quarter just because the latest
    # common quarter lacks a prior-year comparator (e.g. a recently listed issuer).
    current_end = common[-1]
    prior_candidates = [
        old_end for old_end in common
        if 330 <= (current_end - old_end).days <= 400
    ]
    if not prior_candidates:
        return None
    prior_end = min(prior_candidates, key=lambda old: abs((current_end - old).days - 364))
    return current_end, prior_end


def empty_fundamental_result(ticker: str, reason: str, *, status: str = "REVIEW") -> dict[str, Any]:
    """Standard fail-closed row to keep technical results visible without a false pass."""
    return {
        "fundamental_ticker": str(ticker or "").strip().upper(),
        "fundamental_gate_status": status,
        "fundamental_gate_reason": reason,
        "fundamental_data_status": "UNAVAILABLE",
        "fundamental_damage_status": "REVIEW",
        "eps_current_quarter": None,
        "eps_prior_year_quarter": None,
        "eps_growth_yoy_pct": None,
        "revenue_current_quarter": None,
        "revenue_prior_year_quarter": None,
        "revenue_growth_yoy_pct": None,
        "latest_fiscal_quarter_end": None,
        "latest_filing_date": None,
        "sec_cik": None,
        "sec_company_name": None,
        "eps_fact_tag": None,
        "revenue_fact_tag": None,
        "eps_component_status": "REVIEW",
        "revenue_component_status": "REVIEW",
        "fundamental_provenance": "SEC_EDGAR_COMPANYFACTS",
    }


def analyze_company_facts(
    ticker: str,
    cik: int,
    company_name: str,
    company_facts: Mapping[str, Any],
    *,
    as_of_date: date | None = None,
    max_report_age_days: int = MAX_REPORT_AGE_DAYS,
) -> dict[str, Any]:
    """Produce a conservative EPS/revenue YoY gate using reported SEC XBRL data.

    PASS requires positive, comparable YoY growth in both latest common reported
    quarters and positive reported EPS. FAIL requires a complete, definitive
    violation. Missing, stale, ambiguous, or non-comparable facts remain REVIEW.
    No custom growth-rate or acceleration threshold is invented.
    """
    symbol = str(ticker or "").strip().upper()
    cutoff = as_of_date or date.today()
    eps_tag, eps_facts = extract_quarterly_facts(company_facts, EPS_TAGS, "eps", as_of_date=cutoff)
    revenue_tag, revenue_facts = extract_quarterly_facts(company_facts, REVENUE_TAGS, "revenue", as_of_date=cutoff)
    if not eps_facts or not revenue_facts:
        return {
            **empty_fundamental_result(symbol, "QUARTERLY_EPS_OR_REVENUE_FACTS_NOT_FOUND"),
            "fundamental_data_status": "NO_COMPARABLE_XBRL_FACTS",
            "sec_cik": int(cik), "sec_company_name": company_name or None,
            "eps_fact_tag": eps_tag, "revenue_fact_tag": revenue_tag,
        }

    pair = _select_latest_comparable_pair(eps_facts, revenue_facts)
    if pair is None:
        return {
            **empty_fundamental_result(symbol, "NO_COMMON_YOY_COMPARABLE_QUARTERS"),
            "fundamental_data_status": "NO_COMPARABLE_QUARTER_PAIR",
            "sec_cik": int(cik), "sec_company_name": company_name or None,
            "eps_fact_tag": eps_tag, "revenue_fact_tag": revenue_tag,
        }

    current_end, prior_end = pair
    eps_current, eps_prior = eps_facts[current_end], eps_facts[prior_end]
    revenue_current, revenue_prior = revenue_facts[current_end], revenue_facts[prior_end]
    filing_date = max(eps_current.filed, revenue_current.filed)
    oldest_required_filing_date = min(eps_current.filed, revenue_current.filed)
    # Both required current-period facts must be fresh. One recently filed
    # metric must not mask stale filing evidence for the other metric.
    freshness_days = (cutoff - oldest_required_filing_date).days
    if freshness_days < 0:
        freshness_days = 0

    base = {
        "fundamental_ticker": symbol,
        "fundamental_gate_status": "REVIEW",
        "fundamental_gate_reason": "INSUFFICIENT_COMPARABLE_DATA",
        "fundamental_data_status": "LOADED",
        "fundamental_damage_status": "REVIEW",
        "eps_current_quarter": eps_current.value,
        "eps_prior_year_quarter": eps_prior.value,
        "eps_growth_yoy_pct": None,
        "revenue_current_quarter": revenue_current.value,
        "revenue_prior_year_quarter": revenue_prior.value,
        "revenue_growth_yoy_pct": None,
        "latest_fiscal_quarter_end": current_end.isoformat(),
        "prior_fiscal_quarter_end": prior_end.isoformat(),
        "latest_filing_date": filing_date.isoformat(),
        "oldest_required_filing_date": oldest_required_filing_date.isoformat(),
        "fundamental_report_age_days": freshness_days,
        "sec_cik": int(cik),
        "sec_company_name": company_name or None,
        "eps_fact_tag": eps_tag,
        "revenue_fact_tag": revenue_tag,
        "eps_component_status": "REVIEW",
        "revenue_component_status": "REVIEW",
        "fundamental_provenance": "SEC_EDGAR_COMPANYFACTS_REPORTED_QUARTERLY_XBRL",
    }
    if freshness_days > max_report_age_days:
        base.update({
            "fundamental_gate_status": "REVIEW",
            "fundamental_gate_reason": "LATEST_SELECTED_QUARTERLY_FACTS_STALE",
            "fundamental_data_status": "STALE_REPORTED_FACTS",
        })
        return base

    # Revenue per-share growth is undefined for non-positive prior EPS. Do not
    # divide by a negative or near-zero comparison to manufacture a percentage.
    if eps_current.value <= 0:
        base["eps_component_status"] = "FAIL"
        base["fundamental_damage_status"] = "DETECTED"
        eps_fail_reason = "LATEST_QUARTER_EPS_NON_POSITIVE"
    elif eps_prior.value <= 0:
        base["eps_component_status"] = "REVIEW"
        eps_fail_reason = "PRIOR_YEAR_EPS_NON_POSITIVE_GROWTH_NOT_COMPARABLE"
    else:
        eps_growth = (eps_current.value / eps_prior.value - 1.0) * 100.0
        base["eps_growth_yoy_pct"] = eps_growth
        if eps_growth > 0:
            base["eps_component_status"] = "PASS"
        else:
            base["eps_component_status"] = "FAIL"
            base["fundamental_damage_status"] = "DETECTED"
            eps_fail_reason = "EPS_YOY_GROWTH_NOT_POSITIVE"

    if revenue_current.value <= 0 or revenue_prior.value <= 0:
        base["revenue_component_status"] = "REVIEW"
        revenue_fail_reason = "REVENUE_VALUES_NON_POSITIVE_OR_NOT_COMPARABLE"
    else:
        revenue_growth = (revenue_current.value / revenue_prior.value - 1.0) * 100.0
        base["revenue_growth_yoy_pct"] = revenue_growth
        if revenue_growth > 0:
            base["revenue_component_status"] = "PASS"
        else:
            base["revenue_component_status"] = "FAIL"
            base["fundamental_damage_status"] = "DETECTED"
            revenue_fail_reason = "REVENUE_YOY_GROWTH_NOT_POSITIVE"

    # Gate outcomes prioritize definitive fail conditions. Incomplete EPS (for
    # example prior-period losses) or an unavailable revenue comparison cannot pass.
    components = [base["eps_component_status"], base["revenue_component_status"]]
    if "FAIL" in components:
        base["fundamental_gate_status"] = "FAIL"
        reasons = []
        if base["eps_component_status"] == "FAIL":
            reasons.append(locals().get("eps_fail_reason", "EPS_REQUIREMENT_FAILED"))
        if base["revenue_component_status"] == "FAIL":
            reasons.append(locals().get("revenue_fail_reason", "REVENUE_REQUIREMENT_FAILED"))
        base["fundamental_gate_reason"] = "|".join(reasons)
        base["fundamental_damage_status"] = "DETECTED"
    elif components == ["PASS", "PASS"]:
        base["fundamental_gate_status"] = "PASS"
        base["fundamental_gate_reason"] = "EPS_AND_REVENUE_YOY_GROWTH_POSITIVE"
        base["fundamental_damage_status"] = "CLEAR"
    else:
        base["fundamental_gate_status"] = "REVIEW"
        reasons = []
        if base["eps_component_status"] == "REVIEW":
            reasons.append(locals().get("eps_fail_reason", "EPS_NOT_ASSESSABLE"))
        if base["revenue_component_status"] == "REVIEW":
            reasons.append(locals().get("revenue_fail_reason", "REVENUE_NOT_ASSESSABLE"))
        base["fundamental_gate_reason"] = "|".join(reasons) or "FUNDAMENTAL_DATA_REVIEW_REQUIRED"
        base["fundamental_damage_status"] = "REVIEW"
    return base


def assess_ticker_from_sec(
    ticker: str,
    ticker_map: Mapping[str, list[dict[str, Any]]],
    company_facts: Mapping[str, Any] | None = None,
    *,
    as_of_date: date | None = None,
) -> dict[str, Any]:
    """Resolve company identity and analyze an already-fetched Company Facts JSON."""
    cik, name, error = resolve_ticker_cik(ticker, ticker_map)
    if error:
        return empty_fundamental_result(ticker, error)
    if company_facts is None:
        return {
            **empty_fundamental_result(ticker, "SEC_COMPANY_FACTS_NOT_AVAILABLE"),
            "sec_cik": cik, "sec_company_name": name,
        }
    return analyze_company_facts(ticker, cik, name or "", company_facts, as_of_date=as_of_date)
