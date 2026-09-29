"""Run separately: python -m backend.worker [--watch]. Nothing starts on import."""
import argparse
import time
import os
from dotenv import load_dotenv
from .db import ROOT, init, setting
from .pipeline import work_once
from .discovery import refresh
from .notifications import dispatch


def main():
    load_dotenv(ROOT / '.env')
    parser = argparse.ArgumentParser()
    parser.add_argument('--watch', action='store_true', help='Poll public repositories; explicitly opt in')
    args = parser.parse_args()
    init()
    next_poll = 0
    while True:
        if not setting('worker_paused', False):
            if args.watch and time.time() >= next_poll:
                refresh()
                next_poll = time.time()+max(300, int(os.getenv('POLL_SECONDS', '600')))
            work_once()
            if setting('notifications_enabled', False):
                try:
                    dispatch()
                except ValueError:
                    pass
        time.sleep(2)


if __name__ == '__main__':
    main()
