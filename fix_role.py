from google.cloud import firestore
import os

project_id = "autonomous-agent-479317"
db = firestore.Client(project=project_id)
user_email = "admin@renierperez.altostrat.com"

doc_ref = db.collection("allowed_users").document(user_email)
doc = doc_ref.get()

if doc.exists:
    data = doc.to_dict()
    current_rol = data.get('rol')
    current_role = data.get('role')
    
    print(f"Current data: {data}")
    
    if current_rol == 'admin' and current_role != 'admin':
        print(f"Fixing typo for {user_email}: 'rol' -> 'role'")
        doc_ref.update({
            'role': 'admin'
        })
        print("Updated successfully.")
    else:
        print("Role appears correct or 'rol' not set to admin.")
else:
    print("User not found.")
