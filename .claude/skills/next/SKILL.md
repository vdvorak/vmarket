---
name: next
description: Zjistí stav projektu vmarket a pokračuje — ve fázi 1 orchestruje paralelní implementaci úkolů v ../dominik-web přes worktrees.
disable-model-invocation: true
---

Pokračování projektu vmarket v nové session. Plán fází: `docs/plan.md`, pojmy: `GLOSSARY.md`.

## 1. Stav

Fáze 1 (zobecnění aplikace) se dělá v sousedním repu `../dominik-web`: úkoly jsou `.scratch/vmarket-kontrakt/issues/` a stav určíš z jejich `Status:`, z `git worktree list` a větví `ukol/vmarket-*` tamtéž. Nejdřív domergvané a rozdělané věci (krok 1 postupu v `paralelizace.md`), pak pokračuj.

Když jsou úkoly 01–08 `done`, fáze 1 končí: shrň uživateli, že zbývá úkol 09 (ready-for-human), a další práci ber z `docs/plan.md` (fáze 2 — provisioning engine; úkoly pro vmarket samotný se zakládají jako GitHub Issues podle `CLAUDE.md`).

## 2. Orchestrace fáze 1

Jsi orchestrátor: kód nepíšeš, subagenty zadáváš, kontroluješ a merguješ. Všechno řídí `../dominik-web/.scratch/vmarket-kontrakt/paralelizace.md` — vlny, max 3 subagenti současně ve worktrees, merge pořadí, testovací politika (subagenti jen cílené testy; dlouhé běhy jednou po vlně) a šablona zadání subagenta. Merge a testy vln pouštěj v hlavním checkoutu `../dominik-web`.

## 3. Konec session

Shrň: domergované úkoly, stav vln a testů, co selhalo a proč, commity čekající na push a úkoly `ready-for-human`.
