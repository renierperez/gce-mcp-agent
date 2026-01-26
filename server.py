import os
import uuid
import sys
import logging
from fastapi import FastAPI, HTTPException, Depends, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import firebase_admin
from firebase_admin import auth, credentials
import time

from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.genai import types
from agents import create_agent
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from google.api_core.exceptions import GoogleAPICallError, RetryError, ServiceUnavailable

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI
app = FastAPI(title="GCE Manager Agent API")

# Allow CORS for Flutter Web (which might run on different port/domain)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global State
from fs_session import FirestoreSessionService
# Global State
agent = create_agent()
session_service = FirestoreSessionService()
known_sessions = set()

# Determine Firebase Project ID (use ENV or default to the one from Frontend config)
# The user created a new Firebase project which has a different ID than the Cloud Run project.
FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "autonomous-agent-479317")

# Initialize Firebase Admin
try:
    firebase_admin.get_app()
except ValueError:
    # We must explicitly set the projectId because the token is issued by the Firebase Project 
    # which is DIFFERENT from the Cloud Run (Host) Project.
    firebase_admin.initialize_app(options={'projectId': FIREBASE_PROJECT_ID})

security = HTTPBearer()

from firebase_admin import auth, credentials, firestore

# ... (Previous code)


# Auth Cache: {email: (timestamp, is_allowed, role)}
_auth_cache = {}
AUTH_CACHE_TTL = 300  # 5 minutes

def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)):
    token = credentials.credentials
    try:
        # Verify the token with Firebase
        decoded_token = auth.verify_id_token(token)
        email = decoded_token.get("email")
        
        # Check Cache
        now = time.time()
        if email in _auth_cache:
            ts, is_allowed, role = _auth_cache[email]
            if now - ts < AUTH_CACHE_TTL:
                if not is_allowed:
                     logger.warning(f"Unauthorized access attempt by {email} (Cached Deny)")
                     raise HTTPException(
                         status_code=403, 
                         detail="Access Denied: You do not have permission to access the GCE Manager Agent."
                     )
                # Inject role into token dict for downstream use
                decoded_token["role"] = role
                return decoded_token

        # Access Control (Firestore)
        try:
            db = firestore.client()
            user_ref = db.collection('allowed_users').document(email)
            doc = user_ref.get()
            
            if not doc.exists:
                 _auth_cache[email] = (now, False, "viewer")
                 logger.warning(f"Unauthorized access attempt by {email} (Not found in Firestore)")
                 raise HTTPException(
                     status_code=403, 
                     detail="Access Denied: You do not have permission to access the GCE Manager Agent. Please contact the administrator."
                 )
                 
            user_data = doc.to_dict()
            if not user_data.get('active', True):
                 _auth_cache[email] = (now, False, "viewer")
                 logger.warning(f"Unauthorized access attempt by {email} (User disabled)")
                 raise HTTPException(
                     status_code=403, 
                     detail="Access Denied: Your account has been temporarily disabled."
                 )
            
            # Extract Role (default to viewer)
            role = user_data.get('role', 'viewer')
            
            # Cache the success
            _auth_cache[email] = (now, True, role)
            
            decoded_token["role"] = role

        except HTTPException as he:
            raise he
        except Exception as e:
            logger.error(f"Firestore Authorization Error: {e}")
            raise HTTPException(status_code=403, detail="Authorization service unavailable. Please try again later.")
             
        return decoded_token
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Auth error: {e}")
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")


# Setup Environment (Ensure these are set or passed safely)
if "GOOGLE_CLOUD_PROJECT" not in os.environ:
    os.environ["GOOGLE_CLOUD_PROJECT"] = "autonomous-agent-479317"
if "GOOGLE_CLOUD_LOCATION" not in os.environ:
    os.environ["GOOGLE_CLOUD_LOCATION"] = "us-central1"
# Force Vertex AI for ADK
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None

