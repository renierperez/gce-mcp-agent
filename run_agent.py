import asyncio
import os
import sys
import warnings
from agents import create_agent
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.genai import types

# Suppress warnings about non-text parts
warnings.filterwarnings("ignore", message=".*non-text parts.*")

# Setup minimal env for ADK
os.environ["GOOGLE_CLOUD_PROJECT"] = "autonomous-agent-479317"
os.environ["GOOGLE_CLOUD_LOCATION"] = "us-central1"
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"

# Context manager to suppress stderr
class SuppressStderr:
    def __enter__(self):
        self.original_stderr = sys.stderr
        sys.stderr = open(os.devnull, 'w')
    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stderr.close()
        sys.stderr = self.original_stderr

async def main():
    agent = create_agent()
    print(f"🤖 Agent {agent.name} initialized. Type 'exit' to quit.")
    
    # Setup Runner and Session Service
    session_service = InMemorySessionService()
    await session_service.create_session(
        app_name="gce_manager",
        user_id="user",
        session_id="session_1"
    )
    runner = Runner(
        agent=agent,
        app_name="gce_manager",
        session_service=session_service
    )
    
    print("-" * 50)

    while True:
        try:
            user_input = input("\nUser: ")
            if user_input.lower() in ["exit", "quit"]:
                break
            
            # Suppress stderr during generator creation/iteration to catch the warning
            # The warning might be emitted when the generator yields
            # Ideally we want to suppress only the warning, but this is a brute force for the specific unwanted output
            with SuppressStderr():
                 async for event in runner.run_async(
                    user_id="user",
                    session_id="session_1",
                    new_message=types.Content(
                        role="user",
                        parts=[types.Part(text=user_input)]
                    )
                ):
                    # Restore stderr temporarily to print output
                    sys.stderr = sys.__stdout__ # Hacky restore for critical errors if any, or just print to stdout
                    
                    try:
                        # Check for function calls first to avoid SDK warning about invoking .text on non-text parts
                        # The warning comes from accessing .text when only function_call is present
                        
                        # Function call check (structure varies by SDK version, simplified heuristic)
                        has_func_call = False
                        if hasattr(event, 'parts'):
                            for p in event.parts:
                                if hasattr(p, 'function_call') and p.function_call:
                                    has_func_call = True
                                    break
                        if hasattr(event, 'content') and hasattr(event.content, 'parts'):
                            for p in event.content.parts:
                                if hasattr(p, 'function_call') and p.function_call:
                                    has_func_call = True
                                    break
                        
                        if has_func_call:
                            # Skip text printing for function calls (or print a debug msg if enabled)
                            continue

                        if hasattr(event, 'text') and event.text:
                             print(event.text, end="", flush=True)
                        elif hasattr(event, 'part') and hasattr(event.part, 'text'):
                             print(event.part.text, end="", flush=True)
                        elif hasattr(event, 'parts') and event.parts:
                            for p in event.parts:
                                if hasattr(p, 'text') and p.text:
                                    print(p.text, end="", flush=True)
                        elif hasattr(event, 'content') and event.content and hasattr(event.content, 'parts'):
                             for p in event.content.parts:
                                if hasattr(p, 'text') and p.text:
                                    print(p.text, end="", flush=True)
                    except Exception:
                         # Silently ignore iteration errors
                         pass
                    
                    # Re-suppress for next yield
                    sys.stderr = open(os.devnull, 'w')

            print() # Newline after response
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            # Ensure stderr is back
            sys.stderr = sys.__stdout__
            print(f"Error: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
