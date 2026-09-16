# Pinned on purpose. Railway's own builder picks a Python version for you and can change that
# choice when they update it — which is how a build that worked in September fails in March
# with nothing in the diff. This is the version the tests run against.
FROM python:3.12-slim

# Python behaves better in a container this way: no .pyc clutter on a read-only-ish layer, and
# unbuffered output so logs appear as they happen rather than when the buffer fills.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, in their own layer. Application code changes on every deploy; this file
# rarely does, so Docker reuses the installed layer and the build takes seconds.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Not root. A container escape is harder to turn into anything if the process inside was never
# privileged to begin with.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

# Railway injects PORT; 5001 keeps `docker run` working locally without one.
ENV PORT=5001
EXPOSE 5001

# One web worker on purpose: flask-limiter's counters live in this process's memory, so a
# second one doubles every rate limit. Threads handle concurrency instead. Raising this means
# pointing RATELIMIT_STORAGE_URI at Redis first.
#
# The worker service overrides this with: flask --app app tailoring-worker
# `exec` so gunicorn replaces the shell and becomes PID 1 — otherwise SIGTERM reaches the
# shell, gunicorn never hears it, and every deploy ends in a SIGKILL ten seconds later. The
# worker relies on the same thing to finish its run before exiting.
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT:-5001} --workers 1 --threads 4 --timeout 120 app:app"]
