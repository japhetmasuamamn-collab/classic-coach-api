from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
import os

print("=== DATABASE_URL ===")
print(os.getenv("DATABASE_URL"))

SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")

if not SQLALCHEMY_DATABASE_URL:
    raise Exception("DATABASE_URL est vide !")

engine = create_engine(SQLALCHEMY_DATABASE_URL)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()

# Dépendance pour obtenir la DB dans les routes
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()