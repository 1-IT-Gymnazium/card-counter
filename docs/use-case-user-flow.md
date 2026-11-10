# Use case a user flow

## User flow – automatické počítání

```
[ Spuštění aplikace ]
        │
        ▼
[ Hlavní okno overlaye ] ──► (Klikne na výběr výřezu)
        │
        ▼
[ Výběr výřezu ] ──► (Tažením označí blackjackový stůl)
        │
        ▼
[ Volba enginu ] ──► (YOLO zdarma / Claude s API klíčem)
        │
        ▼
[ Start ] ──► (Aplikace snímá stůl a počítá karty)
        │
        ▼
[ Overlay během hry ] ──► (Running count, true count, doporučení k sázce)
        │
        ▼ (Nový balíček)
[ Vynulování počítání ]
```

## Use case – automatické počítání karet

- **Aktér:** Uživatel (hráč, který trénuje počítání karet)
- **Předpodmínky:** Aplikace je spuštěná, má oprávnění ke snímání obrazovky a na obrazovce je blackjackový stůl.
- **Hlavní scénář (Happy Path):**
  1. Uživatel označí výřez obrazovky se stolem.
  2. Zvolí engine detekce.
  3. Klikne na Start.
  4. Systém v pravidelných intervalech snímá výřez a rozpozná odkryté karty.
  5. Systém každou novou kartu jednou započítá a aktualizuje running count a true count.
  6. Systém zobrazí doporučení k sázce.
- **Alternativní / chybové scénáře:**
  - *A1 – Snímek je černý (chybí oprávnění):* Systém zobrazí upozornění, které aplikaci je třeba povolit nahrávání obrazovky, a po chvíli to zkusí znovu.
  - *A2 – Není zvolen výřez:* Systém uživatele vyzve, aby nejdřív označil stůl.
  - *A3 – Neplatný API klíč (engine Claude):* Systém detekci zastaví a vyzve k zadání klíče.
  - *A4 – Model YOLO se ještě načítá:* Systém uživatele požádá, aby počkal.
  - *A5 – Karta je rozpoznána špatně:* Uživatel použije krok zpět nebo kartu zadá ručně.

## Use case – tréninkový režim (plán do dubna)

- **Aktér:** Uživatel
- **Předpodmínky:** Běží automatické počítání.
- **Hlavní scénář:**
  1. Systém skryje aktuální count.
  2. Uživatel počítá sám a na výzvu zadá svůj running count.
  3. Systém jej porovná se svým výsledkem a zobrazí, zda se shodují.
  4. Na konci balíčku systém zobrazí úspěšnost.
- **Alternativní scénář:**
  - *A1 – Uživatel zadá špatný count:* Systém ukáže správnou hodnotu a karty, které rozdíl způsobily.
