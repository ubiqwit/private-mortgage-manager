def register_blueprints(app):
    from .auth import bp as auth_bp
    from .dashboard import bp as dashboard_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
