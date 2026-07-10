"""Wyoming event handler for BlueTTS."""

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import AsyncIterator, Optional, Protocol

import blue_onnx
from blue_onnx import Style
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.event import Event
from wyoming.info import Attribution, Describe, Info, TtsProgram, TtsVoice
from wyoming.server import AsyncEventHandler
from wyoming.tts import Synthesize, SynthesizeStopped

try:
    # blue_onnx.style (zero-shot .wav voice cloning) pulls in a heavy
    # librosa/numba/llvmlite/scipy/scikit-learn/sympy dependency chain
    # (400+ MB) that's only needed for this one optional feature. The default
    # Docker image strips those packages at build time (see Dockerfile's
    # ENABLE_VOICE_CLONING arg) to keep the common case small, so this import
    # must be soft: cloning support becomes unavailable rather than crashing
    # the whole server on startup. `uv sync`/local dev installs always have
    # the full dependency set (blue-onnx declares librosa unconditionally),
    # so this only actually matters for the Docker image.
    from blue_onnx.style import payload_from_style
except ImportError:
    payload_from_style = None  # type: ignore[assignment]  # ty:ignore[invalid-assignment]

_LOGGER = logging.getLogger(__name__)


class StyleExtractor(Protocol):
    """The one method load_voice() needs from blue_onnx.style.VoiceStyleExtractor.

    Kept as a Protocol (rather than importing the concrete class) so tests can
    supply a lightweight fake without touching ONNX Runtime.
    """

    def from_wav(self, ref_wav: str) -> Style: ...


# BlueTTS ships exactly two preset voice-style JSONs in its git repo (not in the
# blue-onnx pip package), so we vendor copies under wyoming_bluetts/voices/.
PRESET_VOICES = ["female1", "male1"]
_PACKAGE_VOICES_DIR = Path(__file__).parent / "voices"

# .wav for zero-shot cloning; narrower than pocket-tts's 5-extension set since
# librosa/soundfile decode wav most reliably across platforms.
CUSTOM_VOICE_EXTENSIONS = {".json", ".wav"}

# <voices_dir>/.bluetts_cache/<name>.json holds the style extracted from a
# <voices_dir>/<name>.wav reference clip, so cloning only runs once per voice.
CACHE_SUBDIR = ".bluetts_cache"

# Bounds the size of any single AudioChunk event (~46ms @44.1kHz); independent
# of the sentence-level streaming granularity below -- this just prevents one
# long sentence from becoming one giant event.
AUDIO_CHUNK_BYTES = 4096

_INLINE_LANG_TAG_RE = re.compile(r"<(\w+)>.*?</\1>", re.DOTALL)

_ATTRIBUTION = Attribution(name="BlueTTS", url="https://github.com/maxmelichov/BlueTTS")

# Process-wide lock serializing generation and voice cloning on the shared ONNX
# Runtime sessions (tuned via ORT_NUM_THREADS/intra_op_num_threads for
# near-exclusive use). Created lazily on the running loop the first time it is
# needed.
_GENERATION_LOCK: "Optional[asyncio.Lock]" = None


def _generation_lock() -> "asyncio.Lock":
    """Return the process-wide generation lock, creating it on first use."""
    global _GENERATION_LOCK
    if _GENERATION_LOCK is None:
        _GENERATION_LOCK = asyncio.Lock()
    return _GENERATION_LOCK


def find_preset_voice_path(name: str) -> "Path | None":
    """Return the bundled style JSON for a preset voice name, if any."""
    return _voice_file_path(_PACKAGE_VOICES_DIR, name, ".json")


def _voice_file_path(directory: Path, name: str, suffix: str) -> "Path | None":
    """Return a voice file path only when it remains inside ``directory``."""
    if not name or name in {".", ".."} or Path(name).name != name:
        return None

    root = directory.resolve()
    candidate = (root / f"{name}{suffix}").resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        # Also rejects symlinks that point outside the configured voice root.
        return None
    return candidate if candidate.is_file() else None


def find_custom_voice_source(voices_dir: str, name: str) -> "Path | None":
    """Return the custom voice source file for ``name`` in ``voices_dir``, if any.

    Prefers a precomputed style JSON over a raw wav reference clip.
    """
    voices_path = Path(voices_dir)
    json_path = _voice_file_path(voices_path, name, ".json")
    if json_path is not None:
        return json_path
    wav_path = _voice_file_path(voices_path, name, ".wav")
    if wav_path is not None:
        return wav_path
    return None


