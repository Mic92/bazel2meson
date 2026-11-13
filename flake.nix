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
          bant = pkgs.callPackage ./examples/bant/package.nix { inherit bazel2meson; };
          jaxlib = pkgs.callPackage ./examples/jax/package.nix { inherit bazel2meson; };
        }
      );

      devShells = forAllSystems (pkgs: {
        # The compiler of the Envoy example, to build its generated tree by hand.
        default =
          (pkgs.mkShell.override { inherit (self.packages.${pkgs.stdenv.hostPlatform.system}.envoy) stdenv; })
            {
              inputsFrom = [ self.packages.${pkgs.stdenv.hostPlatform.system}.envoy ];
              packages = [
                pkgs.ruff
                pkgs.ty
              ];
            };
      });

      formatter = forAllSystems (
        pkgs:
        pkgs.treefmt.withConfig {
          runtimeInputs = [
            pkgs.nixfmt
            pkgs.ruff
          ];
          settings = {
            on-unmatched = "info";
            tree-root-file = "flake.nix";
            formatter.nixfmt = {
              command = "nixfmt";
              includes = [ "*.nix" ];
            };
            formatter.ruff-check = {
              command = "ruff";
              options = [
                "check"
                "--fix"
              ];
              includes = [ "*.py" ];
              priority = 1;
            };
            formatter.ruff-format = {
              command = "ruff";
              options = [ "format" ];
              includes = [ "*.py" ];
              priority = 2;
            };
          };
        }
      );
    };
}
