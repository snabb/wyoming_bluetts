#!/usr/bin/env python3
"""Wyoming server for BlueTTS."""

import argparse
import asyncio
import logging
import os
import sys
from functools import partial
from pathlib import Path
from typing import cast

import blue_onnx
from blue_onnx.style import VoiceStyleExtractor
from wyoming.server import AsyncTcpServer

from . import __version__, models
from .handler import (
    BlueTTSEventHandler,
    get_wyoming_info,
    list_custom_voice_names,
    load_voice,
    plan_voices,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_LANGUAGES = ",".join(blue_onnx.AVAILABLE_LANGS)


async def main() -> None:
    """Run the Wyoming BlueTTS server."""
    parser = argparse.ArgumentParser(description="Wyoming server for BlueTTS")
    parser.add_argument(
        "--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port", type=int, default=10200, help="Port to bind to (default: 10200)"
    )
    parser.add_argument(
        "--voices",
        default="",
        help=(
            "Comma-separated voices to load and advertise, e.g. 'female1' or "
            "'female1,male1'. Each is preloaded for fast first response and is "
            "the ONLY set advertised to Home Assistant. Names are presets "
            "(female1, male1) or custom style JSON / wav filenames without "
            "extension. Leave empty to advertise every preset + custom voice, "
            "loaded on demand."
        ),
    )
    parser.add_argument(
        "--voices-dir",
        default="/share/tts-voices",
        help="Directory containing custom voice style JSON / wav samples (default: /share/tts-voices)",
    )
    parser.add_argument(
        "--models-dir",
        default="/data/models",
        help="Directory holding the BlueTTS ONNX model bundle, downloaded automatically if missing (default: /data/models)",
    )
    parser.add_argument(
        "--languages",
        default=DEFAULT_LANGUAGES,
        help=f"Comma-separated languages to advertise (default: {DEFAULT_LANGUAGES})",
    )
    parser.add_argument(
        "--default-language",
        default="en",
        help="Language used when a request doesn't resolve one (default: en)",
    )
    parser.add_argument(
        "--total-step",
        type=int,
        default=5,
        help="Flow-matching diffusion steps; quality/speed tradeoff (default: 5)",
    )
    parser.add_argument(
        "--cfg-scale",
        type=float,
        default=4.0,
        help="Classifier-free guidance scale (default: 4.0)",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Speech speed multiplier (default: 1.0)",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--version", action="version", version=__version__)

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    # numba (used internally by librosa's voice-cloning feature extraction)
    # logs its JIT type-inference search at DEBUG level, including expected
    # internal exceptions it recovers from -- extremely noisy and not
    # actionable, so keep it quiet even when --debug is set.
    logging.getLogger("numba").setLevel(logging.WARNING)

    _LOGGER.info("Starting Wyoming BlueTTS server v%s", __version__)

    # Resolve to absolute paths before the CWD changes below.
    models_dir = Path(args.models_dir).resolve()
    voices_dir = Path(args.voices_dir).resolve()
    args.voices_dir = str(voices_dir)

    try:
        models.ensure_model_bundle(models_dir)
        models.ensure_blue_onnx_vocab()
    except Exception:
        _LOGGER.critical("Failed to obtain the BlueTTS model bundle", exc_info=True)
        sys.exit(1)

    requested_languages = [
        lang.strip() for lang in args.languages.split(",") if lang.strip()
    ]
    unknown = [
        lang for lang in requested_languages if lang not in blue_onnx.AVAILABLE_LANGS
    ]
    if unknown:
        _LOGGER.warning("Dropping unsupported language(s): %s", ", ".join(unknown))
    requested_languages = [
        lang for lang in requested_languages if lang in blue_onnx.AVAILABLE_LANGS
    ]
    if not requested_languages:
        _LOGGER.warning("No valid languages configured; falling back to 'en'")
        requested_languages = ["en"]

    if "he" in requested_languages and not models.ensure_renikud_model(models_dir):
        _LOGGER.warning(
            "Hebrew G2P model unavailable; dropping 'he' from advertised languages"
        )
        requested_languages = [lang for lang in requested_languages if lang != "he"]

    if args.default_language not in requested_languages:
        _LOGGER.warning(
            "Default language '%s' not in advertised languages; using 'en'",
            args.default_language,
        )
        args.default_language = (
            "en" if "en" in requested_languages else requested_languages[0]
        )
    args.requested_languages = requested_languages

    # blue_onnx.TextProcessor.__init__ auto-discovers Hebrew G2P weights only via
    # a hardcoded relative check `os.path.exists("model.onnx")` in the process's
    # current working directory, and load_text_to_speech() constructs
    # TextProcessor() with no arguments -- there is no way to pass a custom path
    # through the public API. Placing model.onnx directly in models_dir (done by
    # ensure_renikud_model) and chdir-ing into models_dir before loading is the
    # least invasive fix; safe here because every path we pass into blue_onnx or
    # hold onto afterwards was already resolved to absolute, above.
    os.chdir(models_dir)

    engine = blue_onnx.load_text_to_speech(
        onnx_dir=str(models_dir),
        use_gpu=False,
        config_path=str(models_dir / "tts.json"),
    )
    _LOGGER.info("BlueTTS engine loaded (sample_rate=%d Hz)", engine.sample_rate)

    style_extractor = VoiceStyleExtractor(
        onnx_dir=str(models_dir), config=str(models_dir / "tts.json")
    )

    custom_voice_names = list_custom_voice_names(args.voices_dir)
    if custom_voice_names:
        _LOGGER.info(
            "Found %d custom voice(s) in %s: %s",
            len(custom_voice_names),
            args.voices_dir,
            ", ".join(custom_voice_names),
        )

    configured = [v.strip() for v in (args.voices or "").split(",") if v.strip()]
    to_preload, advertise, default_voice = plan_voices(configured, custom_voice_names)
    args.voice = default_voice

    voice_cache: dict = {}
    for name in dict.fromkeys(to_preload):
        style = load_voice(style_extractor, name, args.voices_dir)
        if style is not None:
            voice_cache[name] = style
            _LOGGER.info("Preloaded voice: %s", name)
        else:
            _LOGGER.error("Could not preload voice '%s'", name)

    _LOGGER.info(
        "Default voice: %s | preloaded %d voice(s) | advertising %d voice(s): %s | languages: %s",
        default_voice,
        len(voice_cache),
        len(advertise),
        ", ".join(advertise),
        ", ".join(requested_languages),
    )

    wyoming_info = get_wyoming_info(advertise, requested_languages)

    # Bind all interfaces (IPv4 + IPv6) when host is the wildcard: Home
    # Assistant's hassio network is dual-stack and may resolve the add-on to an
    # IPv6 address, so an IPv4-only socket would be unreachable. host=None makes
    # asyncio listen on every address family. asyncio accepts host=None at
    # runtime even though wyoming types the parameter as str.
    bind_host = None if args.host in ("", "0.0.0.0", "::") else args.host
    server = AsyncTcpServer(host=cast("str", bind_host), port=args.port)
    _LOGGER.info("Server listening on %s:%d", args.host, args.port)

    await server.run(
        partial(
            BlueTTSEventHandler,
            wyoming_info,
            args,
            engine,
            style_extractor,
            voice_cache,
        )
    )


def run() -> None:
    """Entry point."""
    asyncio.run(main())


if __name__ == "__main__":
    run()
