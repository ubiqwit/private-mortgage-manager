"""Command-line helpers:  flask --app run <command>"""
import getpass

import click

from . import db
from .models import ROLES, User


def register_cli(app):
    @app.cli.command("create-user")
    @click.argument("email")
    @click.option("--name", default=None)
    @click.option("--role", type=click.Choice([r for r, _ in ROLES]), default="admin")
    def create_user(email, name, role):
        """Create a login (prompts for the password)."""
        email = email.strip().lower()
        if User.query.filter_by(email=email).first():
            raise click.ClickException(f"{email} already exists")
        password = getpass.getpass("Password (min 10 chars): ")
        if password != getpass.getpass("Repeat password: "):
            raise click.ClickException("Passwords do not match")
        user = User(email=email, name=name, role=role)
        try:
            user.set_password(password)
        except ValueError as exc:
            raise click.ClickException(str(exc))
        db.session.add(user)
        db.session.commit()
        click.echo(f"Created {role} user {email}")

    @app.cli.command("reset-password")
    @click.argument("email")
    def reset_password(email):
        """Set a new password for an existing user."""
        user = User.query.filter_by(email=email.strip().lower()).first()
        if not user:
            raise click.ClickException("No such user")
        password = getpass.getpass("New password (min 10 chars): ")
        try:
            user.set_password(password)
        except ValueError as exc:
            raise click.ClickException(str(exc))
        user.active = True
        db.session.commit()
        click.echo("Password updated")
