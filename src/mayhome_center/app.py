import hmac
import os
import re
import secrets
import time
from collections import OrderedDict
from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from .domain import MODULES, SNAPSHOT_PATTERN, protection, utcnow
from .store import load_status
from .post_boot import display_status, present_monitor

def create_app(config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("MHC_SESSION_KEY") or secrets.token_hex(32),
        AUTH_TOKEN=os.environ.get("MHC_AUTH_TOKEN", ""),
        STATUS_PATH="/run/mayhome-center/status.json",
        TRUSTED_HOSTS=["localhost", "127.0.0.1", "mayhome.cuong.win"],
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=os.environ.get("MHC_COOKIE_SECURE", "1") != "0",
        MAX_CONTENT_LENGTH=4096, PERMANENT_SESSION_LIFETIME=1800,
    )
    if config:
        app.config.update(config)
    if app.config['AUTH_TOKEN'] and not (config and config.get('SECRET_KEY')) and not os.environ.get('MHC_SESSION_KEY'):
        raise RuntimeError('MHC_SESSION_KEY is required when authentication is configured')
    attempts = OrderedDict()

    @app.before_request
    def authorize():
        if request.routing_exception is not None:
            raise request.routing_exception
        if request.path == "/healthz":
            return None
        if request.path == "/login":
            return None
        if request.path.startswith("/static/"):
            return None
        if not app.config["AUTH_TOKEN"]:
            return jsonify(error="AUTH_NOT_CONFIGURED"), 503
        if not session.get("authenticated"):
            if request.path.startswith("/api/") or request.path == "/readyz":
                return jsonify(error="AUTH_REQUIRED"), 401
            return redirect(url_for("login"))
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            abort(405)

    @app.after_request
    def headers(response):
        response.headers.update({
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store", "X-Frame-Options": "DENY",
        })
        return response

    @app.route("/login", methods=["GET", "POST"])
    def login():
        session.setdefault("csrf", secrets.token_urlsafe(32))
        error = None
        if request.method == "POST":
            key = request.remote_addr or "unknown"
            now = time.monotonic()
            count, start = attempts.get(key, (0, now))
            if now - start > 300:
                count, start = 0, now
            if count >= 10:
                return render_template("login.html", error="Thử lại sau 5 phút."), 429
            attempts[key] = (count + 1, start)
            if len(attempts) > 1024:
                attempts.popitem(last=False)
            valid_csrf = hmac.compare_digest(request.form.get("csrf", "").encode(), session["csrf"].encode())
            token = app.config["AUTH_TOKEN"]
            if valid_csrf and token and hmac.compare_digest(request.form.get("token", "").encode(), token.encode()):
                session.clear()
                session["authenticated"] = True
                session.permanent = True
                attempts.pop(key, None)
                return redirect("/")
            error = "Thông tin đăng nhập không hợp lệ."
            return render_template("login.html", error=error), 401
        return render_template("login.html", error=error)

    @app.get("/healthz")
    def health():
        return jsonify(alive=True)

    @app.get("/readyz")
    def ready():
        observed = load_status(app.config["STATUS_PATH"])
        return jsonify(ready=observed.freshness == "fresh", freshness=observed.freshness), (200 if observed.freshness == "fresh" else 503)

    @app.get("/")
    @app.get("/backups")
    @app.get("/logs")
    @app.get("/about")
    def page():
        observed = load_status(app.config["STATUS_PATH"])
        return render_template("dashboard.html", modules=MODULES, data=observed.data,
            errors=observed.errors, freshness=observed.freshness, collected_at=observed.collected_at,
            status=protection(observed.data, observed.freshness), section=request.path, tunnel_status=display_status(observed.data.get("post_boot", {}), observed.freshness),
            tunnel_connectors=present_monitor(observed.data.get("post_boot", {}), observed.freshness).get("connectors", {}))

    def envelope(data, observed, status=200):
        return jsonify(data=data, meta={"collected_at": observed.collected_at,
            "freshness": observed.freshness, "source": "collector-v1"}, errors=observed.errors), status

    @app.get("/api/v1/<path:resource>")
    def api(resource):
        observed = load_status(app.config["STATUS_PATH"])
        data = observed.data
        if resource == "capabilities":
            return envelope({"modules": [m["id"] for m in MODULES], "read_only": True, "post_boot_monitor": True,
                "run_backup": False, "restore": False, "delete": False, "retention_apply": False}, observed)
        allowed = {"overview", "host/post-boot", "backups/status", "backups/jobs", "backups/snapshots", "backups/retention", "backups/storage", "logs"}
        detail = re.fullmatch(r"backups/snapshots/(" + SNAPSHOT_PATTERN + r")", resource)
        if resource not in allowed and not detail:
            abort(404)
        if observed.freshness == "unavailable":
            return envelope({}, observed, 503)
        if resource == "host/post-boot":
            return envelope(present_monitor(data.get("post_boot", {}), observed.freshness), observed)
        if resource == "overview":
            return envelope({**data, "post_boot": present_monitor(data.get("post_boot", {}), observed.freshness), "status": protection(data, observed.freshness), "server_timestamp": utcnow().isoformat()}, observed)
        if detail:
            item = next((x for x in data.get("snapshots", []) if x["id"] == detail[1]), None)
            if item is None:
                abort(404)
            return envelope(item, observed)
        field = {"backups/status": "backup", "backups/retention": "retention", "backups/storage": "storage",
            "backups/jobs": "jobs", "backups/snapshots": "snapshots", "logs": "logs"}[resource]
        result = data.get(field, [] if field in ("jobs", "snapshots", "storage", "logs") else {})
        if field in ("jobs", "snapshots", "logs"):
            try:
                limit = int(request.args.get("limit", 20))
                cursor = int(request.args.get("cursor", 0))
                if not 1 <= limit <= 200 or not 0 <= cursor <= 10000:
                    raise ValueError()
            except ValueError:
                return jsonify(error="INVALID_PAGINATION"), 400
            if field == "logs":
                source = request.args.get("source", "backup")
                if source not in ("backup", "watchdog", "guard", "retention"):
                    return jsonify(error="INVALID_SOURCE"), 400
                result = [x for x in result if x.get("source") == source]
            next_cursor = cursor + limit if cursor + limit < len(result) else None
            result = {"items": result[cursor:cursor + limit], "next_cursor": next_cursor}
        return envelope(result, observed)
    return app
