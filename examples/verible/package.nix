# Verible's formatter built by Meson from a build description generated out
# of its Bazel files. Shows chained genrules around flex and bison.
{
  lib,
  stdenv,
  fetchFromGitHub,
  meson,
  ninja,
  pkg-config,
  bazel2meson,
  flex,
  bison,
  abseil-cpp,
  protobuf,
  re2,
  nlohmann_json,
  zlib,
}:

stdenv.mkDerivation (finalAttrs: {
  pname = "verible";
  version = "0-unstable-2026-11-12";

  src = fetchFromGitHub {
    owner = "chipsalliance";
    repo = "verible";
    rev = "085eef056c31aacd7e16545051f8047b69c2d45b";
    hash = "sha256-GJQAMsFFmdEKJG29mhXj0/yCzAFhWAIik0fe4CfQ7N0=";
  };

  postPatch = ''
    bazel2meson \
      --config ${./verible.toml} \
      --source . \
      --cpu ${stdenv.hostPlatform.parsed.cpu.name} \
      --version ${finalAttrs.version}
  '';

  nativeBuildInputs = [
    meson
    ninja
    pkg-config
    bazel2meson
    flex
    bison
  ];

  buildInputs = [
    abseil-cpp
    protobuf
    re2
    nlohmann_json
    zlib
    flex
  ];

  doInstallCheck = true;
  installCheckPhase = ''
    echo "module m; endmodule" > m.sv
    $out/bin/verible-verilog-format m.sv | grep -q endmodule
  '';

  meta = {
    description = "Suite of SystemVerilog developer tools, built with Meson";
    homepage = "https://github.com/chipsalliance/verible";
    license = lib.licenses.asl20;
    mainProgram = "verible-verilog-format";
    platforms = lib.platforms.linux;
  };
})
