"""Job status views — the UX side of the async AI worker.

When a route enqueues an AI job (via shield.tasks.enqueue_ai), it redirects
the browser to /jobs/<id>/wait. That page mounts an HTMX poll against
/jobs/<id>/status-fragment, which returns a small HTML snippet every
~2 s. When the job finishes the fragment response includes an
`HX-Redirect` header so HTMX navigates the browser to the configured
`next_url`. Failed jobs render the error inline.

The JSON /jobs/<id>/status endpoint is preserved for non-HTMX clients
(curl, tests, the dev-agent).
"""
from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import login_required

from .rbac import admin_or_reviewer

bp = Blueprint("jobs", __name__, template_folder="../templates/spine")


def _fetch_job(job_id: str):
    """Look up the rq.Job by id; return None if not found."""
    from rq.exceptions import NoSuchJobError
    from rq.job import Job

    from ..tasks import _redis_conn
    try:
        return Job.fetch(job_id, connection=_redis_conn())
    except NoSuchJobError:
        return None


@bp.route("/")
@login_required
@admin_or_reviewer
def index():
    """Admin job-observability listing: queued / running / failed / finished.

    Useful when an AI call appears stuck or a worker is misbehaving —
    the failed-job traceback shows up here. Admin + reviewer only.
    """
    from rq import Queue, Worker
    from rq.job import Job
    from rq.registry import (
        FailedJobRegistry,
        FinishedJobRegistry,
        StartedJobRegistry,
    )

    from ..tasks import QUEUE_NAME, _redis_conn

    conn = _redis_conn()
    q = Queue(QUEUE_NAME, connection=conn)

    def _fetch_all(ids: list[str]) -> list[Job]:
        out: list[Job] = []
        for jid in ids:
            try:
                out.append(Job.fetch(jid, connection=conn))
            except Exception as e:  # noqa: BLE001
                # A job in the registry can be missing payload if its
                # TTL expired or it was force-deleted; skip it but log
                # so an op investigating a gap can see why.
                from flask import current_app
                current_app.logger.warning("jobs.index: skipping %r (%s)", jid, e)
        return out

    queued   = q.get_jobs()
    started  = _fetch_all(StartedJobRegistry(QUEUE_NAME, connection=conn).get_job_ids())
    failed   = _fetch_all(FailedJobRegistry(QUEUE_NAME, connection=conn).get_job_ids())
    finished = _fetch_all(FinishedJobRegistry(QUEUE_NAME, connection=conn).get_job_ids()[:25])
    workers  = Worker.all(connection=conn, queue=q)

    return render_template(
        "spine/jobs_index.html",
        queued=queued, started=started, failed=failed,
        finished=finished, workers=workers,
        queue_name=QUEUE_NAME,
    )


@bp.route("/<job_id>/status")
@login_required
def status(job_id: str):
    """JSON status: {status, result, error}. Used by tests and curl."""
    job = _fetch_job(job_id)
    if job is None:
        return jsonify({"status": "unknown"}), 404
    return jsonify({
        "status": job.get_status(refresh=True),
        "result": job.result,
        "error": str(job.exc_info) if job.is_failed else None,
    })


@bp.route("/<job_id>/status-fragment")
@login_required
def status_fragment(job_id: str):
    """HTMX-friendly HTML fragment of the job's current status.

    When the job is `finished`, the response includes an `HX-Redirect`
    header so HTMX navigates the browser to `next` (the URL passed via
    the `next` query parameter). Otherwise returns a small status block
    that the wait page swaps in via `hx-swap=outerHTML`.
    """
    job = _fetch_job(job_id)
    if job is None:
        abort(404)
    next_url = request.args.get("next") or url_for("home")
    job_status = job.get_status(refresh=True)

    if job_status == "finished":
        # HTMX honors HX-Redirect by navigating the top-level browser.
        resp = make_response("", 200)
        resp.headers["HX-Redirect"] = next_url
        return resp

    return render_template(
        "spine/_job_status_fragment.html",
        job_id=job.id, status=job_status, next_url=next_url,
        error=(str(job.exc_info) if job.is_failed else None),
    )


@bp.route("/<job_id>/wait")
@login_required
def wait(job_id: str):
    """The wait page itself — renders once, then HTMX takes over polling.

    If a non-HTMX client hits this and the job is already finished, we
    still 302 to next_url so plain-browser users without JS aren't stuck.
    """
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
