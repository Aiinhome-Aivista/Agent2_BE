import json
from typing import Any, Dict, Optional
from app.core.logger import logger
from app.core.mistral_client import get_mistral_client, is_configured

class AITroubleshootingService:
    @staticmethod
    def generate_solution_and_poc(incident: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Uses the Mistral model to generate a troubleshooting solution and a technical Proof of Concept (POC)
        based on the incident details.
        """
        if not is_configured():
            logger.warning("[AITroubleshootingService] Mistral is not configured.")
            return None

        client = get_mistral_client()
        if client is None:
            return None

        system_prompt = (
            "You are an expert L3 Support AI Engineer. "
            "Given the incident details, provide a step-by-step troubleshooting solution "
            "and a concrete technical Proof of Concept (POC) such as a script, config snippet, or code "
            "that can help the engineer quickly resolve the issue. "
            "Respond ONLY with valid JSON matching this schema:\n"
            "{\n"
            '  "solution_steps": ["step 1", "step 2"],\n'
            '  "poc_description": "Description of the POC",\n'
            '  "poc_code": "The actual script/config code snippet"\n'
            "}"
        )

        user_prompt = f"INCIDENT:\nSubject: {incident.get('subject')}\nDescription: {incident.get('description')}\nPriority: {incident.get('priority')}"

        try:
            response = client.chat(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=1500,
                response_format={"type": "json_object"},
            )
            
            content_str = ((response.get("choices") or [{}])[0].get("message") or {}).get("content") or "{}"
            result = json.loads(content_str)
            return result
        except Exception as e:
            logger.exception(f"[AITroubleshootingService] Failed to generate POC for incident {incident.get('id')}: {e}")
            return None
