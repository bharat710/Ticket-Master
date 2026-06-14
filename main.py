from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# 1. Database Connection Configuration
# Postgress URL format: postgresql://[user]:[password]@[host]:[port]/[database]
# On Mac Homebrew, your default user is your Mac username, and there is no password.
import os
import getpass
USER = getpass.getuser()

DATABASE_URL = f"postgresql://{USER}@localhost:5432/ticketing"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 2. Database Models
class Seat(Base):
    __tablename__ = "seats"

    id = Column(Integer, primary_key=True, index=True)
    seat_number = Column(String, unique=True, nullable=False)
    status = Column(String, default="AVAILABLE") # AVAILABLE, LOCKED, BOOKED
    locked_by_user = Column(String, nullable=True)

# Create tables in Postgres instantly on startup
Base.metadata.create_all(bind=engine)

# 3. Dependency to get Database Session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# 4. FastAPI App Initialization
app = FastAPI(title="Scalable Ticketing Engine - Phase 1")

# Seed data helper: Automatically inserts 5 seats if the DB is empty
@app.on_event("startup")
def seed_data():
    db = SessionLocal()
    if db.query(Seat).count() == 0:
        print("Empty database found! Seeding 5 available seats...")
        for i in range(1, 6):
            db.add(Seat(seat_number=f"A{i}", status="AVAILABLE"))
        db.commit()
    db.close()

# 5. The Secure Booking Endpoint (With Row-Level Locking)
@app.post("/reserve/{seat_id}")
def reserve_seat(seat_id: int, user_id: str, db: Session = Depends(get_db)):
    # Start a clean transaction block
    with db.begin():
        # .with_for_update() locks this specific row in Postgres. 
        # Anyone else trying to look at this seat must wait until this block finishes.
        seat = db.query(Seat).filter(Seat.id == seat_id).with_for_update().first()
        
        if not seat:
            raise HTTPException(status_code=404, detail="Seat not found")
            
        if seat.status != "AVAILABLE":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, 
                detail=f"Seat is already {seat.status} by user: {seat.locked_by_user}"
            )
            
        # Lock the seat
        seat.status = "LOCKED"
        seat.locked_by_user = user_id
        db.add(seat)
        
    return {"message": f"Success! Seat {seat.seat_number} is locked for user {user_id}."}

# Endpoint to check the status of all seats
@app.get("/seats")
def get_seats(db: Session = Depends(get_db)):
    return db.query(Seat).all()