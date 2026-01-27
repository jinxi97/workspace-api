from fastapi import FastAPI, HTTPException
from agentic_sandbox import SandboxClient
import uuid
import os
from kubernetes import client
from google.auth import default
from google.auth.transport.requests import Request

app = FastAPI()

GKE_ENDPOINT = os.environ.get("GKE_ENDPOINT")
ROUTER_URL = os.getenv("ROUTER_URL")  # Your internal load balancer IP

def configure_k8s():
    """Configure Kubernetes client before using SandboxClient."""
    credentials, _ = default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(Request())
    
    configuration = client.Configuration()
    configuration.host = GKE_ENDPOINT
    configuration.api_key = {"authorization": f"Bearer {credentials.token}"}
    configuration.verify_ssl = False
    
    # Set as default configuration globally
    client.Configuration.set_default(configuration)

# Call once at startup
configure_k8s()

# Store active workspaces
workspaces: dict[str, SandboxClient] = {}

@app.post("/workspaces")
def create_workspace():
    # Generate a unique workspace ID
    workspace_id = str(uuid.uuid4())
    sandbox = SandboxClient(
        template_name="python-runtime-template",
        api_url=ROUTER_URL,
        namespace="default"
    )
    sandbox.__enter__()  # Start the sandbox
    
    # Store reference to the sandbox
    workspaces[workspace_id] = sandbox
    
    return {"workspace_id": workspace_id}

@app.post("/workspaces/{workspace_id}/exec")
def exec_command(workspace_id: str, command: str):
    sandbox = workspaces.get(workspace_id)
    if not sandbox:
        raise HTTPException(404, "Workspace not found")
    
    result = sandbox.run(command)
    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code
    }

@app.delete("/workspaces/{workspace_id}")
def delete_workspace(workspace_id: str):
    sandbox = workspaces.pop(workspace_id, None)
    if sandbox:
        sandbox.__exit__(None, None, None)  # Cleanup
    return {"deleted": True}
