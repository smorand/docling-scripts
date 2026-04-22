"""Document analysis prompt for --analyze on documents (PDF, DOCX, PPTX, XLSX).

This prompt is used by BaseConverter.run_analysis() to produce analysis.md.
It is designed to be generic and adapt to any document type.

## How it works

The LLM receives the full document.md content and this system prompt.
It must first identify the document type, then produce a structured analysis
adapted to that type.

## Document types recognized

The prompt handles these document types (non-exhaustive, add as needed):

| Type | Key sections produced |
|------|----------------------|
| Partnership/Project Proposal | Value proposition, architecture, roadmap, deliverables, IP/pricing, teams |
| Technology Article/Paper | Core thesis, technologies, architectures, benchmarks, examples, implications |
| Architecture Document | Components, data flows, technology choices, trade-offs, constraints |
| Strategy/Vision Deck | Vision, strategic axes, target metrics, timeline, enablers |
| Demo/Product Showcase | Capabilities, scenario walkthrough, results, technical stack |
| Status Report | Progress, risks/blockers, decisions, next steps |
| Market/Competitive Analysis | Positioning, strengths/weaknesses, differentiation criteria |
| Meeting Notes | Decisions, action items + owners, open questions |
| Specification | Requirements, constraints, interfaces, acceptance criteria |

## Customization

To override this prompt entirely, use: `doc-convert file.pdf --analyze -i "Your custom prompt"`

To add new document types or change the analysis structure, edit the
DOCUMENT_ANALYSIS_SYSTEM_PROMPT below.
"""

from __future__ import annotations

# ── System prompt for document analysis ──────────────────────────────────────
#
# This prompt is intentionally generic. It asks the LLM to identify the
# document type first, then adapt its analysis structure accordingly.
#
# Key design principles:
# 1. Identify document type before analyzing (adaptive structure)
# 2. Preserve high-value details: architectures, metrics, timelines, names
# 3. Don't flatten rich information into generic bullet points
# 4. Be complementary to document.md (add perspective, don't paraphrase)
# 5. Highlight relationships and dependencies between concepts
#
# If you need to evolve this prompt:
# - Add new document types to the "Adapt your structure" section
# - Adjust section names or priorities
# - Change the level of detail expected
# - The prompt is used as system_prompt in an OpenAI-compatible chat API call

DOCUMENT_ANALYSIS_SYSTEM_PROMPT = """\
You are a senior analyst. Analyze the document provided and produce a structured \
analysis in markdown.

## Step 1: Identify the document type

First, identify what type of document this is. State it clearly at the top:

```
**Document type:** <type>
```

Common types: Partnership/Project Proposal, Technology Article, Architecture Document, \
Strategy/Vision Deck, Demo/Product Showcase, Status Report, Market/Competitive Analysis, \
Meeting Notes, Specification, Financial Report, Training Material. \
If it doesn't fit any of these, describe the type in a few words.

## Step 2: Produce the analysis

Your analysis must:
- Preserve key details: architectures, metrics with numbers, timelines with dates, \
named technologies, concrete examples. Do NOT flatten rich information into generic bullet points.
- Identify the sections in the document that bring SIGNIFICANT information and organize \
your analysis around them. Skip sections that are purely structural or redundant.
- Maintain concrete numbers, dates, version names, and team roles when they matter \
for understanding the document.
- Highlight relationships and dependencies between concepts.
- Be COMPLEMENTARY to the raw document: add perspective, synthesis, and structure. \
Do not simply paraphrase or list everything.
- **Source references**: For each key piece of information, add a reference to its location \
in the source document using the format *(Page X)* or *(Pages X-Y)*. The source document \
contains page/slide markers like `*[Page N]*`. Use them to cite where each fact, decision, \
metric, or architecture element comes from. This helps the reader quickly locate the original \
content.

## Step 3: Adapt your structure to the document type

For **Partnership/Project Proposals**:
- Overview & Value Proposition (what, why, for whom)
- Solution Architecture (layers, components, technology stack, data flows)
- Roadmap & Milestones (phases with dates, deliverables per phase)
- Commercial Terms (pricing model, IP ownership, key conditions)
- Teams & Governance (key people, roles, decision structure)
- Risks & Dependencies

For **Technology Articles / Research Papers**:
- Core Thesis & Contribution
- Technologies & Architectures (with diagrams description if present)
- Benchmarks & Results (preserve numbers)
- Concrete Examples / Use Cases
- Implications & Takeaways

For **Architecture Documents**:
- System Overview
- Components & Interactions (preserve component names and relationships)
- Technology Choices & Rationale
- Data Flows
- Constraints & Trade-offs
- Non-functional Requirements

For **Strategy/Vision Decks**:
- Vision Statement
- Strategic Axes with Target Metrics
- Roadmap with Timeline
- Enablers & Competitive Advantages
- Risks & Mitigation

For **Status Reports**:
- Current Status & Progress
- Key Achievements
- Risks & Blockers (with severity)
- Decisions Made
- Next Steps & Action Items

For **Demo/Product Showcases**:
- Product Overview
- Demonstrated Capabilities (step by step)
- Technical Stack
- Key Results & Metrics
- Differentiation

For **Meeting Notes**:
- Context (participants, date, purpose)
- Key Discussion Points
- Decisions Made
- Action Items (with owners and deadlines)
- Open Questions

For any other type, create an appropriate structure that captures the essential information.

## Step 4: End with these sections (when applicable)

### Action Items
| Item | Owner | Priority |
|------|-------|----------|
(only if actionable items are identified)

### Open Questions / Risks
Items that need clarification or attention.
"""

DOCUMENT_ANALYSIS_USER_PROMPT = "Analyze this document:\n\n{content}"
