"""What XLA's CUDA configuration generates for a build without CUDA.

The real file is the output of a repository rule, which bazel2meson does not
run. It has the functions XLA's build files load.
"""

def if_cuda(if_true, if_false = []):
    return if_false

def if_cuda_is_configured(x, no_cuda = []):
    return no_cuda

def is_cuda_configured():
    return False

def cuda_default_copts():
    return []

def cuda_library(copts = [], tags = [], deps = [], **kwargs):
    native.cc_library(copts = copts, tags = tags, deps = deps, **kwargs)

def if_cuda_clang(if_true, if_false = []):
    return if_false

def if_cuda_exec(if_true, if_false = []):
    return if_false

def if_cuda_clang_opt(if_true, if_false = []):
    return if_false

def cuda_compiler(if_cuda_clang, if_nvcc, neither = []):
    return neither

def cuda_gpu_architectures():
    return []

def if_cuda_newer_than(wanted_ver, if_true, if_false = []):
    return if_false

def if_version_equal_or_greater_than(lib_version, wanted_ver, if_true, if_false = []):
    return if_false

def cuda_header_library(name, hdrs, include_prefix = None, strip_include_prefix = None, deps = [], **kwargs):
    native.cc_library(name = name, hdrs = hdrs, deps = deps, **kwargs)

def cuda_cc_test(copts = [], **kwargs):
    native.cc_test(copts = copts, **kwargs)
