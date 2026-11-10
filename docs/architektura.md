# Architektura

## Technologický stack

| Vrstva | Technologie | Proč |
| --- | --- | --- |
| Jazyk | Python 3.10+ | nejlepší ekosystém pro strojové učení, rychlý vývoj |
| Rozhraní | Tkinter | součást Pythonu, okno „vždy navrchu“ bez dalších závislostí |
| Snímání obrazovky | mss | rychlé snímání zvoleného výřezu |
| Zpracování obrazu | Pillow | porovnání snímků, kontrast, zmenšení pro API |
| Lokální detekce | Ultralytics YOLO | běží na zařízení (CPU, Apple MPS, CUDA), zdarma |
| Cloudová detekce | Anthropic API (Claude Haiku) | zvládne libovolný design karet |
| Stažení modelu | huggingface_hub | model se stáhne při prvním spuštění a uloží do cache |
| Testy | pytest | jednotkové testy logiky |

Databáze není potřeba: stav počítání je v paměti, nastavení (výřez, počet balíčků, engine, API klíč) v souboru `~/.auto_card_counter.json`.

## Schéma

```
[ Obrazovka: blackjackový stůl ]
        │  výřez zvolený uživatelem
        ▼
[ ScreenCapture (mss) ]
        │  snímek
        ▼
[ Kontrola snímku (Pillow) ] ──► černý snímek → upozornění na oprávnění
        │  snímek se změnil?        beze změny → detekce se přeskočí
        ▼
[ Detektor ] ── YOLODetector (lokálně)
        │    └─ ClaudeDetector (Anthropic API)
        │  seznam hodnot karet, např. T, 7, 4, A
        ▼
[ DetectionLoop ]  každou kartu započítá jen jednou, pozná novou hru
        │  nové karty
        ▼
[ CardCounter (counting.py) ]  running count, true count, sázka
        │
        ▼
[ Overlay (Tkinter) ]  zobrazení uživateli
```

## Jak spolu části komunikují

1. **ScreenCapture** sejme výřez obrazovky a vrátí obrázek.
2. **DetectionLoop** běží ve vlákně na pozadí. Snímek porovná s předchozím; když se stůl nezměnil, detekci nespouští. Interval snímání je 0,8 s, na klidném stole 2 s.
3. **Detektor** má dvě implementace se stejným rozhraním (`detect(obrázek) → seznam karet`), proto je lze přepínat:
   - `YOLODetector` spustí model lokálně a názvy tříd (např. `10c`, `ah`) převede na hodnoty Hi-Lo,
   - `ClaudeDetector` pošle zmenšený snímek ve formátu JPEG na Anthropic API a odpověď (seznam karet oddělený čárkami) převede na stejné hodnoty.
4. **DetectionLoop** si pamatuje karty, které už na stole viděl, takže každou započítá jen jednou. Když karty ze stolu zmizí, pozná novou hru; počítání pokračuje.
5. **CardCounter** v `counting.py` nemá žádné rozhraní ani síť. Drží running count, počítá true count (running count / zbývající balíčky) a doporučení k sázce. Sdílí ho ruční i automatický overlay.
6. **Overlay** dostává výsledky přes zpětná volání, která se předávají do hlavního vlákna Tkinteru, takže rozhraní nezamrzá.

## Soubory

- `counting.py` – doménová logika
- `card_counter.py` – ruční overlay
- `auto_card_counter.py` – automatický overlay, detektory, detekční smyčka, výběr výřezu
- `theme.py` – barvy
- `test_counting.py` – testy
