"""Tests for wyoming_bluetts handler."""

import asyncio
from types import SimpleNamespace

import blue_onnx
import numpy as np
from blue_onnx import Style
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.tts import Synthesize, SynthesizeStopped, SynthesizeVoice
from wyoming_bluetts.handler import (
    PRESET_VOICES,
    BlueTTSEventHandler,
    _speak_decimal_points,
    find_custom_voice_source,
    get_wyoming_info,
    load_voice,
    plan_voices,
)

ALL_LANGUAGES = ["en", "es", "de", "it", "he"]


def _long_sentence(word: str) -> str:
    return (f"{word} " * 40).strip() + "."


def _run(coro):
    return asyncio.run(coro)


class _FakeEngine:
    """Minimal stand-in for a loaded BlueTTS TextToSpeech engine."""

    sample_rate = 44100

    def __init__(self, log=None):
        self.calls: list[str] = []
        self.log = log

    def __call__(
        self, text, lang, style, total_step, cfg_scale, speed, silence_duration
    ):
        self.calls.append(text)
        if self.log is not None:
            self.log.append(("call", text))
        samples = np.full((1, 8), 0.5, dtype=np.float32)
        return samples, np.array([0.1], dtype=np.float32)


class _RaisingEngine(_FakeEngine):
    """Fake engine that always fails, to test error-path framing."""

    def __call__(self, text, *args, **kwargs):
        self.calls.append(text)
        raise RuntimeError("boom")


class _FakeStyleExtractor:
    """Fake VoiceStyleExtractor stand-in that never touches ONNX Runtime."""

    def __init__(self):
        self.from_wav_calls = 0

    def from_wav(self, ref_wav):
        self.from_wav_calls += 1
        ttl = np.zeros((1, 50, 256), dtype=np.float32)
        dp = np.zeros((1, 8, 16), dtype=np.float32)
        return Style(ttl, dp)


class _RecordingHandler(BlueTTSEventHandler):
    """Handler whose write_event records events instead of touching a socket."""

    def __init__(
        self,
        engine,
        style_extractor,
        voices_dir="/nonexistent",
        voice_cache=None,
        log=None,
    ):
        # Bypass AsyncEventHandler.__init__ (needs a reader/writer); set only
        # what handle_event uses.
        self.wyoming_info = get_wyoming_info(["female1", "male1"], ALL_LANGUAGES)
        self.cli_args = SimpleNamespace(
            voice="female1",
            voices_dir=voices_dir,
            requested_languages=ALL_LANGUAGES,
            default_language="en",
            total_step=5,
            cfg_scale=4.0,
            speed=1.0,
            speak_decimal_points=True,
        )
        self.engine = engine
        self.style_extractor = style_extractor
        self.voice_cache = voice_cache if voice_cache is not None else {}
        self.log = log
        self.written = []

    async def write_event(self, event):
        self.written.append(event)
        if self.log is not None and AudioChunk.is_type(event.type):
            self.log.append(("write", None))


# --- preset / plan_voices ----------------------------------------------------


def test_preset_voices_not_empty():
    assert len(PRESET_VOICES) > 0


def test_preset_voices_contains_female1_and_male1():
    assert "female1" in PRESET_VOICES
    assert "male1" in PRESET_VOICES


def test_plan_voices_configured_advertises_only_those():
    to_preload, advertise, default = plan_voices(["rocky", "female1"], ["rocky"])
    assert to_preload == ["rocky", "female1"]
    assert advertise == ["rocky", "female1"]
    assert default == "rocky"


def test_plan_voices_dedups_and_strips():
    to_preload, advertise, default = plan_voices([" rocky ", "rocky", "female1"], [])
    assert to_preload == ["rocky", "female1"]
    assert default == "rocky"


def test_plan_voices_empty_advertises_all_presets_plus_custom_and_defaults_to_female1():
    to_preload, advertise, default = plan_voices([], ["rocky"])
    assert default == "female1"
    assert to_preload == ["female1"]
    assert "rocky" in advertise
    assert set(PRESET_VOICES).issubset(set(advertise))


# --- get_wyoming_info ---------------------------------------------------------


def test_get_wyoming_info_returns_info():
    info = get_wyoming_info(["female1", "male1"], ["en"])
    assert len(info.tts) == 1
    assert info.tts[0].name == "bluetts"


def test_get_wyoming_info_voices():
    voices = ["female1", "male1", "custom_voice"]
    info = get_wyoming_info(voices, ["en"])
    assert {v.name for v in info.tts[0].voices} == set(voices)


def test_get_wyoming_info_sets_supports_synthesize_streaming_true():
    info = get_wyoming_info(["female1"], ["en"])
    assert info.tts[0].supports_synthesize_streaming is True


