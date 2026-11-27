# ExamEval — OCR-assisted answer evaluation

ExamEval is a teacher-facing research prototype for processing a printed question paper, a marking scheme and a handwritten answer sheet. It extracts text with PDF parsing/OCR, produces an initial offline semantic-plus-keyword estimate, and stores a teacher-reviewed report.

This repository is separate from Clutch Arena. It does not modify or depend on the Clutch Arena repository.

## Features

- Upload question paper, marking scheme and handwritten answer sheet (PDF, PNG, JPG or TXT; 15 MB each).
- Student name and Roll Number / USN, plus an assessment title.
- OCR/text extraction and an initial criterion-level estimate using a bundled all-MiniLM-L6-v2 sentence embedding model plus lexical matching.
- CPU-only semantic inference from a model downloaded into the Docker image at build time; no model download is needed while evaluating offline.
- Teacher sign-in and protected student/evaluation APIs.
- Persistent SQLite records for students, assessments, extracted text, estimated marks and teacher-confirmed marks.
- Searchable evaluation history and student directory.
- Teacher mark editing, save-review and finalize workflow.
- CSV export of an individual report.
- Installable offline-capable web app shell; the same-origin local deployment serves both UI and API.
- Local Docker deployment with persistent data stored in ./data.

## Offline use (recommended for a standalone computer)

The easiest offline setup is Docker Compose. You need internet **once** to download/build the application image and its dependencies. After the image is built, the complete local application (web interface, API, OCR engine and PDF renderer) runs on the computer without an internet connection.

1. Install Docker Engine and the Docker Compose plugin on the computer.
2. Clone this repository, then open a terminal in the repository folder.
3. Create your private local settings file:

   ```bash
   cp .env.example .env
   nano .env
   ```

   Replace both example values with private, long random strings. Do not share the .env file or upload it to GitHub.

4. Build the app and start it (first run needs internet):

   ```bash
   docker compose up -d --build
   ```

5. Open **http://localhost:8000** in the browser and sign in with the teacher password from .env.
6. Once the image is built and the app has started successfully, disconnect the computer from the internet and use **http://localhost:8000** as usual. Keep the computer and browser running locally; the app does not need Railway or Vercel for this mode.

Useful commands:

```bash
docker compose logs -f
docker compose stop
docker compose start
docker compose down
```

The SQLite database is stored in the local `./data/` folder and survives container restarts and `docker compose down`. Back up this folder regularly to another secure drive. Do not delete it if you want to keep saved student records.

The browser caches the application shell for offline reopening, but it deliberately does **not** cache authenticated API responses or student records. In local mode, the API and database run on the same computer, so evaluation and history continue to work without internet. The first successful page load is needed to install the service worker.

## Local development without Docker

Requirements: Python 3.11+, Node.js 20+, Tesseract OCR and Poppler utilities (for scanned PDFs).

Backend:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export EXAMEVAL_ADMIN_PASSWORD='choose-a-long-private-password'
export EXAMEVAL_TOKEN_SECRET='choose-a-long-random-secret'
export DB_PATH='../data/exameval.db'
uvicorn main:app --reload --port 8000
```

Frontend (in a second terminal):

```bash
cd frontend
npm install
npm run dev
```

Vite proxies API requests to the local backend. Open the development URL shown by Vite. The production-style offline experience is served from **http://localhost:8000** by the Docker image.

## Important evaluation limitation

The current scoring method combines the pre-trained English all-MiniLM-L6-v2 semantic embedding model (70% weight) with lexical F1 overlap (30% weight). The model is bundled into the Docker image during build, then loaded from local disk for CPU inference; evaluation does not call an online AI API. This is a general-purpose English sentence-similarity model, not a model trained specifically for grading. It can still misunderstand subject-specific meaning, negation, numerical reasoning, units, calculations, diagrams and OCR errors. A teacher must inspect extracted text and confirm every criterion. Do not use this prototype for high-stakes autonomous grading. Future fine-tuning should use teacher-verified answer/mark pairs and a separate validation set.

## Production deployment (optional; requires internet)

### Railway backend

- Deploy the repository with the backend service configured to use `backend/Dockerfile`.
- Attach a persistent Railway volume to the backend service at **`/data`**.
- Set these service variables in Railway:
  - `DB_PATH=/data/exameval.db`
  - `EXAMEVAL_ADMIN_PASSWORD` — a strong private teacher password. Never commit it to GitHub or put it in frontend variables.
  - `EXAMEVAL_TOKEN_SECRET` — set a stable random secret so sessions survive service restarts.
  - `CORS_ORIGINS` — comma-separated allowed frontend origins, e.g. `https://exameval-ocr-nlp.vercel.app`.
- The `/health` endpoint is public and contains no student data.

### Vercel frontend

- Import `Hemanth-DP18/exameval-ocr-nlp` with root directory `frontend`.
- Set `VITE_API_URL` to the Railway public API URL, e.g. `https://exameval-api-production.up.railway.app`.
- Redeploy after changing environment variables.

## Data and privacy

The SQLite database stores student identifiers, assessment metadata, extracted document text, marks and review status. In local mode it is stored under `./data` on the computer. Raw uploaded files are processed in memory and are not retained as original files. The API requires teacher authentication for student and evaluation data. Keep the teacher password private, restrict access to the computer and backups, and protect hosting accounts if using online mode. The browser service worker does not store private student API responses. This prototype does not yet provide multiple teacher accounts, role-based access, institution-level tenancy or automated backups.

## API overview

- `POST /api/login` — teacher sign-in, returns an expiring bearer token.
- `GET /api/me` — validate teacher session.
- `POST /api/evaluate` — process and persist an evaluation (authenticated).
- `GET /api/evaluations` — searchable, paginated history (authenticated).
- `GET /api/evaluations/{id}` — load a saved report (authenticated).
- `PATCH /api/evaluations/{id}/review` — save teacher marks or finalize (authenticated).
- `GET /api/students` — student directory (authenticated).
- `GET /health` — health check.

## Research integrity

Do not report accuracy, time savings or grading performance as experimental findings unless measured through a documented evaluation dataset and methodology.
