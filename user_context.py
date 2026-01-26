import contextvars
from typing import Optional, Dict

# Context variable to store user info (email, role)
_user_context_var: contextvars.ContextVar[Optional[Dict[str, str]]] = contextvars.ContextVar("user_context", default=None)

def set_user_context(email: str, role: str):
    """Sets the current user context."""
    _user_context_var.set({"email": email, "role": role})

def get_user_context() -> Optional[Dict[str, str]]:
    """Gets the current user context."""
    return _user_context_var.get()

def get_current_role() -> str:
    """Returns current user role, defaults to 'viewer' if not set."""
    ctx = get_user_context()
    if ctx:
        return ctx.get("role", "viewer")
    return "viewer"

def require_admin():
    """Raises PermissionError if current user is not an admin."""
    role = get_current_role()
    if role != "admin":
        raise PermissionError(f"Access Denied: User '{get_user_context().get('email')}' with role '{role}' is not authorized to perform this action. Required role: 'admin'.")
