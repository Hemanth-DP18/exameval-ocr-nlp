from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from pypdf import PdfReader
from PIL import Image
import io, os, re

app=FastAPI(title="ExamEval API",version="0.1.0")
app.add_middleware(CORSMiddleware,allow_origins=os.getenv("CORS_ORIGINS","*").split(","),allow_credentials=True,allow_methods=["*"],allow_headers=["*"])
ALLOWED={".pdf",".png",".jpg",".jpeg",".txt"}
MAX_SIZE=15*1024*1024

async def read_file(f):
    ext=Path(f.filename or "").suffix.lower()
    if ext not in ALLOWED: raise HTTPException(400,"Use PDF, PNG, JPG or TXT files.")
    data=await f.read()
    if not data: raise HTTPException(400,"The uploaded file is empty.")
    if len(data)>MAX_SIZE: raise HTTPException(413,"Each file must be 15 MB or smaller.")
    return data,ext

def ocr(img):
    try:
        import pytesseract
        return pytesseract.image_to_string(img)
    except Exception as e: raise HTTPException(503,f"OCR unavailable: {e}")

def extract(data,ext):
    if ext==".txt": return data.decode("utf-8",errors="replace")
    if ext==".pdf":
        try:
            reader=PdfReader(io.BytesIO(data))
            text="\n".join(p.extract_text() or "" for p in reader.pages)
            if text.strip(): return text
            from pdf2image import convert_from_bytes
            return "\n".join(ocr(im) for im in convert_from_bytes(data,first_page=1,last_page=12))
        except HTTPException: raise
        except Exception as e: raise HTTPException(422,f"PDF extraction failed. For scanned PDFs, OCR needs Poppler. {e}")
    try: return ocr(Image.open(io.BytesIO(data)))
    except HTTPException: raise
    except Exception as e: raise HTTPException(422,f"Image processing failed: {e}")

def words(s): return {w for w in re.findall(r"[a-z0-9]+",s.lower()) if len(w)>1}
def score(a,e):
    aa,ee=words(a),words(e)
    if not aa or not ee:return 0.0
    p=len(aa&ee)/len(aa); r=len(aa&ee)/len(ee)
    return 2*p*r/(p+r) if p+r else 0.0

def rubric(text):
    out=[]
    for line in text.splitlines():
        m=re.match(r"\s*(?:q(?:uestion)?\s*)?(\d+)\s*[:.)-]\s*(.*?)\s*\|\s*marks?\s*[:=]\s*(\d+(?:\.\d+)?)\s*$",line,re.I)
        if m: out.append({"criterion":f"Q{m.group(1)}","expected":m.group(2),"max_marks":float(m.group(3))})
    if not out:
        paras=[x.strip() for x in re.split(r"\n\s*\n",text) if x.strip()]
        out=[{"criterion":f"Criterion {i+1}","expected":p,"max_marks":1.0} for i,p in enumerate(paras)]
    return out

@app.get("/")
def root(): return {"service":"ExamEval API","docs":"/docs"}
@app.get("/health")
def health(): return {"status":"healthy"}

@app.post("/api/evaluate")
async def evaluate(question_paper:UploadFile=File(...),marking_scheme:UploadFile=File(...),answer_sheet:UploadFile=File(...),student_name:str="Student"):
    qd,qe=await read_file(question_paper); sd,se=await read_file(marking_scheme); ad,ae=await read_file(answer_sheet)
    qt=extract(qd,qe); st=extract(sd,se); at=extract(ad,ae)
    criteria=rubric(st)
    if not criteria: raise HTTPException(422,"No marking criteria found. Use one line per criterion, e.g. Q1: expected answer | marks: 5")
    rows=[]; total=maximum=0.0
    for c in criteria:
        sim=score(at,c["expected"]); awarded=round(sim*c["max_marks"],2)
        total+=awarded; maximum+=c["max_marks"]
        rows.append({**c,"similarity":round(sim,3),"awarded_marks":awarded,"review_required":True})
    return {"student_name":student_name.strip() or "Student","total_marks":round(total,2),"maximum_marks":round(maximum,2),"percentage":round(100*total/maximum,2) if maximum else 0,"question_paper_text":qt[:12000],"marking_scheme_text":st[:12000],"answer_text":at[:20000],"results":rows,"notice":"Prototype estimate only. OCR and word-overlap scoring may miss equivalent wording, handwriting, numerical working and diagrams. A teacher must review and confirm every mark."}
