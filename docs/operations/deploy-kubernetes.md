# Deploy on Kubernetes

Plain manifests with Kustomize, in
[`deploy/kubernetes/base`](https://github.com/rogvid/zentral/tree/main/deploy/kubernetes/base).
You bring PostgreSQL, an ingress controller and a way to manage secrets.

| Object | Purpose |
|---|---|
| `ConfigMap zentral-config` | Non-secret settings |
| `Secret zentral-secrets` | Database URL and the three keys (**you create it**) |
| `Job zentral-migrate` | `zentral db upgrade` |
| `Deployment zentral-api` (×2), `Service`, `PodDisruptionBudget` | The server |
| `Deployment zentral-worker` | Background jobs |
| `Ingress zentral` | TLS and routing |
| `NetworkPolicy` ×3 | Default deny; API only from the ingress; egress to DNS, PostgreSQL and HTTPS |

All pods meet the *restricted* Pod Security Standard: non-root (uid 10001),
read-only root filesystem, no capabilities, `RuntimeDefault` seccomp, no
service-account token.

## 1. Image

Build and push to your registry, pinned by digest in production:

```bash
docker build -t registry.example.com/zentral:0.1.0 .
docker push registry.example.com/zentral:0.1.0
```

## 2. An overlay for your environment

Do not edit `base`. Create an overlay:

```yaml
# deploy/kubernetes/overlays/prod/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: zentral
resources:
  - ../../base
images:
  - name: zentral
    newName: registry.example.com/zentral
    newTag: "0.1.0"
patches:
  - target: { kind: ConfigMap, name: zentral-config }
    patch: |
      - { op: replace, path: /data/ZENTRAL_PUBLIC_BASE_URL, value: https://servicedesk.example.com }
      - { op: replace, path: /data/ZENTRAL_ALLOWED_HOSTS, value: servicedesk.example.com }
      - { op: replace, path: /data/ZENTRAL_ENTRA_TENANT_ID, value: "<tenant-id>" }
      - { op: replace, path: /data/ZENTRAL_ENTRA_API_CLIENT_ID, value: "<api-client-id>" }
      - { op: replace, path: /data/ZENTRAL_ENTRA_SPA_CLIENT_ID, value: "<spa-client-id>" }
  - target: { kind: Ingress, name: zentral }
    patch: |
      - { op: replace, path: /spec/tls/0/hosts/0, value: servicedesk.example.com }
      - { op: replace, path: /spec/rules/0/host, value: servicedesk.example.com }
```

For a generic OIDC provider, replace the `ZENTRAL_ENTRA_*` entries with
`ZENTRAL_AUTH_MODE: oidc` and the `ZENTRAL_OIDC_*` settings
([reference](identity-oidc.md)).

## 3. Secrets

```bash
kubectl create namespace zentral
kubectl label namespace zentral pod-security.kubernetes.io/enforce=restricted

cp deploy/kubernetes/base/secrets.env.example secrets.env && chmod 600 secrets.env
docker run --rm registry.example.com/zentral:0.1.0 keys generate   # paste into secrets.env
$EDITOR secrets.env                                                # database URL

kubectl -n zentral create secret generic zentral-secrets --from-env-file=secrets.env
shred -u secrets.env      # after storing the keys in your vault
```

Better: keep the values in your vault and sync them with External Secrets
Operator or the Secrets Store CSI driver, so they never exist as a file. Enable
encryption at rest for Kubernetes Secrets in either case.

## 4. Apply

```bash
kubectl apply -k deploy/kubernetes/overlays/prod
kubectl -n zentral wait --for=condition=complete job/zentral-migrate --timeout=120s
kubectl -n zentral rollout status deploy/zentral-api
kubectl -n zentral exec deploy/zentral-api -- zentral doctor
```

API pods stay *not ready* until the schema matches their version, so applying
everything at once is safe: the rollout simply waits for the migration Job.

Then continue with the
[first-deployment runbook](../runbooks/first-deployment.md#in-the-application).

## Things to adapt

**Ingress controller.** The annotations are for ingress-nginx. Whatever you
use must: terminate TLS, limit bodies to 1 MiB, and set `X-Forwarded-For`.
Narrow `--forwarded-allow-ips` in `api.yaml` from `*` to your ingress
controller's pod CIDR — the network policy already ensures only the ingress
namespace can connect, this makes it defence in depth.

**Network policies.** `api-from-ingress` assumes the ingress controller runs
in the namespace `ingress-nginx`. `zentral-egress` allows PostgreSQL on 5432
and HTTPS to public addresses. If your database or model server has a private
address on another port, add a rule; if your CNI supports FQDN policies,
replace the broad HTTPS rule with the identity provider and Graph hosts.

**On-prem model in the cluster.** Add an egress rule to its namespace and port
and set `ZENTRAL_AI_BASE_URL` to its service URL.

**Upgrades.** A `Job`'s pod template is immutable. Delete the old Job before
applying a new version (`kubectl -n zentral delete job zentral-migrate`), or
run migrations as a Helm/Argo CD pre-sync hook. See the
[upgrade runbook](../runbooks/upgrade.md).

**Scaling.** `kubectl scale deploy/zentral-api --replicas=N`. The built-in rate
limiter counts per replica.

## Verify the hardening

```bash
kubectl -n zentral get pods -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[0].securityContext}{"\n"}{end}'
kubectl -n zentral exec deploy/zentral-api -- sh -c 'touch /x' ; echo "exit $?"   # must fail: read-only
kubectl -n zentral run probe --rm -it --image=busybox --restart=Never -- wget -T3 -qO- http://zentral-api/healthz
# ↑ must time out: the network policy blocks pods that are not the ingress
```

!!! note "Not yet verified on a live cluster"
    These manifests are validated for syntax and for the hardening properties
    above by an automated check, but have not been applied to a real cluster as
    part of this project's CI. Review them against your platform's conventions
    before first use.
