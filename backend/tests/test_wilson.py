"""Wilson score interval, checked against hand-computed values.

Plan §6 puts this first in the testing order. Every published number in this
project is a proportion plus this interval, so an error here is not a bug that
produces a crash -- it produces a confidently wrong number that looks fine.

The three anchor cases below are standard published Wilson 95% intervals. They
are asserted to 5 decimal places against values not derived from this
implementation, which is the point: a test written by running the code and
pasting the output proves only that the code is consistent with itself.
"""

import pytest

from evaluation.wilson import wilson_interval

ABS = 5e-5


def test_zero_successes_ten_trials():
    """Published Wilson 95% for 0/10 is (0.00000, 0.27753). Note the upper bound
    is far from zero -- 'we saw no failures' is not 'the rate is zero', which is
    the entire reason for using an interval."""
    lo, hi = wilson_interval(0, 10)
    assert lo == pytest.approx(0.0, abs=ABS)
    assert hi == pytest.approx(0.27753, abs=ABS)


def test_half_successes_ten_trials():
    """Published Wilson 95% for 5/10 is (0.23661, 0.76339)."""
    lo, hi = wilson_interval(5, 10)
    assert lo == pytest.approx(0.23661, abs=ABS)
    assert hi == pytest.approx(0.76339, abs=ABS)


def test_all_successes_ten_trials():
    """Published Wilson 95% for 10/10 is (0.72247, 1.00000). A perfect score on
    ten trials still admits a true rate below three-quarters."""
    lo, hi = wilson_interval(10, 10)
    assert lo == pytest.approx(0.72247, abs=ABS)
    assert hi == pytest.approx(1.0, abs=ABS)


def test_interval_is_symmetric_under_success_failure_swap():
    """wilson(k, n) must mirror wilson(n-k, n) about 0.5."""
    for k, n in ((3, 17), (8, 40), (1, 5)):
        lo_a, hi_a = wilson_interval(k, n)
        lo_b, hi_b = wilson_interval(n - k, n)
        assert lo_a == pytest.approx(1 - hi_b, abs=1e-12)
        assert hi_a == pytest.approx(1 - lo_b, abs=1e-12)


@pytest.mark.parametrize("k,n", [(0, 1), (1, 1), (0, 44), (40, 44), (17, 39), (39, 39)])
def test_bounds_stay_inside_zero_and_one(k, n):
    lo, hi = wilson_interval(k, n)
    assert 0.0 <= lo <= hi <= 1.0


@pytest.mark.parametrize("k,n", [(1, 5), (17, 39), (40, 44), (3, 8)])
def test_interval_contains_the_point_estimate(k, n):
    """Wilson is asymmetric about p-hat, but must still cover it."""
    lo, hi = wilson_interval(k, n)
    assert lo <= k / n <= hi


def test_a_less_than_perfect_score_excludes_certainty():
    """39 of 44 correct must not yield an upper bound of 1.0."""
    lo, hi = wilson_interval(40, 44)
    assert hi < 1.0
    assert lo > 0.5


def test_smaller_samples_give_wider_intervals():
    """The whole reason §3 gates per-field claims below n=25."""
    narrow = wilson_interval(90, 100)
    wide = wilson_interval(9, 10)
    assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])


def test_interval_narrows_monotonically_with_n_at_fixed_rate():
    widths = [wilson_interval(int(0.9 * n), n) for n in (10, 40, 100, 400)]
    spans = [hi - lo for lo, hi in widths]
    assert spans == sorted(spans, reverse=True)


def test_zero_trials_is_rejected_not_silently_zero():
    """An empty denominator must fail loudly. Returning (0, 1) or 0.0 would let
    an unlabeled field render as a real measurement."""
    with pytest.raises(ValueError):
        wilson_interval(0, 0)


def test_successes_cannot_exceed_trials():
    with pytest.raises(ValueError):
        wilson_interval(5, 4)


def test_negative_inputs_are_rejected():
    with pytest.raises(ValueError):
        wilson_interval(-1, 10)


def test_confidence_level_is_configurable_and_wider_is_wider():
    lo95, hi95 = wilson_interval(30, 40, confidence=0.95)
    lo99, hi99 = wilson_interval(30, 40, confidence=0.99)
    assert (hi99 - lo99) > (hi95 - lo95)
