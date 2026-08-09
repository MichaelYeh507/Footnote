"""Wilson score interval for a binomial proportion.

Chosen over the normal approximation because the normal interval is badly
behaved exactly where this project lands: small n, and proportions near 0 or 1.
At 9 correct out of 9 the normal interval is [1.0, 1.0], which asserts certainty
from nine observations. Wilson gives [0.70, 1.00], which is the honest answer.

Reference: Wilson, E. B. (1927), "Probable inference, the law of succession, and
statistical inference", JASA 22(158).
"""

import math

# Two-sided normal quantiles. A table rather than an inverse-CDF implementation:
# three levels cover every use here, and a hand-rolled inverse normal is one
# more place for a numerical error to hide inside a published number.
_Z = {
    0.90: 1.6448536269514722,
    0.95: 1.9599639845400545,
    0.99: 2.5758293035489004,
}


def z_for(confidence: float) -> float:
    try:
        return _Z[confidence]
    except KeyError:
        raise ValueError(
            f"confidence must be one of {sorted(_Z)}, got {confidence!r}"
        ) from None


def wilson_interval(
    successes: int, trials: int, confidence: float = 0.95
) -> tuple[float, float]:
    """Return (lower, upper) for `successes` out of `trials`.

    Raises on an empty or impossible denominator rather than returning a
    default. A field with no labeled instances must not be able to render as a
    measurement.
    """
    if trials <= 0:
        raise ValueError("trials must be positive; an empty denominator is not a rate")
    if successes < 0:
        raise ValueError(f"successes must be non-negative, got {successes}")
    if successes > trials:
        raise ValueError(f"successes ({successes}) exceeds trials ({trials})")

    z = z_for(confidence)
    n = float(trials)
    p = successes / n
    z2 = z * z

    denominator = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denominator
    half_width = (z / denominator) * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n))

    return (max(0.0, center - half_width), min(1.0, center + half_width))
