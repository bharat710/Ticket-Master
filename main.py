from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import declarative_base, sessionmaker, Session
import redis
import getpass
import asyncio
import json

# 1. Setup Connections
USER = getpass.getuser()
DATABASE_URL = f"postgresql://{USER}@localhost:5432/ticketing"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)

class Seat(Base):
    __tablename__ = "seats"
    id = Column(Integer, primary_key=True, index=True)
    seat_number = Column(String, unique=True, nullable=False)
    status = Column(String, default="AVAILABLE")

Base.metadata.create_all(bind=engine)

# A set to keep track of all active browser connections listening to live updates
listeners = set()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

app = FastAPI(title="Scalable Ticketing Engine - Phase 3")

@app.on_event("startup")
def seed_data():
    db = SessionLocal()
    if db.query(Seat).count() == 0:
        for i in range(1, 6):
            db.add(Seat(seat_number=f"A{i}", status="AVAILABLE"))
        db.commit()
    db.close()
    redis_client.flushall()

# Helper function to notify all connected frontends that something changed
async def notify_listeners(message_data: dict):
    if listeners:
        # Convert dictionary to a JSON string formatted for SSE standard format ("data: ...\n\n")
        sse_message = f"data: {json.dumps(message_data)}\n\n"
        # Send the message to every connected browser concurrently
        await asyncio.gather(*[listener.put(sse_message) for listener in listeners])

# 3. Reservation Endpoint (Now with Live Broadcasts!)
@app.post("/reserve/{seat_id}")
async def reserve_seat(seat_id: int, user_id: str, db: Session = Depends(get_db)):
    seat = db.query(Seat).filter(Seat.id == seat_id).first()
    if not seat:
        raise HTTPException(status_code=404, detail="Seat not found")
    if seat.status == "BOOKED":
        raise HTTPException(status_code=400, detail="Seat permanently bought.")

    redis_key = f"seat:lock:{seat_id}"
    lock_acquired = redis_client.set(redis_key, user_id, ex=30, nx=True) # 30 secs for rapid testing
    
    if not lock_acquired:
        raise HTTPException(status_code=409, detail="Seat temporarily locked.")
        
    # NEW: Broadcast this lock event instantly to everyone looking at the dashboard!
    await notify_listeners({
        "event": "LOCK",
        "seat_id": seat_id,
        "status": "LOCKED",
        "locked_by": user_id
    })
        
    return {"message": "Seat locked!", "expires_in": redis_client.ttl(redis_key)}

# 4. The Server-Sent Events (SSE) Stream Endpoint
@app.get("/stream")
async def stream_updates():
    # Create an isolated message queue for this specific browser connection
    queue = asyncio.Queue()
    listeners.add(queue)
    
    async def event_generator():
        try:
            while True:
                # Wait until there is a new broadcast message in the queue
                message = await queue.get()
                yield message
        except asyncio.CancelledError:
            # Clean up when the user closes their browser tab
            listeners.remove(queue)

    # Return a continuous streaming response using the text/event-stream format
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/seats")
def get_seats(db: Session = Depends(get_db)):
    seats = db.query(Seat).all()
    result = []
    for s in seats:
        lock_holder = redis_client.get(f"seat:lock:{s.id}")
        current_status = "LOCKED" if lock_holder else s.status
        result.append({"id": s.id, "seat_number": s.seat_number, "status": current_status})
    return result