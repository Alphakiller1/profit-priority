"""Every price is shown in American; nothing that isn't a price ever is."""

from __future__ import annotations


from profit_priority.fees import fmt_american, prob_to_american


def test_standard_lines_convert_exactly() -> None:
    assert prob_to_american(0.5238) == -110
    assert prob_to_american(0.5) == -100
    assert prob_to_american(0.4762) == 110


def test_favourites_are_negative_and_dogs_positive() -> None:
    assert prob_to_american(0.75) < 0
    assert prob_to_american(0.25) > 0


def test_a_partition_sum_is_not_a_price_and_gets_no_odds() -> None:
    """sum(ask) across 30 contracts is 12.86 -- not a probability.

    Converting it would emit an authoritative-looking number that means nothing,
    which is why the dashboard leaves partition sums unconverted.
    """
    assert prob_to_american(12.86) is None
    assert fmt_american(12.86) == "-"


def test_zero_and_certain_prices_have_no_american_equivalent() -> None:
    """You cannot quote odds on something nobody will pay for."""
    for p in (0.0, 1.0, -0.2, None):
        assert prob_to_american(p) is None
        assert fmt_american(p) == "-"


def test_placeholder_is_ascii_so_consoles_can_render_it() -> None:
    """An en-dash renders as a replacement char in every cp1252 CLI report."""
    out = fmt_american(None)
    assert out == "-"
    out.encode("cp1252")            # must not raise


def test_sign_is_always_explicit() -> None:
    assert fmt_american(0.25).startswith("+")
    assert fmt_american(0.75).startswith("-")
