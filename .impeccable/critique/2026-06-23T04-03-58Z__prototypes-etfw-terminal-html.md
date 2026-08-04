---
target: prototypes/ETFW Terminal.html
total_score: 28
p0_count: 0
p1_count: 2
timestamp: 2026-06-23T04-03-58Z
slug: prototypes-etfw-terminal-html
---
## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 3 | Toasts + active states good; "MKT CLOSED" indicator is static, no loading skeletons |
| 2 | Match System / Real World | 3 | Excellent domain fluency (GO/PROBE/WAIT/VETO); heavy jargon for non-E大 newcomers |
| 3 | User Control and Freedom | 2 | DEL on plans/trades is instant with no confirm/undo; tab switch discards planner preview |
| 4 | Consistency and Standards | 4 | Genuinely cohesive component vocabulary across all 6 tabs |
| 5 | Error Prevention | 2 | Toast-only validation; number inputs unbounded (count/price accept negatives, huge values) |
| 6 | Recognition Rather Than Recall | 3 | Text-labeled nav, glossary; must remember to override BASE price to real ETF price |
| 7 | Flexibility and Efficiency | 3 | Sort/filter/row-shortcuts present; no tab hotkeys or command line despite terminal framing |
| 8 | Aesthetic and Minimalist Design | 3 | Strong hierarchy; dense — ticker + status bar + footnotes everywhere edge toward busy |
| 9 | Error Recovery | 2 | "保存失败·请填写完整" names problem vaguely, not the field; no inline recovery |
| 10 | Help and Documentation | 3 | DOCS tab is strong (workflow/glossary/risk); no inline tooltips at point of use |
| **Total** | | **28/40** | **Good (low end) — solid foundation, fix control/error gaps** |

## Anti-Patterns Verdict

**Does this look AI-generated? No — emphatically.** This is a committed, opinionated Bloomberg/terminal aesthetic executed with conviction: IBM Plex Mono with tabular-nums, hairline 1px borders, semantic status squares, no rounded-card-grid slop, deliberate density. It passes both reflex checks — a financial tool that is neither the navy-and-gold fintech default nor the SaaS-cream default. The terminal lane is earned, not decorated on.

**Deterministic scan**: detector flagged 2 side-stripe borders — `.warn` callout (`border-left:3px amber`, line 201) and `#toast` (`border-left:3px green`, line 211). These are the one shared-ban tell present. In a terminal context they're semi-idiomatic, but they're the cheapest thing to neutralize (full hairline border + bg tint, or a leading `!`/status glyph). Low severity.

**Visual evidence**: Rendered RADAR (desktop + mobile) and PLANNER. Mobile responsiveness is genuinely solid — header wraps, nav scrolls horizontally, KPIs reflow to 2-col, ECharts resize cleanly. One chart defect: on PLANNER the legend ("CAPITAL" / "UNREALIZED PNL") collides with the right Y-axis title.

## Overall Impression

This is the rare prototype that already has a point of view. The scatter "opportunity map" — SWEET ZONE markArea + VETO markLine — makes the product thesis (cheap + volatile = grid-worthy) spatially legible in one glance. That's real information design, not a hero-metric template. The biggest opportunity isn't the look; it's the **transactional safety net**: a tool that manages real money deletes records instantly, validates with vague toasts, and lets number fields go out of range. The aesthetic writes a check the interaction layer doesn't yet cash.

## What's Working

1. **A committed, cohesive system.** One semantic vocabulary (square + code + bar + color for go/maybe/wait/no) repeats identically across 6 tabs. The same chip, KPI, table, and button render the same everywhere. This is the Consistency 4.
2. **Domain-fit visualization.** The RADAR scatter and the PLANNER pressure-test crossover chart both encode the actual strategy logic. The pressure test literally visualizes "can your capital survive a full ladder" — the product's core risk discipline made visible.
3. **Strong contrast on the colors that carry meaning.** Status green/amber/red all clear 5.6–8.9:1 even as text. The signal layer is readable, which is exactly where it matters in a dense terminal.

## Priority Issues

- **[P1] Destructive actions with no confirmation or undo**
  - **Why it matters**: `DEL` on PLANS and TRADES removes the record immediately with only a toast (`计划已删除`). A user managing real grid plans / trade history can wipe data with one misclick and no recovery. This is the "would a user contact support?" line — yes.
  - **Fix**: Inline confirm (two-step button → "确认删除?") or an undo affordance in the toast (`已删除 · 撤销`). Keep it inline; don't add a modal.
  - **Suggested command**: `/impeccable harden`

