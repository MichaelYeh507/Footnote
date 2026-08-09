"""The matching spec, pre-registered in HYBRID-RETRIEVAL-SEC-PLAN.md §5.

The matcher decides the accuracy number, and unlike the corpus -- which a reader
can inspect -- nobody can audit a matcher they never saw an earlier version of.
These tests are the executable form of the pre-registration: changing a rule
means changing a test, which shows up in a diff.

Every case below is drawn from real filing variation observed during
calibration, not invented.
"""

import pytest

from evaluation.matching import matches


class TestCompanyName:
    @pytest.mark.parametrize("label,prediction", [
        ("Apple Inc.", "Apple Inc"),
        ("Dow Inc.", "DOW INC."),
        ("Amcor plc", "Amcor PLC"),
        ("PG&E Corporation", "PG&E Corp"),
        ("Marathon Petroleum Corp", "Marathon Petroleum Corporation"),
        ("PROGRESSIVE CORP/OH/", "Progressive Corp"),
    ])
    def test_corporate_suffix_and_case_do_not_matter(self, label, prediction):
        assert matches("company_name", label, prediction)

    @pytest.mark.parametrize("label,prediction", [
        ("PG&E Corporation", "Pacific Gas and Electric Company"),
        ("Kenvue Inc.", "Johnson & Johnson"),
    ])
    def test_a_different_registrant_is_not_a_match(self, label, prediction):
        """Combined filings list several registrants. Picking the wrong one is
        wrong, even though both names appear on the cover page."""
        assert not matches("company_name", label, prediction)


class TestTicker:
    def test_case_insensitive_exact(self):
        assert matches("ticker", "AAPL", "aapl")

    def test_whitespace_is_stripped(self):
        assert matches("ticker", "MPC", " MPC ")

    def test_a_different_security_is_not_a_match(self):
        assert not matches("ticker", "TRTN", "TRTN-PB")


class TestFiscalYearEnd:
    @pytest.mark.parametrize("prediction", [
        "December 31, 2025", "2025-12-31", "12/31/2025", "Dec 31, 2025",
    ])
    def test_date_formats_normalize(self, prediction):
        assert matches("fiscal_year_end", "December 31, 2025", prediction)

    def test_a_different_year_end_is_not_a_match(self):
        """Off-calendar fiscal years are common and the wrong one is wrong."""
        assert not matches("fiscal_year_end", "September 27, 2025", "December 31, 2025")

    def test_adjacent_year_ends_are_not_a_match(self):
        assert not matches("fiscal_year_end", "September 27, 2025", "September 28, 2024")


class TestEmployees:
    @pytest.mark.parametrize("label,prediction", [
        ("approximately 166,000 full-time equivalent employees", "166,000"),
        ("approximately 166,000 full-time equivalent employees", "approximately 166000"),
        ("118,000", "118,000 employees"),
    ])
    def test_compared_as_an_integer_discarding_qualifiers(self, label, prediction):
        assert matches("employees", label, prediction)

    def test_a_units_error_is_caught(self):
        """Exxon states headcount under a '(thousands)' header. A literal read
        gives 57.9, which is wrong by a factor of 1000 and must not pass."""
        assert not matches("employees", "57,900", "57.9")

    def test_a_different_count_is_not_a_match(self):
        assert not matches("employees", "166,000", "164,000")


class TestNumericFields:
    @pytest.mark.parametrize("field", [
        "total_assets", "revenue_most_recent_fy",
        "dividends_declared_per_share", "goodwill_impairment",
    ])
    def test_exact_values_match(self, field):
        assert matches(field, 391035.0, 391035.0)

    def test_rounding_from_unit_conversion_is_tolerated(self):
        """A filing reporting thousands converts to millions without landing on
        an exact float. 0.1% relative tolerance, per the pre-registered spec."""
        assert matches("total_assets", 391035.0, 391034.567)

    def test_tolerance_does_not_swallow_a_real_error(self):
        assert not matches("total_assets", 391035.0, 394000.0)

    def test_tolerance_is_relative_not_absolute(self):
        """0.5 on a per-share dividend is an enormous error; 0.5 on total assets
        in millions is rounding."""
        assert matches("total_assets", 391035.0, 391035.5)
        assert not matches("dividends_declared_per_share", 1.02, 1.52)

    def test_both_zero_matches(self):
        assert matches("goodwill_impairment", 0.0, 0.0)

    def test_zero_against_a_value_does_not_match(self):
        """Relative tolerance is undefined against zero; it must not divide by
        zero and must not silently pass."""
        assert not matches("goodwill_impairment", 0.0, 125.0)
        assert not matches("goodwill_impairment", 125.0, 0.0)

    def test_a_string_number_from_the_model_is_parsed(self):
        assert matches("total_assets", 391035.0, "391035")


class TestCeoName:
    @pytest.mark.parametrize("label,prediction", [
        ("Timothy D. Cook", "Tim Cook"),
        ("Timothy D. Cook", "Timothy Cook"),
        ("Ron M. Vachris", "Ron Vachris"),
        ("W. Rodney McMullen", "Rodney McMullen"),
    ])
    def test_surname_plus_first_initial(self, label, prediction):
        assert matches("ceo_name", label, prediction)

    def test_a_nickname_with_a_different_initial_fails(self):
        """Documented limitation of the pre-registered rule. Robert/Bob is a
        real miss, and name-field mismatches are reported separately so a
        reader can see whether they are substantive."""
        assert not matches("ceo_name", "Robert Smith", "Bob Smith")

    def test_a_different_officer_is_not_a_match(self):
        """Signature pages list many officers; picking the CFO is wrong."""
        assert not matches("ceo_name", "Timothy D. Cook", "Kevan Parekh")

    def test_suffixes_are_dropped(self):
        assert matches("ceo_name", "John Smith Jr.", "John Smith")

    def test_same_surname_different_person_fails(self):
        assert not matches("ceo_name", "James Murdoch", "Lachlan Murdoch")

    def test_same_given_name_different_surname_fails(self):
        """Guards the surname check itself. Every other negative case here also
        differs in first initial, so without this the surname comparison could
        be deleted entirely and the suite would stay green -- found by
        perturbing the matcher rather than by reading it."""
        assert not matches("ceo_name", "Timothy Cook", "Timothy Parekh")
        assert not matches("ceo_name", "Ron Vachris", "Ron Smith")


class TestNullHandling:
    def test_null_matches_null(self):
        assert matches("goodwill_impairment", None, None)

    def test_null_against_a_value_never_matches(self):
        assert not matches("goodwill_impairment", None, 0.0)
        assert not matches("goodwill_impairment", None, 125.0)
        assert not matches("total_assets", 391035.0, None)

    def test_empty_string_is_treated_as_null_not_as_a_value(self):
        """Models return '' rather than null often enough that conflating it
        with a real answer would corrupt the abstention counts."""
        assert matches("ceo_name", None, "")


def test_unknown_field_is_rejected():
    """A typo in a field name must not silently fall through to a default
    comparison that quietly scores everything as wrong."""
    with pytest.raises(KeyError):
        matches("total_asets", 1.0, 1.0)
