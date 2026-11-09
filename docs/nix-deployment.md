# Nix packaging and NixOS deployment

Reproducible packaging for the deployed Bot Briefs dashboard lives at the repository root (`flake.nix`, `nix/`). Personal deployment values belong in **host** TOML and secret files — never in this public repository or the Nix store.

## Build and test locally

```bash
nix flake check
nix build .#briefs
```

Packages:

| Output | Purpose |
|--------|---------|
| `.#briefs` | CLI wrappers, Python env, maintenance scripts |
| `.#briefs-web` | Uvicorn entrypoint |
| `.#briefs-python` | Python package only |

Maintenance commands (on PATH via the `briefs` package):

- `briefs-web` — loopback HTTP server (`BRIEFS_BIND_HOST` defaults to `127.0.0.1`)
- `briefs-backup-db` — SQLite online backup
- `notify-bot-vps` — webhook notify / optional `--retry` (off in NixOS by default)
- `briefs-pack-app` — tar stream of app sources (legacy VPS sync)

## Runtime configuration

1. Copy `dashboard/config.example.toml` to a path on the host (e.g. `/var/lib/briefs/config/briefs.toml`).
2. Set non-secret values: `allowed_logins`, `allowed_hosts`, `public_url`, `[[bots]]`, timezone.
3. Set `BRIEFS_CONFIG_FILE` to that path (the NixOS module does this automatically).
4. Keep secrets in existing host files and point to them with environment/module options only:
   - `BRIEFS_SYNC_TOKEN_FILE` → `/var/lib/briefs/secrets/sync-token`
   - `BRIEFS_WEBHOOK_ENV` → `/var/lib/briefs/secrets/webhook.env`

`BRIEFS_ALLOWED_LOGINS` unset → **fail closed** for Tailscale-identified requests.

## NixOS module

```nix
{
  inputs.bot-briefs.url = "github:djbclark/bot-briefs";
  # …
  nixpkgs.overlays = [ inputs.bot-briefs.overlays.default ];
  imports = [ inputs.bot-briefs.nixosModules.briefs ];
}
```

### Minimal `vps-hub` integration (`site-djbclark`)

```nix
# nixos/vps-hub/configuration.nix (fragment — values from private site-private repo)
{
  imports = [
    inputs.bot-briefs.nixosModules.briefs
  ];

  nixpkgs.overlays = [
    inputs.bot-briefs.overlays.default
  ];

  services.briefs = {
    enable = true;
    package = pkgs.briefs;
    publicUrl = "https://vps-hub.example-tailnet.ts.net"; # from private config
    allowedHosts = [
      "localhost"
      "127.0.0.1"
      "::1"
      "vps-hub.example-tailnet.ts.net"
    ];
    allowedLogins = [ "operator@example.test" ]; # from private config
    syncTokenFile = "/var/lib/briefs/secrets/sync-token";
    webhookEnvFile = "/var/lib/briefs/secrets/webhook.env";
    backup.enable = true;
    wakeRetry.enable = false; # keep false until operator approves replay
    # bots / botIds — copy from private TOML, not the public repo
  };

  # Tailscale Serve (HTTPS → loopback) stays in your existing tailscale config;
  # this module only binds 127.0.0.1:8642.
}
```

### Privilege model

Default `services.briefs.user = "root"` matches the transitional VPS layout: SQLite `600`, secrets directory `700`. Moving to an unprivileged user requires a coordinated `chown` of `/var/lib/briefs` — do not do that in the same change as the first Nix module deploy.

## Safe hub deployment (coordinator)

**Do not run on `vps-primary`.** On `vps-hub` only, after merging the PR and pinning the flake input:

```bash
# 1. Stop transitional root-user units (avoid duplicate listeners on :8642)
sudo systemctl --user stop briefs-web.service
sudo systemctl --user disable briefs-web.service
sudo systemctl --user stop briefs-backup.timer
sudo systemctl --user disable briefs-backup.timer
# Leave briefs-wake-retry.timer disabled (should already be off).

# 2. Rebuild from site-djbclark vps-hub branch with module enabled
cd /path/to/site-djbclark
sudo nixos-rebuild switch --flake .#vps-hub

# 3. Verify (no response bodies in shared logs)
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8642/healthz
systemctl status briefs-web.service
systemctl list-timers briefs-backup.timer
```

No webhooks or test notifications are required for validation.

## Branch / merge note

User-facing goal is eventual merge to public `main`; this implementation is delivered as a **branch and PR** for review (no merge without operator approval).
