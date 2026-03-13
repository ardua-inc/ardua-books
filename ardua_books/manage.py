#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    try:
        from dotenv import load_dotenv
        # The .env file is located at the root of the project, one level up from this file,
        # or inside this directory. Based on the user's comment, it's under `ardua_books`.
        load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
    except ImportError:
        pass

    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ardua_books.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
