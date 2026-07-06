from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker


class Base(DeclarativeBase):
    pass


class Listing(Base):
    __tablename__ = "listings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    external_id = Column(Text, unique=True, nullable=False)
    source = Column(Text, nullable=False)  # apartments_com | hotpads | trulia | zumper
    url = Column(Text)
    address = Column(Text)
    neighborhood = Column(Text)
    borough = Column(Text)
    lat = Column(Float)
    lng = Column(Float)
    beds = Column(Integer)
    baths = Column(Float)
    price = Column(Integer)
    broker_fee = Column(Float)
    broker_fee_source = Column(Text)  # listed | assumed
    amenities_raw = Column(Text)  # JSON blob
    laundry_in_unit = Column(Boolean, default=False)
    laundry_in_building = Column(Boolean, default=False)
    dishwasher = Column(Boolean, default=False)
    near_subway = Column(Boolean, default=False)
    gym = Column(Boolean, default=False)
    rooftop = Column(Boolean, default=False)
    has_flex = Column(Boolean, nullable=True)    # True=flex room present, None=unknown
    has_photos = Column(Boolean, nullable=True)  # True=photos in listing, None=unknown
    nearest_subway = Column(Text)                # e.g. "Wall St (2/3) — 3 min walk"
    building_amenities = Column(Text)            # comma list: "Doorman, Elevator, Gym"
    move_in_date = Column(Text)
    listed_date = Column(Text)
    contact_name = Column(Text)
    contact_email = Column(Text)
    contact_phone = Column(Text)
    status = Column(Text, default="new")  # new | liked | contacted | touring | passed
    contact_notes = Column(Text)  # e.g. "inquiry: Jul 5" or "tour: Sep 15 @ 10:00 AM"
    pre_tour_score = Column(Float)
    post_tour_score = Column(Float)
    notes = Column(Text)
    created_at = Column(Text, default=lambda: datetime.utcnow().isoformat())
    updated_at = Column(Text, default=lambda: datetime.utcnow().isoformat())

    contacts = relationship("Contact", back_populates="listing")
    tours = relationship("Tour", back_populates="listing")


class Contact(Base):
    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    listing_id = Column(Integer, ForeignKey("listings.id"), nullable=False)
    channel = Column(Text, nullable=False)  # email | sms
    message_template = Column(Text)
    sent_at = Column(Text)
    replied_at = Column(Text)
    reply_snippet = Column(Text)
    status = Column(Text)  # sent | replied | no_response

    listing = relationship("Listing", back_populates="contacts")


class Tour(Base):
    __tablename__ = "tours"

    id = Column(Integer, primary_key=True, autoincrement=True)
    listing_id = Column(Integer, ForeignKey("listings.id"), nullable=False)
    scheduled_date = Column(Text)
    scheduled_time = Column(Text)
    neighborhood = Column(Text)
    calendar_event_id = Column(Text)
    confirmed = Column(Boolean, default=False)
    notes = Column(Text)

    listing = relationship("Listing", back_populates="tours")


class MessageTemplate(Base):
    __tablename__ = "message_templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(Text, unique=True, nullable=False)
    label = Column(Text)
    subject = Column(Text)
    body = Column(Text)
    channel = Column(Text)  # email | sms | both


def get_engine(db_path: str = "data/apartments.db"):
    return create_engine(f"sqlite:///{db_path}", echo=False)


def get_session(db_path: str = "data/apartments.db"):
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def init_db(db_path: str = "data/apartments.db"):
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    _migrate(engine)
    return engine


def _migrate(engine) -> None:
    """Add new columns to existing DBs without dropping data."""
    new_cols = [
        ("dishwasher", "BOOLEAN DEFAULT 0"),
        ("near_subway", "BOOLEAN DEFAULT 0"),
        ("has_flex", "BOOLEAN"),
        ("has_photos", "BOOLEAN"),
        ("nearest_subway", "TEXT"),
        ("building_amenities", "TEXT"),
        ("contact_notes", "TEXT"),
    ]
    with engine.connect() as conn:
        for col_name, col_def in new_cols:
            try:
                conn.execute(
                    __import__("sqlalchemy").text(
                        f"ALTER TABLE listings ADD COLUMN {col_name} {col_def}"
                    )
                )
                conn.commit()
            except Exception:
                pass  # column already exists
