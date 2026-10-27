#!/usr/bin/env python
"""Django's command-line entry point: runserver, check, collectstatic, etc."""
import os
import sys


if __name__ == "__main__":
    # Running the development server is an explicit development opt-in. WSGI,
    # Gunicorn and other commands default to production settings instead.
    if len(sys.argv) > 1 and sys.argv[1] == "runserver":
        os.environ.setdefault("DJANGO_DEBUG", "true")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gathering.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)
