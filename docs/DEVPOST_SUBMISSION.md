# Devpost draft: StageSight

**Track:** Parallel

**Project:** StageSight
**Live app:** https://stagesight-web-479126230193.us-central1.run.app/en

## Inspiration

Choosing a location is one of the largest bottlenecks for directors and screenwriters. An imagined scene must become a real space with the right scale, camera possibilities, light, price and operating constraints. Much of that understanding normally arrives only after a physical scout.

## What it does

StageSight gathers real filming spaces from several Korean sources and helps crews evaluate them before visiting. Users browse original listings, adjust camera angle, field of view and time of day, and create a Gemini preview of how the space may feel on camera. They can paste or upload a screenplay, receive location recommendations for each scene, and refine them through chat using budget and mood.

For a selected venue, StageSight uses Parallel Search at runtime to research filming permits, noise and vehicle constraints. It shows links to the retrieved sources and leaves unsupported fields unconfirmed.

## How it was built

- Next.js and React frontend
- FastAPI and SQLite catalogue service
- Gemini through the `google-genai` SDK for screenplay interpretation, chat and image previews
- Parallel Search through the `parallel-web` SDK for sourced location research
- Google Cloud Run and Artifact Registry for deployment

## Limitations

The current catalogue focuses on South Korea. AI images are illustrative and do not prove dimensions, unseen geometry or real lighting. Listing availability, photo rights and filming permissions must be confirmed with the original provider and relevant authority. User activity is stored in the browser; there is no cloud account system.

## Demo focus

In three minutes: open the English workspace, choose a real venue, open its source listing, create a lighting preview, then run Parallel permit research and inspect the cited sources. Use the Korean workspace to show screenplay matching and chat, with English narration or subtitles.
