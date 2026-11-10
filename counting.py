#!/usr/bin/env python3
"""
Hi-Lo blackjack card-counting domain logic.

This module is deliberately free of any UI, screen-capture, or network code so
the counting rules can be unit-tested in isolation and shared by both the manual
(`card_counter.py`) and automatic (`auto_card_counter.py`) overlays.

Hi-Lo system:  2-6 = +1  |  7-9 = 0  |  10/J/Q/K/A = -1
"""

from __future__ import annotations


# ─── Counting constants ───────────────────────────────────────────────────────

DECK_SIZE = 52

# A shoe is never treated as having fewer than this many decks left, so the true
# count stays finite even at the very end of the shoe (avoids divide-by-zero).
MIN_DECKS_REMAINING = 0.5

# Hi-Lo point value for each rank key. Tens and aces are -1; 7-9 are neutral.
HI_LO_VALUES: dict[str, int] = {
    '2': 1, '3': 1, '4': 1, '5': 1, '6': 1,
    '7': 0, '8': 0, '9': 0,
    'T': -1, 'A': -1,
}

# Ranks the counter understands. Any other key is treated as a neutral (0-value)
# card that still advances the shoe — see CardCounter.add_card.
VALID_KEYS: frozenset[str] = frozenset(HI_LO_VALUES)


# ─── Display palette ──────────────────────────────────────────────────────────
# Semantic colours shared by the running-count readout and bet recommendation.

_GREEN      = '#00ff88'   # strongly player-favourable
_LIME       = '#88ff00'   # favourable
_YELLOW     = '#ddff00'   # slightly favourable
_COUNT_ZERO = '#dddddd'   # neutral running count
_BET_ZERO   = '#aaaaaa'   # neutral bet recommendation
_ORANGE     = '#ff9900'   # unfavourable
_RED        = '#ff3300'   # strongly unfavourable

# Running-count colour bands: (minimum running count inclusive, colour).
# Evaluated top to bottom; the final entry is the fallback for anything lower.
_COUNT_COLOR_BANDS: list[tuple[int, str]] = [
    (4,  _GREEN),
    (2,  _LIME),
    (0,  _COUNT_ZERO),
    (-2, _ORANGE),
]
_COUNT_COLOR_FALLBACK = _RED

# Bet recommendation bands: (minimum true count inclusive, label, colour).
# Evaluated top to bottom; the final entry is the fallback for anything lower.
_BET_BANDS: list[tuple[float, str, str]] = [
    (5,  '⬆⬆  MAX BET',      _GREEN),
    (3,  '⬆  RAISE BET',      _LIME),
    (1,  '↑  SLIGHT RAISE',   _YELLOW),
    (-1, '→  KEEP BET',        _BET_ZERO),
    (-3, '↓  SLIGHT LOWER',   _ORANGE),
]
_BET_FALLBACK = ('⬇  LOWER BET', _RED)

# Player-edge model: a rough heuristic of base house edge plus a linear bonus per
# point of true count. Not exact basic-strategy EV, but good enough for the UI.
BASE_EDGE_PCT          = -0.5
EDGE_PCT_PER_TRUE_COUNT = 0.5

# Card-badge (background, foreground) colours keyed by rank category.
_BADGE_TEN_ACE   = ('#2a0a0a', '#ff6666')   # high cards (-1)
_BADGE_NEUTRAL   = ('#141428', '#778899')   # 7-9 (0)
_BADGE_LOW       = ('#071a10', '#44dd88')   # 2-6 (+1)


def card_badge_color(key: str) -> tuple[str, str]:
    """Return the (background, foreground) hex colours for a card badge."""
    if key in ('T', 'A'):
        return _BADGE_TEN_ACE
    if key in ('7', '8', '9'):
        return _BADGE_NEUTRAL
    return _BADGE_LOW


# Player-edge readout colour thresholds (edge as a percentage).
_EDGE_FAVOURABLE_PCT   = 0.5
_EDGE_UNFAVOURABLE_PCT = -1.0
_EDGE_COLOR_POS  = '#00ff88'   # clearly player-favourable
_EDGE_COLOR_NEG  = '#ff4400'   # clearly house-favourable
_EDGE_COLOR_FLAT = '#445566'   # roughly break-even


def edge_color(edge_pct: float) -> str:
    """Colour for the player-edge readout: green favourable, red poor, grey flat."""
    if edge_pct > _EDGE_FAVOURABLE_PCT:
        return _EDGE_COLOR_POS
    if edge_pct < _EDGE_UNFAVOURABLE_PCT:
        return _EDGE_COLOR_NEG
    return _EDGE_COLOR_FLAT


