# Runbook — ship `playground.plnt.work` on DigitalOcean Kubernetes

Prod deploy of the plnt playground API. Target URL: `https://playground.plnt.work`.
The site's `/playground` page (at plnt.work) calls into this.

**Naming reminder:**
- `plnt.work` — static marketing site (Astro). Owns `/playground` UI page.
- `plnt.work/playground` — the chat UI island. Hits the API below.
- `playground.plnt.work` — **this API**. What we're shipping in this runbook.

The UI and the API are on different origins on purpose — the UI is a static
build hosted anywhere (Vercel, Netlify, Cloudflare Pages), the API is a
pod behind an ingress on the K8s cluster. CORS on the API allows the site's
origin (see `deploy/do-k8s/values-do.yaml`).

Total wall-clock: ~40 min if everything works first try, ~90 min with fumbles.
Monthly cost: **~$24** (1× s-1vcpu-2gb node $12 + 1× LB $12; DOCR free tier).

---

## 0. Prereqs (install once)

```bash
brew install doctl kubectl helm
# doctl auth: create a Personal Access Token at
#   https://cloud.digitalocean.com/account/api/tokens (read+write, no expiry
#   for convenience — or 90 days for hygiene)
doctl auth init                          # paste the token
doctl account get                        # sanity — should print your email
```

Cloudflare API token (only needed if you want DNS via API; the UI works too):
- https://dash.cloudflare.com/profile/api-tokens -> **Create Token**
- Template: **Edit zone DNS** -> scope to `plnt.work` zone -> create.
- Export: `export CF_API_TOKEN=...`

---

## 1. Container registry — push the image

DigitalOcean Container Registry (DOCR) free tier: 500MB / 1 repo. Enough
for us; the image is ~150MB.

```bash
# create registry (once; name must be globally unique — pick your own)
doctl registry create plnt --subscription-tier starter

# log docker into the registry
doctl registry login

# build for linux/amd64 (DOKS nodes are amd64; on Apple silicon this matters)
cd /Users/dev16/Documents/den-agent/plnt
docker buildx build \
  --platform linux/amd64 \
  -f docker/playground-api.Dockerfile \
  -t registry.digitalocean.com/plnt/playground-api:0.1.0 \
  --push \
  .

# verify
doctl registry repository list-v2
```

If `plnt` is taken as a registry name, pick another (e.g. `plnt-<yourname>`)
and update `image.repository` in `deploy/do-k8s/values-do.yaml` accordingly.

---

## 2. Cluster — create DOKS

```bash
# smallest usable cluster: 1× s-1vcpu-2gb node, $12/mo
# sfo3 (San Francisco) — closest to NVIDIA Santa Clara panel; swap for nyc1 / fra1 / blr1 as needed
doctl kubernetes cluster create plnt \
  --region sfo3 \
  --version latest \
  --node-pool "name=default;size=s-1vcpu-2gb;count=1;auto-scale=true;min-nodes=1;max-nodes=3" \
  --wait

# kubeconfig context is auto-added
kubectl config current-context           # should be do-sfo3-plnt
kubectl get nodes                        # 1 node, Ready
```

---

## 3. Registry pull secret in the cluster

DOCR needs a `dockerconfigjson` pull secret so the cluster can pull the image.

```bash
# creates the secret in every namespace you list (--namespace can be repeated)
kubectl create namespace plnt
doctl registry kubernetes-manifest --namespace plnt --name docr-plnt \
  | kubectl apply -f -

# verify
kubectl -n plnt get secret docr-plnt
```

---

## 4. Ingress controller — ingress-nginx

Installs a DO LoadBalancer ($12/mo) as a side effect. That LB's external IP
is what Cloudflare DNS will point at.

```bash
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update

helm install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace ingress-nginx --create-namespace \
  --set controller.publishService.enabled=true

# wait for the LB to get an external IP (1-3 min)
kubectl -n ingress-nginx get svc ingress-nginx-controller -w
# ^C when EXTERNAL-IP is not <pending>
```

Copy the `EXTERNAL-IP` — call it `$LB_IP`. You'll need it in step 6.

---

## 5. cert-manager — Let's Encrypt

```bash
helm repo add jetstack https://charts.jetstack.io
helm repo update

helm install cert-manager jetstack/cert-manager \
  --namespace cert-manager --create-namespace \
  --set crds.enabled=true

# wait for readiness
kubectl -n cert-manager rollout status deploy/cert-manager
kubectl -n cert-manager rollout status deploy/cert-manager-webhook

# edit deploy/do-k8s/cert-issuer.yaml — replace ACME_EMAIL@plnt.work with
# a real email you monitor, then apply
kubectl apply -f deploy/do-k8s/cert-issuer.yaml
kubectl get clusterissuer                # letsencrypt-prod, letsencrypt-staging
```

