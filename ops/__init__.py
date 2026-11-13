"""ops - read-only reconnaissance and event-driven seat strategy modules.

Import as a package or run each file directly; no third-party dependencies.
"""
from . import recon, seat_events, p5_probe  # noqa: F401

__all__ = ["recon", "seat_events", "p5_probe"]
__version__ = "1.0.0"
