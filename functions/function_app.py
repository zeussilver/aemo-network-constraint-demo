"""Azure Functions entry point: load new NEMWeb data into the console database hourly, and
keep the console's F1 web app loaded.

Replaces the GitHub Actions schedule, which never fired for this repository. The work
itself is in ingest_job.py and keep_warm.py.
"""
import logging

import azure.functions as func

app = func.FunctionApp()


# NCRONTAB (UTC, with seconds): minute 12 of every hour. SCADA gains 12 files an hour; the
# daily Next Day files (published ~18:15 UTC) are picked up by the 19:12 run.
@app.timer_trigger(schedule="0 12 * * * *", arg_name="timer", run_on_startup=False, use_monitor=True)
def nemweb_ingest(timer: func.TimerRequest) -> None:
    # Imported here so the host starts quickly; pandas and friends load on first run.
    from ingest_job import run_scheduled_ingest

    if timer.past_due:
        logging.warning("NEMWeb ingest timer is running late.")
    results = run_scheduled_ingest()
    logging.info("NEMWeb ingest: %s", ", ".join(f"{r.source}={r.files} files" for r in results))


# Every 10 minutes: F1 unloads the web app after about 20 idle minutes (see keep_warm.py).
@app.timer_trigger(schedule="0 */10 * * * *", arg_name="timer", run_on_startup=False, use_monitor=False)
def console_keep_warm(timer: func.TimerRequest) -> None:
    from keep_warm import health_url, ping_console

    status, seconds = ping_console()
    if status == 200:
        logging.info("Console keep-warm: HTTP 200 in %.1f s", seconds)
    else:
        logging.warning("Console keep-warm: %s answered %s after %.1f s", health_url(), status or "nothing", seconds)
