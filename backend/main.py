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
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=False,allow_methods=["GET","POST","PUT","PATCH","DELETE","OPTIONS"],allow_headers=["Authorization","Content-Type"])
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
        CREATE TABLE IF NOT EXISTS assessment_template(
          id INTEGER PRIMARY KEY CHECK(id=1), assessment_title TEXT NOT NULL DEFAULT '',
          question_paper_name TEXT, question_paper_data BLOB, question_paper_ext TEXT,
          marking_scheme_name TEXT, marking_scheme_data BLOB, marking_scheme_ext TEXT,
          updated_at TEXT NOT NULL
        );
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
    checks={"database":False,"ocr":False,"pdf_renderer":False,"semantic_model":False,"handwriting_model":False}
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
    checks["handwriting_model"]=(Path(os.getenv("EXAMEVAL_HTR_MODEL_PATH","/opt/exameval-htr")) / "config.json").is_file()
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
    except Exception:
        raise HTTPException(503,"Printed-text OCR is unavailable. Check the Tesseract installation.")

_HTR_PROCESSOR=None
_HTR_MODEL=None
def load_htr():
    """Load the small local TrOCR model once per worker."""
    global _HTR_PROCESSOR,_HTR_MODEL
    if _HTR_MODEL is not None and _HTR_PROCESSOR is not None:
        return _HTR_PROCESSOR,_HTR_MODEL
    model_path=os.getenv("EXAMEVAL_HTR_MODEL_PATH","/opt/exameval-htr")
    if not Path(model_path).is_dir():
        raise RuntimeError("Local handwriting model is missing.")
    import torch
    from transformers import TrOCRProcessor, VisionEncoderDecoderModel
    torch.set_num_threads(max(1,int(os.getenv("EXAMEVAL_TORCH_THREADS","2"))))
    _HTR_PROCESSOR=TrOCRProcessor.from_pretrained(model_path,local_files_only=True)
    _HTR_MODEL=VisionEncoderDecoderModel.from_pretrained(model_path,local_files_only=True)
    _HTR_MODEL.to("cpu").eval()
    return _HTR_PROCESSOR,_HTR_MODEL

def handwritten_lines(page):
    """Detect handwriting lines while suppressing photographed-page shadows and borders."""
    import numpy as np
    from PIL import ImageOps, ImageFilter

    image=ImageOps.exif_transpose(page).convert("L")
    image.thumbnail((1800,2400))
    width,height=image.size

    # Remove the photographed desk/page border. These margins contain shadows
    # and page edges that previously made every row look like handwriting.
    image=image.crop((int(width*.04),int(height*.025),int(width*.96),int(height*.98)))
    width,height=image.size

    # Local background subtraction handles uneven lighting better than one
    # global gray threshold on a photographed sheet.
    radius=max(12,int(min(width,height)*.035))
    background=image.filter(ImageFilter.GaussianBlur(radius=radius))
    pixels=np.asarray(image,dtype=np.int16)
    local_bg=np.asarray(background,dtype=np.int16)
    ink=(local_bg-pixels)>24

    # Require a substantial amount of ink across a row. A few dark camera
    # noise pixels must not turn an entire page into one giant "text line".
    row_ink=ink.sum(axis=1)
    row_threshold=max(8,int(width*.08))
    active=row_ink>=row_threshold

    bands=[]
    start=None
    last=None
    gap=max(8,int(height*.006))
    for y,on in enumerate(active):
        if on:
            if start is None:
                start=y
            last=y
        elif start is not None and y-last>gap:
            if last-start>=3:
                bands.append((start,last+1))
            start=None
    if start is not None and last-start>=7:
        bands.append((start,last+1))

    # Join short fragments from the same slanted/cursive writing line.
    merged=[]
    join_gap=max(8,int(height*.008))
    for top,bottom in bands:
        if merged and top-merged[-1][1]<=join_gap:
            merged[-1]=(merged[-1][0],bottom)
        else:
            merged.append((top,bottom))

    lines=[]
    pad=max(8,int(height*.006))
    for top,bottom in merged:
        # Split unusually tall regions into overlapping strips instead of
        # sending a whole photographed page to a line-level TrOCR model.
        max_line_height=max(70,int(height*.065))
        if bottom-top>max_line_height*1.6:
            step=max(45,int(max_line_height*.78))
            chunks=[]
            y=top
            while y<bottom:
                chunks.append((y,min(bottom,y+max_line_height)))
                if y+max_line_height>=bottom:
                    break
                y+=step
        else:
            chunks=[(top,bottom)]

        for line_top,line_bottom in chunks:
            y0=max(0,line_top-pad)
            y1=min(height,line_bottom+pad)
            row_mask=ink[y0:y1]
            if row_mask.size==0 or int(row_mask.sum())<max(30,int(width*.025)):
                continue
            xs=np.where(row_mask.any(axis=0))[0]
            if not len(xs):
                continue
            x0=max(0,int(xs[0])-12)
            x1=min(width,int(xs[-1])+13)
            crop=image.crop((x0,y0,x1,y1))
            if crop.width>=40 and crop.height>=10:
                lines.append(crop.convert("RGB"))

    return lines[:220]

def recognize_handwriting(page):
    """Run TrOCR over line crops; the model is trained for line-level images."""
    try:
        processor,model=load_htr()
        import torch
        lines=handwritten_lines(page)
        if not lines: return ""
        output=[]
        for offset in range(0,len(lines),2):
            batch=lines[offset:offset+2]
            pixels=processor(images=batch,return_tensors="pt").pixel_values
            with torch.inference_mode():
                ids=model.generate(pixels,num_beams=1,max_new_tokens=128)
            output.extend(x.strip() for x in processor.batch_decode(ids,skip_special_tokens=True) if x.strip())
        return "\n".join(output)
    except Exception as exc:
        raise HTTPException(503,"Handwriting recognition could not load or process this page. Check the local TrOCR installation. "+str(exc)[:200])

