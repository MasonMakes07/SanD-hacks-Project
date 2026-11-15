# Account 2 evidence/backend handoff

Branch `codex/evidence-backend`; reconciled baseline `6932e58`.
Owner of #14, #16, #18, #25, #28. Integrate sequentially on Account 3's branch;
leave issues open until combined acceptance checks pass. Plan.md protects Sunday
11 a.m. submission work, with final submission at noon. No finer cutoff supplied.

## Additive UI hooks

Import `renderEvidenceInspector(container, normalizedResult)` and
`renderProvenance(container, normalizedResult)` from `src/evidence.js`.
Load `evidence.css` for scoped `.tt-evidence`, `.tt-history`, `.tt-reassessment`
rules. Account 3 places containers in shared HTML and calls hooks from its
completed-result renderer. `normalizeResult` preserves source passages/kinds.
The inspector renders with textContent, safe links, and fallback labels.

Account 1: `VideoClaimExtraction.provenance` and `ClaimSource.provenance` are
optional additive dictionaries. `_cached_video_claim` marks memory reads;
`_get_video_claim` stamps fresh successful extraction and `_claim_source` copies
metadata. Preserve these small additions when reconciling video lifecycle work.
No timestamp is inferred for legacy cached extraction. Title/page-text fallback
has no extraction provenance and must keep the explicit fallback label supplied
by Account 1's capture/confirmation work.

See backend/API_CONTRACT.md for material limits and whitespace-only matching.

## History

`createHistory({container,getConfig,onOpen})` from `src/history.js` returns
`{load,cancel}`. Call `load()` once after connection config loads and after a
completed check. `onOpen(result,capture)` receives a normalized saved result and
its original capture; route through Account 1's lifecycle cancellation before
calling `renderResult(result,capture,{fresh:true})`. Do not call CAPTURE or
`/api/analyze`. `cancel()` invalidates pending reads when a new investigation
starts or the panel closes. Saved data includes available video position,
duration, detected statement and extraction provenance. Original title is
available only when requests send optional `page_title`.

Legacy capture may be absent: `capture.contextUnavailable` then true, URL null,
text empty. Account 3 must render an explicit original-page-unavailable label
instead of using the active tab or calling `new URL(null)`. Reopened results
retain the original check time and display the saved-result notice. History has
explicit empty, unavailable, unreadable and missing-record states. GET transport
now sends the same API key for protected history reads; health remains public.

## Reassessment and pinning

`createReassessment({container,getConfig,onRevised})` from `src/reassessment.js`
returns `{setInvestigation,acceptPinned,cancel}`. Call `setInvestigation(result)`
from the completed-result renderer. It preserves its own immutable original even
when pinning mutates the board's source collection. Saved revised results embed
both findings and restore the comparison via the same hook.

`chat.js` retains additive passage metadata and emits `pinned_evidence` alongside
its existing `onPin({sources,nodes,edges})` payload. Account 3's `pinEvidence`
handler must pass that selection to `reassessment.acceptPinned(pinned_evidence)`.
This only enables the explicit button; it does not change the finding. The module
POSTs IDs/receipt tokens and unchanged claim, never client material. Route
`onRevised(revised,original)` through the normal completed-result renderer using
the original capture. Call `cancel()` when new work starts/closes. The module
invalidates stale completions and retains the original/comparison on errors.

Receipt tokens expire after one hour/restart/eviction; 409 prompts a new explicit
search/pin. Reopening a saved reassessment still requires no receipt or model call.
The API returns source-ID mappings when pins collide or repeat an original URL.
Do not blindly merge the reassessed source list into the original finding; render
separate findings with their separate cited sets. `renderComparison` is also an
exported independent renderer for full-board integration.

## Seed tracing and budgets

`fetch_sources(claim, seed_url=None)` is additive. Article investigations pass
the original URL; social/video checks omit it. This restores the user's explicit
five-page cap from the pulled baseline's ten-page cap. Seed and one body-link
destination share those five attempts; search still uses at most two SERP calls.
The source metadata model therefore deliberately caps `page_fetches` at five.
A citation only records a body link, never agreement or independent verification.
Seed role/URL and source passage metadata survive caches and saved full results.

## Fixtures, migrations and verification

Synthetic review payloads are in `docs/examples/evidence-synthetic.json`,
`reassessment-synthetic.json`, and `video-history-synthetic.json`. These are
invented fixtures on example.org, not retrieved public evidence. They cover
local source-cache provenance, an unchanged comparison, and a saved video result
with unknown extraction time and distinct capture/statement positions.

Apply additive migrations 004 and 005 with the existing source_tracing migration
runner before enabling new Neon video-cache writes/history. Source passage
material is preserved in existing investigation `meta.source_material`; complete
page/video/comparison context is in existing `tt_analyses.response` JSONB.
No destructive migration or data backfill is needed. Old entries remain readable;
new algorithm/schema identities avoid incorrectly reusing old source graphs.

Reconciled Account 3's presentation through `cd06c0a` on this branch. Source
metadata now survives both safe normalization and repeated pin rendering; the
strict legacy chat request still sends only its supported five source fields.
This does not upgrade arbitrary chat/client passages into server-verified evidence.

Account 1 merge points: preserve `complete_result` after every completed finding
(including no-claim results), `page_title`/original `page_context`, the small video
provenance additions, and the seed URL passed to `_retrieve_evidence` only for
article input. Account 1's additive `input_context` is preserved in full-result JSON on the
combined branch, alongside `page_context.capture_kind`; keep it separate from
source retrieval metadata. Preserve nullable unknown video timestamps and confirmation
semantics from Account 1 when resolving adjacent code.


## Combined integration status

Account 3's integration through `8f5bcc3` includes Account 1's confirmed capture
lifecycle and the actual panel/board hooks for these modules. The evidence branch
has merged that combined state and also preserves the subsequent report fix.
The full-panel browser scenario in `extension/tests/chat.spec.js` now pins an
accepted source without changing the finding, explicitly reassesses the unchanged
claim, closes/reopens the panel, lists both saved analyses, and reloads the saved
comparison with original page context. It asserts protected GET authentication
and no new extraction/analysis/search/chat calls during reopening. The backend
round-trip test separately verifies server receipts, intended material inclusion,
validated quotations and reopening after the in-process registries are cleared.


Final combined verification at `ec3b75d`: **516 Python tests passed, 3 live-Neon
checks skipped; extension build, 22 unit tests and 47 browser tests passed**.
See `docs/verification/evidence-backend/receipt.json`. Offline checks include
source/quotation identity and fidelity, snippet fallback, cache identity and
original timestamps, outages and legacy storage, authenticated read-only history,
server-held reassessment evidence inclusion and ID remapping, saved comparisons,
seed-link selection/deduplication and retrieval budgets. The actual unpacked
panel's combined pin/reassess/close/reopen test passes with authenticated GET-only
history and no fresh upstream work. Migrations are delivered but were not applied
to a live database by this work. Integration PR #29 remains open; main is unmerged.
