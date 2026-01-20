import logging
import time
from typing import Any, Optional, List
import uuid
import copy
from google.cloud import firestore
from google.adk.sessions.base_session_service import BaseSessionService
from google.adk.sessions.base_session_service import GetSessionConfig
from google.adk.sessions.base_session_service import ListSessionsResponse
from google.adk.sessions.session import Session
from google.adk.events.event import Event
from google.adk.sessions import _session_util
from google.adk.sessions.state import State
from typing_extensions import override

logger = logging.getLogger(__name__)

class FirestoreSessionService(BaseSessionService):
    """A Firestore-backed implementation of the session service."""

    def __init__(self, collection_name: str = "sessions"):
        self.db = firestore.Client()
        self.collection_name = collection_name

    def _get_doc_ref(self, session_id: str):
        return self.db.collection(self.collection_name).document(session_id)

    @override
    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: Optional[dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> Session:
        if not session_id:
            session_id = str(uuid.uuid4())
        
        doc_ref = self._get_doc_ref(session_id)
        
        # Check existence? ADK usually expects error if exists? 
        # But for Firestore it might be cleaner to just use create() which fails if exists.
        
        # Prepare initial state from deltas (ADK logic)
        state_deltas = _session_util.extract_state_delta(state)
        # We merge them all into one persistent state dict for simplicity
        # But ADK separates app/user/session state. 
        # We will store them in a single 'state' map in Firestore, prefixed keys handled by ADK logic usually?
        # Actually in InMemory it keeps them separate. 
        # Logic: We just store the final 'session.state' which is a flat dict.
        
        # Wait, InMemorySessionService constructs session.state from deltas.
        combined_state = {}
        # Apply deltas to a temp dict? 
        # Or just trust the Session() constructor logic which takes 'state'.
        # InMemorySessionService uses `session_state` delta for constructor.
        
        session = Session(
            app_name=app_name,
            user_id=user_id,
            id=session_id,
            state=state_deltas['session'] or {},
            last_update_time=time.time(),
        )
        
        # Serialize
        session_data = session.model_dump(by_alias=True)
        # Ensure ID is set
        session_data['id'] = session_id
        
        # We also need to persist 'app_state' and 'user_state' if we want to fully support ADK scope.
        # But for this simple implementation, we might just rely on SESSION state.
        # However, to be robust, we should store everything.
        # For now, we will store the Session object as is.
        
        try:
            doc_ref.create(session_data)
        except Exception as e:
            if "AlreadyExists" in str(e) or "409" in str(e):
                 # re-raise as expected by ADK or just return existing? ADK raises.
                 from google.adk.errors.already_exists_error import AlreadyExistsError
                 raise AlreadyExistsError(f"Session {session_id} already exists.")
            raise e

        return session

    @override
    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        config: Optional[GetSessionConfig] = None,
    ) -> Optional[Session]:
        doc_ref = self._get_doc_ref(session_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            return None
        
        data = doc.to_dict()
        
        # Deserialize
        try:
            session = Session.model_validate(data)
        except Exception as e:
            logger.error(f"Failed to deserialize session {session_id}: {e}")
            return None
            
        if session.user_id != user_id or session.app_name != app_name:
            # Security/Consistency check
            return None

        # Filter events if config provided
        if config:
            if config.num_recent_events:
                 session.events = session.events[-config.num_recent_events:]
            if config.after_timestamp:
                 session.events = [e for e in session.events if e.timestamp >= config.after_timestamp]

        return session

    @override
    async def list_sessions(
        self, *, app_name: str, user_id: Optional[str] = None
    ) -> ListSessionsResponse:
        # Not strictly needed for this agent but good to have
        query = self.db.collection(self.collection_name).where("appName", "==", app_name)
        if user_id:
            query = query.where("userId", "==", user_id)
            
        docs = query.stream()
        sessions = []
        for d in docs:
            # We want sessions without events usually for listing?
            # ADK InMemory creates copies without events.
            data = d.to_dict()
            data['events'] = [] # Clear events for summary
            try:
                s = Session.model_validate(data)
                sessions.append(s)
            except: pass
            
        return ListSessionsResponse(sessions=sessions)

    @override
    async def delete_session(
        self, *, app_name: str, user_id: str, session_id: str
    ) -> None:
        self._get_doc_ref(session_id).delete()

    @override
    async def append_event(self, session: Session, event: Event) -> Event:
        if event.partial:
            return event
            
        # We need to update the session in Firestore.
        # We can append to the 'events' array using array_union?
        # But Event is a complex object (dict).
        # Also need to update 'state' and 'last_update_time'.
        
        # Process state updates first (in memory session)
        # Using parent logic if possible?
        # BaseSessionService.append_event does state updates. 
        # But we need to update storage too.
        
        # Let's call super to update the IN-MEMORY 'session' object first
        await super().append_event(session, event)
        
        # Now persist changes to Firestore
        doc_ref = self._get_doc_ref(session.id)
        
        event_data = event.model_dump(by_alias=True)
        
        # Simple update: Fetch-Modify-Write or just simple updates?
        # 'events' is an array. specific append is best to avoid overwriting if concurrent?
        # But we also update 'state'.
        
        # Ideally, we read, update, write in transaction.
        # For simplicity/speed in this single-threaded-per-user model:
        # We define a helper to serialize the event and append.
        
        update_data = {
            "lastUpdateTime": event.timestamp,
            # We append to events list
            "events": firestore.ArrayUnion([event_data])
        }
        
        # If state changed, update it too
        # ADK logic for state delta is complex. 
        # simple approach: just overwrite 'state' with new session.state
        update_data["state"] = session.state
        
        doc_ref.update(update_data)
        
        return event
