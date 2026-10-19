#!/usr/bin/env python3
"""Apply the private location policy to evidence extracted from job text."""
import os
import re


def _configured_terms(variable):
    return [
        item.strip().casefold()
        for item in os.getenv(variable, "").split(";")
        if item.strip()
    ]


def _normalize(text):
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


def _evidence_is_in_text(evidence, job_text):
    normalized_evidence = _normalize(evidence)
    normalized_text = _normalize(job_text)
    return bool(normalized_evidence and normalized_evidence in normalized_text)


def _contains_any(text, terms):
    normalized_text = _normalize(text)
    return any(
        re.search(rf"(?<!\w){re.escape(_normalize(term))}(?!\w)", normalized_text)
        for term in terms
    )


def classify_location(evaluation, job_text):
    """Return ELIGIBLE, INELIGIBLE, or UNCERTAIN using quoted job evidence."""
    mode = evaluation.get("work_mode")
    evidence = evaluation.get("location_evidence", "")
    location_text = evaluation.get("location_text", "")

    if not _evidence_is_in_text(evidence, job_text):
        return "UNCERTAIN"
    if location_text and _normalize(location_text) not in _normalize(evidence):
        return "UNCERTAIN"

    allowed_remote_countries = _configured_terms("ALLOWED_REMOTE_COUNTRIES")
    allowed_onsite_locations = _configured_terms("ALLOWED_ONSITE_LOCATIONS")
    quoted_text = evidence

    if mode == "REMOTE_OPEN":
        if _contains_any(quoted_text, allowed_remote_countries):
            return "ELIGIBLE"
        if re.search(r"\b(worldwide|anywhere in the world|work from anywhere)\b", quoted_text, re.I):
            return "ELIGIBLE"
        return "UNCERTAIN"

    if mode == "REMOTE_RESTRICTED":
        if not location_text:
            return "UNCERTAIN"
        if _contains_any(quoted_text, allowed_remote_countries):
            return "UNCERTAIN"
        return "INELIGIBLE"

    if mode == "ONSITE_ALLOWED":
        if _contains_any(quoted_text, allowed_onsite_locations):
            return "ELIGIBLE"
        return "UNCERTAIN"

    if mode == "ONSITE_OTHER":
        if not location_text:
            return "UNCERTAIN"
        if _contains_any(quoted_text, allowed_onsite_locations):
            return "UNCERTAIN"
        return "INELIGIBLE"

    return "UNCERTAIN"
