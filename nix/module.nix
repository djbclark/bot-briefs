{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.briefs;

  # When configFile is set, only that path is used (runtime private TOML outside the store).
  # Generated store config is omitted so module option defaults cannot clobber allowlist/bots in TOML.
  generatedToml = lib.generators.toTOML (
    {
      server =
        {
          port = cfg.port;
          public_url = cfg.publicUrl;
          timezone = cfg.timezone;
        }
        // lib.optionalAttrs (cfg.baseUrl != null) { base_url = cfg.baseUrl; };

      auth =
        {
          allowed_hosts = cfg.allowedHosts;
        }
        // lib.optionalAttrs (cfg.allowedLogins != [ ]) {
          allowed_logins = cfg.allowedLogins;
        }
        // lib.optionalAttrs (cfg.botIds != { }) {
          bot_ids = cfg.botIds;
        };

      paths = {
        db = "${cfg.stateDir}/data/briefs.db";
      };
    }
    // lib.optionalAttrs (cfg.bots != [ ]) {
      bots =
        map (b: {
          id = b.id;
          slug = b.slug;
          name = b.name;
          sort = b.sort;
          area_default = b.area_default;
          cli_name = b.cli_name;
        })
        cfg.bots;
    }
  );

  configFile =
    if cfg.configFile != null then
      cfg.configFile
    else
      pkgs.writeText "briefs-generated.toml" generatedToml;

  pkg = cfg.package;

  backupScript = "${pkg}/bin/briefs-backup-db";
  webBin = "${pkg}/bin/briefs-web";
  notifyBin = "${pkg}/bin/notify-bot-vps";

  briefsEnv = {
    BRIEFS_CONFIG_FILE = configFile;
    BRIEFS_VPS_HOME = cfg.stateDir;
    BRIEFS_DB = "${cfg.stateDir}/data/briefs.db";
    BRIEFS_LOGS_DIR = "${cfg.stateDir}/logs";
    BRIEFS_BACKUP_DIR = "${cfg.stateDir}/backups";
    BRIEFS_BIND_HOST = cfg.bindHost;
    BRIEFS_PORT = toString cfg.port;
    BRIEFS_NOTIFY_CMD = notifyBin;
  }
  // lib.optionalAttrs (cfg.syncTokenFile != null) {
    BRIEFS_SYNC_TOKEN_FILE = cfg.syncTokenFile;
  }
  // lib.optionalAttrs (cfg.webhookEnvFile != null) {
    BRIEFS_WEBHOOK_ENV = cfg.webhookEnvFile;
  };

