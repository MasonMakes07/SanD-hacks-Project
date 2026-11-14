# TruthTrace

**Real-Time Misinformation Detection and Source Tracing**

**Project:** SanD Hacks Fall 2026
**Team Size:** 3 developers
**Primary Track:** Bright Data — Data for Good
**Additional Target Tracks:** Best Overall, Neon, Gemini API, Most Innovative Project

---

## 1. Project Overview

TruthTrace is an AI-powered Chrome extension that helps people identify potentially false or misleading information while browsing the internet.

Unlike traditional fact-checking websites, where users must copy and paste claims into a separate platform, TruthTrace operates directly in the browser.

Our goal is to let users read news articles and online posts while the extension identifies factual claims, checks available evidence, and helps users investigate where the information came from.

Our main innovation is not just detecting misinformation, but showing how different sources are connected.

For example, five news websites might report the same claim, making it appear that five independent sources have confirmed it. TruthTrace could discover that all five websites actually reference one original report. It would display those relationships visually so users can understand the evidence for themselves.

---

## 2. The Problem We're Solving

Misinformation spreads quickly, especially when an unsupported claim is repeated across multiple websites and social media platforms.

There are three major problems:

- People often cannot tell whether information they encounter is reliable.
- Even when information is questionable, determining where it came from requires significant research.
- Repeated information can create the appearance of independent confirmation, even when every article relies on the same source.

Existing fact-checking tools can identify previously reviewed claims, but users still need an easier way to investigate source relationships directly while browsing.

TruthTrace aims to make that process accessible, transparent, and interactive.

---

## 3. Main Application Goals

### Goal 1 — Detect potentially misleading claims

The extension should recognize factual statements in webpage text and send them for analysis.

The system will use AI to identify checkable claims, look for relevant fact-checks, and compare those claims with retrieved sources.

The result should distinguish between:

- Supported by available evidence
- Contradicted by reliable evidence
- Potentially misleading or missing context
- Insufficient evidence to determine

The extension should never automatically assume that a statement is false just because no supporting information was found.

### Goal 2 — Show results directly on the webpage

Users should not have to leave their current website.

When a claim has been analyzed, TruthTrace should be able to:

- Highlight or mark the statement.
- Display a small indicator beside it.
- Let the user click the indicator for an explanation.
- Open a Chrome side panel with supporting evidence and links.

Our ideal experience is for this to happen naturally while someone is scrolling through an article.

### Goal 3 — Trace where information came from

This is our most important differentiating feature.

When TruthTrace identifies a claim, it should search for other websites that published the same or closely related information.

It should then:

- Find earlier publicly accessible occurrences of the claim.
- Identify articles that explicitly cite other sources.
- Detect articles using highly similar wording.
- Compare publication dates.
- Build a visual network showing the relationships.

We cannot guarantee discovery of the first-ever origin of a claim. Instead, we will show the earliest source we found, along with verifiable connections to other sources.

### Goal 4 — Make the evidence understandable

Users should be able to investigate a claim rather than blindly trusting an AI-generated verdict.

Each result should provide:

- The statement being investigated.
- An evidence-based assessment.
- A short explanation.
- Links to supporting or contradicting evidence.
- A visual source network.
- An indication of uncertainty.

---

## 4. Main Features and Priorities

### MUST HAVE — Minimum Viable Product

These features must work before adding anything else.

- Chrome extension installs successfully.
- User can open a news article.
- User can highlight a sentence and select "Check with TruthTrace," or click a button to analyze the page.
- Gemini extracts one checkable factual claim.
- Bright Data searches for relevant sources.
- Google Fact Check Tools is checked for existing fact-checks, when available.
- The app returns an evidence-based result with clickable sources.
- The app displays a basic network showing related sources.
- The result appears in the extension's side panel.

### SHOULD HAVE — Scrolling Detection

- User enables an "Auto-Check" toggle.
- Extension detects new visible text as the user scrolls.
- It selects a limited number of factual claims for checking.
- Previously analyzed claims are not checked repeatedly.
- An indicator appears near claims with results.
- Clicking the indicator opens the investigation.

For the hackathon, automatic scrolling analysis only needs to work reliably on one or two supported public news websites. It does not need to work across every social media platform.

### STRETCH FEATURES — Only If Time Remains

- ElevenLabs reads explanations aloud.
- More advanced graph visualization with filtering.
- Investigation history and saved claims.
- Multiple claims analyzed on one page.
- Support for additional websites with dynamically loaded feeds.

