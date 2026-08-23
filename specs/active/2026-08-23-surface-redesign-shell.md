# Surface redesign — the shell

**Status:** draft, awaiting review
**Owner:** Muizzkolapo
**Date:** 2026-08-23
**Depends on:** nothing — every region below is buildable against the app as it
exists today. Some content (project name display, mart recommendation grounding,
per-layer materialization reasons) upgrades automatically if/when a target-project
loader is built later; none of it is blocked waiting for that.

This document is self-contained. It does not assume access to the private planning
notes this spec was distilled from — anyone reading only this file has what they
need to implement it.

---

## 1. Mission — why this redesign, not just what

> "The idea is a training wheel for dbt users — get them using dbt from day one,
> as long as they understand SQL, since we can help them build."

That sentence is the tiebreaker for every ambiguous call in this document. This
tool's job is not "convert SQL to dbt models" — sqlglot already does the mechanical
part. Its job is to take someone who can write a `SELECT` and get them *building a
real dbt project* without first reading the dbt docs, and without the tool lying to
them about their own progress along the way.

That reframes what "redesign" means here. It is not a skin change. Two audiences
use this tool and need opposite things from it:

- **A SQL-literate dbt newcomer** needs the tool to teach as it converts — what a
  mart is, why staging is often legitimately empty, what `source()` resolves to.
  The explanation *is* the product, not decoration around it.
- **A dbt-fluent user** already knows all of that. For them, ten sequential screens
  to set a description, a materialization, and a tag on twelve models is worse than
  editing YAML by hand. Making them click through the newcomer's lesson plan every
  time is how a tool gets abandoned.

**Guided/Direct mode is the product's answer to having two audiences, not a
UI nicety.** Everything else in this shell — the right rail's "why this step
exists," the honest three-state step dots, the empty-state copy that turns
"nothing here" into a lesson — exists to make Guided mode actually teach, so that
someone graduates from Guided to Direct over their first few conversions instead
of bouncing off the tool entirely.

---

## 2. Scope

**In scope:** the application shell — top bar, phase bar, left rail (steps),
canvas frame, right rail, footer, the Guided/Direct mode split and its Workbench,
the diff drawer, deploy failure/recovery states, two small modals. All ten existing
conversion steps keep their step identities, their order, and (for five of them)
their current guided-mode logic unchanged — this redesign changes how the tool
presents itself and teaches, not what it computes.

**Out of scope, named explicitly so no one mistakes silence for oversight:**

- **A target-dbt-project loader** (reading `dbt_project.yml`, detecting real naming
  conventions from an existing repo). Real, valuable, independent — its own spec.
  Three places in this shell degrade gracefully without it (§7.2, §9.3, §11) and
  upgrade automatically once it exists.
- **Per-line diff explanations** ("why this line changed," per row in the diff
  drawer). The diff drawer itself ships in this spec; per-line tips need the SQL
  rewriter to emit provenance it doesn't emit today. Separate spec.
- **Any change to the SQL parser, the generated dbt output, or the git/PR
  mechanics.** This spec touches presentation and state, never the conversion
  logic itself.
- **Reordering, adding, or removing conversion steps.** All ten stay.

**Definition of done:** a SQL-literate newcomer can complete a conversion in
Guided mode without leaving the app to look anything up, and a dbt-fluent user can
complete the same conversion in Direct mode without reading a sentence of
explanation. The step rail never claims a step is done that hasn't actually been
answered.

---

## 3. Design principles

1. **Teach by default, get out of the way on request.** Guided is the default
   mode. Direct is opt-in and, once chosen, persists — a returning expert user
   should never have to re-opt-out.
2. **The interface never lies about state.** A step is "done" because its real
   data says so, never because the user scrolled past it. (This machinery already
   shipped — §6.1.)
3. **Empty states teach.** A layer with nothing in it says *why*, not just that
   it's empty. "No staging models" is a fact about the SQL, and a good one to
   explain, not a blank panel.
4. **Nothing is written to git before deploy.** True today; the shell states it
   plainly on Home and implicitly through every step before step 10.
5. **Read the target project's real conventions where they exist; degrade
   honestly where they don't.** Never fabricate a project name, a recommendation
   reason, or a materialization justification the tool didn't actually derive.
