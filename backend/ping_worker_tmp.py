from app.workers.celery_app import celery_app
print("BROKER:", celery_app.conf.broker_url)
try:
    workers = celery_app.control.inspect(timeout=3).ping() or {}
    print("STDOUT online" if any(workers.values()) else "STDOUT offline")
    print("WORKERS:", workers)
except Exception as e:
    import traceback
    traceback.print_exc()
