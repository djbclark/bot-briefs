{
  lib,
  python3Packages,
  writeShellScriptBin,
  symlinkJoin,
}:
let
  src = lib.cleanSourceWith {
    src = ../dashboard;
    filter =
      path: type:
      let
        base = baseNameOf path;
      in
      base != ".venv"
      && base != "data"
      && base != "__pycache__"
      && !(lib.hasSuffix ".pyc" base);
  };

  briefsPy = python3Packages.buildPythonPackage {
    pname = "briefs-dashboard";
    version = "0.1.0";
    pyproject = true;
    src = src;

    nativeBuildInputs = with python3Packages; [
      setuptools
      wheel
    ];

    propagatedBuildInputs = with python3Packages; [
      click
      fastapi
      jinja2
      markdown-it-py
      python-multipart
      starlette
      uvicorn
    ];

    doCheck = false;
    pythonImportsCheck = [ "briefs" "briefs.web" ];
  };

  pyEnv = python3Packages.python.withPackages (
    ps:
    with ps;
    [
      briefsPy
      uvicorn
      pytest
      httpx
      playwright
    ]
  );

  backupDb = writeShellScriptBin "briefs-backup-db" ''
    exec ${pyEnv}/bin/python ${src}/ops/vps/backup-db
  '';

  notifyBotVps = writeShellScriptBin "notify-bot-vps" ''
    exec ${pyEnv}/bin/python ${src}/ops/vps/notify-bot-vps "$@"
  '';

  briefsWeb = writeShellScriptBin "briefs-web" ''
    exec ${pyEnv}/bin/python -m uvicorn briefs.web:app \
      --host "''${BRIEFS_BIND_HOST:-127.0.0.1}" \
      --port "''${BRIEFS_PORT:-8642}" \
      --no-access-log "$@"
  '';

  packApp = writeShellScriptBin "briefs-pack-app" ''
    cd ${src}
    tar --exclude='__pycache__' --exclude='*.bak-*' --exclude='*.pyc' \
      -cf - briefs ops/vps requirements.txt pyproject.toml config.example.toml
  '';

  default = symlinkJoin {
    name = "briefs";
    paths = [
      backupDb
      notifyBotVps
      briefsWeb
      packApp
    ];
    meta.mainProgram = "briefs-web";
  };

in
{
  inherit briefsPy pyEnv backupDb notifyBotVps briefsWeb packApp default;
}
