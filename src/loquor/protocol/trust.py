"""Trust levels (AMP §9).

0 ``HEARD``      — a valid signature, nothing more
1 ``VERIFIED``   — public key confirmed out of band (QR code, typed fingerprint)
2 ``PROXIMATE``  — distance-bounded by UWB

A capability declares the minimum level it requires; the agent enforces it
at dispatch. The stores that establish levels 1 and 2 are interfaces
(:mod:`loquor.extensions.trust`); the protocol only carries the number.
"""

from __future__ import annotations

from enum import IntEnum


class Trust(IntEnum):
    HEARD = 0
    VERIFIED = 1
    PROXIMATE = 2
