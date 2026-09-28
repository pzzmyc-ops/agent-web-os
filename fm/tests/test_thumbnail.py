import os
import tempfile
import unittest
from unittest.mock import patch

from fm.backend import api


class ThumbnailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = os.path.join(self.temp.name, "clip.mp4")
        with open(self.source, "wb") as file:
            file.write(b"video")

    def test_video_thumbnail_renders_one_frame(self):
        target = os.path.join(self.temp.name, "thumb.png")
        with patch.object(api.subprocess, "run") as run:
            api._render_thumbnail(self.source, target)
        command = run.call_args.args[0]
        self.assertIn("-frames:v", command)
        self.assertEqual(command[command.index("-frames:v") + 1], "1")
        self.assertIn("scale=256:256:force_original_aspect_ratio=decrease", command)
        self.assertTrue(run.call_args.kwargs["check"])

    def test_thumbnail_reuses_cached_file(self):
        cache = os.path.join(self.temp.name, "cache")

        def render(source, target):
            with open(target, "wb") as file:
                file.write(b"thumbnail")

        with patch.object(api, "_THUMBNAIL_DIR", cache), patch.object(api, "resolve", return_value=self.source), patch.object(api, "_render_thumbnail", side_effect=render) as renderer:
            first = api.thumbnail("/clip.mp4")
            second = api.thumbnail("/clip.mp4")
        self.assertEqual(first.path, second.path)
        self.assertEqual(renderer.call_count, 1)

    def test_thumbnail_cache_changes_with_source(self):
        cache = os.path.join(self.temp.name, "cache")

        def render(source, target):
            with open(target, "wb") as file:
                file.write(b"thumbnail")

        with patch.object(api, "_THUMBNAIL_DIR", cache), patch.object(api, "resolve", return_value=self.source), patch.object(api, "_render_thumbnail", side_effect=render):
            first = api.thumbnail("/clip.mp4")
            with open(self.source, "ab") as file:
                file.write(b"changed")
            second = api.thumbnail("/clip.mp4")
        self.assertNotEqual(first.path, second.path)

    def test_thumbnail_rejects_unsupported_files(self):
        source = os.path.join(self.temp.name, "data.bin")
        with open(source, "wb") as file:
            file.write(b"data")
        with patch.object(api, "resolve", return_value=source):
            with self.assertRaises(ValueError):
                api.thumbnail("/data.bin")


if __name__ == "__main__":
    unittest.main()
