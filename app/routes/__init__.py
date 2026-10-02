def register_blueprints(app):
    from .auth import bp as auth_bp
    from .dashboard import bp as dashboard_bp
    from .mortgages import bp as mortgages_bp
    from .reports import bp as reports_bp
    from .statements import bp as statements_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(mortgages_bp)
    app.register_blueprint(statements_bp)
    app.register_blueprint(reports_bp)