def list_custom_voice_names(voices_dir: str) -> list[str]:
    """List custom voice names (file stems) directly under ``voices_dir``."""
    voices_path = Path(voices_dir)
    if not voices_path.exists():
        return []
    return [
        f.stem
        for f in voices_path.iterdir()
        if f.is_file() and f.suffix.lower() in CUSTOM_VOICE_EXTENSIONS
    ]


def plan_voices(
    configured: list[str], custom_voice_names: list[str]
) -> tuple[list[str], list[str], str]:
    """Decide which voices to preload, which to advertise, and the default.

    Returns ``(to_preload, to_advertise, default_voice)``. Unlike pocket-tts,
    no language parameter is needed: BlueTTS voices are language-independent
    style embeddings, so any voice works with any advertised language.

    When ``configured`` is non-empty, exactly those voices are preloaded and
    advertised (the first is the default). When empty, every built-in preset
    plus any custom files is advertised (loaded on demand), preloading only
    the first preset.
    """
    configured = list(dict.fromkeys(v.strip() for v in configured if v.strip()))
    if configured:
        return configured, configured, configured[0]

    advertise = list(dict.fromkeys(PRESET_VOICES + custom_voice_names))
    return [PRESET_VOICES[0]], advertise, PRESET_VOICES[0]


def _style_cache_path(voices_dir: str, name: str) -> "Path | None":
    if not name or name in {".", ".."} or Path(name).name != name:
        return None

    voices_root = Path(voices_dir).resolve()
    candidate = (voices_root / CACHE_SUBDIR / f"{name}.json").resolve()
    try:
        candidate.relative_to(voices_root)
    except ValueError:
        return None
    return candidate


def load_voice(
    style_extractor: "StyleExtractor | None", name: str, voices_dir: str
) -> "Style | None":
    """Load a voice style by name (preset, custom JSON, or cloned from a wav).

    Wav-cloned styles are cached on disk (keyed by the source wav's mtime) so
    the (comparatively slow) style extraction only runs once per voice.
    """
    preset_path = find_preset_voice_path(name)
    if preset_path is not None:
        try:
            return blue_onnx.load_voice_style([str(preset_path)])
        except Exception:
            _LOGGER.exception("Failed to load preset voice %s", name)
            return None

    source = find_custom_voice_source(voices_dir, name)
    if source is None:
        return None

    if source.suffix.lower() == ".json":
        try:
            return blue_onnx.load_voice_style([str(source)])
        except Exception:
            _LOGGER.exception("Failed to load custom voice %s", name)
            return None

    # .wav -> zero-shot cloning, with an on-disk cache keyed by source mtime.
    cache_path = _style_cache_path(voices_dir, name)
    if (
        cache_path is not None
        and cache_path.is_file()
        and cache_path.stat().st_mtime >= source.stat().st_mtime
    ):
        try:
            return blue_onnx.load_voice_style([str(cache_path)])
        except Exception:
            _LOGGER.warning(
                "Cached voice style for %s is unreadable, regenerating", name
            )

    if style_extractor is None:
        _LOGGER.warning(
            "Cannot clone voice '%s' from %s: voice cloning is not available in "
            "this build (the librosa/numba dependency chain was excluded from "
            "the image; see ENABLE_VOICE_CLONING in the Dockerfile). Use a "
            "precomputed style JSON instead, or build with "
            "--build-arg ENABLE_VOICE_CLONING=true.",
            name,
            source,
        )
        return None

    try:
        style = style_extractor.from_wav(str(source))
    except Exception:
        _LOGGER.exception("Failed to extract voice style from %s", source)
        return None

    if cache_path is not None:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = payload_from_style(style, metadata={"source": str(source)})
            cache_path.write_text(json.dumps(payload))
        except Exception:
            _LOGGER.warning(
                "Failed to write voice style cache for %s", name, exc_info=True
            )

    return style


def get_wyoming_info(
    voices: list[str], languages: list[str], supports_cloning: bool = True
) -> Info:
    """Create Wyoming info describing available TTS voices."""
    tts_voices = [
        TtsVoice(
            name=voice,
            attribution=_ATTRIBUTION,
            installed=True,
            description=f"BlueTTS voice: {voice}",
            version=None,
            languages=list(languages),
        )
        for voice in voices
    ]

    description = "BlueTTS - multilingual ONNX text-to-speech"
    if supports_cloning:
        description += " with zero-shot voice cloning"

    from . import __version__

    return Info(
        tts=[
            TtsProgram(
                name="bluetts",
                attribution=_ATTRIBUTION,
                installed=True,
                description=description,
                version=__version__,
                voices=tts_voices,
                supports_synthesize_streaming=True,
            )
        ]
    )