6. **One explanation system, not two.** The tour and the right rail cover the same
   ground; keeping both means maintaining two descriptions of the same ten steps
   that will drift. This spec retires the tour.

---

## 4. Current state (brief — see the app for ground truth)

Shipped and load-bearing for this spec:

- **Three-state step model** — `blocked` / `settled` / `defaulted` — computed by
  `validateStepCompletion()` / `getStepState()` / `getBlockedStepIds()` /
  `getSettledStepCount()` in `static/js/validation.js`, driving the left rail via
  `renderConversionSteps()` in `static/js/main.js`. This is the mechanism behind
  principle 2 above; the shell restyles its presentation but does not touch its
  logic.
- **Home screen** (`static/js/home.js`, the `#empty-state` region) — three intake
  routes (folder, file, paste), the anatomy explainer, the privacy line. This spec
  adds a fourth intake route (existing queries) and otherwise leaves Home
  untouched — Home is `view: 'home'`; everything in this spec is `view: 'app'`.
- **Ten step renderers** under `static/js/steps/*.js`, each registered in
  `dbt_training_wheels/config.py`'s `CONVERSION_STEPS` with an `id`, and each
  reading/writing the shared `modelConfigurations` / `analysisResults` state via
  helpers in `static/js/utils.js` (`getAllModels()`, `getSavedDescription()`,
  `updateModelConfig()`).
- **Vendored Tailwind** (`static/css/tailwind.css`, generated, committed) plus
  hand-written `static/css/styles.css`. No feature-flag system exists in the app;
  this spec doesn't add one (§13).

What does **not** exist yet, and is entirely new in this spec: everything in §7–11.

---

## 5. Architecture

### 5.1 State

One new state axis. Everything else in the shell is computed from state that
already exists.

```
mode: 'guided' | 'direct'      NEW — persisted, default 'guided'
step: string (step id)          EXISTING — currentStep in main.js / StepRegistry
```

