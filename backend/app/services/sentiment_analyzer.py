"""
Sentiment Analyzer - Analyze email tone for industry optimization.
Helps analysts refine email format/style for specific industries or parameters.
"""
from typing import Optional

from app.services.llm import complete_json, rank_model_id


def analyze_email_sentiment(
    subject: str,
    body: str,
    industry: Optional[str] = None,
    target_role: Optional[str] = None,
    model: Optional[str] = None,
) -> dict:
    """
    Analyze sentiment and fit of an email for a given industry/audience.
    Returns: sentiment_score (-1 to 1), sentiment_label, industry_fit, suggested_improvements
    """
    industry_ctx = f"Target industry: {industry}" if industry else "No specific industry"
    role_ctx = f"Target role: {target_role}" if target_role else "General professional"

    prompt = f"""You are an expert at analyzing B2B cold outreach emails for effectiveness and tone.

EMAIL TO ANALYZE:
Subject: {subject}

Body:
{body}

CONTEXT:
- {industry_ctx}
- {role_ctx}

Analyze this email and respond with ONLY valid JSON (no markdown, no explanation):
{{
  "sentiment_score": <float from -1.0 to 1.0, where -1=very negative/cold, 0=neutral, 1=very positive/warm>,
  "sentiment_label": "<one word: cold|neutral|warm|enthusiastic|pushy|formal|casual>",
  "industry_fit": "<1-2 sentences on how well this tone fits the target industry/role>",
  "suggested_improvements": "<2-4 bullet points on how to optimize for better reception>"
}}"""

    data = complete_json(prompt, model_id=model or rank_model_id())
    if data:
        return {
            "sentiment_score": float(data.get("sentiment_score", 0)),
            "sentiment_label": data.get("sentiment_label", "neutral"),
            "industry_fit": data.get("industry_fit", ""),
            "suggested_improvements": data.get("suggested_improvements", ""),
        }
    return {
        "sentiment_score": 0,
        "sentiment_label": "neutral",
        "industry_fit": "Analysis unavailable.",
        "suggested_improvements": "",
    }
