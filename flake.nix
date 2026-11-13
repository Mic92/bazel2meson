{
  description = "Generate a Meson build from a project's Bazel build files";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  outputs =
    {
      self,
      nixpkgs,
    }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAllSystems (
        pkgs:
        let
          bazel2meson = pkgs.python3Packages.callPackage ./package.nix { };
        in
        {
          inherit bazel2meson;
          default = bazel2meson;
          envoy = pkgs.callPackage ./examples/envoy/package.nix { inherit bazel2meson; };
          jax-modules = pkgs.linkFarm "jax-modules" (
            import ./examples/jax/modules.nix { inherit (pkgs) callPackage fetchFromGitHub; }
          );
        }
      );

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          inputsFrom = [ self.packages.${pkgs.stdenv.hostPlatform.system}.envoy ];
          packages = [
            pkgs.ruff
            pkgs.ty
          ];
        };
      });

      formatter = forAllSystems (pkgs: pkgs.nixfmt-tree);
    };
}
