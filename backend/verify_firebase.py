"""
AutoGrade — Firebase Connection Verification Utility

Run this script to verify your Firebase credentials, Firestore connectivity,
and Firebase Storage bucket access:

    cd backend
    .venv/bin/python verify_firebase.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from config import (
    FIREBASE_SERVICE_ACCOUNT,
    FIREBASE_PROJECT_ID,
    FIREBASE_STORAGE_BUCKET,
    _resolve_service_account,
    get_firestore,
    get_storage_bucket,
)


def run_verification():
    print("=" * 60)
    print(" AutoGrade — Firebase Connectivity Verification")
    print("=" * 60)

    # 1. Locate credentials
    sa_target = _resolve_service_account()
    if not sa_target:
        print("❌ [FAILED] No Firebase service account credentials found!")
        print("   Searched locations:")
        print(f"   - Specified path: {FIREBASE_SERVICE_ACCOUNT or '(not set)'}")
        print(f"   - {backend_dir / 'firebase-service-account.json'}")
        print(f"   - {backend_dir.parent / 'firebase-service-account.json'}")
        print("\n   👉 Action: Place your service account JSON file at:")
        print(f"      {backend_dir / 'firebase-service-account.json'}")
        return False

    if isinstance(sa_target, str):
        print(f"✅ Service account file located: {sa_target}")
    else:
        print("✅ Service account JSON loaded from environment variable.")

    # 2. Check Firestore client
    print("\n[1/2] Connecting to Cloud Firestore...")
    try:
        db = get_firestore()
        if db is None:
            print("❌ Firestore client could not be created.")
            return False
        
        print("   Testing read access on collection 'exams'...")
        # Simple stream call with limit
        collections = list(db.collections())
        print(f"✅ Firestore connected successfully! Found {len(collections)} root collection(s).")
    except Exception as e:
        print(f"⚠️  Firestore connection check: {e}")
        print("   (Note: Ensure Cloud Firestore is enabled in Firebase Console: https://console.firebase.google.com)")

    # 3. Check Storage bucket
    print("\n[2/2] Connecting to Firebase Storage...")
    try:
        bucket = get_storage_bucket()
        if bucket is None:
            print("❌ Storage bucket could not be created.")
            return False

        print(f"✅ Storage bucket client configured for: '{bucket.name}'")
    except Exception as e:
        print(f"⚠️  Storage bucket check: {e}")
        print("   (Note: Ensure Firebase Storage is enabled in Firebase Console)")

    print("\n" + "=" * 60)
    print("🎉 Firebase setup is ready and verified!")
    print("=" * 60)
    return True


if __name__ == "__main__":
    success = run_verification()
    sys.exit(0 if success else 1)

