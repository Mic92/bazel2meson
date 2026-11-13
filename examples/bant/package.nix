# bant built by Meson from a build description generated out of its Bazel
# files. The smallest example: every dependency is an installed library.
{
  lib,
  stdenv,
  fetchFromGitHub,
  meson,
  ninja,
  pkg-config,
  bazel2meson,
  abseil-cpp,
  re2,
}:

stdenv.mkDerivation (finalAttrs: {
  pname = "bant";
  version = "0.3.7";

  src = fetchFromGitHub {
    owner = "hzeller";
    repo = "bant";
    tag = "v${finalAttrs.version}";
    hash = "sha256-AYjf4a5fKPEIQzFWGTDF2MGkVwZ5o7AP7I43whgcaMs=";
  };

  postPatch = ''
    bazel2meson \
      --config ${./bant.toml} \
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
    re2
  ];

  doInstallCheck = true;
  installCheckPhase = ''
    $out/bin/bant -V 2>&1 | grep -F ${finalAttrs.version}
  '';

  meta = {
    description = "Bazel build file navigator, built with Meson";
    homepage = "https://github.com/hzeller/bant";
    license = lib.licenses.gpl3Only;
    mainProgram = "bant";
    platforms = lib.platforms.linux;
  };
})
