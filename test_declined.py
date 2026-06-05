import sys
sys.path.append('d:\\aiInHomes_workplace\\Agent_2\\backend')
from app.db.database import get_db

with get_db() as conn:
    with conn.cursor() as cur:
        try:
            cur.execute('ALTER TABLE incidents ADD COLUMN declined_by JSON DEFAULT NULL;')
            print("Added declined_by column")
        except Exception as e:
            print(f"Error (might exist already): {e}")
