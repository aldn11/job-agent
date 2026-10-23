"""Optional local filters for job records parsed from RSS feeds."""
import re


def _values(value):
    if isinstance(value, str):
        values = value.split(";")
    elif isinstance(value, (list, tuple, set, frozenset)):
        values = (
            normalized
            for item in value
            for normalized in _values(item)
        )
    else:
        values = () if value is None else (value,)
    return tuple(
        normalized
        for item in values
        if (normalized := re.sub(r"\s+", " ", str(item)).strip().casefold())
    )


def _text(value):
    return " ".join(_values(value))


def filter_jobs(
    jobs,
    *,
    include_terms="",
    exclude_terms="",
    include_categories="",
    include_locations="",
):
    """Apply optional case-insensitive RSS metadata filters.

    Values within one option use OR semantics. Different configured options
    are combined with AND. Empty options do not filter the feed.
    """
    include_terms = _values(include_terms)
    exclude_terms = _values(exclude_terms)
    include_categories = _values(include_categories)
    include_locations = _values(include_locations)
    filtered = []

    for job in jobs:
        searchable = _text(
            (
                job.get("title", ""),
                job.get("description", ""),
                job.get("job_type", ""),
                job.get("category", ""),
                job.get("categories", ()),
            )
        )
        category_values = {
            normalized
            for normalized in _values(
                (job.get("category", ""), job.get("categories", ()))
            )
        }
        location = _text(job.get("location", ""))

        if include_terms and not any(term in searchable for term in include_terms):
            continue
        if any(term in searchable for term in exclude_terms):
            continue
        if include_categories and not any(
            expected == available
            for expected in include_categories
            for available in category_values
        ):
            continue
        if include_locations and not any(
            expected in location for expected in include_locations
        ):
            continue
        filtered.append(job)

    return filtered
