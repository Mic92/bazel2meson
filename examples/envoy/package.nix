{
  lib,
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
  protoc-gen-validate,
  abseil-cpp,
  antlr4,
  boringssl,
  brotli,
  c-ares,
  flatbuffers,
  fmt,
  grpc,
  libmaxminddb,
  liburing,
  luajit,
  msgpack-cxx,
  nlohmann_json,
  opentelemetry-cpp,
  re2,
  spdlog,
  tclap,
  xxhash,
  yaml-cpp,
  zlib,
  zstd,
}:

let
  version = "1.40.2";
  # The commit of the release tag; the binary reports it.
  revision = "b2d89e0ae53916f28770771fbdd49c5c99c5c5a2";

  # Bazel modules nixpkgs has no package for, as pinned by Envoy's lockfile.
  modules = callPackage ../../nix/fetch-modules.nix { } {
    sources = ./sources.json;
    pname = "envoy";
    inherit src;
  };

  src = fetchFromGitHub {
    owner = "envoyproxy";
    repo = "envoy";
    tag = "v${version}";
    hash = "sha256-Ni+vX3rqo8s7000IPjzg2B7smtFm3SQF501tdRZAv4M=";
  };

  # Upstream develops with Clang, and it compiles this code about twice as
  # fast as GCC. On Linux this is Clang with libstdc++, matching the libraries.
  inherit (llvmPackages_latest) stdenv;
in
stdenv.mkDerivation {
  pname = "envoy";
  inherit version src;

  nativeBuildInputs = [
    meson
    ninja
    pkg-config
    cmake
    python3
    bazel2meson
    protobuf
    protoc-gen-validate
    antlr4
    grpc
  ];

  buildInputs = [
    abseil-cpp
    antlr4.runtime.cpp
    boringssl
    brotli
    c-ares
    flatbuffers
    fmt
    grpc
    libmaxminddb
    liburing
    luajit
    msgpack-cxx
    nlohmann_json
    opentelemetry-cpp
    protobuf
    re2
    spdlog
    tclap
    xxhash
    yaml-cpp
    zlib
    zstd
  ];

  dontUseCmakeConfigure = true;

  postPatch = ''
    mkdir external
    ${lib.concatStrings (
      lib.mapAttrsToList (name: module: ''
        cp -r ${module} external/${name}
      '') modules
    )}
    # Only the .proto files; the library itself comes from nixpkgs.
    mkdir -p external/grpc/src
    cp -r ${grpc.src}/src/proto external/grpc/src/proto
    chmod -R u+w external

    bazel2meson \
      --config ${./envoy.toml} \
      --source . \
      --cpu ${stdenv.hostPlatform.parsed.cpu.name} \
      --version ${version} \
      --revision ${revision} \
      ${lib.concatMapStringsSep " " (name: "--repo ${name}=external/${name}") (lib.attrNames modules)}
  '';

  # Bazel names the binary after its target.
  postInstall = ''
    mv $out/bin/envoy-static $out/bin/envoy
  '';

  passthru = { inherit modules; };

  meta = {
    description = "Cloud-native edge and service proxy, built with Meson";
    homepage = "https://envoyproxy.io";
    license = lib.licenses.asl20;
    mainProgram = "envoy";
    platforms = [
      "x86_64-linux"
      "aarch64-linux"
    ];
  };
}
