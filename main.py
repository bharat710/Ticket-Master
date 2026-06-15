from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import declarative_base, sessionmaker, Session
import redis
import getpass

# 1. PostgreSQL & Redis Setup
USER = getpass.getuser()
DATABASE_URL = f"postgresql://{USER}@localhost:5432/ticketing"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Connect to your local Redis instance
redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)

# 2. Database Model
class Seat(Base):
    __tablename__ = "seats"
    id = Column(Integer, primary_key=True, index=True)
    seat_number = Column(String, unique=True, nullable=False)
    status = Column(String, default="AVAILABLE") # Now only used for permanent 'BOOKED' status

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

app = FastAPI(title="Scalable Ticketing Engine - Phase 2")

@app.on_event("startup")
def seed_data():
    db = SessionLocal()
    if db.query(Seat).count() == 0:
        print("Seeding 5 available seats in PostgreSQL...")
        for i in range(1, 6):
            db.add(Seat(seat_number=f"A{i}", status="AVAILABLE"))
        db.commit()
    db.close()
    # Flush Redis on startup just for clean testing
    redis_client.flushall()

# 3. Optimized Reservation Endpoint using Redis Locks
@app.post("/reserve/{seat_id}")
def reserve_seat(seat_id: int, user_id: str, db: Session = Depends(get_db)):
    # Step A: Check PostgreSQL to ensure the seat isn't permanently SOLD/BOOKED already
    seat = db.query(Seat).filter(Seat.id == seat_id).first()
    if not seat:
        raise HTTPException(status_code=404, detail="Seat not found")
    if seat.status == "BOOKED":
        raise HTTPException(status_code=400, detail="This seat has already been permanently bought.")

    # Step B: Leverage Redis for high-speed temporary locking
    redis_key = f"seat:lock:{seat_id}"
    
    # .set(key, value, ex=seconds, nx=True)
    # nx=True means: "Only create this key if it DOES NOT already exist" (Our lock defense!)
    # ex=60 means: Set expiration time to 60 seconds (using 1 minute for easy testing instead of 10)
    lock_acquired = redis_client.set(redis_key, user_id, ex=60, nx=True)
    
    if not lock_acquired:
        # If nx=True failed, it means someone else already holds this key in Redis
        current_holder = redis_client.get(redis_key)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, 
            detail=f"Seat is temporarily locked by another user."
        )
        
    return {
        "message": f"Success! Seat {seat.seat_number} is locked in RAM for user {user_id}.",
        "expires_in_seconds": redis_client.ttl(redis_key)
    }

# Endpoint to check real-time status combining Postgres and Redis
@app.get("/seats")
def get_seats(db: Session = Depends(get_db)):
    seats = db.query(Seat).all()
    result = []
    
    for s in seats:
        # Check if Redis holds a temporary lock
        lock_holder = redis_client.get(f"seat:lock:{s.id}")
        
        # Determine the true real-time status
        if s.status == "BOOKED":
            current_status = "BOOKED"
        elif lock_holder:
            current_status = "LOCKED"
        else:
            # If it's not permanently BOOKED, and Redis has no lock, 
            # it is absolutely AVAILABLE!
            current_status = "AVAILABLE"
            
            # Optional: If Postgres still says "LOCKED" from Phase 1, 
            # let's clean it up lazily right now.
            if s.status == "LOCKED":
                s.status = "AVAILABLE"
                db.add(s)
                db.commit()

        result.append({
            "id": s.id,
            "seat_number": s.seat_number,
            "status": current_status,
            "locked_by": lock_holder
        })
        
    return result
# @app.get("/seats")
# def get_seats(db: Session = Depends(get_db)):
#     seats = db.query(Seat).all()
#     result = []
#     for s in seats:
#         # Check if Redis holds a temporary lock for this seat
#         lock_holder = redis_client.get(f"seat:lock:{s.id}")
#         current_status = "LOCKED" if lock_holder else s.status
#         result.append({
#             "id": s.id,
#             "seat_number": s.seat_number,
#             "status": current_status,
#             "locked_by": lock_holder
#         })
#     return result