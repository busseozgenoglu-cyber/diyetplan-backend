from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, PlainTextResponse
from pydantic import BaseModel
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime, timedelta
import jwt, os, csv, io, hashlib, hmac, json, base64
from typing import Optional

app = FastAPI(title="DiyetPlan API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "diyetplan")
client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
db = client[DB_NAME]

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "DiyetAdmin2024!")
JWT_SECRET = os.getenv("JWT_SECRET", "supersecretkey")
PAYTR_MERCHANT_ID = os.getenv("PAYTR_MERCHANT_ID", "")
PAYTR_MERCHANT_KEY = os.getenv("PAYTR_MERCHANT_KEY", "")
PAYTR_MERCHANT_SALT = os.getenv("PAYTR_MERCHANT_SALT", "")
PRICE = int(os.getenv("PRICE", "29900"))

def verify_token(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token gerekli")
    token = authorization.split(" ")[1]
    try:
        jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except:
        raise HTTPException(status_code=401, detail="Geçersiz token")

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

class PayTRRequest(BaseModel):
    submission_id: str
    user_ip: str = "1.2.3.4"

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

@app.post("/api/payments/paytr-token")
def get_paytr_token(data: PayTRRequest):
    import requests as req
    if not PAYTR_MERCHANT_ID:
        raise HTTPException(status_code=400, detail="PayTR bilgileri eksik")
    try:
        oid = ObjectId(data.submission_id)
    except:
        raise HTTPException(status_code=400, detail="Gecersiz submission_id")
    sub = db.submissions.find_one({"_id": oid})
    if not sub:
        raise HTTPException(status_code=404, detail="Kayit bulunamadi")

    import time
    merchant_oid = "MP" + str(int(time.time()))
    email = sub.get("email", "test@test.com")
    user_name = sub.get("full_name", "Musteri")
    phone = sub.get("phone", "05000000000")
    user_ip = "88.255.0.1"
    payment_amount = PRICE
    currency = "TL"
    no_installment = 0
    max_installment = 0
    test_mode = 1
    merchant_ok_url = "https://fitnova.ink/basari"
    merchant_fail_url = "https://fitnova.ink/odeme"

    basket = [["Diyet Plani", str(payment_amount), 1]]
    basket_encoded = base64.b64encode(json.dumps(basket).encode()).decode()

    hash_str = "".join([
        PAYTR_MERCHANT_ID, user_ip, merchant_oid, email,
        str(payment_amount), basket_encoded,
        str(no_installment), str(max_installment),
        currency, str(test_mode), PAYTR_MERCHANT_SALT
    ])
    paytr_token = base64.b64encode(
        hmac.new(PAYTR_MERCHANT_KEY.encode("utf-8"), hash_str.encode("utf-8"), hashlib.sha256).digest()
    ).decode()

    # PayTR'ye token isteği gönder
    paytr_response = req.post("https://www.paytr.com/odeme/api/get-token", data={
        "merchant_id": PAYTR_MERCHANT_ID,
        "user_ip": user_ip,
        "merchant_oid": merchant_oid,
        "email": email,
        "payment_amount": str(payment_amount),
        "user_basket": basket_encoded,
        "no_installment": str(no_installment),
        "max_installment": str(max_installment),
        "currency": currency,
        "test_mode": str(test_mode),
        "user_name": user_name,
        "user_address": "Turkiye",
        "user_phone": phone,
        "merchant_ok_url": merchant_ok_url,
        "merchant_fail_url": merchant_fail_url,
        "paytr_token": paytr_token,
        "lang": "tr",
        "debug_on": "1",
    })

    result = paytr_response.json()
    if result.get("status") != "success":
        raise HTTPException(status_code=400, detail=f"PayTR hatasi: {result.get('reason', 'Bilinmeyen hata')}")

    iframe_token = result["token"]
    db.submissions.update_one({"_id": oid}, {"$set": {"merchant_oid": merchant_oid}})

    return {"iframe_token": iframe_token}

@app.post("/api/payments/paytr-callback")
async def paytr_callback(request: Request):
    form = await request.form()
    merchant_oid = form.get("merchant_oid", "")
    status = form.get("status", "")
    total_amount = form.get("total_amount", "")
    hash_val = form.get("hash", "")

    hash_str = merchant_oid + PAYTR_MERCHANT_SALT + status + total_amount
    expected = base64.b64encode(
        hmac.new(PAYTR_MERCHANT_KEY.encode(), hash_str.encode(), hashlib.sha256).digest()
    ).decode()

    if hash_val != expected:
        return {"status": "error"}

    if status == "success":
        db.submissions.update_one(
            {"merchant_oid": merchant_oid},
            {"$set": {"status": "paid", "paid_at": datetime.utcnow().isoformat()}}
        )
    else:
        db.submissions.update_one(
            {"merchant_oid": merchant_oid},
            {"$set": {"status": "failed"}}
        )
    return PlainTextResponse("OK")

@app.post("/api/admin/login")
def admin_login(data: AdminLogin):
    if data.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Hatali sifre")
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
        "revenue": paid * (PRICE // 100),
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
    header = ["ID", "Ad Soyad", "E-posta", "Telefon", "Durum", "Tarih"]
    for i in range(1, 61):
        header.append(f"Soru {i}")
    writer.writerow(header)
    for doc in docs:
        answers = doc.get("answers", {})
        row = [
            str(doc["_id"]),
            doc.get("full_name", ""),
            doc.get("email", ""),
            doc.get("phone", ""),
            doc.get("status", ""),
            doc.get("created_at", ""),
        ]
        for i in range(1, 61):
            row.append(answers.get(str(i), ""))
        writer.writerow(row)
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=musteriler.csv"}
    )


# PayTR token'ı backend'den al, iframe URL'i döndür

@app.get("/api/payments/paytr-callback")
def paytr_callback_get():
    return PlainTextResponse("OK")

@app.post("/api/admin/mark-sent/{submission_id}")
def mark_sent(submission_id: str, _=Depends(verify_token)):
    try:
        oid = ObjectId(submission_id)
    except:
        raise HTTPException(status_code=400, detail="Geçersiz ID")
    db.submissions.update_one({"_id": oid}, {"$set": {
        "status": "gonderildi",
        "sent_at": datetime.utcnow().isoformat(),
    }})
    return {"success": True}
