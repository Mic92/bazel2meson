# The repositories jaxlib is built from; a full package follows once the
# generated build compiles.
{
  callPackage,
  fetchFromGitHub,
}:

callPackage ../../nix/fetch-modules.nix { } {
  sources = ./sources.json;
  pname = "jaxlib";
  src = fetchFromGitHub {
    owner = "jax-ml";
    repo = "jax";
    tag = "jax-v0.12.0.1";
    hash = "sha256-um7ZO79sZCVFW0Ci4igjabBXildH698V1mCKpIBUVe8=";
  };
}
