"""Command-line helpers:  flask --app run <command>"""
import getpass

import click

from . import db
from .models import ROLES, User
from .timeutil import today as local_today


def register_cli(app):
    register_seed(app)

    @app.cli.command("refresh-market")
    def refresh_market():
        """Fetch the latest Bank of Canada rates and news (run from a scheduled job)."""
        from .services import market

        result = market.refresh(force=True)
        click.echo(f"New observations: {sum(result['series'].values())}, news items: {result['news']}")
        for err in result["errors"]:
            click.echo(f"  ! {err}", err=True)

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
            raise click.ClickException(str(exc)) from exc
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
            raise click.ClickException(str(exc)) from exc
        user.active = True
        db.session.commit()
        click.echo("Password updated")


def _seed_demo(today=None):
    """Populate a realistic sample book (for trying the app out)."""
    import random
    from decimal import Decimal

    from dateutil.relativedelta import relativedelta

    from .models import Mortgage, MortgageTransaction
    from .services import ledger
    from .services.matching import name_tokens

    today = today or local_today()
    rnd = random.Random(42)
    samples = [
        ("Jane & Mark Smith", "12 Maple Ave", "Toronto", "detached", 1_150_000, 2, 610_000, 250_000, "10.99", 12, 9, "interest_only"),
        ("Rahul Patel", "88 Lakeshore Rd W", "Mississauga", "semi", 980_000, 1, 0, 540_000, "8.49", 24, 14, "interest_only"),
        ("Northgate Holdings Inc.", "455 Dundas St E", "Hamilton", "commercial", 2_400_000, 1, 0, 1_200_000, "9.75", 12, 11, "interest_only"),
        ("Li Wei", "3 Birchwood Crt", "Markham", "detached", 1_620_000, 2, 900_000, 300_000, "11.50", 12, 4, "interest_only"),
        ("Sarah O'Neil", "1201-20 Bay St", "Toronto", "condo", 740_000, 1, 0, 480_000, "7.95", 24, 20, "amortizing"),
        ("Carlos Mendes", "17 Elm Grove", "Brampton", "townhouse", 890_000, 2, 520_000, 180_000, "12.00", 12, 13, "interest_only"),
        ("Greenfield Dev Corp", "Lot 4 Conc 7", "Caledon", "land", 1_900_000, 1, 0, 800_000, "13.00", 12, 2, "interest_only"),
        ("Amira Haddad", "54 Queen St N", "Kitchener", "multi_unit", 1_300_000, 1, 0, 700_000, None, 18, 7, "interest_only"),
    ]
    for i, (name, addr, city, ptype, value, pos, prior, principal, rate, term, age, ptype_pay) in enumerate(samples, 1):
        funded = (today - relativedelta(months=age)).replace(day=1)
        m = Mortgage(
            reference=f"M-{i:03d}", borrower_name=name, property_address=addr, property_city=city,
            property_province="ON", property_type=ptype, property_value=Decimal(value), position=pos,
            prior_charges=Decimal(prior), principal_amount=Decimal(principal),
            interest_rate=Decimal(rate or "9.95"), rate_type="variable" if rate is None else "fixed",
            prime_spread=Decimal("5.00") if rate is None else None, rate_floor=Decimal("9.50") if rate is None else None,
            compounding="semi_annual" if ptype_pay == "amortizing" else "monthly",
            payment_type=ptype_pay, payment_frequency="monthly", amortization_months=300 if ptype_pay == "amortizing" else None,
            funded_date=funded, first_payment_date=funded + relativedelta(months=1), term_months=term,
            maturity_date=funded + relativedelta(months=term), lender_fee=Decimal(principal) * Decimal("0.02"),
            broker_name=rnd.choice(["Mortgage Alliance", "Dominion Lending", "Private broker"]),
            broker_fee=Decimal(principal) * Decimal("0.01"), appraisal_date=funded - relativedelta(weeks=3),
            insurance_expiry=today + relativedelta(days=[12, 140, 200, 300, 75, 260, 330, 190][i - 1]),
            status="active", match_keywords="\n".join(name_tokens(name)[-1:] + [f"E-TRANSFER {name.split()[0].upper()}"]),
        )
        db.session.add(m)
        db.session.add(MortgageTransaction(mortgage=m, date=funded, type="fee", amount=m.lender_fee,
                                           fees=m.lender_fee, interest=0, principal=0, notes="Lender fee at funding"))
        db.session.flush()
        missed = 2 if i == 6 else 0  # one borrower behind on payments
        dues = [d for d in m.due_dates(end=today) if d < today]
        for n, d in enumerate(dues):
            if n >= len(dues) - missed:
                break
            pay = m.regular_payment()
            day = min(d + relativedelta(days=rnd.choice([0, 0, 1, 2, 3])), today)
            split = ledger.suggest_split(m, pay, day, "payment")
            db.session.add(MortgageTransaction(mortgage=m, date=day, type="payment", amount=pay, **split))
            db.session.flush()
    db.session.commit()


def register_seed(app):
    @app.cli.command("seed-demo")
    @click.option("--yes", is_flag=True, help="Skip the confirmation prompt")
    def seed_demo(yes):
        """Load a sample mortgage book (only into an empty database)."""
        from .models import Mortgage

        if Mortgage.query.first():
            raise click.ClickException("The database already has mortgages; demo data is only loaded into an empty book.")
        if not yes:
            click.confirm("Load sample mortgages for trying the app out?", abort=True)
        from .services.demo import load_demo
        from .timeutil import today

        n = load_demo(today())
        db.session.commit()
        click.echo(f"Loaded {n} demo mortgages.")
