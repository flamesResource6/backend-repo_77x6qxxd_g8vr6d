import os
from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from database import db, create_document
from schemas import Client, Case, Appointment, Document, MessageTemplate, Payment, AuditLog

# Simple RBAC using header X-Role. In production, replace with proper auth (JWT + users).
class Role(BaseModel):
    role: str


def get_role(x_role: Optional[str] = Header(default=None)):
    # Reads X-Role header; defaults to 'client' if absent
    return x_role or "client"


app = FastAPI(title="Notary Management API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {"message": "Notary Management Backend Ready"}


@app.get("/test")
def test_database():
    resp = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "collections": [],
    }
    try:
        if db is not None:
            resp["database"] = "✅ Connected"
            resp["collections"] = db.list_collection_names()[:10]
    except Exception as e:
        resp["database"] = f"⚠️ {str(e)[:80]}"
    return resp


# Schema discovery endpoint for no-code data viewer
@app.get("/schema")
def get_schema():
    return {
        "client": Client.model_json_schema(),
        "case": Case.model_json_schema(),
        "appointment": Appointment.model_json_schema(),
        "document": Document.model_json_schema(),
        "messageTemplate": MessageTemplate.model_json_schema(),
        "payment": Payment.model_json_schema(),
        "auditlog": AuditLog.model_json_schema(),
    }


# ---- Helper functions ----

