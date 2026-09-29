"""Dictator dry-run suite: 5 separate tests exercising the real code paths.

Run from the app folder with the venv python:
    cd "D:\\Project X\\Dictator"
    .venv\\Scripts\\python.exe drytest.py

Tests are side-effect-safe: typing injection, history logging and the
GUI overlay are stubbed; no text is ever typed into a real window and no
hotkey is needed. T1/T3 synthesize real speech with Windows SAPI (16 kHz)
and transcribe it with the actual Whisper model on your GPU/CPU.

Exits 0 when all 5 pass, 1 otherwise.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import wave

import numpy as np

import main  # the app itself (imported from this folder)

RESULTS = []


# ------------------------------------------------------------------ helpers

class StubOverlay:
    """Replaces the Tk overlay so the pipeline can run without a GUI."""

    def __init__(self):
        self.states = []

    def set_state(self, state, detail=None):
        self.states.append(state)

    def set_level(self, level):
        pass


def make_app():
    """A real App instance with every machine-touching side effect stubbed."""
    app = main.App()
    app.overlay = StubOverlay()
    app.injected = []  # what the pipeline would have typed
    app.recorded = []  # dictations that would have hit history/dashboard
    main.inject_text = lambda text, cfg: app.injected.append(text)
    main.log_history = lambda *a, **k: None
    app._record_dictation = lambda raw, cleaned, secs, t: app.recorded.append(
        (raw, cleaned, secs))
    return app


def drain_ui_q(app):
    """Run the lambdas process() pushed onto ui_q (normally the tk loop)."""
    while not app.ui_q.empty():
        app.ui_q.get()()


def synth_speech_16k(text, out_path):
    """Synthesize text to a 16 kHz mono 16-bit WAV via Windows SAPI.

    Returns True on success. Whisper expects 16 kHz, so asking SAPI for
    16 kHz avoids resampling artifacts.
    """
    ps = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
    16000,
    [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
    [System.Speech.AudioFormat.AudioChannel]::Mono)
$synth.SetOutputToWaveFile('%OUT%', $fmt)
$synth.Speak('%TEXT%')
$synth.Dispose()
"""
    ps = ps.replace('%OUT%', out_path.replace("'", "''"))
    ps = ps.replace('%TEXT%', text.replace("'", "''"))
    with tempfile.NamedTemporaryFile('w', suffix='.ps1', delete=False,
                                     encoding='utf-8') as f:
        f.write(ps)
        script = f.name
    try:
        subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy',
                        'Bypass', '-File', script], check=True, timeout=60,
                       capture_output=True)
        return os.path.exists(out_path)
    except Exception as e:
        print(f'    (TTS unavailable: {e})')
        return False
    finally:
        try:
            os.unlink(script)
        except OSError:
            pass


def read_wav_16k(path):
    """Read a 16 kHz PCM16 WAV into float32 in [-1, 1)."""
    with wave.open(path, 'rb') as w:
        assert w.getframerate() == 16000, f'unexpected rate {w.getframerate()}'
        assert w.getsampwidth() == 2, 'expected 16-bit PCM'
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0


def ollama_up(url='http://127.0.0.1:11434'):
    try:
        with urllib.request.urlopen(url + '/api/tags', timeout=3) as r:
            return json.load(r)
    except Exception:
        return None


def ensure_ollama():
    """Start the Ollama server if it isn't running (detached, logged)."""
    if ollama_up():
        return True
    exe = os.path.join(os.environ.get('LOCALAPPDATA', ''),
                       'Programs', 'Ollama', 'ollama.exe')
    if not os.path.exists(exe):
        print('    FAIL: ollama.exe not found at', exe)
        return False
    out = os.path.join(tempfile.gettempdir(), 'ollama_drytest.log')
    subprocess.Popen([exe, 'serve'], stdout=open(out, 'w'),
                     stderr=subprocess.STDOUT, creationflags=0x08000000)
    for _ in range(20):
        time.sleep(1)
        if ollama_up():
            return True
    return False


def report(name, ok, detail=''):
    RESULTS.append(ok)
    print(f'  [{"PASS" if ok else "FAIL"}] {name}' + (f' — {detail}' if detail else ''))
    return ok


# ------------------------------------------------------------------ test 1

