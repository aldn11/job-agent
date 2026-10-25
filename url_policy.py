"""URL allowlist and redirect validation for supported job sources."""
from urllib.parse import urljoin, urlsplit
import urllib.request


ALLOWED_JOB_DOMAINS = (
    "linkedin.com",
    "weworkremotely.com",
    "workremot.com",
    "jobspresso.co",
    "remotive.com",
)


def validate_https_url(url):
    """Validate an HTTPS job link without imposing a source-domain allowlist."""
    try:
        parts = urlsplit(url)
        hostname = (parts.hostname or "").rstrip(".").lower()
        port = parts.port
    except (AttributeError, TypeError, ValueError):
        raise ValueError("URL inválida.") from None

    if (
        parts.scheme.lower() != "https"
        or not hostname
        or parts.username is not None
        or parts.password is not None
        or port not in (None, 443)
        or any(character.isspace() for character in url)
    ):
        raise ValueError("URL inválida: informe um endereço HTTPS válido.")
    return url


def validate_allowed_url(url, allowed_domains=ALLOWED_JOB_DOMAINS):
    try:
        parts = urlsplit(url)
        hostname = (parts.hostname or "").rstrip(".").lower()
    except (AttributeError, TypeError, ValueError):
        raise ValueError("URL inválida.") from None

    validate_https_url(url)
    if not any(
        hostname == domain or hostname.endswith("." + domain)
        for domain in allowed_domains
    ):
        raise ValueError("URL fora da lista permitida.")
    return url


class _AllowedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_domains):
        super().__init__()
        self.allowed_domains = allowed_domains

    def redirect_request(self, request, response, code, message, headers, new_url):
        target_url = urljoin(request.full_url, new_url)
        validate_allowed_url(target_url, self.allowed_domains)
        return super().redirect_request(
            request, response, code, message, headers, target_url
        )


def open_allowed_url(request, timeout, allowed_domains=ALLOWED_JOB_DOMAINS):
    validate_allowed_url(request.full_url, allowed_domains)
    opener = urllib.request.build_opener(
        _AllowedRedirectHandler(allowed_domains)
    )
    return opener.open(request, timeout=timeout)
