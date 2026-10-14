"""Préflight sans inférence/réseau, chemins de secours et worker local borné."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import voice_transcribe as voice


def local_model(path: Path, name: str = "tiny") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "model.bin").write_bytes(b"local-model-weights")
    (path / "config.json").write_text(json.dumps({"alignment_heads": [[3 if name == "tiny" else 5, 0]]}), encoding="utf-8")
    (path / "tokenizer.json").write_text("{}", encoding="utf-8")
    (path / "vocabulary.txt").write_text("token\n", encoding="utf-8")
    return path


class VoiceReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.temporary = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.app = self.temporary / "app"
        self.app.mkdir()
        self.project = self.temporary / "project"
        self.project.mkdir()
        self.stack.enter_context(patch.dict(os.environ, {}, clear=True))
        self.stack.enter_context(patch.object(voice, "APP_DIR", self.app))
        self.stack.enter_context(patch("voice_transcribe.Path.cwd", return_value=self.project))
        self.stack.enter_context(patch("voice_transcribe.Path.home", return_value=self.temporary / "home"))
        self.stack.enter_context(patch.object(voice, "_cache_roots", return_value=[]))
        self.factory = Mock(name="WhisperModel")
        self.download = Mock(side_effect=AssertionError("Aucun téléchargement en check"))
        self.package = SimpleNamespace(WhisperModel=self.factory)
        self.utility = SimpleNamespace(download_model=self.download)
        self.importer = self.stack.enter_context(patch("voice_transcribe.importlib.import_module", side_effect=self.import_module))

    def import_module(self, name: str):
        if name == "faster_whisper":
            return self.package
        if name == "faster_whisper.utils":
            return self.utility
        raise ImportError(name)

    def test_ready_checks_dependency_and_assets_without_loading_or_downloading(self) -> None:
        path = local_model(self.app / "models" / "whisper")
        result = voice.is_voice_ready()
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["ready"])
        self.assertTrue(result["download_available"])
        self.assertEqual(result["model_path"], str(path.resolve()))
        self.factory.assert_not_called()
        self.download.assert_not_called()
        self.assertIn("faster_whisper", [call.args[0] for call in self.importer.call_args_list])

    def test_missing_model_is_not_ready_merely_because_download_available(self) -> None:
        result = voice.is_voice_ready()
        self.assertEqual(result["status"], "missing_model")
        self.assertFalse(result["ready"])
        self.assertTrue(result["download_available"])
        self.factory.assert_not_called()
        self.download.assert_not_called()

    def test_missing_or_broken_dependency_is_disabled_without_error_leak(self) -> None:
        local_model(self.app / "models" / "whisper")
        self.importer.side_effect = OSError("native library failed; private-secret")
        result = voice.is_voice_ready()
        self.assertEqual(result["status"], "disabled")
        self.assertFalse(result["ready"])
        self.assertFalse(result["download_available"])
        self.assertIn("faster-whisper", result["reason"])
        self.assertNotIn("private-secret", json.dumps(result))

    def test_disabled_configuration_does_not_import_native_runtime(self) -> None:
        os.environ["NOC_VOICE_ENABLED"] = "false"
        result = voice.is_voice_ready()
        self.assertEqual(result["status"], "disabled")
        self.importer.assert_not_called()

    def test_invalid_configuration_never_loads_or_downloads(self) -> None:
        for name, value in (("NOC_VOICE_ENABLED", "perhaps"), ("NOC_WHISPER_MODEL", "large")):
            with self.subTest(name=name), patch.dict(os.environ, {name: value}, clear=True):
                self.assertEqual(voice.is_voice_ready()["status"], "disabled")
        self.importer.assert_not_called()

    def test_preferred_path_and_missing_preferred_path_fall_back(self) -> None:
        standard = local_model(self.app / "models" / "whisper")
        preferred = local_model(self.temporary / "custom-model", "base")
        os.environ["NOC_WHISPER_MODEL_PATH"] = str(preferred)
        self.assertEqual(voice.is_voice_ready()["model_path"], str(preferred.resolve()))
        self.assertEqual(voice.is_voice_ready()["model"], "base")
        os.environ["NOC_WHISPER_MODEL_PATH"] = str(self.temporary / "missing")
        self.assertEqual(voice.is_voice_ready()["model_path"], str(standard.resolve()))

    def test_legacy_model_and_relative_project_model_supported(self) -> None:
        legacy = local_model(self.app / "models" / "whisper-tiny")
        self.assertEqual(voice.is_voice_ready()["model_path"], str(legacy.resolve()))
        (legacy / "model.bin").unlink()
        project_model = local_model(self.project / "models" / "whisper")
        self.assertEqual(voice.is_voice_ready()["model_path"], str(project_model.resolve()))

    def test_existing_huggingface_snapshot_found_without_remote_request(self) -> None:
        root = self.temporary / "hf-cache"
        repository = root / "models--Systran--faster-whisper-base"
        revision = "a" * 40
        cached = local_model(repository / "snapshots" / revision, "base")
        (repository / "refs").mkdir()
        (repository / "refs" / "main").write_text(revision, encoding="ascii")
        os.environ["NOC_WHISPER_MODEL"] = "base"
        with patch.object(voice, "_cache_roots", return_value=[root]):
            result = voice.is_voice_ready()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["model"], "base")
        self.assertEqual(result["model_path"], str(cached.resolve()))
        self.download.assert_not_called()

    def test_snapshot_reference_cannot_escape_cache(self) -> None:
        repository = self.temporary / "repository"
        (repository / "refs").mkdir(parents=True)
        (repository / "refs" / "main").write_text("../../outside", encoding="ascii")
        self.assertEqual(list(voice._snapshots(repository)), [])

    def test_incomplete_or_invalid_assets_not_reported_ready(self) -> None:
        path = local_model(self.app / "models" / "whisper")
        (path / "tokenizer.json").unlink()
        self.assertEqual(voice.is_voice_ready()["status"], "missing_model")
        (path / "tokenizer.json").write_text("{}", encoding="utf-8")
        (path / "config.json").write_text("not-json", encoding="utf-8")
        self.assertEqual(voice.is_voice_ready()["status"], "missing_model")
        (path / "config.json").write_text("{}", encoding="utf-8")
        (path / "model.bin").write_bytes(b"")
        self.assertEqual(voice.is_voice_ready()["status"], "missing_model")

    def test_actual_file_open_failure_not_hidden_by_stat(self) -> None:
        path = local_model(self.app / "models" / "whisper")
        original_open = Path.open
        def denied(candidate, *args, **kwargs):
            if candidate == path / "model.bin":
                raise PermissionError("Denied")
            return original_open(candidate, *args, **kwargs)
        with patch.object(Path, "open", denied):
            self.assertEqual(voice.is_voice_ready()["status"], "missing_model")

    def test_incompatible_heavy_model_not_loaded(self) -> None:
        path = local_model(self.app / "models" / "whisper")
        (path / "config.json").write_text('{"alignment_heads": [[11, 0]]}', encoding="utf-8")
        self.assertEqual(voice.is_voice_ready()["status"], "missing_model")
        self.factory.assert_not_called()

    def test_marker_exposes_actual_installed_model(self) -> None:
        path = local_model(self.app / "models" / "whisper")
        (path / ".noc-whisper-model.json").write_text('{"model":"base"}', encoding="utf-8")
        self.assertEqual(voice.is_voice_ready()["model"], "base")
        (path / ".noc-whisper-model.json").write_text('{"model":"large"}', encoding="utf-8")
        self.assertEqual(voice.is_voice_ready()["status"], "missing_model")

    def test_transcription_uses_local_int8_french_vad_and_no_download(self) -> None:
        path = local_model(self.app / "models" / "whisper")
        audio = self.temporary / "note.wav"
        audio.write_bytes(b"RIFFtest")
        engine = Mock()
        engine.transcribe.return_value = (iter([SimpleNamespace(text=" statut "), SimpleNamespace(text=" des services ")]), None)
        self.factory.return_value = engine
        self.assertEqual(voice.transcribe_audio(audio), "statut des services")
        arguments = self.factory.call_args.kwargs
        self.assertEqual(self.factory.call_args.args[0], str(path.resolve()))
        self.assertEqual((arguments["device"], arguments["compute_type"], arguments["cpu_threads"]), ("cpu", "int8", 2))
        self.assertTrue(arguments["local_files_only"])
        self.assertTrue(engine.transcribe.call_args.kwargs["vad_filter"])
        self.assertEqual(engine.transcribe.call_args.kwargs["language"], "fr")
        self.download.assert_not_called()

    def test_no_model_or_missing_audio_never_constructs_engine(self) -> None:
        with self.assertRaises(ValueError):
            voice.transcribe_audio(self.temporary / "absent.wav")
        audio = self.temporary / "note.wav"
        audio.write_bytes(b"RIFFtest")
        with self.assertRaises(RuntimeError):
            voice.transcribe_audio(audio)
        self.factory.assert_not_called()

    def test_transcription_empty_or_overlong_rejected(self) -> None:
        local_model(self.app / "models" / "whisper")
        audio = self.temporary / "note.wav"
        audio.write_bytes(b"RIFFtest")
        for text in (" ", "x" * 1001):
            engine = Mock()
            engine.transcribe.return_value = (iter([SimpleNamespace(text=text)]), None)
            self.factory.return_value = engine
            with self.subTest(text=text[:20]), self.assertRaises(ValueError):
                voice.transcribe_audio(audio)

    def test_check_cli_needs_no_audio_and_keeps_explicit_model_name(self) -> None:
        local_model(self.app / "models" / "whisper", "base")
        output = io.StringIO()
        with patch.object(sys, "argv", ["voice_transcribe.py", "--check", "--model-name", "base"]), contextlib.redirect_stdout(output):
            self.assertEqual(voice.main(), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["model"], "base")
        self.factory.assert_not_called()
        self.download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