def test_1_stt_engine(app):
    """Real Whisper model loads (CUDA or CPU) and transcribes real speech."""
    print('TEST 1: STT engine — Whisper loads and transcribes real audio')
    tmp = os.path.join(tempfile.gettempdir(), 'dictator_t1.wav')
    phrase = 'The quick brown fox jumps over the lazy dog'
    if not synth_speech_16k(phrase, tmp):
        return report('T1', False, 'no Windows TTS voice available to synthesize test audio')
    audio = read_wav_16k(tmp)
    try:
        os.unlink(tmp)
    except OSError:
        pass
    if not audio.size:
        return report('T1', False, 'synthesized audio is empty')
    app._ensure_transcriber()
    if app.transcriber.model is None:
        return report('T1', False, 'model did not load')
    ok_dev = app.transcriber.device in ('CUDA', 'CPU')
    text = app.transcriber.transcribe(audio, app.cfg['vocabulary'],
                                      app.cfg.get('language', 'en'))
    low = (text or '').lower()
    ok_words = all(w in low for w in ('quick', 'fox', 'dog'))
    detail = f'device={app.transcriber.device} text="{text[:60]}"'
    return report('T1', ok_dev and bool(text) and ok_words, detail)


# ------------------------------------------------------------------ test 2

def test_2_cleanup(cfg):
    """Ollama cleanup: model resolves, filler removal works, guard has no false positives."""
    print('TEST 2: cleanup — Ollama model resolves and cleans without rejections')
    model = main.resolve_ollama_model(cfg)
    if model != cfg['ollama_model']:
        return report('T2', False,
                      f'resolved {model!r}, expected configured {cfg["ollama_model"]!r}')
    messy = 'um so I was thinking you know we could uh maybe grab lunch tomorrow okay'
    cleaned = main.ollama_cleanup(messy, cfg, model)
    if not cleaned:
        return report('T2', False, 'cleanup returned None (silent fallback would occur)')
    low = cleaned.lower()
    ok_filler = not any(f in low for f in (' um', ' uh', 'you know', 'um '))
    ok_guard = main.validate_no_hallucinated_words(messy, cleaned, cfg['vocabulary'])
    # guard must not reject real contractions / numbers
    ok_numbers = main.ollama_cleanup(
        'the meeting is at 3pm in the main conference room', cfg, model) is not None
    ok_contr = main.ollama_cleanup(
        'lets connect at noon instead', cfg, model) is not None
    detail = f'model={model} cleaned="{cleaned[:60]}"'
    return report('T2', bool(ok_filler and ok_guard and ok_numbers and ok_contr), detail)


# ------------------------------------------------------------------ test 3

def test_3_pipeline(app):
    """Full pipeline on one real App: audio -> raw -> clean -> typed text."""
    print('TEST 3: pipeline — record-to-type path end to end (injection stubbed)')
    tmp = os.path.join(tempfile.gettempdir(), 'dictator_t3.wav')
    spoken = 'so I was thinking you know we could grab lunch tomorrow'
    if not synth_speech_16k(spoken, tmp):
        return report('T3', False, 'no Windows TTS voice available')
    audio = read_wav_16k(tmp)
    try:
        os.unlink(tmp)
    except OSError:
        pass
    app.process(audio, tone=None, target_hwnd=None, target_exe='drytest.exe')
    drain_ui_q(app)
    if not app.injected:
        return report('T3', False, 'nothing was injected (pipeline died before typing)')
    typed = app.injected[0]
    low = typed.lower()
    ok_raw = bool(app.recorded and app.recorded[0][0])
    ok_content = 'lunch' in low
    ok_fillers_gone = 'you know' not in low and ' um' not in low and ' uh' not in low
    detail = f'typed="{typed[:70]}"'
    return report('T3', bool(ok_raw and ok_content and ok_fillers_gone), detail)


# ------------------------------------------------------------------ test 4

