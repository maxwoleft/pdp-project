"""Створює першого супер-адміністратора в booking.admin_users.

Запуск: python create_superadmin.py --email admin@example.com --password YourPassword
"""
import argparse

from werkzeug.security import generate_password_hash

from _db import IntegrityError, get_db


def create_superadmin(email: str, password: str) -> None:
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """INSERT INTO admin_users (email, password_hash, role)
               VALUES (%s, %s, 'superadmin')""",
            (email.strip().lower(), generate_password_hash(password)),
        )
        conn.commit()
        print(f"Superadmin '{email}' created successfully.")
    except IntegrityError:
        conn.rollback()
        print(f"User '{email}' already exists. Updating password and role to superadmin...")
        cursor.execute(
            "UPDATE admin_users SET password_hash = %s, role = 'superadmin' WHERE email = %s",
            (generate_password_hash(password), email.strip().lower()),
        )
        conn.commit()
        print("Done.")
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create or update superadmin")
    parser.add_argument("--email", required=True, help="Admin email")
    parser.add_argument("--password", required=True, help="Admin password")
    args = parser.parse_args()
    create_superadmin(args.email, args.password)
