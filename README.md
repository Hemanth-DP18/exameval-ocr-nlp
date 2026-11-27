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

## Offline use on Windows 10/11 and Lubuntu

ExamEval uses the same Docker Compose deployment on Windows 10, Windows 11 and Lubuntu. The container includes the web interface, API, Tesseract OCR, PDF renderer and the pre-downloaded semantic model. Runtime evaluation uses the local model and does not call an online AI API.

**One-time preparation (internet required):**

1. Install Docker:
   - **Windows 10/11:** Install Docker Desktop, enable the WSL 2 backend and start Docker Desktop. Hardware virtualization must be enabled. Docker Desktop availability depends on the Windows edition and system requirements; if it is not supported on your PC, use a compatible Linux installation.
   - **Lubuntu:** Install Docker Engine and the Docker Compose plugin using the official Docker instructions for Ubuntu-compatible distributions, then ensure your user can run Docker.
2. Clone this repository and open a terminal/PowerShell in the repository folder.
3. Create a private `.env` file by copying `.env.example`. Set a strong private teacher password and a long random token secret. Never share or commit this file.
4. Build the image and start ExamEval. This first build downloads the app dependencies and model, so allow time and disk space for it.

**Windows 10/11 quick start:**

Double-click `start_exam_eval.bat` in File Explorer, or run it from PowerShell:

```powershell
.\start_exam_eval.bat
```

The helper creates `.env` on first run and asks you to set the teacher password and token secret. It then builds/starts the app and opens **http://localhost:8000**. To stop it, run `stop_exam_eval.bat`.

**Lubuntu quick start:**

```bash
chmod +x start_exam_eval.sh
./start_exam_eval.sh
```

The helper creates `.env` on first run and prompts for the private teacher password and token secret. It then builds/starts the app. Open **http://localhost:8000**. To stop it, run `docker compose stop`.

**After the first successful build:** verify that the app opens and that `/health` reports all checks healthy. Then disconnect from the internet and test sign-in, a sample evaluation, review and history. You can use the app offline as long as the computer remains on and Docker is running. Keep your `data/` folder safe and back it up regularly.

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
