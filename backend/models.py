from sqlalchemy import Column, Integer, String
from database import Base


class Employee(Base):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    contact = Column(String)
    location = Column(String)   # VN | SGP | AUS
    company = Column(String)    # Accenture | TechVision | DataSync
    department = Column(String) # HR | Engineering | Finance | Marketing | Operations
    position = Column(String)   # Director | Manager | Senior Engineer | Analyst | Junior Engineer
    status = Column(String)     # Active | Not Started | Terminated
