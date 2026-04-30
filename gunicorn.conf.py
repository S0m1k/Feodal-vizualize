# Gunicorn configuration for Rstone
# Usage: gunicorn main:app -c gunicorn.conf.py

import multiprocessing

bind = "127.0.0.1:8001"
workers = 4
worker_class = "uvicorn.workers.UvicornWorker"
timeout = 400          # AI generation can take up to 5 min
graceful_timeout = 30
keepalive = 5

# Logging
accesslog = "/var/www/your_project/logs/gunicorn-access.log"
errorlog = "/var/www/your_project/logs/gunicorn-error.log"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'
