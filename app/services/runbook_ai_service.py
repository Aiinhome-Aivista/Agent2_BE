import json
import re

from app.core.mistral_client import get_mistral_client


def extract_steps(text: str):
    steps = []

    lines = [line.strip() for line in text.splitlines() if line.strip()]

    i = 0

    while i < len(lines):
        line = lines[i]

        # Case 1: "1" only in line
        if line.isdigit():

            order = int(line)

            title = ""

            if i + 1 < len(lines):
                title = lines[i + 1].strip()

            if title:
                steps.append({
                    "order": order,
                    "title": title,
                    "command": None,
                    "expectedOutput": None,
                    "rollback": None
                })

            i += 2
            continue

        # Case 2: "1. step"
        match = re.match(r"^(\d+)[\.\)]\s+(.*)", line)

        if match:
            steps.append({
                "order": int(match.group(1)),
                "title": match.group(2).strip(),
                "command": None,
                "expectedOutput": None,
                "rollback": None
            })

        i += 1

    return steps

def generate_runbook_summary(text: str):

    client = get_mistral_client()

    if not client:
        raise Exception("Mistral client not configured")

    system_prompt = """
You are an IT Operations Runbook Analyzer.

Analyze the uploaded runbook/manual.

Extract:
1. Runbook Name
2. Category
3. Short Summary
4. Detailed Description

Return STRICT JSON ONLY.

Format:

{
  "name": "",
  "category": "",
  "summary": "",
  "description": ""
}
"""

    user_prompt = f"""
RUNBOOK CONTENT:

{text[:12000]}
"""

    response = client.chat(
        [
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": user_prompt
            }
        ],
        temperature=0.1,
        max_tokens=1000,
        response_format={"type": "json_object"},
    )

    content = (
        ((response.get("choices") or [{}])[0]
         .get("message") or {})
         .get("content", "")
    )

    try:
        parsed = json.loads(content)

    except Exception:

        parsed = {
            "name": "Unknown Runbook",
            "category": "General",
            "summary": text[:300],
            "description": text[:1500]
        }

    steps = extract_steps(text)

    return {
        "name": parsed.get("name"),
        "category": parsed.get("category"),
        "summary": parsed.get("summary"),
        "description": parsed.get("description"),
        "steps": steps
    }