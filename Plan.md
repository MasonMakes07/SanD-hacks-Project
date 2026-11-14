Event: SanD Hacks Fall 2026
Team size: 3 developers
Tracks submitted: Bright Data — Data for Good (primary), Best Overall Hack, Neon Track, MLH Best Use of Gemini API, Most Innovative Project (judged holistically)
Budget: $0. Free tiers and event-provided credits only.
Last updated: Saturday, November 14, 2026, 11:21 a.m.
Supersedes: TruthTrace_Plan_Updated.md

1. Project in One Sentence

TruthTrace is a Chrome extension that checks a claim from a web page against existing fact-checks and retrieved sources, and shows how those sources are connected, so the user can inspect the evidence rather than trust a verdict.

2. Biggest Risk and Mitigations

Risk: About 24 hours, three people, several external services, and a required video and write-up. Any single failure can stop the demo or the submission.

Mitigations

Walking skeleton first. One claim travels through every layer by Saturday 9 p.m., with stubs where real services aren't ready yet.
Frozen scope. Only the MUST-HAVE list is built. Everything else is cut unless all MUST-HAVE items work by Sunday 9 a.m.
Hard checkpoints with fallbacks (Section 6).
Pre-cached demo claim. One full investigation is run and saved ahead of time for the live demo.
Judging criteria set priorities. Judges score idea, demo, technical complexity and completion. They do not score code quality.
Submission is protected. The video and write-up start by 11:00 a.m. Sunday from a working build, not from new features.
3. Scope
MUST HAVE
Chrome extension (Manifest V3) that loads in developer mode
User selects text or clicks "Analyze Page" on a supported news article
Gemini extracts one checkable factual claim
Bright Data SERP API returns relevant sources for the claim
Google Fact Check Tools API is checked for existing fact-checks
Backend returns a structured result with clickable sources
Side panel shows status, explanation, sources and a basic source network
Inconclusive results are handled without a false verdict
CUT (only if all MUST-HAVE items work by Sunday 9 a.m.)
Scrolling detection and Auto-Check
Multiple claims per page
Investigation history and saved claims
ElevenLabs voice output (stretch; needed only for the ElevenLabs track, which we are not targeting)
Advanced graph filtering
Additional news sites beyond the demo site
Status rules
Default to insufficient_evidence unless a fact-check or primary source clearly supports or contradicts the claim.
Use contradicted only when reliable evidence directly disagrees, and the explanation links to it.
Never mark a claim false because no supporting source was found.
4. Shared API Contract

Owner: Person 2. Published with a mock response by Saturday 11:45 a.m.

Request: POST /api/analyze

Field	Type	Notes
text	string	Selected text or visible article text
url	string	Page where the text appeared
mode	string	manual or automatic

Response

Field	Type	Notes
investigation_id	string	Unique ID
claim	string	Factual claim being checked
status	string	supported, contradicted, misleading, or insufficient_evidence
explanation	string	Short analysis; every claim cites a retrieved source
sources	array	title, url, date (or null), excerpt
earliest_observed_source	object or null	Earliest source with a reliable date, else null
graph_nodes	array	Articles or documents
graph_edges	array	source_id, target_id, relationship_type, evidence

Edge rules

relationship_type must be one of explicit_citation, similar_wording, or same_fact_check.
Every edge needs its evidence. Edges without evidence are not drawn.
Similar wording is never presented as proof of copying.
5. Full Task Breakdown
Person 1 — Chrome Extension and Interface

Build

[ ] manifest.json (Manifest V3) with the minimum permissions needed (activeTab, sidePanel, storage)
[ ] Background service worker
[ ] Content script that reads selected text and visible article text
[ ] Side panel UI: Analyze button, loading state, error state
[ ] Result view: status badge, explanation, source list with links that open the real URL
[ ] Basic source network (React Flow or Cytoscape.js, simple fixed layout)
[ ] Connects to the backend URL from a config file, not a hardcoded key

