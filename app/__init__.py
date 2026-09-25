from pathlib import Path

from flask import Flask, render_template

BASE_DIR = Path(__file__).resolve().parent.parent


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(BASE_DIR / "templates"),
        static_folder=str(BASE_DIR / "static"),
    )

    from app.routes import bp as main_bp
    app.register_blueprint(main_bp)

    @app.route("/")
    def root():
        return render_template("landing.html")

    return app
