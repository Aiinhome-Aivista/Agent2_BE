import sys
import argparse

sys.path.append('.')
from app.db.database import get_db

def add_user(user_id, email):
    with get_db() as conn:
        with conn.cursor() as cur:
            # Check if user exists in `users`
            cur.execute("SELECT id FROM users WHERE id = %s", (user_id,))
            if not cur.fetchone():
                print(f"Error: User {user_id} does not exist in the 'users' table.")
                return
            
            # Insert into team_config
            cur.execute(
                "INSERT INTO team_config (user_id, user_email) VALUES (%s, %s) ON DUPLICATE KEY UPDATE user_email = %s",
                (user_id, email, email)
            )
            conn.commit()
            print(f"Successfully added/updated {user_id} ({email}) in the team_config table.")

def list_users():
    with get_db() as conn:
        with conn.cursor(dictionary=True) as cur:
            cur.execute("SELECT * FROM team_config")
            rows = cur.fetchall()
            print("Current Team Config Users:")
            for row in rows:
                print(f"- {row['user_id']} ({row['user_email']})")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage team_config table")
    parser.add_argument("--add", nargs=2, metavar=('USER_ID', 'EMAIL'), help="Add a user to team config")
    parser.add_argument("--list", action='store_true', help="List all users in team config")
    
    args = parser.parse_args()
    
    if args.add:
        add_user(args.add[0], args.add[1])
    elif args.list:
        list_users()
    else:
        parser.print_help()
