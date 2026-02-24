#!/usr/bin/env python3
"""
Create a single test user for testing the auth system.
Run this script once to create your admin account, then delete it.

Usage:
    python create_test_user.py
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from auth.db import init_db, create_user, set_user_password, get_user_by_email

def main():
    # Initialize the database
    init_db()

    # Test user details - CHANGE THESE
    email = "jake@pulsemarketing.co"
    username = "Jake Shumaker"
    password = "testpass123"  # Change this to your desired test password
    role = "admin"

    # Check if user already exists
    existing = get_user_by_email(email)
    if existing:
        print(f"User {email} already exists!")
        print(f"  Role: {existing['role']}")
        print(f"  Has password: {bool(existing.get('password_hash'))}")
        print(f"  2FA enabled: {bool(existing.get('totp_enabled'))}")

        # Optionally reset password
        response = input("\nReset password? (y/n): ")
        if response.lower() == 'y':
            set_user_password(existing['id'], password)
            print(f"Password reset to: {password}")
        return

    # Create the user with password directly (skip invite flow)
    user = create_user(
        email=email,
        username=username,
        role=role,
        password=password,  # Set password directly
    )

    if user:
        print(f"\nTest user created successfully!")
        print(f"  Email: {email}")
        print(f"  Password: {password}")
        print(f"  Role: {role}")
        print(f"\nYou can now log in at /login")
        print("After login, you'll be prompted to set up 2FA.")
    else:
        print("Failed to create user. Check logs for errors.")

if __name__ == "__main__":
    main()
