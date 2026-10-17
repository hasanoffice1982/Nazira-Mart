"""Gunicorn settings tuned for a small shared VPS (2 vCPU / ~4 GB RAM shared with Postgres, Nginx and other apps)."""
import os

# Gunicorn creates this unix socket itself. systemd makes /run/narizmart (RuntimeDirectory) owned by the app user with
# group www-data, and umask 007 gives the socket mode 0660, so Nginx (www-data) can connect and nobody else can.
bind = os.environ.get("GUNICORN_BIND", "unix:/run/narizmart/gunicorn.sock")
umask = 0o007
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))  # raise to 3-4 on a dedicated server
threads = 2
timeout = 30
graceful_timeout = 30
max_requests = 1000  # recycle workers to cap slow memory growth
max_requests_jitter = 100
forwarded_allow_ips = "*"  # only Nginx can reach the unix socket; it sets X-Forwarded-For/Proto itself
control_socket_disable = True  # the admin control socket would live in the app user's home, which the service sandbox hides
accesslog = "-"
errorlog = "-"
capture_output = True
worker_tmp_dir = "/dev/shm" if os.path.isdir("/dev/shm") else None  # RAM-backed heartbeat files on Linux
