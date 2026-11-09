{
  description = "Bot Briefs dashboard — reproducible package and NixOS module";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.11";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs {
          inherit system;
          overlays = [ self.overlays.default ];
        };
        briefs = import ./nix/package.nix {
          inherit (pkgs) lib python3Packages writeShellScriptBin linkFarm;
        };
      in
      {
        packages = {
          default = briefs.default;
          briefs = briefs.default;
          briefs-python = briefs.briefsPy;
          briefs-web = briefs.briefsWeb;
          inherit (briefs) backupDb notifyBotVps packApp;
        };

        checks = {
          briefs-import =
            pkgs.runCommand "briefs-import-check" {
              nativeBuildInputs = [ briefs.pyEnv ];
            } ''
              ${briefs.pyEnv}/bin/python -c "import briefs, briefs.web; print('ok')"
              touch $out
            '';

          briefs-config-fail-closed =
            pkgs.runCommand "briefs-config-fail-closed" {
              nativeBuildInputs = [ briefs.pyEnv ];
            } ''
              ${briefs.pyEnv}/bin/python <<'PY'
              import os
              os.environ.pop("BRIEFS_ALLOWED_LOGINS", None)
              os.environ.pop("BRIEFS_CONFIG_FILE", None)
              os.environ.pop("BRIEFS_ENV_FILE", None)
              import briefs.config as c
              assert c.ALLOWED_LOGINS == set(), c.ALLOWED_LOGINS
              PY
              touch $out
            '';

          briefs-tests =
            pkgs.runCommand "briefs-tests" {
              nativeBuildInputs = [ briefs.pyEnv ];
              src = pkgs.lib.cleanSource ./dashboard;
            } ''
              cp -r $src source
              cd source
              # Source tree must shadow the installed wheel (test fixture TOML bot inventory).
              export PYTHONPATH=$PWD
              export BRIEFS_CONFIG_FILE=$PWD/tests/fixtures/briefs-test.toml
              export BRIEFS_ALLOWED_LOGINS=user@example.com
              export BRIEFS_DB=/tmp/briefs-test.db
              export HOME=/tmp
              ${briefs.pyEnv}/bin/pytest tests -q --ignore=tests/test_browser_filters.py
              touch $out
            '';

          briefs-toml =
            pkgs.runCommand "briefs-toml-check" {
              nativeBuildInputs = [ briefs.pyEnv pkgs.coreutils ];
            } ''
              cfg=$(mktemp)
              cat > "$cfg" <<'EOF'
[auth]
allowed_logins = ["operator@example.test"]
allowed_hosts = ["localhost"]
[server]
timezone = "Europe/London"
[[bots]]
id = "00000000-0000-4000-8000-000000009999"
slug = "demo"
name = "Demo"
sort = 1
area_default = "general"
cli_name = "demo"
EOF
              export BRIEFS_CONFIG_FILE="$cfg"
              unset BRIEFS_ALLOWED_LOGINS
              ${briefs.pyEnv}/bin/python -c "
import briefs.config as c
assert 'operator@example.test' in c.ALLOWED_LOGINS
assert c.TZ_ET.key == 'Europe/London'
assert any(b.slug == 'demo' for b in c.BOTS)
"
              touch $out
            '';
        };

        devShells.default = pkgs.mkShell {
          packages = [
            briefs.pyEnv
            pkgs.ruff
          ];
          shellHook = ''
            export BRIEFS_ALLOWED_LOGINS=user@example.com
          '';
        };
      }
    )
    // {
      overlays.default = final: prev:
        let
          briefsPackages = import ./nix/package.nix {
            inherit (final) lib;
            inherit (final) python3Packages writeShellScriptBin linkFarm;
          };
        in
        {
          inherit (briefsPackages) briefsPy;
          briefs = briefsPackages.default;
        };

      nixosModules.briefs = import ./nix/module.nix;

      overlays.briefs = final: prev: {
        briefs = import ./nix/package.nix {
          inherit (final) lib;
          inherit (final) python3Packages writeShellScriptBin linkFarm;
        };
      };
    };
}
