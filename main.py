import uuid
import os
import threading
import google.auth

from fastapi import FastAPI, HTTPException
from agentic_sandbox import SandboxClient
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.cloud import container_v1

app = FastAPI()

ROUTER_URL = os.getenv("ROUTER_URL")  # Your internal load balancer IP

_KUBECONFIG_PATH = "/tmp/kubeconfig.yaml"
_kube_lock = threading.Lock()
_cached_cluster = None  # (endpoint, ca_cert_b64)

def _get_cluster_endpoint_and_ca() -> tuple[str, str]:
    """
    Returns (endpoint, base64_ca_cert).
    Uses GKE API to fetch cluster metadata.
    """
    global _cached_cluster

    if _cached_cluster:
        return _cached_cluster

    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    cluster_name = os.environ["GKE_CLUSTER_NAME"]
    location = os.environ["GKE_LOCATION"]

    client = container_v1.ClusterManagerClient()
    name = f"projects/{project_id}/locations/{location}/clusters/{cluster_name}"
    cluster = client.get_cluster(name=name)

    # endpoint is typically fine; if you use private endpoint you might prefer:
    # cluster.private_cluster_config.private_endpoint (if set)
    endpoint = cluster.endpoint
    ca_cert_b64 = cluster.master_auth.cluster_ca_certificate

    _cached_cluster = (endpoint, ca_cert_b64)
    return endpoint, ca_cert_b64


def ensure_kubeconfig():
    """
    Creates/updates a kubeconfig file in /tmp using Cloud Run's service account
    (ADC) and points KUBECONFIG at it.
    """
    with _kube_lock:
        endpoint, ca_cert_b64 = _get_cluster_endpoint_and_ca()

        # IMPORTANT: include userinfo.email scope; GKE validates and retrieves email identity.
        creds, _ = google.auth.default(
            scopes=[
                "https://www.googleapis.com/auth/cloud-platform",
                "https://www.googleapis.com/auth/userinfo.email",
            ]
        )
        creds.refresh(GoogleAuthRequest())
        token = creds.token

        kubeconfig_yaml = f"""
            apiVersion: v1
            kind: Config
            clusters:
            - name: gke
            cluster:
                server: https://{endpoint}
                certificate-authority-data: {ca_cert_b64}
            users:
            - name: cloudrun-gsa
            user:
                token: {token}
            contexts:
            - name: gke
            context:
                cluster: gke
                user: cloudrun-gsa
            current-context: gke
            """

        with open(_KUBECONFIG_PATH, "w") as f:
            f.write(kubeconfig_yaml)

        os.environ["KUBECONFIG"] = _KUBECONFIG_PATH


# Store active workspaces
workspaces: dict[str, SandboxClient] = {}

@app.post("/workspaces")
def create_workspace():
    ensure_kubeconfig()
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
    ensure_kubeconfig()
    sandbox = workspaces.pop(workspace_id, None)
    if sandbox:
        sandbox.__exit__(None, None, None)  # Cleanup
    return {"deleted": True}
