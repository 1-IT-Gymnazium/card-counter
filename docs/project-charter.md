# Project Charter

## Cíl projektu

Card Counter je desktopová aplikace pro trénink počítání karet v blackjacku systémem Hi-Lo. Karty rozpoznává z obrazovky pomocí počítačového vidění a uživateli ukazuje running count, true count a doporučení k sázce.

## Rozsah MVP (duben 2027)

### Už funguje

- ruční overlay (zadávání karet klávesnicí) a automatický overlay (rozpoznávání z obrazovky),
- dva enginy detekce: lokální YOLO a cloudový Claude Haiku,
- počítání Hi-Lo, true count, doporučení k sázce, penetrace balíčku,
- 21 jednotkových testů logiky počítání.

### Do dubna přibude

1. **Vlastní model** – vlastní dataset anotovaný v Roboflow a na něm natrénovaný model YOLO místo modelu staženého z HuggingFace.
2. **Měření přesnosti** – testovací sada snímků a porovnání přesnosti YOLO a Claude.
3. **Tréninkový režim** – uživatel počítá sám, aplikace ho kontroluje a vyhodnotí úspěšnost.

### Mimo rozsah

- hra o skutečné peníze a jakékoli napojení na kasino,
- mobilní aplikace, uživatelské účty, cloudové ukládání,
- jiné karetní hry a jiné počítací systémy než Hi-Lo.

## Cílová skupina

- **Hlavní:** začínající hráči blackjacku, kteří se chtějí naučit počítat karty a trénovat doma.
- **Vedlejší:** studenti a učitelé, pro které je aplikace názornou ukázkou pravděpodobnosti a počítačového vidění.

## Milníky

| Termín | Milník |
| --- | --- |
| prosinec 2026 | vlastní dataset anotovaný v Roboflow |
| únor 2027 | natrénovaný model, změřená přesnost |
| březen 2027 | tréninkový režim |
| duben 2027 | ladění, dokumentace, prezentace MVP |

## Rizika a jejich řešení

| Riziko | Co hrozí | Řešení |
| --- | --- | --- |
| Čas | Maturitní ročník, na projektu pracuje jeden člověk | Jádro už běží; zbývají tři přírůstky s měsíčními milníky |
| Technologie | Vlastní model nerozpozná každý design karet | Záložní engine Claude Vision a ruční režim; přesnost se měří |
| Právo | Softwarové pomůcky při hře porušují podmínky kasin | Výhradně tréninkový nástroj, bez napojení na kasino, upozornění v aplikaci |
| Soukromí a bezpečnost | U enginu Claude odchází snímek obrazovky do cloudu | Výchozí YOLO běží lokálně; snímá se jen zvolený výřez; při počítání se snímky neukládají; API klíč je mimo repozitář |
| Licence | Ultralytics YOLO je pod AGPL-3.0; model třetí strany | Projekt bude open-source pod AGPL-3.0; cizí model nahradí vlastní |

## Business a komunitní potenciál

- **Přínos:** trénink počítání s okamžitou zpětnou vazbou, bez krupiéra a bez peněz.
- **Model:** open-source a zdarma (B2C). Rámcová možnost freemia: placené lekce a dlouhodobé statistiky tréninku.
- **Komunita:** zveřejněný dataset a model karet pro další projekty s počítačovým viděním.
