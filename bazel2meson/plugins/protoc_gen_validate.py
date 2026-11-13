"""Validation code from protoc-gen-validate (https://github.com/bufbuild/protoc-gen-validate)."""

from __future__ import annotations

from ..meson import Emitter, ProtocPlugin


def register(em: Emitter) -> None:
    em.protoc_plugins.append(
        ProtocPlugin(
            name="validate",
            kinds={"pgv_cc_proto_library", "cc_proto_gen_validate"},
            suffixes=(".pb.validate.cc", ".pb.validate.h"),
            options="lang=cc",
        )
    )