Verify

[ ] Extension loads on a fresh Chrome profile
[ ] Works on the chosen demo news site
[ ] Error states display clearly when the backend is off
Person 2 — AI, Claim Analysis and Backend

Build

[ ] FastAPI app with POST /api/analyze
[ ] Mock response published first (Section 4)
[ ] Gemini claim extraction via Google AI Studio free tier
[ ] Google Fact Check Tools API lookup (create a Google Cloud API key Saturday morning)
[ ] Final analysis that produces status and explanation from retrieved sources only
[ ] Output validation: reject any citation that doesn't match a retrieved source
[ ] Error handling for API failures, empty results and timeouts
[ ] Keys read from environment variables, never committed

Verify

[ ] Returns a consistent result for the demo claim and two other claims
[ ] Never invents a citation
Person 3 — Bright Data, Source Tracing and Database

Build

[ ] Bright Data account and API key; confirm credit balance
[ ] Search query builder from the claim
[ ] SERP call; parse titles, URLs, snippets and dates
[ ] Normalize and deduplicate results
[ ] Edge detection: explicit citations and close wording found in retrieved text
[ ] Neon Postgres (promo credits, free tier): tables for investigations, sources, edges
[ ] Caching so repeated claims don't use credits again
[ ] Credit usage tracked; a per-demo budget is set

Verify

[ ] One claim returns real sources and at least one evidence-backed edge
[ ] Earliest-source logic handles missing dates
Shared Tasks (all three)

Setup

[ ] Join the Slack workspace (sandhacks2026) and set display name to full name + school
[ ] Register the team on Devpost (https://sand-hacks-26.devpost.com/)
[ ] Confirm team members are all registered at the event
[ ] Confirm each member has a government or university ID (mandatory)
[ ] Check in at Jacobs Hall outside the Qualcomm Room if not done

Repository

[ ] One GitHub repository, created after 10:30 a.m. Saturday
[ ] .gitignore covers .env and key files
[ ] Repository is scrubbed of secrets before it is made public (check history, not just current files)
[ ] README with: what it does, setup steps, how to load the extension, how to run the backend, required keys (names only)
[ ] License file chosen (MIT is a common default for hackathons)
[ ] Credits any libraries and APIs used

Demo preparation

[ ] Demo news article chosen and tested, with a backup article
[ ] Demo claim pre-cached (Section 7)
[ ] Demo laptop: charged, charger packed, Chrome set up with the extension loaded, backend running
[ ] Offline plan: saved screenshots and a recorded run in case Wi-Fi fails
[ ] Hotspot as backup network

Submission

[ ] Devpost project page: title, tagline, description, tracks selected
[ ] Devpost write-up prompts answered: problem, solution, challenges, tools used
[ ] 3-minute demo video uploaded to YouTube or Google Drive with visibility set to Public
[ ] GitHub repository link added
[ ] Live demo link: optional. We will not offer one, since the backend runs on the demo laptop. Judges will see the demo in person.
[ ] Initial placeholder submitted by 11:00 a.m. Sunday
[ ] Final submission by 12:00 p.m. Sunday, including video and write-up
[ ] Check the submission page after submitting, confirm every link opens

Judging

[ ] All three members at the table from 12:30 to 2:30 p.m.
[ ] Rehearse a 3-minute pitch and 2 minutes of Q&A
[ ] Prepare answers to likely judge questions (Section 9)

Track-specific requirements

[ ] Bright Data track: show the SERP API in use and explain the credit budget
[ ] Neon track: show Neon storing investigations, sources and edges, with a database-driven flow
[ ] MLH Gemini track: confirm MLH eligibility rules at https://www.mlh.com/events/sand-hacks/prizes; show Gemini doing claim extraction and analysis
[ ] Best Overall / Most Innovative: the demo and pitch carry these; no separate work needed