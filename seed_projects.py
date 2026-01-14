import firebase_admin
from firebase_admin import credentials, firestore
import os

# Initialize Firebase (Auto-discovery of credentials or use default)
# For Cloud Run, default credentials work. For local, we might need GOOGLE_APPLICATION_CREDENTIALS set.
try:
    firebase_admin.get_app()
except ValueError:
    firebase_admin.initialize_app()

db = firestore.client()

PROJECT_ID = "autonomous-agent-479317"
COLLECTION = "managed_projects"

def seed_projects():
    print(f"Seeding '{COLLECTION}' in Firestore...")
    
    # Check if exists
    doc_ref = db.collection(COLLECTION).document(PROJECT_ID)
    doc = doc_ref.get()
    
    if doc.exists:
        print(f"Project '{PROJECT_ID}' already exists in managed_projects.")
    else:
        doc_ref.set({
            "project_id": PROJECT_ID,
            "name": "Primary Agent Project",
            "description": "The main project hosting the agent."
        })
        print(f"Successfully added '{PROJECT_ID}' to managed_projects.")

if __name__ == "__main__":
    # Ensure project ID for context if needed (local dev)
    if "GOOGLE_CLOUD_PROJECT" not in os.environ:
         os.environ["GOOGLE_CLOUD_PROJECT"] = PROJECT_ID
         
    seed_projects()
