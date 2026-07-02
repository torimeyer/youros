"""Regression tests for the certifi SSL bootstrap.

Root cause of the Gmail/Calendar reconnect loop: the framework Python's default
HTTPS context had zero CA certs loaded (cafile was None), so urllib calls to
Google's token endpoint failed CERTIFICATE_VERIFY_FAILED. install_certifi_default
must leave the default context able to verify real public roots.
"""
import ssl

from services.ssl_bootstrap import install_certifi_default


def test_install_returns_true_when_certifi_present():
    assert install_certifi_default() is True


def test_default_https_context_has_ca_certs_loaded():
    install_certifi_default()
    ctx = ssl._create_default_https_context()
    # The framework-build stdlib default loads zero roots here; certifi loads
    # the full public bundle. Non-empty proves the bundle is actually wired in.
    assert len(ctx.get_ca_certs()) > 0


def test_default_context_verifies_mode_required():
    install_certifi_default()
    ctx = ssl._create_default_https_context()
    # Must still verify (not an insecure/unverified context that would trade the
    # cert error for a silent security hole).
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
