import uuid
import os

from fastapi import FastAPI, HTTPException
from agentic_sandbox import SandboxClient

app = FastAPI()

ROUTER_URL = os.getenv("ROUTER_URL")  # Your internal load balancer IP


# Store active workspaces
workspaces: dict[str, SandboxClient] = {}

@app.get("/")
def echo_command():
    with SandboxClient(
        template_name="python-runtime-template",
        api_url=ROUTER_URL,
        namespace="default"
    ) as sandbox:
        return sandbox.run("echo 'Hello from the sandboxed environment!'").stdout


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