- **[P1] Validation is toast-only with unbounded inputs**
  - **Why it matters**: Forms surface errors as a transient toast that names no field ("请填写完整" / "参数错误·请检查输入"). Number inputs (`g-count`, `g-base`, `t-price`, `t-shares`) have no `min`/`max`/`step` guards, so a negative count or 0 base silently produces a broken ladder. Error Prevention and Recovery are the two lowest scores for this reason.
  - **Fix**: Inline field-level errors anchored to the offending input; add `min`/`max` and required constraints; validate on blur, not just submit.
  - **Suggested command**: `/impeccable harden`

- **[P2] The `--faint` text tier fails WCAG AA**
  - **Why it matters**: `--faint` (#5A6472) measures 3.13:1 on `--bg` and 2.86:1 on panels — below the 4.5:1 floor. It carries ts_codes, footnotes, chart axis labels, and the "AS OF" date. The supplementary layer of a data terminal is exactly what a low-vision user squints at.
  - **Fix**: Lift `--faint` toward ~#727C8B (≈4.5:1) or reserve it strictly for ≥18px / non-essential decoration. `--muted` already passes — close the gap.
  - **Suggested command**: `/impeccable colorize`

- **[P2] The terminal promises keyboard speed it doesn't deliver**
  - **Why it matters**: The whole frame says "power tool" (mono, codes, MKT CLOSED status line) but the only keyboard path is Enter-to-search. No tab hotkeys, no `:`/`/` command line, no Esc to dismiss. Alex (power user) feels the gap immediately; the aesthetic sets an expectation the interaction breaks.
  - **Fix**: Hotkeys for the 6 tabs (e.g. `g r`, or `1–6`), `/` to focus search, `Esc` to clear. A command line is optional but on-brand.
  - **Suggested command**: `/impeccable harden`

- **[P3] Chart label collision + static status indicator**
  - **Why it matters**: On PLANNER the legend overlaps the right Y-axis title; the header "MKT CLOSED" dot is permanently amber regardless of state. Small, but they're the kind of detail that separates "prototype" from "product."
  - **Fix**: Move chart legend left or drop the duplicate axis name; drive the market indicator from a time check (or label it explicitly as mock).
  - **Suggested command**: `/impeccable polish`

## Persona Red Flags

**Alex (Power User)**: Lands in a terminal, reaches for hotkeys — none. Can't switch RADAR→PLANNER without the mouse. No Esc, no command line. The click-row-to-planner shortcut is the one thing that lands; everything else forces the cursor.

**Sam (Accessibility)**: ts_codes, footnotes, and axis labels in `--faint` fail AA (3.13:1). Status is encoded by color square + a text code (good — not color alone). Focus-visible rings exist (green outline) — a real positive. But ECharts canvas content is invisible to a screen reader with no table fallback.

**Riley (Stress Tester)**: Sets COUNT to 0 or BASE to a negative — no guard; the ladder generator runs on garbage. Deletes a plan, no undo. Switches tabs mid-preview, the planner viz resets with no state retained. Empty search shows a hint, which is handled well.

**"老王" (retail E大 follower, project persona)**: The target user — a self-directed Chinese retail investor following the ETF拯救世界 method. DOCS, glossary, and the repeated "低估≠不会跌 / 资金要扛得住满仓" risk warnings serve him genuinely well. Risk: the dense bilingual EN/CN labeling (VAL %ILE, ANN VOL) assumes more comfort with English abbreviations than this audience may have; the Chinese sub-labels mitigate but the primary column heads are English.

## Minor Observations

- DOCS uses `01 / 02 / 03 / 04 / 05` section numbers, but only `03 WORKFLOW` is actually sequential. Numbered scaffolding on non-ordered sections (ABOUT, GLOSSARY, RISK) is decorative — drop the numbers or keep them only on the 5-step workflow.
- `disposeCharts()` + full `innerHTML` re-render on every tab switch means PLANNER preview state is lost on navigation. Consider preserving the last preview.
- Forms have no loading/disabled state on SAVE/PREVIEW (acceptable for mock, but the real version will need it).
- Empty states ("NO MATCH", "暂无计划") are present and correctly styled — good baseline, but they only say "nothing here" rather than teaching the next action.

## Questions to Consider

- What would a confident keyboard story look like for a tool that calls itself a terminal?
- The pressure test is the product's conscience. Should it be harder to skip — e.g. a plan can't be SAVED until the pressure test has been viewed?
- Should destructive actions on real money ever be one click, even in a fast tool?
- The English column heads + Chinese sublabels: which language is the primary for 老王, and is the other one earning its space?
