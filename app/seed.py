from sqlalchemy.orm import Session

from app.auth import hash_password
from app.models import Location, Provider, RoleEnum, User


SEED_LOCATIONS = ["Main", "Brown", "Peds", "Women's Health", "Chester"]
SEED_PROVIDERS = ["John Smith", "Jane Doe"]
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

    if db.query(Provider).count() == 0:
        for name in SEED_PROVIDERS:
            db.add(Provider(name=name))
        changed = True

    if db.query(User).count() == 0:
        for username, password, role in SEED_USERS:
            db.add(
                User(
                    username=username,
                    password_hash=hash_password(password),
                    role=role,
                    is_active=True,
                )
            )
        changed = True
        print("WARNING: Seed users created with default passwords. Change them immediately.")

    if changed:
        db.commit()
