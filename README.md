# Workspace API

A FastAPI-based service for managing isolated execution environments (sandboxes) using the [Agentic Sandbox](https://github.com/kubernetes-sigs/agent-sandbox) framework. This API allows you to create ephemeral workspaces, execute commands within them, and manage their lifecycle.

## Features

- **Create Workspaces**: Provision isolated sandbox environments on-demand.
- **Execute Commands**: Run shell commands securely within the provisioned sandboxes.
- **Lifecycle Management**: Clean up and delete workspaces when they are no longer needed.
- **Kubernetes Native**: Designed to run within a Kubernetes cluster, leveraging Custom Resource Definitions (CRDs) for sandbox management.

## Prerequisites

- Python 3.13+
- A Kubernetes cluster with [Agentic Sandbox](https://github.com/kubernetes-sigs/agent-sandbox) installed.
- Access to the `sandbox-router-svc` within the cluster.

## Installation & Local Development

This project uses [`uv`](https://github.com/astral-sh/uv) for dependency management.

1.  **Clone the repository:**
    ```bash
    git clone <repository-url>
    cd workspace-api
    ```

2.  **Install dependencies:**
    ```bash
    uv sync
    ```

3.  **Run the application:**
    Set the `ROUTER_URL` environment variable to point to your sandbox router service.
    ```bash
    export ROUTER_URL="http://localhost:8080" # Update with your actual router URL
    uv run fastapi dev main.py
    ```

## API Usage

### 1. Create a Workspace
Initialize a new sandbox environment.

**Request:**
`POST /workspaces`

**Response:**
```json
{
  "workspace_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

### 2. Execute a Command
Run a shell command in the created workspace.

**Request:**
`POST /workspaces/{workspace_id}/exec?command=echo hello`

**Response:**
```json
{
  "stdout": "hello\n",
  "stderr": "",
  "exit_code": 0
}
```

### 3. Delete a Workspace
Terminate the sandbox environment.

**Request:**
`DELETE /workspaces/{workspace_id}`

**Response:**
```json
{
  "deleted": true
}
```

### 4. Health Check / Echo
Simple verification that the API and Sandbox connection are working.

**Request:**
`GET /`

## Deployment

The application is designed to be deployed to Kubernetes.

1.  **Build the Docker image:**
    ```bash
    gcloud builds submit --tag gcr.io/your-project/workspace-api:latest .
    ```

2.  **Apply RBAC permissions:**
    The API needs permissions to manage `sandboxclaims`, `sandboxtemplates`, and `sandboxes`.
    ```bash
    kubectl apply -f workspace-rbac.yaml
    ```

3.  **Deploy the Service and Deployment:**
    ```bash
    kubectl apply -f workspace-api-deployment.yaml
    kubectl apply -f workspace-api-service.yaml
    ```

## Configuration

| Environment Variable | Description | Default |
|----------------------|-------------|---------|
| `ROUTER_URL` | URL of the Sandbox Router Service | `http://sandbox-router-svc.default.svc.cluster.local:8080` |
