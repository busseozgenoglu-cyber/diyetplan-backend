from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime, timedelta
import jwt, os, csv, io
from typing import Optional

app = FastAPI(title="DiyetPlan API")

# CORS
origins = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# MongoDB
MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "diyetplan")
client = MongoClient(MONGO_URL)
db = client[DB_NAME]

# Config
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "DiyetAdmin2024!")
JWT_SECRET = os.getenv("JWT_SECRET", "supersecretkey")

# ── Auth ──────────────────────────────────────────────
def verify_token(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token gerekli")
    token = authorization.split(" ")[1]
    try:
        jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except:
        raise HTTPException(status_code=401, detail="Geçersiz token")

# ── Models ────────────────────────────────────────────
class SubmissionCreate(BaseModel):
    answers: dict
    full_name: str
    email: str
    phone: str = ""

class HavaleNotify(BaseModel):
    submission_id: str
    sender_name: str
    sender_phone: str
    note: str = ""

class AdminLogin(BaseModel):
    password: str

# ── Routes ───────────────────────────────────────────
@app.get("/api/")
def root():
    return {"message": "DietPlan API v1.0"}

@app.post("/api/submissions")
def create_submission(data: SubmissionCreate):
    doc = {
        "answers": data.answers,
        "full_name": data.full_name,
        "email": data.email,
        "phone": data.phone,
        "status": "pending",
        "created_at": datetime.utcnow().isoformat(),
    }
    result = db.submissions.insert_one(doc)
    return {"id": str(result.inserted_id)}

@app.post("/api/payments/havale-notify")
def havale_notify(data: HavaleNotify):
    try:
        oid = ObjectId(data.submission_id)
    except:
        raise HTTPException(status_code=400, detail="Geçersiz submission_id")
    
    sub = db.submissions.find_one({"_id": oid})
    if not sub:
        raise HTTPException(status_code=404, detail="Kayıt bulunamadı")
    
    db.submissions.update_one({"_id": oid}, {"$set": {
        "status": "havale_bekliyor",
        "havale_sender_name": data.sender_name,
        "havale_sender_phone": data.sender_phone,
        "havale_note": data.note,
        "havale_notified_at": datetime.utcnow().isoformat(),
    }})
    return {"success": True, "message": "Havale bildirimi alındı"}

@app.post("/api/admin/login")
def admin_login(data: AdminLogin):
    if data.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Hatalı şifre")
    token = jwt.encode(
        {"sub": "admin", "exp": datetime.utcnow() + timedelta(days=7)},
        JWT_SECRET, algorithm="HS256"
    )
    return {"token": token}

@app.get("/api/admin/stats")
def admin_stats(_=Depends(verify_token)):
    total = db.submissions.count_documents({})
    paid = db.submissions.count_documents({"status": "paid"})
    pending_havale = db.submissions.count_documents({"status": "havale_bekliyor"})
    pending = db.submissions.count_documents({"status": "pending"})
    return {
        "total": total,
        "paid": paid,
        "havale_bekliyor": pending_havale,
        "pending": pending,
        "revenue": paid * 299,  # fiyatı buradan değiştir
    }

@app.get("/api/admin/submissions")
def admin_submissions(
    page: int = 1,
    limit: int = 20,
    search: str = "",
    status: str = "",
    _=Depends(verify_token)
):
    query = {}
    if search:
        query["$or"] = [
            {"full_name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}},
            {"phone": {"$regex": search, "$options": "i"}},
        ]
    if status:
        query["status"] = status
    
    skip = (page - 1) * limit
    cursor = db.submissions.find(query).sort("created_at", -1).skip(skip).limit(limit)
    items = []
    for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        items.append(doc)
    
    total = db.submissions.count_documents(query)
    return {"items": items, "total": total, "page": page}

@app.post("/api/admin/approve-havale/{submission_id}")
def approve_havale(submission_id: str, _=Depends(verify_token)):
    try:
        oid = ObjectId(submission_id)
    except:
        raise HTTPException(status_code=400, detail="Geçersiz ID")
    db.submissions.update_one({"_id": oid}, {"$set": {
        "status": "paid",
        "paid_at": datetime.utcnow().isoformat(),
    }})
    return {"success": True}

@app.post("/api/admin/reject-havale/{submission_id}")
def reject_havale(submission_id: str, _=Depends(verify_token)):
    try:
        oid = ObjectId(submission_id)
    except:
        raise HTTPException(status_code=400, detail="Geçersiz ID")
    db.submissions.update_one({"_id": oid}, {"$set": {"status": "failed"}})
    return {"success": True}

@app.get("/api/admin/export")
def export_csv(_=Depends(verify_token)):
    docs = list(db.submissions.find({}).sort("created_at", -1))
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Ad Soyad", "E-posta", "Telefon", "Durum", "Tarih"])
    for doc in docs:
        writer.writerow([
            str(doc["_id"]),
            doc.get("full_name", ""),
            doc.get("email", ""),
            doc.get("phone", ""),
            doc.get("status", ""),
            doc.get("created_at", ""),
        ])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=diyetplan_musteriler.csv"}
    )
