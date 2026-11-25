"""Check of the PBX REST address."""
from urllib.parse import urlsplit


def url_ist_https(url):
    """Return True if ``url`` is an https URL with a host, without credentials or blanks."""
    url = (url or '').strip()
    if not url or any(zeichen.isspace() for zeichen in url):
        return False
    try:
        teile = urlsplit(url)
        host = teile.hostname
        teile.port  # noqa: B018 - wirft ValueError bei ungültigem Port
    except ValueError:
        return False
    return teile.scheme.lower() == 'https' and bool(host) and not (teile.username or teile.password)
