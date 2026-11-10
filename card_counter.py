#!/usr/bin/env python3
"""
Live Blackjack Card Counter — Always-on-top overlay
Hi-Lo system: 2-6 = +1 | 7-9 = 0 | 10/J/Q/K/A = -1
Keys: 2-9, T=Ten/Face, A=Ace, Z=Undo, N=New Shoe
"""

import tkinter as tk
from tkinter import messagebox

from counting import (CardCounter, edge_color, penetration_bar_color,
                      display_rank, signed_prefix)
from theme import BG, BG_PANEL, BG_BAR, ACCENT


# ─── UI ────────────────────────────────────────────────────────────────────────

CARD_ROWS = [
    [('2', '2'), ('3', '3'), ('4', '4'), ('5', '5'), ('6', '6')],
    [('7', '7'), ('8', '8'), ('9', '9'), ('T', '10/J/Q/K'), ('A', 'A')],
]


class App:
    _PEN_TRACK = '#111122'   # penetration-bar background track colour

    def __init__(self):
        self.ctr = CardCounter(num_decks=6)
        self._dx = self._dy = 0

        # ── Root window ──────────────────────────────────────────────────────
        self.root = tk.Tk()
        self.root.title("♠ Card Counter")
        self.root.configure(bg=BG)
        self.root.attributes('-topmost', True)
        self.root.attributes('-alpha', 0.93)
        self.root.geometry('310x600+40+40')
        self.root.resizable(False, False)

        self._build_ui()
        self._bind_keys()
        self.root.after(80, self._refresh)   # first render after window is sized

    # ── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        r = self.root

        # Title / drag bar
        bar = tk.Frame(r, bg=BG_BAR, cursor='fleur')
        bar.pack(fill='x', padx=2, pady=2)
        bar.bind('<Button-1>',  self._drag_start)
        bar.bind('<B1-Motion>', self._drag_move)

        tk.Label(bar, text="♠  CARD COUNTER  ♠", font=('Helvetica', 11, 'bold'),
                 fg=ACCENT, bg=BG_BAR).pack(side='left', padx=10, pady=7)

        # Deck selector
        df = tk.Frame(bar, bg=BG_BAR)
        df.pack(side='right', padx=8, pady=4)
        tk.Label(df, text="Decks:", font=('Helvetica', 9), fg='#8899bb', bg=BG_BAR).pack(side='left')
        self._deck_var = tk.StringVar(value='6')
        om = tk.OptionMenu(df, self._deck_var, '1', '2', '4', '6', '8',
                           command=self._deck_change)
        om.config(bg='#22224a', fg='white', font=('Helvetica', 9),
                  bd=0, highlightthickness=0, relief='flat', activebackground='#33335a')
        om['menu'].config(bg='#22224a', fg='white', activebackground=ACCENT)
        om.pack(side='left')

        # ── Running Count ────────────────────────────────────────────────────
        tk.Label(r, text="RUNNING COUNT", font=('Helvetica', 8, 'bold'),
                 fg='#334455', bg=BG).pack(pady=(10, 0))

        self._rc_lbl = tk.Label(r, text="0", font=('Helvetica', 60, 'bold'),
                                fg='#dddddd', bg=BG)
        self._rc_lbl.pack()

        # ── True Count ───────────────────────────────────────────────────────
        tk.Label(r, text="TRUE COUNT", font=('Helvetica', 8, 'bold'),
                 fg='#334455', bg=BG).pack(pady=(2, 0))

        self._tc_lbl = tk.Label(r, text="0.0", font=('Helvetica', 26, 'bold'),
                                fg='#8899ff', bg=BG)
        self._tc_lbl.pack()

        # ── Bet Recommendation ───────────────────────────────────────────────
        betf = tk.Frame(r, bg=BG_PANEL)
        betf.pack(fill='x', padx=12, pady=8)

        tk.Label(betf, text="BET RECOMMENDATION", font=('Helvetica', 8, 'bold'),
                 fg='#334455', bg=BG_PANEL).pack(pady=(6, 0))

        self._bet_lbl = tk.Label(betf, text="→  KEEP BET",
                                 font=('Helvetica', 17, 'bold'),
                                 fg='#aaaaaa', bg=BG_PANEL)
        self._bet_lbl.pack(pady=(2, 6))

        # ── Shoe penetration bar ─────────────────────────────────────────────
        pf = tk.Frame(r, bg=BG)
        pf.pack(fill='x', padx=12, pady=(0, 2))
        tk.Label(pf, text="Shoe Penetration", font=('Helvetica', 8),
                 fg='#334455', bg=BG).pack(side='left')
        self._pen_lbl = tk.Label(pf, text="0%", font=('Helvetica', 8),
                                 fg='#334455', bg=BG)
        self._pen_lbl.pack(side='right')

        self._pen_canvas = tk.Canvas(r, height=8, bg=self._PEN_TRACK,
                                     bd=0, highlightthickness=0)
        self._pen_canvas.pack(fill='x', padx=12, pady=(0, 4))

        # ── Edge / cards seen ────────────────────────────────────────────────
        sf = tk.Frame(r, bg=BG)
        sf.pack(fill='x', padx=14)

        self._edge_lbl = tk.Label(sf, text="Edge: -0.5%", font=('Helvetica', 9),
                                  fg='#445566', bg=BG)
        self._edge_lbl.pack(side='left')

        self._cards_lbl = tk.Label(sf, text="Cards: 0 / 312", font=('Helvetica', 9),
                                   fg='#445566', bg=BG)
        self._cards_lbl.pack(side='right')

        # ── Separator ────────────────────────────────────────────────────────
        tk.Frame(r, bg='#1a1a33', height=1).pack(fill='x', padx=8, pady=8)

        tk.Label(r, text="KEYBOARD:  2-9 · T = Ten/Face · A = Ace · Z = Undo · N = New Shoe",
                 font=('Helvetica', 7), fg='#2a3a44', bg=BG).pack()

        # ── Card Buttons ─────────────────────────────────────────────────────
        bf = tk.Frame(r, bg=BG)
        bf.pack(padx=10, pady=6)

        self._btns: dict[str, tk.Button] = {}
        for row_i, row in enumerate(CARD_ROWS):
            for col_i, (k, label) in enumerate(row):
                v = CardCounter.HI_LO.get(k, 0)
                if v > 0:
                    bg, abg = '#144422', '#1e6633'
                elif v < 0:
                    bg, abg = '#441414', '#662020'
                else:
                    bg, abg = '#1c1c3c', '#2a2a50'

                btn = tk.Button(
                    bf, text=label, font=('Helvetica', 9, 'bold'),
                    bg=bg, fg='white', activebackground=abg,
                    activeforeground='white', bd=0, padx=6, pady=8,
                    cursor='hand2', command=lambda k=k: self._card(k)
                )
                btn.grid(row=row_i, column=col_i, padx=3, pady=3, sticky='nsew')
                self._btns[k] = btn

        # ── Last card info ───────────────────────────────────────────────────
        self._last_lbl = tk.Label(r, text="Last card: —", font=('Helvetica', 8),
                                  fg='#334455', bg=BG)
        self._last_lbl.pack(pady=(2, 0))

        # ── Control buttons ──────────────────────────────────────────────────
        tk.Frame(r, bg='#1a1a33', height=1).pack(fill='x', padx=8, pady=6)

        cf = tk.Frame(r, bg=BG)
        cf.pack(fill='x', padx=10, pady=(0, 6))

        tk.Button(cf, text="↩  Undo  (Z)", font=('Helvetica', 9, 'bold'),
                  bg='#181830', fg='#8899bb', activebackground='#252548',
                  bd=0, padx=8, pady=7, cursor='hand2',
                  command=self._undo).pack(side='left', expand=True, fill='x', padx=(0, 2))

        tk.Button(cf, text="♻  New Shoe  (N)", font=('Helvetica', 9, 'bold'),
                  bg='#0a220a', fg='#77aa77', activebackground='#153815',
                  bd=0, padx=8, pady=7, cursor='hand2',
                  command=self._new_shoe).pack(side='right', expand=True, fill='x', padx=(2, 0))

        # ── Opacity slider ───────────────────────────────────────────────────
        af = tk.Frame(r, bg=BG)
        af.pack(fill='x', padx=10, pady=(0, 6))

        tk.Label(af, text="Opacity:", font=('Helvetica', 8),
                 fg='#2a3a44', bg=BG).pack(side='left')

        self._alpha_var = tk.DoubleVar(value=0.93)
        sl = tk.Scale(
            af, from_=0.30, to=1.0, resolution=0.05, orient='horizontal',
            variable=self._alpha_var, command=self._set_alpha,
            bg=BG, fg='#334455', troughcolor='#1a1a33',
            highlightthickness=0, bd=0, length=170, sliderrelief='flat',
            showvalue=False,
        )
        sl.pack(side='right')

    # ── Key bindings ─────────────────────────────────────────────────────────

    def _bind_keys(self) -> None:
        for k in '23456789':
            self.root.bind(f'<Key-{k}>', lambda e, k=k: self._card(k))
        for k in ('t', 'T', '0'):
            self.root.bind(f'<Key-{k}>', lambda e: self._card('T'))
        for k in ('a', 'A'):
            self.root.bind(f'<Key-{k}>', lambda e: self._card('A'))
        for k in ('z', 'Z'):
            self.root.bind(f'<Key-{k}>', lambda e: self._undo())
        self.root.bind('<BackSpace>', lambda e: self._undo())
        for k in ('n', 'N'):
            self.root.bind(f'<Key-{k}>', lambda e: self._new_shoe())

    # ── Event handlers ────────────────────────────────────────────────────────

    def _drag_start(self, e) -> None:
        self._dx, self._dy = e.x, e.y

    def _drag_move(self, e) -> None:
        x = self.root.winfo_x() + e.x - self._dx
        y = self.root.winfo_y() + e.y - self._dy
        self.root.geometry(f'+{x}+{y}')

    def _card(self, k: str) -> None:
        val = self.ctr.add_card(k)
        disp = display_rank(k)
        sign = signed_prefix(val)
        self._last_lbl.config(text=f"Last card: {disp}  ({sign}{val})")

        btn = self._btns.get(k)
        if btn:
            orig = btn.cget('bg')
            btn.config(bg='#ffffff')
            self.root.after(130, lambda b=btn, c=orig: b.config(bg=c))

        self._refresh()

    def _undo(self) -> None:
        k = self.ctr.undo()
        if k:
            self._last_lbl.config(text=f"Undone: {display_rank(k)}")
            self._refresh()

    def _new_shoe(self) -> None:
        if messagebox.askyesno("New Shoe", "Reset the count for a new shoe?",
                               parent=self.root):
            self.ctr.reset()
            self._last_lbl.config(text="Last card: —  (New Shoe)")
            self._refresh()

    def _deck_change(self, v: str) -> None:
        self.ctr.num_decks = int(v)
        self.ctr.reset()
        self._refresh()

    def _set_alpha(self, v) -> None:
        self.root.attributes('-alpha', float(v))

    # ── Display refresh ───────────────────────────────────────────────────────

    def _refresh(self) -> None:
        rc  = self.ctr.running_count
        tc  = self.ctr.true_count
        pen = self.ctr.penetration_pct

        # Running count
        self._rc_lbl.config(text=f"{signed_prefix(rc)}{rc}", fg=self.ctr.count_color())

        # True count
        self._tc_lbl.config(text=f"{signed_prefix(tc)}{tc:.1f}")

        # Bet recommendation
        bet_txt, bet_clr = self.ctr.bet_info()
        self._bet_lbl.config(text=bet_txt, fg=bet_clr)

        # Player edge
        edge = self.ctr.player_edge()
        self._edge_lbl.config(text=f"Edge: {signed_prefix(edge)}{edge:.1f}%",
                              fg=edge_color(edge))

        # Cards seen
        self._cards_lbl.config(
            text=f"Cards: {self.ctr.cards_seen} / {self.ctr.total_cards}"
        )

        # Penetration bar
        self._pen_lbl.config(text=f"{pen:.0f}%")
        c = self._pen_canvas
        c.delete('all')
        w = c.winfo_width() or 286
        filled = int(w * pen / 100)
        c.create_rectangle(0, 0, w, 8, fill=self._PEN_TRACK, outline='')
        if filled > 0:
            c.create_rectangle(0, 0, filled, 8, fill=penetration_bar_color(pen), outline='')

    # ── Entry point ───────────────────────────────────────────────────────────

    def run(self) -> None:
        self.root.mainloop()


if __name__ == '__main__':
    App().run()
