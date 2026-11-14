"""ops - read-only recon, event-driven seat strategy, fingerprints and phases 2-3.

Import as a package or run each file directly; no third-party dependencies.
fingerprint_bridge only imports mitmproxy lazily (it is never required here).
signature performs static analysis only - it never executes a hook or a request.
orchestrator never performs a write itself; act hands an explicit single-fire
order to a caller-supplied executor. race_probe is the only multi-shot path
and stays disarmed unless armed=True is passed explicitly.
"""
from . import (recon, seat_events, p5_probe,  # noqa: F401
               fingerprint, fingerprint_bridge,
               endpoints, preflight, crosscheck, signature,
               orchestrator, adapter, race_probe, offering_bridge)

__all__ = ["recon", "seat_events", "p5_probe", "fingerprint",
           "fingerprint_bridge", "endpoints", "preflight",
           "crosscheck", "signature", "orchestrator", "adapter",
           "race_probe", "offering_bridge"]
__version__ = "2.1.0"