def test_get_wyoming_info_advertises_requested_languages():
    info = get_wyoming_info(["female1", "male1"], ALL_LANGUAGES)
    for voice in info.tts[0].voices:
        assert voice.languages == ALL_LANGUAGES


def test_get_wyoming_info_empty_voices():
    info = get_wyoming_info([], ["en"])
    assert len(info.tts[0].voices) == 0


def test_get_wyoming_info_mentions_cloning_by_default():
    info = get_wyoming_info(["female1"], ["en"])
    assert "cloning" in (info.tts[0].description or "")


def test_get_wyoming_info_omits_cloning_when_unsupported():
    info = get_wyoming_info(["female1"], ["en"], supports_cloning=False)
    assert "cloning" not in (info.tts[0].description or "")


# --- load_voice: preset + custom wav cloning cache ----------------------------


def test_load_voice_preset_female1_loads_real_bundled_json():
    style = load_voice(_FakeStyleExtractor(), "female1", "/nonexistent")
    assert style is not None
    assert style.ttl.shape == (1, 50, 256)


def test_load_voice_unknown_name_returns_none():
    assert load_voice(_FakeStyleExtractor(), "nope", "/nonexistent") is None


def test_custom_voice_name_cannot_escape_voices_dir(tmp_path):
    voices_dir = tmp_path / "voices"
    voices_dir.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}")

    assert find_custom_voice_source(str(voices_dir), "../outside") is None
    assert (
        find_custom_voice_source(str(voices_dir), str(outside.with_suffix(""))) is None
    )


def test_custom_voice_symlink_cannot_escape_voices_dir(tmp_path):
    voices_dir = tmp_path / "voices"
    voices_dir.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    (voices_dir / "linked.json").symlink_to(outside)

    assert find_custom_voice_source(str(voices_dir), "linked") is None


def test_custom_voice_wav_triggers_style_extractor_and_writes_cache(tmp_path):
    (tmp_path / "cloned.wav").write_bytes(b"fake wav data")
    extractor = _FakeStyleExtractor()

    style = load_voice(extractor, "cloned", str(tmp_path))
    assert style is not None
    assert extractor.from_wav_calls == 1
    cache_path = tmp_path / ".bluetts_cache" / "cloned.json"
    assert cache_path.is_file()

    # Second load with an unchanged source mtime must hit the cache, not
    # re-invoke the (comparatively slow) style extractor.
    style2 = load_voice(extractor, "cloned", str(tmp_path))
    assert style2 is not None
    assert extractor.from_wav_calls == 1


def test_load_voice_wav_without_style_extractor_returns_none_gracefully(tmp_path):
    """Default (no ENABLE_VOICE_CLONING) images pass style_extractor=None."""
    (tmp_path / "cloned.wav").write_bytes(b"fake wav data")
    assert load_voice(None, "cloned", str(tmp_path)) is None


# --- streaming synthesis -------------------------------------------------------


def test_multi_sentence_text_calls_engine_once_per_sentence_chunk_and_streams_between_calls():
    text = " ".join(_long_sentence(w) for w in ["Alpha", "Bravo", "Charlie"])
    expected_chunks = blue_onnx.chunk_text(text, max_len=300)
    assert len(expected_chunks) >= 2  # sanity: text is long enough to actually split

    log: list = []
    engine = _FakeEngine(log=log)
    handler = _RecordingHandler(engine, _FakeStyleExtractor(), log=log)

    result = _run(handler.handle_event(Synthesize(text=text).event()))

    assert result is True
    assert len(engine.calls) == len(expected_chunks)
    # Interleaved: a "write" (AudioChunk) appears before the NEXT chunk's "call"
    # starts, proving audio for chunk i is flushed before chunk i+1 is even
    # synthesized -- not buffered until the whole reply finishes.
    call_indices = [i for i, (kind, _) in enumerate(log) if kind == "call"]
    for call_idx, next_call_idx in zip(call_indices, call_indices[1:], strict=False):
        assert any(kind == "write" for kind, _ in log[call_idx + 1 : next_call_idx])


def test_inline_lang_tags_fall_back_to_single_whole_text_call():
    text = "Hello <en>welcome</en> and <es>hola</es>."
    engine = _FakeEngine()
    handler = _RecordingHandler(engine, _FakeStyleExtractor())

    result = _run(handler.handle_event(Synthesize(text=text).event()))

    assert result is True
    assert engine.calls == [text]


def test_synthesize_streams_chunks_in_order():
    engine = _FakeEngine()
    handler = _RecordingHandler(engine, _FakeStyleExtractor())

    result = _run(handler.handle_event(Synthesize(text="hello there").event()))

    assert result is True
    assert engine.calls == ["hello there"]
    assert AudioStart.is_type(handler.written[0].type)
    chunk_events = [e for e in handler.written if AudioChunk.is_type(e.type)]
    assert len(chunk_events) >= 1
    assert AudioStop.is_type(handler.written[-2].type)
    assert SynthesizeStopped.is_type(handler.written[-1].type)


