# Work Pulse Stage Demo Frontend

Standalone Next.js UI for stage demonstrations of the Work Pulse backend modules.

## Features

- Pipeline Runner: baseline ingestion -> drift baseline -> update ingestion -> risk -> drift -> brief
- Chat Console: intent-based orchestration flow
- File Recognition: upload file and inspect extraction output
- Model Settings: per-module traditional/LLM mode and tier control
- Backend mode switch: `mock` or `real`

## Pages

- `/` overview
- `/pipeline` pipeline test flow
- `/chat` conversation testing
- `/file-recognition` file ingestion testing
- `/settings` module model controls

## Environment

Copy `.env.local.example` to `.env.local` and adjust:

```bash
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
NEXT_PUBLIC_DEMO_MODE=mock
```

## Run

```bash
npm install
npm run dev
```

Open `http://localhost:3000`.

## Real backend usage

1. Start FastAPI:
   - from repo root: `cd work-pulse`
   - run: `uvicorn api.main:app --reload --port 8000`
2. In UI `/settings`:
   - set API mode to `real`
   - confirm API base URL is `http://localhost:8000`
3. Run `/pipeline`, `/chat`, and `/file-recognition`.