_DECIMAL_RE = re.compile(r"\b(\d+)\.(\d+)\b")

# Only the espeak-routed languages: verified each renders the same "literal
# '.' left between the two expanded number words" artifact this fixes, and
# that substituting this word back in phonemizes cleanly. Hebrew is
# deliberately excluded -- it doesn't go through espeak at all (renikud G2P
# instead), plain digit/latin text bypasses phonemization entirely rather
# than hitting this same bug, and mixing in a Hebrew word for "point" would
# route it through renikud instead with unverified results, a separate,
# untested change beyond what this fix covers.
_DECIMAL_POINT_WORDS = {
    "en": "point",
    "es": "punto",
    "de": "Punkt",
    "it": "punto",
}


def _speak_decimal_points(text: str, lang: str) -> str:
    """Rewrite decimal numbers like '3.5' to '3 point 5' (localized per language).

    espeak's number reading expands each side into words ("three", "five")
    but keeps the literal '.' between them instead of converting it to
    "point" -- that leftover '.' plays as a silent pause in the synthesized
    audio, indistinguishable from a sentence break. Spelling out "point"
    ourselves before phonemization sidesteps this entirely.
    """
    word = _DECIMAL_POINT_WORDS.get(lang)
    if word is None:
        return text
    return _DECIMAL_RE.sub(lambda m: f"{m.group(1)} {word} {m.group(2)}", text)


def _split_for_streaming(text: str) -> list[str]:
    """Sentence/paragraph chunks for incremental synthesis, or ``[text]`` unchanged.

    Falls back to a single whole-text piece (no streaming benefit for this one
    request, but still correct) whenever an inline ``<lang>...</lang>`` span is
    present: our chunk boundaries are picked on raw text and could otherwise
    split a tag pair across two chunks, breaking blue_onnx's per-tag language
    routing. Mixed-language requests are rare enough that trading streaming for
    correctness here is the right call.
    """
    if _INLINE_LANG_TAG_RE.search(text):
        return [text]
    return blue_onnx.chunk_text(text, max_len=300) or [text]


async def _iter_audio_pcm_chunks(
    engine, style: Style, text: str, lang: str, cli_args
) -> AsyncIterator[bytes]:
    """Yield 16-bit PCM byte pieces, flushing after each sentence-chunk synthesizes.

    BlueTTS's ``tts(...)`` call is blocking and returns one whole-clip buffer
    per call -- no native frame-by-frame generator like some other engines
    offer. Incremental delivery is recovered by doing BlueTTS's own internal
    chunk-and-concatenate loop ourselves (``blue_onnx.chunk_text``), calling
    ``engine()`` once per piece and writing that piece's audio to the client
    before starting the next piece's synthesis, instead of waiting for the
    entire reply to finish. Each ``engine()`` call still runs in a worker
    thread so the event loop stays responsive.
    """
    for piece in _split_for_streaming(text):

        def _call(piece: str = piece):
            audio, _dur = engine(
                piece,
                lang=lang,
                style=style,
                total_step=cli_args.total_step,
                cfg_scale=cli_args.cfg_scale,
                speed=cli_args.speed,
                silence_duration=0.0,
            )
            if audio.ndim == 2:
                audio = audio[0]
            return (audio * 32767.0).clip(-32768, 32767).astype("int16").tobytes()

        pcm = await asyncio.to_thread(_call)
        for i in range(0, len(pcm), AUDIO_CHUNK_BYTES):
            yield pcm[i : i + AUDIO_CHUNK_BYTES]


