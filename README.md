# Card Counter

Trenažér počítání karet v blackjacku, který čte karty přímo z obrazovky pomocí počítačového vidění.

Maturitní softwarový projekt · Jáchym Spilka · 1. IT Gymnázium · školní rok 2026/27

> **Upozornění:** Projekt slouží výhradně k tréninku a k výuce pravděpodobnosti. Není určen ke hře o skutečné peníze. Používání softwarových pomůcek při hře porušuje podmínky kasin.

## Co aplikace umí

- **Ruční overlay** (`card_counter.py`) – okno vždy navrchu, karty se zadávají klávesnicí nebo tlačítky.
- **Automatický overlay** (`auto_card_counter.py`) – snímá zvolený výřez obrazovky a karty rozpoznává sám. Dva vyměnitelné enginy:
  - **YOLO** (Ultralytics) – běží lokálně, zdarma, bez připojení k internetu,
  - **Claude Haiku** (Anthropic API) – cloudová detekce, placená, zvládne libovolný design karet.
- **Systém Hi-Lo** – karty 2–6 = +1, karty 7–9 = 0, karty 10/J/Q/K/A = −1.
- **Running count a true count** (running count dělený počtem zbývajících balíčků).
- **Doporučení k sázce**, odhad výhody hráče a ukazatel penetrace balíčku.
- Volba počtu balíčků (1, 2, 4, 6, 8), krok zpět a nový balíček.

## Instalace

Požadavky: Python 3.10 nebo novější (včetně Tkinteru), macOS.

```bash
pip3 install -r requirements.txt
```

Automatický overlay potřebuje na macOS oprávnění **Nahrávání obrazovky** pro aplikaci, ze které se spouští (Nastavení systému → Soukromí a zabezpečení).

## Spuštění

```bash
./run.sh        # ruční overlay
./run_auto.sh   # automatický overlay (YOLO / Claude)
```

Při prvním spuštění automatického overlaye se model YOLO stáhne z HuggingFace a uloží do `~/.cache/card_counter/`. Pro engine Claude je potřeba vlastní API klíč Anthropic; ukládá se mimo repozitář do `~/.auto_card_counter.json`.

### Ovládání ručního overlaye

| Klávesa | Akce |
| --- | --- |
| `2`–`9` | karta dané hodnoty |
| `T` nebo `0` | desítka, J, Q, K |
| `A` | eso |
| `Z` nebo Backspace | krok zpět |
| `N` | nový balíček |

## Struktura repozitáře

| Soubor | Účel |
| --- | --- |
| `counting.py` | Logika Hi-Lo (třída `CardCounter`), bez rozhraní a bez sítě |
| `card_counter.py` | Ruční overlay v Tkinteru |
| `auto_card_counter.py` | Automatický overlay: snímání obrazovky, detekce, detekční smyčka |
| `theme.py` | Společná barevná paleta obou overlayů |
| `test_counting.py` | 21 jednotkových testů logiky počítání |
| `requirements.txt` | Závislosti |
| `run.sh`, `run_auto.sh` | Spouštěcí skripty |
| `docs/` | Dokumentace projektu |

## Testy

```bash
pip3 install pytest
python3 -m pytest test_counting.py
```

## Dokumentace

- [Project Charter](docs/project-charter.md) – rozsah MVP, cílová skupina, rizika
- [Architektura](docs/architektura.md) – stack a komunikace mezi částmi systému
- [Use case a user flow](docs/use-case-user-flow.md)

## Plán do dubna 2027 (MVP)

- [x] Ruční a automatický overlay, Hi-Lo, true count, doporučení k sázce
- [x] Jednotkové testy logiky počítání
- [ ] Vlastní model YOLO natrénovaný na vlastním datasetu (anotace v Roboflow)
- [ ] Změřená přesnost detekce: YOLO vs. Claude na testovací sadě snímků
- [ ] Tréninkový režim: uživatel počítá sám, aplikace ho kontroluje a vyhodnotí

## Licence

Projekt bude vydán jako open-source pod licencí AGPL-3.0, stejnou, jakou má knihovna Ultralytics YOLO, na které je detekce postavena.
