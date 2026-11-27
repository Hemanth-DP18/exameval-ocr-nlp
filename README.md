# ExamEval — OCR-assisted answer evaluation

ExamEval is a teacher-facing research prototype for processing a printed question paper, a marking scheme and a handwritten answer sheet. It extracts text with PDF parsing/OCR, produces an initial word-overlap estimate, and stores a teacher-reviewed report.

This repository is separate from Clutch Arena. It does not modify or depend on the Clutch Arena repository.

## Features

- Upload question paper, marking scheme and handwritten answer sheet (PDF, PNG, JPG or TXT; 15 MB each).
- Student name and Roll Number / USN, plus an assessment title.
- OCR/text extraction and an initial criterion-level estimate.
- Teacher sign-in and protected student/evaluation APIs.
- Persistent SQLite records for students, assessments, extracted text, estimated marks and teacher-confirmed marks.
- Searchable evaluation history and student directory.
- Teacher mark editing, save-review and finalize workflow.
- CSV export of an individual report.

## Important evaluation limitation

The current scoring method is a simple word-overlap/F1 baseline. It is **not validated semantic NLP** and must not be treated as an automatic grading decision. OCR can misread handwriting. Similarity scoring may fail to recognize equivalent wording, numerical reasoning, units, calculation steps and diagrams. A teacher must inspect the extracted content and confirm each criterion. This prototype has not been validated for high-stakes assessment.

## Local development

### Backend

Requirements: Python 3.11+, Tesseract OCR and Poppler utilities (for scanned PDFs).

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export EXAMEVAL_ADMIN_PASSWORD='choose-a-long-private-password'
export DB_PATH='./exameval.db'
uvicorn main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
```

Create `frontend/.env.local`:

```env
VITE_API_URL=http://localhost:8000
```

Then run `npm run dev`.

## Production deployment

### Railway backend

- Deploy the repository with the backend service configured to use `backend/Dockerfile`.
- Attach a persistent Railway volume to the backend service at **`/data`**.
- Set these service variables in Railway:
  - `DB_PATH=/data/exameval.db`
  - `EXAMEVAL_ADMIN_PASSWORD` — a strong private teacher password. Never commit it to GitHub or put it in frontend variables.
  - `EXAMEVAL_TOKEN_SECRET` — optional; if omitted, the service generates a process-local signing secret, which invalidates sessions after a restart.
  - `CORS_ORIGINS` — comma-separated allowed frontend origins, e.g. `https://exameval-ocr-nlp.vercel.app`.
- The `/health` endpoint is public and contains no student data.

### Vercel frontend

- Import `Hemanth-DP18/exameval-ocr-nlp` with root directory `frontend`.
- Set `VITE_API_URL` to the Railway public API URL, e.g. `https://exameval-api-production.up.railway.app`.
- Redeploy after changing environment variables.

## Data and privacy

The SQLite database stores student identifiers, assessment metadata, extracted document text, marks and review status on the Railway volume. Raw uploaded files are processed in memory and are not retained as original files. The API requires teacher authentication for student and evaluation data. Keep the teacher password private, restrict CORS to the real frontend origin, and protect access to the hosting accounts and backups. This prototype does not yet provide multiple teacher accounts, role-based access, institution-level tenancy or automated backups.

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
