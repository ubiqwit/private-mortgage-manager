def register_blueprints(app):
    from .activity import bp as activity_bp
    from .auth import bp as auth_bp
    from .companies import bp as companies_bp
    from .dashboard import bp as dashboard_bp
    from .documents import bp as documents_bp
    from .market import bp as market_bp
    from .mortgages import bp as mortgages_bp
    from .reports import bp as reports_bp
    from .statements import bp as statements_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(companies_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(mortgages_bp)
    app.register_blueprint(documents_bp)
    app.register_blueprint(activity_bp)
    app.register_blueprint(statements_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(market_bp)
