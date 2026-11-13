# protoc-gen-js built by Meson from a build description generated out of its
# Bazel files. Shows a build tool of the tree run by a genrule.
{
  lib,
  stdenv,
  fetchFromGitHub,
  meson,
  ninja,
  pkg-config,
  bazel2meson,
  abseil-cpp,
  protobuf,
}:

stdenv.mkDerivation (finalAttrs: {
  pname = "protoc-gen-js";
  version = "4.0.3";

  src = fetchFromGitHub {
    owner = "protocolbuffers";
    repo = "protobuf-javascript";
    tag = "v${finalAttrs.version}";
    hash = "sha256-fcLtBXXAnK1iunrDfT9V8aX6AbaWgCeS0lcueuThM5s=";
  };

  postPatch = ''
    bazel2meson \
      --config ${./protoc-gen-js.toml} \
      --source . \
      --cpu ${stdenv.hostPlatform.parsed.cpu.name} \
      --version ${finalAttrs.version}
  '';

  nativeBuildInputs = [
    meson
    ninja
    pkg-config
    bazel2meson
  ];

  buildInputs = [
    abseil-cpp
    protobuf
  ];

  doInstallCheck = true;
  installCheckPhase = ''
    echo 'syntax = "proto3"; message M { int32 a = 1; }' > m.proto
    ${lib.getExe protobuf} --plugin=$out/bin/protoc-gen-js --js_out=. m.proto
    grep -q proto.M m.js
  '';

  meta = {
    description = "Protobuf plugin for generating JavaScript code, built with Meson";
    homepage = "https://github.com/protocolbuffers/protobuf-javascript";
    license = lib.licenses.bsd3;
    mainProgram = "protoc-gen-js";
    platforms = lib.platforms.linux;
  };
})
