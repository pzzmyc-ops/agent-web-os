import os
import stat
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fm.backend.fileop import STORE, SpeedCurve, create_job, same_volume, volume_label
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

    def run_with_policy(self, job, policy):
        thread = threading.Thread(target=job.run)
        thread.start()
        deadline = time.time() + 3
        while time.time() < deadline and job.status != "waiting":
            time.sleep(0.02)
        self.assertEqual(job.status, "waiting")
        job.request_policy(policy)
        thread.join(5)
        self.assertFalse(thread.is_alive())
        return job

    def test_conflicts_copy_others_first_then_wait(self):
        self.write(os.path.join(self.dst, "src", "a.txt"), "old")
        job = create_job("copy", [self.fs(self.src)], self.fs(self.dst))
        thread = threading.Thread(target=job.run)
        thread.start()
        deadline = time.time() + 3
        while time.time() < deadline and job.status != "waiting":
            time.sleep(0.02)
        self.assertEqual(job.status, "waiting")
        self.assertEqual(job.public()["conflictCount"], 1)
        self.assertTrue(job.public()["needDecision"])
        self.assertEqual(self.read(os.path.join(self.dst, "src", "sub", "b.txt")), "world")
        self.assertEqual(self.read(os.path.join(self.dst, "src", "a.txt")), "old")
        job.request_policy("skip")
        thread.join(5)
        self.assertEqual(job.status, "success", job.error)
        self.assertEqual(self.read(os.path.join(self.dst, "src", "a.txt")), "old")
        self.assertEqual(job.public()["percent"], 1)
        self.assertEqual(job.public()["doneFiles"], 2)

    def test_overwrite_replaces_existing_file(self):
        self.write(os.path.join(self.dst, "src", "a.txt"), "old")
        job = self.run_with_policy(create_job("copy", [self.fs(self.src)], self.fs(self.dst)), "overwrite")
        self.assertEqual(job.status, "success", job.error)
        self.assertEqual(self.read(os.path.join(self.dst, "src", "a.txt")), "hello")

    def test_keep_both_renames_new_file(self):
        self.write(os.path.join(self.dst, "src", "a.txt"), "old")
        job = self.run_with_policy(create_job("copy", [self.fs(self.src)], self.fs(self.dst)), "keep")
        self.assertEqual(job.status, "success", job.error)
        self.assertEqual(self.read(os.path.join(self.dst, "src", "a.txt")), "old")
        self.assertEqual(self.read(os.path.join(self.dst, "src", "a (2).txt")), "hello")

    def test_move_skip_leaves_source_and_folder(self):
        self.write(os.path.join(self.dst, "src", "a.txt"), "old")
        job = self.run_with_policy(create_job("move", [self.fs(self.src)], self.fs(self.dst)), "skip")
        self.assertEqual(job.status, "success", job.error)
        self.assertEqual(self.read(os.path.join(self.src, "a.txt")), "hello")
        self.assertFalse(os.path.exists(os.path.join(self.src, "sub")))
        self.assertEqual(self.read(os.path.join(self.dst, "src", "sub", "b.txt")), "world")

    def test_move_overwrite_replaces_and_removes_source(self):
        self.write(os.path.join(self.dst, "src", "a.txt"), "old")
        job = self.run_with_policy(create_job("move", [self.fs(self.src)], self.fs(self.dst)), "overwrite")
        self.assertEqual(job.status, "success", job.error)
        self.assertEqual(self.read(os.path.join(self.dst, "src", "a.txt")), "hello")
        self.assertFalse(os.path.exists(self.src))

    def test_copy_into_same_folder_makes_copy_name(self):
        job = create_job("copy", [self.fs(os.path.join(self.src, "a.txt"))], self.fs(self.src))
        job.run()
        self.assertEqual(job.status, "success", job.error)
        self.assertEqual(self.read(os.path.join(self.src, "a - 副本.txt")), "hello")
        self.assertEqual(self.read(os.path.join(self.src, "a.txt")), "hello")

    def test_file_versus_folder_conflict_is_rejected(self):
        self.write(os.path.join(self.dst, "src"), "x")
        with self.assertRaises(FileExistsError):
            create_job("copy", [self.fs(self.src)], self.fs(self.dst))

    def test_cancel_while_waiting_for_decision(self):
        self.write(os.path.join(self.dst, "src", "a.txt"), "old")
        job = create_job("copy", [self.fs(self.src)], self.fs(self.dst))
        thread = threading.Thread(target=job.run)
        thread.start()
        deadline = time.time() + 3
        while time.time() < deadline and job.status != "waiting":
            time.sleep(0.02)
        job.request_cancel()
        thread.join(5)
        self.assertEqual(job.status, "cancelled")
        self.assertEqual(self.read(os.path.join(self.dst, "src", "a.txt")), "old")

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

    def test_paused_job_keeps_chart_for_restore(self):
        job = create_job("copy", [self.fs(self.src)], self.fs(self.dst))
        job.phase = "run"
        job.total_bytes = 1000
        job.status = "running"
        for _ in range(6):
            job._add_bytes(100, "a.txt")
            time.sleep(0.3)
        job.last_eta = 40
        job.status = "paused"
        snap = job.public(chart=True)
        self.assertEqual(snap["status"], "paused")
        self.assertEqual(snap["etaSeconds"], 40)
        self.assertGreater(len(snap["chart"]), 0)
        self.assertTrue(snap["refreshPaths"])
        STORE.jobs[job.id] = job
        STORE.order.append(job.id)
        self.addCleanup(lambda: (STORE.jobs.pop(job.id, None), STORE.order.remove(job.id) if job.id in STORE.order else None))
        listed = STORE.active()
        self.assertEqual(listed[0]["id"], job.id)
        self.assertGreater(len(listed[0]["chart"]), 0)

    def test_speed_curve_waits_then_uses_slope(self):
        curve = SpeedCurve()
        for index in range(5):
            curve.add(index * 0.5, index * 500)
        self.assertIsNone(curve.eta_seconds(10000))
        self.assertGreater(curve.display_speed(), 0)
        steady = SpeedCurve()
        for index in range(20):
            steady.add(index * 0.5, index * 500)
        eta = steady.eta_seconds(5000)
        self.assertIsNotNone(eta)
        self.assertAlmostEqual(eta, 5.0, delta=0.6)

    def test_sparse_samples_still_have_speed(self):
        curve = SpeedCurve()
        curve.add(0.0, 0)
        curve.add(2.0, 2 * 1024 * 1024)
        self.assertGreater(curve.current_speed(), 0)

    def test_dense_chunks_keep_speed_and_eta(self):
        curve = SpeedCurve()
        done = 0
        for index in range(600):
            done += 1024 * 1024
            curve.add(index * 0.01, done)
        self.assertGreater(len(curve.points), 2)
        self.assertGreater(curve.display_speed(), 0)
        self.assertGreater(curve.current_speed(), 0)
        eta = curve.eta_seconds(1024 * 1024 * 1024)
        self.assertIsNotNone(eta)
        self.assertGreater(eta, 0)

    def test_dense_job_writes_report_speed(self):
        job = create_job("copy", [self.fs(os.path.join(self.src, "a.txt"))], self.fs(self.dst))
        job.phase = "run"
        job.status = "running"
        job.total_bytes = 64 * 1024 * 1024
        for _ in range(60):
            job._add_bytes(256 * 1024, "a.bin")
            time.sleep(0.02)
        snap = job.public(chart=True)
        self.assertGreater(snap["speed"], 0)
        self.assertGreater(len(snap["chart"]), 0)

    def test_eta_appears_after_history_even_when_rate_changes(self):
        curve = SpeedCurve()
        done = 0
        for index in range(20):
            done += 100 if index < 14 else 2000
            curve.add(index * 0.5, done)
        eta = curve.eta_seconds(10000)
        self.assertIsNotNone(eta)
        self.assertGreater(eta, 0)

    def test_slow_chunks_still_draw_chart(self):
        job = create_job("copy", [self.fs(os.path.join(self.src, "a.txt"))], self.fs(self.dst))
        job.phase = "run"
        job.status = "running"
        job.total_bytes = 4 * 1024 * 1024
        job._add_bytes(1024 * 1024, "a.bin")
        time.sleep(1.2)
        job._add_bytes(1024 * 1024, "a.bin")
        snap = job.public(chart=True)
        self.assertGreater(snap["speed"], 0)
        self.assertGreater(snap["chart"][-1]["to"], 0)

    def test_chart_keeps_fast_and_slow_segments(self):
        job = create_job("copy", [self.fs(os.path.join(self.src, "a.txt"))], self.fs(self.dst))
        job.phase = "run"
        job.status = "running"
        job.total_bytes = 200 * 1024 * 1024
        job._add_bytes(40 * 1024 * 1024, "a.bin")
        time.sleep(0.3)
        job._add_bytes(40 * 1024 * 1024, "a.bin")
        time.sleep(0.4)
        job._add_bytes(256 * 1024, "a.bin")
        time.sleep(1.2)
        job._add_bytes(256 * 1024, "a.bin")
        chart = job.public(chart=True)["chart"]
        speeds = [item["speed"] for item in chart]
        self.assertGreater(len(speeds), 1)
        self.assertGreater(max(speeds), min(speeds) * 4)

    def test_speed_curve_reset_drops_history(self):
        curve = SpeedCurve()
        for index in range(20):
            curve.add(float(index), float(index * 1000))
        curve.reset()
        curve.add(100.0, 0)
        curve.add(101.0, 1000)
        self.assertIsNone(curve.eta_seconds(1000))

    @unittest.skipUnless(IS_WINDOWS, "volume label is a Windows path")
    def test_volume_label_contains_drive_letter(self):
        letter = os.path.splitdrive(os.path.abspath(self.root))[0][0]
        self.assertIn("(" + letter.upper() + ":)", volume_label(letter))
