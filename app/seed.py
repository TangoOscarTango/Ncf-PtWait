from sqlalchemy.orm import Session

from app.auth import hash_password
from app.models import Location, Provider, RoleEnum, User


SEED_LOCATIONS = ["Main", "Brown", "Peds", "Women's Health", "Chester"]
SEED_PROVIDERS = [
    ("Gashaw Tafari", False),
    ("Adnan Qadeer", False),
    ("Kimberly Davis", False),
    ("Douglas Tiedt", False),
    ("Jacquelyn Gill", False),
    ("Jovan Wright", False),
    ("Alyse Suillivan", False),
    ("Joyce Hart", False),
    ("Roosevelt Daniel", False),
    ("Veleka Mayfield", False),
    ("Charita Johnson", False),
    ("Brittney Congdon", True),
]
SEED_USERS = [
    ("admin", "ChangeMeAdmin!", RoleEnum.ADMIN),
    ("fd1", "ChangeMeFD!", RoleEnum.FD),
    ("nurse1", "ChangeMeNurse!", RoleEnum.NURSE),
]


def seed_initial_data(db: Session) -> None:
    changed = False

    if db.query(Location).count() == 0:
        for name in SEED_LOCATIONS:
            db.add(Location(name=name))
        changed = True

    existing_providers = {provider.name: provider for provider in db.query(Provider).all()}
    for name, is_hidden in SEED_PROVIDERS:
        existing = existing_providers.get(name)
        if existing is None:
            db.add(Provider(name=name, is_hidden=is_hidden))
            changed = True
        elif existing.is_hidden != is_hidden:
            existing.is_hidden = is_hidden
            db.add(existing)
            changed = True

    if db.query(User).count() == 0:
        for username, password, role in SEED_USERS:
            db.add(
                User(
                    username=username,
                    password_hash=hash_password(password),
                    role=role,
                    is_active=True,
                    must_change_password=True,
                )
            )
        changed = True
        print("WARNING: Seed users created with default passwords. Change them immediately.")

    if changed:
        db.commit()
