from pathlib import Path

from flask import Flask

BASE_DIR = Path(__file__).resolve().parent.parent


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(BASE_DIR / "templates"),
        static_folder=str(BASE_DIR / "static"),
    )

    from app.routes import bp as main_bp
    app.register_blueprint(main_bp)

    return app
