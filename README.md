# malitan-platform
A GitOps Kubernetes platform for a real Flask application: CI, Argo CD, observability, tested backups and infrastructure as code.

The workload is **Malitan 3D**, a business-management web app I wrote for a small 3D-printing business. This repository takes it from a single container to a reproducible platform.

**Status:** work in progress, started November 2026. Currently on Day 0 (foundations).

## Stack

k3d (k3s) · Helm · Argo CD · GitHub Actions · GHCR · Prometheus · Grafana · Loki · PostgreSQL · Terraform (AWS) · Ansible

## Roadmap

### Day 0: Foundations
- [x] Installation: WSL2 / Ubuntu 24.04
- [x] Docker
- [x] kubectl, Helm, k3d, Git
- [ ] GitHub repo + basic files

- [ ] Containerize the app (multi-arch image)

### Week 1: Kubernetes and Helm
- [ ] Multi-node k3d cluster
- [ ] Helm chart from scratch: probes, resources, ConfigMap, Secret, Ingress
- [ ] Install, upgrade, rollback
- [ ] Failure drills: CrashLoopBackOff, failing probes, OOMKilled, node loss

### Week 2: CI and GitOps
- [ ] GitHub Actions: test, build, push to GHCR
- [ ] CI bumps the image tag in Git
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
```

## Design decisions

- **Local multi-node k3d cluster.** Free, rebuilt in seconds, and multi-node so node-failure drills are possible. Terraform covers the cloud side.
- **Traefik as ingress controller.** Ships with k3s and supports the Gateway API; ingress-nginx was retired in 2026.
- **One repository for app and deployment config.** Easier to review. CI uses path filters so the commit that bumps the image tag doesn't trigger another build.
- **Multi-arch images (amd64, arm64).** The same image runs on cloud nodes and on ARM boards.

## Run it locally

Instructions arrive with week 1.

## Author

David Scemama, DevOps and CI/CD engineer · [LinkedIn](https://www.linkedin.com/in/davidscemama)
