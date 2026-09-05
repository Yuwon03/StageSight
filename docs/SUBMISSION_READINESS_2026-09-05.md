# Submission readiness — 5 September 2026

Later update: the English workspace and permit research are deployed. The audited release is public at https://github.com/Yuwon03/StageSight; see [release verification](RELEASE_2026-09-05.md).

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

1. The audited 93-file release is pushed to the public `Yuwon03/StageSight` repository. The local development repository keeps its fuller history and tracks the public release separately as `public-main`.
2. GitHub detects the tracked Apache-2.0 LICENSE. Secrets, local catalogue data, PDFs, internal assistant material and generated evaluation results are excluded from the public release.
3. No mandatory commit count or development diary was found. Commit real changes, push the runnable release, and record its SHA/tag in the submission. Never fabricate historical logs. Freeze the submitted version at the deadline.
4. `parallel_search.py` imports and calls Parallel Search. A live deployed request returned sourced English results; demonstrate this same path in the video.
5. The project uses a custom Python orchestrator and the Google Gen AI SDK. Public descriptions no longer claim an ADK or Vertex AI runtime.
6. The current account experience is localStorage, not authenticated cloud accounts. Registration is not needed for the guest scouting workflow. Prioritize reliable browsing, source attribution, AI results, partner evidence, error states, and an English demo before building authentication.
7. `/en` adds English catalogue pagination, category labels, venue selection, source link, fixed-viewpoint lighting preview, comparison and download, plus no-derivative enforcement. Korean source names remain intact. Screenplay matching and personal activity are not yet localized. This is a scoped English workspace, not full localization.

## Suggested judge walkthrough

Open `/en`, select a category and real venue, inspect the original listing, select a lighting scenario, generate and compare the preview. Explain that the output is an illustration, not a measured lighting/geometry guarantee. Separately demonstrate the existing Parallel permit-research flow with citations and a successful runtime call. Show the screenplay flow with English narration/subtitles until its UI and generated text are localized.

## Publication status

The English release is deployed and browser-verified. The public repository, README and Apache-2.0 license are accessible. Demo video creation and the completed Devpost form remain separate submission tasks.
