from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Header
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from pypdf import PdfReader
from PIL import Image
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from datetime import datetime, timezone
import io, os, re, json, sqlite3, secrets

app=FastAPI(title="ExamEval API",version="1.0.0")
origins=[x.strip() for x in os.getenv("CORS_ORIGINS","").split(",") if x.strip()]
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=False,allow_methods=["GET","POST","PATCH","OPTIONS"],allow_headers=["Authorization","Content-Type"])
DB_PATH=os.getenv("DB_PATH","./exameval.db")
STATIC_DIR=Path(__file__).parent / "static"
ADMIN_PASSWORD=os.getenv("EXAMEVAL_ADMIN_PASSWORD","")
TOKEN_SECRET=os.getenv("EXAMEVAL_TOKEN_SECRET") or secrets.token_urlsafe(32)
signer=URLSafeTimedSerializer(TOKEN_SECRET,salt="exameval-teacher-session")
ALLOWED={".pdf",".png",".jpg",".jpeg",".txt"}
MAX_SIZE=15*1024*1024

def connect():
    Path(DB_PATH).parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(DB_PATH,timeout=30)
    db.row_factory=sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db

def init_db():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS students(
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, roll_no TEXT NOT NULL UNIQUE,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS evaluations(
          id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER NOT NULL REFERENCES students(id),
          assessment_title TEXT NOT NULL DEFAULT 'Untitled assessment', created_at TEXT NOT NULL,
          question_paper_text TEXT NOT NULL, marking_scheme_text TEXT NOT NULL, answer_text TEXT NOT NULL,
          results_json TEXT NOT NULL, estimated_total REAL NOT NULL, confirmed_total REAL,
          maximum_marks REAL NOT NULL, review_status TEXT NOT NULL DEFAULT 'pending'
        );
        CREATE INDEX IF NOT EXISTS idx_evaluations_created ON evaluations(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_students_roll ON students(roll_no);
        """)
init_db()

def now(): return datetime.now(timezone.utc).isoformat()
def require_teacher(authorization: str=Header(default="")):
    if not ADMIN_PASSWORD: raise HTTPException(503,"Teacher login is not configured. Set EXAMEVAL_ADMIN_PASSWORD.")
    if not authorization.startswith("Bearer "): raise HTTPException(401,"Please sign in as a teacher.")
    try: signer.loads(authorization[7:],max_age=43200)
    except SignatureExpired: raise HTTPException(401,"Your session expired. Please sign in again.")
    except BadSignature: raise HTTPException(401,"Invalid session. Please sign in again.")
    return True

@app.get("/")
def root():
    index=STATIC_DIR / "index.html"
    if index.is_file(): return FileResponse(index)
    return {"service":"ExamEval API","version":app.version,"docs":"/docs","offline_mode":"Use the bundled local web app or run the frontend separately."}
@app.get("/health")
def health():
    checks={"database":False,"ocr":False,"pdf_renderer":False,"semantic_model":False}
    try:
        with connect() as db:
            db.execute("SELECT 1").fetchone()
        checks["database"]=True
    except Exception:
        pass
    try:
        import pytesseract
        checks["ocr"]=bool(__import__("shutil").which("tesseract"))
    except Exception:
        pass
    checks["pdf_renderer"]=bool(__import__("shutil").which("pdftoppm"))
    checks["semantic_model"]=(Path(os.getenv("EXAMEVAL_MODEL_PATH","/opt/exameval-model")) / "config.json").is_file()
    healthy=all(checks.values())
    return {"status":"healthy" if healthy else "degraded","checks":checks,"version":app.version}
@app.post("/api/login")
def login(payload:dict):
    if not ADMIN_PASSWORD: raise HTTPException(503,"Teacher login is not configured.")
    if not secrets.compare_digest(str(payload.get("password","")),ADMIN_PASSWORD):
        raise HTTPException(401,"Incorrect teacher password.")
    return {"token":signer.dumps({"role":"teacher"}),"expires_in":43200}
@app.get("/api/me")
def me(_:bool=Depends(require_teacher)): return {"role":"teacher","authenticated":True}

async def read_file(f):
    ext=Path(f.filename or "").suffix.lower()
    if ext not in ALLOWED: raise HTTPException(400,"Use PDF, PNG, JPG or TXT files.")
    data=await f.read()
    if not data: raise HTTPException(400,"The uploaded file is empty.")
    if len(data)>MAX_SIZE: raise HTTPException(413,"Each file must be 15 MB or smaller.")
    if ext==".pdf" and not data.startswith(b"%PDF-"):
        raise HTTPException(400,"The selected PDF does not have a valid PDF signature.")
    if ext in {".jpg",".jpeg"} and not data.startswith(b"\xff\xd8\xff"):
        raise HTTPException(400,"The selected file is not a valid JPEG image.")
    if ext==".png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(400,"The selected file is not a valid PNG image.")
    return data,ext

def ocr(img):
    try:
        import pytesseract
        return pytesseract.image_to_string(img)
    except Exception: raise HTTPException(503,"OCR service is unavailable. Check the backend OCR installation.")

def extract(data,ext):
    if ext==".txt": return data.decode("utf-8",errors="replace")[:50000]
    if ext==".pdf":
        try:
            reader=PdfReader(io.BytesIO(data))
            pages=reader.pages[:12]
            text="\n".join(p.extract_text() or "" for p in pages)
            if text.strip(): return text[:50000]
            from pdf2image import convert_from_bytes
            return "\n".join(ocr(im) for im in convert_from_bytes(data,first_page=1,last_page=12))[:50000]
        except HTTPException: raise
        except Exception: raise HTTPException(422,"PDF extraction failed. Check that the PDF is readable.")
    try: return ocr(Image.open(io.BytesIO(data)))[:50000]
    except HTTPException: raise
    except Exception: raise HTTPException(422,"Image processing failed. Use a clear PNG or JPG image.")

def words(s): return {w for w in re.findall(r"[a-z0-9]+",s.lower()) if len(w)>1}

_SEMANTIC_MODEL=None
def score(a,e):
    """Offline hybrid estimate: semantic embeddings plus lexical overlap."""
    global _SEMANTIC_MODEL
    aa,ee=words(a),words(e)
    if not a.strip() or not e.strip(): return 0.0
    p=len(aa&ee)/len(aa) if aa else 0.0
    r=len(aa&ee)/len(ee) if ee else 0.0
    lexical=(2*p*r/(p+r)) if p+r else 0.0
    try:
        if _SEMANTIC_MODEL is None:
            from sentence_transformers import SentenceTransformer
            model_path=Path(os.getenv("EXAMEVAL_MODEL_PATH","/opt/exameval-model"))
            if not model_path.is_dir():
                raise RuntimeError("Local semantic model directory is missing.")
            _SEMANTIC_MODEL=SentenceTransformer(str(model_path),device="cpu",local_files_only=True)
        vectors=_SEMANTIC_MODEL.encode([a[:4000],e[:4000]],convert_to_tensor=True,normalize_embeddings=True)
        semantic=max(0.0,min(1.0,float((vectors[0]*vectors[1]).sum().item())))
        return round(max(0.0,min(1.0,0.70*semantic+0.30*lexical)),6)
    except Exception:
        raise HTTPException(503,"The offline semantic model could not be loaded. Rebuild the local ExamEval image while connected to the internet, then retry.")

def rubric(text):
    """Read common explicit marking-scheme lines; use a visible placeholder fallback."""
    patterns=[
      re.compile(r"\s*(?:q(?:uestion)?\s*)?(\d+)\s*[:.)-]\s*(.*?)\s*\|\s*marks?\s*[:=]\s*(\d+(?:\.\d+)?)\s*$",re.I),
      re.compile(r"\s*(?:q(?:uestion)?\s*)?(\d+)\s*[:.)-]\s*(.*?)\s*\(\s*(\d+(?:\.\d+)?)\s*marks?\s*\)\s*$",re.I),
      re.compile(r"\s*(?:q(?:uestion)?\s*)?(\d+)\s*[:.)-]\s*(.*?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*marks?\s*$",re.I)
    ]
    out=[]; seen=set()
    for line in text.splitlines():
        for pattern in patterns:
            m=pattern.match(line)
            if m:
                qno=int(m.group(1)); expected=m.group(2).strip(); marks=float(m.group(3))
                if expected and marks>0 and qno not in seen:
                    out.append({"criterion":f"Q{qno}","expected":expected,"max_marks":marks})
                    seen.add(qno)
                break
    if out:
        if len(out)>100: raise HTTPException(422,"The marking scheme contains more than 100 criteria. Please split the assessment.")
        return out
    paras=[x.strip() for x in re.split(r"\n\s*\n",text) if x.strip()]
    if len(paras)>100: raise HTTPException(422,"The marking scheme contains more than 100 text blocks. Please split the assessment.")
    return [{"criterion":f"Criterion {i+1}","expected":p,"max_marks":1.0,"rubric_warning":"Unstructured scheme: 1 mark per text block is only a placeholder. Edit marks before finalizing."} for i,p in enumerate(paras)]

def answer_for_question(answer_text, criterion):
    match=re.fullmatch(r"Q(\d+)",criterion,re.I)
    if not match:return answer_text
    qno=match.group(1)
    # Accept common handwritten/OCR headings: "1.", "Question 1:", and "Q1:".
    pattern=re.compile(r"(?im)^\s*(?:(?:question|q)\s*)?"+re.escape(qno)+r"\s*[.):/-]\s*")
    matches=list(pattern.finditer(answer_text))
    if not matches:return answer_text
    start=matches[0].end()
    next_match=re.search(r"(?im)^\s*(?:(?:question|q)\s*)?\d+\s*[.):/-]\s*",answer_text[start:])
    end=start+next_match.start() if next_match else len(answer_text)
    return answer_text[start:end].strip() or answer_text

def evaluation_dict(row,include_text=True):
    d=dict(row)
    results=json.loads(d.pop("results_json"))
    d["results"]=results
    d["student_name"]=d.pop("name",None)
    d["student_roll_no"]=d.pop("roll_no",None)
    d["percentage"]=round(100*(d["confirmed_total"] if d["confirmed_total"] is not None else d["estimated_total"])/d["maximum_marks"],2) if d["maximum_marks"] else 0
    d["total_marks"]=d["confirmed_total"] if d["confirmed_total"] is not None else d["estimated_total"]
    d["is_finalized"]=d["review_status"]=="finalized"
    if not include_text:
        d.pop("question_paper_text",None);d.pop("marking_scheme_text",None);d.pop("answer_text",None)
    return d

@app.post("/api/evaluate")
async def evaluate(question_paper:UploadFile=File(...),marking_scheme:UploadFile=File(...),answer_sheet:UploadFile=File(...),student_name:str=Form("Student"),student_roll_no:str=Form(...),assessment_title:str=Form("Untitled assessment"),_:bool=Depends(require_teacher)):
    roll=student_roll_no.strip().upper()
    if not roll or len(roll)>80 or not re.fullmatch(r"[A-Z0-9][A-Z0-9/_-]{0,79}",roll):
        raise HTTPException(422,"Enter a valid Roll Number or USN using letters, numbers, /, _ or - (maximum 80 characters).")
    if len(assessment_title.strip())>160:
        raise HTTPException(422,"Assessment title must be 160 characters or fewer.")
    if len(student_name.strip())>120:
        raise HTTPException(422,"Student name must be 120 characters or fewer.")
    qd,qe=await read_file(question_paper); sd,se=await read_file(marking_scheme); ad,ae=await read_file(answer_sheet)
    qt=extract(qd,qe); st=extract(sd,se); at=extract(ad,ae)
    criteria=rubric(st)
    if not criteria: raise HTTPException(422,"No marking criteria found. Use one line per criterion, e.g. Q1: expected answer | marks: 5")
    rows=[]; total=maximum=0.0
    for c in criteria:
        candidate=answer_for_question(at,c["criterion"])
        sim=score(candidate,c["expected"])
        awarded=round(sim*c["max_marks"],2)
        total+=awarded; maximum+=c["max_marks"]
        # Numeric flags are advisory only; they never change the estimated score.
        expected_numbers=re.findall(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?",c["expected"])
        answer_numbers=re.findall(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?",candidate)
        numeric_check=("not_applicable" if not expected_numbers else
                       ("exact_tokens" if sorted(expected_numbers)==sorted(answer_numbers) else "teacher_check"))
        rows.append({**c,"student_answer":candidate[:4000],"numeric_check":numeric_check,
                     "similarity":round(sim,3),"awarded_marks":awarded,"estimated_marks":awarded,
                     "teacher_marks":None,"review_required":True})
    timestamp=now()
    with connect() as db:
        student=db.execute("SELECT id FROM students WHERE roll_no=?",(roll,)).fetchone()
        if student:
            sid=student["id"]
            db.execute("UPDATE students SET name=?,updated_at=? WHERE id=?",(student_name.strip() or "Student",timestamp,sid))
        else:
            cur=db.execute("INSERT INTO students(name,roll_no,created_at,updated_at) VALUES(?,?,?,?)",(student_name.strip() or "Student",roll,timestamp,timestamp));sid=cur.lastrowid
        cur=db.execute("""INSERT INTO evaluations(student_id,assessment_title,created_at,question_paper_text,marking_scheme_text,answer_text,results_json,estimated_total,maximum_marks,review_status)
          VALUES(?,?,?,?,?,?,?,?,?,'pending')""",(sid,assessment_title.strip() or "Untitled assessment",timestamp,qt[:50000],st[:50000],at[:50000],json.dumps(rows),round(total,2),round(maximum,2)))
        eid=cur.lastrowid
        row=db.execute("""SELECT e.*,s.name,s.roll_no FROM evaluations e JOIN students s ON s.id=e.student_id WHERE e.id=?""",(eid,)).fetchone()
    report=evaluation_dict(row)
    report["scoring_method"]="offline_semantic_plus_lexical_estimate"
    report["semantic_model"]="sentence-transformers/all-MiniLM-L6-v2"
    report["teacher_review_required"]=True
    return report

@app.get("/api/evaluations")
def list_evaluations(q:str="",limit:int=50,offset:int=0,_:bool=Depends(require_teacher)):
    limit=max(1,min(limit,100));offset=max(0,offset);term=f"%{q.strip()}%"
    with connect() as db:
        rows=db.execute("""SELECT e.id,e.assessment_title,e.created_at,e.estimated_total,e.confirmed_total,e.maximum_marks,e.review_status,s.name,s.roll_no
          FROM evaluations e JOIN students s ON s.id=e.student_id
          WHERE s.name LIKE ? OR s.roll_no LIKE ? OR e.assessment_title LIKE ?
          ORDER BY e.created_at DESC LIMIT ? OFFSET ?""",(term,term,term,limit,offset)).fetchall()
        total=db.execute("""SELECT COUNT(*) n FROM evaluations e JOIN students s ON s.id=e.student_id
          WHERE s.name LIKE ? OR s.roll_no LIKE ? OR e.assessment_title LIKE ?""",(term,term,term)).fetchone()["n"]
    return {"items":[dict(x) for x in rows],"total":total,"limit":limit,"offset":offset}

@app.get("/api/students")
def list_students(q:str="",limit:int=100,_:bool=Depends(require_teacher)):
    term=f"%{q.strip()}%"
    with connect() as db:
        rows=db.execute("""SELECT s.id,s.name,s.roll_no,s.created_at,s.updated_at,COUNT(e.id) evaluation_count,
          MAX(e.created_at) latest_evaluation FROM students s LEFT JOIN evaluations e ON e.student_id=s.id
          WHERE s.name LIKE ? OR s.roll_no LIKE ? GROUP BY s.id ORDER BY latest_evaluation DESC LIMIT ?""",(term,term,max(1,min(limit,200)))).fetchall()
    return {"items":[dict(x) for x in rows]}

@app.get("/api/evaluations/{evaluation_id}")
def get_evaluation(evaluation_id:int,_:bool=Depends(require_teacher)):
    with connect() as db:
        row=db.execute("""SELECT e.*,s.name,s.roll_no FROM evaluations e JOIN students s ON s.id=e.student_id WHERE e.id=?""",(evaluation_id,)).fetchone()
    if not row: raise HTTPException(404,"Evaluation not found.")
    return evaluation_dict(row)

@app.patch("/api/evaluations/{evaluation_id}/review")
def review_evaluation(evaluation_id:int,payload:dict,_:bool=Depends(require_teacher)):
    with connect() as db:
        row=db.execute("SELECT * FROM evaluations WHERE id=?",(evaluation_id,)).fetchone()
        if not row: raise HTTPException(404,"Evaluation not found.")
        results=json.loads(row["results_json"])
        edits=payload.get("results",[])
        by_name={str(x.get("criterion")):x for x in edits}
        for item in results:
            edit=by_name.get(item["criterion"])
            if edit is not None:
                try: marks=float(edit.get("teacher_marks"))
                except (TypeError,ValueError): raise HTTPException(422,"Every reviewed mark must be a number.")
                if marks<0 or marks>float(item["max_marks"]): raise HTTPException(422,f"Marks for {item['criterion']} must be between 0 and {item['max_marks']}.")
                item["teacher_marks"]=round(marks,2);item["awarded_marks"]=round(marks,2);item["review_required"]=False
        finalized=bool(payload.get("finalized",False))
        if finalized and any(x["teacher_marks"] is None for x in results):
            raise HTTPException(422,"Review every criterion before finalizing.")
        total=round(sum(float(x["awarded_marks"]) for x in results),2)
        status="finalized" if finalized else ("reviewing" if any(x["teacher_marks"] is not None for x in results) else "pending")
        db.execute("UPDATE evaluations SET results_json=?,confirmed_total=?,review_status=? WHERE id=?",(json.dumps(results),total if finalized else None,status,evaluation_id))
        updated=db.execute("""SELECT e.*,s.name,s.roll_no FROM evaluations e JOIN students s ON s.id=e.student_id WHERE e.id=?""",(evaluation_id,)).fetchone()
    return evaluation_dict(updated)



# Serve the bundled web application from the same local server. API routes above take precedence.
if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="frontend")
