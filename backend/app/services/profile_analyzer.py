"""
Profile Analyzer - Analyze contacts for value proposition, role, online sentiment.
Helps understand what each person adds, what they do, and what messaging they'd be receptive to.
"""
from typing import Optional

from app.services.llm import complete_json, rank_model_id


def analyze_contact_profile(
    name: str,
    title: str,
    company: str,
    linkedin_url: Optional[str] = None,
    department: Optional[str] = None,
    model: Optional[str] = None,
) -> dict:
    """
    Analyze a contact's profile to infer:
    - value_proposition: what value they add to the company
    - role_summary: what they do day-to-day
    - online_sentiment: inferred tone/persona from their role/industry
    - receptiveness_notes: what messaging they'd be more receptive to
    """
    ctx = f"""
Contact: {name or 'Unknown'}
Title: {title or 'Unknown'}
Company: {company or 'Unknown'}
Department: {department or 'Not specified'}
LinkedIn: {linkedin_url or 'Not provided'}
"""

    prompt = f"""You are an expert at analyzing B2B contacts for outreach strategy.

CONTACT INFO:
{ctx}

Based on their title, company, and role, infer (you may not have full data - make reasonable inferences):

Respond with ONLY valid JSON (no markdown, no explanation):
{{
  "value_proposition": "<1-2 sentences: what value does this person add to their company? What are they responsible for?>",
  "role_summary": "<1-2 sentences: what do they likely do day-to-day? Key responsibilities?>",
  "online_sentiment": "<1-2 sentences: what tone/persona might they have based on their role and industry? (e.g. data-driven, relationship-focused, time-constrained)>",
  "receptiveness_notes": "<2-3 bullet points: what messaging angles would they likely be more receptive to? What to avoid?>",
  "industry": "<inferred industry/sector if possible>"
}}"""

    data = complete_json(prompt, model_id=model or rank_model_id())
    if data:
        return {
            "value_proposition": data.get("value_proposition", ""),
            "role_summary": data.get("role_summary", ""),
            "online_sentiment": data.get("online_sentiment", ""),
            "receptiveness_notes": data.get("receptiveness_notes", ""),
            "industry": data.get("industry", ""),
        }
    return {
        "value_proposition": "",
        "role_summary": "",
        "online_sentiment": "",
        "receptiveness_notes": "",
        "industry": "",
    }