in
{
  options.services.briefs = {
    enable = lib.mkEnableOption "Bot Briefs dashboard (loopback FastAPI + optional VPS mirror)";

    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.briefs;
      defaultText = lib.literalExpression "pkgs.briefs";
      description = "Briefs application package (requires the bot-briefs flake overlay or an explicit flake package).";
    };

    stateDir = lib.mkOption {
      type = lib.types.str;
      default = "/var/lib/briefs";
      description = "Persistent state (database, logs, backups, wake spool). Not stored in /nix/store.";
    };

    configFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      description = ''
        Runtime TOML on the host (e.g. `/var/lib/briefs/config/briefs.toml`), outside the Nix store.
        When set, {option}`services.briefs.allowedLogins`, {option}`services.briefs.bots`, and related
        auth/inventory options are **not** used — only this file defines allowlist, bots, and URLs.
        When null, a minimal generated TOML is used (empty bots list is omitted so packaged defaults apply).
        Never put secrets in TOML; reference secret paths via
        {option}`services.briefs.syncTokenFile` and {option}`services.briefs.webhookEnvFile`.
      '';
    };

    bindHost = lib.mkOption {
      type = lib.types.str;
      default = "127.0.0.1";
      description = "HTTP bind address (loopback only; expose via Tailscale Serve, not Funnel).";
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 8642;
    };

    publicUrl = lib.mkOption {
      type = lib.types.str;
      default = "https://briefs.example-tailnet.ts.net";
      description = "HTTPS URL operators use (item links for bots).";
    };

    baseUrl = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
    };

    timezone = lib.mkOption {
      type = lib.types.str;
      default = "America/New_York";
    };

    allowedLogins = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = ''
        Tailscale user logins allowed to use the UI. Required for production: the app fails closed
        when this list is empty and a request presents a Tailscale identity.
      '';
    };

    allowedHosts = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [
        "localhost"
        "127.0.0.1"
        "::1"
        "briefs.example-tailnet.ts.net"
      ];
      description = "Trusted Host headers (DNS rebinding guard).";
    };

    botIds = lib.mkOption {
      type = lib.types.attrsOf lib.types.str;
      default = { };
      description = "Optional slug → agent UUID overrides (from private config, not the public repo).";
    };

    bots = lib.mkOption {
      type = lib.types.listOf (
        lib.types.submodule {
          options = {
            id = lib.mkOption { type = lib.types.str; };
            slug = lib.mkOption { type = lib.types.str; };
            name = lib.mkOption { type = lib.types.str; };
            sort = lib.mkOption { type = lib.types.int; };
            area_default = lib.mkOption { type = lib.types.str; default = "general"; };
            cli_name = lib.mkOption { type = lib.types.str; };
          };
        }
      );
      default = [ ];
      description = "Bot inventory; when empty, the app uses packaged neutral example bots.";
    };

    user = lib.mkOption {
      type = lib.types.str;
      default = "root";
      description = ''
        Service user. Default root matches the transitional VPS deployment (SQLite and secrets at 600/700).
        A dedicated user is possible only after migrating ownership of {option}`services.briefs.stateDir`.
      '';
    };

    syncTokenFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Path to mirror sync bearer token file (mode 600). Enables /sync API on the VPS copy.";
    };

    webhookEnvFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Path to webhook env file for notify-bot-vps (never in the Nix store).";
    };

    backup = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = "SQLite online backup timer (07:30 UTC).";
      };
      keep = lib.mkOption {
        type = lib.types.int;
        default = 7;
      };
      onCalendar = lib.mkOption {
        type = lib.types.str;
        default = "*-*-* 07:30:00 UTC";
      };
    };

    wakeRetry = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = ''
          Resend failed Chief-of-Staff wake-ups from wake-spool. Left disabled by default so copied
          spool files cannot replay old notifications without explicit operator approval.
        '';
      };
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.services.briefs-web = {
      description = "Bot Briefs dashboard";
      after = [ "network-online.target" ];
      wantedBy = [ "multi-user.target" ];
      path = [ pkg ];
      serviceConfig = {
        Type = "simple";
        User = cfg.user;
        UMask = "0077";
        WorkingDirectory = cfg.stateDir;
        Environment = lib.mapAttrsToList (k: v: "${k}=${v}") briefsEnv;
        ExecStart = webBin;
        Restart = "always";
        RestartSec = 3;
      };
    };

    systemd.services.briefs-backup = lib.mkIf cfg.backup.enable {
      description = "Bot Briefs SQLite backup";
      serviceConfig = {
        Type = "oneshot";
        User = cfg.user;
        UMask = "0077";
        Environment =
          (lib.mapAttrsToList (k: v: "${k}=${v}") briefsEnv)
          ++ [
            "BRIEFS_BACKUP_KEEP=${toString cfg.backup.keep}"
          ];
        ExecStart = backupScript;
      };
    };

    systemd.timers.briefs-backup = lib.mkIf cfg.backup.enable {
      description = "Nightly Bot Briefs DB backup";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = cfg.backup.onCalendar;
        Persistent = true;
      };
    };

    systemd.services.briefs-wake-retry = lib.mkIf cfg.wakeRetry.enable {
      description = "Resend failed Bot Briefs Chief-of-Staff wake-ups";
      serviceConfig = {
        Type = "oneshot";
        User = cfg.user;
        UMask = "0077";
        Environment = lib.mapAttrsToList (k: v: "${k}=${v}") briefsEnv;
        ExecStart = "${notifyBin} chief-of-staff --retry";
      };
    };

    systemd.timers.briefs-wake-retry = lib.mkIf cfg.wakeRetry.enable {
      description = "Retry failed Bot Briefs wake-ups every minute";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnBootSec = "2min";
        OnUnitActiveSec = 60;
      };
    };

    # State directories (secrets and DB are expected pre-provisioned on VPS migration).
    systemd.tmpfiles.rules = [
      "d ${cfg.stateDir} 0700 ${cfg.user} ${cfg.user} -"
      "d ${cfg.stateDir}/data 0700 ${cfg.user} ${cfg.user} -"
      "d ${cfg.stateDir}/backups 0700 ${cfg.user} ${cfg.user} -"
      "d ${cfg.stateDir}/logs 0700 ${cfg.user} ${cfg.user} -"
      "d ${cfg.stateDir}/wake-spool 0700 ${cfg.user} ${cfg.user} -"
      "d ${cfg.stateDir}/config 0700 ${cfg.user} ${cfg.user} -"
    ];
  };
}
