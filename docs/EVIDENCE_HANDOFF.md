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