**Tip:** on the first deploy, flip `values-do.yaml`'s annotation to
`letsencrypt-staging` and confirm the cert flow end-to-end (browser will
show an untrusted cert, that's fine). Once green, switch to `letsencrypt-prod`
and re-apply.

---

## 6. DNS — Cloudflare record

Point `playground.plnt.work` at the LB. In Cloudflare dashboard:

- Zone: `plnt.work` -> **DNS** -> **Add record**
- Type: `A`
- Name: `playground`
- IPv4 address: `$LB_IP` (from step 4)
- Proxy status: **DNS only** (grey cloud — Cloudflare's orange-cloud proxy
  strips HTTP/1.1 upgrade for SSE in some tiers; keep it grey until you've
  verified SSE end-to-end, then flip to orange if you want their WAF)
- TTL: Auto

Verify:
```bash
dig +short playground.plnt.work                 # should return $LB_IP within a minute
```

The site owns the `plnt.work` apex + `www.plnt.work` records. Whatever host
it's on (Vercel / Netlify / Cloudflare Pages), those get their own DNS entries
— the site agent handles them.

---

## 7. Ship the playground API

```bash
cd /Users/dev16/Documents/den-agent/plnt

helm install plnt-playground plnt/charts/playground-api \
  --namespace plnt \
  -f deploy/do-k8s/values-do.yaml

kubectl -n plnt rollout status deploy/plnt-playground-playground-api
kubectl -n plnt get pods,svc,ingress
```

cert-manager will take 30-90s to provision the cert. Watch it:
```bash
kubectl -n plnt describe certificate playground-plnt-work-tls
kubectl -n plnt get certificate                          # READY should go True
```

---

## 8. Verify from the internet

```bash
# TLS handshake + model list
curl -s https://playground.plnt.work/v1/models | jq

# non-streaming
curl -s https://playground.plnt.work/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"plnt-mock-7b","messages":[{"role":"user","content":"hello prod"}]}' \
  | jq

# streaming
curl -sN https://playground.plnt.work/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"plnt-mock-7b","messages":[{"role":"user","content":"stream me"}],"stream":true}' \
  | head -20
```

Expected: JSON on the first two, `data: {...}\n\n ... data: [DONE]` frames on
the third, chunk-by-chunk (not batched — proves SSE is passing through).

---

## 9. Point the site's chat panel at the API

In the plnt-site repo, set `PUBLIC_PLNT_ENDPOINT=https://playground.plnt.work`
in the deployment env (Vercel/Netlify/wherever the site is hosted) and
redeploy. The playground island already falls back to a stub if the env
var isn't set — that's why the site is usable without this API being up.

---

## 10. Upgrade / rollback

```bash
# after code / values changes
docker buildx build --platform linux/amd64 \
  -f docker/playground-api.Dockerfile \
  -t registry.digitalocean.com/plnt/playground-api:0.1.1 \
  --push .

helm upgrade plnt-playground plnt/charts/playground-api \
  -n plnt -f deploy/do-k8s/values-do.yaml \
  --set image.tag=0.1.1

# rollback
helm history plnt-playground -n plnt
helm rollback plnt-playground <revision> -n plnt
```

---

## 11. Teardown (if you need to stop the meter)

```bash
helm uninstall plnt-playground -n plnt
helm uninstall ingress-nginx -n ingress-nginx        # frees the $12 LB
helm uninstall cert-manager -n cert-manager
doctl kubernetes cluster delete plnt                 # frees the $12 node
doctl registry delete plnt                           # frees registry (already free tier)
```

---

## Common failures

| Symptom                                                   | Fix                                                                                     |
|-----------------------------------------------------------|-----------------------------------------------------------------------------------------|
| `exec format error` in pod logs                           | Image built for arm64 on Apple silicon — rebuild with `--platform linux/amd64`.         |
| `ImagePullBackOff` for `registry.digitalocean.com/...`    | `docr-plnt` secret missing in `plnt` namespace. Re-run step 3.                          |
| `certificate` stuck `Ready: False`                        | DNS not resolving yet — Let's Encrypt HTTP-01 needs `playground.plnt.work` to reach the LB.    |
| SSE responses arrive all at once                          | Cloudflare orange-cloud proxy buffering. Flip to grey, or upgrade to a tier that streams. |
| `helm install` complains about the `imagePullSecrets` key | Old chart — `git pull` / rebuild. New template expects `.Values.imagePullSecrets`.      |
