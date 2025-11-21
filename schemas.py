"""
Database Schemas for Notary Management App

Each Pydantic model represents a collection in MongoDB. The collection name is the
lowercase of the class name (e.g., Client -> "client").
"""

from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List, Literal
from datetime import datetime


# Core domain models
class Client(BaseModel):
    first_name: str = Field(..., description="Client first name")
    last_name: str = Field(..., description="Client last name")
    email: EmailStr = Field(..., description="Primary email")
    phone: Optional[str] = Field(None, description="Phone number")
    address: Optional[str] = Field(None, description="Street address")
    city: Optional[str] = Field(None)
    country: Optional[str] = Field(None)
    notes: Optional[str] = Field(None, description="Internal notes")


CaseStatus = Literal[
    "New",
    "Draft",
    "Waiting Signature",
    "Completed",
    "Archived",
]


class Case(BaseModel):
    client_id: str = Field(..., description="Reference to Client _id as string")
    title: str = Field(..., description="Case title e.g. Power of Attorney for John Doe")
    type: str = Field(..., description="Case type e.g. Power of Attorney, Affidavit")
    status: CaseStatus = Field("New")
    description: Optional[str] = None
    assigned_to: Optional[str] = Field(None, description="User id or name of notary/assistant")
    due_date: Optional[datetime] = None


class Appointment(BaseModel):
    client_id: Optional[str] = Field(None, description="Reference to Client _id as string if known")
    service: str = Field(..., description="Service type selected during booking")
    start_time: datetime = Field(...)
    end_time: datetime = Field(...)
    location: Optional[str] = Field(None)
    notes: Optional[str] = Field(None)
    status: Literal["Scheduled", "Completed", "Cancelled"] = Field("Scheduled")
    case_id: Optional[str] = Field(None, description="Related case id if any")


class Document(BaseModel):
    case_id: str = Field(...)
    name: str = Field(..., description="Document name")
    template_key: Optional[str] = Field(None, description="Reference to a template type")
    content: Optional[str] = Field(None, description="Rendered document text")
    file_url: Optional[str] = Field(None, description="Stored file URL if uploaded")
    ocr_text: Optional[str] = Field(None, description="OCR extracted text if available")
    tags: Optional[List[str]] = Field(default_factory=list)


class MessageTemplate(BaseModel):
    key: str = Field(..., description="Template identifier")
    channel: Literal["email", "sms"]
    subject: Optional[str] = None
    body: str = Field(...)


class Payment(BaseModel):
    client_id: Optional[str] = None
    case_id: Optional[str] = None
    service: str = Field(...)
    amount_cents: int = Field(..., ge=50)
    currency: str = Field("usd")
    status: Literal["pending", "paid", "failed"] = Field("pending")
    stripe_session_id: Optional[str] = None


class AuditLog(BaseModel):
    actor_role: Literal["notary", "assistant", "client", "system"]
    actor_id: Optional[str] = None
    action: str = Field(..., description="What happened e.g. created_case, updated_status")
    entity: str = Field(..., description="client/case/document/appointment/payment")
    entity_id: Optional[str] = None
    details: Optional[dict] = None
"""
# The Flames database viewer can read these via GET /schema endpoint.
# Collections will be created based on these models.
"""