def test_empty_text_sends_clean_empty_response():
    engine = _FakeEngine()
    handler = _RecordingHandler(engine, _FakeStyleExtractor())

    result = _run(handler.handle_event(Synthesize(text="   ").event()))

    assert result is True
    assert engine.calls == []


# --- decimal point speaking ---------------------------------------------------


def test_speak_decimal_points_english():
    assert _speak_decimal_points("It's 3.5 meters.", "en") == "It's 3 point 5 meters."


def test_speak_decimal_points_spanish():
    assert _speak_decimal_points("Son 3.5 metros.", "es") == "Son 3 punto 5 metros."


def test_speak_decimal_points_german():
    assert (
        _speak_decimal_points("Es sind 3.5 Meter.", "de") == "Es sind 3 Punkt 5 Meter."
    )


def test_speak_decimal_points_italian():
    assert _speak_decimal_points("Sono 3.5 metri.", "it") == "Sono 3 punto 5 metri."


def test_speak_decimal_points_hebrew_is_a_noop():
    # Hebrew doesn't go through espeak at all -- leave it untouched rather
    # than risk routing plain digits through the unrelated renikud G2P path.
    assert _speak_decimal_points("3.5", "he") == "3.5"


def test_speak_decimal_points_ignores_non_decimal_periods():
    assert _speak_decimal_points("Hello. World.", "en") == "Hello. World."


def test_speak_decimal_points_multiple_in_one_string():
    assert _speak_decimal_points("3.5 and 2.75", "en") == "3 point 5 and 2 point 75"


def test_synthesize_expands_decimal_point_by_default():
    engine = _FakeEngine()
    handler = _RecordingHandler(engine, _FakeStyleExtractor())

    _run(handler.handle_event(Synthesize(text="It's 3.5 meters.").event()))

    assert engine.calls == ["It's 3 point 5 meters."]


def test_synthesize_leaves_decimal_point_when_disabled():
    engine = _FakeEngine()
    handler = _RecordingHandler(engine, _FakeStyleExtractor())
    handler.cli_args.speak_decimal_points = False

    _run(handler.handle_event(Synthesize(text="It's 3.5 meters.").event()))

    assert engine.calls == ["It's 3.5 meters."]


def test_synthesize_stopped_sent_even_when_synthesis_raises():
    engine = _RaisingEngine()
    handler = _RecordingHandler(engine, _FakeStyleExtractor())

    result = _run(handler.handle_event(Synthesize(text="hello").event()))

    assert result is True
    assert engine.calls == ["hello"]
    assert AudioStart.is_type(handler.written[0].type)
    assert AudioStop.is_type(handler.written[-2].type)
    assert SynthesizeStopped.is_type(handler.written[-1].type)
    assert not [e for e in handler.written if AudioChunk.is_type(e.type)]


# --- language resolution -------------------------------------------------------


def test_language_resolution_falls_back_to_default_when_not_requested():
    handler = _RecordingHandler(_FakeEngine(), _FakeStyleExtractor())
    synth = Synthesize(text="hi", voice=SynthesizeVoice(name="female1", language="fr"))
    assert handler._resolve_language(synth) == "en"


def test_language_resolution_honors_requested_language():
    handler = _RecordingHandler(_FakeEngine(), _FakeStyleExtractor())
    synth = Synthesize(text="hi", voice=SynthesizeVoice(name="female1", language="es"))
    assert handler._resolve_language(synth) == "es"


def test_voice_falls_back_gracefully_when_cloning_unavailable(tmp_path):
    """style_extractor=None (default images): a .wav-only voice request falls
    back to the default voice instead of crashing, same as any other
    unavailable-voice fallback."""
    (tmp_path / "cloned.wav").write_bytes(b"fake wav data")
    engine = _FakeEngine()
    handler = _RecordingHandler(engine, None, voices_dir=str(tmp_path))

    result = _run(
        handler.handle_event(
            Synthesize(text="hi", voice=SynthesizeVoice(name="cloned")).event()
        )
    )

    assert result is True
    assert engine.calls == ["hi"]
    chunk_events = [e for e in handler.written if AudioChunk.is_type(e.type)]
    assert len(chunk_events) >= 1
    assert SynthesizeStopped.is_type(handler.written[-1].type)


def test_voice_fallback_when_requested_voice_unavailable():
    handler = _RecordingHandler(_FakeEngine(), _FakeStyleExtractor())
    result = _run(
        handler.handle_event(
            Synthesize(text="hi", voice=SynthesizeVoice(name="does_not_exist")).event()
        )
    )
    assert result is True
    # Falls back to cli_args.voice ("female1") rather than sending no audio.
    chunk_events = [e for e in handler.written if AudioChunk.is_type(e.type)]
    assert len(chunk_events) >= 1
