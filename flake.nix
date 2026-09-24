{
  description = "Independent policies and external checks for Nix projects";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
    nixpkgs-unstable.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs =
    { self, nixpkgs, ... }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forSystems = nixpkgs.lib.genAttrs systems;
      forCheckerSystems = nixpkgs.lib.genAttrs (builtins.attrNames nixpkgs.legacyPackages);
      project = forCheckerSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          python = pkgs.python3.withPackages (packages: [ packages.pyyaml ]);
          formatter = pkgs.callPackage ./nix/formatter.nix { };
          checker = pkgs.callPackage ./nix/package.nix {
            python3 = python;
            checkerRevision = self.rev or "";
          };
          checks = {
            tests = mkCheck {
              name = "policy-tests";
              packages = [
                python
                pkgs.bash
                pkgs.git
                pkgs.jq
              ];
              script = ''
                python -m unittest discover -s tests -v
                python tools/policy.py validate
              '';
            };
            formatting = mkCheck {
              name = "policy-formatting";
              packages = [ formatter ];
              script = ''
                policy-fmt --ci
              '';
            };
            lint = mkCheck {
              name = "policy-lint";
              packages = [
                pkgs.statix
                pkgs.deadnix
                pkgs.ruff
                pkgs.actionlint
              ];
              script = ''
                statix check .
                deadnix --fail .
                ruff check tools tests
                actionlint .github/workflows/*.yml
              '';
            };
          };
          mkCheck =
            {
              name,
              packages,
              script,
            }:
            pkgs.runCommand name { nativeBuildInputs = packages; } ''
              cp -R ${sourceDir} source
              chmod -R u+w source
              cd source
              ${script}
              touch "$out"
            '';
          sourceDir = pkgs.lib.cleanSource ./.;
        in
        {
          inherit checker checks formatter;
          shell = pkgs.mkShellNoCC {
            packages = [
              pkgs.nix
              pkgs.nil
              pkgs.nixfmt
              pkgs.statix
              pkgs.deadnix
              pkgs.git
              pkgs.jq
              pkgs.shfmt
              pkgs.prettier
              pkgs.ruff
              pkgs.actionlint
              python
              formatter
              checker
            ];
          };
        }
      );
    in
    {
      devShells = forSystems (system: {
        default = project.${system}.shell;
      });
      formatter = forSystems (system: project.${system}.formatter);
      checks = forSystems (system: project.${system}.checks);
      packages = forCheckerSystems (system: {
        default = project.${system}.checker;
        policy-check = project.${system}.checker;
      });
      apps = forCheckerSystems (system: {
        default = {
          type = "app";
          program = "${project.${system}.checker}/bin/nixos-project-policy";
          meta.description = "Inspect project compliance and pin proposals";
        };
      });
    };
}
