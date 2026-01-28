Agentic Sandbox Workspace API - Setup GuideThis documentation covers the end-to-end setup for the workspace-api service on Google Kubernetes Engine (GKE). It includes internal cluster permissions, CI/CD, Gateway networking, HTTPS security, and rate limiting.1. PrerequisitesGKE Cluster running with Gateway API enabled.Domain Name (e.g., api.funky.dev) managed via GoDaddy or Cloud DNS.Tools: gcloud, kubectl.2. Cluster Identity & Permissions (RBAC)The workspace-api needs permission to talk to the Kubernetes API to spin up dynamic sandboxes.A. Create the RBAC Manifest (sandbox-rbac.yaml)This grants the service account permission to manage sandboxes, sandboxclaims, and sandboxtemplates.apiVersion: v1
kind: ServiceAccount
metadata:
  name: workspace-api-sa
  namespace: default
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: sandbox-manager-role
  namespace: default
rules:
- apiGroups: ["extensions.agents.x-k8s.io", "agents.x-k8s.io"]
  resources: ["sandboxclaims", "sandboxtemplates", "sandboxes"]
  verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: sandbox-manager-binding
  namespace: default
subjects:
- kind: ServiceAccount
  name: workspace-api-sa
  namespace: default
roleRef:
  kind: Role
  name: sandbox-manager-role
  apiGroup: rbac.authorization.k8s.io
Apply it:kubectl apply -f sandbox-rbac.yaml
3. Application Configuration (Internal Mode)The Python application must use "Internal Mode" to avoid needing kubectl inside the container.A. Deployment Manifest (deployment.yaml)Critical: Ensure ROUTER_URL is set correctly. Do NOT use ROUTER_INTERNAL_URL or the Python client will crash trying to find kubectl.apiVersion: apps/v1
kind: Deployment
metadata:
  name: workspace-api
  namespace: default
spec:
  replicas: 1
  selector:
    matchLabels:
      app: workspace-api
  template:
    metadata:
      labels:
        app: workspace-api
    spec:
      serviceAccountName: workspace-api-sa  # <--- Must match RBAC ServiceAccount
      containers:
      - name: workspace-api
        image: gcr.io/funky-485504/workspace-api:latest
        imagePullPolicy: Always
        ports:
        - containerPort: 8080
        env:
        # Correct Internal DNS for the router service
        - name: ROUTER_URL 
          value: "[http://sandbox-router-svc.default.svc.cluster.local:8080](http://sandbox-router-svc.default.svc.cluster.local:8080)"
        - name: SANDBOX_TEMPLATE
          value: "python-sandbox-template"
B. Service Manifest (service.yaml)Use ClusterIP because the Gateway handles external traffic.apiVersion: v1
kind: Service
metadata:
  name: workspace-api
spec:
  type: ClusterIP
  selector:
    app: workspace-api
  ports:
  - protocol: TCP
    port: 8080        # Service Port (Must match HTTPRoute)
    targetPort: 8080  # Container Port
4. Public Access (GKE Gateway)We use the GKE Gateway API for a clean L7 Load Balancer setup.A. Gateway Manifest (gateway.yaml)This creates the Global External Load Balancer and attaches the SSL Certificate Map.kind: Gateway
apiVersion: gateway.networking.k8s.io/v1
metadata:
  name: workspace-gateway
  namespace: default
  annotations:
    # Link to Certificate Manager Map (Full Path preferred)
    networking.gke.io/certmap: funky-cert-map
spec:
  gatewayClassName: gke-l7-global-external-managed
  listeners:
  - name: https
    protocol: HTTPS
    port: 443
    allowedRoutes:
      namespaces:
        from: Same
  - name: http
    protocol: HTTP
    port: 80
    allowedRoutes:
      namespaces:
        from: Same
B. HTTP Route (http-route.yaml)Routes traffic from api.funky.dev to the service.kind: HTTPRoute
apiVersion: gateway.networking.k8s.io/v1
metadata:
  name: workspace-api-route
spec:
  parentRefs:
  - name: workspace-gateway
  hostnames:
  - "api.funky.dev"
  rules:
  - backendRefs:
    - name: workspace-api
      port: 8080
C. HTTP-to-HTTPS Redirect (redirect-route.yaml)Forces secure connections.kind: HTTPRoute
apiVersion: gateway.networking.k8s.io/v1
metadata:
  name: api-redirect-route
spec:
  parentRefs:
  - name: workspace-gateway
    sectionName: http # Attach only to port 80 listener
  hostnames:
  - "api.funky.dev"
  rules:
  - filters:
    - type: RequestRedirect
      requestRedirect:
        scheme: https
        statusCode: 301
5. Security InfrastructureA. SSL Certificates (Certificate Manager)Do NOT use ManagedCertificate resources with Gateway. Use the CLI:Enable API: gcloud services enable certificatemanager.googleapis.comCreate Cert: gcloud certificate-manager certificates create api-funky-cert --domains="api.funky.dev"Create Map: gcloud certificate-manager maps create funky-cert-mapLink Entry: ```bashgcloud certificate-manager maps entries create api-funky-entry--map=funky-cert-map--hostname="api.funky.dev"--certificates=api-funky-cert
B. Rate Limiting (Cloud Armor)Create Policy:gcloud compute security-policies create workspace-rate-limit
gcloud compute security-policies rules create 100 \
    --security-policy workspace-rate-limit \
    --action "throttle" \
    --rate-limit-threshold-count 60 \
    --rate-limit-threshold-interval-sec 60 \
    --enforce-on-key "IP"
Link via Kubernetes (backend-policy.yaml):apiVersion: networking.gke.io/v1
kind: GCPBackendPolicy
metadata:
  name: workspace-api-security
spec:
  default:
    securityPolicy: workspace-rate-limit
  targetRef:
    group: ""
    kind: Service
    name: workspace-api
6. CI/CD (Cloud Build)Trigger: Push to main.File: cloudbuild.yamlEnsure the Cloud Build Service Account has:roles/container.developer (To update GKE)roles/artifactregistry.writer (To push images)7. Troubleshooting Cheat SheetFileNotFoundError: 'kubectl': The Python client is in "Local Mode". Check that ROUTER_URL is set in the Deployment env vars.Gateway Error GWCER106: The Gateway cannot find the SSL cert. Check the networking.gke.io/certmap annotation or verify the map is active via gcloud certificate-manager maps list.Gateway Error GWCER104: Port mismatch. Ensure HTTPRoute references the Service port (8080), not targetPort.Certificate Stuck in Provisioning: Check DNS. Ensure api.funky.dev points to the Gateway IP and there are NO AAAA records.