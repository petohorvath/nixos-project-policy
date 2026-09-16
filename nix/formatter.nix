{
  writeShellApplication,
  treefmt,
  nixfmt,
  shfmt,
  prettier,
  ruff,
}:
writeShellApplication {
  name = "policy-fmt";
  runtimeInputs = [
    treefmt
    nixfmt
    shfmt
    prettier
    ruff
  ];
  text = ''
    exec treefmt --tree-root . --walk filesystem --config-file ${../treefmt.toml} "$@"
  '';
}
