import os
import stat
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fm.backend.fileop import create_job, same_volume, volume_label
from fm.backend.pathutil import IS_WINDOWS, to_fs


class FileOpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.src = os.path.join(self.root, "src")
        self.dst = os.path.join(self.root, "dst")
        os.makedirs(self.src)
        os.makedirs(self.dst)
        self.write(os.path.join(self.src, "a.txt"), "hello")
        self.write(os.path.join(self.src, "sub", "b.txt"), "world")

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as file:
            file.write(text)

    def read(self, path):
        with open(path, "r", encoding="utf-8") as file:
            return file.read()

    def fs(self, path):
        return to_fs(path)

    def test_copy_counts_files_then_copies_each_one(self):
        job = create_job("copy", [self.fs(self.src)], self.fs(self.dst))
        job.run()
        self.assertEqual(job.status, "success")
        self.assertEqual(job.total_files, 2)
        self.assertEqual(job.total_bytes, len("hello") + len("world"))
        self.assertEqual(job.public()["percent"], 1)
        self.assertEqual(self.read(os.path.join(self.dst, "src", "a.txt")), "hello")
        self.assertEqual(self.read(os.path.join(self.dst, "src", "sub", "b.txt")), "world")
        self.assertEqual(self.read(os.path.join(self.src, "a.txt")), "hello")

    def test_move_removes_source_after_each_file(self):
        job = create_job("move", [self.fs(self.src)], self.fs(self.dst))
        job.run()
        self.assertEqual(job.status, "success")
        self.assertEqual(self.read(os.path.join(self.dst, "src", "a.txt")), "hello")
        self.assertFalse(os.path.exists(self.src))

    def test_delete_removes_files_then_directories(self):
        job = create_job("delete", [self.fs(self.src)])
        job.run()
        self.assertEqual(job.status, "success")
        self.assertEqual(job.total_files, 2)
        self.assertFalse(os.path.exists(self.src))

    def test_existing_target_is_rejected_before_copy(self):
        os.makedirs(os.path.join(self.dst, "src"))
        with self.assertRaises(FileExistsError):
            create_job("copy", [self.fs(self.src)], self.fs(self.dst))
        self.assertFalse(os.path.exists(os.path.join(self.dst, "src", "a.txt")))

    def test_cannot_move_folder_into_itself(self):
        with self.assertRaises(ValueError):
            create_job("move", [self.fs(self.src)], self.fs(os.path.join(self.src, "sub")))

    def test_cancel_before_copy_leaves_source(self):
        job = create_job("copy", [self.fs(self.src)], self.fs(self.dst))
        job.cancel = True
        job.run()
        self.assertEqual(job.status, "cancelled")
        self.assertFalse(os.path.exists(os.path.join(self.dst, "src", "a.txt")))
        self.assertTrue(os.path.exists(os.path.join(self.src, "a.txt")))

    def test_pause_blocks_until_resume(self):
        job = create_job("copy", [self.fs(self.src)], self.fs(self.dst))
        job.pause_event.clear()
        thread = threading.Thread(target=job.run)
        thread.start()
        self.addCleanup(lambda: (job.pause_event.set(), thread.join(5)))
        deadline = time.time() + 3
        while time.time() < deadline and job.status != "paused":
            time.sleep(0.02)
        self.assertEqual(job.status, "paused")
        self.assertFalse(os.path.exists(os.path.join(self.dst, "src", "a.txt")))
        job.request_resume()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(job.status, "success")
        self.assertEqual(self.read(os.path.join(self.dst, "src", "sub", "b.txt")), "world")

    def test_cross_volume_move_copies_then_deletes_source(self):
        with patch("fm.backend.fileop.same_volume", return_value=False):
            job = create_job("move", [self.fs(self.src)], self.fs(self.dst))
            job.run()
        self.assertEqual(job.status, "success", job.error)
        self.assertEqual(self.read(os.path.join(self.dst, "src", "a.txt")), "hello")
        self.assertFalse(os.path.exists(self.src))

    def test_same_volume_for_local_paths(self):
        self.assertTrue(same_volume(self.src, self.dst))

    def test_duplicate_names_are_rejected_before_copy(self):
        other = os.path.join(self.root, "other")
        self.write(os.path.join(other, "a.txt"), "x")
        with self.assertRaises(FileExistsError):
            create_job("copy", [self.fs(os.path.join(self.src, "a.txt")), self.fs(os.path.join(other, "a.txt"))], self.fs(self.dst))
        self.assertFalse(os.path.exists(os.path.join(self.dst, "a.txt")))

    def test_move_empty_folder(self):
        empty = os.path.join(self.root, "empty")
        os.makedirs(empty)
        job = create_job("move", [self.fs(empty)], self.fs(self.dst))
        job.run()
        self.assertEqual(job.status, "success", job.error)
        self.assertFalse(os.path.exists(empty))
        self.assertTrue(os.path.isdir(os.path.join(self.dst, "empty")))

    def test_delete_readonly_file(self):
        path = os.path.join(self.root, "ro.txt")
        self.write(path, "x")
        os.chmod(path, stat.S_IREAD)
        job = create_job("delete", [self.fs(path)])
        job.run()
        self.assertEqual(job.status, "success", job.error)
        self.assertFalse(os.path.exists(path))

    def test_root_cannot_be_deleted(self):
        if not IS_WINDOWS:
            with self.assertRaises(ValueError):
                create_job("delete", ["/"])
            return
        drive = os.path.splitdrive(os.path.abspath(self.root))[0] + "\\"
        with self.assertRaises(ValueError):
            create_job("delete", [to_fs(drive)])

    @unittest.skipUnless(IS_WINDOWS, "volume label is a Windows path")
    def test_volume_label_contains_drive_letter(self):
        letter = os.path.splitdrive(os.path.abspath(self.root))[0][0]
        self.assertIn("(" + letter.upper() + ":)", volume_label(letter))
