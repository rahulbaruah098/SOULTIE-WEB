import os
from dotenv import load_dotenv
from pymongo import MongoClient
from werkzeug.security import generate_password_hash

load_dotenv()
client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017/soultie"))
db = client.get_default_database()
email = os.getenv("ADMIN_EMAIL", "admin@xuoroni.local").strip().lower()
password = os.getenv("ADMIN_PASSWORD", "ChangeMe123!")
db.admins.update_one({"email": email}, {"$setOnInsert": {"email": email, "password_hash": generate_password_hash(password)}}, upsert=True)
db.testers.create_index("email", unique=True)
db.settings.create_index("key", unique=True)
print(f"Admin ready: {email}. Change the local demo password before deployment.")
