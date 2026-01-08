import asyncio
import os
import sys
from agents import create_agent
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.genai import types

# Setup minimal env for ADK
os.environ["GOOGLE_CLOUD_PROJECT"] = "autonomous-agent-479317"
os.environ["GOOGLE_CLOUD_LOCATION"] = "us-central1"
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"

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
            
            async for event in runner.run_async(
                user_id="user",
                session_id="session_1",
                new_message=types.Content(
                    role="user",
                    parts=[types.Part(text=user_input)]
                )
            ):
                # Print text parts as they arrive
                # DEBUG: Inspect event
                # print(f"DEBUG_EVENT: {type(event)} {event}")
                try:
                    if hasattr(event, 'text') and event.text:
                         print(event.text, end="", flush=True)
                    elif hasattr(event, 'part') and hasattr(event.part, 'text'):
                         print(event.part.text, end="", flush=True)
                    elif hasattr(event, 'parts') and event.parts:
                        # Sometimes content is in parts list directly on event or event.content
                        for p in event.parts:
                            if hasattr(p, 'text') and p.text:
                                print(p.text, end="", flush=True)
                    elif hasattr(event, 'content') and event.content and hasattr(event.content, 'parts'):
                         for p in event.content.parts:
                            if hasattr(p, 'text') and p.text:
                                print(p.text, end="", flush=True)
                except:
                     pass
            
            print() # Newline after response
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
