"""Point Python's default HTTPS verification at certifi's CA bundle.

The python.org macOS framework build ships with no system CA bundle
(``ssl.get_default_verify_paths().cafile`` is ``None``). Any bare urllib/ssl
call to an external HTTPS host then fails with::

    [SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate

That is exactly what broke Google token exchange/refresh/revoke in
``services.google_auth`` (which use ``urllib.request.urlopen``), so every Gmail
and Calendar reconnect returned ``token_exchange_failed`` and the connection
looped forever. Google's own client libraries (used by gmail.py / calendar.py)
bundle their own certificates and were unaffected; only the raw token calls
broke.

certifi is already installed in the backend venv, so we set the process-wide
default HTTPS context factory to use it. This fixes every current and future
bare-urllib call at the root instead of patching each call site.
"""
from __future__ import annotations

import ssl


def install_certifi_default() -> bool:
    """Make urllib's default HTTPS context verify against certifi's CA bundle.

    Idempotent and safe to call from several import sites. Returns ``True`` if
    the default was installed, ``False`` if certifi is unavailable (in which
    case the stdlib default is left untouched so the backend still boots).
    """
    try:
        import certifi
    except ImportError:
        return False

    ca_bundle = certifi.where()

    def _default_https_context(*_args, **_kwargs) -> ssl.SSLContext:
        return ssl.create_default_context(cafile=ca_bundle)

    # urllib calls ssl._create_default_https_context() with no args when no
    # explicit context is passed to urlopen; overriding it covers every
    # bare-urllib HTTPS call in the process.
    ssl._create_default_https_context = _default_https_context  # type: ignore[attr-defined]
    return True
