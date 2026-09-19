{
  writeShellApplication,
  python3,
  git,
  nix,
  checkerRevision,
}:
writeShellApplication {
  name = "nixos-project-policy";
  runtimeInputs = [
    git
    nix
  ];
  text = ''
    exec ${python3}/bin/python -c 'import runpy; runpy.run_path("${../.}/tools/policy.py", run_name="__main__", init_globals={"PACKAGED_REVISION": "${checkerRevision}"})' "$@"
  '';
}
