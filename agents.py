import os
import yaml
from google.adk.agents import LlmAgent
try:
    import tools
except ImportError:
    from gce_manager_agent import tools

def load_config():
    # Load all prompts
    base_dir = os.path.dirname(__file__)
    prompt_path = os.path.join(base_dir, 'prompts', 'agent_instructions.yaml')
    
    with open(prompt_path, 'r') as f:
        data = yaml.safe_load(f)
        
    return data

def create_agent():
    config = load_config()
    combined_instructions = f"{config.get('persona', '')}\n\n{config.get('rules', '')}\n\n{config.get('instructions', '')}"
    
    # Model configuration
    # Updated to Gemini 2.5 Flash (gemini-2.5-flash)
    model_name = os.environ.get("MODEL_NAME", "gemini-2.5-flash")

    agent = LlmAgent(
        name="GceManagerAgent",
        model=model_name,
        tools=[
            tools.list_instances,
            tools.get_instance_report,
            tools.start_instance,
            tools.stop_instance,
            tools.list_managed_projects,
            tools.create_custom_instance,
            tools.get_instance_sku_report
        ],
        instruction=combined_instructions
    )
    
    return agent