`vol` (folder vs. single-file) is **not** new persisted state — it's a fact
knowable from how the current query was uploaded, read once at render time from
`currentQuery`, never toggled by the user (§7.1 explains why the mockup's
live-switching Volume control doesn't carry over).

**`mode` plumbing, concretely:**

1. `static/js/state.js`: add `'mode'` to `PERSISTENT_STATE_KEYS` (the same list
   `'stepCompletionState'` is already in), and to `AppState`'s defaults, value
   `'guided'`.
2. `static/js/shell.js` (new): `setMode(next)` validates `'guided'|'direct'`,
   calls `appState.set('mode', next, {session: true})` — the same call shape
   `updateModelConfig()` already uses — then calls `updateStepNavigation()`.
3. `updateStepNavigation()` (`main.js`) gains one branch: if
   `appState.get('mode') === 'direct'` **and** `currentStep` is one of
   `layer-staging | layer-intermediate | layer-mart | materialization | tags`,
   render the Workbench (§9) instead of that step's own `renderFn`. Every other
   step (`analyze`, `cross-project-refs`, `sources`, `review`, `deploy`) renders
   identically regardless of mode.

No other file needs to know `mode` exists.

### 5.2 File inventory

**New:**

| File | Purpose |
|---|---|
| `static/css/shell.css` | Tokens (§6) plus every shell region's layout. |
| `static/js/shell.js` | `mode` state, phase-pill grouping, step-copy lookup tables (§8), top bar / phase bar / right rail / footer rendering, keyboard shortcuts, the two modals. |
| `static/js/workbench.js` | Direct-mode table (§9). |
| `static/js/diff.js` | Diff drawer (§10). |
| `tests/js/check-shell-state.mjs` | Pure-logic checks for `shell.js` (phase-pill worst-state-wins, mode-branch routing, copy-lookup never throws on an unknown step id) — node builtins only, same pattern as the two existing `tests/js/check-*.mjs` files. |

**Rewritten:**

| File | Change |
|---|---|
| `templates/index.html` | `#main-content`'s children replaced with the shell skeleton (§7). `#step-breadcrumb` and its contents removed (superseded three ways over — top bar, phase pills, left rail). New `<link>`/`<script>` tags for the five new files, `shell.js` ordered after `validation.js` (reads step state) and before `main.js`. |
| `static/js/main.js` | `renderConversionSteps()` restyled to §6/§7.4 tokens and geometry. `selectQuery()` writes conversion title/meta to the new top-bar location instead of `#query-name`/`#query-dataset`. `updateStepNavigation()` gains the mode branch (§5.1). |
| `static/js/state.js` | `'mode'` added per §5.1. |
| `static/js/tour.js`, `static/css/tour.css` | **Deleted** (principle 6, §4 decision). `tour-btn` and its wiring in `main.js`/`index.html` removed with them. |
| `static/css/styles.css` | Unchanged until §12's merge step. |

### 5.3 Existing element ids — what happens to each

The tour and drag-and-drop both silently broke once already in this project from
an id being repurposed without checking every consumer first. This table exists
so that doesn't happen a third time.

| id | Owned by today | Disposition |
|---|---|---|
| `sidebar`, `scheduled-queries-section`, `query-tree`, `sidebar-toggle-btn`, `sidebar-upload-area`, `sidebar-file-input`, `sidebar-folder-input` | `main.js` query browsing | **Removed from the in-app view.** Query browsing becomes Home's fourth intake tab (§7.5) instead of a persistent sidebar. `renderQueryTree()` and friends are retargeted to render inside the new Home tab panel rather than deleted — the function's logic (build the list, handle selection) doesn't change, only where it mounts. |
| `empty-state` and everything Phase 2 built inside it (`home-*`, `upload-area`, `file-input`, `folder-input`, `upload-progress`) | `home.js` | **Kept as-is**, plus the new fourth tab. Out of scope otherwise (§2). |
| `main-content` | `main.js:866-867` visibility toggle | **Becomes the shell's outer frame.** Div survives; children replaced wholesale. |
| `query-name`, `query-dataset` | set by `selectQuery()` | **Retired**, replaced by the top bar's title/meta (§7.1). `selectQuery()` writes to the new location. |
| `header-controls`, `sql-preview-btn`, `toggle-sql-text`, `prerequisite-toggle-btn`, `glossary-btn`, `export-btn` | assorted, `main.js` | **Redistributed.** `glossary-btn` → top bar, beside Mode. `sql-preview-btn`/`prerequisite-toggle-btn` → canvas header or right rail, per step. `export-btn` → footer. |
| `tour-btn` | tour | **Deleted** (§5.2). |
| `sql-preview`, `original-sql`, `fullscreen-sql-modal` + children | SQL preview panel | **Kept, relocated**, standalone — not merged into the diff drawer or Inspect panel in this pass. Don't couple two builds. |
| `step-breadcrumb`, `breadcrumb-step-number`, `breadcrumb-step-name` | breadcrumb | **Deleted.** Redundant against top bar + phase pills + left rail. |
| `conversion-steps-overview`, `conversion-steps` | left rail, `renderConversionSteps()` | **Restyled in place**, logic unchanged (§5.2, §7.4). |
| `step-content` | every `steps/*.js` renderer | **Becomes the canvas body**, wrapped by the new header/right-rail frame. Every existing renderer keeps writing into this id unmodified for the first slice of this build (§13); this is what makes an incremental rollout possible at all. |

**New ids introduced:** `shell-topbar`, `shell-phasebar`, `shell-rightrail`,
`shell-footer`, `shell-workbench` (mounted in place of `step-content`'s renderer
call when Direct mode + a Workbench-eligible step), `shell-diff-drawer`,
`shell-shortcuts-modal`, `shell-reanalyze-confirm`.

---

## 6. Design tokens

Extracted from the reference mockup's own inline styles (not eyeballed), and the
only visual language this spec's new markup uses. Existing hand-written CSS
(`styles.css`) keeps its current tokens until §12.

### 6.1 Type

Two families: **Ubuntu** for prose, **Ubuntu Mono** for anything that names a real
thing in the system — model names, file paths, YAML, branch names, step numbers,
counts. Never mixed mid-sentence. Self-host both fonts (no Google Fonts CDN — this
app makes no other outbound requests and the Home screen's privacy claim shouldn't
have an asterisk).

Weight is **400 for body copy, 500 for anything acting as a control or label**
(buttons, active tab, headings). No 600/700 anywhere. Emphasis comes from size and
color, not boldness — a real constraint to hold when re-skinning existing
`font-semibold`/`font-bold` usage.

Scale (px): `10/10.5` step numbers · `11/11.5` labels/hints · `12/12.5` the
workhorse size for buttons/cells/most UI text · `13/13.5` step titles/card body ·
`14/15` panel headers/footer prose · `16-19` drawer titles/stat numbers · `22` step
header title · `24/26` review stat numbers · `30` step number in canvas header ·
`40` Home headline (already built).

### 6.2 Color

Neutrals dominate. Every non-neutral color is a **state**, never decoration.

```css
--ink:           #0f172a;  /* primary text, active state */
--ink-soft:      #334155;  /* secondary text on colored surfaces */
--muted:         #475569;  /* body copy, descriptions */
--muted-2:       #64748b;  /* labels, meta */
--faint:         #94a3b8;  /* placeholders, tertiary meta */
--border:        #e2e8f0;  /* the one border color */
--border-strong: #cbd5e1;  /* input borders, hover borders */
--surface:       #ffffff;
--surface-sunk:  #f8fafc;  /* canvas/rail background */
--surface-hover: #f1f5f9;

--layer-staging:      #2563eb;
--layer-intermediate: #d97706;
--layer-mart:          #16a34a;

--state-blocked:   #dc2626;  /* filled dot */
--state-settled:   #16a34a;  /* filled dot */
--state-defaulted: #cbd5e1;  /* outline only, no fill */

--warn:          #d97706;
--warn-open:     #b45309;
--danger-bg:     #fef2f2;
--danger-border: #fecaca;
--danger-text:   #dc2626;
--success-bg:    #dcfce7;
--success-text:  #15803d;
--diff-removed-bg:   #fee2e2;
--diff-removed-text: #b91c1c;
--diff-unchanged-bg: #f2ece3;
```

**This app's existing `--brand-primary`/`--notion-*` tokens (`styles.css`) do not
match this palette** and are not replaced by this spec — §12 handles that merge,
deliberately after every consumer of the old tokens is rebuilt, so nothing
visually clashes mid-migration. `shell.css` is additive and self-contained until
then.

### 6.3 Elevation, radius, geometry

```css
--radius-sm: 6px; --radius-md: 7px; --radius-lg: 8px; --radius-xl: 9px;
--radius-2xl: 10px; --radius-3xl: 12px; --radius-full: 20px;

--shadow-tab: 0 1px 2px rgba(15,23,42,.06);
--shadow-modal: 0 24px 60px rgba(28,26,23,.24);
--shadow-modal-heavy: 0 24px 60px rgba(28,26,23,.28);
--shadow-drawer: -20px 0 60px rgba(28,26,23,.22);
```

Borders are `1px solid var(--border)` everywhere except: active-tab (no border,
uses `--shadow-tab`), input focus (`1px solid var(--border-strong)`), and three
deliberate accent borders (Workbench row's layer-colored left border, review stat
card's colored top border, alert banner's red/amber border).

```
Top bar:      52px tall, full width
Phase bar:    46px tall, full width, directly under the top bar
Left rail:    264px wide, fixed
Canvas:       flexible, content capped per region (1100px canvas body / review,
              1000px sources-YAML two-column, 860px deploy, 640px empty-state card)
              — never edge-to-edge, even on wide viewports
Right rail:   340px wide, fixed
Diff drawer:  min(1120px, 92vw), full height, slides from the right
Modals:       520px (shortcuts) / 460px (re-analyze confirm), centered
```

### 6.4 Segmented control (Mode toggle)

```
track: background #f1f5f9, border 1px solid #e2e8f0, radius 10px, padding 3px, gap 2px
tab:   radius 8px, padding 5px 13px, font-size 12.5px, weight 500
active: white background, 1px solid #e2e8f0, shadow-tab, ink text
inactive: transparent, muted-2 text
```

### 6.5 Status dots

Every state indicator (step dots, phase dots, deploy log checks) is a **7px
filled circle** for a positive/negative state, or a **7px circle, 1.5px border, no
fill** for the neutral/default state. No icon glyphs inside the rail — this
replaces the checkmark-in-icon-box treatment Phase 1 currently uses, so the two
status languages don't coexist once the rail is restyled (§7.4).

---

## 7. Component specs

### 7.1 Top bar (52px)

```
○ training wheels  sql → dbt  │  {title}  {meta}          [Diff]  Mode[Guided|Direct]
```

| Element | Contract |
|---|---|
| Mark + `sql → dbt` tag | Static, links to Home |
| Title | `currentQuery.name`, or the upload folder name for a multi-file conversion |
| Meta | Pre-analysis: `"{fileCount} files · {lineCount} lines"`. Post-analysis: `"{modelCount} models · {sourceCount} sources"`. **No project-name segment** — the reference mockup shows one (`analytics-prod`), but nothing in this app today tracks "the current target project" as a concept; inventing a display value would violate principle 5. Omit the segment. If a target-project loader is built later, it adds this segment; this spec does not block on it. |
| Diff button | Opens the diff drawer (§10). Disabled with a tooltip before analysis has run. |
| Mode toggle | Guided / Direct, per §5.1/§6.4. **No Volume toggle.** The reference mockup's Volume control live-switches between two canned demo datasets — a mockup-only affordance. A real conversion's folder-vs-single-file shape is fixed at upload time; there is nothing to toggle. If a value is worth surfacing, it's a static label in the meta segment, not a control. |

### 7.2 Phase bar (46px)

Six phase pills grouping the ten steps: **Sources** (step 1) · **Describe**
(2–4) · **References** (5) · **Configure** (6–7) · **Generate** (8) · **Ship**
(9–10). This grouping doesn't exist in `config.py`'s `CONVERSION_STEPS` today and
isn't added there — it's a presentation-only lookup table in `shell.js`:

```js
const STEP_PHASE = {
  'analyze': 'sources',
  'layer-staging': 'describe', 'layer-intermediate': 'describe', 'layer-mart': 'describe',
  'cross-project-refs': 'references',
  'materialization': 'configure', 'tags': 'configure',
  'sources': 'generate',
  'review': 'ship', 'deploy': 'ship',
};
```

A phase pill's dot state is the **worst state among its member steps** — blocked
beats defaulted beats settled — computed from the existing `getStepState()`, no
new step-state logic. A status phrase to the right ("Analyzed · 29 models") is a
one-line summary from `analysisResults`/`getAllModels()`.

### 7.3 Canvas header

```
{no}  {Title}                                              {count}
      {kicker}                                              {countLabel}
```

`{no}`/`{Title}` from `CONVERSION_STEPS`. `{count}`/`{countLabel}` from
`getAllModels()`, filtered per layer where relevant. `{kicker}` is new copy — see
§8.1, one sentence per step, written once as a static lookup keyed by step id
(same shape as `STEP_PHASE` above).

### 7.4 Left rail (264px) — restyle only

No new logic. `renderConversionSteps()`, `getStepState()`,
`getBlockedStepIds()`, `getSettledStepCount()` (all shipped, Phase 1) drive this
region unchanged. Restyle to §6.5's dot language (retiring the current
checkmark-in-box treatment so only one status language exists), §6.3's 264px
geometry, and add the "N of 10 settled" counter + legend copy if not already
matching (`main.js`'s `renderStepRailSummary()` already implements this — confirm
it against the exact copy in §8.3 rather than rebuilding).

