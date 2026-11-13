"""ops - read-only recon, event-driven seat strategy, fingerprints and phase 2.

Import as a package or run each file directly; no third-party dependencies.
fingerprint_bridge only imports mitmproxy lazily (it is never required here).
signature performs static analysis only - it never executes a hook or a request.
"""
from . import (recon, seat_events, p5_probe,  # noqa: F401
               fingerprint, fingerprint_bridge,
               endpoints, preflight, crosscheck, signature)

__all__ = ["recon", "seat_events", "p5_probe", "fingerprint",
           "fingerprint_bridge", "endpoints", "preflight",
           "crosscheck", "signature"]
__version__ = "2.0.0"
