#!/usr/bin/env python3
"""
Auto Blackjack Card Counter  v5
Dual detection engines — YOLO (free, local) and Claude Haiku (paid, highly accurate).
"""

import sys
import os
import base64
import logging
import threading
import time
import json
from collections import Counter, deque
from io import BytesIO
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, simpledialog


# ─── Dependency check ─────────────────────────────────────────────────────────

def _check_deps():
    missing = []
    for pkg, imp in [('mss', 'mss'), ('Pillow', 'PIL'),
                     ('ultralytics', 'ultralytics'),
                     ('huggingface_hub', 'huggingface_hub'),
                     ('anthropic', 'anthropic')]:
        try:
            __import__(imp)
        except ImportError:
            missing.append(pkg)
    if missing:
        print("\nMissing packages. Run:")
        print(f"  pip3 install {' '.join(missing)} --break-system-packages\n")
        sys.exit(1)

_check_deps()

logging.getLogger('ultralytics').setLevel(logging.ERROR)

import mss
from PIL import Image, ImageChops, ImageStat, ImageEnhance
from ultralytics import YOLO
import anthropic

from counting import (CardCounter, card_badge_color, edge_color,
                      penetration_bar_color, display_rank, signed_prefix,
                      VALID_KEYS)
from theme import BG as _BG, BG_PANEL as _BG_PANEL, BG_BAR as _BG_BAR, ACCENT as _ACCENT


# ─── Config ───────────────────────────────────────────────────────────────────

CONFIG_FILE = Path.home() / '.auto_card_counter.json'

def _load_cfg() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception:
        return {}

def _save_cfg(cfg: dict) -> None:
    try:
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    except Exception:
        pass


# ─── Device detection ─────────────────────────────────────────────────────────

def _best_device() -> str:
    try:
        import torch
        if torch.backends.mps.is_available():  return 'mps'
        if torch.cuda.is_available():           return 'cuda'
    except Exception:
        pass
    return 'cpu'


# Hi-Lo counting logic lives in counting.py (CardCounter, card_badge_color),
# shared with the manual overlay so the rules can never drift between the two.


# ─── Image utilities ──────────────────────────────────────────────────────────

# Black-screen detection: downscale to this many pixels per side, then flag the
# frame if at least BLACK_FRACTION of pixels are darker than BLACK_LEVEL. This
# catches the "Screen Recording permission not granted" all-black capture.
_BLACK_THUMB_PX  = 32
_BLACK_LEVEL     = 15
_BLACK_FRACTION  = 0.95

# Frame-difference: downscale to this many pixels per side and treat the frame as
# changed when the mean per-pixel luminance delta exceeds _DIFF_THRESHOLD. Lets
# the loop skip re-running detection on a static table.
_DIFF_THUMB_PX   = 64
_DIFF_THRESHOLD  = 2.5

# Pre-inference enhancement to help the detector on compressed casino streams.
_CONTRAST_BOOST  = 1.4
_SHARPNESS_BOOST = 1.8

# Largest edge (px) sent to the Claude vision API, and JPEG quality for it.
_API_MAX_EDGE    = 1024
_API_JPEG_QUALITY = 85


# Friendly names for the apps that commonly launch this script, keyed by the
# TERM_PROGRAM value each one sets. Used to tell the user exactly which app needs
# Screen Recording permission.
_TERM_APP_NAMES = {
    'Apple_Terminal': 'Terminal',
    'iTerm.app':      'iTerm',
    'vscode':         'Visual Studio Code',
    'WarpTerminal':   'Warp',
    'Hyper':          'Hyper',
    'Tabby':          'Tabby',
    'WezTerm':        'WezTerm',
}


def _launching_app_name() -> str:
    """Best-effort name of the app that launched this script (for permission help)."""
    prog = os.environ.get('TERM_PROGRAM', '')
    return _TERM_APP_NAMES.get(prog, prog or 'your terminal app')


def _is_black_screen(img: Image.Image) -> bool:
    """True if the capture is almost entirely black (likely a permissions issue)."""
    small  = img.resize((_BLACK_THUMB_PX, _BLACK_THUMB_PX)).convert('L')
    pixels = small.getdata()
    dark   = sum(1 for p in pixels if p < _BLACK_LEVEL)
    return dark / len(pixels) >= _BLACK_FRACTION


def _frames_differ(prev: Image.Image, curr: Image.Image) -> bool:
    """True if two captures differ enough to be worth re-running detection."""
    a    = prev.resize((_DIFF_THUMB_PX, _DIFF_THUMB_PX)).convert('L')
    b    = curr.resize((_DIFF_THUMB_PX, _DIFF_THUMB_PX)).convert('L')
    diff = ImageChops.difference(a, b)
    return ImageStat.Stat(diff).mean[0] > _DIFF_THRESHOLD


def _enhance(img: Image.Image) -> Image.Image:
    """Boost contrast and sharpness to improve card recognition before inference."""
    img = ImageEnhance.Contrast(img).enhance(_CONTRAST_BOOST)
    img = ImageEnhance.Sharpness(img).enhance(_SHARPNESS_BOOST)
    return img


def _resize_for_api(img: Image.Image, max_px: int = _API_MAX_EDGE) -> Image.Image:
    """Downscale so the longest edge is at most max_px (keeps API payloads small)."""
    w, h = img.size
    longest = max(w, h)
    if longest <= max_px:
        return img
    scale = max_px / longest
    return img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)


# ─── YOLO Detector ────────────────────────────────────────────────────────────

_HF_REPO     = 'mustafakemal0146/playing-cards-yolov8'
_MODEL_CACHE = Path.home() / '.cache' / 'card_counter' / 'playing_cards.pt'

_RANK_MAP: dict[str, str] = {
    'a': 'A',
    '2': '2', '3': '3', '4': '4', '5': '5', '6': '6',
    '7': '7', '8': '8', '9': '9',
    '10': 'T', 'j': 'T', 'q': 'T', 'k': 'T',
}


