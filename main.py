import uuid
import os

from fastapi import FastAPI, HTTPException, Header, Depends, status
from agentic_sandbox import SandboxClient
from kubernetes import client, config

app = FastAPI()

ROUTER_URL = os.getenv("ROUTER_URL", "http://sandbox-router-svc.default.svc.cluster.local:8080")  # Your internal load balancer IP
API_SECRET = os.getenv("API_SECRET", "dev-secret-key")
STORAGE_CLASS = os.getenv("STORAGE_CLASS", "standard-rwo")
WORKSPACE_STORAGE_SIZE = os.getenv("WORKSPACE_STORAGE_SIZE", "5Gi")
SANDBOX_IMAGE = os.getenv("SANDBOX_IMAGE", "registry.k8s.io/agent-sandbox/python-runtime-sandbox:v0.1.0")

if not ROUTER_URL:
    # Fail here so you know exactly what is wrong
    raise ValueError("CRITICAL: ROUTER_URL env var is missing. Cannot start Internal Mode.")
else:
    print(f"ROUTER_URL: {ROUTER_URL}")

# Load k8s config (in-cluster or local)
try:
    config.load_incluster_config()
except:
    config.load_kube_config()

k8s_core = client.CoreV1Api()
k8s_custom = client.CustomObjectsApi()

# CRD details for SandboxTemplate
TEMPLATE_GROUP = "extensions.agents.x-k8s.io"
TEMPLATE_VERSION = "v1alpha1"
TEMPLATE_PLURAL = "sandboxtemplates"

# Store active workspaces
workspaces: dict[str, SandboxClient] = {}


def create_pvc(workspace_id: str, namespace: str = "default") -> str:
    """Create a PVC for a workspace, returns PVC name."""
    pvc_name = f"workspace-{workspace_id}"
    
    pvc = client.V1PersistentVolumeClaim(
        metadata=client.V1ObjectMeta(
            name=pvc_name,
            labels={"workspace-id": workspace_id}
        ),
        spec=client.V1PersistentVolumeClaimSpec(
            access_modes=["ReadWriteOnce"],
            resources=client.V1ResourceRequirements(
                requests={"storage": WORKSPACE_STORAGE_SIZE}
            ),
            storage_class_name=STORAGE_CLASS
        )
    )
    
    k8s_core.create_namespaced_persistent_volume_claim(namespace=namespace, body=pvc)
    return pvc_name


def delete_pvc(workspace_id: str, namespace: str = "default"):
    """Delete a workspace's PVC."""
    pvc_name = f"workspace-{workspace_id}"
    try:
        k8s_core.delete_namespaced_persistent_volume_claim(name=pvc_name, namespace=namespace)
    except client.ApiException as e:
        if e.status != 404:
            raise

def create_sandbox_template(workspace_id: str, pvc_name: str, namespace: str = "default") -> str:
    """Create a SandboxTemplate with the workspace's PVC mounted."""
    template_name = f"workspace-template-{workspace_id}"
    
    template = {
        "apiVersion": f"{TEMPLATE_GROUP}/{TEMPLATE_VERSION}",
        "kind": "SandboxTemplate",
        "metadata": {
            "name": template_name,
            "namespace": namespace,
            "labels": {"workspace-id": workspace_id}
        },
        "spec": {
            "podTemplate": {
                "metadata": {
                    "labels": {"workspace-id": workspace_id}
                },
                "spec": {
                    "securityContext": {
                        "runAsUser": 1000,
                        "runAsGroup": 1000,
                        "fsGroup": 1000
                    },
                    "containers": [{
                        "name": "python-runtime",
                        "image": SANDBOX_IMAGE,
                        "ports": [{"containerPort": 8888, "protocol": "TCP"}],
                        "readinessProbe": {
                            "httpGet": {"path": "/", "port": 8888},
                            "initialDelaySeconds": 0,
                            "periodSeconds": 1
                        },
                        "resources": {
                            "requests": {"cpu": "1", "memory": "2Gi", "ephemeral-storage": "5Gi"},
                            "limits": {"cpu": "2", "memory": "4Gi", "ephemeral-storage": "5Gi"}
                        },
                        "volumeMounts": [{
                            "name": "workspace",
                            "mountPath": "/workspace"
                        }]
                    }],
                    "volumes": [{
                        "name": "workspace",
                        "persistentVolumeClaim": {"claimName": pvc_name}
                    }],
                    "restartPolicy": "OnFailure",
                    "runtimeClassName": "gvisor"
                }
            }
        }
    }
    
    k8s_custom.create_namespaced_custom_object(
        group=TEMPLATE_GROUP,
        version=TEMPLATE_VERSION,
        namespace=namespace,
        plural=TEMPLATE_PLURAL,
        body=template
    )
    
    return template_name


def delete_sandbox_template(workspace_id: str, namespace: str = "default"):
    """Delete a workspace's SandboxTemplate."""
    template_name = f"workspace-template-{workspace_id}"
    try:
        k8s_custom.delete_namespaced_custom_object(
            group=TEMPLATE_GROUP,
            version=TEMPLATE_VERSION,
            namespace=namespace,
            plural=TEMPLATE_PLURAL,
            name=template_name
        )
    except client.ApiException as e:
        if e.status != 404:
            raise


async def verify_secret(x_api_secret: str = Header(...)):
    if x_api_secret != API_SECRET:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API Secret")


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
    
    # 1. Create dedicated PVC
    pvc_name = create_pvc(workspace_id)
    
    # 2. Create SandboxTemplate with PVC mounted
    template_name = create_sandbox_template(workspace_id, pvc_name)
    
    # 3. Create sandbox using the custom template
    sandbox = SandboxClient(
        template_name=template_name,
        # api_url=ROUTER_URL,
        namespace="default"
    )
    sandbox.__enter__()
    
    workspaces[workspace_id] = {
        "sandbox": sandbox,
        "pvc_name": pvc_name,
        "template_name": template_name
    }
    
    return {"workspace_id": workspace_id}


@app.post("/workspaces/{workspace_id}/exec", dependencies=[Depends(verify_secret)])
def exec_command(workspace_id: str, command: str):
    ws = workspaces.get(workspace_id)
    if not ws:
        raise HTTPException(404, "Workspace not found")
    
    result = ws["sandbox"].run(command)
    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code
    }


@app.delete("/workspaces/{workspace_id}", dependencies=[Depends(verify_secret)])
def delete_workspace(workspace_id: str, keep_storage: bool = False):
    ws = workspaces.pop(workspace_id, None)
    if ws:
        ws["sandbox"].__exit__(None, None, None)
        delete_sandbox_template(workspace_id)
        
        if not keep_storage:
            delete_pvc(workspace_id)
    
    return {"deleted": True, "storage_deleted": not keep_storage}