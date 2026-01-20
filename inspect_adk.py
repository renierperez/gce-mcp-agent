from google.adk.sessions.in_memory_session_service import InMemorySessionService
import inspect

print("Methods:")
for name, method in inspect.getmembers(InMemorySessionService, predicate=inspect.isfunction):
    print(name)
