{
  description = "Meson build for Envoy, generated from its Bazel build";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  inputs.envoy = {
    url = "github:envoyproxy/envoy/v1.40.2";
    flake = false;
  };

  outputs =
    {
      self,
      nixpkgs,
      envoy,
    }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAllSystems (pkgs: rec {
        default = envoy-meson;
        envoy-meson = pkgs.callPackage ./package.nix {
          src = envoy;
          version = nixpkgs.lib.trim (builtins.readFile "${envoy}/VERSION.txt");
        };
      });

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          inputsFrom = [ self.packages.${pkgs.stdenv.hostPlatform.system}.envoy-meson ];
          packages = [
            pkgs.ruff
            pkgs.ty
          ];
        };
      });

      formatter = forAllSystems (pkgs: pkgs.nixfmt-tree);
    };
}