def _download_yolo_model(on_status) -> str:
    import shutil
    from huggingface_hub import hf_hub_download, list_repo_files

    if _MODEL_CACHE.exists():
        on_status('Loading cached YOLO model…')
        return str(_MODEL_CACHE)

    _MODEL_CACHE.parent.mkdir(parents=True, exist_ok=True)
    on_status('Connecting to HuggingFace…')

    try:
        all_files = list(list_repo_files(_HF_REPO))
        pt_files  = [f for f in all_files if f.endswith('.pt')]
    except Exception:
        pt_files  = []

    if not pt_files:
        pt_files = ['playing_cards_model_0_playing-cards-colab.pt', 'best.pt', 'model.pt']

    last_err = None
    for fname in pt_files:
        try:
            on_status(f'Downloading {fname}…')
            src = hf_hub_download(repo_id=_HF_REPO, filename=fname)
            shutil.copy2(src, _MODEL_CACHE)
            return str(_MODEL_CACHE)
        except Exception as e:
            last_err = e

    raise RuntimeError(f'Could not download YOLO model: {last_err}')


class YOLODetector:
    """Local YOLOv8 playing-card detector. Free, runs on-device, no network."""

    # Low confidence floor — favours recall on real (noisy) casino screenshots.
    CONF = 0.10
    # Confidence used by the Test Shot diagnostic, below the counting threshold,
    # so the user can see near-misses the live loop would discard.
    DEBUG_CONF = 0.05
    # Inference image size; larger catches small/distant cards at a speed cost.
    IMG_SIZE   = 1280
    # Non-max-suppression IoU threshold for overlapping detections.
    IOU        = 0.45
    # Trailing suit letters on class names like "10C"/"AH" — stripped to a rank.
    _SUITS     = 'cdhs'

    def __init__(self, model_path: str) -> None:
        self._device = _best_device()
        self._model  = YOLO(model_path)

    def detect(self, img: Image.Image) -> list[str]:
        """Detect face-up cards and return their Hi-Lo rank keys."""
        results = self._predict(img, self.CONF)
        cards = []
        if results:
            for box in results[0].boxes:
                name = results[0].names[int(box.cls[0])].lower()
                rank = self._to_rank(name)
                if rank:
                    cards.append(rank)
        return cards

    def debug_detect(self, img: Image.Image, conf: float = DEBUG_CONF):
        """Return ([(class_name, confidence), ...], result) for the Test Shot tool."""
        results = self._predict(img, conf)
        dets = []
        if results:
            for box in results[0].boxes:
                name = results[0].names[int(box.cls[0])]
                dets.append((name, float(box.conf[0])))
        return dets, results[0] if results else None

    def _predict(self, img: Image.Image, conf: float):
        """Run enhanced-image inference at the given confidence threshold."""
        return self._model.predict(
            source=_enhance(img), conf=conf,
            device=self._device, verbose=False,
            imgsz=self.IMG_SIZE, iou=self.IOU,
        )

    @classmethod
    def _to_rank(cls, cls_name: str) -> str | None:
        """Map a model class name (e.g. '10c', 'ah') to a Hi-Lo rank key, or None."""
        rank = cls_name[:-1] if cls_name and cls_name[-1] in cls._SUITS else cls_name
        return _RANK_MAP.get(rank)


# ─── Claude Haiku Detector ────────────────────────────────────────────────────

_CLAUDE_PROMPT = """\
You are analyzing a live online blackjack screenshot. Find every face-up playing card.

Use ONLY these codes:
  A  = Ace
  T  = Ten, Jack, Queen, or King (any 10-value card)
  2-9 = the number shown

Include ALL face-up cards: dealer upcard + every player hand.
Ignore face-down cards (card backs).

Reply with ONLY a comma-separated list, or NONE if no face-up cards are visible.

Examples:
  Dealer K, players have 7+4 and A+6  →  T, 7, 4, A, 6
  No face-up cards                     →  NONE"""


