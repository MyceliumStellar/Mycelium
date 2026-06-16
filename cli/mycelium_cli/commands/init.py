import os

def run_init(project_name: str):
    print(f"Initializing Mycelium project: {project_name}...")
    
    # Create directory layout
    os.makedirs(os.path.join(project_name, "agents"), exist_ok=True)
    os.makedirs(os.path.join(project_name, "shared"), exist_ok=True)
    os.makedirs(os.path.join(project_name, "tests"), exist_ok=True)
    
    # Create mycelium.toml configuration file
    config_content = f"""# Mycelium project configuration for {project_name}
[project]
name = "{project_name}"
version = "0.1.0"

[network]
default = "testnet"
"""
    with open(os.path.join(project_name, "mycelium.toml"), "w") as f:
        f.write(config_content)
        
    # Create default basic agent script
    agent_content = """from mycelium import contract, state, Symbol, i128

@contract
class BasicAgent:
    owner: Symbol
    counter: i128

    @state.instance
    def initialize(self, owner: Symbol):
        self.owner = owner
        self.counter = 0

    @state.instance
    def increment(self) -> i128:
        self.counter = self.counter + 1
        return self.counter
"""
    with open(os.path.join(project_name, "agents", "basic_agent.py"), "w") as f:
        f.write(agent_content)
        
    print(f"Project {project_name} successfully initialized.")