### 7.5 Home — one addition

Home (`home.js`, out of scope otherwise per §2) gains a fourth intake tab:
**Existing queries**, hosting the query-tree UI currently in the permanent
sidebar (§5.3). `renderQueryTree()`'s logic is unchanged; only its mount point
moves from the always-visible sidebar to this tab's panel.

### 7.6 Right rail (340px) — the pedagogical core

Three stacked blocks, present in Guided mode, **absent entirely in Direct mode**
(one line back: "Comfortable? Switch to guided mode."):

1. **Why this step exists / a good answer / a weak one** — three short blocks of
   static copy per step (§8.2). This is principle 1 made concrete: it's the part
   of the tool that actually teaches, and it is the highest-value writing in this
   entire spec.
2. **The three layers** (staging/intermediate/mart) — a small legend, counts from
   `getAllModels()` filtered by layer (real), one-line description of each layer
   (static, §8.2).
3. **Inspect panel** — select any model, see its real compiled SQL
   (`component.transformedSql`, already computed by the parser — confirmed present
   in `layer-staging.js` today), its dependencies, its tags. This is real data
   already computed per model; the only new work is a picker + this panel's
   layout.

### 7.7 Footer

`← Back` / `Next →` (with `⌘←`/`⌘→`), `step N/10`, mode label, a note —
`"{blankCount} descriptions still empty — the only thing this tool insists on"`
when steps 2–4 have gaps, otherwise `"Nothing here is locked. You can come
back."` — a blocked-step count from `getBlockedStepIds()`, "saved locally" (real
— `appState` already persists to `localStorage`), and a `⌘/` shortcuts hint.