def test_4_lazy_idle(app):
    """Lazy load on first use + auto-unload after 10 min idle + reload on demand."""
    print('TEST 4: lifecycle — lazy load, idle unload, reload on demand')
    if app.transcriber.model is not None:
        return report('T4', False, 'expected Whisper to start unloaded (lazy)')
    app._ensure_transcriber()
    if app.transcriber.model is None or app.transcriber.device == '?':
        return report('T4', False, 'model failed to load on first use')
    app._last_dictation_end = time.time() - 900  # pretend 15 min idle
    app._maybe_unload_transcriber()
    unloaded = app.transcriber.model is None and app.transcriber.device == '?'
    app._ensure_transcriber()                     # reload on demand (next dictation)
    reloaded = app.transcriber.model is not None
    app._last_dictation_end = time.time() - 60    # only 1 min idle
    app._maybe_unload_transcriber()               # must NOT unload
    kept = app.transcriber.model is not None
    app._last_dictation_end = time.time() - 900
    app._maybe_unload_transcriber()               # clean up: unload again
    return report('T4', bool(unloaded and reloaded and kept),
                  f'unloaded={unloaded} reloaded={reloaded} kept_recent={kept}')


# ------------------------------------------------------------------ test 5

def test_5_config_pure(cfg):
    """Config is sane and every pure text-shaping helper behaves."""
    print('TEST 5: config + text shaping — environment and pure logic')
    checks = []

    # model files actually on disk (offline proof, no network)
    small_dir = os.path.join(main.WHISPER_CACHE,
                             'models--Systran--faster-whisper-small.en')
    checks.append(('whisper small.en cached on disk',
                   os.path.isdir(small_dir) and any(
                       f.endswith('.bin') for _, _, f in os.walk(small_dir)
                       for f in f)))
    store = os.environ.get('OLLAMA_MODELS', '')
    man = os.path.join(store, 'manifests', 'registry.ollama.ai', 'library',
                       'qwen2.5', '3b-instruct')
    checks.append(('ollama qwen2.5:3b-instruct manifest present',
                   os.path.isfile(man)))

    # pure logic
    checks.append(('voice command strips and returns tone',
                   main.apply_voice_command('send it to john make it formal') ==
                   ('send it to john', 'formal')))
    checks.append(('instant mode punctuates + fills',
                   main.quick_clean('um okay', cfg) == 'Okay.'))
    checks.append(('filler removal',
                   main.remove_filler_words('um uh you know hi i mean bye') == 'hi bye'))
    checks.append(('spoken formatting',
                   'hello\nworld' in main.apply_commands('hello new line world')))
    checks.append(('snippet expansion',
                   main.expand_snippet('hi there', {'snippets': {'hi there': 'Hello, friend!'}})
                   == 'Hello, friend!'))
    checks.append(('redaction',
                   main.redact('my pin is 1234', ['1234']) == 'my pin is [redacted]'))
    checks.append(('config sane',
                   cfg['model_size'] in ('base.en', 'small.en', 'medium.en')
                   and cfg['enabled'] is True
                   and cfg['ollama_model'] == 'qwen2.5:3b-instruct'
                   and cfg['ollama_timeout_s'] >= 3))

    bad = [name for name, ok in checks if not ok]
    detail = '' if not bad else 'failed: ' + '; '.join(bad)
    return report('T5', not bad, detail)


# ------------------------------------------------------------------ main

def main_run():
    print('=== Dictator dry-run suite ===')
    print('app:', os.path.abspath('main.py'))
    print('time:', time.strftime('%Y-%m-%d %H:%M:%S'))

    if not ensure_ollama():
        print('Ollama is not reachable and could not be started — cleanup tests'
              ' (T2/T3) cannot pass.')
        sys.exit(1)

    cfg = main.load_config()
    print('config: model_size=%s ollama_model=%s device-model-on-disk=%s'
          % (cfg['model_size'], cfg['ollama_model'],
             os.path.isdir(os.path.join(
                 main.WHISPER_CACHE,
                 'models--Systran--faster-whisper-%s.en' % cfg['model_size']))))

    # T5 first (pure, no heavy models), then a shared app for T1/T3 and a
    # second fresh app for T4 (it must observe the lazy pre-load state).
    test_5_config_pure(cfg)
    app_life = make_app()
    test_4_lazy_idle(app_life)
    app_voice = make_app()
    test_1_stt_engine(app_voice)
    test_3_pipeline(app_voice)
    test_2_cleanup(cfg)

    passed = sum(1 for ok in RESULTS if ok)
    print(f'\n=== {passed}/{len(RESULTS)} tests passed ===')
    sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == '__main__':
    main_run()
