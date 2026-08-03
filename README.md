# slipmesh-network

Helm chart for the `slipmesh.net` operators (`mesh`, `router`, `nftables`, `roadwarriors`):
a full-mesh AmneziaWG overlay between Talos nodes, BIRD OSPF/iBGP routing over that overlay,
NAT/masquerade firewalling, and road-warrior (client) VPN termination.

There is no separate "cni" component: `router` writes `/etc/cni/net.d/10-slipmesh.conflist`
itself from each node's PodCIDR at startup, using the plain `bridge`/`host-local`/`loopback`
CNI plugins already shipped in `/opt/cni/bin` on Talos v1.8+. No Calico/Flannel involved.

## Prerequisites

- A Talos v1.8+ Kubernetes cluster, nodes reachable at real public/routable addresses.
- Helm 3+ (server-side apply support recommended; tested against Helm 4).
- Container images (`mesh`, `router`, `nftables`, `roadwarriors`) already published at the
  repository referenced in `values.yaml`'s `image` block.

## Structure

- `values.yaml` - tracked chart defaults: image refs, resource limits, log levels, and the
  generic/mechanical structure of every value (empty placeholders for anything
  deployment-specific - see below).
- `.values.yaml` - **not committed** (see `.gitignore`): the real, environment-specific facts
  for one particular deployment - node IPs, pool CIDRs, per-link obfuscation keys, bypass ASN
  lists, road-warrior client identities. Always merge it on top:

  ```sh
  helm upgrade slipmesh . -n slipmesh -f .values.yaml
  ```

- `crds/` - CRDs generated directly from the operators' own `*-crdgen` binaries
  (`~/workspace/svaroglab/slipmesh/operators`), not hand-written. Helm only installs `crds/`
  on `helm install`, never updates/deletes them on `upgrade`/`uninstall` - if the operators'
  CRD schema changes, re-run `*-crdgen` and `kubectl apply -f crds/` by hand.

## Quick start

Fill in `.values.yaml` (see template below), then install/upgrade:

```sh
helm install slipmesh . -n slipmesh --create-namespace -f .values.yaml
helm upgrade slipmesh . -n slipmesh -f .values.yaml
```

No secrets to create by hand: both `mesh` (per-node keys) and `roadwarriors` (one shared key)
generate and persist their own AmneziaWG private keys on first boot, via a `Secret` each pod
gets `get`/`create` access to (`mesh-keys`, `roadwarriors-key`).

## `.values.yaml` template

Create this file next to `values.yaml` (it's gitignored) with your real values. Anything
omitted here falls back to `values.yaml`'s empty placeholder, which will fail CRD validation
(`format: cidr`, etc.) at apply time - that's intentional, so a missing local value fails
loudly instead of silently deploying nothing.

```yaml
# One entry per cluster node. `name` MUST equal the Kubernetes Node object name
# (kubectl get nodes). meshLabel/routerLabel: short (<=10 char) identifiers, used to build
# mesh-<label> interface names and BIRD protocol names - conventionally same as `name`.
# `endpoint`: the node's real public IP/hostname, for inbound WireGuard.
nodes:
  - name: node-a
    meshLabel: node-a
    routerLabel: node-a
    endpoint: 203.0.113.1
  - name: node-b
    meshLabel: node-b
    routerLabel: node-b
    endpoint: 203.0.113.2
  - name: node-c
    meshLabel: node-c
    routerLabel: node-c
    endpoint: 203.0.113.3

mesh:
  pool:
    name: default
    # A /24 (or larger) private range, not colliding with pod/service CIDRs or router.pool
    # below. MeshLinks draw /31s from it, one per full-mesh pair.
    network: 10.99.255.0/24
    # First UDP port handed out; each additional link gets the next one. Pick something that
    # won't collide with roadwarriors.listenPort on any node that runs both.
    basePort: 52800

  # Per-link AmneziaWG obfuscation magic numbers, keyed by "<meshLabelA>-<meshLabelB>" in the
  # same order the two nodes appear in `nodes` above (e.g. "node-a-node-b", not
  # "node-b-node-a"). Generate distinct random uint32s per link once, then keep them stable -
  # changing them flaps that link's handshake. Every unordered pair needs an entry, or that
  # link runs plain (unobfuscated) WireGuard.
  linkObfuscation:
    node-a-node-b: { h1: 1111111111, h2: 1111111112, h3: 1111111113, h4: 1111111114 }
    node-a-node-c: { h1: 2222222221, h2: 2222222222, h3: 2222222223, h4: 2222222224 }
    node-b-node-c: { h1: 3333333331, h2: 3333333332, h3: 3333333333, h4: 3333333334 }

router:
  pool:
    name: default
    # RouterNode OSPF/iBGP loopbacks draw from here - must not overlap mesh.pool.network.
    network: 10.99.0.0/24

  # Optional: per-node VPN egress bypass lists (ASN/prefix sets routed directly instead of
  # through the tunnel). Omit entirely (bypassSources: []) if not needed yet.
  bypassSources:
    - node: node-a
      include:
        - kind: asn
          label: some-provider
          asns: ["AS64500"]
        - kind: literal
          prefixes:
            - net: 198.51.100.0/24
              label: "some transit block"

roadwarriors:
  # Which node(s) terminate road-warrior client connections (kubernetes.io/hostname values,
  # matching `nodes[].name` above). Leave empty ([]) to disable roadwarriors entirely.
  nodeHostnames: ["node-a"]
  # Server-side tunnel address/port clients connect to - keep stable once real clients exist,
  # changing it means reconfiguring every client device.
  address: 192.0.2.1/24
  listenPort: 51820

  # One entry per client device. `name` must be a valid Kubernetes object name (lowercase,
  # digits, '-'; no underscores/spaces). `publicKey` is the client's own AmneziaWG public key
  # (never its private key - that never leaves the client).
  clients:
    - name: someones-phone
      publicKey: "BASE64_CLIENT_PUBLIC_KEY_HERE="
      allowedIps: ["192.0.2.10/32"]
```

## `bypassSources` entry kinds

Each `include`/`exclude` entry under `router.bypassSources[].include`/`.exclude` has a `kind`,
resolved hourly plus on spec change:

| `kind` | required fields | resolves via |
| --- | --- | --- |
| `asn` | `label`, `asns: ["AS...", ...]` | RIPEstat announced-prefixes per ASN, in parallel |
| `literal` | `prefixes: [{net, label?}]` | verbatim, no network call |
| `dns` | `label`, `hostnames: [...]` | live A-record lookup -> one `/32` per resolved address (a literal IP string also works - it's tried as an address before falling back to DNS) |
| `geoip` | `country` (label optional, defaults to `"geoip <country>"`) | RIPEstat country-resource-list -> every IPv4 prefix allocated to that country |

`exclude` uses the same four kinds, symmetric with `include` - e.g. `{kind: geoip, country: RU}`
to carve one country back out of a broader ASN-based `include` list. Every `MeshNode`'s own
endpoint is auto-excluded regardless, so a bypass can never accidentally blackhole the cluster
itself - no need to add that by hand.
