# malitan-platform
A GitOps Kubernetes platform for a real Flask application: CI, Argo CD, observability, tested backups and infrastructure as code.

The workload is **Malitan 3D**, a business-management web app I wrote for a small 3D-printing business. This repository takes it from a single container to a reproducible platform.

**Status:** work in progress, started November 2026. Week 1 (Kubernetes and Helm) done; now on week 2 (CI and GitOps).

## Stack

k3d (k3s) · Helm · Argo CD · GitHub Actions · GHCR · Prometheus · Grafana · Loki · PostgreSQL · Terraform (AWS) · Ansible

## Roadmap

### Day 0: Foundations
- [x] Installation: WSL2 / Ubuntu 24.04
- [x] Docker
- [x] kubectl, Helm, k3d, Git
- [x] GitHub repo + basic files
- [x] Containerize the app (multi-arch image)

### Week 1: Kubernetes and Helm
- [x] Multi-node k3d cluster
- [x] Helm chart from scratch: probes, resources, ConfigMap, Secret, Ingress
- [x] Install, upgrade, rollback
- [x] Failure drills: CrashLoopBackOff, failing probes, OOMKilled, node loss

### Week 2: CI and GitOps
- [x] GitHub Actions: test, build, push to GHCR
- [x] CI bumps the image tag in Git
- [ ] Argo CD with automated sync and self-heal
- [ ] Secrets encrypted in Git
- [ ] dev and prod environments

### Week 3: Observability and data
- [ ] Prometheus and a Grafana dashboard (requests, errors, latency)
- [ ] Loki for logs
- [ ] Alerting on a real failure
- [ ] PostgreSQL on a persistent volume
- [ ] Scheduled backups with a tested restore

### Week 4: Infrastructure as code
- [ ] Terraform: AWS VPC, S3 backup bucket, least-privilege IAM
- [ ] Remote state with locking; `terraform plan` on pull requests
- [ ] Ansible: provision a k3s node from a fresh VM, idempotently
- [ ] Architecture diagrams

## Repository layout

Directories are added as the work progresses.

```
app/                Flask application and Dockerfile
charts/malitan/     Helm chart
argocd/             Argo CD Applications
.github/workflows/  CI pipelines
terraform/          AWS infrastructure
ansible/            Node provisioning
k3d/                Cluster config
```

## Design decisions

- **Local multi-node k3d cluster.** Free, rebuilt in seconds, and multi-node so node-failure drills are possible. Terraform covers the cloud side.
- **Traefik as ingress controller.** Ships with k3s and supports the Gateway API; ingress-nginx was retired in 2026.
- **One repository for app and deployment config.** Easier to review. CI uses path filters so the commit that bumps the image tag doesn't trigger another build.
- **Multi-arch images (amd64, arm64).** The same image runs on cloud nodes and on ARM boards.

## Run it locally

### Prerequisites (Windows 11 + WSL2)

The lab runs inside WSL2 (Ubuntu 24.04) with Docker Engine (not Docker Desktop), kubectl, Helm and k3d. Two Windows settings are needed so the browser on Windows reaches the cluster on `localhost:8080`:

1. **WSL in mirrored networking mode.** In the default NAT mode, WSL relays ports opened in Linux to Windows' `localhost`, but the relay does not pick up ports published by recent Docker Engine versions: the app answers inside WSL while Windows gets "connection refused". Mirrored mode shares Windows' network interfaces, so no relay is involved. In `%UserProfile%\.wslconfig`:

   ```ini
   [wsl2]
   networkingMode=mirrored
   ```

   Then run `wsl --shutdown` and reopen the WSL terminal.

2. **Port 8080 not reserved by Windows.** Hyper-V reserves blocks of ports inside Windows' dynamic port range, and in mirrored mode those reservations also apply to WSL. If the range has been moved down (for example to 1024-15000), 8080 can end up reserved and the k3d load balancer fails with `address already in use` even though nothing is listening. Check in PowerShell:

   ```powershell
   netsh interface ipv4 show excludedportrange protocol=tcp
   netsh int ipv4 show dynamicport tcp
   ```

   If 8080 falls in a reserved range, restore the default dynamic range (49152-65535) from an administrator PowerShell, then reboot:

   ```powershell
   netsh int ipv4 set dynamicport tcp start=49152 num=16384
   netsh int ipv6 set dynamicport tcp start=49152 num=16384
   ```

### Start the cluster and deploy

```bash
# Cluster: 1 server, 2 agents, host port 8080 to the load balancer
k3d cluster create --config k3d/cluster.yaml

# Image: build it, then copy it into the cluster's nodes (no registry yet)
scripts/docker-build.sh            # builds malitan:dev
k3d image import malitan:dev -c malitan

# Namespace and secret key, created outside the chart (never committed)
kubectl create namespace malitan
kubectl create secret generic malitan-auth -n malitan \
  --from-literal=AUTH_SECRET_KEY="$(openssl rand -hex 32)"

# App
helm install malitan charts/malitan -n malitan
```

An init container runs `flask init-db` before the app starts: it creates the missing tables and the initial users (with unusable passwords), and leaves an existing database alone. The SQLite database lives on a PersistentVolumeClaim, so it survives pod restarts. Set a password (prompted, never passed on the command line), then open <http://malitan.localhost:8080>:

```bash
kubectl exec -it -n malitan deploy/malitan -c malitan -- flask set-password david
```

## Author

David Scemama, DevOps and CI/CD engineer · [LinkedIn](https://www.linkedin.com/in/davidscemama)
