import sys
sys.path.append('d:\\aiInHomes_workplace\\Agent_2\\backend')
from app.db.database import get_db

with get_db() as conn:
    with conn.cursor(dictionary=True) as cur:
        cur.execute('SELECT u.id, u.full_name, u.role, (SELECT COUNT(*) FROM incidents i WHERE i.assigned_to = u.id) as ticket_count FROM users u WHERE u.is_active = 1')
        print(cur.fetchall())
