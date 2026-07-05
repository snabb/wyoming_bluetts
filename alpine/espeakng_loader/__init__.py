"""Shim replacing the real PyPI `espeakng_loader` package for the Alpine build.

blue_onnx/__init__.py unconditionally does `import espeakng_loader` and calls
`get_library_path()`/`get_data_path()` to wire up phonemizer-fork's espeak
backend. The real PyPI package bundles a prebuilt shared library that is
glibc-only (no musllinux wheel, and building from source doesn't produce a
usable binary either) -- so it can't be used on Alpine. This shim points at
Alpine's own musl-native `espeak-ng`/`espeak-ng-data` apk packages instead,
which provide the same functionality.
"""


def get_library_path() -> str:
    return "/usr/lib/libespeak-ng.so.1"


def get_data_path() -> str:
    return "/usr/share/espeak-ng-data"
