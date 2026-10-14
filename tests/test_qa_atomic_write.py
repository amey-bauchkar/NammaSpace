"""Pre-capture QA: atomic JSON writes must survive a reader holding the file open (Windows sharing violation).

Found by the real Studio journey (QA-04): the capture check died with PermissionError [WinError 5] when the
server polled ingest_status.json at the moment the worker replaced it.
"""
import threading
import time

from namma.core.jsonio import load_strict, write_json


def test_write_json_retries_while_a_reader_holds_the_file(tmp_path):
    p = tmp_path / "status.json"
    write_json(p, {"n": 0})
    opened = threading.Event()

    def reader():
        with open(p, "rb"):              # on Windows this blocks os.replace (no FILE_SHARE_DELETE)
            opened.set()
            time.sleep(0.4)

    t = threading.Thread(target=reader)
    t.start()
    opened.wait(2)
    write_json(p, {"n": 1})               # must not raise; waits for the reader
    t.join()
    assert load_strict(p) == {"n": 1}
    assert not list(tmp_path.glob(".status.json.*.tmp"))


def test_reading_while_another_thread_keeps_replacing_never_fails(tmp_path):
    # pre-capture QA (R150): the Studio answered a run-status poll with HTTP 500 during an 84-min run; on Windows,
    # opening a file at the instant it is being replaced can fail with PermissionError
    p = tmp_path / "run.json"
    write_json(p, {"n": 0})
    stop = threading.Event()

    def writer():
        k = 0
        while not stop.is_set():
            k += 1
            write_json(p, {"n": k, "pad": "x" * 2000})

    t = threading.Thread(target=writer)
    t.start()
    errors = []
    try:
        for _ in range(3000):
            try:
                assert "n" in load_strict(p)
            except Exception as e:     # noqa: BLE001 - counted, reported below
                errors.append(repr(e))
    finally:
        stop.set()
        t.join()
    assert not errors, f"{len(errors)} failed reads, e.g. {errors[0]}"
