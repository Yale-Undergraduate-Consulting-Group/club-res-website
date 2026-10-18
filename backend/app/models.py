"""Pydantic models for YUCG Outreach."""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class ContactCreate(BaseModel):
    name: Optional[str] = None
    email: str
    title: Optional[str] = None
    company: Optional[str] = None
    company_domain: Optional[str] = None
    linkedin_url: Optional[str] = None
    confidence: Optional[str] = "medium"
    department: Optional[str] = None


class Contact(ContactCreate):
    id: int
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ScrapeRequest(BaseModel):
    company_name: Optional[str] = None
    domain: Optional[str] = None
    max_people: Optional[int] = 80  # cap on web-discovery people
    enable_web_discovery: Optional[bool] = True  # Tavily web search for named employees


class SearchPersonRequest(BaseModel):
    """Search the web for information about a contact (name, optional company)."""
    name: str
    company: Optional[str] = None


class EmailGenerateRequest(BaseModel):
    contact_id: int
    tone: str = "professional"  # professional, conversational, bold, empathetic, authority
    length: str = "short"  # ultra-short, short, standard
    angle: str = "pain_point"  # pain_point, social_proof, case_study, compliment, advisory
    custom_instructions: Optional[str] = None
    value_proposition: Optional[str] = None
    model: Optional[str] = None


class EmailGenerateResponse(BaseModel):
    subject: str
    body: str
    contact_id: Optional[int] = None


class EmailGenerateTemplateRequest(BaseModel):
    """Generate email without a contact - use manual name, company, title."""
    name: Optional[str] = None
    company: Optional[str] = None
    title: Optional[str] = None
    email: Optional[str] = None
    tone: str = "professional"
    length: str = "short"
    angle: str = "pain_point"
    custom_instructions: Optional[str] = None
    value_proposition: Optional[str] = None
    model: Optional[str] = None


class CampaignCreate(BaseModel):
    name: str


class CampaignContactAdd(BaseModel):
    contact_ids: list[int] = Field(min_length=1, max_length=500)
    email_subjects: Optional[dict[str, str]] = None  # contact_id -> subject
    email_bodies: Optional[dict[str, str]] = None    # contact_id -> body


class Campaign(BaseModel):
    id: int
    name: str
    status: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

