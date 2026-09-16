{
  writeShellApplication,
  python3,
  git,
  nix,
}:
writeShellApplication {
  name = "nixos-project-policy";
  runtimeInputs = [
    git
    nix
  ];
  text = ''
    exec ${python3}/bin/python ${../tools/policy.py} --policy-root ${../.} "$@"
  '';
}
