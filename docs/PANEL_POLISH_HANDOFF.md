# Panel refinement — issues #7, #8 and #9

Implementation owner: this work's GitHub owner (`MokeyCodes`). Branch:
`codex/panel-polish`, based on combined integration `147146f`. The branch also
merges evidence delivery `6ecdcf4`, including the previously missing legacy-result
reset guard, complete pin/reassess/history panel regression and verification notes.
Main is unchanged. All completed work belongs in review PR #29.

The repository's Impeccable skill guided the scoped refinement. Existing walnut,
paper, brass and yarn remain; this does not replace the board's identity or assets.
The delivered changes were made before Plan.md's Sunday 11 a.m. submission-work
cutoff; no more specific commit deadline was supplied.

## Static surfaces and reading (#7)

`theme.css` supplies shared paper/elevation/focus tokens with restrained forest-
tinted shadows. `sidepanel.css` scopes their use to the panel, making the claim
surface brighter and higher than ordinary paper and source cards. Small metadata,
legend, caveat, dossier and footer text is at least 12px; headings remain serif,
while body/control text uses the existing bundled Manrope font. Its license is
already shipped and credited. The sticky compact summary now uses dark ink on
paper instead of inheriting a pale foreground.

Focused browser checks exercise all four findings at 280/380px, long titles and
URLs in the selected-source dossier, wrapping, preserved evidence caveats and
sampled metadata contrast (at least 4.5:1 on its actual solid surface). Empty,
pending and error states are reviewed alongside retained completed results.
Disabled controls intentionally use a muted static treatment.

## Control feedback (#8)

`panel-feedback.css` is loaded after the existing panel styles. Its selectors are
scoped to `.panel` controls; graph cards, loading animation and result containers
are excluded. Shared controls have an 80ms hover hesitation, 140ms transitions,
1px hover lift and 1px press depression. Press transitions start immediately with
zero delay and settle within 120ms. Keyboard focus uses a separate immediate
outline (green on paper, brass on walnut). Disabled controls remain flat and
non-actionable. Reduced motion removes transforms/transitions while retaining
static pressed, focus and disabled cues.

There is no event-handler delay or new control timer. Browser checks exercise
pointer down/release, keyboard settings save, confirmation, cancellation, evidence
expansion, native disabled state and keyboard focus. Capture, model, persistence
and source-tracing contracts are unchanged by these styling changes.

## Completed-result arrival (#9)

`createResultArrival(root)` in `src/arrival.js` exposes `play`, `cancel` and
`destroy`. The completed renderer calls `play()` only for `fresh:true`; selection,
expansion, pinning and resizing do not replay it. New checks, observed pending
phases and page teardown cancel and clean up existing animations. Existing
request-identity guards still reject cancelled/late results before rendering.

The claim paper settles by 3px with opacity never below .96; evidence headings
follow in 40ms steps. All motion finishes within 340ms, including one soft green
claim-outline cue. Content and controls remain available immediately. Warnings,
status semantics, source selection and graph geometry are not animated. Reduced
motion (including a preference changed during animation) cancels motion and
uses a static claim outline. Unsupported animation capability gets the same
static fallback. Web Animations completion promises handle cleanup; no arrival
timeout, minimum wait, percentage or fabricated backend phase is introduced.

Browser checks cover all findings, initial/repeated completion, selection,
expansion, resize, pinning, cancel/late reply, failure/retry, class cleanup and
live reduced-motion preference changes.

## Visual evidence and reproduction

Screenshots in `verification/panel-polish/` come from an isolated, headed Chromium
profile loading the actual unpacked extension with an explicitly synthetic local
HTTP article/backend. They show the empty, pending and error panel at 380px and
the insufficient-evidence finding at 280px. They are UI evidence, not live source
or model verification. The synthetic warning remains visible in result flows.

- `npm --prefix extension run check`: extension build, unit and browser checks.
- `POLISH_HEADED=1 npm --prefix extension run test:browser -- tests/polish.spec.js`:
  the same focused checks in a headed Chromium profile.
- `python -m pytest backend/tests source_tracing/tests -q`: offline API/source
  regressions with the existing development environment.

All browser profiles, credentials and HTTP responses used here are isolated test
fixtures. No production permission or dependency is added. The three optional
Neon tests require a separately configured test database; no new live vendor or
database behavior is claimed by this UI pass. Existing live receipts remain
separately documented in `verification/capture-flow/`.
