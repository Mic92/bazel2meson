{
  lib,
  stdenv,
  stdenvNoCC,
  fetchurl,
  unzip,
  meson,
  ninja,
  pkg-config,
  cmake,
  python3,
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
  libevent,
  libmaxminddb,
  liburing,
  luajit,
  msgpack-cxx,
  nghttp2,
  nlohmann_json,
  opentelemetry-cpp,
  re2,
  spdlog,
  tclap,
  xxhash,
  yaml-cpp,
  zlib-ng,
  zstd,

  src,
  version,
}:

let
  # Bazel modules nixpkgs has no package for, as pinned by Envoy's lockfile
  # and prepared the way Bazel would: unpack, patch, add the registry's
  # BUILD files.
  fetchModule =
    name: spec:
    stdenvNoCC.mkDerivation {
      pname = "envoy-module-${name}";
      inherit (spec) version;
      src = fetchurl { inherit (spec) url hash; };
      nativeBuildInputs = [ unzip ];
      unpackPhase = ''
        runHook preUnpack
        mkdir unpacked
        cd unpacked
        unpackFile "$src"
        cd ${lib.escapeShellArg "./${spec.stripPrefix}"}
        runHook postUnpack
      '';
      patches = map (p: fetchurl { inherit (p) url hash; }) spec.patches;
      patchFlags = [ "-p${toString spec.patchStrip}" ];
      postPatch = ''
        install -Dm644 ${fetchurl { inherit (spec.moduleFile) url sha256; }} MODULE.bazel
      ''
      + lib.concatMapStrings (o: ''
        install -Dm644 ${fetchurl { inherit (o) url hash; }} ${lib.escapeShellArg o.path}
      '') spec.overlay;
      dontConfigure = true;
      dontBuild = true;
      dontFixup = true;
      installPhase = ''
        cp -r . $out
      '';
    };

  modules = lib.mapAttrs fetchModule (lib.importJSON ./sources.json);
in
stdenv.mkDerivation {
  pname = "envoy-meson";
  inherit src version;

  nativeBuildInputs = [
    meson
    ninja
    pkg-config
    cmake
    python3
    protobuf
    protoc-gen-validate
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
    libevent
    libmaxminddb
    liburing
    luajit
    msgpack-cxx
    nghttp2
    nlohmann_json
    opentelemetry-cpp
    protobuf
    re2
    spdlog
    tclap
    xxhash
    yaml-cpp
    zlib-ng
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
    chmod -R u+w external

    PYTHONPATH=${./.} python3 -m bazel2meson \
      --config ${./envoy.toml} \
      --source . \
      --cpu ${stdenv.hostPlatform.parsed.cpu.name} \
      --version ${version} \
      ${lib.concatMapStringsSep " " (name: "--repo ${name}=external/${name}") (lib.attrNames modules)}
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
