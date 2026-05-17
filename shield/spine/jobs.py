"""Job status views — the UX side of the async AI worker.

When a route enqueues an AI job (via shield.tasks.enqueue_ai), it redirects
the browser to /jobs/<id>/wait. That page polls every 2 s and redirects to
the configured `next_url` once the job finishes. Failed jobs render the
error inline.

The job-status JSON endpoint is also useful for HTMX or external pollers.
"""
from __future__ import annotations

from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

bp = Blueprint("jobs", __name__, template_folder="../templates/spine")


def _fetch_job(job_id: str):
    """Look up the rq.Job by id; return None if not found."""
    from rq.job import Job
    from rq.exceptions import NoSuchJobError
    from ..tasks import _redis_conn
    try:
        return Job.fetch(job_id, connection=_redis_conn())
    except NoSuchJobError:
        return None


@bp.route("/<job_id>/status")
@login_required
def status(job_id: str):
    """JSON status: {status, result, error} for the wait page poll."""
    job = _fetch_job(job_id)
    if job is None:
        return jsonify({"status": "unknown"}), 404
    return jsonify({
        "status": job.get_status(refresh=True),
        "result": job.result,
        "error": str(job.exc_info) if job.is_failed else None,
    })


@bp.route("/<job_id>/wait")
@login_required
def wait(job_id: str):
    """Poll the job and redirect once done."""
    job = _fetch_job(job_id)
    if job is None:
        abort(404)
    next_url = request.args.get("next") or url_for("home")
    job_status = job.get_status(refresh=True)
    if job_status == "finished":
        return redirect(next_url)
    return render_template(
        "spine/job_wait.html",
        job_id=job.id, status=job_status, next_url=next_url,
        error=(str(job.exc_info) if job.is_failed else None),
    )
