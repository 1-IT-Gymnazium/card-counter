#!/usr/bin/env python3
"""
Unit tests for the shared Hi-Lo counting logic (counting.py).

These also serve as a regression guard for the refactor that extracted the
domain logic out of the two UI overlays: the expected values below are the exact
behaviour the original inline CardCounter produced.

Run:  python3 -m pytest test_counting.py        (or)  python3 test_counting.py
"""

import counting
from counting import (CardCounter, card_badge_color, edge_color,
                      penetration_bar_color, display_rank, signed_prefix)


# ─── add_card / running count ──────────────────────────────────────────────────

def test_low_cards_increment_count():
    c = CardCounter()
    for k in ('2', '3', '4', '5', '6'):
        c.add_card(k)
    assert c.running_count == 5
    assert c.cards_seen == 5


def test_high_cards_decrement_count():
    c = CardCounter()
    for k in ('T', 'A'):
        c.add_card(k)
    assert c.running_count == -2


def test_neutral_cards_do_not_change_count():
    c = CardCounter()
    for k in ('7', '8', '9'):
        assert c.add_card(k) == 0
    assert c.running_count == 0
    assert c.cards_seen == 3


def test_unknown_key_is_neutral_but_advances_shoe():
    c = CardCounter()
    assert c.add_card('?') == 0
    assert c.running_count == 0
    assert c.cards_seen == 1


def test_add_card_returns_value():
    c = CardCounter()
    assert c.add_card('5') == 1
    assert c.add_card('T') == -1
    assert c.add_card('8') == 0


# ─── undo ──────────────────────────────────────────────────────────────────────

def test_undo_reverts_last_card():
    c = CardCounter()
    c.add_card('5')
    c.add_card('T')
    assert c.undo() == 'T'
    assert c.running_count == 1
    assert c.cards_seen == 1


def test_undo_on_empty_history_returns_none():
    assert CardCounter().undo() is None


# ─── reset ─────────────────────────────────────────────────────────────────────

def test_reset_clears_count_but_keeps_decks():
    c = CardCounter(num_decks=8)
    c.add_card('5')
    c.reset()
    assert c.running_count == 0
    assert c.cards_seen == 0
    assert c.num_decks == 8
    assert c.undo() is None  # history cleared too


# ─── derived statistics ────────────────────────────────────────────────────────

def test_total_cards():
    assert CardCounter(num_decks=6).total_cards == 312
    assert CardCounter(num_decks=1).total_cards == 52


def test_true_count_normalises_by_decks_remaining():
    c = CardCounter(num_decks=6)
    # 52 low cards seen → running +52, 5 decks remain → true count 10.4
    for _ in range(52):
        c.add_card('5')
    assert round(c.true_count, 4) == round(52 / 5, 4)


def test_decks_remaining_floored():
    c = CardCounter(num_decks=1)
    for _ in range(52):           # whole deck dealt
        c.add_card('7')
    assert c.decks_remaining == counting.MIN_DECKS_REMAINING


def test_true_count_never_divides_by_zero_at_end_of_shoe():
    c = CardCounter(num_decks=1)
    for _ in range(52):
        c.add_card('5')
    # Would be /0 without the floor; must stay finite.
    assert c.true_count == 52 / counting.MIN_DECKS_REMAINING


def test_penetration_pct():
    c = CardCounter(num_decks=6)
    for _ in range(156):          # half the shoe
        c.add_card('8')
    assert round(c.penetration_pct, 1) == 50.0


# ─── bet recommendation bands ──────────────────────────────────────────────────

def _force_true_count(c: CardCounter, target: float) -> None:
    """Set decks_remaining to 1 so running_count == true_count for easy testing."""
    # With (total - seen)/52 == 1, true_count equals running_count.
    c.num_decks = 2
    c.cards_seen = c.total_cards - 52   # exactly 1 deck remaining
    c.running_count = int(target)


def test_bet_bands_cover_full_range():
    c = CardCounter()
    cases = {6: 'MAX BET', 4: 'RAISE BET', 2: 'SLIGHT RAISE',
             0: 'KEEP BET', -2: 'SLIGHT LOWER', -5: 'LOWER BET'}
    for tc, expected in cases.items():
        _force_true_count(c, tc)
        label, color = c.bet_info()
        assert expected in label, f'true count {tc} → {label!r}'
        assert color.startswith('#')


# ─── running-count colour bands ────────────────────────────────────────────────

def test_count_color_bands():
    c = CardCounter()
    for rc, expected in [(5, '#00ff88'), (3, '#88ff00'), (1, '#dddddd'),
                         (-1, '#ff9900'), (-5, '#ff3300')]:
        c.running_count = rc
        assert c.count_color() == expected


# ─── player edge ───────────────────────────────────────────────────────────────

def test_player_edge_linear_model():
    c = CardCounter()
    _force_true_count(c, 0)
    assert c.player_edge() == counting.BASE_EDGE_PCT
    _force_true_count(c, 2)
    assert c.player_edge() == counting.BASE_EDGE_PCT + 2 * counting.EDGE_PCT_PER_TRUE_COUNT


# ─── badge colours ─────────────────────────────────────────────────────────────

def test_card_badge_color_categories():
    assert card_badge_color('A') == card_badge_color('T')      # high cards
    assert card_badge_color('8') != card_badge_color('A')      # neutral vs high
    assert card_badge_color('2') == card_badge_color('6')      # low cards
    for key in ('A', 'T', '7', '2'):
        bg, fg = card_badge_color(key)
        assert bg.startswith('#') and fg.startswith('#')


# ─── shared display helpers ─────────────────────────────────────────────────────

def test_edge_color_bands():
    assert edge_color(1.0)  == counting._EDGE_COLOR_POS    # favourable
    assert edge_color(0.0)  == counting._EDGE_COLOR_FLAT   # break-even
    assert edge_color(-2.0) == counting._EDGE_COLOR_NEG    # poor
    # Boundaries are exclusive: exactly at a threshold stays "flat".
    assert edge_color(0.5)  == counting._EDGE_COLOR_FLAT
    assert edge_color(-1.0) == counting._EDGE_COLOR_FLAT


def test_penetration_bar_color_bands():
    assert penetration_bar_color(0)   == counting._PEN_COLOR_LOW
    assert penetration_bar_color(49)  == counting._PEN_COLOR_LOW
    assert penetration_bar_color(50)  == counting._PEN_COLOR_MID
    assert penetration_bar_color(74)  == counting._PEN_COLOR_MID
    assert penetration_bar_color(75)  == counting._PEN_COLOR_HIGH
    assert penetration_bar_color(100) == counting._PEN_COLOR_HIGH


def test_display_rank():
    assert display_rank('T') == '10/J/Q/K'
    for k in ('A', '2', '9'):
        assert display_rank(k) == k


def test_signed_prefix():
    assert signed_prefix(5)    == '+'
    assert signed_prefix(0)    == ''     # zero gets no '+'
    assert signed_prefix(-3)   == ''     # negatives carry their own '-'
    assert signed_prefix(0.1)  == '+'


# ─── allow running without pytest ──────────────────────────────────────────────

if __name__ == '__main__':
    import sys
    funcs = [v for k, v in sorted(globals().items())
             if k.startswith('test_') and callable(v)]
    failures = 0
    for fn in funcs:
        try:
            fn()
            print(f'  PASS  {fn.__name__}')
        except AssertionError as e:
            failures += 1
            print(f'  FAIL  {fn.__name__}: {e}')
    print(f'\n{len(funcs) - failures}/{len(funcs)} passed')
    sys.exit(1 if failures else 0)
