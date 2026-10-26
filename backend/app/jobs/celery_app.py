"""Flask-aware Celery entry point for Soultie Notifications.

Importing the application does not start workers or scheduling.
"""

from celery import Celery
from app import create_app


def create_notification_celery(flask_app=None):
    if flask_app is None:
        flask_app = create_app()

    celery = Celery(
        "xuoroni_notifications",
        broker=flask_app.config["CELERY_BROKER_URL"],
    )

    celery.conf.update(
        task_serializer="json",
        accept_content=["json"],
        task_ignore_result=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_time_limit=40,
        task_soft_time_limit=35,
        task_always_eager=False,
        broker_connection_retry_on_startup=True,
        beat_schedule={},
        imports=("app.jobs.notification_tasks",),
    )

    class FlaskContextTask(celery.Task):
        abstract = True

        def __call__(self, *args, **kwargs):
            with flask_app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = FlaskContextTask

    if flask_app.config.get("NOTIFICATION_CELERY_BEAT_ENABLED") is True:
        celery.conf.beat_schedule = {
            "xuoroni-notifications-process-due": {
                "task": "xuoroni.notifications.process_due",
                "schedule": 60.0,
                "args": (1,),
            },
            "xuoroni-notifications-reconcile": {
                "task": "xuoroni.notifications.reconcile_page",
                "schedule": 300.0,
            },
        }

    return celery


celery_app = create_notification_celery()