class BlueTTSEventHandler(AsyncEventHandler):
    """Handle Wyoming TTS events with BlueTTS."""

    def __init__(
        self,
        wyoming_info: Info,
        cli_args,
        engine,
        style_extractor: "StyleExtractor | None",
        voice_cache: dict,
        *args,
        **kwargs,
    ) -> None:
        """Initialize handler."""
        super().__init__(*args, **kwargs)
        self.wyoming_info = wyoming_info
        self.cli_args = cli_args
        self.engine = engine
        self.style_extractor = style_extractor
        self.voice_cache = voice_cache

    async def _load_voice(self, name: str) -> "Style | None":
        return await asyncio.to_thread(
            load_voice, self.style_extractor, name, self.cli_args.voices_dir
        )

    def _resolve_voice_name(self, synthesize: Synthesize) -> str:
        voice_name = self.cli_args.voice
        if synthesize.voice and synthesize.voice.name:
            voice_name = synthesize.voice.name
        elif synthesize.voice and synthesize.voice.speaker:
            voice_name = synthesize.voice.speaker
        return voice_name

    def _resolve_language(self, synthesize: Synthesize) -> str:
        # SynthesizeVoice.name strictly overrides language in Wyoming's own
        # client code (SynthesizeVoice.from_dict only falls back to "language"
        # when "name" is absent), so voice.language is rarely populated once a
        # specific voice is chosen -- cli_args.default_language is what
        # actually governs the normal Home Assistant flow.
        voice = synthesize.voice
        if (
            voice
            and voice.language
            and voice.language in self.cli_args.requested_languages
        ):
            return voice.language
        return self.cli_args.default_language

    async def _resolve_style(self, voice_name: str) -> "tuple[Style | None, str]":
        """Look up (or load) a voice's style, falling back to the default voice."""
        style = self.voice_cache.get(voice_name)
        if style is not None:
            return style, voice_name

        # Voice loading can run ONNX cloning inference, so keep it off the event
        # loop and serialize it with synthesis on the shared runtime sessions.
        async with _generation_lock():
            style = self.voice_cache.get(voice_name)
            if style is not None:
                return style, voice_name

            style = await self._load_voice(voice_name)
            if style is not None:
                self.voice_cache[voice_name] = style
                return style, voice_name

            fallback = self.cli_args.voice
            if fallback == voice_name:
                return None, voice_name

            _LOGGER.warning(
                "Voice '%s' unavailable, falling back to '%s'", voice_name, fallback
            )
            style = self.voice_cache.get(fallback)
            if style is None:
                style = await self._load_voice(fallback)
                if style is not None:
                    self.voice_cache[fallback] = style
            return style, fallback

    async def handle_event(self, event: Event) -> bool:
        """Handle Wyoming events."""
        if Describe.is_type(event.type):
            await self.write_event(self.wyoming_info.event())
            _LOGGER.debug("Sent info in response to describe")
            return True

        if Synthesize.is_type(event.type):
            synthesize = Synthesize.from_event(event)
            voice_name = self._resolve_voice_name(synthesize)
            lang = self._resolve_language(synthesize)
            style, resolved_voice = await self._resolve_style(voice_name)
            text = (synthesize.text or "").strip()
            if self.cli_args.speak_decimal_points:
                text = _speak_decimal_points(text, lang)

            _LOGGER.info(
                "Synthesize request: voice=%s, language=%s, chars=%d",
                resolved_voice,
                lang,
                len(text),
            )

            await self.write_event(
                AudioStart(rate=self.engine.sample_rate, width=2, channels=1).event()
            )
            try:
                if style is None:
                    _LOGGER.error(
                        "No voice available for '%s'; check --voices-dir", voice_name
                    )
                elif text:
                    chunk_count = 0
                    audio_bytes = 0
                    t_start = time.monotonic()
                    async with _generation_lock():
                        async for chunk in _iter_audio_pcm_chunks(
                            self.engine, style, text, lang, self.cli_args
                        ):
                            await self.write_event(
                                AudioChunk(
                                    audio=chunk,
                                    rate=self.engine.sample_rate,
                                    width=2,
                                    channels=1,
                                ).event()
                            )
                            chunk_count += 1
                            audio_bytes += len(chunk)
                    generation_ms = int((time.monotonic() - t_start) * 1000)
                    audio_ms = int(audio_bytes / 2 / self.engine.sample_rate * 1000)
                    rtf = audio_ms / generation_ms if generation_ms > 0 else 0.0
                    _LOGGER.info(
                        "Synthesized %d ms of audio in %d ms (%.2fx real-time, %d chunk(s))",
                        audio_ms,
                        generation_ms,
                        rtf,
                        chunk_count,
                    )
            except Exception:
                _LOGGER.exception("Error generating audio")
            finally:
                await self.write_event(AudioStop().event())
                # Required whenever we advertise supports_synthesize_streaming:
                # Home Assistant's incremental streaming client reads events
                # until SynthesizeStopped specifically (it never checks for
                # AudioStop), so omitting this would hang it forever -- even on
                # the exception path above.
                await self.write_event(SynthesizeStopped().event())
            return True

        # SynthesizeStart / SynthesizeChunk / SynthesizeStop (streaming TEXT
        # INPUT, wyoming>=1.7.0) are safely ignored: Home Assistant's own
        # client always also sends one full classic Synthesize event with the
        # complete accumulated text "for backwards compatibility" alongside
        # these, so acting only on that event is sufficient.
        return True
