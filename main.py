import uuid
import os
import asyncio
import json

from fastapi import FastAPI, HTTPException, Header, Depends, status, WebSocket, WebSocketDisconnect
from agentic_sandbox import SandboxClient
from kubernetes import client, config
from kubernetes.stream import stream

app = FastAPI()

ROUTER_URL = os.getenv("ROUTER_URL", "http://sandbox-router-svc.agent-sandbox-application.svc.cluster.local:8080")  # Your internal load balancer IP
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
workspaces: dict[str, dict[str, object]] = {}


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
        api_url=ROUTER_URL,
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


@app.websocket("/workspaces/{workspace_id}/terminal")
async def terminal_session(websocket: WebSocket, workspace_id: str):
    await websocket.accept()

    api_secret = websocket.query_params.get("token")
    if not api_secret:
        protocol_header = websocket.headers.get("sec-websocket-protocol")
        if protocol_header:
            # Use first protocol value as token.
            api_secret = protocol_header.split(",")[0].strip()
    if api_secret != API_SECRET:
        await websocket.close(code=1008)
        return

    ws = workspaces.get(workspace_id)
    if not ws:
        await websocket.send_json({"type": "error", "data": "Workspace not found"})
        await websocket.close(code=1008)
        return

    sandbox = ws["sandbox"]
    pod_name = getattr(sandbox, "pod_name", None)
    if not pod_name:
        await websocket.send_json({"type": "error", "data": "Workspace pod not ready"})
        await websocket.close(code=1011)
        return

    exec_client = None
    try:
        exec_client = stream(
            k8s_core.connect_get_namespaced_pod_exec,
            pod_name,
            "default",
            container="python-runtime",
            command=["/bin/sh", "-c", "cd /workspace && exec /bin/sh"],
            stderr=True,
            stdin=True,
            stdout=True,
            tty=True,
            _preload_content=False,
        )

        async def send_output():
            try:
                while exec_client.is_open():
                    await asyncio.to_thread(exec_client.update, timeout=1)
                    while exec_client.peek_stdout():
                        data = exec_client.read_stdout()
                        if data:
                            await websocket.send_json({"type": "stdout", "data": data})
                    while exec_client.peek_stderr():
                        data = exec_client.read_stderr()
                        if data:
                            await websocket.send_json({"type": "stderr", "data": data})
                    await asyncio.sleep(0.01)
            except Exception:
                pass

        async def recv_input():
            try:
                while True:
                    message = await websocket.receive_text()
                    try:
                        payload = json.loads(message)
                    except json.JSONDecodeError:
                        payload = None

                    if isinstance(payload, dict):
                        if "input" in payload:
                            exec_client.write_stdin(payload["input"])
                        elif payload.get("signal") == "ctrl+c":
                            exec_client.write_stdin("\x03")
                        elif payload.get("type") == "resize":
                            rows = payload.get("rows")
                            cols = payload.get("cols")
                            if isinstance(rows, int) and isinstance(cols, int):
                                resize_payload = json.dumps({"Height": rows, "Width": cols})
                                exec_client.write_channel(4, resize_payload)
                        else:
                            exec_client.write_stdin(message)
                    else:
                        exec_client.write_stdin(message)
            except WebSocketDisconnect:
                pass

        output_task = asyncio.create_task(send_output())
        input_task = asyncio.create_task(recv_input())
        done, pending = await asyncio.wait(
            {output_task, input_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        if exec_client and exec_client.is_open():
            exec_client.close()


@app.delete("/workspaces/{workspace_id}", dependencies=[Depends(verify_secret)])
def delete_workspace(workspace_id: str, keep_storage: bool = False):
    ws = workspaces.pop(workspace_id, None)
    if ws:
        ws["sandbox"].__exit__(None, None, None)
        delete_sandbox_template(workspace_id)
        
        if not keep_storage:
            delete_pvc(workspace_id)
    
    return {"deleted": True, "storage_deleted": not keep_storage}