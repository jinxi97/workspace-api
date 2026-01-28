import uuid
import os

from fastapi import FastAPI, HTTPException, Header, Depends, status
from agentic_sandbox import SandboxClient

app = FastAPI()

ROUTER_URL = os.getenv("ROUTER_URL")  # Your internal load balancer IP
API_SECRET = os.getenv("API_SECRET", "dev-secret-key")

if not ROUTER_URL:
    # Fail here so you know exactly what is wrong
    raise ValueError("CRITICAL: ROUTER_URL env var is missing. Cannot start Internal Mode.")
else:
    print(f"ROUTER_URL: {ROUTER_URL}")

# Store active workspaces
workspaces: dict[str, SandboxClient] = {}

async def verify_secret(x_api_secret: str = Header(..., description="Secret token to access workspace APIs")):
    """
    Middleware-like dependency to verify the secret header.
    Default header name is 'x-api-secret' (case-insensitive).
    """
    if x_api_secret != API_SECRET:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Secret"
        )

@app.get("/")
def health_check():
    return {"status": "healthy"}

@app.get("/echo", dependencies=[Depends(verify_secret)])
def echo_command():
    print(f"ROUTER_URL: {ROUTER_URL}")
    with SandboxClient(
        template_name="python-runtime-template",
        api_url=ROUTER_URL,
        namespace="default"
    ) as sandbox:
        output = sandbox.run("echo 'Hello from the sandboxed environment!'").stdout
    return output


@app.post("/workspaces", dependencies=[Depends(verify_secret)])
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

@app.post("/workspaces/{workspace_id}/exec", dependencies=[Depends(verify_secret)])
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

@app.delete("/workspaces/{workspace_id}", dependencies=[Depends(verify_secret)])
def delete_workspace(workspace_id: str):
    sandbox = workspaces.pop(workspace_id, None)
    if sandbox:
        sandbox.__exit__(None, None, None)  # Cleanup
    return {"deleted": True}
