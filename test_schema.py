import sys
sys.path.append('d:\\aiInHomes_workplace\\Agent_2\\backend')
from app.db.database import get_db

with get_db() as conn:
    with conn.cursor(dictionary=True) as cur:
        try:
            cur.execute('ALTER TABLE incidents ADD COLUMN proposed_to VARCHAR(255) DEFAULT NULL;')
            print("Added proposed_to column")
        except Exception as e:
            print(f"Error (might exist already): {e}")
        
        # We need to migrate existing pending ones
        try:
            cur.execute('UPDATE incidents SET proposed_to = assigned_to, assigned_to = NULL WHERE assignment_status = "pending_approval";')
            conn.commit()
            print("Migrated existing pending tickets")
        except Exception as e:
            print(f"Error migrating: {e}")
