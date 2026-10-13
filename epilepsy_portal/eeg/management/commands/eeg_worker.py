import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from eeg.queue import claim_next, cleanup_waveform_previews, db_retry, recover_interrupted, run_job
from eeg.storage import locked


class Command(BaseCommand):
    help = "Run the private EEG preview or analysis queue."

    def add_arguments(self, parser):
        parser.add_argument("--lane", choices=["preview", "analysis"], required=True)
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        lane = options["lane"]
        try:
            with locked("worker-" + lane, blocking=False):
                db_retry(lambda: recover_interrupted(lane))
                last_cleanup = 0
                while True:
                    close_old_connections()
                    if lane == "preview" and time.monotonic() - last_cleanup > 3600:
                        db_retry(cleanup_waveform_previews)
                        last_cleanup = time.monotonic()
                    job = db_retry(lambda: claim_next(lane))
                    if job is not None:
                        run_job(job)
                    elif not options["once"]:
                        time.sleep(1)
                    if options["once"]:
                        break
        except BlockingIOError as exc:
            raise CommandError("该队列已有工作进程在运行。") from exc
