"""TLS trust for outbound HTTPS — certifi bundle first, OS store as fallback.

Python's default SSL context trusts whatever the operating system's
certificate store holds. On Windows that store is refreshed lazily, so a
machine can be missing a root that entered circulation recently — notably
ISRG Root X2, which Let's Encrypt's ECDSA chain started routing through in
May 2026. pixeldrain.com and the bypass CDN both sit on that chain:

    leaf → Let's Encrypt YE1 → ISRG Root YE → ISRG Root X2 → ISRG Root X1

A store that has X1 but not X2 has to walk one hop further than it can. When
OpenSSL runs out of path it doesn't report "missing link" — it reports
whatever went wrong on the dead end it tried last, which surfaces as the
thoroughly misleading:

    <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify
    failed: certificate has expired (_ssl.c:1010)>

Nothing is expired. The root is simply absent. Bundling certifi (Mozilla's
list, which has carried ISRG Root X2 since 2021) fixes it without depending
on the user's Windows being current.

We keep the OS store as a fallback rather than replacing it: users behind
corporate TLS-inspecting proxies or HTTPS-scanning antivirus (Kaspersky,
ESET, some Bitdefender setups) are signed by a root that lives in the OS
store and will never be in certifi. Certifi-only would fix the many and
break the few.
"""

from __future__ import annotations

import ssl
import threading
import urllib.request

_lock = threading.Lock()
_contexts: list[ssl.SSLContext] | None = None

# Index into _contexts() of the last context that verified successfully.
# Sticky so the common case costs one handshake, not two. A stale value is
# harmless — worst case one wasted attempt before we cycle to the other.
_preferred = 0


def contexts() -> list[ssl.SSLContext]:
    """Verification contexts to try, in order. Built once, then cached."""
    global _contexts
    if _contexts is None:
        with _lock:
            if _contexts is None:
                built = []
                try:
                    import certifi
                    built.append(
                        ssl.create_default_context(cafile=certifi.where()))
                except Exception:  # noqa: BLE001
                    # certifi absent (source checkout with no deps installed).
                    # The OS store alone still works for most users.
                    pass
                built.append(ssl.create_default_context())
                _contexts = built
    return _contexts


def is_cert_error(exc: BaseException) -> bool:
    """True for a certificate-verification failure, raw or wrapped in the
    URLError that urlopen puts around it."""
    if isinstance(exc, ssl.SSLCertVerificationError):
        return True
    return isinstance(getattr(exc, "reason", None),
                      ssl.SSLCertVerificationError)


def urlopen(req, *, timeout: float):
    """urllib.request.urlopen with our trust chain.

    Only certificate-verification failures fall through to the next context.
    HTTP errors, timeouts and connection resets propagate untouched, so
    callers keep their existing retry and CDN-fallback behaviour.
    """
    global _preferred
    ctxs = contexts()
    order = list(range(_preferred, len(ctxs))) + list(range(0, _preferred))
    last: BaseException | None = None
    for i in order:
        try:
            r = urllib.request.urlopen(req, timeout=timeout, context=ctxs[i])
        except Exception as e:  # noqa: BLE001
            if not is_cert_error(e):
                raise
            last = e
            continue
        _preferred = i
        return r
    raise last