def oid(id_str: str):
    try:
        from bson.objectid import ObjectId  # use PyMongo's bundled bson
        return ObjectId(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id or bson not available")


def require_role(allowed: List[str]):
    def checker(role: str = Depends(get_role)):
        if role not in allowed:
            raise HTTPException(status_code=403, detail="Forbidden for role")
        return role
    return checker


# ---- Core CRUD endpoints ----

@app.post("/clients", dependencies=[Depends(require_role(["notary", "assistant"]))])
def create_client(payload: Client):
    new_id = create_document("client", payload)
    create_document("auditlog", AuditLog(actor_role="assistant", action="create", entity="client", entity_id=new_id))
    return {"_id": new_id}


@app.get("/clients")
def list_clients(q: Optional[str] = None, limit: int = 50, role: str = Depends(require_role(["notary", "assistant"]))):
    filt = {}
    if q:
        filt = {"$or": [
            {"first_name": {"$regex": q, "$options": "i"}},
            {"last_name": {"$regex": q, "$options": "i"}},
            {"email": {"$regex": q, "$options": "i"}},
        ]}
    items = list(db["client"].find(filt).limit(limit))
    for it in items:
        it["_id"] = str(it["_id"])
    return items


@app.post("/cases", dependencies=[Depends(require_role(["notary", "assistant"]))])
def create_case(payload: Case):
    # Ensure client exists
    if payload.client_id:
        if not db["client"].find_one({"_id": oid(payload.client_id)}):
            raise HTTPException(status_code=404, detail="Client not found")
    new_id = create_document("case", payload)
    create_document("auditlog", AuditLog(actor_role="assistant", action="create", entity="case", entity_id=new_id))
    return {"_id": new_id}


@app.get("/cases")
def list_cases(status: Optional[str] = None, limit: int = 100, role: str = Depends(require_role(["notary", "assistant"]))):
    filt = {"status": status} if status else {}
    items = list(db["case"].find(filt).limit(limit))
    for it in items:
        it["_id"] = str(it["_id"])
    return items


@app.patch("/cases/{case_id}/status", dependencies=[Depends(require_role(["notary", "assistant"]))])
def update_case_status(case_id: str, status: str):
    allowed = ["New", "Draft", "Waiting Signature", "Completed", "Archived"]
    if status not in allowed:
        raise HTTPException(status_code=400, detail="Invalid status")
    res = db["case"].update_one({"_id": oid(case_id)}, {"$set": {"status": status, "updated_at": datetime.utcnow()}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Case not found")
    create_document("auditlog", AuditLog(actor_role="assistant", action="update_status", entity="case", entity_id=case_id, details={"status": status}))
    return {"ok": True}


@app.post("/appointments/public")
def public_create_appointment(payload: Appointment):
    # Public endpoint for booking widget (client role)
    if payload.end_time <= payload.start_time:
        raise HTTPException(status_code=400, detail="Invalid time range")
    # Basic conflict check (overlap if start < existing_end AND end > existing_start)
    conflict = db["appointment"].find_one({
        "start_time": {"$lt": payload.end_time},
        "end_time": {"$gt": payload.start_time}
    })
    if conflict:
        raise HTTPException(status_code=409, detail="Time slot not available")
    new_id = create_document("appointment", payload)
    create_document("auditlog", AuditLog(actor_role="client", action="book", entity="appointment", entity_id=new_id))
    return {"_id": new_id}


@app.get("/appointments")
def list_appointments(day: Optional[str] = None, role: str = Depends(require_role(["notary", "assistant"]))):
    filt = {}
    if day:
        # day format YYYY-MM-DD
        try:
            d = datetime.strptime(day, "%Y-%m-%d")
            start = datetime(d.year, d.month, d.day)
            end = start + timedelta(days=1)
            filt = {"start_time": {"$gte": start, "$lt": end}}
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid day format")
    items = list(db["appointment"].find(filt).sort("start_time", 1))
    for it in items:
        it["_id"] = str(it["_id"])
    return items


@app.post("/documents", dependencies=[Depends(require_role(["notary", "assistant"]))])
def create_document_record(payload: Document):
    new_id = create_document("document", payload)
    create_document("auditlog", AuditLog(actor_role="assistant", action="create", entity="document", entity_id=new_id))
    return {"_id": new_id}


# Simple template repository
DEFAULT_TEMPLATES = {
    "power_of_attorney": {
        "name": "Power of Attorney",
        "content": """
        POWER OF ATTORNEY\n\n        Principal: {{client_first_name}} {{client_last_name}}\n        Address: {{client_address}}\n        Date: {{date}}\n        Case: {{case_title}}\n\n        I hereby appoint ... (sample content)
        """.strip(),
    },
    "affidavit": {
        "name": "Affidavit",
        "content": """
        AFFIDAVIT\n\n        Affiant: {{client_first_name}} {{client_last_name}}\n        Statement: ... (sample content)\n        Date: {{date}}
        """.strip(),
    },
}


@app.get("/templates")
def list_templates():
    return [{"key": k, **v} for k, v in DEFAULT_TEMPLATES.items()]


class RenderRequest(BaseModel):
    template_key: str
    case_id: str


@app.post("/templates/render", dependencies=[Depends(require_role(["notary", "assistant"]))])
def render_template(req: RenderRequest):
    tpl = DEFAULT_TEMPLATES.get(req.template_key)
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")
    case = db["case"].find_one({"_id": oid(req.case_id)})
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    client = db["client"].find_one({"_id": oid(case["client_id"])}) if case.get("client_id") else None

    def subst(text: str, vars: dict):
        for k, v in vars.items():
            text = text.replace("{{" + k + "}}", str(v or ""))
        return text

    vars = {
        "client_first_name": client.get("first_name") if client else "",
        "client_last_name": client.get("last_name") if client else "",
        "client_address": client.get("address") if client else "",
        "case_title": case.get("title", ""),
        "date": datetime.utcnow().strftime("%Y-%m-%d"),
    }
    content = subst(tpl["content"], vars)
    doc_id = create_document("document", Document(case_id=req.case_id, name=tpl["name"], template_key=req.template_key, content=content))
    return {"document_id": doc_id, "content": content}


# Payments via Stripe Checkout (test-mode; expects STRIPE_SECRET_KEY)
try:
    import stripe  # type: ignore
    STRIPE_SECRET = os.getenv("STRIPE_SECRET_KEY")
    if STRIPE_SECRET:
        stripe.api_key = STRIPE_SECRET
except Exception:
    stripe = None
    STRIPE_SECRET = None


class CheckoutRequest(BaseModel):
    service: str
    amount_cents: int
    case_id: Optional[str] = None
    client_id: Optional[str] = None
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


@app.post("/payments/checkout")
def create_checkout_session(req: CheckoutRequest):
    if not stripe or not STRIPE_SECRET:
        raise HTTPException(status_code=503, detail="Stripe not configured")
    success_url = req.success_url or "https://example.com/success"
    cancel_url = req.cancel_url or "https://example.com/cancel"
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {"name": req.service},
                    "unit_amount": req.amount_cents,
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"case_id": req.case_id or "", "client_id": req.client_id or ""},
        )
        pay_id = create_document("payment", Payment(service=req.service, amount_cents=req.amount_cents, case_id=req.case_id, client_id=req.client_id, status="pending", stripe_session_id=session.id))
        return {"checkout_url": session.url, "payment_id": pay_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Simple dashboard aggregates
@app.get("/dashboard")
def dashboard(role: str = Depends(require_role(["notary", "assistant"]))):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    # call list_appointments directly for today; avoid recursion issues by inline query
    d = datetime.strptime(today, "%Y-%m-%d")
    start = datetime(d.year, d.month, d.day)
    end = start + timedelta(days=1)
    appts_today = db["appointment"].count_documents({"start_time": {"$gte": start, "$lt": end}})
    open_cases = db["case"].count_documents({"status": {"$in": ["New", "Draft", "Waiting Signature"]}})
    completed_month = db["case"].count_documents({"status": "Completed"})
    recent_activity = list(db["auditlog"].find({}).sort("created_at", -1).limit(10))
    for a in recent_activity:
        a["_id"] = str(a["_id"])
    return {
        "kpis": {
            "appointments_today": int(appts_today),
            "open_cases": int(open_cases),
            "completed_cases": int(completed_month),
        },
        "recent_activity": recent_activity,
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
