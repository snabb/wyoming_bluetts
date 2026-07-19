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
from wyoming.server import AsyncTcpServer

try:
    # See the matching try/except in handler.py for why this is soft.
    from blue_onnx.style import VoiceStyleExtractor
except ImportError:
    VoiceStyleExtractor = None  # type: ignore[assignment,misc]  # ty:ignore[invalid-assignment]

from . import __version__, models
from .handler import (
    BlueTTSEventHandler,
    finalize_voice_plan,
    get_wyoming_info,
    list_custom_voice_names,
    load_voice,
    plan_voices,
)

_LOGGER = logging.getLogger(__name__)

# Hebrew needs an extra ~20 MB G2P (renikud) model download, so it's left out
# of the default set -- add it back with --languages en,es,de,it,he (or
# whichever subset you want) if you need it.
DEFAULT_LANGUAGES = ",".join(lang for lang in blue_onnx.AVAILABLE_LANGS if lang != "he")


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
    parser.add_argument(
        "--speak-decimal-points",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Rewrite decimal numbers like '3.5' to '3 point 5' before synthesis "
            "(default: enabled). espeak's number reading otherwise renders the "
            "decimal point as a literal pause instead of the word 'point'; "
            "disable with --no-speak-decimal-points to turn this off."
        ),
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
    # phonemizer's espeak backend logs a "words count mismatch" WARNING
    # whenever its word-count sanity check trips, which happens routinely
    # with preserve_punctuation=True (our config) -- e.g. punctuation or
    # numbers commonly shift the count without indicating a real problem.
    # This fires unconditionally on every mismatch regardless of phonemizer's
    # own word-mismatch "mode" (even "ignore" mode still logs the summary via
    # BaseWordsMismatch._resume()), so the logger level is the only knob --
    # except setLevel() alone doesn't stick: blue_onnx never passes phonemizer
    # a custom logger, so EspeakBackend.__init__ calls
    # phonemizer.logger.get_logger() the first time it's lazily constructed
    # (on the first synthesis request per language) which unconditionally
    # does logging.getLogger("phonemizer").setLevel(logging.WARNING),
    # silently reverting this line the moment real synthesis happens. A
    # logging.Filter isn't reset that way (get_logger() never touches
    # logger.filters), so it's the part that actually suppresses the noise in
    # production; the setLevel() call is kept too as a (harmless, if
    # ultimately overridden) belt-and-braces default for any log record that
    # doesn't go through this exact path.
    logging.getLogger("phonemizer").setLevel(logging.ERROR)

    class _SuppressWordsMismatch(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return "words count mismatch" not in record.getMessage()

    logging.getLogger("phonemizer").addFilter(_SuppressWordsMismatch())

    _LOGGER.info("Starting Wyoming BlueTTS server v%s", __version__)

    # Resolve to absolute paths before the CWD changes below.
    models_dir = Path(args.models_dir).resolve()
    voices_dir = Path(args.voices_dir).resolve()
    args.voices_dir = str(voices_dir)

    try:
        models.ensure_model_bundle(
            models_dir, include_cloning=VoiceStyleExtractor is not None
        )
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
        _LOGGER.warning(
            "No valid languages configured; falling back to the default set (%s)",
            DEFAULT_LANGUAGES,
        )
        requested_languages = DEFAULT_LANGUAGES.split(",")

    if "he" in requested_languages and not models.ensure_renikud_model(models_dir):
        _LOGGER.warning(
            "Hebrew G2P model unavailable; dropping 'he' from advertised languages"
        )
        requested_languages = [lang for lang in requested_languages if lang != "he"]

    if args.default_language not in requested_languages:
        fallback = "en" if "en" in requested_languages else requested_languages[0]
        _LOGGER.warning(
            "Default language '%s' not in advertised languages; using '%s'",
            args.default_language,
            fallback,
        )
        args.default_language = fallback
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

    if VoiceStyleExtractor is not None:
        style_extractor = VoiceStyleExtractor(
            onnx_dir=str(models_dir), config=str(models_dir / "tts.json")
        )
    else:
        style_extractor = None
        _LOGGER.info(
            "Zero-shot voice cloning from .wav samples is unavailable in this "
            "build (librosa/numba dependency chain excluded to keep the image "
            "small). Precomputed style JSON custom voices still work."
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

    voice_cache: dict = {}
    for name in dict.fromkeys(to_preload):
        style = load_voice(style_extractor, name, args.voices_dir)
        if style is not None:
            voice_cache[name] = style
            _LOGGER.info("Preloaded voice: %s", name)
        else:
            _LOGGER.error("Could not preload voice '%s'", name)

    try:
        advertise, default_voice = finalize_voice_plan(
            configured, advertise, default_voice, voice_cache
        )
    except ValueError as err:
        _LOGGER.critical("No usable default voice: %s", err)
        sys.exit(1)
    args.voice = default_voice

    _LOGGER.info(
        "Default voice: %s | preloaded %d voice(s) | advertising %d voice(s): %s | languages: %s",
        default_voice,
        len(voice_cache),
        len(advertise),
        ", ".join(advertise),
        ", ".join(requested_languages),
    )

    wyoming_info = get_wyoming_info(
        advertise, requested_languages, supports_cloning=style_extractor is not None
    )

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