# Penetration-bar fill colour by how deep into the shoe play has reached.
_PEN_SHALLOW_PCT = 50
_PEN_DEEP_PCT    = 75
_PEN_COLOR_LOW   = '#1a5522'   # early shoe
_PEN_COLOR_MID   = '#554400'   # mid shoe
_PEN_COLOR_HIGH  = '#772211'   # deep shoe (count most reliable)


def penetration_bar_color(pen_pct: float) -> str:
    """Fill colour for the shoe-penetration progress bar."""
    if pen_pct < _PEN_SHALLOW_PCT:
        return _PEN_COLOR_LOW
    if pen_pct < _PEN_DEEP_PCT:
        return _PEN_COLOR_MID
    return _PEN_COLOR_HIGH


def display_rank(key: str) -> str:
    """Human-friendly label for a rank key — 'T' expands to '10/J/Q/K'."""
    return '10/J/Q/K' if key == 'T' else key


def signed_prefix(value: float) -> str:
    """Return '+' for positive values, '' otherwise (negatives carry their own '-')."""
    return '+' if value > 0 else ''


# ─── Counter ──────────────────────────────────────────────────────────────────

class CardCounter:
    """
    Tracks a running Hi-Lo count over a multi-deck shoe.

    The counter is a pure data model: callers feed it observed cards via
    ``add_card`` and read derived statistics (true count, penetration, bet
    recommendation) from its properties and helper methods. It performs no I/O.
    """

    # Exposed for callers that build UI from the rank set (e.g. button grids).
    HI_LO = HI_LO_VALUES

    def __init__(self, num_decks: int = 6) -> None:
        self.num_decks     = num_decks
        self.running_count = 0
        self.cards_seen    = 0
        self.hands_played  = 0
        self._history: list[tuple[str, int]] = []

    # ── Derived statistics ──────────────────────────────────────────────────

    @property
    def total_cards(self) -> int:
        """Total cards in the full shoe."""
        return self.num_decks * DECK_SIZE

    @property
    def decks_remaining(self) -> float:
        """Estimated decks left in the shoe, floored at MIN_DECKS_REMAINING."""
        return max(MIN_DECKS_REMAINING, (self.total_cards - self.cards_seen) / DECK_SIZE)

    @property
    def true_count(self) -> float:
        """Running count normalised by decks remaining (the betting signal)."""
        return self.running_count / self.decks_remaining

    @property
    def penetration_pct(self) -> float:
        """Percentage of the shoe that has been dealt, capped at 100%."""
        if self.total_cards == 0:
            return 0.0
        return min(100.0, self.cards_seen / self.total_cards * 100)

    # ── Mutations ───────────────────────────────────────────────────────────

    def add_card(self, key: str) -> int:
        """
        Record one observed card and return its Hi-Lo value.

        Unknown keys count as a neutral (0-value) card that still advances the
        shoe; this matches how detectors occasionally surface a card whose rank
        is recognised for dealing purposes but carries no counting weight.
        """
        val = HI_LO_VALUES.get(key, 0)
        self.running_count += val
        self.cards_seen    += 1
        self._history.append((key, val))
        return val

    def undo(self) -> str | None:
        """Revert the most recently added card. Returns its key, or None if empty."""
        if not self._history:
            return None
        key, val = self._history.pop()
        self.running_count -= val
        self.cards_seen    -= 1
        return key

    def reset(self) -> None:
        """Clear the count and history for a fresh shoe (keeps deck count/hands)."""
        self.running_count = 0
        self.cards_seen    = 0
        self._history.clear()

    # ── Display helpers ─────────────────────────────────────────────────────

    def count_color(self) -> str:
        """Hex colour reflecting how favourable the current running count is."""
        for minimum, color in _COUNT_COLOR_BANDS:
            if self.running_count >= minimum:
                return color
        return _COUNT_COLOR_FALLBACK

    def bet_info(self) -> tuple[str, str]:
        """Return (label, hex_color) for the bet recommendation at this true count."""
        tc = self.true_count
        for minimum, label, color in _BET_BANDS:
            if tc >= minimum:
                return label, color
        return _BET_FALLBACK

    def player_edge(self) -> float:
        """Rough edge estimate: base house edge ± a linear bonus per true-count point."""
        return BASE_EDGE_PCT + self.true_count * EDGE_PCT_PER_TRUE_COUNT