---

## 8. Content — the writing this spec requires

Not decoration. This is where principle 1 and 3 actually get implemented. All of
it is static, written once, stored as lookup tables in `shell.js` keyed by step
id (matching `STEP_PHASE`'s shape).

### 8.1 Canvas kickers — one sentence per step

Ten one-line descriptions, e.g. step 5: *"Tables another team already models in
their own dbt project."* Written to explain the step to someone who has never
seen a dbt project before, without being so long it competes with the step title.

### 8.2 Right-rail teaching copy — ten triples

For each step: **why this step exists** (2–3 sentences, the actual reasoning —
e.g. "Until a raw table is declared as a source, dbt has no idea it exists, and
no lineage can be drawn back to it"), **a good answer looks like** (one concrete
example), **and a weak one** (one concrete anti-example). Plus the three-layer
legend's one-liners (e.g. "Cleaned reads of raw sources. Often zero.").

**Every empty-state message in the ten steps gets the same treatment** — not a
blank panel, an explanation. The canonical example already exists as a target to
match: *"No staging models — and that's the normal answer. Staging only appears
when your script reads a raw table, cleans it, and writes that cleaned version
out as its own table."* Write the equivalent for every step that can legitimately
render empty.

### 8.3 Rail legend + footer copy

*"Needs an answer from you" / "You've answered it" / "Defaults stand — safe to
skip"* and *"Position in the flow never marks a step done. Jump anywhere, in any
order."* — largely already implemented (§7.4); this section is the writing spec
those implementations should be checked against, not new prose.

---

## 9. Workbench (Direct mode)

Replaces steps 2–4 and 6–7's guided bodies when `mode === 'direct'`. One table:

| Column | Source |
|---|---|
| Model name, layer | `getAllModels()` |
| Description | `getSavedDescription(model.name)` read / `updateModelConfig(idx, 'description', v)` write — both already exist, already used by every guided step today |
| Materialization | `modelConfigurations[idx].materialization` |
| Tags | `modelConfigurations[idx].tags` |

Plus: bulk set-materialization and bulk add-tag over the current selection (new
UI, existing setters applied per selected row), a "missing description (N)"
filter, select-all.

**Zero backend gap.** This is the highest-value net-new component in the spec and
the cheapest, because every column already has a real data source. The guided
step renderers for 2–4/6–7 are not modified to build this — Workbench is a second
view over the same state (§5.1), not a rewrite of the first.

---

## 10. Diff drawer

Two-pane before/after, read-only, file switcher across domains. Real data:
`component.sql` (before) and `component.transformedSql` (after) — both already
computed by the existing parser pipeline per model, confirmed present in
`layer-staging.js` today. Line-level highlighting (which lines changed) is a text
diff between two known strings — new, but mechanical, not a design question.

**No per-line "why it changed" tips in this spec** (§2) — the drawer is valuable
without them; tips need SQL-rewriter provenance that doesn't exist yet.

Opens from: the top bar's Diff button, and a new entry point on the Review step
(§11).

---

## 11. Review and Deploy — new user-facing states

**Review (step 9)** gains: an entry point into the diff drawer ("See what dbt
changed · {N} changes"), and a blocker banner when descriptions are missing
(*"{N} models have no description — deploy is blocked until they do"*) with a
jump-to-fix action, both reading from `getBlockedStepIds()` (real, existing).

**Deploy (step 10)** gains three distinguishable states beyond idle/busy/success,
each with its own recovery action:

| State | Backend reality | Recovery UI |
|---|---|---|
| **Conflict** — branch already exists | **Already implemented**: `BranchesExistError` (`github_service.py`) | "Push under new name" / "Force-update existing" (the force-with-lease path the exception already supports) |
| **Partial** — some domains in a stack pushed, one failed | Partially implemented — `deploy.js` already distinguishes stacked vs. independent domain groups and knows dependency order; **needs verification** that the existing push loop reports which branches actually succeeded before this state can render accurately | "Retry the last {N}" / "Review what pushed" |
| **Auth** — SSH key rejected | **New**: `github_service.py`'s clone/push failures raise a generic `GitHubError` today; nothing classifies a `publickey`-rejected git failure distinctly. New: catch and classify that specific subprocess failure | Show the deploy key to add, "Retry push" |

Two of these three states are backend-complete and simply have no UI today —
building them is the strongest argument in this spec for why the shell is worth
building now rather than deferring further.

---

## 12. Migration of existing tokens

`shell.css` is additive; it does not touch `styles.css`'s `--brand-primary`/
`--notion-*` tokens. Once every step body has been re-skinned to §6's palette
(§13, restructure/restyle passes), merge `shell.css` into `styles.css` and retire
the old tokens in one final pass — not before, or Home (built on
`--brand-primary`) and the shell will visibly clash mid-migration.

---

## 13. Rollout — restyle vs. restructure, sequenced

Delivery approach: **incremental, region-by-region**, each slice its own
independently reviewable PR (matches how the three already-shipped phases
delivered via `gh-stack`). No feature flag — this app has no flag infrastructure,
and adding one would be new plumbing orthogonal to this redesign. `mode` defaults
to `'guided'`, so nothing changes for an existing user until they touch the new
toggle.

**The restyle/restructure split matters for review, not just sequencing.** A
restyle PR is CSS/markup only, no new interaction, reviewable quickly. A
restructure PR adds real interaction and needs the scrutiny a feature change
gets — these should never be batched together.

| # | Slice | Kind |
|---|---|---|
| 1 | `shell.css` + `shell.js` skeleton: top bar, phase bar, right-rail container (empty), footer. Wraps `#step-content` untouched — every existing step renderer keeps working, unstyled but functional. **This is the checkpoint that proves the shell against real data before anything else is built.** | restructure (new shell), zero risk to existing steps |
| 2 | Right-rail layer legend (real counts, no new copy needed) | restyle-adjacent, low risk |
| 3 | Left rail restyle (§7.4) + step 5's restyle + canvas frame restyle for all steps | **restyle** |
| 4 | Step 1 restructure: mart-selection table, DECLARE-variables card | **restructure** |
| 5 | `workbench.js` + `mode` state plumbing (§5.1, §9) | **restructure** |
| 6 | Restyle steps 2–4/6–7's guided bodies, now that Workbench has proven the shared state layer both paths read | **restyle** |
| 7 | `diff.js` (§10) | **restructure** |
| 8 | Step 9 restructure: diff entry point, blocker banner | **restructure** |
| 9 | Step 10 restructure: conflict/partial/auth states (§11) | **restructure** |
| 10 | Home's fourth intake tab (§7.5); delete `tour.js`/`tour.css` (§5.2) | restructure (small) |
| 11 | Right-rail teaching copy (§8) — can land any time after slice 1, in parallel with the rest, since it's a static lookup table with no code dependency | content |
| 12 | Token merge (§12), retiring old `--brand-primary`/`--notion-*` | cleanup |

---

## 14. Testing

- Every new pure-logic function in `shell.js` (phase-pill worst-state-wins,
  mode-branch routing, copy lookups) gets a `tests/js/check-*.mjs` file — node
  builtins only, no test runner dependency, matching the two that already exist.
- `github_service.py`'s new auth-failure classification (§11) gets a `pytest`
  unit test exercising the specific subprocess failure shape.
- Existing `pytest` suite must stay green throughout — none of slices 1–12 change
  parser or generation logic, only presentation and state.
- Manual verification per slice, minimum: does every id in §5.3's table resolve
  where its consumer expects it (the concrete failure mode this spec is designed
  to prevent)?

---

## 15. Open follow-ups (explicitly deferred, not forgotten)

- Target-project loader — upgrades §7.1 (project name), §9/§11 (grounded mart
  recommendations, real per-layer materialization reasoning). Independent spec.
- Per-line diff explanations (§10). Needs SQL-rewriter provenance. Independent
  spec.
- SQL dialect claims on Home ("BigQuery · Snowflake · Redshift") — unverified
  against what the parser actually accepts. Small, unrelated to this shell;
  worth a follow-up ticket, not a blocker here.
