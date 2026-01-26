from google.cloud import firestore
import os

# Use the project ID from context
project_id = "autonomous-agent-479317"
try:
    db = firestore.Client(project=project_id)
    user_email = "admin@renierperez.altostrat.com"
    
    print(f"Checking role for: {user_email}")
    print(f"Project: {project_id}")
    
    doc_ref = db.collection("allowed_users").document(user_email)
    doc = doc_ref.get()

    if doc.exists:
        data = doc.to_dict()
        print(f"User Document Exists.")
        print(f"Role: {data.get('role', 'NOT_SET')}")
        print(f"Full Data: {data}")
    else:
        print("User NOT FOUND in 'allowed_users' collection.")
        # List all users to help debug
        print("\nExisting users:")
        docs = db.collection("allowed_users").stream()
        for d in docs:
            print(f"- {d.id}: {d.to_dict()}")

except Exception as e:
    print(f"Error accessing Firestore: {e}")
