# Caddy local verification checkpoint — 2026-10-29

The user approved workspace-local Caddy and official signature-verification tooling for synthetic loopback tests. No Caddy process or test listener was started. The Railway service, retained volume, physical devices, and real health data were not touched.

Downloads are in `mom-health/tools/caddy-2.11.7-verification/`:

- Caddy 2.11.7 Windows amd64 archive SHA-256 `0a1edc0b799512051c57071ce0e798d3f2cf67dc98171366f1d2b326072e3b06`, matching the publisher's release listing. Its certificate and detached signature also matched the release listing hashes.
- Cosign 3.1.4 SHA-256 `cbe953787074611f1334cbf37dc1cf953995bb8c45398eb4fc7bb781d29c1f2a`, matching the release listing. The already installed Sigstore JavaScript TUF client (npm's `@sigstore/tuf` 4.0.0) refreshed signed metadata into a workspace-local cache and fetched `artifact.pub`. Existing Git OpenSSL verified the Cosign 3.1.4 KMS signature against that key.
- Cosign 2.6.5 SHA-256 `19ec346d0050f59ffad9694da97ef9704be34ec60ddc41c5f1d16d8619385825`, matching the release listing. Git OpenSSL verified its artifact-key signature against the same TUF-authenticated key. This version was used for Caddy's separate legacy certificate and signature files; Cosign 3's CLI does not provide the same detached-signature options. No legacy JSON bundle was supplied.
- OpenSSL inspection showed the Caddy certificate SAN `https://github.com/caddyserver/caddy/.github/workflows/release.yml@refs/tags/v2.11.7`. Inspection alone is not certificate-chain or signature verification.

The identity- and issuer-constrained Cosign verification of the Caddy ZIP failed when querying `https://rekor.sigstore.dev/api/v1/log/entries/retrieve`: `remote error: tls: access denied` after its internal retries. The command exited 1. Therefore Caddy's publisher signature and transparency inclusion are **not verified**, and the archive has not been extracted or executed. A matching release hash is not a substitute for the promised signature check. Do not disable transparency verification or route around the refused request.

The pre-existing loopback harness remains ready for a later approved and successfully verified run. Its process and Caddy must each be started with an owned process handle and stopped only via those handles, with exit verified; see `CADDY_SYNTHETIC_PREPARATION.md`. Actual Caddy configuration parsing, timeout behavior, response backpressure, and shutdown have not been tested. The local experiment cannot resolve Railway edge identity or proxy trust.

Automatic approval review previously rejected public Railway domain creation because verified Railway edge identity and proxy-CIDR trust were missing. This checkpoint does not authorize retrying or bypassing that decision.

Sources: https://caddyserver.com/docs/signature-verification ; https://docs.sigstore.dev/cosign/system_config/installation/ ; https://github.com/caddyserver/caddy/releases/expanded_assets/v2.11.7 ; https://github.com/sigstore/cosign/releases/expanded_assets/v3.1.4 ; https://github.com/sigstore/cosign/releases/expanded_assets/v2.6.5 .
