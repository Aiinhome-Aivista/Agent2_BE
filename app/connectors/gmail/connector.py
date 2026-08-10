"""GmailConnector — fetches emails via IMAP."""
from __future__ import annotations

import email as email_lib
import imaplib
import re
import time
from email.header import decode_header
from typing import Any, Dict, List, Optional

from app.connectors.base import (
    BaseConnector,
    ConnectorConfigError,
    HttpClient,
    IncidentPayload,
    load_credentials,
)
from app.connectors.gmail import META
from app.connectors.gmail.sync import upsert_from_email
from app.core.logger import logger


class GmailConnector(BaseConnector):
    meta = META

    def http(self) -> HttpClient:
        """Not used for IMAP, but must be implemented for BaseConnector."""
        return HttpClient(base_url="https://dummy.email.com")

    def verify_webhook(self, raw_body: bytes, headers: Dict[str, str], url_token: Optional[str]) -> bool:
        return False

    def parse_webhook_event(self, body: Dict[str, Any]) -> Optional[IncidentPayload]:
        return None

    def _get_imap_connection(self) -> imaplib.IMAP4_SSL:
        try:
            creds = load_credentials(self.connector_id)
        except Exception:
            creds = {}
            
        email_addr = creds.get("email") or (self.config or {}).get("email")
        app_password = creds.get("app_password") or (self.config or {}).get("app_password")
        imap_server = creds.get("imap_server") or (self.config or {}).get("imap_server") or "imap.gmail.com"

        if not email_addr or not app_password:
            raise ConnectorConfigError("Email or App Password missing.")

        mail = imaplib.IMAP4_SSL(imap_server)
        mail.login(email_addr, app_password)
        return mail

    def health_check(self) -> Dict[str, Any]:
        start = time.perf_counter()
        try:
            mail = self._get_imap_connection()
            mail.logout()
            latency_ms = int((time.perf_counter() - start) * 1000)
            return {
                "ok": True,
                "latency_ms": latency_ms,
                "info": {"status": "Successfully connected to IMAP server."}
            }
        except Exception as e:
            return {"ok": False, "error": str(e)[:300]}

    def _decode_str(self, header_str: str) -> str:
        if not header_str:
            return ""
        decoded_fragments = decode_header(header_str)
        result = ""
        for frag, encoding in decoded_fragments:
            if isinstance(frag, bytes):
                result += frag.decode(encoding or "utf-8", errors="ignore")
            else:
                result += frag
        return result

    def poll_for_changes(self, since: Optional[str] = None) -> List[IncidentPayload]:
        """Poll for UNSEEN emails, parse them and return IncidentPayloads."""
        out: List[IncidentPayload] = []
        try:
            mail = self._get_imap_connection()
            mail.select("inbox")

            status, messages = mail.search(None, "UNSEEN")
            if status != "OK":
                return []

            email_ids = messages[0].split()
            for e_id in email_ids:
                res, msg_data = mail.fetch(e_id, "(RFC822)")
                if res != "OK":
                    continue
                
                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email_lib.message_from_bytes(response_part[1])
                        subject = self._decode_str(msg.get("Subject", ""))
                        from_header = self._decode_str(msg.get("From", ""))
                        message_id = msg.get("Message-ID", e_id.decode())

                        # Extract email address and name from 'From' header
                        from_email = ""
                        from_name = from_header
                        match = re.search(r'<(.+?)>', from_header)
                        if match:
                            from_email = match.group(1)
                            from_name = from_header.replace(f"<{from_email}>", "").strip().strip('"')
                        elif "@" in from_header:
                            from_email = from_header
                        
                        # Get body
                        body = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                content_type = part.get_content_type()
                                content_disposition = str(part.get("Content-Disposition"))
                                if content_type == "text/plain" and "attachment" not in content_disposition:
                                    try:
                                        body = part.get_payload(decode=True).decode()
                                        break
                                    except Exception:
                                        pass
                        else:
                            try:
                                body = msg.get_payload(decode=True).decode()
                            except Exception:
                                pass

                        email_data = {
                            "message_id": message_id,
                            "subject": subject,
                            "body": body,
                            "from_email": from_email,
                            "from_name": from_name
                        }
                        
                        out.append(IncidentPayload(
                            external_id=message_id,
                            external_key=message_id[:50],
                            subject=subject[:200],
                            description=body,
                            status="new",
                            priority="P3",
                            severity="medium",
                            category="Email Request",
                            caller=from_name,
                            caller_email=from_email,
                            tags=["email"],
                            raw=email_data,
                        ))

                        # Mark as Seen explicitly (though fetching RFC822 often does this automatically)
                        mail.store(e_id, '+FLAGS', '\\Seen')

            mail.logout()
        except Exception as e:
            logger.exception(f"[gmail] poll failed: {e}")

        return out

    def sync_inbound(self, payload: IncidentPayload) -> str:
        """Apply an inbound payload — returns local incident_id."""
        return upsert_from_email(self.connector_id, payload.raw or {})
