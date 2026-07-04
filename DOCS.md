# Home Assistant App: Wyoming BlueTTS

Fast, local, multilingual text-to-speech with zero-shot voice cloning, using
the [BlueTTS](https://github.com/maxmelichov/BlueTTS) ONNX model. CPU only.

## Installation

1. Settings → Apps → Install app → ⋮ (three dots) → Repositories → add this
   repository's URL.
2. Find "Wyoming BlueTTS" in the store and install it.
3. Start the app. On first start it downloads the ONNX model bundle
   (several hundred MB) — this can take a few minutes; the app log will
   show progress.
4. Home Assistant auto-discovers it via the Wyoming protocol. Assign it as
   the TTS provider for your voice assistant under Settings → Voice
   assistants.

## Configuration options

| Option | Default | Description |
|---|---|---|
| `languages` | `en, es, de, it, he` | Languages to advertise |
| `default_language` | `en` | Used when a request doesn't resolve one |
| `voices` | `female1` | Voices to preload + advertise; empty list = advertise all, load on demand |
| `voices_dir` | `/share/tts-voices` | Folder for custom voice style JSON / wav samples |
| `models_dir` | `/data/models` | Folder for the auto-downloaded ONNX model bundle (standalone Docker only; app installs should leave this at the default) |
| `debug` | `false` | Verbose logging |

## Preset voices

| Voice | Description |
|---|---|
| `female1` | Built-in female voice |
| `male1` | Built-in male voice |

Both work with every advertised language — BlueTTS voices are
language-independent style embeddings, unlike some TTS engines where each
preset voice is tied to one language.

## Custom / cloned voices

Drop a file into the `voices_dir` folder (`/share/tts-voices` by default) and
reference it by filename without its extension:

- A precomputed style JSON, or
- A clean 5-15 second mono reference `.wav` clip — cloned automatically on
  first use, then cached under `.bluetts_cache/` so cloning only runs once.

## Troubleshooting

- **Slow first start**: the model bundle download happens on first boot; check
  the app log for progress. Subsequent starts are fast.
- **Hebrew missing from the TTS voice's supported languages**: the optional
  Hebrew G2P model failed to download (check the log); the other four
  languages are unaffected.
- **Custom voice not found**: check the filename (without extension) matches
  what you typed in `voices`, and that it's a `.json` or `.wav` file directly
  in `voices_dir` (not a subfolder).
- **Cloned voice sounds off**: try a cleaner, quieter reference clip without
  background noise or music.
