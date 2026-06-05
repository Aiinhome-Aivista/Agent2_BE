import sys
sys.path.append('d:\\aiInHomes_workplace\\Agent_2\\backend')
from app.db.database import get_db

with get_db() as conn:
    with conn.cursor(dictionary=True) as cur:
        # Get Sara's tickets
        cur.execute("SELECT id FROM incidents WHERE proposed_to = 'USR-ENG-001' AND assignment_status = 'pending_approval' ORDER BY created_at DESC LIMIT 2")
        rows = cur.fetchall()
        for r in rows:
            cur.execute("UPDATE incidents SET proposed_to = 'USR-ENG-002' WHERE id = %s", (r['id'],))
        conn.commit()
        print(f"Reassigned {len(rows)} tickets from Sara to Mike")
