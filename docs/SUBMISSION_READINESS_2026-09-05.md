# Submission readiness — 5 September 2026

Later update: English workspace and English permit research are now deployed. Source committed locally as `d48c3e0`; see [release verification](RELEASE_2026-09-05.md). The inventory findings below describe the pre-release audit.

## Repository inventory

- `apps/web/src/app`: catalogue `/`, venue simulator `/location/[id]`, browser-local activity `/me`, and the newly added English scouting workspace `/en`.
- `apps/web/src/components`: catalogue filters/cards, screenplay matcher, orbit picker, favourites.
- `apps/web/src/lib`: API client, solar calculations, local user/conversation/render persistence.
- `services/agent/app`: FastAPI routes, catalogue/store, upload and image handling, Gemini and Parallel tools, geometry/solar workflow, tests.
- `services/crawler`: worker and provider adapters (Hourplace, PlaceHub, public data, tourism, heritage).
- `services/agent/evaluation`: image evaluation harness and generated results.
- `infra`: Cloud Run deployment and two Dockerfiles. Production catalogue is an image-baked snapshot; crawler refresh is local.
- `docs`: architecture, demo/submission drafts, research and handoff notes.
- Root: Apache-2.0 LICENSE, README, AGENTS/CLAUDE guidance, environment templates, design PDFs.

## Official requirements checked

Sources: [overview](https://agentic-cinema.devpost.com/), [rules](https://agentic-cinema.devpost.com/rules), [schedule](https://agentic-cinema.devpost.com/details/dates).

Submission deadline: 9 September 2026, 14:00 PDT = 10 September, 07:00 Sydney AEST = 10 September, 06:00 Korea KST.
Schedule lists judging 10 September–8 October PDT and announcement 13 October, noon PDT (14 October, 06:00 Sydney AEDT). Rules instead describe approximately 23 September–7 October judging. Keep the submitted service available through announcement and seek organizer clarification if necessary.

Screening checks required materials and working Google/partner integration. Four equally weighted criteria cover engineering/integration, coherent product design, demonstrated audience impact, and originality. Accounts, billing, and commercial launch are not independently required. A reliable complete user journey matters; browser-local storage must be described accurately.

Provide a hosted URL, public GitHub/GitLab/Bitbucket repository, detectable open-source license, runnable source/setup instructions, English submission materials, public demonstration video (overview requests a three-minute demo), chosen track, and completed Devpost form. Parallel track requires actual Search API runtime usage. Rules list google-genai among accepted Google SDKs; ADK is not the only accepted package. Other-vendor AI tooling restrictions remain an eligibility issue to clarify with organizers given development history; do not conceal tool usage.

## Findings and priority

1. Local git has no remote configured. Latest commit is `de50555` dated 29 August; many current files are modified/untracked. Deployment does not publish source. A remote repository elsewhere may exist, but it is not linked here.
2. Apache-2.0 LICENSE exists and is tracked. Check visibility/detection after publishing. Do not include secrets, personal scripts, caches, or third-party catalogue/photos under the code license without appropriate rights.
3. No mandatory commit count or development diary was found. Commit real changes, push the runnable release, and record its SHA/tag in the submission. Never fabricate historical logs. Freeze the submitted version at the deadline.
4. `parallel_search.py` imports and calls Parallel Search; verify the live judge journey reaches it and exposes usable evidence. A healthy API alone does not prove a successful partner call.
5. `workflow.py` calls itself an ADK workflow but is a custom Python orchestrator; do not claim actual ADK usage based on the docstring alone.
6. The current account experience is localStorage, not authenticated cloud accounts. Registration is not needed for the guest scouting workflow. Prioritize reliable browsing, source attribution, AI results, partner evidence, error states, and an English demo before building authentication.
7. `/en` adds English catalogue pagination, category labels, venue selection, source link, fixed-viewpoint lighting preview, comparison and download, plus no-derivative enforcement. Korean source names remain intact. Screenplay matching and personal activity are not yet localized. This is a scoped English workspace, not full localization.

## Suggested judge walkthrough

Open `/en`, select a category and real venue, inspect the original listing, select a lighting scenario, generate and compare the preview. Explain that the output is an illustration, not a measured lighting/geometry guarantee. Separately demonstrate the existing Parallel permit-research flow with citations and a successful runtime call. Show the screenplay flow with English narration/subtitles until its UI and generated text are localized.

## Publication status

No repository was created or pushed by this review. New English source is local and has not been deployed. `npm run build` passed, including TypeScript validation and `/en` prerendering. Browser interaction and live generation through the new page have not yet been verified. Existing Cloud Run web revision is `stagesight-web-00005-fs7`, URL `https://stagesight-web-c7gdwequ2q-uc.a.run.app`; API health returned healthy. A repository-wide whitespace check reports a pre-existing trailing blank line in `services/agent/app/models/korean_locations.py:83`.
