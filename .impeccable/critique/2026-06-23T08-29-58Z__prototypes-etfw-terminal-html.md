---
target: prototypes/ETFW Terminal.html
total_score: 34
p0_count: 0
p1_count: 0
timestamp: 2026-06-23T08-29-58Z
slug: prototypes-etfw-terminal-html
---
## Design Health Score (re-run after harden + colorize + polish)

| # | Heuristic | Was | Now | Note |
|---|-----------|-----|-----|------|
| 1 | Visibility of System Status | 3 | 3 | Dynamic market clock + semantic toasts + aria-live added; still no loading skeletons (mock app) |
| 2 | Match System / Real World | 3 | 3 | Unchanged — column heads still English-first with Chinese sublabels |
| 3 | User Control and Freedom | 2 | 4 | Two-step delete + undo, Esc universal cancel, command line, full keyboard nav |
| 4 | Consistency and Standards | 4 | 4 | New cmd/confirm/validation components reuse the existing vocabulary |
| 5 | Error Prevention | 2 | 4 | min/max/step constraints, inline validation, confirm-before-destruct |
| 6 | Recognition Rather Than Recall | 3 | 3 | Command palette + `?` panel aid discovery; base-price recall caveat remains |
| 7 | Flexibility and Efficiency | 3 | 4 | Tab hotkeys, `:` command line, `/` search focus — power layer without complicating basics |
| 8 | Aesthetic and Minimalist Design | 3 | 3 | Cleaner DOCS + fixed chart labels; density unchanged |
| 9 | Error Recovery | 2 | 3 | Specific inline field errors + semantic error toasts + undo; messages state constraints, not always the exact fix |
| 10 | Help and Documentation | 3 | 3 | Shortcuts cheat sheet + command help added; no inline tooltips on dense metrics yet |
| **Total** | | **28** | **34/40** | **Good (upper end)** |

## Anti-Patterns Verdict

Detector: **0 findings** (exit 0). The two side-stripe borders (`.warn`, `#toast`) are resolved — full hairline borders + leading status square. The command-palette selection indicator was rebuilt from a 2px left-border to a caret + background to stay clear of the side-stripe ban. The terminal aesthetic is fully intact.

## What Changed Since Last Run

- **The two P1s are closed.** Destructive deletes now require a two-step confirm and offer undo; validation is inline + field-level with `min`/`max`/`step` guards. These were the highest-user-impact gaps.
- **The terminal framing is now real.** `1–6`, `:`, `/`, `?`, `Esc` all work; the command line filters live and is on-brand.
- **Contrast passes.** `--faint` lifted to #7E8896 → 5.24 / 4.77 / 4.51 across bg/panel/panel2, all ≥ 4.5.

## Top Remaining Issues

- **[P2] English-first column heads** (Heuristic 2). VAL %ILE / ANN VOL / SCORE lead; Chinese is the sublabel. For the 老王 retail persona, consider which language is primary. → `/impeccable clarify`
- **[P3] Planner preview state lost on tab switch.** `disposeCharts()` + full re-render drops the last preview when navigating away and back.
- **[P3] No loading/skeleton states.** Acceptable for a mock, but the real data-backed version will need them (Heuristic 1 ceiling).
- **[P3] No inline tooltips on dense metrics** (VAL %ILE vs PE %ILE at point of use). DOCS glossary covers it, but not contextually.

## Persona Re-check

**Alex (Power User)**: Now lands and immediately has `1–6`, `:`, `/`. The earlier "no keyboard path" red flag is gone. Only nit: no `g`-prefixed vim-style combos, but the digit + command line cover it.

**Sam (Accessibility)**: Contrast passes; charts carry `role="img"` + labels with table fallbacks; skip link + focus rings present. Remaining: command-line list could use richer `aria-activedescendant` wiring, but `role=listbox`/`option`/`aria-selected` are in place.

**Riley (Stress Tester)**: COUNT=0 / negative BASE now blocked with inline errors; delete is reversible. The earlier "runs on garbage / no undo" flags are closed. Tab-switch-mid-preview still resets the viz (now a P3, not a data-loss issue).