---

## 5. Team Responsibilities

Each developer owns one major part of the application. Everyone will use the same GitHub repository.

### PERSON 1 — Chrome Extension and User Interface

**Primary responsibility:** Everything the user sees and interacts with.

**Tasks**

- Create the Chrome extension using Manifest V3.
- Build manifest.json, content script, and background service worker.
- Create the extension's side panel.
- Add an "Analyze Page" button.
- Allow users to select text for checking.
- Send selected text and the webpage URL to the backend.
- Display the backend's result.
- Add colored indicators and source links.
- Build the interactive source graph using React Flow or Cytoscape.js.
- Implement scrolling detection if the MVP is complete.

**Expected Deliverable**

A working Chrome extension that can send a claim to the backend and display a fact-check with its source network.

**Definition of done:** A user opens a supported webpage, starts an investigation, and sees the returned findings without leaving that webpage.

### PERSON 2 — AI, Claim Analysis, and Backend

**Primary responsibility:** Making the system understand claims and evaluate retrieved evidence.

**Tasks**

- Set up the Python FastAPI backend.
- Create the endpoint that receives claims from the extension.
- Integrate Gemini for factual-claim extraction.
- Extract a concise, searchable claim from submitted text.
- Search Google's Fact Check Tools API for relevant existing fact-checks.
- Compare the claim against retrieved sources.
- Identify supporting evidence, contradictions, and missing context.
- Generate a short explanation with cited sources.
- Return structured JSON for the extension.
- Handle API errors and inconclusive results.

**Expected Deliverable**

An API that accepts webpage text and returns an evidence-based assessment of a factual claim.

**Definition of done:** The backend processes a real submitted claim and returns a consistent result without inventing citations or declaring unsupported verdicts.

### PERSON 3 — Bright Data, Source Tracing, and Database

**Primary responsibility:** Investigating the internet and discovering connections between sources.

**Tasks**

- Set up Bright Data API access.
- Build search queries based on the claim received from Person 2.
- Retrieve relevant web-search results.
- Extract source titles, URLs, descriptions, and available dates.
- Remove duplicate results.
- Identify explicit citations between articles.
- Identify articles with similar claims or wording.
- Identify the earliest dated source found in the retrieved evidence.
- Create the source network's nodes and connections.
- Set up Neon Postgres to store investigations, sources, and relationships.
- Return graph data to the backend.

**Expected Deliverable**

A source-investigation pipeline that retrieves actual web data and turns it into a structured network.

**Definition of done:** Given a claim, the system produces a set of real sources and evidence-backed relationships that the frontend can visualize.

---

## 6. How the Three Parts Connect

The application should follow this process:

1. **User reads or selects text**
2. **Person 1 — Chrome Extension:** Captures the selected text or visible claim and sends it to the backend.
3. **Person 2 — AI Backend:** Extracts a factual claim, checks for existing fact-checks, and requests supporting evidence.
4. **Person 3 — Web Investigation:** Uses Bright Data to discover related sources, creates their connections, and stores the investigation.
5. **Person 2 — Final Analysis:** Combines the evidence into an assessment and explanation.
6. **Person 1 — Results:** Displays the assessment, citations, and source network in the Chrome extension.

### Shared API Agreement

The team must agree on the data format immediately so everyone can develop independently.

**Request**

```
POST /api/analyze
```

Fields:
- `text` — the statement or selected webpage text
- `url` — the webpage where the text appeared
- `mode` — manual or automatic

**Response**

- `investigation_id` — unique identifier
- `claim` — factual claim being checked
- `status` — supported, contradicted, misleading, or insufficient evidence
- `explanation` — short analysis
- `sources` — retrieved sources and relevant evidence
- `earliest_observed_source` — earliest dated source found, if available
- `graph_nodes` — articles or source documents
- `graph_edges` — documented citations or other evidence relationships

Every graph connection must identify the relationship type. Similar wording is not proof that one article copied another.

Person 2 owns the endpoint and response contract. Person 3 provides the source and graph information. Person 1 consumes the response.

---

## 7. Technology Stack

| Component | Technology |
|---|---|
| Browser extension | Chrome Manifest V3 |
| User interface | JavaScript / React |
| Visual source network | React Flow or Cytoscape.js |
| Backend | Python / FastAPI |
| Claim extraction and analysis | Google Gemini API |
| Web source discovery | Bright Data SERP API |
| Existing fact-check lookup | Google Fact Check Tools API |
| Database | Neon Postgres |
| Optional voice | ElevenLabs |