def extract(data,ext,handwriting=False):
    if ext==".txt": return data.decode("utf-8",errors="replace")[:50000]
    if ext==".pdf":
        try:
            reader=PdfReader(io.BytesIO(data))
            pages=reader.pages[:12]
            embedded="\n".join(p.extract_text() or "" for p in pages)
            if embedded.strip() and not handwriting: return embedded[:50000]
            from pdf2image import convert_from_bytes
            images=convert_from_bytes(data,first_page=1,last_page=min(12,len(reader.pages)),dpi=160)
            if handwriting:
                return "\n\n".join(recognize_handwriting(im) for im in images)[:50000]
            if embedded.strip(): return embedded[:50000]
            return "\n".join(ocr(im) for im in images)[:50000]
        except HTTPException: raise
        except Exception as exc:
            raise HTTPException(422,"PDF extraction failed. Check that the PDF is readable. "+str(exc)[:160])
    try:
        image=Image.open(io.BytesIO(data))
        return (recognize_handwriting(image) if handwriting else ocr(image))[:50000]
    except HTTPException: raise
    except Exception as exc:
        raise HTTPException(422,"Image processing failed. Use a clear PNG or JPG image. "+str(exc)[:160])

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

@app.get("/api/template")
def get_template(_:bool=Depends(require_teacher)):
    with connect() as db:
        row=db.execute("SELECT * FROM assessment_template WHERE id=1").fetchone()
    if not row:
        return {"assessment_title":"","question_paper":None,"marking_scheme":None,"ready":False}
    q=bool(row["question_paper_data"]); s=bool(row["marking_scheme_data"])
    return {"assessment_title":row["assessment_title"],
            "question_paper":{"name":row["question_paper_name"]} if q else None,
            "marking_scheme":{"name":row["marking_scheme_name"]} if s else None,
            "ready":q and s,"updated_at":row["updated_at"]}

@app.put("/api/template/subject")
def save_template_subject(assessment_title:str=Form(...),_:bool=Depends(require_teacher)):
    title=assessment_title.strip()
    if not title or len(title)>160:
        raise HTTPException(422,"Enter a subject or assessment name (maximum 160 characters).")
    with connect() as db:
        db.execute("""INSERT INTO assessment_template(id,assessment_title,updated_at) VALUES(1,?,?)
          ON CONFLICT(id) DO UPDATE SET assessment_title=excluded.assessment_title,updated_at=excluded.updated_at""",(title,now()))
    return get_template(True)

@app.put("/api/template/{kind}")
async def save_template_file(kind:str,file:UploadFile=File(...),_:bool=Depends(require_teacher)):
    if kind not in {"question_paper","marking_scheme"}:
        raise HTTPException(404,"Unknown template document.")
    data,ext=await read_file(file)
    name=Path(file.filename or f"{kind}{ext}").name
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO assessment_template(id,assessment_title,updated_at) VALUES(1,'',?)",(now(),))
        db.execute(f"UPDATE assessment_template SET {kind}_name=?,{kind}_data=?,{kind}_ext=?,updated_at=? WHERE id=1",
                   (name,sqlite3.Binary(data),ext,now()))
    return get_template(True)

@app.delete("/api/template/{kind}")
def clear_template_file(kind:str,_:bool=Depends(require_teacher)):
    if kind not in {"question_paper","marking_scheme"}:
        raise HTTPException(404,"Unknown template document.")
    with connect() as db:
        db.execute(f"UPDATE assessment_template SET {kind}_name=NULL,{kind}_data=NULL,{kind}_ext=NULL,updated_at=? WHERE id=1",(now(),))
    return get_template(True)

@app.post("/api/evaluate")
async def evaluate(answer_sheet:UploadFile=File(...),question_paper:UploadFile|None=File(None),marking_scheme:UploadFile|None=File(None),student_name:str=Form("Student"),student_roll_no:str=Form(...),assessment_title:str=Form(""),_:bool=Depends(require_teacher)):
    roll=student_roll_no.strip().upper()
    if not roll or len(roll)>80 or not re.fullmatch(r"[A-Z0-9][A-Z0-9/_-]{0,79}",roll):
        raise HTTPException(422,"Enter a valid Roll Number or USN using letters, numbers, /, _ or - (maximum 80 characters).")
    if len(assessment_title.strip())>160:
        raise HTTPException(422,"Assessment title must be 160 characters or fewer.")
    if len(student_name.strip())>120:
        raise HTTPException(422,"Student name must be 120 characters or fewer.")
    ad,ae=await read_file(answer_sheet)
    if question_paper is not None and marking_scheme is not None:
        qd,qe=await read_file(question_paper); sd,se=await read_file(marking_scheme)
    else:
        with connect() as db:
            template=db.execute("SELECT * FROM assessment_template WHERE id=1").fetchone()
        if not template or not template["question_paper_data"] or not template["marking_scheme_data"]:
            raise HTTPException(422,"Lock a question paper and marking scheme before evaluating student answers.")
        qd,qe=bytes(template["question_paper_data"]),template["question_paper_ext"]
        sd,se=bytes(template["marking_scheme_data"]),template["marking_scheme_ext"]
        if not assessment_title.strip():
            assessment_title=template["assessment_title"]
    if not assessment_title.strip(): assessment_title="Untitled assessment"
    qt=extract(qd,qe); st=extract(sd,se); at=extract(ad,ae,handwriting=True)
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
    report["answer_recognition"]="TrOCR small handwritten, line-by-line"
    report["answer_recognition_mode"]="handwriting_htr"
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
