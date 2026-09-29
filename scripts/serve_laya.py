"""Run upstream Laya with local, CPU-only task-routing defaults."""
import os


def main():
    for name, value in {
        'LAYA_HOST': '127.0.0.1', 'LAYA_PORT': '8001',
        'LAYA_DEVICE': 'cpu', 'LAYA_THREADS': '4',
        'LAYA_MODELS': 'typed-decisions', 'LAYA_PRELOAD': '1',
    }.items():
        os.environ.setdefault(name, value)
    from laya.serve import main as serve
    serve()


if __name__ == '__main__':
    main()