- API keys must remain on the backend, not inside the Chrome extension.
- For the hackathon, the extension can be installed locally through Chrome's developer mode. Publishing it to the Chrome Web Store is not required for the demonstration.

---

## 8. Development Schedule

### Saturday, November 14

**Phase 1 — Build the foundation**

*Target: Complete the basic interfaces and prove that each external service works.*

- **Person 1:**
  - Create the Chrome extension and side panel.
  - Make selected webpage text appear in the side panel.
  - Connect to a mock backend response.
- **Person 2:**
  - Start the FastAPI server.
  - Connect Gemini.
  - Return one extracted claim and a structured test response.
- **Person 3:**
  - Test Bright Data retrieval.
  - Set up Neon.
  - Return real source records for a test claim.

**Team checkpoint:** The extension must communicate successfully with the backend.

**Phase 2 — Build the investigation**

- **Person 1:**
  - Display results and source citations.
  - Start the interactive graph.
- **Person 2:**
  - Integrate claim extraction, fact-check searching, and evidence analysis.
- **Person 3:**
  - Build source discovery, normalization, deduplication, and basic connections.

**Team checkpoint:** One real claim can travel through the entire system and return a source-backed investigation.

**Phase 3 — Integration**

Everyone works together to:

- Test the complete workflow.
- Fix API mismatches.
- Verify that graph links open real sources.
- Make status messages clear.
- Prepare one reliable example for the demo.

**Saturday-night goal:** A functional manually triggered investigation, even if automatic scrolling is unfinished.

### Sunday, November 15

**Phase 4 — Scrolling Feature**

If the core application is working:

- **Person 1:** Add scroll detection and optional auto-check mode.
- **Person 2:** Ensure claim extraction works with short visible paragraphs.
- **Person 3:** Add caching so repeated claims do not trigger unnecessary searches.

**Phase 5 — Final Testing and Presentation**

Everyone:

- Test the extension on a supported news website.
- Verify every cited URL.
- Test an inconclusive claim.
- Prepare a fallback investigation using real previously retrieved evidence.
- Record the three-minute demo video.
- Prepare the Devpost description and GitHub link.

**Hackathon deadlines:**

- Sunday 11:00 a.m. — Initial Devpost submission
- Sunday 12:00 p.m. — Final submission
- Sunday 12:30–2:30 p.m. — In-person judging

The final video, write-up, and repository link must be submitted by noon. The team must follow the event's code-commit time restrictions.

---

## 9. Live Demonstration for the Judges

Our demonstration should focus on one compelling investigation.

- **0:00–0:30 — Introduce the problem.** Explain how repeated misinformation can appear credible when several websites publish the same claim.
- **0:30–1:00 — Show the Chrome extension.** Open an article and select a checkable claim. Activate TruthTrace.
- **1:00–1:45 — Show the investigation.** The extension retrieves evidence and returns an assessment with citations.
- **1:45–2:30 — Show the source network.** Reveal how related articles are connected. Click on one connection to demonstrate that several reports may rely on the same underlying source.
- **2:30–3:00 — Show the impact.** Explain how TruthTrace helps readers independently examine evidence instead of blindly accepting an AI-generated verdict.

If automatic scrolling is working reliably, demonstrate the appearance of an on-page indicator before opening the full investigation.

---

## 10. Final Goals and Success Criteria

We consider the hackathon MVP successful when:

- [ ] The Chrome extension runs locally.
- [ ] The user can check a claim from a webpage.
- [ ] The backend retrieves real external evidence.
- [ ] The app identifies relevant sources and existing fact-checks when available.
- [ ] The app displays a transparent assessment.
- [ ] Users can inspect the source links.
- [ ] A source relationship graph appears.
- [ ] At least one relationship can be demonstrated with real evidence.
- [ ] The project handles unavailable or inconclusive evidence appropriately.
- [ ] The complete demonstration works within three minutes.

**Optional success goal:** The extension automatically marks analyzed claims as the user scrolls through a supported webpage.

### Our Central Pitch

TruthTrace doesn't just tell you whether a claim might be false. It shows you the evidence, where the claim has appeared, and how its sources are connected.

We want to turn passive scrolling into active, informed reading.

By integrating AI analysis, live web retrieval, and interactive source tracing into one Chrome extension, TruthTrace gives users a practical way to investigate information without leaving the websites they're already using.