class ClaudeDetector:
    """Cloud card detector using the Claude vision API. Paid, but robust to any
    card design — the fallback when the local YOLO model misses a casino's deck."""

    MODEL      = 'claude-haiku-4-5'
    # The reply is a short comma-separated rank list, so a tiny budget suffices.
    MAX_TOKENS = 80

    def __init__(self, api_key: str) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)

    def detect(self, img: Image.Image) -> list[str]:
        """Detect face-up cards via the vision API and return Hi-Lo rank keys."""
        b64 = self._encode(img)
        resp = self._client.messages.create(
            model=self.MODEL,
            max_tokens=self.MAX_TOKENS,
            messages=[{'role': 'user', 'content': [
                {'type': 'image',
                 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': b64}},
                {'type': 'text', 'text': _CLAUDE_PROMPT},
            ]}],
        )
        return self._parse(resp.content[0].text)

    def debug_detect(self, img: Image.Image, conf: float = 0.0):
        """Mirror YOLODetector.debug_detect; confidence is always 1.0 (no boxes)."""
        cards = self.detect(img)
        return [(c, 1.0) for c in cards], None

    @staticmethod
    def _encode(img: Image.Image) -> str:
        """Downscale to the API size limit and return a base64 JPEG string."""
        buf = BytesIO()
        _resize_for_api(img).save(buf, format='JPEG', quality=_API_JPEG_QUALITY)
        return base64.standard_b64encode(buf.getvalue()).decode()

    @staticmethod
    def _parse(text: str) -> list[str]:
        """Parse Claude's comma/space-separated reply into valid Hi-Lo rank keys."""
        text = text.strip().upper()
        if not text or 'NONE' in text: return []
        return [t for tok in text.replace(',', ' ').split()
                if (t := tok.strip()) in VALID_KEYS]


# ─── Screen Capture ───────────────────────────────────────────────────────────

class ScreenCapture:
    """Grabs a fixed screen region as an RGB image via mss.

    A fresh mss instance is opened per grab on purpose: mss handles are not safe
    to share across threads, and the detection loop runs on a background thread.
    """

    def __init__(self, region: dict) -> None:
        self.region = region

    def grab(self) -> Image.Image:
        with mss.mss() as sct:
            shot = sct.grab(self.region)
            return Image.frombytes('RGB', shot.size, shot.rgb)


# ─── Detection Loop ───────────────────────────────────────────────────────────

class DetectionLoop:
    """Background thread that repeatedly captures the table and counts new cards.

    Each scan diffs against the previous frame and skips detection when the table
    is static, slowing the cadence once the frame has been stable for a while.
    Cards are counted on a single-frame basis: any card present now that wasn't in
    `_seen` is counted immediately and remembered, so a card only counts once even
    while it stays on the table. All UI updates are delivered through the callbacks
    (which marshal onto the Tk main thread), never touched directly from here.
    """

    _INTERVAL_FAST = 0.8   # seconds between scans while the table is changing
    _INTERVAL_SLOW = 2.0   # slower cadence once the table has been stable
    _STABLE_LIMIT  = 6     # consecutive unchanged scans before slowing down
    _BLACK_RETRY   = 4.0   # pause after a black (no-permission) capture

    def __init__(self, capture, detector, on_card, on_status, on_new_hand, on_stop) -> None:
        self.capture     = capture
        self.detector    = detector
        self.on_card     = on_card
        self.on_status   = on_status
        self.on_new_hand = on_new_hand
        self.on_stop     = on_stop

        self._seen:    Counter = Counter()
        self._running  = False
        self._thread:  threading.Thread | None = None
        self._prev_img: Image.Image | None = None
        self._stable   = 0
        self.next_scan_at: float = 0

    def start(self) -> None:
        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def reset_hand(self) -> None:
        self._seen.clear()
        self._prev_img = None
        self._stable   = 0

    def _loop(self) -> None:
        while self._running:
            t0       = time.monotonic()
            interval = (self._INTERVAL_FAST if self._stable < self._STABLE_LIMIT
                        else self._INTERVAL_SLOW)
            try:
                self.on_status('Scanning…')
                img = self.capture.grab()

                if _is_black_screen(img):
                    self.on_status(
                        f'⚠ Black screen — grant Screen Recording to "{_launching_app_name()}" '
                        '(System Settings → Privacy & Security), then fully quit & reopen it.')
                    time.sleep(self._BLACK_RETRY)
                    continue

                if self._prev_img is not None and not _frames_differ(self._prev_img, img):
                    self._stable += 1
                    self.next_scan_at = time.monotonic() + interval
                    time.sleep(max(0.05, interval - (time.monotonic() - t0)))
                    continue

                self._prev_img = img
                cards   = self.detector.detect(img)
                had_new = self._process(cards)
                self._stable = 0 if had_new else self._stable + 1

            except anthropic.AuthenticationError:
                self.on_status('⚠ API key invalid — click "Set API Key"')
                self._running = False
                self.on_stop()
                return
            except anthropic.BadRequestError as exc:
                self.on_status(f'⚠ Claude: {exc}')
                time.sleep(3)
            except anthropic.RateLimitError:
                self.on_status('⚠ Rate limit — slowing down…')
                time.sleep(12)
            except anthropic.APIConnectionError:
                self.on_status('⚠ Network error — retrying…')
                time.sleep(5)
            except anthropic.APIStatusError as exc:
                self.on_status(f'⚠ Claude API {exc.status_code}: {exc.message}')
                time.sleep(3)
            except Exception as exc:
                self.on_status(f'⚠ {exc}')
                time.sleep(2)

            elapsed    = time.monotonic() - t0
            sleep_time = max(0.05, interval - elapsed)
            self.next_scan_at = time.monotonic() + sleep_time
            time.sleep(sleep_time)

    def _process(self, cards: list[str]) -> bool:
        current = Counter(cards)
        n_seen  = sum(self._seen.values())
        n_curr  = sum(current.values())

        if n_curr <= 1 and n_seen >= 3:
            self._seen.clear()
            self.on_new_hand()
            self.on_status('↺ New hand — count continues')
            return False

        new_cards = current - self._seen
        for key, count in new_cards.items():
            for _ in range(count):
                self.on_card(key)

        self._seen += new_cards

        if cards:
            self.on_status(f'On table: {", ".join(sorted(set(cards)))}')
        else:
            self.on_status('No cards detected')

        return bool(new_cards)


# ─── Region Selector ─────────────────────────────────────────────────────────

class RegionSelector:
    """Borderless translucent overlay for drag-selecting a capture region.

    The window itself is semi-transparent (via the '-alpha' attribute, the same
    mechanism the main overlay uses), so the real screen shows through it live —
    no screenshot is taken and no Screen Recording permission is required just to
    pick a region. The user drags directly over their actual casino table.

    Note: '-fullscreen' is deliberately NOT used here. On macOS a fullscreen
    window gets an opaque black backing that ignores transparency; a borderless
    (overrideredirect) window sized to the screen honours '-alpha' correctly.
    """

    _ALPHA      = 0.5         # window opacity — lower = more see-through
    _ACCENT     = '#00ff88'   # selection outline / corner marks
    _BAND       = '#0a3a0a'   # instruction banner background
    _CORNER_LEN = 18          # corner accent length (px)
    _MIN_W      = 80          # ignore selections smaller than this (display px)
    _MIN_H      = 60

    def __init__(self, parent: tk.Tk) -> None:
        self._parent = parent
        self.result: dict | None = None

    def select(self) -> dict | None:
        scale_x, scale_y, origin = self._screen_geometry()
        if scale_x is None:
            return None

        W = self._parent.winfo_screenwidth()
        H = self._parent.winfo_screenheight()

        self._parent.withdraw()
        win = tk.Toplevel(self._parent)
        win.overrideredirect(True)              # borderless: cover the whole screen
        win.geometry(f'{W}x{H}+0+0')
        win.attributes('-topmost', True)
        win.attributes('-alpha', self._ALPHA)   # see-through: live screen shows through
        win.configure(cursor='crosshair', bg='black')

        canvas = tk.Canvas(win, cursor='crosshair', bd=0,
                           highlightthickness=0, bg='black')
        canvas.pack(fill='both', expand=True)
        win.focus_force()                        # so the ESC key binding receives focus

        self._draw_idle(canvas, W, H)

        sx = sy = 0

        def press(e):
            nonlocal sx, sy
            sx, sy = e.x, e.y

        def drag(e):
            self._draw_selection(canvas, W, H, sx, sy, e.x, e.y, scale_x, scale_y)

        def release(e):
            x1, y1 = min(sx, e.x), min(sy, e.y)
            x2, y2 = max(sx, e.x), max(sy, e.y)
            w, h   = x2 - x1, y2 - y1
            if w >= self._MIN_W and h >= self._MIN_H:
                ox, oy = origin
                self.result = {
                    'left':   int(x1 * scale_x) + ox,
                    'top':    int(y1 * scale_y) + oy,
                    'width':  int(w  * scale_x),
                    'height': int(h  * scale_y),
                }
            win.destroy()

        canvas.bind('<ButtonPress-1>',   press)
        canvas.bind('<B1-Motion>',        drag)
        canvas.bind('<ButtonRelease-1>', release)
        win.bind('<Escape>',    lambda e: win.destroy())
        canvas.bind('<Escape>', lambda e: win.destroy())
        self._parent.wait_window(win)
        self._parent.deiconify()
        return self.result

    def _screen_geometry(self):
        """Return (scale_x, scale_y, (origin_left, origin_top)) mapping the
        overlay's logical coordinates to the physical pixels mss.grab expects.

        Reads display geometry only (mss.monitors) — this does NOT capture pixels,
        so it works without Screen Recording permission. Returns (None, None,
        None) if geometry can't be read."""
        try:
            with mss.mss() as sct:
                mons    = sct.monitors
                primary = mons[1] if len(mons) > 1 else mons[0]
        except Exception as e:
            messagebox.showerror('Screen Error',
                f'Could not read screen geometry:\n{e}', parent=self._parent)
            return None, None, None

        log_w = self._parent.winfo_screenwidth()  or primary['width']
        log_h = self._parent.winfo_screenheight() or primary['height']
        scale_x = primary['width']  / log_w
        scale_y = primary['height'] / log_h
        return scale_x, scale_y, (primary['left'], primary['top'])

    def _draw_idle(self, canvas, W, H) -> None:
        """Draw just the instruction banner; the rest of the glass stays clear."""
        canvas.delete('band', 'sel')
        canvas.create_rectangle(0, 0, W, 56, fill=self._BAND, outline='', tags='band')
        canvas.create_text(W // 2, 28,
            text='  Drag over the casino TABLE to select it  •  ESC to cancel  ',
            fill='white', font=('Helvetica', 18, 'bold'), tags='band')

    def _draw_selection(self, canvas, W, H, x1, y1, x2, y2, scale_x, scale_y) -> None:
        """Redraw the selection rectangle, corner marks, size readout, and banner."""
        lx, ly = min(x1, x2), min(y1, y2)
        rx, ry = max(x1, x2), max(y1, y2)
        canvas.delete('band', 'sel')

        canvas.create_rectangle(lx, ly, rx, ry, outline=self._ACCENT, width=2, tags='sel')
        clen = self._CORNER_LEN
        for cx, cy, dx, dy in [(lx,ly,1,1),(rx,ly,-1,1),(lx,ry,1,-1),(rx,ry,-1,-1)]:
            canvas.create_line(cx, cy, cx+dx*clen, cy, fill=self._ACCENT, width=4, tags='sel')
            canvas.create_line(cx, cy, cx, cy+dy*clen, fill=self._ACCENT, width=4, tags='sel')

        pw = int((rx - lx) * scale_x)
        ph = int((ry - ly) * scale_y)
        canvas.create_text(lx + (rx - lx) // 2, min(ry + 22, H - 8),
            text=f'{pw} × {ph} px', fill=self._ACCENT,
            font=('Helvetica', 13, 'bold'), tags='sel')
        canvas.create_rectangle(0, 0, W, 36, fill=self._BAND, outline='', tags='band')
        canvas.create_text(W // 2, 18,
            text='Release to confirm  •  ESC to cancel',
            fill='white', font=('Helvetica', 13, 'bold'), tags='band')


# Theme palette (_BG, _BG_PANEL, _BG_BAR, _ACCENT) and card_badge_color are
# imported at the top from theme.py / counting.py.


# ─── Main Overlay App ─────────────────────────────────────────────────────────

class AutoCounterApp:
    """The always-on-top overlay: builds the UI, owns the CardCounter, and drives
    a DetectionLoop. Detection runs on a background thread; every callback from it
    marshals back onto the Tk main thread via root.after before touching widgets."""

    ENGINE_YOLO   = 'yolo'
    ENGINE_CLAUDE = 'claude'

    # Scan-button colour palettes for its two states.
    _BTN_START_STYLE = dict(bg='#0a3a0a', fg='#00ff88',
                            activebackground='#154a15', activeforeground='#00ff88')
    _BTN_STOP_STYLE  = dict(bg='#3a0a0a', fg='#ff4444',
                            activebackground='#4a1010', activeforeground='#ff4444')

    _PEN_TRACK = '#111122'   # penetration-bar background track colour
    _TEST_MAX_ROWS = 20      # max detection rows shown in the Test Shot dialog

    def __init__(self) -> None:
        self.counter = CardCounter()
        self.cfg     = _load_cfg()
        self.region: dict | None = self.cfg.get('region')

        self._loop:          DetectionLoop | None = None
        self._yolo_detector: YOLODetector  | None = None
        self._yolo_ready     = False
        self._engine         = self.cfg.get('engine', self.ENGINE_YOLO)
        self._dx = self._dy  = 0
        self._blink_on       = False
        self._recent: deque[str] = deque(maxlen=10)

        self.root = tk.Tk()
        self.root.title('♠ Auto Card Counter')
        self.root.configure(bg=_BG)
        self.root.attributes('-topmost', True)
        self.root.attributes('-alpha', 0.94)
        self.root.resizable(False, False)

        self._build_ui()
        self._bind_keys()

        # Size window to fit content, capped at screen height minus menu bar + dock
        self.root.update_idletasks()
        req_h    = self.root.winfo_reqheight()
        screen_h = self.root.winfo_screenheight()
        win_h    = min(req_h + 4, screen_h - 90)
        self.root.geometry(f'340x{win_h}+30+25')

        self.root.after(100, self._refresh)
        self.root.after(500, self._tick)

        threading.Thread(target=self._load_yolo, daemon=True).start()

    # ── YOLO loading ──────────────────────────────────────────────────────────

    def _set_yolo_status(self, msg: str) -> None:
        self.root.after(0, lambda: self._yolo_lbl.config(text=f'● {msg}', fg='#aaaa44'))

    def _load_yolo(self) -> None:
        try:
            path = _download_yolo_model(self._set_yolo_status)
            self._set_yolo_status('Initialising YOLO…')
            self._yolo_detector = YOLODetector(path)
            self._yolo_ready    = True
            def _done():
                self._yolo_lbl.config(text='● YOLO ready  ✓', fg='#337744')
                self._update_start_btn()
            self.root.after(0, _done)
        except Exception as exc:
            err = str(exc)
            self.root.after(0, lambda: self._yolo_lbl.config(
                text=f'● YOLO failed: {err}', fg='#ff4444'))

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        r = self.root

        # Title / drag bar
        bar = tk.Frame(r, bg=_BG_BAR, cursor='fleur')
        bar.pack(fill='x', padx=2, pady=2)
        bar.bind('<Button-1>',  self._drag_start)
        bar.bind('<B1-Motion>', self._drag_move)
        tk.Label(bar, text='♠  AUTO CARD COUNTER  ♠',
                 font=('Helvetica', 11, 'bold'), fg=_ACCENT, bg=_BG_BAR
                 ).pack(side='left', padx=10, pady=6)
        df = tk.Frame(bar, bg=_BG_BAR)
        df.pack(side='right', padx=8, pady=4)
        tk.Label(df, text='Decks:', font=('Helvetica', 9), fg='#8899bb', bg=_BG_BAR).pack(side='left')
        self._deck_var = tk.StringVar(value=str(self.cfg.get('decks', 6)))
        om = tk.OptionMenu(df, self._deck_var, '1', '2', '4', '6', '8',
                           command=self._deck_change)
        om.config(bg='#22224a', fg='white', font=('Helvetica', 9),
                  bd=0, highlightthickness=0, relief='flat', activebackground='#33335a')
        om['menu'].config(bg='#22224a', fg='white', activebackground=_ACCENT)
        om.pack(side='left')

        # Status bar
        sf = tk.Frame(r, bg=_BG_PANEL)
        sf.pack(fill='x', padx=2, pady=(0, 2))
        self._dot = tk.Label(sf, text='●', font=('Helvetica', 11),
                             fg='#2a2a44', bg=_BG_PANEL)
        self._dot.pack(side='left', padx=(8, 4), pady=4)
        self._status = tk.Label(sf, text='Initialising…',
                                font=('Helvetica', 8), fg='#556677',
                                bg=_BG_PANEL, anchor='w', wraplength=270, justify='left')
        self._status.pack(side='left', fill='x', expand=True, pady=4)

        # Running count
        tk.Label(r, text='RUNNING COUNT', font=('Helvetica', 8, 'bold'),
                 fg='#334455', bg=_BG).pack(pady=(6, 0))
        self._rc_lbl = tk.Label(r, text='0', font=('Helvetica', 56, 'bold'),
                                fg='#dddddd', bg=_BG)
        self._rc_lbl.pack(pady=(0, 0))

        # True count
        tk.Label(r, text='TRUE COUNT', font=('Helvetica', 8, 'bold'),
                 fg='#334455', bg=_BG).pack()
        self._tc_lbl = tk.Label(r, text='0.0', font=('Helvetica', 26, 'bold'),
                                fg='#8899ff', bg=_BG)
        self._tc_lbl.pack()

        # Bet recommendation
        betf = tk.Frame(r, bg=_BG_PANEL)
        betf.pack(fill='x', padx=12, pady=(4, 2))
        tk.Label(betf, text='BET RECOMMENDATION', font=('Helvetica', 8, 'bold'),
                 fg='#334455', bg=_BG_PANEL).pack(pady=(4, 0))
        self._bet_lbl = tk.Label(betf, text='→  KEEP BET',
                                 font=('Helvetica', 16, 'bold'), fg='#aaaaaa', bg=_BG_PANEL)
        self._bet_lbl.pack(pady=(1, 5))

        # Penetration bar
        pf = tk.Frame(r, bg=_BG)
        pf.pack(fill='x', padx=12, pady=(2, 0))
        tk.Label(pf, text='Shoe Penetration', font=('Helvetica', 8),
                 fg='#334455', bg=_BG).pack(side='left')
        self._pen_lbl = tk.Label(pf, text='0%', font=('Helvetica', 8),
                                 fg='#334455', bg=_BG)
        self._pen_lbl.pack(side='right')
        self._pen_canvas = tk.Canvas(r, height=6, bg=self._PEN_TRACK, bd=0, highlightthickness=0)
        self._pen_canvas.pack(fill='x', padx=12, pady=(2, 2))

        # Edge + stats
        ef = tk.Frame(r, bg=_BG)
        ef.pack(fill='x', padx=14)
        self._edge_lbl  = tk.Label(ef, text='Edge: -0.5%', font=('Helvetica', 9),
                                   fg='#445566', bg=_BG)
        self._edge_lbl.pack(side='left')
        self._hands_lbl = tk.Label(ef, text='Hands: 0', font=('Helvetica', 9),
                                   fg='#334455', bg=_BG)
        self._hands_lbl.pack(side='left', padx=8)
        self._cards_lbl = tk.Label(ef, text='Cards: 0 / 312', font=('Helvetica', 9),
                                   fg='#445566', bg=_BG)
        self._cards_lbl.pack(side='right')

        # Separator + recent cards
        tk.Frame(r, bg='#1a1a33', height=1).pack(fill='x', padx=8, pady=(6, 3))
        rf = tk.Frame(r, bg=_BG)
        rf.pack(fill='x', padx=12)
        tk.Label(rf, text='Recent:', font=('Helvetica', 8),
                 fg='#334455', bg=_BG).pack(side='left', padx=(0, 4))
        self._badges: list[tk.Label] = []
        for _ in range(10):
            lbl = tk.Label(rf, text='', font=('Helvetica', 9, 'bold'),
                           bg=_BG, fg=_BG, width=2, pady=2)
            lbl.pack(side='left', padx=1)
            self._badges.append(lbl)

        # On table / last counted
        self._on_table_lbl = tk.Label(r, text='On table: —',
                                      font=('Helvetica', 8), fg='#334466', bg=_BG)
        self._on_table_lbl.pack(pady=(2, 0))
        self._last_lbl = tk.Label(r, text='Last counted: —',
                                  font=('Helvetica', 8), fg='#334455', bg=_BG)
        self._last_lbl.pack()

        # Separator
        tk.Frame(r, bg='#1a1a33', height=1).pack(fill='x', padx=8, pady=(5, 3))

        # Engine selector
        ef2 = tk.Frame(r, bg=_BG_PANEL)
        ef2.pack(fill='x', padx=12, pady=(0, 0))
        tk.Label(ef2, text='Detection engine:', font=('Helvetica', 8, 'bold'),
                 fg='#7788aa', bg=_BG_PANEL).pack(side='left', padx=(6, 8), pady=4)
        self._engine_var = tk.StringVar(value=self._engine)
        for label, val in [('🤖 YOLO  (free)', self.ENGINE_YOLO),
                            ('🧠 Claude AI', self.ENGINE_CLAUDE)]:
            tk.Radiobutton(ef2, text=label, variable=self._engine_var, value=val,
                           font=('Helvetica', 8), fg='#aabbcc', bg=_BG_PANEL,
                           selectcolor=_BG_BAR, activebackground=_BG_PANEL,
                           command=self._engine_changed).pack(side='left', padx=4)

        # Claude API key row (hidden when YOLO selected)
        self._api_frame = tk.Frame(r, bg=_BG)
        self._api_lbl   = tk.Label(self._api_frame, font=('Helvetica', 8),
                                   fg='#664433', bg=_BG)
        self._api_lbl.pack(side='left', padx=(12, 4))
        tk.Button(self._api_frame, text='Set API Key',
                  font=('Helvetica', 8), bg='#1a1a3a', fg='#8899bb',
                  activebackground='#252548', bd=0, padx=6, pady=3,
                  cursor='hand2', command=self._set_api_key).pack(side='right', padx=(0, 12))

        # YOLO model status row (hidden when Claude selected)
        self._yolo_frame = tk.Frame(r, bg=_BG)
        self._yolo_lbl   = tk.Label(self._yolo_frame,
            text='● Loading YOLO model…',
            font=('Helvetica', 8), fg='#aaaa44', bg=_BG, anchor='w')
        self._yolo_lbl.pack(side='left', padx=12, pady=2)

        # Show the correct engine panel NOW (before creating start_btn so pack order is right)
        if self._engine == self.ENGINE_YOLO:
            self._yolo_frame.pack(fill='x')
        else:
            self._update_api_lbl()
            self._api_frame.pack(fill='x')

        # START / STOP button
        self._start_btn = tk.Button(
            r, text='▶  START SCANNING',
            font=('Helvetica', 12, 'bold'),
            bd=0, pady=8, cursor='hand2', state='disabled',
            command=self._toggle, **self._BTN_START_STYLE)
        self._start_btn.pack(fill='x', padx=12, pady=(6, 3))

        # Determine whether start should be enabled right now
        self._update_start_btn()

        # Region select
        self._region_btn = tk.Button(
            r, text='⊞  Select Table Region',
            font=('Helvetica', 9, 'bold'), bg='#1a1a3a', fg='#8899ff',
            activebackground='#2a2a5a', bd=0, pady=5, cursor='hand2',
            command=self._select_region)
        self._region_btn.pack(fill='x', padx=12, pady=(0, 2))
        self._region_lbl = tk.Label(r, font=('Helvetica', 8), fg='#334455', bg=_BG)
        self._region_lbl.pack()
        self._update_region_lbl()

        # Test Shot button
        self._test_btn = tk.Button(
            r, text='📸  Test Shot  (saves annotated image to Desktop)',
            font=('Helvetica', 8, 'bold'), bg='#1a2a1a', fg='#557755',
            activebackground='#253525', bd=0, pady=5, cursor='hand2',
            command=self._test_detection)
        self._test_btn.pack(fill='x', padx=12, pady=(2, 0))

        # Undo / New Shoe
        cf = tk.Frame(r, bg=_BG)
        cf.pack(fill='x', padx=12, pady=(4, 0))
        tk.Button(cf, text='↩  Undo  (Z)',
                  font=('Helvetica', 9, 'bold'), bg='#181830', fg='#8899bb',
                  activebackground='#252548', bd=0, padx=8, pady=5, cursor='hand2',
                  command=self._undo).pack(side='left', expand=True, fill='x', padx=(0, 2))
        tk.Button(cf, text='♻  New Shoe  (N)',
                  font=('Helvetica', 9, 'bold'), bg='#0a220a', fg='#77aa77',
                  activebackground='#153815', bd=0, padx=8, pady=5, cursor='hand2',
                  command=self._new_shoe).pack(side='right', expand=True, fill='x', padx=(2, 0))

        # Countdown + opacity
        tk.Frame(r, bg='#1a1a33', height=1).pack(fill='x', padx=8, pady=(5, 0))
        self._countdown_lbl = tk.Label(r, text='', font=('Helvetica', 7),
                                       fg='#223344', bg=_BG)
        self._countdown_lbl.pack()
        af = tk.Frame(r, bg=_BG)
        af.pack(fill='x', padx=10, pady=(0, 8))
        tk.Label(af, text='Opacity:', font=('Helvetica', 8),
                 fg='#2a3a44', bg=_BG).pack(side='left')
        self._alpha_var = tk.DoubleVar(value=0.94)
        tk.Scale(af, from_=0.30, to=1.0, resolution=0.05, orient='horizontal',
                 variable=self._alpha_var,
                 command=lambda v: self.root.attributes('-alpha', float(v)),
                 bg=_BG, fg='#334455', troughcolor='#1a1a33',
                 highlightthickness=0, bd=0, length=190, sliderrelief='flat',
                 showvalue=False).pack(side='right')

    # ── Engine selector logic ──────────────────────────────────────────────────

    def _engine_changed(self) -> None:
        self._engine = self._engine_var.get()
        self.cfg['engine'] = self._engine
        _save_cfg(self.cfg)

        if self._engine == self.ENGINE_YOLO:
            self._api_frame.pack_forget()
            self._yolo_frame.pack(fill='x', before=self._start_btn)
        else:
            self._yolo_frame.pack_forget()
            self._update_api_lbl()
            self._api_frame.pack(fill='x', before=self._start_btn)

        self._update_start_btn()

    def _update_start_btn(self) -> None:
        if self._engine == self.ENGINE_YOLO:
            ready = self._yolo_ready
        else:
            ready = bool(self.cfg.get('api_key'))
        self._start_btn.config(state='normal' if ready else 'disabled')
        if ready:
            self._status.config(text='Press  ▶ START  to begin')

    # ── Key bindings ──────────────────────────────────────────────────────────

    def _bind_keys(self) -> None:
        for k in ('z', 'Z'):      self.root.bind(f'<Key-{k}>', lambda e: self._undo())
        self.root.bind('<BackSpace>', lambda e: self._undo())
        for k in ('n', 'N'):      self.root.bind(f'<Key-{k}>', lambda e: self._new_shoe())
        for key, code in [('a','A'),('2','2'),('3','3'),('4','4'),('5','5'),
                           ('6','6'),('7','7'),('8','8'),('9','9'),('t','T')]:
            self.root.bind(f'<Key-{key}>', lambda e, c=code: self._manual_card(c))

    # ── Window drag ───────────────────────────────────────────────────────────

    def _drag_start(self, e) -> None:
        self._dx, self._dy = e.x, e.y

    def _drag_move(self, e) -> None:
        x = self.root.winfo_x() + e.x - self._dx
        y = self.root.winfo_y() + e.y - self._dy
        self.root.geometry(f'+{x}+{y}')

    # ── Countdown tick ────────────────────────────────────────────────────────

    def _tick(self) -> None:
        if self._loop and self._loop._running:
            remaining = self._loop.next_scan_at - time.monotonic()
            self._countdown_lbl.config(
                text=f'Next scan in {remaining:.1f}s' if remaining > 0.15 else '',
                fg='#223344')
        else:
            self._countdown_lbl.config(text='')
        self.root.after(200, self._tick)

    # ── Start / Stop ──────────────────────────────────────────────────────────

    def _toggle(self) -> None:
        if self._loop and self._loop._running:
            self._stop()
        else:
            self._start()

    def _show_scan_button(self, scanning: bool, engine_label: str = '') -> None:
        """Style the scan button as STOP (while scanning) or START (when idle)."""
        if scanning:
            self._start_btn.config(text=f'⏹  STOP  ({engine_label})', **self._BTN_STOP_STYLE)
        else:
            self._start_btn.config(text='▶  START SCANNING', **self._BTN_START_STYLE)

    def _make_detector(self):
        if self._engine == self.ENGINE_YOLO:
            if not self._yolo_ready:
                messagebox.showinfo('YOLO Loading',
                    'YOLO model is still loading. Please wait.', parent=self.root)
                return None
            return self._yolo_detector
        else:
            api_key = self.cfg.get('api_key', '')
            if not api_key:
                messagebox.showerror('API Key Required',
                    'Enter your Anthropic API key.\nClick "Set API Key".',
                    parent=self.root)
                return None
            return ClaudeDetector(api_key)

    def _start(self) -> None:
        if not self.region:
            messagebox.showinfo('Select Region',
                'Click "Select Table Region" and drag over your casino table.',
                parent=self.root)
            return
        detector = self._make_detector()
        if detector is None:
            return

        self._loop = DetectionLoop(
            capture=ScreenCapture(self.region),
            detector=detector,
            on_card=self._on_card,
            on_status=self._on_status,
            on_new_hand=self._on_new_hand,
            on_stop=self._on_stop,
        )
        self._loop.start()
        eng = 'YOLO' if self._engine == self.ENGINE_YOLO else 'Claude AI'
        self._show_scan_button(scanning=True, engine_label=eng)
        self._blink()

    def _stop(self) -> None:
        if self._loop:
            self._loop.stop()
            self._loop = None
        self._show_scan_button(scanning=False)
        self._dot.config(fg='#2a2a44')
        self._status.config(text='Stopped')
        self._countdown_lbl.config(text='')

    def _blink(self) -> None:
        if not (self._loop and self._loop._running):
            return
        self._blink_on = not self._blink_on
        self._dot.config(fg='#00ff88' if self._blink_on else '#2a2a44')
        self.root.after(700, self._blink)

    # ── Region ────────────────────────────────────────────────────────────────

    def _select_region(self) -> None:
        region = RegionSelector(self.root).select()
        if region:
            self.region = region
            self.cfg['region'] = region
            _save_cfg(self.cfg)
            self._update_region_lbl()

    def _update_region_lbl(self) -> None:
        if self.region:
            r = self.region
            self._region_lbl.config(
                text=f"Region: {r['width']}×{r['height']} px  @ ({r['left']}, {r['top']})",
                fg='#446644')
        else:
            self._region_lbl.config(text='Region: not set — click above ↑', fg='#664433')

    # ── API key ───────────────────────────────────────────────────────────────

    def _set_api_key(self) -> None:
        key = simpledialog.askstring(
            'Anthropic API Key',
            'Enter your Anthropic API key\n(get one at console.anthropic.com):',
            parent=self.root, show='*')
        if key and key.strip():
            self.cfg['api_key'] = key.strip()
            _save_cfg(self.cfg)
            self._update_api_lbl()
            self._update_start_btn()

    def _update_api_lbl(self) -> None:
        key = self.cfg.get('api_key', '')
        if key:
            masked = key[:10] + '…' + key[-4:] if len(key) > 14 else '***'
            self._api_lbl.config(text=f'Claude API: {masked}  ✓', fg='#335533')
        else:
            self._api_lbl.config(text='Claude API key: not set  ⚠', fg='#664433')

    # ── Deck / count controls ─────────────────────────────────────────────────

    def _deck_change(self, v: str) -> None:
        self.counter.num_decks = int(v)
        self.cfg['decks'] = int(v)
        _save_cfg(self.cfg)
        self.counter.reset()
        self._refresh()

    def _undo(self) -> None:
        k = self.counter.undo()
        if k:
            if self._recent:
                self._recent.pop()
            self._last_lbl.config(text=f'Undone: {display_rank(k)}')
            self._update_badges()
            self._refresh()

    def _new_shoe(self) -> None:
        if messagebox.askyesno('New Shoe', 'Reset count for a new shoe?', parent=self.root):
            self.counter.reset()
            self._recent.clear()
            if self._loop:
                self._loop.reset_hand()
            self._last_lbl.config(text='Last counted: —  (New Shoe)')
            self._on_table_lbl.config(text='On table: —')
            self._update_badges()
            self._refresh()

    def _manual_card(self, code: str) -> None:
        val  = self.counter.add_card(code)
        self._recent.append(code)
        disp = display_rank(code)
        sign = signed_prefix(val)
        self._last_lbl.config(text=f'Last (manual): {disp}  ({sign}{val})')
        self._update_badges()
        self._flash_rc()
        self._refresh()

    # ── Detection callbacks ────────────────────────────────────────────────────

    def _on_card(self, key: str) -> None:
        val  = self.counter.add_card(key)
        self._recent.append(key)
        disp = display_rank(key)
        sign = signed_prefix(val)
        text = f'Last counted: {disp}  ({sign}{val})'
        def _update():
            self._last_lbl.config(text=text)
            self._update_badges()
            self._flash_rc()
            self._refresh()
        self.root.after(0, _update)

    def _on_status(self, msg: str) -> None:
        def _update():
            is_err = msg.startswith('⚠')
            self._status.config(text=msg, fg='#ff6644' if is_err else '#556677')
            if is_err:
                self._dot.config(fg='#ff4400')
            if msg.startswith('On table:'):
                self._on_table_lbl.config(text=msg, fg='#446644')
        self.root.after(0, _update)

    def _on_new_hand(self) -> None:
        self.counter.hands_played += 1
        def _update():
            self._on_table_lbl.config(text='↺  New hand started', fg='#667799')
            self._refresh()
        self.root.after(0, _update)

    def _on_stop(self) -> None:
        def _update():
            self._loop = None
            self._show_scan_button(scanning=False)
            self._dot.config(fg='#2a2a44')
            self._countdown_lbl.config(text='')
        self.root.after(0, _update)

    # ── Test Shot ─────────────────────────────────────────────────────────────

    def _test_detection(self) -> None:
        if not self.region:
            messagebox.showinfo('No region', 'Select a table region first.', parent=self.root)
            return
        detector = self._make_detector()
        if detector is None:
            return

        self._test_btn.config(text='Capturing…', state='disabled')
        self.root.update()

        try:
            img      = ScreenCapture(self.region).grab()
            dets, res = detector.debug_detect(img, conf=YOLODetector.DEBUG_CONF)
            dets.sort(key=lambda x: -x[1])

            count_conf = YOLODetector.CONF       # detections at/above this are counted
            debug_pct  = f'{YOLODetector.DEBUG_CONF:.0%}'

            desktop   = Path.home() / 'Desktop' / 'card_detection_test.jpg'
            saved_msg = ''
            if res is not None:
                try:
                    Image.fromarray(res.plot()).save(desktop)
                    saved_msg = '\n\nAnnotated image → Desktop/card_detection_test.jpg'
                except Exception:
                    pass
            else:
                try:
                    img.save(desktop)
                    saved_msg = '\n\nScreenshot → Desktop/card_detection_test.jpg'
                except Exception:
                    pass

            if dets:
                lines = '\n'.join(
                    f'  {"★" if c >= count_conf else "·"} {n:<6} {c:.0%}'
                    for n, c in dets[:self._TEST_MAX_ROWS])
                above = sum(1 for _, c in dets if c >= count_conf)
                msg = (f'{len(dets)} detections at ≥{debug_pct} confidence '
                       f'({above} above counting threshold):\n\n{lines}{saved_msg}\n\n'
                       f'★ = counted   · = below threshold\n\n'
                       f'If cards show as "·", try 🧠 Claude AI engine instead.')
            else:
                msg = (f'Nothing detected at {debug_pct} confidence.\n\n'
                       'Tip: switch engine to 🧠 Claude AI — it works with\n'
                       'any card design from any live casino.\n\n'
                       'Or try zooming your region in closer to the cards.'
                       f'{saved_msg}')

            messagebox.showinfo('Test Shot Result', msg, parent=self.root)

        except Exception as exc:
            messagebox.showerror('Test failed', str(exc), parent=self.root)
        finally:
            self._test_btn.config(
                text='📸  Test Shot  (saves annotated image to Desktop)',
                state='normal')

    # ── Badge strip ───────────────────────────────────────────────────────────

    def _update_badges(self) -> None:
        cards = list(self._recent)
        for i, badge in enumerate(self._badges):
            if i < len(cards):
                k = cards[i]
                bg, fg = card_badge_color(k)
                badge.config(text=k, bg=bg, fg=fg)
            else:
                badge.config(text='', bg=_BG, fg=_BG)

    def _flash_rc(self) -> None:
        flash = '#0d2a10' if self.counter.running_count >= 0 else '#2a0d0d'
        self._rc_lbl.config(bg=flash)
        self.root.after(200, lambda: self._rc_lbl.config(bg=_BG))

    # ── Display refresh ───────────────────────────────────────────────────────

    def _refresh(self) -> None:
        rc  = self.counter.running_count
        tc  = self.counter.true_count
        pen = self.counter.penetration_pct

        self._rc_lbl.config(text=f'{signed_prefix(rc)}{rc}', fg=self.counter.count_color())
        self._tc_lbl.config(text=f'{signed_prefix(tc)}{tc:.1f}')

        bet_txt, bet_clr = self.counter.bet_info()
        self._bet_lbl.config(text=bet_txt, fg=bet_clr)

        edge = self.counter.player_edge()
        self._edge_lbl.config(text=f'Edge: {signed_prefix(edge)}{edge:.1f}%',
                              fg=edge_color(edge))
        self._hands_lbl.config(text=f'Hands: {self.counter.hands_played}')
        total = self.counter.total_cards
        self._cards_lbl.config(text=f'Cards: {self.counter.cards_seen} / {total}')
        self._pen_lbl.config(text=f'{pen:.0f}%')

        c      = self._pen_canvas
        c.delete('all')
        w      = c.winfo_width() or 316
        filled = int(w * pen / 100)
        c.create_rectangle(0, 0, w, 6, fill=self._PEN_TRACK, outline='')
        if filled > 0:
            c.create_rectangle(0, 0, filled, 6, fill=penetration_bar_color(pen), outline='')

    def run(self) -> None:
        self.root.mainloop()


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == '__main__':
    # _check_deps() already ran at import time (above), so dependencies are
    # guaranteed present here.
    AutoCounterApp().run()