class ChatResponse(BaseModel):
    response: str
    session_id: str

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type((GoogleAPICallError, RetryError, ServiceUnavailable, IOError)))
async def execute_agent_turn(runner, user_id, session_id, message_text):
    full_response_text = []
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=types.Content(
            role="user",
            parts=[types.Part(text=message_text)]
        )
    ):
        text_chunk = None
        
        # Check for function calls to avoid warnings/errors
        has_func = False
        if hasattr(event, 'parts'):
            for p in event.parts:
                if hasattr(p, 'function_call') and p.function_call:
                    has_func = True
        if hasattr(event, 'content') and hasattr(event.content, 'parts'):
            for p in event.content.parts:
                if hasattr(p, 'function_call') and p.function_call:
                    has_func = True
        
        # If it's purely a function call event, we might skip text extraction or log it
        if has_func and not (hasattr(event, 'text') and event.text):
            continue

        if hasattr(event, 'text') and event.text:
            text_chunk = event.text
        elif hasattr(event, 'part') and hasattr(event.part, 'text'):
            text_chunk = event.part.text
        elif hasattr(event, 'parts') and event.parts:
            for p in event.parts:
                if hasattr(p, 'text') and p.text:
                    text_chunk = p.text 
        elif hasattr(event, 'content') and event.content and hasattr(event.content, 'parts'):
            for p in event.content.parts:
                if hasattr(p, 'text') and p.text:
                    text_chunk = p.text

        if text_chunk:
            full_response_text.append(text_chunk)
            
    return "".join(full_response_text)

import user_context

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest, user: dict = Depends(verify_token)):
    session_id = req.session_id or str(uuid.uuid4())
    user_id = user.get("uid", "default_user")
    user_email = user.get("email", "unknown")
    user_role = user.get("role", "viewer")
    
    logger.info(f"Chat request from {user_email} (role: {user_role})")
    
    # SET USER CONTEXT FOR THIS REQUEST
    user_context.set_user_context(user_email, user_role)
    
    # Ensure session exists
    if session_id not in known_sessions:
        try:
            await session_service.create_session(
                app_name="gce_manager",
                user_id=user_id,
                session_id=session_id
            )
            known_sessions.add(session_id)
            logger.info(f"Created new session: {session_id}")
        except Exception as e:
            logger.warning(f"Session creation warning: {e}")
            known_sessions.add(session_id)

    # Create Runner (stateless wrapper around agent + session)
    runner = Runner(
        agent=agent,
        app_name="gce_manager",
        session_service=session_service
    )

    try:
        final_response = await execute_agent_turn(runner, user_id, session_id, req.message)
        return ChatResponse(response=final_response, session_id=session_id)
                
    except Exception as e:
        logger.error(f"Error during agent execution: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    """Readiness probe."""
    try:
        # Simple check: can we get the firestore client?
        # We won't do a heavy query to save costs/latency on frequent checks,
        # but ensuring the client initializes is good.
        firestore.client() 
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=503, detail="Service not ready")

@app.on_event("startup")
async def startup_event():
    """Seeds the managed_projects collection if empty."""
    try:
        db = firestore.client()
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "autonomous-agent-479317")
        doc_ref = db.collection('managed_projects').document(project_id)
        
        # Check if exists (sync call wrapped or just do it? It's startup, blocking is OK-ish but fast)
        # Using a transaction or just get/set is fine.
        doc = doc_ref.get()
        if not doc.exists:
            logger.info(f"Seeding 'managed_projects' with {project_id}")
            doc_ref.set({
                "project_id": project_id,
                "name": "Primary Agent Project",
                "description": "Auto-seeded on startup"
            })
        else:
             logger.info(f"Project {project_id} already managed.")
    except Exception as e:
        logger.error(f"Startup seeding failed: {e}")

from google.cloud import recommender_v1
import re


if __name__ == "__main__":
    import uvicorn
    # Listen on all interfaces for Cloud Run (port 8080 default env)
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
