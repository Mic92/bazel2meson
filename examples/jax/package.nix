# jaxlib built by Meson from a build description generated out of JAX's and
# XLA's Bazel files. Work in progress: CPU only, and only the _jax module.
{
  lib,
  llvmPackages_git,
  llvmPackages_latest,
  callPackage,
  fetchFromGitHub,
  meson,
  ninja,
  pkg-config,
  cmake,
  python3,
  bazel2meson,
  protobuf,
  grpc,
  abseil-cpp,
  boringssl,
  brotli,
  cpuinfo,
  curl,
  dlpack,
  eigen,
  farmhash,
  hwloc,
  jsoncpp,
  libhwy,
  oneDNN,
  re2,
  robin-map,
  snappy,
  sqlite,
  zlib,
  zstd,
}:

let
  version = "0.12.0.1";

  # XLA follows LLVM's main branch. The snapshot in nixpkgs is not in the
  # binary cache and one of its 81k tests fails (tools/gold/PowerPC/mtriple.ll).
  llvmPackages = llvmPackages_git.overrideScope (
    _final: prev: {
      libllvm = prev.libllvm.overrideAttrs { doCheck = false; };
    }
  );

  src = fetchFromGitHub {
    owner = "jax-ml";
    repo = "jax";
    tag = "jax-v${version}";
    hash = "sha256-um7ZO79sZCVFW0Ci4igjabBXildH698V1mCKpIBUVe8=";
  };

  # Repositories nixpkgs has no package for, as pinned by JAX and XLA.
  modules = callPackage ../../nix/fetch-modules.nix { } {
    sources = ./sources.json;
    pname = "jaxlib";
    inherit src;
  };

  # Clang compiles this kind of code about twice as fast as GCC. The LLVM and
  # MLIR libraries are a separate matter: XLA follows LLVM's main branch, so
  # those come from llvmPackages_git.
  inherit (llvmPackages_latest) stdenv;
in
stdenv.mkDerivation {
  pname = "jaxlib";
  inherit version src;

  postPatch = ''
    mkdir external
    ${lib.concatStrings (
      lib.mapAttrsToList (name: path: ''
        cp -r ${path} external/${name}
        chmod -R u+w external/${name}
      '') modules
    )}
    bazel2meson \
      --config ${./jax.toml} \
      --source . \
      --cpu ${stdenv.hostPlatform.parsed.cpu.name} \
      --version ${version} \
      ${lib.concatMapStringsSep " " (name: "--repo ${name}=external/${name}") (lib.attrNames modules)}
  '';

  nativeBuildInputs = [
    meson
    ninja
    pkg-config
    cmake
    python3
    bazel2meson
    protobuf
    grpc
    llvmPackages.tblgen
    llvmPackages.mlir
  ];
  dontUseCmakeConfigure = true;

  buildInputs = [
    llvmPackages.libllvm
    llvmPackages.mlir
    python3
    python3.pkgs.numpy
    protobuf
    grpc
    abseil-cpp
    boringssl
    brotli
    cpuinfo
    curl
    dlpack
    eigen
    farmhash
    hwloc
    jsoncpp
    libhwy
    oneDNN
    re2
    robin-map
    snappy
    sqlite
    zlib
    zstd
  ];

  # numpy keeps its pkg-config file inside the Python package.
  env.PKG_CONFIG_PATH = "${python3.pkgs.numpy}/${python3.sitePackages}/numpy/_core/lib/pkgconfig";

  passthru = { inherit modules; };

  meta = {
    description = "Prebuilt binaries for JAX, built with Meson";
    homepage = "https://github.com/jax-ml/jax";
    license = lib.licenses.asl20;
    platforms = [ "x86_64-linux" ];
  };
}
